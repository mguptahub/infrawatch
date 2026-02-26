from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/mq", tags=["MQ"])


def _fetch_mq(session):
    mq = session.client("mq")
    cw = session.client("cloudwatch")

    brokers_list = mq.list_brokers().get("BrokerSummaries", [])
    if not brokers_list:
        return {"brokers": [], "count": 0}

    def fetch_broker_data(b):
        broker_id = b["BrokerId"]
        detail = mq.describe_broker(BrokerId=broker_id)

        def get_mq_metric(metric_name, extra_dims=None):
            dims = [{"Name": "Broker", "Value": detail.get("BrokerName", broker_id)}]
            if extra_dims:
                dims += extra_dims
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/AmazonMQ",
                    MetricName=metric_name,
                    Dimensions=dims,
                    StartTime=datetime.utcnow() - timedelta(minutes=10),
                    EndTime=datetime.utcnow(),
                    Period=300,
                    Statistics=["Average"],
                )
                pts = resp.get("Datapoints", [])
                return round(pts[-1]["Average"], 1) if pts else None
            except:
                return None

        return {
            "id": broker_id,
            "name": detail.get("BrokerName", "—"),
            "state": detail.get("BrokerState", "—"),
            "engine_type": detail.get("EngineType", "—"),
            "engine_version": detail.get("EngineVersion", "—"),
            "instance_type": detail.get("HostInstanceType", "—"),
            "deployment_mode": detail.get("DeploymentMode", "—"),
            "publicly_accessible": detail.get("PubliclyAccessible", False),
            "auto_minor_upgrade": detail.get("AutoMinorVersionUpgrade", False),
            "endpoints": detail.get("BrokerInstances", [{}])[0].get("Endpoints", []) if detail.get("BrokerInstances") else [],
            "cpu_percent": get_mq_metric("CpuUtilization"),
            "heap_usage": get_mq_metric("HeapUsage"),
            "total_connections": get_mq_metric("TotalConnectionCount"),
            "total_queues": get_mq_metric("TotalQueueCount"),
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        brokers = list(executor.map(fetch_broker_data, brokers_list))

    return {"brokers": brokers, "count": len(brokers)}


@router.get("/brokers")
def get_mq_brokers(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("mq", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_mq(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
