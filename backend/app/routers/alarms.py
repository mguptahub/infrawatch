from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/alarms", tags=["Alarms"])


def _fetch_alarms(session):
    cw = session.client("cloudwatch")
    response = cw.describe_alarms()
    alarms = []
    for a in response["MetricAlarms"]:
        alarms.append({
            "name": a["AlarmName"],
            "state": a["StateValue"],
            "metric": a.get("MetricName", "—"),
            "namespace": a.get("Namespace", "—"),
            "description": a.get("AlarmDescription", ""),
            "updated": a["StateUpdatedTimestamp"].isoformat() if a.get("StateUpdatedTimestamp") else None,
        })
    state_order = {"ALARM": 0, "INSUFFICIENT_DATA": 1, "OK": 2}
    alarms.sort(key=lambda x: state_order.get(x["state"], 3))
    return {"alarms": alarms, "alarm_count": sum(1 for a in alarms if a["state"] == "ALARM")}


@router.get("")
def get_alarms(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("alarms", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_alarms(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
