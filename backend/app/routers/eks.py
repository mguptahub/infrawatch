from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/eks", tags=["EKS"])


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

    from datetime import datetime, timedelta, timezone

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
            pts = cw.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=datetime.utcnow() - timedelta(minutes=10),
                EndTime=datetime.utcnow(),
                Period=300,
                Statistics=["Average"],
            )["Datapoints"]
            if pts:
                cpu = round(sorted(pts, key=lambda x: x["Timestamp"])[-1]["Average"], 1)
        except Exception:
            pass

        # Pod count from Container Insights (requires Container Insights to be enabled)
        pod_count = None
        try:
            pts = cw.get_metric_statistics(
                Namespace="ContainerInsights",
                MetricName="node_number_of_running_pods",
                Dimensions=[
                    {"Name": "ClusterName", "Value": cluster_name},
                    {"Name": "NodeName",    "Value": private_dns},
                ],
                StartTime=datetime.utcnow() - timedelta(minutes=10),
                EndTime=datetime.utcnow(),
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


@router.get("/clusters")
def get_eks_clusters(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("eks", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_eks(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clusters/{name}")
def get_eks_cluster_detail(name: str, request: Request):
    session, _ = get_session_and_config(request)
    try:
        return _fetch_eks_detail(session, name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise HTTPException(status_code=404, detail=f"EKS cluster '{name}' not found")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/clusters/{name}/nodes")
def get_eks_cluster_nodes(name: str, request: Request):
    session, _ = get_session_and_config(request)
    try:
        return _fetch_eks_nodes(session, cluster_name=name)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
