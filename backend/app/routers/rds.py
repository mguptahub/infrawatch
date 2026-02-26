from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/rds", tags=["RDS"])


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


def _enrich_security_groups(session, vpc_sg_list):
    """Enrich VpcSecurityGroups (VpcSecurityGroupId) with inbound/outbound rules."""
    if not vpc_sg_list:
        return []
    sg_ids = [sg["VpcSecurityGroupId"] for sg in vpc_sg_list if sg.get("VpcSecurityGroupId")]
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
        return [{"id": sg["VpcSecurityGroupId"], "name": sg["VpcSecurityGroupId"],
                 "description": "", "inbound": [], "outbound": []} for sg in vpc_sg_list]


def _snapshot(cw, db_id, metric, stat="Average"):
    try:
        pts = cw.get_metric_statistics(
            Namespace="AWS/RDS",
            MetricName=metric,
            Dimensions=[{"Name": "DBInstanceIdentifier", "Value": db_id}],
            StartTime=datetime.utcnow() - timedelta(minutes=10),
            EndTime=datetime.utcnow(),
            Period=300,
            Statistics=[stat],
        ).get("Datapoints", [])
        return round(pts[-1][stat], 1) if pts else None
    except Exception:
        return None


def _fetch_rds(session):
    rds = session.client("rds")
    cw = session.client("cloudwatch")

    # Fetch cluster metadata (may fail on accounts without Aurora/Multi-AZ clusters)
    try:
        cluster_meta = {c["DBClusterIdentifier"]: c
                        for c in rds.describe_db_clusters().get("DBClusters", [])}
    except Exception:
        cluster_meta = {}

    db_instances = rds.describe_db_instances().get("DBInstances", [])
    instance_map = {i["DBInstanceIdentifier"]: i for i in db_instances}

    # Group instances by DBClusterIdentifier — this is the authoritative source
    # of cluster membership, even when describe_db_clusters is unavailable.
    cluster_groups = {}   # cluster_id -> [instance, ...]
    for i in db_instances:
        cid = i.get("DBClusterIdentifier")
        if cid:
            cluster_groups.setdefault(cid, []).append(i)

    cluster_member_ids = {
        i["DBInstanceIdentifier"]
        for insts in cluster_groups.values()
        for i in insts
    }

    def process_cluster(cluster_id):
        c = cluster_meta.get(cluster_id, {})
        insts = cluster_groups[cluster_id]

        # Identify writer: use official cluster data if available, else first instance
        official_members = c.get("DBClusterMembers", [])
        if official_members:
            writer_id = next(
                (m["DBInstanceIdentifier"] for m in official_members if m["IsClusterWriter"]),
                insts[0]["DBInstanceIdentifier"],
            )
            member_list = sorted([{
                "id": m["DBInstanceIdentifier"],
                "role": "Writer" if m["IsClusterWriter"] else "Reader",
                "class": instance_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceClass", "—"),
                "az":    instance_map.get(m["DBInstanceIdentifier"], {}).get("AvailabilityZone", "—"),
                "status": instance_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceStatus", "—"),
            } for m in official_members], key=lambda x: 0 if x["role"] == "Writer" else 1)
        else:
            # Fallback: synthesise from instance list (no IsClusterWriter info)
            writer_id = insts[0]["DBInstanceIdentifier"]
            member_list = [{
                "id": i["DBInstanceIdentifier"],
                "role": "Writer" if idx == 0 else "Reader",
                "class": i.get("DBInstanceClass", "—"),
                "az":    i.get("AvailabilityZone", "—"),
                "status": i.get("DBInstanceStatus", "—"),
            } for idx, i in enumerate(insts)]

        writer_inst = instance_map.get(writer_id, {})
        cpu      = _snapshot(cw, writer_id, "CPUUtilization")
        conn_raw = _snapshot(cw, writer_id, "DatabaseConnections")

        # Prefer cluster-level fields; fall back to representative instance
        ref = insts[0]
        return {
            "id":                cluster_id,
            "type":              "cluster",
            "engine":            c.get("Engine")          or ref.get("Engine", "—"),
            "version":           c.get("EngineVersion")   or ref.get("EngineVersion", "—"),
            "status":            c.get("Status")          or ref.get("DBInstanceStatus", "—"),
            "instance_count":    len(insts),
            "writer_id":         writer_id,
            "writer_class":      writer_inst.get("DBInstanceClass", "—"),
            "multi_az":          c.get("MultiAZ", len(insts) > 1),
            "encrypted":         c.get("StorageEncrypted", ref.get("StorageEncrypted", False)),
            "storage_type":      c.get("StorageType")     or ref.get("StorageType", "—"),
            "endpoint":          c.get("Endpoint"),
            "port":              c.get("Port")            or (ref.get("Endpoint") or {}).get("Port"),
            "deletion_protection": c.get("DeletionProtection", False),
            "cpu_percent":       cpu,
            "connections":       int(conn_raw) if conn_raw is not None else None,
            "members":           member_list,
        }

    def process_instance(i):
        iid = i["DBInstanceIdentifier"]
        cpu      = _snapshot(cw, iid, "CPUUtilization")
        conn_raw = _snapshot(cw, iid, "DatabaseConnections")
        return {
            "id":                iid,
            "type":              "instance",
            "engine":            i.get("Engine", "—"),
            "version":           i.get("EngineVersion", "—"),
            "class":             i["DBInstanceClass"],
            "status":            i["DBInstanceStatus"],
            "az":                i.get("AvailabilityZone", "—"),
            "multi_az":          i.get("MultiAZ", False),
            "storage_gb":        i.get("AllocatedStorage", 0),
            "storage_type":      i.get("StorageType", "—"),
            "encrypted":         i.get("StorageEncrypted", False),
            "endpoint":          (i.get("Endpoint") or {}).get("Address"),
            "port":              (i.get("Endpoint") or {}).get("Port"),
            "deletion_protection": i.get("DeletionProtection", False),
            "cpu_percent":       cpu,
            "connections":       int(conn_raw) if conn_raw is not None else None,
        }

    standalone = [i for i in db_instances if i["DBInstanceIdentifier"] not in cluster_member_ids]

    with ThreadPoolExecutor(max_workers=10) as ex:
        clusters  = list(ex.map(process_cluster,  cluster_groups.keys()))
        instances = list(ex.map(process_instance, standalone))

    return {
        "clusters":  clusters,
        "instances": instances,
        "total":     len(clusters) + len(instances),
    }


@router.get("/instances")
def get_rds_instances(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("rds", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_rds(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail")
def get_rds_detail(request: Request, id: str, is_cluster: bool = False):
    session, _ = get_session_and_config(request)
    rds = session.client("rds")

    def get_tags(arn):
        try:
            return [{"key": t["Key"], "value": t["Value"]}
                    for t in rds.list_tags_for_resource(ResourceArn=arn).get("TagList", [])]
        except Exception:
            return []

    try:
        if is_cluster:
            c = rds.describe_db_clusters(DBClusterIdentifier=id).get("DBClusters", [{}])[0]

            # Fetch member instance details in one call using cluster filter
            member_instances = rds.describe_db_instances(
                Filters=[{"Name": "db-cluster-id", "Values": [id]}]
            ).get("DBInstances", [])
            member_map = {i["DBInstanceIdentifier"]: i for i in member_instances}

            members = sorted([{
                "id": m["DBInstanceIdentifier"],
                "role": "Writer" if m["IsClusterWriter"] else "Reader",
                "class": member_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceClass", "—"),
                "az": member_map.get(m["DBInstanceIdentifier"], {}).get("AvailabilityZone", "—"),
                "status": member_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceStatus", "—"),
                "parameter_group": (member_map.get(m["DBInstanceIdentifier"], {})
                                    .get("DBParameterGroups", [{}])[0]
                                    .get("DBParameterGroupName", "—")),
                "performance_insights": member_map.get(m["DBInstanceIdentifier"], {}).get("PerformanceInsightsEnabled", False),
            } for m in c.get("DBClusterMembers", [])], key=lambda x: 0 if x["role"] == "Writer" else 1)

            arn = c.get("DBClusterArn")
            return {
                "type": "cluster",
                "id": c["DBClusterIdentifier"],
                "engine": c.get("Engine", "—"),
                "version": c.get("EngineVersion", "—"),
                "status": c.get("Status", "—"),
                "multi_az": c.get("MultiAZ", len(members) > 1),
                "endpoint": c.get("Endpoint"),
                "reader_endpoint": c.get("ReaderEndpoint"),
                "port": c.get("Port"),
                "storage_gb": c.get("AllocatedStorage", 0),
                "storage_type": c.get("StorageType", "—"),
                "encrypted": c.get("StorageEncrypted", False),
                "kms_key": c.get("KmsKeyId", "").split("/")[-1] if c.get("KmsKeyId") else None,
                "backup_retention": c.get("BackupRetentionPeriod", 0),
                "backup_window": c.get("PreferredBackupWindow", "—"),
                "maintenance_window": c.get("PreferredMaintenanceWindow", "—"),
                "deletion_protection": c.get("DeletionProtection", False),
                "iam_auth": c.get("IAMDatabaseAuthenticationEnabled", False),
                "cluster_parameter_group": c.get("DBClusterParameterGroup", "—"),
                "created_at": c.get("ClusterCreateTime").isoformat() if c.get("ClusterCreateTime") else None,
                "members": members,
                "security_groups": _enrich_security_groups(session, c.get("VpcSecurityGroups", [])),
                "tags": get_tags(arn) if arn else [],
            }
        else:
            i = rds.describe_db_instances(DBInstanceIdentifier=id).get("DBInstances", [{}])[0]
            arn = i.get("DBInstanceArn")
            return {
                "type": "instance",
                "id": i["DBInstanceIdentifier"],
                "engine": i.get("Engine", "—"),
                "version": i.get("EngineVersion", "—"),
                "class": i.get("DBInstanceClass", "—"),
                "status": i.get("DBInstanceStatus", "—"),
                "az": i.get("AvailabilityZone", "—"),
                "multi_az": i.get("MultiAZ", False),
                "endpoint": i.get("Endpoint", {}).get("Address"),
                "port": i.get("Endpoint", {}).get("Port"),
                "storage_gb": i.get("AllocatedStorage", 0),
                "storage_type": i.get("StorageType", "—"),
                "encrypted": i.get("StorageEncrypted", False),
                "kms_key": i.get("KmsKeyId", "").split("/")[-1] if i.get("KmsKeyId") else None,
                "backup_retention": i.get("BackupRetentionPeriod", 0),
                "backup_window": i.get("PreferredBackupWindow", "—"),
                "maintenance_window": i.get("PreferredMaintenanceWindow", "—"),
                "deletion_protection": i.get("DeletionProtection", False),
                "iam_auth": i.get("IAMDatabaseAuthenticationEnabled", False),
                "publicly_accessible": i.get("PubliclyAccessible", False),
                "auto_minor_upgrade": i.get("AutoMinorVersionUpgrade", False),
                "parameter_group": (i.get("DBParameterGroups", [{}])[0]
                                    .get("DBParameterGroupName", "—")),
                "option_group": (i.get("OptionGroupMemberships", [{}])[0]
                                 .get("OptionGroupName", "—")),
                "performance_insights": i.get("PerformanceInsightsEnabled", False),
                "created_at": i.get("InstanceCreateTime").isoformat() if i.get("InstanceCreateTime") else None,
                "security_groups": _enrich_security_groups(session, i.get("VpcSecurityGroups", [])),
                "tags": get_tags(arn) if arn else [],
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/metrics")
def get_rds_metrics(request: Request, id: str, is_cluster: bool = False, hours: int = 24):
    session, _ = get_session_and_config(request)
    rds_client = session.client("rds")
    cw = session.client("cloudwatch")

    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)
    period = 60 if hours <= 6 else 300 if hours <= 24 else 3600

    # For clusters, use the writer instance ID for per-instance CW dimensions
    dim_value = id
    if is_cluster:
        try:
            c = rds_client.describe_db_clusters(DBClusterIdentifier=id).get("DBClusters", [{}])[0]
            writer = next(
                (m["DBInstanceIdentifier"] for m in c.get("DBClusterMembers", []) if m["IsClusterWriter"]),
                None,
            )
            if writer:
                dim_value = writer
        except Exception:
            pass

    GB = 1 / (1024 ** 3)

    def fetch(metric, stat="Average", scale=1.0):
        try:
            pts = cw.get_metric_statistics(
                Namespace="AWS/RDS",
                MetricName=metric,
                Dimensions=[{"Name": "DBInstanceIdentifier", "Value": dim_value}],
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
        "DatabaseConnections": fetch("DatabaseConnections"),
        "FreeStorageSpace":   fetch("FreeStorageSpace", scale=GB),
        "FreeableMemory":     fetch("FreeableMemory", scale=GB),
        "ReadIOPS":           fetch("ReadIOPS"),
        "WriteIOPS":          fetch("WriteIOPS"),
        "ReadLatency":        fetch("ReadLatency", scale=1000),   # seconds → ms
        "WriteLatency":       fetch("WriteLatency", scale=1000),
    }
