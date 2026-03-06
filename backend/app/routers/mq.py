from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL
from ..core.sse_refresh import stream_refresh_done
from ..core.database import get_db
from ..db.models import CollectedResource

router = APIRouter(prefix="/api/mq", tags=["MQ"])
USE_COLLECTOR_DB = True


def _list_mq_from_db(region: str, db: Session):
    """Return { brokers: [...], count } from collected_resources."""
    rows = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "mq",
            CollectedResource.region == region,
        )
        .all()
    )
    brokers = [r.attributes or {} for r in rows]
    return {"brokers": brokers, "count": len(brokers)}


def _detail_mq_from_db(broker_id: str, region: str, db: Session):
    """Return broker detail dict from collected_resources or None."""
    r = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "mq",
            CollectedResource.region == region,
            CollectedResource.resource_id == broker_id,
        )
        .first()
    )
    if not r:
        return None
    return r.attributes or {}


def _get_mq_metric_configs(engine_type):
    """Return metric configurations based on MQ engine type."""
    if engine_type == "RabbitMQ":
        return {
            "cpu": {"name": "SystemCpuUtilization", "stat": "Average", "label": "CPU"},
            "memory": {"name": "MemoryUsed", "stat": "Average", "label": "Memory"},
            "connections": {"name": "ConnectionCount", "stat": "Average", "label": "Connections"},
            "queues": {"name": "Queues", "stat": "Average", "label": "Queues"},
            "messages": {"name": "MessageCount", "stat": "Average", "label": "Messages"},
            "storage_free": {"name": "RabbitMQDiskFree", "stat": "Average", "label": "Disk Free"}
        }
    else: # ActiveMQ
        return {
            "cpu": {"name": "CpuUtilization", "stat": "Average", "label": "CPU"},
            "memory": {"name": "MemoryUsage", "stat": "Average", "label": "Memory"},
            "connections": {"name": "CurrentConnectionsCount", "stat": "Average", "label": "Connections"},
            "queues": {"name": "TotalQueueCount", "stat": "Sum", "label": "Queues"},
            "messages": {"name": "TotalMessageCount", "stat": "Sum", "label": "Messages"},
            "storage_usage": {"name": "StorePercentUsage", "stat": "Average", "label": "Storage Usage"}
        }

def _fetch_mq(session):
    mq = session.client("mq")
    cw = session.client("cloudwatch")

    brokers_list = mq.list_brokers().get("BrokerSummaries", [])
    if not brokers_list:
        return {"brokers": [], "count": 0}

    def fetch_broker_data(b):
        broker_id = b["BrokerId"]
        detail = mq.describe_broker(BrokerId=broker_id)
        engine = detail.get("EngineType", "ActiveMQ")
        m_configs = _get_mq_metric_configs(engine)

        def get_mq_metric(cfg):
            dims = [{"Name": "Broker", "Value": detail.get("BrokerName", broker_id)}]
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/AmazonMQ",
                    MetricName=cfg["name"],
                    Dimensions=dims,
                    StartTime=datetime.utcnow() - timedelta(minutes=10),
                    EndTime=datetime.utcnow(),
                    Period=300,
                    Statistics=[cfg["stat"]],
                )
                pts = resp.get("Datapoints", [])
                val = pts[-1][cfg["stat"]] if pts else None
                return round(val, 1) if val is not None else None
            except:
                return None

        # Enhanced detail for list view
        instances = detail.get("BrokerInstances", [])
        endpoints = []
        if instances:
            # Aggregate all endpoints in case of multi-instance
            for inst in instances:
                endpoints.extend(inst.get("Endpoints", []))

        return {
            "id": broker_id,
            "name": detail.get("BrokerName", "—"),
            "state": detail.get("BrokerState", "—"),
            "engine_type": engine,
            "engine_version": detail.get("EngineVersion", "—"),
            "instance_type": detail.get("HostInstanceType", "—"),
            "deployment_mode": detail.get("DeploymentMode", "—"),
            "publicly_accessible": detail.get("PubliclyAccessible", False),
            "auto_minor_upgrade": detail.get("AutoMinorVersionUpgrade", False),
            "endpoints": endpoints,
            "instances": instances,
            "cpu_percent": get_mq_metric(m_configs["cpu"]),
            "heap_usage": get_mq_metric(m_configs["memory"]),
            "total_connections": get_mq_metric(m_configs["connections"]),
            "total_queues": get_mq_metric(m_configs["queues"]),
            "storage_free": get_mq_metric(m_configs.get("storage_free", {"name": "N/A"})),
            "storage_usage": get_mq_metric(m_configs.get("storage_usage", {"name": "N/A"})),
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        brokers = list(executor.map(fetch_broker_data, brokers_list))

    return {"brokers": brokers, "count": len(brokers)}


def _fetch_mq_metrics(session, broker_id, hours=24):
    mq = session.client("mq")
    cw = session.client("cloudwatch")
    
    detail = mq.describe_broker(BrokerId=broker_id)
    engine = detail.get("EngineType", "ActiveMQ")
    m_configs = _get_mq_metric_configs(engine)
    broker_name = detail.get("BrokerName", broker_id)
    
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)
    period = 300 if hours <= 24 else 3600
    
    dims = [{"Name": "Broker", "Value": broker_name}]
    
    def get_series(cfg):
        try:
            resp = cw.get_metric_statistics(
                Namespace="AWS/AmazonMQ",
                MetricName=cfg["name"],
                Dimensions=dims,
                StartTime=start,
                EndTime=end,
                Period=period,
                Statistics=[cfg["stat"]],
            )
            pts = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
            return {p["Timestamp"].isoformat(): p[cfg["stat"]] for p in pts}
        except:
            return {}

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {k: ex.submit(get_series, cfg) for k, cfg in m_configs.items()}
        return {k: f.result() for k, f in futures.items()}


@router.get("/brokers")
def get_mq_brokers(request: Request, force: bool = False, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        return _list_mq_from_db(config.region, db)
    session, config2 = get_session_and_config(request)
    key = make_cache_key("mq", config2.access_key or "", config2.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_mq(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh/stream")
def get_mq_refresh_stream(request: Request):
    """SSE stream: emits refresh_done when the collector for this region finishes."""
    _, config = get_session_and_config(request)
    channel = f"refresh:mq:{config.region}"
    return StreamingResponse(
        stream_refresh_done(channel),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/refresh")
def post_mq_refresh(request: Request):
    _, config = get_session_and_config(request)
    from app.tasks.collect_tasks import collect_resources
    collect_resources.delay("mq", config.region)
    return {"ok": True, "message": "Refresh started for region " + config.region}


@router.get("/brokers/{broker_id}")
def get_mq_detail(broker_id: str, request: Request, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        detail = _detail_mq_from_db(broker_id, config.region, db)
        if not detail:
            raise HTTPException(status_code=404, detail="Broker not found")
        return detail
    session, _ = get_session_and_config(request)
    try:
        mq = session.client("mq")
        detail = mq.describe_broker(BrokerId=broker_id)
        return detail
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brokers/{broker_id}/metrics")
def get_mq_metrics(broker_id: str, request: Request, hours: int = 24):
    session, _ = get_session_and_config(request)
    try:
        return _fetch_mq_metrics(session, broker_id, hours)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
