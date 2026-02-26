from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/opensearch", tags=["OpenSearch"])


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
    """Enrich a plain list of SG IDs with inbound/outbound rules."""
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


def _domain_status(d):
    if d.get("Deleted"):
        return "Deleting"
    if d.get("UpgradeProcessing"):
        return "Upgrading"
    if d.get("Processing"):
        return "Processing"
    return "Active"


def _fetch_opensearch(session):
    os_client = session.client("opensearch")
    cw = session.client("cloudwatch")

    names = [d["DomainName"] for d in os_client.list_domain_names().get("DomainNames", [])]
    if not names:
        return {"domains": [], "count": 0}

    domains_detail = os_client.describe_domains(DomainNames=names).get("DomainStatusList", [])

    def fetch_domain(d):
        domain_name = d["DomainName"]
        client_id = d["ARN"].split(":")[4]

        def metric(name):
            try:
                pts = cw.get_metric_statistics(
                    Namespace="AWS/ES",
                    MetricName=name,
                    Dimensions=[
                        {"Name": "DomainName", "Value": domain_name},
                        {"Name": "ClientId",   "Value": client_id},
                    ],
                    StartTime=datetime.utcnow() - timedelta(minutes=10),
                    EndTime=datetime.utcnow(),
                    Period=300,
                    Statistics=["Average"],
                ).get("Datapoints", [])
                return round(pts[-1]["Average"], 1) if pts else None
            except Exception:
                return None

        ec   = d.get("ClusterConfig") or d.get("ElasticsearchClusterConfig") or {}
        ebs  = d.get("EBSOptions", {})
        vpc  = d.get("VPCOptions", {})
        enc  = d.get("EncryptionAtRestOptions", {})
        n2n  = d.get("NodeToNodeEncryptionOptions", {})
        dep  = d.get("DomainEndpointOptions", {})

        return {
            "name":                domain_name,
            "arn":                 d.get("ARN"),
            "engine_version":      d.get("EngineVersion") or d.get("ElasticsearchVersion", "—"),
            "status":              _domain_status(d),
            "instance_type":       ec.get("InstanceType", "—"),
            "instance_count":      ec.get("InstanceCount", 1),
            "dedicated_master":    ec.get("DedicatedMasterEnabled", False),
            "zone_awareness":      ec.get("ZoneAwarenessEnabled", False),
            "ebs_volume_gb":       ebs.get("VolumeSize"),
            "ebs_type":            ebs.get("VolumeType", "—"),
            "encrypted":           enc.get("Enabled", False),
            "node_to_node_enc":    n2n.get("Enabled", False),
            "enforce_https":       dep.get("EnforceHTTPS", False),
            "in_vpc":              bool(vpc.get("VPCId")),
            "endpoint": (
                d.get("Endpoint")
                or (list(d.get("Endpoints", {}).values())[0] if d.get("Endpoints") else None)
            ),
            "cpu_percent":         metric("CPUUtilization"),
            "jvm_memory_percent":  metric("JVMMemoryPressure"),
            "free_storage_mb":     metric("FreeStorageSpace"),
        }

    with ThreadPoolExecutor(max_workers=5) as ex:
        domains = list(ex.map(fetch_domain, domains_detail))

    return {"domains": domains, "count": len(domains)}


@router.get("/domains")
def get_opensearch_domains(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("opensearch", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_opensearch(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail")
def get_opensearch_detail(request: Request, name: str):
    session, _ = get_session_and_config(request)
    os_client = session.client("opensearch")
    try:
        d = os_client.describe_domain(DomainName=name)["DomainStatus"]

        ec  = d.get("ClusterConfig") or d.get("ElasticsearchClusterConfig") or {}
        ebs = d.get("EBSOptions", {})
        vpc = d.get("VPCOptions", {})
        enc = d.get("EncryptionAtRestOptions", {})
        n2n = d.get("NodeToNodeEncryptionOptions", {})
        dep = d.get("DomainEndpointOptions", {})
        adv = d.get("AdvancedSecurityOptions", {})
        sw  = d.get("ServiceSoftwareOptions", {})
        snap = d.get("SnapshotOptions", {})
        zac = ec.get("ZoneAwarenessConfig", {})

        # Tags
        tags = []
        try:
            arn = d.get("ARN")
            if arn:
                tags = [{"key": t["Key"], "value": t["Value"]}
                        for t in os_client.list_tags(ARN=arn).get("TagList", [])]
        except Exception:
            pass

        # Security groups (VPC domains only)
        sg_ids = vpc.get("SecurityGroupIds", [])
        security_groups = _enrich_sg_ids(session, sg_ids)

        endpoint = (
            d.get("Endpoint")
            or (list(d.get("Endpoints", {}).values())[0] if d.get("Endpoints") else None)
        )

        return {
            "name":             d["DomainName"],
            "arn":              d.get("ARN"),
            "engine_version":   d.get("EngineVersion") or d.get("ElasticsearchVersion", "—"),
            "status":           _domain_status(d),
            "endpoint":         endpoint,
            "enforce_https":    dep.get("EnforceHTTPS", False),
            "tls_policy":       dep.get("TLSSecurityPolicy", "—"),
            # Cluster config
            "cluster": {
                "instance_type":    ec.get("InstanceType", "—"),
                "instance_count":   ec.get("InstanceCount", 1),
                "master_enabled":   ec.get("DedicatedMasterEnabled", False),
                "master_type":      ec.get("DedicatedMasterType"),
                "master_count":     ec.get("DedicatedMasterCount"),
                "zone_awareness":   ec.get("ZoneAwarenessEnabled", False),
                "az_count":         zac.get("AvailabilityZoneCount", 2) if ec.get("ZoneAwarenessEnabled") else 1,
                "warm_enabled":     ec.get("WarmEnabled", False),
                "warm_type":        ec.get("WarmType"),
                "warm_count":       ec.get("WarmCount"),
            },
            # Storage
            "ebs": {
                "enabled":    ebs.get("EBSEnabled", False),
                "type":       ebs.get("VolumeType", "—"),
                "size_gb":    ebs.get("VolumeSize"),
                "iops":       ebs.get("Iops"),
                "throughput": ebs.get("Throughput"),
            },
            # Network
            "in_vpc":  bool(vpc.get("VPCId")),
            "vpc_id":  vpc.get("VPCId"),
            "subnets": vpc.get("SubnetIds", []),
            "azs":     vpc.get("AvailabilityZones", []),
            # Security
            "encryption_at_rest":      enc.get("Enabled", False),
            "kms_key":                 enc.get("KmsKeyId", "").split("/")[-1] if enc.get("KmsKeyId") else None,
            "node_to_node_encryption": n2n.get("Enabled", False),
            "fine_grained_access":     adv.get("Enabled", False),
            "internal_user_db":        adv.get("InternalUserDatabaseEnabled", False),
            "security_groups":         security_groups,
            # Maintenance
            "snapshot_hour": snap.get("AutomatedSnapshotStartHour"),
            # Software update
            "software": {
                "current_version":  sw.get("CurrentVersion"),
                "update_available": sw.get("UpdateAvailable", False),
                "new_version":      sw.get("NewVersion"),
                "update_status":    sw.get("UpdateStatus"),
                "optional_deploy":  sw.get("OptionalDeployment", False),
            },
            "tags": tags,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics")
def get_opensearch_metrics(request: Request, name: str, hours: int = 24):
    session, _ = get_session_and_config(request)
    os_client = session.client("opensearch")
    cw = session.client("cloudwatch")

    try:
        d = os_client.describe_domain(DomainName=name)["DomainStatus"]
        client_id = d["ARN"].split(":")[4]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    end_time   = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)
    period     = 60 if hours <= 6 else 300 if hours <= 24 else 3600

    def fetch(metric_name, stat="Average", scale=1.0):
        try:
            pts = cw.get_metric_statistics(
                Namespace="AWS/ES",
                MetricName=metric_name,
                Dimensions=[
                    {"Name": "DomainName", "Value": name},
                    {"Name": "ClientId",   "Value": client_id},
                ],
                StartTime=start_time,
                EndTime=end_time,
                Period=period,
                Statistics=[stat],
            ).get("Datapoints", [])
            return sorted(
                [{"time": p["Timestamp"].isoformat(), "value": round(p[stat] * scale, 4)} for p in pts],
                key=lambda x: x["time"],
            )
        except Exception:
            return []

    return {
        "CPUUtilization":     fetch("CPUUtilization"),
        "JVMMemoryPressure":  fetch("JVMMemoryPressure"),
        "FreeStorageSpace":   fetch("FreeStorageSpace", stat="Minimum", scale=1/1024),  # MB → GB, Minimum recommended
        "SearchRate":         fetch("SearchRate"),
        "IndexingRate":       fetch("IndexingRate"),
        "SysMemoryUtilization": fetch("SysMemoryUtilization"),
    }
