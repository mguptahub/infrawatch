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
from ..db.models import CollectedResource, CollectedMetric

router = APIRouter(prefix="/api/ec2", tags=["EC2"])

# When True, list/detail/metrics read from collected_resources and collected_metrics (Phase 2).
USE_COLLECTOR_DB = True


def _uptime_hours(launch_time):
    if not launch_time:
        return None
    now = datetime.now(timezone.utc)
    if isinstance(launch_time, str):
        try:
            launch_time = datetime.fromisoformat(launch_time.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if launch_time.tzinfo is None:
        launch_time = launch_time.replace(tzinfo=timezone.utc)
    return round((now - launch_time).total_seconds() / 3600, 1)


def _list_ec2_from_db(region: str, db: Session):
    """Return {instances: [...], count: N} from collected_resources + latest CPU from collected_metrics."""
    rows = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "ec2",
            CollectedResource.region == region,
        )
        .all()
    )
    if not rows:
        return {"instances": [], "count": 0}
    resource_ids = [r.resource_id for r in rows]
    # Latest CPU per resource (one subquery per resource_id would be N+1; do one raw or use distinct on)
    latest_cpu = {}
    cutoff = datetime.utcnow() - timedelta(hours=1)
    for r in rows:
        m = (
            db.query(CollectedMetric)
            .filter(
                CollectedMetric.service_type == "ec2",
                CollectedMetric.resource_id == r.resource_id,
                CollectedMetric.region == region,
                CollectedMetric.metric_name == "CPUUtilization",
                CollectedMetric.timestamp >= cutoff,
            )
            .order_by(CollectedMetric.timestamp.desc())
            .first()
        )
        if m:
            latest_cpu[r.resource_id] = round(m.value, 1)
    instances = []
    for r in rows:
        att = r.attributes or {}
        launch_time = att.get("launch_time")
        instances.append({
            "id": r.resource_id,
            "name": r.name or r.resource_id,
            "state": att.get("state", "—"),
            "type": att.get("instance_type", "—"),
            "az": att.get("availability_zone") or "—",
            "private_ip": att.get("private_ip"),
            "public_ip": att.get("public_ip"),
            "launch_time": launch_time,
            "uptime_hours": _uptime_hours(launch_time),
            "cpu_percent": latest_cpu.get(r.resource_id),
        })
    return {"instances": instances, "count": len(instances)}


def _detail_ec2_from_db(instance_id: str, region: str, db: Session):
    """Return instance detail from collected_resources or None."""
    r = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "ec2",
            CollectedResource.region == region,
            CollectedResource.resource_id == instance_id,
        )
        .first()
    )
    if not r:
        return None
    att = r.attributes or {}
    launch_time = att.get("launch_time")
    return {
        "id": r.resource_id,
        "name": r.name or r.resource_id,
        "state": att.get("state", "—"),
        "type": att.get("instance_type", "—"),
        "az": att.get("availability_zone") or "—",
        "vpc_id": att.get("vpc_id") or "—",
        "subnet_id": att.get("subnet_id") or "—",
        "private_ip": att.get("private_ip"),
        "public_ip": att.get("public_ip"),
        "launch_time": launch_time,
        "uptime_hours": _uptime_hours(launch_time),
        "key_name": att.get("key_name") or "—",
        "iam_profile": att.get("iam_profile"),
        "architecture": att.get("architecture", "—"),
        "ami_id": att.get("ami_id", "—"),
        "tags": att.get("tags") or [],
        "security_groups": att.get("security_groups") or [],
        "volumes": att.get("volumes") or [],
        "metrics": att.get("metrics") or {},
    }


def _metrics_ec2_from_db(instance_id: str, region: str, hours: int, db: Session):
    """Return metrics time-series from collected_metrics (same shape as _fetch_ec2_metrics)."""
    start = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        db.query(CollectedMetric)
        .filter(
            CollectedMetric.service_type == "ec2",
            CollectedMetric.resource_id == instance_id,
            CollectedMetric.region == region,
            CollectedMetric.metric_name == "CPUUtilization",
            CollectedMetric.timestamp >= start,
        )
        .order_by(CollectedMetric.timestamp.asc())
        .all()
    )
    period_map = {24: 300, 48: 600, 72: 900}
    period = period_map.get(hours, 300)
    cpu = [{"ts": m.timestamp.isoformat(), "v": round(m.value, 3)} for m in rows]
    return {
        "instance_id": instance_id,
        "hours": hours,
        "period_seconds": period,
        "metrics": {
            "cpu": cpu,
            "network_in": [],
            "network_out": [],
            "disk_read": [],
            "disk_write": [],
            "memory": [],
        },
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
            "description": r.get("Description", ""),
        })
    return result


def _fetch_ec2(session):
    ec2 = session.client("ec2")
    cw = session.client("cloudwatch")

    paginator = ec2.get_paginator("describe_instances")
    all_instances = []
    for page in paginator.paginate():
        for reservation in page["Reservations"]:
            all_instances.extend(reservation["Instances"])

    if not all_instances:
        return {"instances": [], "count": 0}

    def fetch_instance_data(i):
        name = next(
            (t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "—"
        )
        cpu = None
        try:
            metrics = cw.get_metric_statistics(
                Namespace="AWS/EC2",
                MetricName="CPUUtilization",
                Dimensions=[{"Name": "InstanceId", "Value": i["InstanceId"]}],
                StartTime=datetime.utcnow() - timedelta(minutes=10),
                EndTime=datetime.utcnow(),
                Period=300,
                Statistics=["Average"],
            )
            pts = sorted(metrics["Datapoints"], key=lambda x: x["Timestamp"])
            if pts:
                cpu = round(pts[-1]["Average"], 1)
        except Exception:
            pass

        launch_time = i.get("LaunchTime")
        return {
            "id": i["InstanceId"],
            "name": name,
            "state": i["State"]["Name"],
            "type": i["InstanceType"],
            "az": i.get("Placement", {}).get("AvailabilityZone", "—"),
            "private_ip": i.get("PrivateIpAddress"),
            "public_ip": i.get("PublicIpAddress"),
            "launch_time": launch_time.isoformat() if launch_time else None,
            "uptime_hours": _uptime_hours(launch_time),
            "cpu_percent": cpu,
        }

    with ThreadPoolExecutor(max_workers=10) as executor:
        instances = list(executor.map(fetch_instance_data, all_instances))

    return {"instances": instances, "count": len(instances)}


def _fetch_ec2_detail(session, instance_id):
    ec2 = session.client("ec2")
    cw = session.client("cloudwatch")

    resp = ec2.describe_instances(InstanceIds=[instance_id])
    if not resp.get("Reservations"):
        return None
    i = resp["Reservations"][0]["Instances"][0]

    name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "—")
    tags = [{"key": t["Key"], "value": t["Value"]} for t in i.get("Tags", [])]

    iam_profile = None
    if i.get("IamInstanceProfile"):
        arn = i["IamInstanceProfile"].get("Arn", "")
        iam_profile = arn.split("/")[-1] if "/" in arn else arn

    sg_ids = [sg["GroupId"] for sg in i.get("SecurityGroups", [])]
    volume_ids = [
        v["Ebs"]["VolumeId"]
        for v in i.get("BlockDeviceMappings", [])
        if "Ebs" in v
    ]

    def fetch_sgs():
        if not sg_ids:
            return []
        sgs = ec2.describe_security_groups(GroupIds=sg_ids)["SecurityGroups"]
        return [
            {
                "id": sg["GroupId"],
                "name": sg["GroupName"],
                "description": sg.get("Description", ""),
                "inbound": _fmt_sg_rules(sg.get("IpPermissions", [])),
                "outbound": _fmt_sg_rules(sg.get("IpPermissionsEgress", [])),
            }
            for sg in sgs
        ]

    def fetch_volumes():
        if not volume_ids:
            return []
        vols = ec2.describe_volumes(VolumeIds=volume_ids)["Volumes"]
        result = []
        for vol in vols:
            device = next(
                (m["DeviceName"] for m in i.get("BlockDeviceMappings", [])
                 if m.get("Ebs", {}).get("VolumeId") == vol["VolumeId"]),
                "—",
            )
            result.append({
                "id": vol["VolumeId"],
                "device": device,
                "size_gb": vol["Size"],
                "type": vol["VolumeType"],
                "state": vol["State"],
                "encrypted": vol.get("Encrypted", False),
                "iops": vol.get("Iops"),
            })
        return result

    def fetch_metrics():
        def _get(metric_name, stat="Average", minutes=5):
            try:
                r = cw.get_metric_statistics(
                    Namespace="AWS/EC2",
                    MetricName=metric_name,
                    Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                    StartTime=datetime.utcnow() - timedelta(minutes=minutes),
                    EndTime=datetime.utcnow(),
                    Period=minutes * 60,
                    Statistics=[stat],
                )
                pts = sorted(r["Datapoints"], key=lambda x: x["Timestamp"])
                return round(pts[-1][stat], 2) if pts else None
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=5) as ex:
            cpu_f  = ex.submit(_get, "CPUUtilization")
            nin_f  = ex.submit(_get, "NetworkIn")
            nout_f = ex.submit(_get, "NetworkOut")
            dr_f   = ex.submit(_get, "DiskReadBytes")
            dw_f   = ex.submit(_get, "DiskWriteBytes")
            return {
                "cpu_percent":       cpu_f.result(),
                "network_in_bytes":  nin_f.result(),
                "network_out_bytes": nout_f.result(),
                "disk_read_bytes":   dr_f.result(),
                "disk_write_bytes":  dw_f.result(),
            }

    with ThreadPoolExecutor(max_workers=3) as executor:
        sg_f  = executor.submit(fetch_sgs)
        vol_f = executor.submit(fetch_volumes)
        met_f = executor.submit(fetch_metrics)
        security_groups = sg_f.result()
        volumes         = vol_f.result()
        metrics         = met_f.result()

    launch_time = i.get("LaunchTime")
    return {
        "id": instance_id,
        "name": name,
        "state": i["State"]["Name"],
        "type": i["InstanceType"],
        "az": i.get("Placement", {}).get("AvailabilityZone", "—"),
        "vpc_id": i.get("VpcId") or "—",
        "subnet_id": i.get("SubnetId") or "—",
        "private_ip": i.get("PrivateIpAddress"),
        "public_ip": i.get("PublicIpAddress"),
        "launch_time": launch_time.isoformat() if launch_time else None,
        "uptime_hours": _uptime_hours(launch_time),
        "key_name": i.get("KeyName") or "—",
        "iam_profile": iam_profile,
        "architecture": i.get("Architecture", "—"),
        "ami_id": i.get("ImageId", "—"),
        "tags": tags,
        "security_groups": security_groups,
        "volumes": volumes,
        "metrics": metrics,
    }


@router.get("/instances")
def get_ec2_instances(
    request: Request,
    force: bool = False,
    db: Session = Depends(get_db),
):
    session, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        return _list_ec2_from_db(config.region, db)
    key = make_cache_key("ec2", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_ec2(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


def _fetch_ec2_metrics(session, instance_id, hours):
    cw = session.client("cloudwatch")
    end = datetime.utcnow()
    start = end - timedelta(hours=hours)
    # ~288 data points regardless of range
    period_map = {24: 300, 48: 600, 72: 900}
    period = period_map.get(hours, 300)

    def _series(namespace, metric, stat="Average"):
        try:
            resp = cw.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric,
                Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
                StartTime=start,
                EndTime=end,
                Period=period,
                Statistics=[stat],
            )
            pts = sorted(resp["Datapoints"], key=lambda x: x["Timestamp"])
            return {p["Timestamp"].isoformat(): p[stat] for p in pts}
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=8) as ex:
        cpu_f  = ex.submit(_series, "AWS/EC2", "CPUUtilization")
        nin_f  = ex.submit(_series, "AWS/EC2", "NetworkIn", "Sum")
        nout_f = ex.submit(_series, "AWS/EC2", "NetworkOut", "Sum")
        dr_f   = ex.submit(_series, "AWS/EC2", "DiskReadBytes", "Sum")
        dw_f   = ex.submit(_series, "AWS/EC2", "DiskWriteBytes", "Sum")
        er_f   = ex.submit(_series, "AWS/EC2", "EBSReadBytes", "Sum")
        ew_f   = ex.submit(_series, "AWS/EC2", "EBSWriteBytes", "Sum")
        mem_f  = ex.submit(_series, "CWAgent", "mem_used_percent")

        # Get results
        cpu_pts = cpu_f.result()
        nin_pts = nin_f.result()
        nout_pts = nout_f.result()
        dr_pts = dr_f.result()
        dw_pts = dw_f.result()
        er_pts = er_f.result()
        ew_pts = ew_f.result()
        mem_pts = mem_f.result()

    def _to_list(pts):
        return [{"ts": ts, "v": round(v, 3)} for ts, v in sorted(pts.items())]

    # Combine Disk and EBS metrics
    disk_read = dr_pts.copy()
    for ts, v in er_pts.items():
        disk_read[ts] = disk_read.get(ts, 0) + v

    disk_write = dw_pts.copy()
    for ts, v in ew_pts.items():
        disk_write[ts] = disk_write.get(ts, 0) + v

    return {
        "instance_id": instance_id,
        "hours": hours,
        "period_seconds": period,
        "metrics": {
            "cpu":        _to_list(cpu_pts),
            "network_in": _to_list(nin_pts),
            "network_out": _to_list(nout_pts),
            "disk_read":  _to_list(disk_read),
            "disk_write": _to_list(disk_write),
            "memory":     _to_list(mem_pts),
        },
    }


@router.get("/instances/{instance_id}/metrics")
def get_ec2_metrics(
    instance_id: str,
    request: Request,
    hours: int = 24,
    db: Session = Depends(get_db),
):
    hours = min(max(hours, 1), 72)
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        return _metrics_ec2_from_db(instance_id, config.region, hours, db)
    try:
        session, _ = get_session_and_config(request)
        return _fetch_ec2_metrics(session, instance_id, hours)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/instances/{instance_id}")
def get_ec2_instance_detail(
    instance_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        detail = _detail_ec2_from_db(instance_id, config.region, db)
        if detail is None:
            raise HTTPException(status_code=404, detail="Instance not found")
        return detail
    try:
        session, _ = get_session_and_config(request)
        detail = _fetch_ec2_detail(session, instance_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Instance not found")
        return detail
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh/stream")
def get_ec2_refresh_stream(request: Request):
    """SSE stream: emits refresh_done when the collector for this region finishes."""
    _, config = get_session_and_config(request)
    channel = f"refresh:ec2:{config.region}"
    return StreamingResponse(
        stream_refresh_done(channel),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/refresh")
def post_ec2_refresh(request: Request):
    """Enqueue Celery task to refresh EC2 resources for the session's region."""
    _, config = get_session_and_config(request)
    from app.tasks.collect_tasks import collect_resources
    collect_resources.delay("ec2", config.region)
    return {"ok": True, "message": "Refresh started for region " + config.region}
