"""
Celery tasks: collect AWS resources and CloudWatch metrics into DB.
Uses power-account credentials (AssumeRole) to run describe_* and get_metric_*.
Metrics: incremental pull — fetch only from last stored timestamp to now (max 72h).
"""
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from botocore.exceptions import ClientError

from celery import current_task, chord, group
from app.celery_app import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.sts_service import assume_role_for_services, ALL_SERVICES
from app.core.valkey_client import get_client
from app.db.models import CollectedResource, CollectedMetric, CollectedAlarm, CollectedHealthEvent

logger = logging.getLogger(__name__)


def _flush_logs():
    """Flush logger streams so progress appears immediately in docker compose logs -f."""
    for h in logger.root.handlers:
        if getattr(h, "stream", None) and getattr(h.stream, "flush", None):
            try:
                h.stream.flush()
            except Exception:
                pass


METRICS_UQ_COLS = ["service_type", "resource_id", "region", "metric_name", "timestamp"]


def _last_metric_timestamp(db, service_type: str, resource_id: str, region: str):
    """Return the latest timestamp we have in collected_metrics for this (service_type, resource_id, region), or None."""
    return db.query(func.max(CollectedMetric.timestamp)).filter(
        CollectedMetric.service_type == service_type,
        CollectedMetric.resource_id == resource_id,
        CollectedMetric.region == region,
    ).scalar()


def _metrics_start_end(db, service_type: str, resource_id: str, region: str):
    """
    Return (start, end) for CloudWatch fetch: from last stored point to now, capped at METRICS_RETENTION_HOURS.
    If no previous data, start = now - 72h.
    """
    end = datetime.utcnow()
    last_ts = _last_metric_timestamp(db, service_type, resource_id, region)
    if last_ts is None:
        start = end - timedelta(hours=METRICS_RETENTION_HOURS)
    else:
        # Fetch from last point to now, but cap window at 72h
        start = max(last_ts, end - timedelta(hours=METRICS_RETENTION_HOURS))
    return start, end


def _publish_refresh_done(service_type: str, region: str) -> None:
    """Notify SSE listeners that a refresh for this service/region completed."""
    try:
        get_client().publish(f"refresh:{service_type}:{region}", "done")
    except Exception as e:
        logger.warning("publish refresh_done %s %s: %s", service_type, region, e)

# Services to collect; add collectors below and extend API routers to read from DB.
COLLECTOR_SERVICE_TYPES = ["ec2", "eks", "elb", "rds", "docdb", "opensearch", "elasticache", "mq", "secrets", "iam"]
METRICS_RETENTION_HOURS = 72


def _get_session():
    """Assume role with all services and return a boto3 session."""
    creds = assume_role_for_services(
        services=ALL_SERVICES,
        duration_hours=1,
        session_name="celery-collector",
    )
    import boto3
    return boto3.Session(
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        aws_session_token=creds["session_token"],
        region_name=creds["region"],
    )


NAMESPACE_TO_SERVICE = {
    "AWS/EC2": "ec2",
    "AWS/EKS": "eks",
    "AWS/RDS": "databases",
    "AWS/DocDB": "databases",
    "AWS/ElastiCache": "elasticache",
    "AWS/ES": "opensearch",
    "AWS/AmazonMQ": "mq",
    "AWS/SES": "ses",
    "AWS/ELB": "elb",
    "AWS/ApplicationELB": "elb",
    "AWS/NetworkELB": "elb",
}

_DIMENSION_ID_KEYS = [
    "InstanceId", "DBInstanceIdentifier", "ClusterName", "DomainName",
    "BrokerId", "CacheClusterId", "LoadBalancer", "TargetGroup",
    "ReplicationGroupId", "FunctionName",
]


def _extract_resource_id_from_dimensions(dimensions):
    """Extract resource ID from CloudWatch alarm dimensions.

    Checks common dimension names (InstanceId, DBInstanceIdentifier, etc.)
    and falls back to the first dimension value if none match.
    """
    if not dimensions:
        return None
    for dim in dimensions:
        if dim.get("Name") in _DIMENSION_ID_KEYS:
            return dim.get("Value")
    # Fallback: first dimension value
    if dimensions:
        return dimensions[0].get("Value")
    return None


def _collect_alarms_region(session, region: str, db):
    """Fetch CloudWatch alarms in one region and upsert into collected_alarms."""
    cw = session.client("cloudwatch", region_name=region)
    paginator = cw.get_paginator("describe_alarms")
    count = 0
    now = datetime.utcnow()
    seen_arns = set()

    for page in paginator.paginate():
        for a in page.get("MetricAlarms", []):
            arn = a["AlarmArn"]
            seen_arns.add(arn)
            namespace = a.get("Namespace")
            dims = a.get("Dimensions", [])
            service_type = NAMESPACE_TO_SERVICE.get(namespace)
            resource_id = _extract_resource_id_from_dimensions(dims)
            state_updated = a.get("StateUpdatedTimestamp")

            stmt = pg_insert(CollectedAlarm).values(
                alarm_name=a["AlarmName"],
                alarm_arn=arn,
                service_type=service_type,
                resource_id=resource_id,
                region=region,
                state=a.get("StateValue", "INSUFFICIENT_DATA"),
                state_reason=a.get("StateReason"),
                state_updated_at=state_updated,
                metric_name=a.get("MetricName"),
                namespace=namespace,
                dimensions=dims or None,
                collected_at=now,
            ).on_conflict_do_update(
                index_elements=["alarm_arn"],
                set_={
                    "alarm_name": a["AlarmName"],
                    "service_type": service_type,
                    "resource_id": resource_id,
                    "state": a.get("StateValue", "INSUFFICIENT_DATA"),
                    "state_reason": a.get("StateReason"),
                    "state_updated_at": state_updated,
                    "metric_name": a.get("MetricName"),
                    "namespace": namespace,
                    "dimensions": dims or None,
                    "collected_at": now,
                },
            )
            db.execute(stmt)
            count += 1

        # Also collect composite alarms (they lack Namespace/Dimensions)
        for a in page.get("CompositeAlarms", []):
            arn = a["AlarmArn"]
            seen_arns.add(arn)
            state_updated = a.get("StateUpdatedTimestamp")

            stmt = pg_insert(CollectedAlarm).values(
                alarm_name=a["AlarmName"],
                alarm_arn=arn,
                service_type=None,
                resource_id=None,
                region=region,
                state=a.get("StateValue", "INSUFFICIENT_DATA"),
                state_reason=a.get("StateReason"),
                state_updated_at=state_updated,
                metric_name=None,
                namespace=None,
                dimensions=None,
                collected_at=now,
            ).on_conflict_do_update(
                index_elements=["alarm_arn"],
                set_={
                    "alarm_name": a["AlarmName"],
                    "service_type": None,
                    "resource_id": None,
                    "state": a.get("StateValue", "INSUFFICIENT_DATA"),
                    "state_reason": a.get("StateReason"),
                    "state_updated_at": state_updated,
                    "collected_at": now,
                },
            )
            db.execute(stmt)
            count += 1

    # Delete stale alarms in this region that AWS no longer returns
    if seen_arns:
        db.query(CollectedAlarm).filter(
            CollectedAlarm.region == region,
            ~CollectedAlarm.alarm_arn.in_(seen_arns),
        ).delete(synchronize_session=False)
    else:
        # No alarms returned — delete all for this region
        db.query(CollectedAlarm).filter(
            CollectedAlarm.region == region,
        ).delete(synchronize_session=False)

    db.commit()
    logger.info("collect_alarms %s: %d alarms", region, count)
    _flush_logs()
    return count


def _collect_ec2_region(session, region: str, db):
    """Fetch EC2 instances in one region and upsert into collected_resources."""
    ec2 = session.client("ec2", region_name=region)
    paginator = ec2.get_paginator("describe_instances")
    count = 0
    now = datetime.utcnow()

    for page in paginator.paginate():
        for reservation in page.get("Reservations", []):
            for i in reservation.get("Instances", []):
                resource_id = i["InstanceId"]
                name = next(
                    (t["Value"] for t in i.get("Tags", []) if t.get("Key") == "Name"),
                    None,
                ) or resource_id
                launch_time = i.get("LaunchTime")
                attributes = {
                    "state": i.get("State", {}).get("Name"),
                    "instance_type": i.get("InstanceType"),
                    "availability_zone": i.get("Placement", {}).get("AvailabilityZone"),
                    "private_ip": i.get("PrivateIpAddress"),
                    "public_ip": i.get("PublicIpAddress"),
                    "launch_time": launch_time.isoformat() if launch_time else None,
                    "vpc_id": i.get("VpcId"),
                    "subnet_id": i.get("SubnetId"),
                    "key_name": i.get("KeyName"),
                    "architecture": i.get("Architecture"),
                    "ami_id": i.get("ImageId"),
                    "tags": [{"key": t["Key"], "value": t["Value"]} for t in i.get("Tags", [])],
                }
                if i.get("IamInstanceProfile", {}).get("Arn"):
                    arn = i["IamInstanceProfile"]["Arn"]
                    attributes["iam_profile"] = arn.split("/")[-1] if "/" in arn else arn
                stmt = pg_insert(CollectedResource).values(
                    service_type="ec2",
                    region=region,
                    resource_id=resource_id,
                    name=name,
                    attributes=attributes,
                    collected_at=now,
                ).on_conflict_do_update(
                    index_elements=["service_type", "region", "resource_id"],
                    set_={
                        "name": name,
                        "attributes": attributes,
                        "collected_at": now,
                    },
                )
                db.execute(stmt)
                count += 1
    db.commit()
    return count


def _fetch_eks_nodes_for_cluster(session, region: str, cluster_name: str):
    """Return list of node dicts (id, name, state, type, az, private_ip, launch_time, uptime_hours, nodegroup_name, karpenter_pool) for the cluster."""
    ec2 = session.client("ec2", region_name=region)
    instances = []
    try:
        resp = ec2.describe_instances(Filters=[
            {"Name": "tag:eks:cluster-name", "Values": [cluster_name]},
            {"Name": "instance-state-name", "Values": ["running", "pending", "stopping", "stopped"]},
        ])
        for r in resp.get("Reservations", []):
            instances.extend(r.get("Instances", []))
    except ClientError:
        pass
    if not instances:
        try:
            resp = ec2.describe_instances(Filters=[
                {"Name": f"tag:kubernetes.io/cluster/{cluster_name}", "Values": ["owned"]},
                {"Name": "instance-state-name", "Values": ["running", "pending", "stopping", "stopped"]},
            ])
            for r in resp.get("Reservations", []):
                instances.extend(r.get("Instances", []))
        except ClientError:
            pass
    nodes = []
    for i in instances:
        tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
        launch_time = i.get("LaunchTime")
        lt_iso = launch_time.isoformat() if launch_time else None
        uptime = None
        if launch_time:
            now = datetime.now(timezone.utc)
            if launch_time.tzinfo is None:
                launch_time = launch_time.replace(tzinfo=timezone.utc)
            uptime = round((now - launch_time).total_seconds() / 3600, 1)
        nodes.append({
            "id": i["InstanceId"],
            "name": tags.get("Name", "—"),
            "state": i.get("State", {}).get("Name", "—"),
            "type": i.get("InstanceType", "—"),
            "az": i.get("Placement", {}).get("AvailabilityZone", "—"),
            "private_ip": i.get("PrivateIpAddress"),
            "launch_time": lt_iso,
            "uptime_hours": uptime,
            "cpu_percent": None,
            "pod_count": None,
            "nodegroup_name": tags.get("eks:nodegroup-name"),
            "karpenter_pool": tags.get("karpenter.sh/nodepool") or tags.get("karpenter.sh/provisioner-name"),
        })
    return nodes


def _collect_eks_region(session, region: str, db):
    """Fetch EKS clusters in one region; upsert into collected_resources."""
    eks = session.client("eks", region_name=region)
    try:
        names = eks.list_clusters().get("clusters", [])
    except ClientError:
        return 0
    if not names:
        return 0
    now = datetime.utcnow()
    count = 0
    for name in names:
        try:
            c = eks.describe_cluster(name=name)["cluster"]
            ng_list = eks.list_nodegroups(clusterName=name).get("nodegroups", [])
            nodegroups = []
            for ng_name in ng_list:
                try:
                    ng = eks.describe_nodegroup(clusterName=name, nodegroupName=ng_name)["nodegroup"]
                    nodegroups.append({
                        "name": ng.get("nodegroupName"),
                        "status": ng.get("status"),
                        "instance_types": ng.get("instanceTypes", []),
                        "scaling_config": ng.get("scalingConfig", {}),
                        "capacity_type": ng.get("capacityType", ""),
                        "ami_type": ng.get("amiType", ""),
                        "disk_size": ng.get("diskSize"),
                        "release_version": ng.get("releaseVersion", ""),
                    })
                except Exception:
                    pass
            vpc_cfg = c.get("resourcesVpcConfig", {})
            total_nodes = sum(
                ng.get("scaling_config", {}).get("desiredSize", 0) for ng in nodegroups
            )
            # Fetch EC2 instances that belong to this cluster (for Nodes side panel)
            nodes = _fetch_eks_nodes_for_cluster(session, region, name)
            attributes = {
                "arn": c.get("arn"),
                "status": c.get("status"),
                "version": c.get("version"),
                "platform_version": c.get("platformVersion"),
                "endpoint": c.get("endpoint"),
                "role_arn": c.get("roleArn"),
                "created_at": c["createdAt"].isoformat() if c.get("createdAt") else None,
                "public_access": vpc_cfg.get("endpointPublicAccess", False),
                "private_access": vpc_cfg.get("endpointPrivateAccess", False),
                "nodegroup_count": len(nodegroups),
                "node_count": total_nodes,
                "nodegroups": nodegroups,
                "nodes": nodes,
                "vpc_id": vpc_cfg.get("vpcId"),
                "subnet_ids": vpc_cfg.get("subnetIds", []),
            }
            stmt = pg_insert(CollectedResource).values(
                service_type="eks",
                region=region,
                resource_id=name,
                name=name,
                attributes=attributes,
                collected_at=now,
            ).on_conflict_do_update(
                index_elements=["service_type", "region", "resource_id"],
                set_={"name": name, "attributes": attributes, "collected_at": now},
            )
            db.execute(stmt)
            count += 1
        except Exception as e:
            logger.warning("eks cluster %s: %s", name, e)
    db.commit()
    return count


def _collect_elb_region(session, region: str, db):
    """Fetch ALB/NLB/Classic LBs in one region; upsert into collected_resources."""
    elbv2 = session.client("elbv2", region_name=region)
    elb_classic = session.client("elb", region_name=region)
    now = datetime.utcnow()
    count = 0
    for page in elbv2.get_paginator("describe_load_balancers").paginate():
        for lb in page.get("LoadBalancers", []):
            resource_id = lb.get("LoadBalancerArn") or lb.get("LoadBalancerName", "")
            name = lb.get("LoadBalancerName", "—")
            created = lb.get("CreatedTime")
            attributes = {
                "arn": lb.get("LoadBalancerArn"),
                "type": (lb.get("Type") or "").upper(),
                "state": lb.get("State", {}).get("Code", "unknown"),
                "scheme": lb.get("Scheme", "—"),
                "dns": lb.get("DNSName", "—"),
                "vpc_id": lb.get("VpcId") or "—",
                "azs": [a.get("ZoneName", "") for a in lb.get("AvailabilityZones", []) if a.get("ZoneName")],
                "created_at": created.isoformat() if created else None,
                "generation": "v2",
            }
            stmt = pg_insert(CollectedResource).values(
                service_type="elb",
                region=region,
                resource_id=resource_id,
                name=name,
                attributes=attributes,
                collected_at=now,
            ).on_conflict_do_update(
                index_elements=["service_type", "region", "resource_id"],
                set_={"name": name, "attributes": attributes, "collected_at": now},
            )
            db.execute(stmt)
            count += 1
    try:
        for lb in elb_classic.describe_load_balancers().get("LoadBalancerDescriptions", []):
            name = lb.get("LoadBalancerName", "—")
            resource_id = name
            created = lb.get("CreatedTime")
            attributes = {
                "arn": None,
                "type": "CLASSIC",
                "state": "active",
                "scheme": lb.get("Scheme", "—"),
                "dns": lb.get("DNSName", "—"),
                "vpc_id": lb.get("VPCId") or "—",
                "azs": lb.get("AvailabilityZones", []),
                "created_at": created.isoformat() if created else None,
                "generation": "classic",
            }
            stmt = pg_insert(CollectedResource).values(
                service_type="elb",
                region=region,
                resource_id=resource_id,
                name=name,
                attributes=attributes,
                collected_at=now,
            ).on_conflict_do_update(
                index_elements=["service_type", "region", "resource_id"],
                set_={"name": name, "attributes": attributes, "collected_at": now},
            )
            db.execute(stmt)
            count += 1
    except Exception as e:
        logger.warning("classic elb %s: %s", region, e)
    db.commit()
    return count


def _collect_rds_region(session, region: str, db):
    """Fetch RDS clusters and instances in one region; upsert into collected_resources."""
    rds = session.client("rds", region_name=region)
    now = datetime.utcnow()
    count = 0
    try:
        cluster_meta = {c["DBClusterIdentifier"]: c for c in rds.describe_db_clusters().get("DBClusters", [])}
    except Exception:
        cluster_meta = {}
    try:
        db_instances = rds.describe_db_instances().get("DBInstances", [])
    except ClientError:
        return 0
    instance_map = {i["DBInstanceIdentifier"]: i for i in db_instances}
    cluster_groups = {}
    for i in db_instances:
        cid = i.get("DBClusterIdentifier")
        if cid:
            cluster_groups.setdefault(cid, []).append(i)
    cluster_member_ids = {i["DBInstanceIdentifier"] for insts in cluster_groups.values() for i in insts}

    for cluster_id, insts in cluster_groups.items():
        c = cluster_meta.get(cluster_id, {})
        ref = insts[0]
        # Only store RDS/Aurora; skip DocumentDB (docdb has its own collector)
        engine = (c.get("Engine") or ref.get("Engine") or "").lower()
        if "docdb" in engine:
            continue
        official_members = c.get("DBClusterMembers", [])
        if official_members:
            member_list = sorted([
                {
                    "id": m["DBInstanceIdentifier"],
                    "role": "Writer" if m["IsClusterWriter"] else "Reader",
                    "class": instance_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceClass", "—"),
                    "az": instance_map.get(m["DBInstanceIdentifier"], {}).get("AvailabilityZone", "—"),
                    "status": instance_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceStatus", "—"),
                }
                for m in official_members
            ], key=lambda x: 0 if x["role"] == "Writer" else 1)
            writer_id = next((m["DBInstanceIdentifier"] for m in official_members if m["IsClusterWriter"]), insts[0]["DBInstanceIdentifier"])
        else:
            writer_id = insts[0]["DBInstanceIdentifier"]
            member_list = [
                {"id": i["DBInstanceIdentifier"], "role": "Writer" if idx == 0 else "Reader", "class": i.get("DBInstanceClass", "—"),
                 "az": i.get("AvailabilityZone", "—"), "status": i.get("DBInstanceStatus", "—")}
                for idx, i in enumerate(insts)
            ]
        writer_inst = instance_map.get(writer_id, {})
        attributes = {
            "id": cluster_id,
            "type": "cluster",
            "engine": c.get("Engine") or ref.get("Engine", "—"),
            "version": c.get("EngineVersion") or ref.get("EngineVersion", "—"),
            "status": c.get("Status") or ref.get("DBInstanceStatus", "—"),
            "instance_count": len(insts),
            "writer_id": writer_id,
            "writer_class": writer_inst.get("DBInstanceClass", "—"),
            "multi_az": c.get("MultiAZ", len(insts) > 1),
            "encrypted": c.get("StorageEncrypted", ref.get("StorageEncrypted", False)),
            "storage_type": c.get("StorageType") or ref.get("StorageType", "—"),
            "endpoint": c.get("Endpoint"),
            "port": c.get("Port") or (ref.get("Endpoint") or {}).get("Port"),
            "deletion_protection": c.get("DeletionProtection", False),
            "cpu_percent": None,
            "connections": None,
            "members": member_list,
        }
        stmt = pg_insert(CollectedResource).values(
            service_type="rds",
            region=region,
            resource_id=cluster_id,
            name=cluster_id,
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": cluster_id, "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1

    for i in db_instances:
        if i["DBInstanceIdentifier"] in cluster_member_ids:
            continue
        # Only store RDS; skip DocumentDB instances
        if "docdb" in (i.get("Engine") or "").lower():
            continue
        iid = i["DBInstanceIdentifier"]
        ep = i.get("Endpoint") or {}
        attributes = {
            "id": iid,
            "type": "instance",
            "engine": i.get("Engine", "—"),
            "version": i.get("EngineVersion", "—"),
            "class": i.get("DBInstanceClass", "—"),
            "status": i.get("DBInstanceStatus", "—"),
            "az": i.get("AvailabilityZone", "—"),
            "multi_az": i.get("MultiAZ", False),
            "storage_gb": i.get("AllocatedStorage", 0),
            "storage_type": i.get("StorageType", "—"),
            "encrypted": i.get("StorageEncrypted", False),
            "endpoint": ep.get("Address"),
            "port": ep.get("Port"),
            "deletion_protection": i.get("DeletionProtection", False),
            "cpu_percent": None,
            "connections": None,
        }
        stmt = pg_insert(CollectedResource).values(
            service_type="rds",
            region=region,
            resource_id=iid,
            name=iid,
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": iid, "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1
    db.commit()
    return count


def _collect_opensearch_region(session, region: str, db):
    """Fetch OpenSearch domains in one region; upsert into collected_resources."""
    os_client = session.client("opensearch", region_name=region)
    now = datetime.utcnow()
    count = 0
    try:
        names = [d["DomainName"] for d in os_client.list_domain_names().get("DomainNames", [])]
    except ClientError:
        return 0
    if not names:
        db.commit()
        return 0
    try:
        domains_detail = os_client.describe_domains(DomainNames=names).get("DomainStatusList", [])
    except ClientError:
        db.commit()
        return 0
    for d in domains_detail:
        domain_name = d["DomainName"]
        ec = d.get("ClusterConfig") or d.get("ElasticsearchClusterConfig") or {}
        ebs = d.get("EBSOptions", {})
        vpc = d.get("VPCOptions", {})
        enc = d.get("EncryptionAtRestOptions", {})
        n2n = d.get("NodeToNodeEncryptionOptions", {})
        dep = d.get("DomainEndpointOptions", {})
        status = "Deleting" if d.get("Deleted") else "Upgrading" if d.get("UpgradeProcessing") else "Processing" if d.get("Processing") else "Active"
        endpoint = d.get("Endpoint") or (list(d.get("Endpoints", {}).values())[0] if d.get("Endpoints") else None)
        attributes = {
            "name": domain_name,
            "arn": d.get("ARN"),
            "engine_version": d.get("EngineVersion") or d.get("ElasticsearchVersion", "—"),
            "status": status,
            "instance_type": ec.get("InstanceType", "—"),
            "instance_count": ec.get("InstanceCount", 1),
            "dedicated_master": ec.get("DedicatedMasterEnabled", False),
            "zone_awareness": ec.get("ZoneAwarenessEnabled", False),
            "ebs_volume_gb": ebs.get("VolumeSize"),
            "ebs_type": ebs.get("VolumeType", "—"),
            "encrypted": enc.get("Enabled", False),
            "node_to_node_enc": n2n.get("Enabled", False),
            "enforce_https": dep.get("EnforceHTTPS", False),
            "in_vpc": bool(vpc.get("VPCId")),
            "endpoint": endpoint,
            "cpu_percent": None,
            "jvm_memory_percent": None,
            "free_storage_mb": None,
        }
        stmt = pg_insert(CollectedResource).values(
            service_type="opensearch",
            region=region,
            resource_id=domain_name,
            name=domain_name,
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": domain_name, "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1
    db.commit()
    return count


def _collect_docdb_region(session, region: str, db):
    """Fetch DocumentDB clusters and instances in one region; upsert into collected_resources."""
    docdb = session.client("docdb", region_name=region)
    now = datetime.utcnow()
    count = 0

    cluster_list = []
    try:
        marker = None
        while True:
            kw = {} if marker is None else {"Marker": marker}
            resp = docdb.describe_db_clusters(**kw)
            cluster_list.extend(resp.get("DBClusters", []))
            marker = resp.get("Marker")
            if not marker:
                break
    except ClientError:
        pass

    db_instances = []
    try:
        marker = None
        while True:
            kw = {} if marker is None else {"Marker": marker}
            resp = docdb.describe_db_instances(**kw)
            db_instances.extend(resp.get("DBInstances", []))
            marker = resp.get("Marker")
            if not marker:
                break
    except ClientError:
        pass

    instance_map = {i["DBInstanceIdentifier"]: i for i in db_instances}
    cluster_groups = {}
    for i in db_instances:
        cid = i.get("DBClusterIdentifier")
        if cid:
            cluster_groups.setdefault(cid, []).append(i)
    cluster_member_ids = {i["DBInstanceIdentifier"] for insts in cluster_groups.values() for i in insts}

    for c in cluster_list:
        cid = c["DBClusterIdentifier"]
        insts = cluster_groups.get(cid, [])
        official_members = c.get("DBClusterMembers", [])
        if official_members:
            writer_id = next(
                (m["DBInstanceIdentifier"] for m in official_members if m.get("IsClusterWriter")),
                insts[0]["DBInstanceIdentifier"] if insts else None,
            )
            member_list = sorted([
                {
                    "id": m["DBInstanceIdentifier"],
                    "role": "Writer" if m.get("IsClusterWriter") else "Reader",
                    "class": instance_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceClass", "—"),
                    "az": instance_map.get(m["DBInstanceIdentifier"], {}).get("AvailabilityZone", "—"),
                    "status": instance_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceStatus", "—"),
                }
                for m in official_members
            ], key=lambda x: 0 if x["role"] == "Writer" else 1)
        else:
            writer_id = insts[0]["DBInstanceIdentifier"] if insts else None
            member_list = [
                {
                    "id": i["DBInstanceIdentifier"],
                    "role": "Writer" if idx == 0 else "Reader",
                    "class": i.get("DBInstanceClass", "—"),
                    "az": i.get("AvailabilityZone", "—"),
                    "status": i.get("DBInstanceStatus", "—"),
                }
                for idx, i in enumerate(insts)
            ]
        ref = insts[0] if insts else c
        writer_inst = instance_map.get(writer_id, {}) if writer_id else {}
        attributes = {
            "id": cid,
            "type": "cluster",
            "engine": c.get("Engine", "docdb"),
            "version": c.get("EngineVersion", "—"),
            "status": c.get("Status", "—"),
            "instance_count": len(insts),
            "writer_id": writer_id,
            "writer_class": writer_inst.get("DBInstanceClass", "—"),
            "multi_az": c.get("MultiAZ", len(insts) > 1),
            "encrypted": c.get("StorageEncrypted", ref.get("StorageEncrypted", False)),
            "storage_type": ref.get("StorageType", "—"),
            "endpoint": c.get("Endpoint"),
            "reader_endpoint": c.get("ReaderEndpoint"),
            "port": c.get("Port") or (ref.get("Endpoint") or {}).get("Port"),
            "deletion_protection": c.get("DeletionProtection", False),
            "cpu_percent": None,
            "connections": None,
            "members": member_list,
        }
        stmt = pg_insert(CollectedResource).values(
            service_type="docdb",
            region=region,
            resource_id=cid,
            name=cid,
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": cid, "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1

    for i in db_instances:
        if i["DBInstanceIdentifier"] in cluster_member_ids:
            continue
        iid = i["DBInstanceIdentifier"]
        ep = i.get("Endpoint") or {}
        attributes = {
            "id": iid,
            "type": "instance",
            "engine": i.get("Engine", "docdb"),
            "version": i.get("EngineVersion", "—"),
            "class": i.get("DBInstanceClass", "—"),
            "status": i.get("DBInstanceStatus", "—"),
            "az": i.get("AvailabilityZone", "—"),
            "multi_az": i.get("MultiAZ", False),
            "storage_gb": i.get("AllocatedStorage", 0),
            "storage_type": i.get("StorageType", "—"),
            "encrypted": i.get("StorageEncrypted", False),
            "endpoint": ep.get("Address"),
            "port": ep.get("Port"),
            "deletion_protection": i.get("DeletionProtection", False),
            "cpu_percent": None,
            "connections": None,
        }
        stmt = pg_insert(CollectedResource).values(
            service_type="docdb",
            region=region,
            resource_id=iid,
            name=iid,
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": iid, "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1
    db.commit()
    return count


def _collect_elasticache_region(session, region: str, db):
    """Fetch ElastiCache replication groups and standalone clusters; upsert into collected_resources."""
    ec_client = session.client("elasticache", region_name=region)
    now = datetime.utcnow()
    count = 0
    try:
        rgs = ec_client.describe_replication_groups().get("ReplicationGroups", [])
        clusters = ec_client.describe_cache_clusters(ShowCacheNodeInfo=True).get("CacheClusters", [])
    except ClientError:
        return 0
    cluster_meta = {c["CacheClusterId"]: (c.get("Engine"), c.get("EngineVersion")) for c in clusters}
    for rg in rgs:
        member_clusters = rg.get("MemberClusters", [])
        primary_id = member_clusters[0] if member_clusters else None
        engine, version = cluster_meta.get(primary_id, ("—", "—")) if primary_id else ("—", "—")
        ng0 = rg.get("NodeGroups", [{}])[0]
        primary_ep = ng0.get("PrimaryEndpoint", {}) or rg.get("ConfigurationEndpoint", {})
        attributes = {
            "id": rg["ReplicationGroupId"],
            "kind": "replication_group",
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
            "primary_endpoint": primary_ep.get("Address"),
            "port": primary_ep.get("Port"),
            "cpu_percent": None,
            "memory_percent": None,
            "connections": None,
        }
        stmt = pg_insert(CollectedResource).values(
            service_type="elasticache",
            region=region,
            resource_id=rg["ReplicationGroupId"],
            name=rg["ReplicationGroupId"],
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": rg["ReplicationGroupId"], "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1
    rg_members = {c for rg in rgs for c in rg.get("MemberClusters", [])}
    for c in clusters:
        cid = c["CacheClusterId"]
        if cid in rg_members:
            continue
        ep = c.get("ConfigurationEndpoint", {}) or (c.get("CacheNodes", [{}])[0].get("Endpoint", {}) if c.get("CacheNodes") else {})
        attributes = {
            "id": cid,
            "kind": "standalone",
            "engine": f"{c.get('Engine', '—')} {c.get('EngineVersion', '')}".strip(),
            "status": c.get("CacheClusterStatus", "—"),
            "node_type": c.get("CacheNodeType", "—"),
            "num_nodes": c.get("NumCacheNodes", 1),
            "az": c.get("PreferredAvailabilityZone", "—"),
            "endpoint": ep.get("Address"),
            "port": ep.get("Port"),
            "cpu_percent": None,
            "memory_percent": None,
            "connections": None,
        }
        stmt = pg_insert(CollectedResource).values(
            service_type="elasticache",
            region=region,
            resource_id=cid,
            name=cid,
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": cid, "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1
    db.commit()
    return count


def _collect_mq_region(session, region: str, db):
    """Fetch MQ brokers in one region; upsert into collected_resources."""
    mq = session.client("mq", region_name=region)
    now = datetime.utcnow()
    count = 0
    try:
        brokers_list = mq.list_brokers().get("BrokerSummaries", [])
    except ClientError:
        return 0
    for b in brokers_list:
        broker_id = b["BrokerId"]
        try:
            detail = mq.describe_broker(BrokerId=broker_id)
        except Exception:
            continue
        engine = detail.get("EngineType", "ActiveMQ")
        instances = detail.get("BrokerInstances", [])
        endpoints = []
        for inst in instances:
            endpoints.extend(inst.get("Endpoints", []))
        attributes = {
            "id": broker_id,
            "name": detail.get("BrokerName", "—"),
            "state": detail.get("BrokerState", "—"),
            "engine_type": engine,
            "engine_version": detail.get("EngineVersion", "—"),
            "instance_type": detail.get("HostInstanceType", "—"),
            "deployment_mode": detail.get("DeploymentMode", "—"),
            "publicly_accessible": detail.get("PubliclyAccessible", False),
            "auto_minor_upgrade": detail.get("AutoMinorVersionUpgrade", False),
            "endpoints": endpoints,
            "instances": instances,
            "cpu_percent": None,
            "heap_usage": None,
            "total_connections": None,
            "total_queues": None,
        }
        stmt = pg_insert(CollectedResource).values(
            service_type="mq",
            region=region,
            resource_id=broker_id,
            name=detail.get("BrokerName", "—"),
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": detail.get("BrokerName", "—"), "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1
    db.commit()
    return count


def _collect_secrets_region(session, region: str, db):
    """Fetch Secrets Manager list (metadata only, no values) and upsert into collected_resources."""
    sm = session.client("secretsmanager", region_name=region)
    paginator = sm.get_paginator("list_secrets")
    now = datetime.now(timezone.utc)
    count = 0
    for page in paginator.paginate():
        for s in page.get("SecretList", []):
            last_accessed = s.get("LastAccessedDate")
            last_changed = s.get("LastChangedDate")
            last_rotated = s.get("LastRotatedDate")
            rotation_enabled = s.get("RotationEnabled", False)
            rotation_days = None
            if rotation_enabled and s.get("RotationRules"):
                rotation_days = s["RotationRules"].get("AutomaticallyAfterDays")
            ref_date = last_rotated or last_changed or s.get("CreatedDate")
            age_days = None
            if ref_date:
                age_days = (now.replace(tzinfo=ref_date.tzinfo) - ref_date).days
            attributes = {
                "name": s["Name"],
                "arn": s["ARN"],
                "description": s.get("Description", ""),
                "rotation_enabled": rotation_enabled,
                "rotation_days": rotation_days,
                "age_days": age_days,
                "last_accessed": last_accessed.isoformat() if last_accessed else None,
                "last_changed": last_changed.isoformat() if last_changed else None,
                "last_rotated": last_rotated.isoformat() if last_rotated else None,
                "kms_key": s.get("KmsKeyId", "Default"),
                "tags": {t["Key"]: t["Value"] for t in s.get("Tags", [])},
                "stale": (not rotation_enabled and age_days is not None and age_days > 90),
            }
            stmt = pg_insert(CollectedResource).values(
                service_type="secrets",
                region=region,
                resource_id=s["ARN"],
                name=s["Name"],
                attributes=attributes,
                collected_at=now,
            ).on_conflict_do_update(
                index_elements=["service_type", "region", "resource_id"],
                set_={"name": s["Name"], "attributes": attributes, "collected_at": now},
            )
            db.execute(stmt)
            count += 1
    db.commit()
    return count


def _collect_iam_region(session, region: str, db):
    """Fetch IAM users (list + detail per user) and upsert into collected_resources. IAM is account-global; region is 'global'."""
    from app.routers.iam import _fetch_users, _fetch_user_detail
    now = datetime.now(timezone.utc)
    users_data = _fetch_users(session)
    count = 0
    for list_item in users_data.get("users", []):
        username = list_item.get("username")
        if not username:
            continue
        try:
            detail = _fetch_user_detail(session, username)
        except Exception as e:
            logger.warning("_collect_iam_region: skip user %s: %s", username, e)
            continue
        attributes = {"list": list_item, "detail": detail}
        stmt = pg_insert(CollectedResource).values(
            service_type="iam",
            region=region,
            resource_id=username,
            name=username,
            attributes=attributes,
            collected_at=now,
        ).on_conflict_do_update(
            index_elements=["service_type", "region", "resource_id"],
            set_={"name": username, "attributes": attributes, "collected_at": now},
        )
        db.execute(stmt)
        count += 1
    db.commit()
    return count


@app.task(name="app.tasks.collect_tasks.collect_resources")
def collect_resources(service_type: str, region: str):
    """Collect resources for one service type and region; upsert into collected_resources."""
    if not settings.power_aws_access_key_id or not settings.base_role_arn:
        logger.warning("Power AWS keys not configured; skipping collect_resources")
        return 0
    db = SessionLocal()
    try:
        session = _get_session()
        if service_type == "ec2":
            count = _collect_ec2_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "eks":
            count = _collect_eks_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "elb":
            count = _collect_elb_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "rds":
            count = _collect_rds_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "docdb":
            count = _collect_docdb_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "opensearch":
            count = _collect_opensearch_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "elasticache":
            count = _collect_elasticache_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "mq":
            count = _collect_mq_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "secrets":
            count = _collect_secrets_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        if service_type == "iam":
            count = _collect_iam_region(session, region, db)
            _publish_refresh_done(service_type, region)
            return count
        logger.info("collect_resources: %s not implemented yet", service_type)
        return 0
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        # Skip opt-in or disabled regions (AuthFailure / UnauthorizedOperation)
        if error_code in ("AuthFailure", "InvalidClientTokenId", "UnauthorizedOperation"):
            logger.warning(
                "collect_resources %s %s skipped (region not enabled or no access): %s",
                service_type, region, error_code,
            )
            db.rollback()
            _publish_refresh_done(service_type, region)
            return 0
        logger.exception("collect_resources %s %s AWS error: %s", service_type, region, e)
        db.rollback()
        raise
    except Exception as e:
        logger.exception("collect_resources %s %s failed: %s", service_type, region, e)
        db.rollback()
        raise
    finally:
        db.close()


@app.task(name="app.tasks.collect_tasks.collect_resources_all")
def collect_resources_all():
    """Collect resources for all configured service types and regions (Beat schedule)."""
    regions = settings.collector_regions_list
    if not regions:
        logger.warning("collect_resources_all: no regions configured (check COLLECTOR_REGIONS)")
        return 0
    total = 0
    for st in COLLECTOR_SERVICE_TYPES:
        if st == "iam":
            try:
                n = collect_resources("iam", "global")
                total += n
            except Exception as e:
                logger.exception("collect_resources_all iam: %s", e)
            continue
        for region in regions:
            try:
                n = collect_resources(st, region)
                total += n
            except Exception as e:
                logger.exception("collect_resources_all %s %s: %s", st, region, e)
    logger.info("collect_resources_all finished: %d resources", total)
    return total


def _collect_ec2_metrics_region(session, region: str, db):
    """Fetch CloudWatch metrics for EC2 instances in region; insert into collected_metrics (incremental)."""
    from app.db.models import CollectedResource
    ec2_resources = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "ec2",
            CollectedResource.region == region,
        )
        .all()
    )
    if not ec2_resources:
        return 0
    cw = session.client("cloudwatch", region_name=region)
    period = 3600  # 1 hour points
    dim = [{"Name": "InstanceId", "Value": None}]
    # CPU (Average); Network/Disk (Sum). Memory (CWAgent) skipped — requires agent on instance.
    ec2_metric_configs = [
        ("CPUUtilization", "Average"),
        ("NetworkIn", "Sum"),
        ("NetworkOut", "Sum"),
        ("DiskReadBytes", "Sum"),
        ("DiskWriteBytes", "Sum"),
        ("EBSReadBytes", "Sum"),
        ("EBSWriteBytes", "Sum"),
    ]
    count = 0
    for r in ec2_resources:
        start, end = _metrics_start_end(db, "ec2", r.resource_id, region)
        if start >= end:
            continue
        dim[0]["Value"] = r.resource_id
        for metric_name, stat in ec2_metric_configs:
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/EC2",
                    MetricName=metric_name,
                    Dimensions=dim,
                    StartTime=start,
                    EndTime=end,
                    Period=period,
                    Statistics=[stat],
                )
                for pt in resp.get("Datapoints", []):
                    ts = pt["Timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    val = float(pt.get(stat, 0))
                    stmt = pg_insert(CollectedMetric).values(
                        service_type="ec2",
                        resource_id=r.resource_id,
                        region=region,
                        metric_name=metric_name,
                        timestamp=ts,
                        value=round(val, 4),
                        unit=pt.get("Unit"),
                    ).on_conflict_do_nothing(index_elements=METRICS_UQ_COLS)
                    db.execute(stmt)
                    count += 1
            except Exception as e:
                logger.warning("metrics for %s %s: %s", r.resource_id, metric_name, e)
    db.commit()
    return count


def _collect_rds_metrics_region(session, region: str, db):
    """Fetch RDS CloudWatch metrics for instances and clusters; insert into collected_metrics (incremental)."""
    resources = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "rds", CollectedResource.region == region)
        .all()
    )
    if not resources:
        return 0
    cw = session.client("cloudwatch", region_name=region)
    period = 300
    GB = 1 / (1024 ** 3)
    count = 0
    for r in resources:
        start, end = _metrics_start_end(db, "rds", r.resource_id, region)
        if start >= end:
            continue
        att = r.attributes or {}
        dim_value = r.resource_id
        if att.get("type") == "cluster":
            dim_value = att.get("writer_id") or r.resource_id
        if not dim_value:
            continue
        for metric_name, stat, scale in [
            ("CPUUtilization", "Average", 1.0),
            ("DatabaseConnections", "Average", 1.0),
            ("FreeStorageSpace", "Average", GB),
            ("FreeableMemory", "Average", GB),
            ("ReadIOPS", "Average", 1.0),
            ("WriteIOPS", "Average", 1.0),
            ("ReadLatency", "Average", 1000.0),
            ("WriteLatency", "Average", 1000.0),
        ]:
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/RDS",
                    MetricName=metric_name,
                    Dimensions=[{"Name": "DBInstanceIdentifier", "Value": dim_value}],
                    StartTime=start,
                    EndTime=end,
                    Period=period,
                    Statistics=[stat],
                )
                for pt in resp.get("Datapoints", []):
                    ts = pt["Timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    val = float(pt.get(stat, 0)) * scale
                    stmt = pg_insert(CollectedMetric).values(
                        service_type="rds",
                        resource_id=r.resource_id,
                        region=region,
                        metric_name=metric_name,
                        timestamp=ts,
                        value=round(val, 4),
                        unit=pt.get("Unit"),
                    ).on_conflict_do_nothing(index_elements=METRICS_UQ_COLS)
                    db.execute(stmt)
                    count += 1
            except Exception as e:
                logger.warning("rds metrics %s %s: %s", r.resource_id, metric_name, e)
    db.commit()
    return count


def _collect_docdb_metrics_region(session, region: str, db):
    """Fetch DocumentDB CloudWatch metrics; insert into collected_metrics (incremental)."""
    resources = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "docdb", CollectedResource.region == region)
        .all()
    )
    if not resources:
        return 0
    cw = session.client("cloudwatch", region_name=region)
    period = 300
    GB = 1 / (1024 ** 3)
    count = 0
    for r in resources:
        start, end = _metrics_start_end(db, "docdb", r.resource_id, region)
        if start >= end:
            continue
        att = r.attributes or {}
        dim_value = r.resource_id if att.get("type") != "cluster" else (att.get("writer_id") or r.resource_id)
        if not dim_value:
            continue
        for metric_name in ["CPUUtilization", "DatabaseConnections", "FreeStorageSpace", "ReadIOPS", "WriteIOPS"]:
            try:
                scale = GB if metric_name == "FreeStorageSpace" else 1.0
                resp = cw.get_metric_statistics(
                    Namespace="AWS/DocDB",
                    MetricName=metric_name,
                    Dimensions=[{"Name": "DBInstanceIdentifier", "Value": dim_value}],
                    StartTime=start, EndTime=end, Period=period, Statistics=["Average"],
                )
                for pt in resp.get("Datapoints", []):
                    ts = pt["Timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    val = float(pt.get("Average", 0)) * scale
                    stmt = pg_insert(CollectedMetric).values(
                        service_type="docdb", resource_id=r.resource_id, region=region,
                        metric_name=metric_name, timestamp=ts, value=round(val, 4), unit=pt.get("Unit"),
                    ).on_conflict_do_nothing(index_elements=METRICS_UQ_COLS)
                    db.execute(stmt)
                    count += 1
            except Exception as e:
                logger.warning("docdb metrics %s %s: %s", r.resource_id, metric_name, e)
    db.commit()
    return count


def _collect_opensearch_metrics_region(session, region: str, db):
    """Fetch OpenSearch CloudWatch metrics; insert into collected_metrics (incremental)."""
    resources = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "opensearch", CollectedResource.region == region)
        .all()
    )
    if not resources:
        return 0
    os_client = session.client("opensearch", region_name=region)
    cw = session.client("cloudwatch", region_name=region)
    period = 300
    count = 0
    for r in resources:
        start, end = _metrics_start_end(db, "opensearch", r.resource_id, region)
        if start >= end:
            continue
        try:
            d = os_client.describe_domain(DomainName=r.resource_id)["DomainStatus"]
            client_id = d["ARN"].split(":")[4]
        except Exception as e:
            logger.warning("opensearch describe_domain %s: %s", r.resource_id, e)
            continue
        for metric_name, stat, scale in [
            ("CPUUtilization", "Average", 1.0),
            ("JVMMemoryPressure", "Average", 1.0),
            ("FreeStorageSpace", "Minimum", 1.0 / 1024),
            ("SearchRate", "Average", 1.0),
            ("IndexingRate", "Average", 1.0),
            ("SysMemoryUtilization", "Average", 1.0),
        ]:
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/ES",
                    MetricName=metric_name,
                    Dimensions=[
                        {"Name": "DomainName", "Value": r.resource_id},
                        {"Name": "ClientId", "Value": client_id},
                    ],
                    StartTime=start, EndTime=end, Period=period, Statistics=[stat],
                )
                for pt in resp.get("Datapoints", []):
                    ts = pt["Timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    val = float(pt.get(stat, 0)) * scale
                    stmt = pg_insert(CollectedMetric).values(
                        service_type="opensearch", resource_id=r.resource_id, region=region,
                        metric_name=metric_name, timestamp=ts, value=round(val, 4), unit=pt.get("Unit"),
                    ).on_conflict_do_nothing(index_elements=METRICS_UQ_COLS)
                    db.execute(stmt)
                    count += 1
            except Exception as e:
                logger.warning("opensearch metrics %s %s: %s", r.resource_id, metric_name, e)
    db.commit()
    return count


def _collect_elasticache_metrics_region(session, region: str, db):
    """Fetch ElastiCache CloudWatch metrics; insert into collected_metrics (incremental)."""
    resources = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "elasticache", CollectedResource.region == region)
        .all()
    )
    if not resources:
        return 0
    ec = session.client("elasticache", region_name=region)
    cw = session.client("cloudwatch", region_name=region)
    period = 300
    count = 0
    for r in resources:
        start, end = _metrics_start_end(db, "elasticache", r.resource_id, region)
        if start >= end:
            continue
        att = r.attributes or {}
        dim_name = "CacheClusterId"
        dim_value = r.resource_id
        if att.get("kind") == "replication_group":
            try:
                rg_resp = ec.describe_replication_groups(ReplicationGroupId=r.resource_id)
                rg = rg_resp.get("ReplicationGroups", [{}])[0]
                if rg.get("MemberClusters"):
                    dim_value = rg["MemberClusters"][0]
                elif rg.get("NodeGroups"):
                    dim_value = rg["NodeGroups"][0].get("NodeGroupMembers", [{}])[0].get("CacheClusterId", r.resource_id)
            except Exception:
                pass
        for cw_name, label in [
            ("CPUUtilization", "CPUUtilization"),
            ("CurrConnections", "CurrConnections"),
            ("DatabaseMemoryUsagePercentage", "DatabaseMemoryUsagePercentage"),
            ("CacheHits", "CacheHits"),
            ("CacheMisses", "CacheMisses"),
        ]:
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/ElastiCache",
                    MetricName=cw_name,
                    Dimensions=[{"Name": dim_name, "Value": dim_value}],
                    StartTime=start, EndTime=end, Period=period, Statistics=["Average"],
                )
                for pt in resp.get("Datapoints", []):
                    ts = pt["Timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    stmt = pg_insert(CollectedMetric).values(
                        service_type="elasticache", resource_id=r.resource_id, region=region,
                        metric_name=label, timestamp=ts, value=round(float(pt.get("Average", 0)), 2), unit=pt.get("Unit"),
                    ).on_conflict_do_nothing(index_elements=METRICS_UQ_COLS)
                    db.execute(stmt)
                    count += 1
            except Exception as e:
                logger.warning("elasticache metrics %s %s: %s", r.resource_id, label, e)
    db.commit()
    return count


def _mq_metric_configs(engine_type):
    if engine_type == "RabbitMQ":
        return {"cpu": "SystemCpuUtilization", "memory": "MemoryUsed", "connections": "ConnectionCount",
                "queues": "Queues", "messages": "MessageCount", "storage_free": "RabbitMQDiskFree"}
    # ActiveMQ: MemoryUsage may be unavailable; also collect HeapUsage as fallback
    return {"cpu": "CpuUtilization", "memory": "MemoryUsage", "memory_heap": "HeapUsage",
            "connections": "CurrentConnectionsCount", "queues": "TotalQueueCount",
            "messages": "TotalMessageCount", "storage_usage": "StorePercentUsage"}


def _collect_mq_metrics_region(session, region: str, db):
    """Fetch MQ CloudWatch metrics; insert into collected_metrics (incremental). Broker dimension uses BrokerName (r.name)."""
    resources = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "mq", CollectedResource.region == region)
        .all()
    )
    if not resources:
        return 0
    cw = session.client("cloudwatch", region_name=region)
    period = 300
    count = 0
    for r in resources:
        start, end = _metrics_start_end(db, "mq", r.resource_id, region)
        if start >= end:
            continue
        broker_name = r.name or r.resource_id
        engine = (r.attributes or {}).get("engine_type", "ActiveMQ")
        configs = _mq_metric_configs(engine)
        for key, cw_name in configs.items():
            try:
                stat = "Sum" if cw_name in ("TotalQueueCount", "TotalMessageCount", "Queues", "MessageCount") else "Average"
                resp = cw.get_metric_statistics(
                    Namespace="AWS/AmazonMQ",
                    MetricName=cw_name,
                    Dimensions=[{"Name": "Broker", "Value": broker_name}],
                    StartTime=start, EndTime=end, Period=period, Statistics=[stat],
                )
                for pt in resp.get("Datapoints", []):
                    ts = pt["Timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    stmt = pg_insert(CollectedMetric).values(
                        service_type="mq", resource_id=r.resource_id, region=region,
                        metric_name=key, timestamp=ts, value=round(float(pt.get(stat, pt.get("Average", 0))), 4), unit=pt.get("Unit"),
                    ).on_conflict_do_nothing(index_elements=METRICS_UQ_COLS)
                    db.execute(stmt)
                    count += 1
            except Exception as e:
                logger.warning("mq metrics %s %s: %s", r.resource_id, key, e)
    db.commit()
    return count


def _collect_elb_metrics_region(session, region: str, db):
    """Fetch LB CloudWatch metrics; insert into collected_metrics (incremental)."""
    resources = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "elb", CollectedResource.region == region)
        .all()
    )
    if not resources:
        return 0
    cw = session.client("cloudwatch", region_name=region)
    period = 300
    count = 0
    for r in resources:
        start, end = _metrics_start_end(db, "elb", r.resource_id, region)
        if start >= end:
            continue
        att = r.attributes or {}
        arn = att.get("arn") or ""
        if arn.startswith("arn:") and "/app/" in arn:
            namespace = "AWS/ApplicationELB"
            dim_val = "/".join(arn.split("/")[-3:])
        elif arn.startswith("arn:") and "/net/" in arn:
            namespace = "AWS/NetworkELB"
            dim_val = "/".join(arn.split("/")[-3:])
        else:
            namespace = "AWS/ELB"
            dim_val = r.resource_id
        dim_name = "LoadBalancer" if "ELB" in namespace else "LoadBalancerName"
        metrics = []
        if namespace == "AWS/ApplicationELB":
            metrics = [("ProcessedBytes", "Sum"), ("RequestCount", "Sum")]
        elif namespace == "AWS/NetworkELB":
            metrics = [("ProcessedBytes", "Sum"), ("RequestCount", "Sum"), ("ActiveFlowCount", "Average")]
        else:
            # Classic: ProcessedBytes often unavailable; use EstimatedProcessedBytes
            metrics = [("EstimatedProcessedBytes", "Sum"), ("RequestCount", "Sum")]
        for cw_name, stat in metrics:
            try:
                resp = cw.get_metric_statistics(
                    Namespace=namespace,
                    MetricName=cw_name,
                    Dimensions=[{"Name": dim_name, "Value": dim_val}],
                    StartTime=start, EndTime=end, Period=period, Statistics=[stat],
                )
                for pt in resp.get("Datapoints", []):
                    ts = pt["Timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    stmt = pg_insert(CollectedMetric).values(
                        service_type="elb", resource_id=r.resource_id, region=region,
                        metric_name=cw_name, timestamp=ts, value=round(float(pt.get(stat, pt.get("Average", 0))), 4), unit=pt.get("Unit"),
                    ).on_conflict_do_nothing(index_elements=METRICS_UQ_COLS)
                    db.execute(stmt)
                    count += 1
            except Exception as e:
                logger.warning("elb metrics %s %s: %s", r.resource_id, cw_name, e)
    db.commit()
    return count


# AWS/EKS namespace (Kubernetes 1.28+): dimension ClusterName, 1-min frequency.
_EKS_METRIC_CONFIGS = [
    ("apiserver_flowcontrol_current_executing_seats", "Sum"),
    ("scheduler_schedule_attempts_total", "Sum"),
    ("scheduler_schedule_attempts_SCHEDULED", "Sum"),
    ("scheduler_schedule_attempts_UNSCHEDULABLE", "Sum"),
    ("scheduler_pending_pods", "Sum"),
    ("apiserver_request_total", "Sum"),
    ("apiserver_request_total_4XX", "Sum"),
    ("apiserver_request_total_429", "Sum"),
    ("apiserver_request_total_5XX", "Sum"),
    ("apiserver_storage_size_bytes", "Maximum"),
]


def _collect_eks_metrics_region(session, region: str, db):
    """Fetch EKS cluster control-plane metrics (AWS/EKS namespace); insert into collected_metrics (incremental)."""
    from app.db.models import CollectedResource
    resources = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "eks", CollectedResource.region == region)
        .all()
    )
    if not resources:
        logger.info("eks metrics: no clusters in region %s", region)
        return 0
    cw = session.client("cloudwatch", region_name=region)
    period = 300
    count = 0
    logger.info("eks metrics: region=%s clusters=%d", region, len(resources))
    for r in resources:
        start, end = _metrics_start_end(db, "eks", r.resource_id, region)
        if start >= end:
            logger.debug("eks metrics: skip %s (start >= end)", r.resource_id)
            continue
        dims = [{"Name": "ClusterName", "Value": r.resource_id}]
        cluster_points = 0
        for metric_name, stat in _EKS_METRIC_CONFIGS:
            try:
                resp = cw.get_metric_statistics(
                    Namespace="AWS/EKS",
                    MetricName=metric_name,
                    Dimensions=dims,
                    StartTime=start,
                    EndTime=end,
                    Period=period,
                    Statistics=[stat],
                )
                datapoints = resp.get("Datapoints", [])
                for pt in datapoints:
                    ts = pt["Timestamp"]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    val = float(pt.get(stat, pt.get("Average", 0)))
                    stmt = pg_insert(CollectedMetric).values(
                        service_type="eks",
                        resource_id=r.resource_id,
                        region=region,
                        metric_name=metric_name,
                        timestamp=ts,
                        value=round(val, 4),
                        unit=pt.get("Unit"),
                    ).on_conflict_do_nothing(index_elements=METRICS_UQ_COLS)
                    db.execute(stmt)
                    count += 1
                    cluster_points += 1
            except Exception as e:
                logger.warning("eks metrics %s %s: %s", r.resource_id, metric_name, e)
        if cluster_points > 0:
            logger.info("eks metrics: %s inserted %d points", r.resource_id, cluster_points)
    db.commit()
    return count


@app.task(name="app.tasks.collect_tasks.collect_metrics")
def collect_metrics(service_type: str = "ec2", region: str = None):
    """Collect CloudWatch metrics for resources in DB (optional region filter)."""
    if not settings.power_aws_access_key_id or not settings.base_role_arn:
        logger.warning("Power AWS keys not configured; skipping collect_metrics")
        return 0
    db = SessionLocal()
    try:
        session = _get_session()
        regions = settings.collector_regions_list if region is None else [region]
        if not regions:
            return 0
        total = 0
        def _skip_region(exc, st, reg):
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("AuthFailure", "InvalidClientTokenId", "UnauthorizedOperation"):
                logger.warning("collect_metrics %s %s skipped: %s", st, reg, code)
                return True
            return False
        if service_type == "ec2":
            for r in regions:
                try:
                    total += _collect_ec2_metrics_region(session, r, db)
                except ClientError as e:
                    if not _skip_region(e, service_type, r):
                        raise
        elif service_type == "eks":
            for r in regions:
                try:
                    total += _collect_eks_metrics_region(session, r, db)
                except ClientError as e:
                    if not _skip_region(e, service_type, r):
                        raise
        elif service_type == "rds":
            for r in regions:
                try:
                    total += _collect_rds_metrics_region(session, r, db)
                except ClientError as e:
                    if not _skip_region(e, service_type, r):
                        raise
        elif service_type == "docdb":
            for r in regions:
                try:
                    total += _collect_docdb_metrics_region(session, r, db)
                except ClientError as e:
                    if not _skip_region(e, service_type, r):
                        raise
        elif service_type == "opensearch":
            for r in regions:
                try:
                    total += _collect_opensearch_metrics_region(session, r, db)
                except ClientError as e:
                    if not _skip_region(e, service_type, r):
                        raise
        elif service_type == "elasticache":
            for r in regions:
                try:
                    total += _collect_elasticache_metrics_region(session, r, db)
                except ClientError as e:
                    if not _skip_region(e, service_type, r):
                        raise
        elif service_type == "mq":
            for r in regions:
                try:
                    total += _collect_mq_metrics_region(session, r, db)
                except ClientError as e:
                    if not _skip_region(e, service_type, r):
                        raise
        elif service_type == "elb":
            for r in regions:
                try:
                    total += _collect_elb_metrics_region(session, r, db)
                except ClientError as e:
                    if not _skip_region(e, service_type, r):
                        raise
        return total
    except Exception as e:
        logger.exception("collect_metrics failed: %s", e)
        db.rollback()
        raise
    finally:
        db.close()


@app.task(name="app.tasks.collect_tasks.collect_metrics_all_finish")
def collect_metrics_all_finish(points_per_service):
    """Chord callback: sum group results and run retention. Called after all collect_metrics tasks finish."""
    if not isinstance(points_per_service, (list, tuple)):
        points_per_service = []
    total = sum(n for n in points_per_service if isinstance(n, (int, float)))
    for i, n in enumerate(points_per_service):
        if i < len(COLLECTOR_SERVICE_TYPES) and not isinstance(n, (int, float)):
            logger.warning("collect_metrics_all %s failed: %s", COLLECTOR_SERVICE_TYPES[i], n)
    logger.info("collect_metrics_all: parallel done (%s points total)", total)
    _flush_logs()
    db = SessionLocal()
    try:
        deleted = _apply_metrics_retention(db)
        if deleted:
            logger.info("collect_metrics_all: retention cleanup deleted %s rows", deleted)
    except Exception as e:
        logger.exception("collect_metrics_all retention cleanup failed: %s", e)
        db.rollback()
    finally:
        db.close()
    logger.info("collect_metrics_all finished: %d points", total)
    return total


@app.task(name="app.tasks.collect_tasks.collect_metrics_all", bind=True)
def collect_metrics_all(self):
    """Dispatch metrics collection for all service types in parallel (Beat schedule). Uses chord so we don't block on result.get()."""
    job = group(collect_metrics.s(st, None) for st in COLLECTOR_SERVICE_TYPES)
    chord(job, collect_metrics_all_finish.s()).apply_async()
    logger.info("collect_metrics_all: chord dispatched (%d service types)", len(COLLECTOR_SERVICE_TYPES))
    return "chord dispatched"


def _apply_metrics_retention(db) -> int:
    """Delete collected_metrics older than METRICS_RETENTION_HOURS. Returns deleted row count."""
    cutoff = datetime.utcnow() - timedelta(hours=METRICS_RETENTION_HOURS)
    result = db.execute(text(
        "DELETE FROM collected_metrics WHERE timestamp < :cutoff"
    ), {"cutoff": cutoff})
    db.commit()
    return result.rowcount if result.rowcount is not None else 0


@app.task(name="app.tasks.collect_tasks.apply_metrics_retention")
def apply_metrics_retention():
    """Delete collected_metrics older than METRICS_RETENTION_HOURS (also run after collect_metrics_all)."""
    db = SessionLocal()
    try:
        deleted = _apply_metrics_retention(db)
        logger.info("apply_metrics_retention: deleted %s rows", deleted)
        return deleted
    except Exception as e:
        logger.exception("apply_metrics_retention failed: %s", e)
        db.rollback()
        raise
    finally:
        db.close()


@app.task(name="app.tasks.collect_tasks.collect_alarms")
def collect_alarms(region: str = None):
    """Collect CloudWatch alarms for one or all regions."""
    if not settings.power_aws_access_key_id or not settings.base_role_arn:
        logger.warning("Power AWS keys not configured; skipping collect_alarms")
        return 0
    db = SessionLocal()
    try:
        session = _get_session()
        regions = settings.collector_regions_list if region is None else [region]
        if not regions:
            return 0
        total = 0
        for r in regions:
            try:
                total += _collect_alarms_region(session, r, db)
            except ClientError as e:
                logger.warning("collect_alarms %s: %s", r, e)
        return total
    except Exception as e:
        logger.exception("collect_alarms failed: %s", e)
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# AWS Health events (global endpoint, requires Business/Enterprise support)
# ---------------------------------------------------------------------------

HEALTH_SERVICE_TO_TYPE = {
    "EC2": "ec2",
    "EKS": "eks",
    "RDS": "databases",
    "DOCUMENTDB": "databases",
    "ELASTICACHE": "elasticache",
    "ELASTICSEARCH": "opensearch",
    "OPENSEARCH": "opensearch",
    "MQ": "mq",
    "SES": "ses",
    "ELASTICLOADBALANCING": "elb",
    "SECRETSMANAGER": "secrets",
    "IAM": "iam",
    "BILLING": "cost",
}


def _collect_health_events(session, db):
    """Fetch open/upcoming AWS Health events (global endpoint) and upsert into collected_health_events."""
    health = session.client("health", region_name="us-east-1")
    now = datetime.utcnow()
    count = 0
    seen_arns = set()

    try:
        next_token = None
        while True:
            kwargs = {
                "filter": {"eventStatusCodes": ["open", "upcoming"]},
                "maxResults": 100,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            resp = health.describe_events(**kwargs)

            for ev in resp.get("events", []):
                arn = ev.get("arn")
                if not arn:
                    continue
                seen_arns.add(arn)

                service_name = ev.get("service", "")
                service_type = HEALTH_SERVICE_TO_TYPE.get(service_name.upper())
                ev_region = ev.get("region")
                start_time = ev.get("startTime")
                end_time = ev.get("endTime")
                last_updated = ev.get("lastUpdatedTime")

                stmt = pg_insert(CollectedHealthEvent).values(
                    event_arn=arn,
                    service=service_name or None,
                    service_type=service_type,
                    region=ev_region,
                    event_type=ev.get("eventTypeCode"),
                    category=ev.get("eventTypeCategory"),
                    status=ev.get("statusCode", "open"),
                    title=ev.get("eventTypeCode"),
                    description=None,  # detail requires describe_event_details (expensive)
                    start_time=start_time,
                    end_time=end_time,
                    last_updated=last_updated,
                    collected_at=now,
                ).on_conflict_do_update(
                    index_elements=["event_arn"],
                    set_={
                        "service": service_name or None,
                        "service_type": service_type,
                        "region": ev_region,
                        "event_type": ev.get("eventTypeCode"),
                        "category": ev.get("eventTypeCategory"),
                        "status": ev.get("statusCode", "open"),
                        "title": ev.get("eventTypeCode"),
                        "start_time": start_time,
                        "end_time": end_time,
                        "last_updated": last_updated,
                        "collected_at": now,
                    },
                )
                db.execute(stmt)
                count += 1

            next_token = resp.get("nextToken")
            if not next_token:
                break

    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "SubscriptionRequiredException":
            logger.info(
                "collect_health_events: AWS Health API requires Business/Enterprise support plan — skipping"
            )
            _flush_logs()
            return 0
        raise

    # Mark events NOT returned (currently open/upcoming in DB) as closed
    if seen_arns:
        db.query(CollectedHealthEvent).filter(
            CollectedHealthEvent.status.in_(["open", "upcoming"]),
            ~CollectedHealthEvent.event_arn.in_(seen_arns),
        ).update({"status": "closed", "collected_at": now}, synchronize_session=False)
    else:
        # No events returned — close all open/upcoming
        db.query(CollectedHealthEvent).filter(
            CollectedHealthEvent.status.in_(["open", "upcoming"]),
        ).update({"status": "closed", "collected_at": now}, synchronize_session=False)

    db.commit()
    logger.info("collect_health_events: %d events", count)
    _flush_logs()
    return count


@app.task(name="app.tasks.collect_tasks.collect_health_events")
def collect_health_events():
    """Collect AWS Health events (global endpoint)."""
    if not settings.power_aws_access_key_id or not settings.base_role_arn:
        logger.warning("Power AWS keys not configured; skipping collect_health_events")
        return 0
    db = SessionLocal()
    try:
        session = _get_session()
        return _collect_health_events(session, db)
    finally:
        db.close()
