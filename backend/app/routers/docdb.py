"""
DocumentDB (Amazon DocDB) API: list clusters and instances, detail, CloudWatch metrics.
When USE_COLLECTOR_DB is True, list and detail read from collected_resources (Celery populates).
"""
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

router = APIRouter(prefix="/api/docdb", tags=["DocumentDB"])
USE_COLLECTOR_DB = True


def _is_docdb_engine(engine):
    """Only DocumentDB (docdb 5.0, docdb 8.0, etc.)."""
    if not engine:
        return False
    return "docdb" in (engine or "").lower()


def _list_docdb_from_db(region: str, db: Session):
    """Return { clusters, instances, total } from collected_resources (DocumentDB only)."""
    rows = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "docdb",
            CollectedResource.region == region,
        )
        .all()
    )
    clusters = []
    instances = []
    for r in rows:
        att = r.attributes or {}
        if not _is_docdb_engine(att.get("engine")):
            continue
        if att.get("type") == "cluster":
            clusters.append(att)
        else:
            instances.append(att)
    return {"clusters": clusters, "instances": instances, "total": len(clusters) + len(instances)}


def _detail_docdb_from_db(resource_id: str, is_cluster: bool, region: str, db: Session):
    """Return detail dict from collected_resources or None."""
    r = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "docdb",
            CollectedResource.region == region,
            CollectedResource.resource_id == resource_id,
        )
        .first()
    )
    if not r:
        return None
    att = r.attributes or {}
    if att.get("type") != ("cluster" if is_cluster else "instance"):
        return None
    if not _is_docdb_engine(att.get("engine")):
        return None
    if is_cluster:
        return {
            "type": "cluster",
            "id": att.get("id"),
            "engine": att.get("engine", "docdb"),
            "version": att.get("version", "—"),
            "status": att.get("status", "—"),
            "multi_az": att.get("multi_az"),
            "endpoint": att.get("endpoint"),
            "reader_endpoint": att.get("reader_endpoint"),
            "port": att.get("port"),
            "storage_gb": 0,
            "storage_type": att.get("storage_type", "—"),
            "encrypted": att.get("encrypted", False),
            "deletion_protection": att.get("deletion_protection", False),
            "members": att.get("members", []),
            "security_groups": [],
            "tags": [],
        }
    return {
        "type": "instance",
        "id": att.get("id"),
        "engine": att.get("engine", "docdb"),
        "version": att.get("version", "—"),
        "class": att.get("class", "—"),
        "status": att.get("status", "—"),
        "az": att.get("az", "—"),
        "multi_az": att.get("multi_az", False),
        "endpoint": att.get("endpoint"),
        "port": att.get("port"),
        "storage_gb": att.get("storage_gb", 0),
        "storage_type": att.get("storage_type", "—"),
        "encrypted": att.get("encrypted", False),
        "deletion_protection": att.get("deletion_protection", False),
        "security_groups": [],
        "tags": [],
    }


def _snapshot(cw, db_id, metric, stat="Average", namespace="AWS/DocDB"):
    try:
        pts = cw.get_metric_statistics(
            Namespace=namespace,
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


def _fetch_docdb(session):
    docdb = session.client("docdb")
    cw = session.client("cloudwatch")

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

    cluster_member_ids = {
        i["DBInstanceIdentifier"]
        for insts in cluster_groups.values()
        for i in insts
    }

    clusters = []
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
        cpu = _snapshot(cw, writer_id, "CPUUtilization") if writer_id else None
        conn_raw = _snapshot(cw, writer_id, "DatabaseConnections") if writer_id else None
        clusters.append({
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
            "port": c.get("Port") or (ref.get("Endpoint") or {}).get("Port"),
            "deletion_protection": c.get("DeletionProtection", False),
            "cpu_percent": cpu,
            "connections": int(conn_raw) if conn_raw is not None else None,
            "members": member_list,
        })

    instances = []
    for i in db_instances:
        if i["DBInstanceIdentifier"] in cluster_member_ids:
            continue
        iid = i["DBInstanceIdentifier"]
        ep = i.get("Endpoint") or {}
        cpu = _snapshot(cw, iid, "CPUUtilization")
        conn_raw = _snapshot(cw, iid, "DatabaseConnections")
        instances.append({
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
            "cpu_percent": cpu,
            "connections": int(conn_raw) if conn_raw is not None else None,
        })

    return {
        "clusters": clusters,
        "instances": instances,
        "total": len(clusters) + len(instances),
    }


@router.get("/instances")
def get_docdb_instances(request: Request, force: bool = False, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        return _list_docdb_from_db(config.region, db)
    session, config2 = get_session_and_config(request)
    key = make_cache_key("docdb", config2.access_key or "", config2.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_docdb(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh/stream")
def get_docdb_refresh_stream(request: Request):
    """SSE stream: emits refresh_done when the collector for this region finishes."""
    _, config = get_session_and_config(request)
    channel = f"refresh:docdb:{config.region}"
    return StreamingResponse(
        stream_refresh_done(channel),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/refresh")
def post_docdb_refresh(request: Request):
    _, config = get_session_and_config(request)
    from app.tasks.collect_tasks import collect_resources
    collect_resources.delay("docdb", config.region)
    return {"ok": True, "message": "Refresh started for region " + config.region}


@router.get("/detail")
def get_docdb_detail(request: Request, id: str, is_cluster: bool = False, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        detail = _detail_docdb_from_db(id, is_cluster, config.region, db)
        if detail is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        return detail
    session, _ = get_session_and_config(request)
    docdb = session.client("docdb")
    try:
        if is_cluster:
            c = docdb.describe_db_clusters(DBClusterIdentifier=id).get("DBClusters", [{}])[0]
            member_instances = docdb.describe_db_instances(
                Filters=[{"Name": "db-cluster-id", "Values": [id]}]
            ).get("DBInstances", [])
            member_map = {i["DBInstanceIdentifier"]: i for i in member_instances}
            members = sorted([
                {
                    "id": m["DBInstanceIdentifier"],
                    "role": "Writer" if m.get("IsClusterWriter") else "Reader",
                    "class": member_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceClass", "—"),
                    "az": member_map.get(m["DBInstanceIdentifier"], {}).get("AvailabilityZone", "—"),
                    "status": member_map.get(m["DBInstanceIdentifier"], {}).get("DBInstanceStatus", "—"),
                }
                for m in c.get("DBClusterMembers", [])
            ], key=lambda x: 0 if x["role"] == "Writer" else 1)
            return {
                "type": "cluster",
                "id": c["DBClusterIdentifier"],
                "engine": c.get("Engine", "docdb"),
                "version": c.get("EngineVersion", "—"),
                "status": c.get("Status", "—"),
                "multi_az": c.get("MultiAZ", len(members) > 1),
                "endpoint": c.get("Endpoint"),
                "reader_endpoint": c.get("ReaderEndpoint"),
                "port": c.get("Port"),
                "storage_gb": 0,
                "storage_type": c.get("StorageType", "—"),
                "encrypted": c.get("StorageEncrypted", False),
                "deletion_protection": c.get("DeletionProtection", False),
                "members": members,
                "security_groups": [],
                "tags": [],
            }
        else:
            i = docdb.describe_db_instances(DBInstanceIdentifier=id).get("DBInstances", [{}])[0]
            return {
                "type": "instance",
                "id": i["DBInstanceIdentifier"],
                "engine": i.get("Engine", "docdb"),
                "version": i.get("EngineVersion", "—"),
                "class": i.get("DBInstanceClass", "—"),
                "status": i.get("DBInstanceStatus", "—"),
                "az": i.get("AvailabilityZone", "—"),
                "multi_az": i.get("MultiAZ", False),
                "endpoint": (i.get("Endpoint") or {}).get("Address"),
                "port": (i.get("Endpoint") or {}).get("Port"),
                "storage_gb": i.get("AllocatedStorage", 0),
                "storage_type": i.get("StorageType", "—"),
                "encrypted": i.get("StorageEncrypted", False),
                "deletion_protection": i.get("DeletionProtection", False),
                "security_groups": [],
                "tags": [],
            }
    except ClientError as e:
        raise HTTPException(status_code=404 if e.response.get("Error", {}).get("Code") == "DBClusterNotFoundFault" or e.response.get("Error", {}).get("Code") == "DBInstanceNotFoundFault" else 500, detail=str(e))


@router.get("/metrics")
def get_docdb_metrics(request: Request, id: str, is_cluster: bool = False, hours: int = 24):
    session, _ = get_session_and_config(request)
    docdb_client = session.client("docdb")
    cw = session.client("cloudwatch")

    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)
    period = 60 if hours <= 6 else 300 if hours <= 24 else 3600

    dim_value = id
    if is_cluster:
        try:
            c = docdb_client.describe_db_clusters(DBClusterIdentifier=id).get("DBClusters", [{}])[0]
            writer = next(
                (m["DBInstanceIdentifier"] for m in c.get("DBClusterMembers", []) if m.get("IsClusterWriter")),
                None,
            )
            if writer:
                dim_value = writer
        except Exception:
            pass

    def fetch(metric, stat="Average", scale=1.0):
        try:
            pts = cw.get_metric_statistics(
                Namespace="AWS/DocDB",
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
        "CPUUtilization": fetch("CPUUtilization"),
        "DatabaseConnections": fetch("DatabaseConnections"),
        "FreeStorageSpace": fetch("FreeStorageSpace", scale=1 / (1024 ** 3)),
        "ReadIOPS": fetch("ReadIOPS"),
        "WriteIOPS": fetch("WriteIOPS"),
    }
