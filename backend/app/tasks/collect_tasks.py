"""
Celery tasks: collect AWS resources and CloudWatch metrics into DB.
Uses power-account credentials (AssumeRole) to run describe_* and get_metric_*.
"""
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.dialects.postgresql import insert as pg_insert
from botocore.exceptions import ClientError

from app.celery_app import app
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.sts_service import assume_role_for_services, ALL_SERVICES
from app.core.valkey_client import get_client
from app.db.models import CollectedResource, CollectedMetric

logger = logging.getLogger(__name__)


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
    """Fetch CloudWatch metrics for EC2 instances in region; insert into collected_metrics."""
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
    end = datetime.utcnow()
    start = end - timedelta(hours=METRICS_RETENTION_HOURS)
    period = 3600  # 1 hour points
    count = 0
    for r in ec2_resources:
        try:
            resp = cw.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": r.resource_id}],
                StartTime=start,
                EndTime=end,
                Period=period,
                Statistics=["Average"],
            )
            for pt in resp.get("Datapoints", []):
                ts = pt["Timestamp"]
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                db.add(CollectedMetric(
                    service_type="ec2",
                    resource_id=r.resource_id,
                    region=region,
                    metric_name="CPUUtilization",
                    timestamp=ts,
                    value=float(pt["Average"]),
                    unit=pt.get("Unit", "Percent"),
                ))
                count += 1
        except Exception as e:
            logger.warning("metrics for %s: %s", r.resource_id, e)
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
        if service_type == "ec2":
            for r in regions:
                try:
                    total += _collect_ec2_metrics_region(session, r, db)
                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    if error_code in ("AuthFailure", "InvalidClientTokenId", "UnauthorizedOperation"):
                        logger.warning("collect_metrics %s %s skipped (region not enabled): %s", service_type, r, error_code)
                        continue
                    raise
        return total
    except Exception as e:
        logger.exception("collect_metrics failed: %s", e)
        db.rollback()
        raise
    finally:
        db.close()


@app.task(name="app.tasks.collect_tasks.collect_metrics_all")
def collect_metrics_all():
    """Collect metrics for all service types (Beat schedule)."""
    total = 0
    for st in COLLECTOR_SERVICE_TYPES:
        try:
            total += collect_metrics(st, None)
        except Exception as e:
            logger.exception("collect_metrics_all %s: %s", st, e)
    logger.info("collect_metrics_all finished: %d points", total)
    return total


@app.task(name="app.tasks.collect_tasks.apply_metrics_retention")
def apply_metrics_retention():
    """Delete collected_metrics older than METRICS_RETENTION_HOURS."""
    from sqlalchemy import text
    db = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(hours=METRICS_RETENTION_HOURS)
        result = db.execute(text(
            "DELETE FROM collected_metrics WHERE timestamp < :cutoff"
        ), {"cutoff": cutoff})
        db.commit()
        deleted = result.rowcount if result.rowcount is not None else 0
        logger.info("apply_metrics_retention: deleted %s rows", deleted)
        return deleted
    except Exception as e:
        logger.exception("apply_metrics_retention failed: %s", e)
        db.rollback()
        raise
    finally:
        db.close()
