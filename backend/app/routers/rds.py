from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/rds", tags=["RDS"])


def _fetch_rds(session):
    rds = session.client("rds")
    cw = session.client("cloudwatch")

    response = rds.describe_db_instances()
    db_instances = response.get("DBInstances", [])
    if not db_instances:
        return {"instances": [], "count": 0}

    def fetch_db_data(db):
        db_id = db["DBInstanceIdentifier"]

        cpu = None
        try:
            metrics = cw.get_metric_statistics(
                Namespace="AWS/RDS",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_id}],
                StartTime=datetime.utcnow() - timedelta(minutes=10),
                EndTime=datetime.utcnow(),
                Period=300,
                Statistics=["Average"]
            )
            if metrics["Datapoints"]:
                cpu = round(metrics["Datapoints"][-1]["Average"], 1)
        except:
            pass

        connections = None
        try:
            metrics = cw.get_metric_statistics(
                Namespace="AWS/RDS",
                MetricName="DatabaseConnections",
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_id}],
                StartTime=datetime.utcnow() - timedelta(minutes=10),
                EndTime=datetime.utcnow(),
                Period=300,
                Statistics=["Average"]
            )
            if metrics["Datapoints"]:
                connections = int(metrics["Datapoints"][-1]["Average"])
        except:
            pass

        return {
            "id": db_id,
            "engine": f"{db['Engine']} {db.get('EngineVersion', '')}",
            "class": db["DBInstanceClass"],
            "status": db["DBInstanceStatus"],
            "az": db.get("AvailabilityZone", "—"),
            "multi_az": db.get("MultiAZ", False),
            "storage_gb": db.get("AllocatedStorage", 0),
            "endpoint": db.get("Endpoint", {}).get("Address"),
            "port": db.get("Endpoint", {}).get("Port"),
            "cpu_percent": cpu,
            "connections": connections,
        }

    with ThreadPoolExecutor(max_workers=10) as executor:
        instances = list(executor.map(fetch_db_data, db_instances))

    return {"instances": instances, "count": len(instances)}


@router.get("/instances")
def get_rds_instances(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("rds", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_rds(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
