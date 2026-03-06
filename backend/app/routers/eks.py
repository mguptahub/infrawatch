from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL
from ..core.sse_refresh import stream_refresh_done
from ..core.database import get_db
from ..db.models import CollectedResource

router = APIRouter(prefix="/api/eks", tags=["EKS"])
USE_COLLECTOR_DB = True


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


def _enrich_sg_ids(session, sg_ids):
    if not sg_ids:
        return []
    try:
        ec2 = session.client("ec2")
        resp = ec2.describe_security_groups(GroupIds=list(sg_ids))
        return [{
            "id": sg["GroupId"],
            "name": sg.get("GroupName", sg["GroupId"]),
            "description": sg.get("Description", ""),
            "inbound": _fmt_sg_rules(sg.get("IpPermissions", [])),
            "outbound": _fmt_sg_rules(sg.get("IpPermissionsEgress", [])),
        } for sg in resp.get("SecurityGroups", [])]
    except Exception:
        return [{"id": sgid, "name": sgid, "description": "", "inbound": [], "outbound": []}
                for sgid in sg_ids]


def _fetch_eks(session):
    eks = session.client("eks")

    cluster_names = eks.list_clusters()["clusters"]
    if not cluster_names:
        return {"clusters": []}

    def fetch_cluster_details(name):
        c = eks.describe_cluster(name=name)["cluster"]
        ng_list = eks.list_nodegroups(clusterName=name).get("nodegroups", [])

        def fetch_nodegroup(ng_name):
            ng = eks.describe_nodegroup(clusterName=name, nodegroupName=ng_name)["nodegroup"]
            return {
                "name": ng["nodegroupName"],
                "status": ng["status"],
                "instance_types": ng.get("instanceTypes", []),
                "scaling_config": ng.get("scalingConfig", {}),
                "capacity_type": ng.get("capacityType", ""),
                "ami_type": ng.get("amiType", ""),
                "disk_size": ng.get("diskSize"),
                "release_version": ng.get("releaseVersion", ""),
            }

        with ThreadPoolExecutor(max_workers=5) as ng_executor:
            nodegroups = list(ng_executor.map(fetch_nodegroup, ng_list))

        vpc_cfg = c.get("resourcesVpcConfig", {})
        total_nodes = sum(
            ng.get("scaling_config", {}).get("desiredSize", 0)
            for ng in nodegroups
        )

        return {
            "name": c["name"],
            "arn": c["arn"],
            "status": c["status"],
            "version": c["version"],
            "platform_version": c.get("platformVersion"),
            "endpoint": c.get("endpoint"),
            "role_arn": c.get("roleArn"),
            "created_at": c["createdAt"].isoformat() if c.get("createdAt") else None,
            "public_access": vpc_cfg.get("endpointPublicAccess", False),
            "private_access": vpc_cfg.get("endpointPrivateAccess", False),
            "nodegroup_count": len(nodegroups),
            "node_count": total_nodes,
            "nodegroups": nodegroups,
        }

    with ThreadPoolExecutor(max_workers=5) as executor:
        result = list(executor.map(fetch_cluster_details, cluster_names))

    return {"clusters": result}


def _fetch_eks_detail(session, name):
    eks = session.client("eks")
    c = eks.describe_cluster(name=name)["cluster"]

    vpc_cfg = c.get("resourcesVpcConfig", {})
    sg_ids = (
        vpc_cfg.get("securityGroupIds", [])
        + ([vpc_cfg["clusterSecurityGroupId"]] if vpc_cfg.get("clusterSecurityGroupId") else [])
    )

    logging_cfg = c.get("logging", {}).get("clusterLogging", [])
    enabled_logs = []
    for entry in logging_cfg:
        if entry.get("enabled"):
            enabled_logs.extend(entry.get("types", []))

    encryption_cfg = c.get("encryptionConfig", [])
    kms_key = None
    for enc in encryption_cfg:
        if enc.get("provider", {}).get("keyArn"):
            kms_key = enc["provider"]["keyArn"]
            break

    ng_list = eks.list_nodegroups(clusterName=name).get("nodegroups", [])

    def fetch_nodegroup(ng_name):
        ng = eks.describe_nodegroup(clusterName=name, nodegroupName=ng_name)["nodegroup"]
        health_issues = ng.get("health", {}).get("issues", [])
        return {
            "name": ng["nodegroupName"],
            "status": ng["status"],
            "instance_types": ng.get("instanceTypes", []),
            "scaling_config": ng.get("scalingConfig", {}),
            "capacity_type": ng.get("capacityType", ""),
            "ami_type": ng.get("amiType", ""),
            "disk_size": ng.get("diskSize"),
            "release_version": ng.get("releaseVersion", ""),
            "health_issues": health_issues,
        }

    with ThreadPoolExecutor(max_workers=5) as ex:
        nodegroups = list(ex.map(fetch_nodegroup, ng_list))

    security_groups = _enrich_sg_ids(session, list(dict.fromkeys(sg_ids)))

    tags = [{"key": k, "value": v} for k, v in c.get("tags", {}).items()]

    return {
        "name": c["name"],
        "arn": c["arn"],
        "status": c["status"],
        "version": c["version"],
        "platform_version": c.get("platformVersion"),
        "role_arn": c.get("roleArn"),
        "created_at": c["createdAt"].isoformat() if c.get("createdAt") else None,
        "endpoint": c.get("endpoint"),
        "public_access": vpc_cfg.get("endpointPublicAccess", False),
        "private_access": vpc_cfg.get("endpointPrivateAccess", False),
        "public_access_cidrs": vpc_cfg.get("publicAccessCidrs", []),
        "vpc_id": vpc_cfg.get("vpcId"),
        "subnet_ids": vpc_cfg.get("subnetIds", []),
        "security_groups": security_groups,
        "enabled_log_types": enabled_logs,
        "kms_key": kms_key,
        "oidc_issuer": c.get("identity", {}).get("oidc", {}).get("issuer"),
        "nodegroups": nodegroups,
        "tags": tags,
    }


def _fetch_eks_nodes(session, cluster_name):
    ec2 = session.client("ec2")
    cw  = session.client("cloudwatch")

    # Primary filter: standard EKS tag applied to all node types (managed + Karpenter v0.33+)
    resp = ec2.describe_instances(Filters=[
        {"Name": "tag:eks:cluster-name", "Values": [cluster_name]},
        {"Name": "instance-state-name", "Values": ["running", "pending", "stopping", "stopped"]},
    ])

    instances = []
    for reservation in resp.get("Reservations", []):
        instances.extend(reservation.get("Instances", []))

    # Fallback: self-managed nodes tagged with kubernetes.io/cluster/<name>=owned
    if not instances:
        resp2 = ec2.describe_instances(Filters=[
            {"Name": f"tag:kubernetes.io/cluster/{cluster_name}", "Values": ["owned"]},
            {"Name": "instance-state-name", "Values": ["running", "pending", "stopping", "stopped"]},
        ])
        for reservation in resp2.get("Reservations", []):
            instances.extend(reservation.get("Instances", []))

    if not instances:
        return {"nodes": [], "count": 0}

    def _uptime_hours(launch_time):
        if not launch_time:
            return None
        now = datetime.now(timezone.utc)
        if launch_time.tzinfo is None:
            launch_time = launch_time.replace(tzinfo=timezone.utc)
        return round((now - launch_time).total_seconds() / 3600, 1)

    def fetch_node(i):
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        instance_id = i["InstanceId"]
        name        = tags.get("Name", "—")
        nodegroup   = tags.get("eks:nodegroup-name")
        karpenter   = tags.get("karpenter.sh/nodepool") or tags.get("karpenter.sh/provisioner-name")
        private_dns = i.get("PrivateDnsName", "")

        # CPU from EC2 namespace
        cpu = None
        try:
            now = datetime.now(timezone.utc)
            pts = cw.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=now - timedelta(minutes=10),
                EndTime=now,
                Period=300,
                Statistics=["Average"],
            )["Datapoints"]
            if pts:
                cpu = round(sorted(pts, key=lambda x: x["Timestamp"])[-1]["Average"], 1)
        except Exception:
            pass

        # Pod count from Container Insights (requires Container Insights to be enabled)
        pod_count = None
        if private_dns:
            try:
                now = datetime.now(timezone.utc)
                pts = cw.get_metric_statistics(
                    Namespace="ContainerInsights",
                    MetricName="node_number_of_running_pods",
                    Dimensions=[
                        {"Name": "ClusterName", "Value": cluster_name},
                        {"Name": "NodeName",    "Value": private_dns},
                    ],
                    StartTime=now - timedelta(minutes=10),
                    EndTime=now,
                    Period=300,
                    Statistics=["Average"],
                )["Datapoints"]
                if pts:
                    pod_count = int(sorted(pts, key=lambda x: x["Timestamp"])[-1]["Average"])
            except Exception:
                pass

        launch_time = i.get("LaunchTime")
        return {
            "id":             instance_id,
            "name":           name,
            "state":          i["State"]["Name"],
            "type":           i["InstanceType"],
            "az":             i.get("Placement", {}).get("AvailabilityZone", "—"),
            "private_ip":     i.get("PrivateIpAddress"),
            "launch_time":    launch_time.isoformat() if launch_time else None,
            "uptime_hours":   _uptime_hours(launch_time),
            "cpu_percent":    cpu,
            "pod_count":      pod_count,
            "nodegroup_name": nodegroup,
            "karpenter_pool": karpenter,
        }

    with ThreadPoolExecutor(max_workers=10) as ex:
        nodes = list(ex.map(fetch_node, instances))

    return {"nodes": nodes, "count": len(nodes)}


def _list_eks_from_db(region: str, db: Session):
    rows = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "eks", CollectedResource.region == region)
        .all()
    )
    clusters = []
    for r in rows:
        att = r.attributes or {}
        clusters.append({
            "name": r.name or r.resource_id,
            "arn": att.get("arn"),
            "status": att.get("status"),
            "version": att.get("version"),
            "platform_version": att.get("platform_version"),
            "endpoint": att.get("endpoint"),
            "role_arn": att.get("role_arn"),
            "created_at": att.get("created_at"),
            "public_access": att.get("public_access", False),
            "private_access": att.get("private_access", False),
            "nodegroup_count": att.get("nodegroup_count", 0),
            "node_count": att.get("node_count", 0),
            "nodegroups": att.get("nodegroups", []),
        })
    return {"clusters": clusters}


def _detail_eks_from_db(name: str, region: str, db: Session):
    r = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "eks",
            CollectedResource.region == region,
            CollectedResource.resource_id == name,
        )
        .first()
    )
    if not r:
        return None
    att = r.attributes or {}
    return {
        "name": r.name or r.resource_id,
        "arn": att.get("arn"),
        "status": att.get("status"),
        "version": att.get("version"),
        "platform_version": att.get("platform_version"),
        "role_arn": att.get("role_arn"),
        "created_at": att.get("created_at"),
        "endpoint": att.get("endpoint"),
        "public_access": att.get("public_access", False),
        "private_access": att.get("private_access", False),
        "public_access_cidrs": att.get("public_access_cidrs", []),
        "vpc_id": att.get("vpc_id"),
        "subnet_ids": att.get("subnet_ids", []),
        "security_groups": att.get("security_groups", []),
        "enabled_log_types": att.get("enabled_log_types", []),
        "kms_key": att.get("kms_key"),
        "oidc_issuer": att.get("oidc_issuer"),
        "nodegroups": att.get("nodegroups", []),
        "tags": att.get("tags", []),
    }


def _nodes_eks_from_db(cluster_name: str, region: str, db: Session):
    """Return {nodes: [...], count: N} from stored cluster attributes; enrich cpu_percent from collected_metrics."""
    from ..db.models import CollectedMetric
    r = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "eks",
            CollectedResource.region == region,
            CollectedResource.resource_id == cluster_name,
        )
        .first()
    )
    if not r:
        return None
    nodes = list((r.attributes or {}).get("nodes", []))
    if not nodes:
        return {"nodes": [], "count": 0}
    cutoff = datetime.utcnow() - timedelta(hours=1)
    for node in nodes:
        instance_id = node.get("id")
        if not instance_id:
            continue
        m = (
            db.query(CollectedMetric)
            .filter(
                CollectedMetric.service_type == "ec2",
                CollectedMetric.resource_id == instance_id,
                CollectedMetric.region == region,
                CollectedMetric.metric_name == "CPUUtilization",
                CollectedMetric.timestamp >= cutoff,
            )
            .order_by(CollectedMetric.timestamp.desc())
            .first()
        )
        if m:
            node["cpu_percent"] = round(m.value, 1)
    return {"nodes": nodes, "count": len(nodes)}


@router.get("/clusters")
def get_eks_clusters(request: Request, force: bool = False, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        return _list_eks_from_db(config.region, db)
    session, config2 = get_session_and_config(request)
    key = make_cache_key("eks", config2.access_key or "", config2.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_eks(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clusters/{name}")
def get_eks_cluster_detail(name: str, request: Request, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        detail = _detail_eks_from_db(name, config.region, db)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"EKS cluster '{name}' not found")
        return detail
    try:
        session, _ = get_session_and_config(request)
        return _fetch_eks_detail(session, name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise HTTPException(status_code=404, detail=f"EKS cluster '{name}' not found")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh/stream")
def get_eks_refresh_stream(request: Request):
    """SSE stream: emits refresh_done when the collector for this region finishes."""
    _, config = get_session_and_config(request)
    channel = f"refresh:eks:{config.region}"
    return StreamingResponse(
        stream_refresh_done(channel),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/refresh")
def post_eks_refresh(request: Request):
    _, config = get_session_and_config(request)
    from app.tasks.collect_tasks import collect_resources
    collect_resources.delay("eks", config.region)
    return {"ok": True, "message": "Refresh started for region " + config.region}


@router.get("/clusters/{name}/nodes")
def get_eks_cluster_nodes(name: str, request: Request, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        result = _nodes_eks_from_db(name, config.region, db)
        if result is None:
            raise HTTPException(status_code=404, detail=f"EKS cluster '{name}' not found")
        return result
    try:
        session, _ = get_session_and_config(request)
        return _fetch_eks_nodes(session, cluster_name=name)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
