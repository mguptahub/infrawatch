from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from datetime import datetime, timedelta
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/elasticache", tags=["ElastiCache"])


def _fetch_elasticache(session):
    ec_client = session.client("elasticache")
    cw = session.client("cloudwatch")

    rgs = ec_client.describe_replication_groups().get("ReplicationGroups", [])
    clusters = ec_client.describe_cache_clusters(ShowCacheNodeInfo=True).get("CacheClusters", [])

    def get_ec_metric(cluster_id, metric_name):
        try:
            resp = cw.get_metric_statistics(
                Namespace="AWS/ElastiCache",
                MetricName=metric_name,
                Dimensions=[{"Name": "CacheClusterId", "Value": cluster_id}],
                StartTime=datetime.utcnow() - timedelta(minutes=10),
                EndTime=datetime.utcnow(),
                Period=300,
                Statistics=["Average"],
            )
            pts = resp.get("Datapoints", [])
            return round(pts[-1]["Average"], 1) if pts else None
        except:
            return None

    replication_groups = []
    for rg in rgs:
        member_clusters = rg.get("MemberClusters", [])
        primary_cluster_id = member_clusters[0] if member_clusters else None

        replication_groups.append({
            "id": rg["ReplicationGroupId"],
            "description": rg.get("Description", ""),
            "status": rg.get("Status", "—"),
            "mode": "Cluster" if rg.get("ClusterEnabled") else "Single",
            "node_groups": len(rg.get("NodeGroups", [])),
            "member_clusters": len(member_clusters),
            "automatic_failover": rg.get("AutomaticFailover", "disabled"),
            "at_rest_encryption": rg.get("AtRestEncryptionEnabled", False),
            "in_transit_encryption": rg.get("TransitEncryptionEnabled", False),
            "primary_endpoint": rg.get("NodeGroups", [{}])[0].get("PrimaryEndpoint", {}).get("Address") if rg.get("NodeGroups") else None,
            "cpu_percent": get_ec_metric(primary_cluster_id, "CPUUtilization") if primary_cluster_id else None,
            "memory_percent": get_ec_metric(primary_cluster_id, "DatabaseMemoryUsagePercentage") if primary_cluster_id else None,
            "cache_hits": get_ec_metric(primary_cluster_id, "CacheHits") if primary_cluster_id else None,
            "cache_misses": get_ec_metric(primary_cluster_id, "CacheMisses") if primary_cluster_id else None,
            "connections": get_ec_metric(primary_cluster_id, "CurrConnections") if primary_cluster_id else None,
        })

    rg_members = {c for rg in rgs for c in rg.get("MemberClusters", [])}
    standalone = []
    for c in clusters:
        cid = c["CacheClusterId"]
        if cid in rg_members:
            continue
        standalone.append({
            "id": cid,
            "engine": f"{c.get('Engine', '—')} {c.get('EngineVersion', '')}",
            "status": c.get("CacheClusterStatus", "—"),
            "node_type": c.get("CacheNodeType", "—"),
            "num_nodes": c.get("NumCacheNodes", 1),
            "az": c.get("PreferredAvailabilityZone", "—"),
            "endpoint": c.get("ConfigurationEndpoint", {}).get("Address") or
                        (c.get("CacheNodes", [{}])[0].get("Endpoint", {}).get("Address") if c.get("CacheNodes") else None),
            "cpu_percent": get_ec_metric(cid, "CPUUtilization"),
            "memory_percent": get_ec_metric(cid, "DatabaseMemoryUsagePercentage"),
            "connections": get_ec_metric(cid, "CurrConnections"),
        })

    return {
        "replication_groups": replication_groups,
        "standalone_clusters": standalone,
        "total": len(replication_groups) + len(standalone),
    }


@router.get("/clusters")
def get_elasticache_clusters(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("elasticache", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_elasticache(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
