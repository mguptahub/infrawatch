from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError
from datetime import datetime, timedelta
from sqlalchemy.orm import Session

from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL
from ..core.sse_refresh import stream_refresh_done
from ..core.database import get_db
from ..db.models import CollectedResource

router = APIRouter(prefix="/api/elasticache", tags=["ElastiCache"])
USE_COLLECTOR_DB = True


def _list_elasticache_from_db(region: str, db: Session):
    """Return { replication_groups, standalone_clusters, total } from collected_resources."""
    rows = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "elasticache",
            CollectedResource.region == region,
        )
        .all()
    )
    replication_groups = []
    standalone_clusters = []
    for r in rows:
        att = r.attributes or {}
        if att.get("kind") == "replication_group":
            # Map to list-view shape (primary_endpoint vs endpoint)
            replication_groups.append({
                **att,
                "primary_endpoint": att.get("primary_endpoint"),
            })
        else:
            standalone_clusters.append(att)
    return {
        "replication_groups": replication_groups,
        "standalone_clusters": standalone_clusters,
        "total": len(replication_groups) + len(standalone_clusters),
    }


def _detail_elasticache_from_db(resource_id: str, is_rg: bool, region: str, db: Session):
    """Return detail dict from collected_resources or None (minimal shape for side panel)."""
    r = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "elasticache",
            CollectedResource.region == region,
            CollectedResource.resource_id == resource_id,
        )
        .first()
    )
    if not r:
        return None
    att = r.attributes or {}
    kind = "replication_group" if is_rg else "standalone"
    if att.get("kind") != kind:
        return None
    ep = att.get("primary_endpoint") or att.get("endpoint")
    port = att.get("port")
    return {
        **att,
        "ConnectionEndpoint": f"{ep}:{port}" if ep and port else (ep or "—"),
        "SecurityGroupsEnriched": [],
    }


def _fmt_sg_rules(rules):
    result = []
    for r in rules:
        proto = r.get("IpProtocol", "-1")
        if proto == "-1":
            proto, port = "All", "All"
        elif proto in ("tcp", "udp"):
            fp, tp = r.get("FromPort", 0), r.get("ToPort", 0)
            port = str(fp) if fp == tp else f"{fp}-{tp}"
        else:
            port = "—"
        sources = (
            [ip.get("CidrIp", "") for ip in r.get("IpRanges", [])]
            + [ip.get("CidrIpv6", "") for ip in r.get("Ipv6Ranges", [])]
            + [sg.get("GroupId", "") for sg in r.get("UserIdGroupPairs", [])]
        )
        result.append({
            "protocol": proto,
            "port": port,
            "source": ", ".join(s for s in sources if s) or "—",
        })
    return result


def _enrich_security_groups(session, sg_list):
    """Given a list of {SecurityGroupId, Status} dicts, return enriched SG data with rules."""
    if not sg_list:
        return []
    sg_ids = [sg["SecurityGroupId"] for sg in sg_list if sg.get("SecurityGroupId")]
    if not sg_ids:
        return []
    try:
        ec2 = session.client("ec2")
        resp = ec2.describe_security_groups(GroupIds=sg_ids)
        enriched = []
        for sg in resp.get("SecurityGroups", []):
            enriched.append({
                "id": sg["GroupId"],
                "name": sg.get("GroupName", sg["GroupId"]),
                "description": sg.get("Description", ""),
                "inbound": _fmt_sg_rules(sg.get("IpPermissions", [])),
                "outbound": _fmt_sg_rules(sg.get("IpPermissionsEgress", [])),
            })
        return enriched
    except Exception:
        # Fall back to plain IDs if EC2 describe fails
        return [{"id": sg["SecurityGroupId"], "name": sg["SecurityGroupId"], "description": "", "inbound": [], "outbound": []} for sg in sg_list]


def _fetch_elasticache(session):
    ec_client = session.client("elasticache")
    cw = session.client("cloudwatch")

    rgs = ec_client.describe_replication_groups().get("ReplicationGroups", [])
    clusters = ec_client.describe_cache_clusters(ShowCacheNodeInfo=True).get("CacheClusters", [])

    # Map cluster IDs to engine/version for replication group lookup
    cluster_meta = {c["CacheClusterId"]: (c.get("Engine"), c.get("EngineVersion")) for c in clusters}

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
        
        engine = "—"
        version = "—"
        if primary_cluster_id in cluster_meta:
            engine, version = cluster_meta[primary_cluster_id]

        replication_groups.append({
            "id": rg["ReplicationGroupId"],
            "description": rg.get("Description", ""),
            "status": rg.get("Status", "—"),
            "engine": engine,
            "version": version,
            "mode": "Cluster" if rg.get("ClusterEnabled") else "Single",
            "node_groups": len(rg.get("NodeGroups", [])),
            "member_clusters": len(member_clusters),
            "automatic_failover": rg.get("AutomaticFailover", "disabled"),
            "at_rest_encryption": rg.get("AtRestEncryptionEnabled", False),
            "in_transit_encryption": rg.get("TransitEncryptionEnabled", False),
            "primary_endpoint": rg.get("NodeGroups", [{}])[0].get("PrimaryEndpoint", {}).get("Address") if rg.get("NodeGroups") else None,
            "port": rg.get("ConfigurationEndpoint", {}).get("Port") or (rg.get("NodeGroups", [{}])[0].get("PrimaryEndpoint", {}).get("Port") if rg.get("NodeGroups") else None),
            "security_groups": rg.get("SecurityGroups", []), # Some RGs might have them, but usually they're on clusters
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
            "port": c.get("ConfigurationEndpoint", {}).get("Port") or
                    (c.get("CacheNodes", [{}])[0].get("Endpoint", {}).get("Port") if c.get("CacheNodes") else None),
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
def get_elasticache_clusters(request: Request, force: bool = False, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        return _list_elasticache_from_db(config.region, db)
    session, config2 = get_session_and_config(request)
    key = make_cache_key("elasticache", config2.access_key or "", config2.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_elasticache(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh/stream")
def get_elasticache_refresh_stream(request: Request):
    """SSE stream: emits refresh_done when the collector for this region finishes."""
    _, config = get_session_and_config(request)
    channel = f"refresh:elasticache:{config.region}"
    return StreamingResponse(
        stream_refresh_done(channel),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/refresh")
def post_elasticache_refresh(request: Request):
    _, config = get_session_and_config(request)
    from app.tasks.collect_tasks import collect_resources
    collect_resources.delay("elasticache", config.region)
    return {"ok": True, "message": "Refresh started for region " + config.region}


@router.get("/detail")
def get_cluster_detail(request: Request, id: str, is_rg: bool = True, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        detail = _detail_elasticache_from_db(id, is_rg, config.region, db)
        if detail is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return detail
    session, _ = get_session_and_config(request)
    ec = session.client("elasticache")
    try:
        if is_rg:
            resp = ec.describe_replication_groups(ReplicationGroupId=id)
            rg = resp.get("ReplicationGroups", [{}])[0]
            # Replication Groups often don't have Engine/Version at the top level
            if "Engine" not in rg and rg.get("MemberClusters"):
                c_resp = ec.describe_cache_clusters(CacheClusterId=rg["MemberClusters"][0])
                cluster = c_resp.get("CacheClusters", [{}])[0]
                rg["Engine"] = cluster.get("Engine")
                rg["EngineVersion"] = cluster.get("EngineVersion")
                if not rg.get("SecurityGroups"):
                    rg["SecurityGroups"] = cluster.get("SecurityGroups", [])

            # Add ARN and search for SecretArn in tags
            rg_arn = rg.get("ARN")
            if rg_arn:
                try:
                    tags_resp = ec.list_tags_for_resource(ResourceName=rg_arn)
                    for tag in tags_resp.get("TagList", []):
                        if "secret" in tag["Key"].lower() and "arn" in tag["Key"].lower():
                            rg["SecretArn"] = tag["Value"]
                except:
                    pass

            # Add human-friendly endpoint for easy access
            endpoint = None
            port = None
            if rg.get("ConfigurationEndpoint"):
                endpoint = rg["ConfigurationEndpoint"].get("Address")
                port = rg["ConfigurationEndpoint"].get("Port")
            elif rg.get("NodeGroups"):
                # Use PrimaryEndpoint for non-cluster mode
                pe = rg["NodeGroups"][0].get("PrimaryEndpoint", {})
                endpoint = pe.get("Address")
                port = pe.get("Port")
            
            if endpoint:
                rg["ConnectionEndpoint"] = f"{endpoint}:{port}" if port else endpoint

            rg["SecurityGroupsEnriched"] = _enrich_security_groups(session, rg.get("SecurityGroups", []))

            return rg
        else:
            resp = ec.describe_cache_clusters(CacheClusterId=id, ShowCacheNodeInfo=True)
            c = resp.get("CacheClusters", [{}])[0]
            
            # Check for SecretArn in tags for standalone too
            c_arn = c.get("ARN")
            if c_arn:
                try:
                    tags_resp = ec.list_tags_for_resource(ResourceName=c_arn)
                    for tag in tags_resp.get("TagList", []):
                        if "secret" in tag["Key"].lower() and "arn" in tag["Key"].lower():
                            c["SecretArn"] = tag["Value"]
                except:
                    pass

            endpoint = None
            port = None
            if c.get("ConfigurationEndpoint"):
                endpoint = c["ConfigurationEndpoint"].get("Address")
                port = c["ConfigurationEndpoint"].get("Port")
            elif c.get("CacheNodes"):
                pe = c.get("CacheNodes")[0].get("Endpoint", {})
                endpoint = pe.get("Address")
                port = pe.get("Port")

            if endpoint:
                c["ConnectionEndpoint"] = f"{endpoint}:{port}" if port else endpoint

            c["SecurityGroupsEnriched"] = _enrich_security_groups(session, c.get("SecurityGroups", []))

            return c
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics")
def get_cluster_metrics(request: Request, id: str, engine: str = "redis", hours: int = 1, is_rg: bool = True):
    session, _ = get_session_and_config(request)
    cw = session.client("cloudwatch")
    
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)
    period = 60 if hours <= 6 else 300 if hours <= 24 else 3600

    # Determine dimension based on whether it's a Replication Group or Standalone Cluster
    # NOTE: CPUUtilization etc. are often ONLY available at CacheClusterId level.
    # For RGs, we'll try to use the primary node's CacheClusterId.
    dim_name = "CacheClusterId"
    dim_value = id
    
    if is_rg:
        try:
            ec = session.client("elasticache")
            rg_resp = ec.describe_replication_groups(ReplicationGroupId=id)
            rg = rg_resp.get("ReplicationGroups", [{}])[0]
            # Try to get primary cluster ID
            if rg.get("MemberClusters"):
                dim_value = rg["MemberClusters"][0]
            elif rg.get("NodeGroups"):
                # Cluster mode enabled: NodeGroups -> NodeGroupMembers
                dim_value = rg["NodeGroups"][0].get("NodeGroupMembers", [{}])[0].get("CacheClusterId", id)
            
            # For some aggregate metrics, ReplicationGroupId dimension is better
            # We'll stick to CacheClusterId for node metrics like CPU, 
            # but we can try ReplicationGroupId as a fallback if desired.
        except:
            pass

    # Map generic metric names to engine-specific ones
    # Redis/Valkey vs Memcached
    metric_map = {
        "CPUUtilization": "CPUUtilization",
        "CurrConnections": "CurrConnections",
        "DatabaseMemoryUsagePercentage": "DatabaseMemoryUsagePercentage" if engine.lower() != "memcached" else "MemoryUtilization",
        "CacheHits": "CacheHits" if engine.lower() != "memcached" else "GetHits",
        "CacheMisses": "CacheMisses" if engine.lower() != "memcached" else "GetMisses",
    }

    results = {}
    for label, m_name in metric_map.items():
        try:
            # Try primary dimension first
            resp = cw.get_metric_statistics(
                Namespace="AWS/ElastiCache",
                MetricName=m_name,
                Dimensions=[{"Name": dim_name, "Value": dim_value}],
                StartTime=start_time,
                EndTime=end_time,
                Period=period,
                Statistics=["Average"]
            )
            
            # Fallback to ReplicationGroupId for RGs if no data found
            if not resp.get("Datapoints") and is_rg:
                 resp = cw.get_metric_statistics(
                    Namespace="AWS/ElastiCache",
                    MetricName=m_name,
                    Dimensions=[{"Name": "ReplicationGroupId", "Value": id}],
                    StartTime=start_time,
                    EndTime=end_time,
                    Period=period,
                    Statistics=["Average"]
                )

            data = sorted(
                [{"time": p["Timestamp"].isoformat(), "value": round(p["Average"], 2)} for p in resp.get("Datapoints", [])],
                key=lambda x: x["time"]
            )
            results[label] = data
        except:
            results[label] = []
            
    return results
