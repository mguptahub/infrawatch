from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/opensearch", tags=["OpenSearch"])


def _fetch_opensearch(session):
    os_client = session.client("opensearch")
    cw = session.client("cloudwatch")

    names = [d["DomainName"] for d in os_client.list_domain_names().get("DomainNames", [])]
    if not names:
        return {"domains": []}

    domains_detail = os_client.describe_domains(DomainNames=names).get("DomainStatusList", [])

    def fetch_domain_data(d):
        domain_name = d["DomainName"]
        client_id = d["ARN"].split(":")[4]

        def get_os_metric(metric_name):
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/ES",
                    MetricName=metric_name,
                    Dimensions=[
                        {"Name": "DomainName", "Value": domain_name},
                        {"Name": "ClientId", "Value": client_id},
                    ],
                    StartTime=datetime.utcnow() - timedelta(minutes=10),
                    EndTime=datetime.utcnow(),
                    Period=300,
                    Statistics=["Average"],
                )
                pts = resp.get("Datapoints", [])
                return round(pts[-1]["Average"], 1) if pts else None
            except:
                return None

        ec = d.get("ElasticsearchClusterConfig") or d.get("ClusterConfig", {})
        return {
            "name": domain_name,
            "arn": d.get("ARN"),
            "engine_version": d.get("ElasticsearchVersion") or d.get("EngineVersion", "—"),
            "status": "Active" if d.get("Processing") is False else "Processing" if d.get("Processing") else "Unknown",
            "instance_type": ec.get("InstanceType", "—"),
            "instance_count": ec.get("InstanceCount", 1),
            "dedicated_master": ec.get("DedicatedMasterEnabled", False),
            "zone_awareness": ec.get("ZoneAwarenessEnabled", False),
            "ebs_volume_gb": d.get("EBSOptions", {}).get("VolumeSize"),
            "endpoint": d.get("Endpoint") or (list(d.get("Endpoints", {}).values())[0] if d.get("Endpoints") else None),
            "cpu_percent": get_os_metric("CPUUtilization"),
            "jvm_memory_percent": get_os_metric("JVMMemoryPressure"),
            "cluster_status_green": get_os_metric("ClusterStatus.green"),
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        domains = list(executor.map(fetch_domain_data, domains_detail))

    return {"domains": domains, "count": len(domains)}


@router.get("/domains")
def get_opensearch_domains(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("opensearch", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_opensearch(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
