from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session

from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL
from ..core.sse_refresh import stream_refresh_done
from ..core.database import get_db
from ..db.models import CollectedResource, CollectedMetric

router = APIRouter(prefix="/api/lb", tags=["Load Balancers"])
USE_COLLECTOR_DB = True


def _parse_az(azs):
    return [a.get("ZoneName", "") for a in azs if a.get("ZoneName")]


def _fetch_lbs(session):
    """Fetch ALB/NLB/GWLB via elbv2, plus classic ELBs via elb."""
    elbv2 = session.client("elbv2")
    elb_classic = session.client("elb")

    results = []

    # ── elbv2 (ALB, NLB, GWLB) ────────────────────────────────────────────────
    paginator = elbv2.get_paginator("describe_load_balancers")
    v2_lbs = []
    for page in paginator.paginate():
        v2_lbs.extend(page.get("LoadBalancers", []))

    for lb in v2_lbs:
        created = lb.get("CreatedTime")
        results.append({
            "arn": lb.get("LoadBalancerArn", ""),
            "name": lb.get("LoadBalancerName", "—"),
            "type": lb.get("Type", "").upper(),   # application | network | gateway
            "state": lb.get("State", {}).get("Code", "unknown"),
            "scheme": lb.get("Scheme", "—"),
            "dns": lb.get("DNSName", "—"),
            "vpc_id": lb.get("VpcId") or "—",
            "azs": _parse_az(lb.get("AvailabilityZones", [])),
            "created_at": created.isoformat() if created else None,
            "generation": "v2",
        })

    # ── Classic ELB ────────────────────────────────────────────────────────────
    try:
        classic_resp = elb_classic.describe_load_balancers()
        for lb in classic_resp.get("LoadBalancerDescriptions", []):
            created = lb.get("CreatedTime")
            results.append({
                "arn": None,
                "name": lb.get("LoadBalancerName", "—"),
                "type": "CLASSIC",
                "state": "active",
                "scheme": lb.get("Scheme", "—"),
                "dns": lb.get("DNSName", "—"),
                "vpc_id": lb.get("VPCId") or "—",
                "azs": lb.get("AvailabilityZones", []),
                "created_at": created.isoformat() if created else None,
                "generation": "classic",
            })
    except Exception:
        pass

    return {"load_balancers": results, "count": len(results)}


def _fetch_lb_detail(session, lb_name_or_arn):
    """Fetch full detail: listeners, target groups, tags."""
    elbv2 = session.client("elbv2")
    elb_classic = session.client("elb")

    # Try elbv2 first (by ARN if it starts with arn:, else by name)
    lb_data = None
    generation = "v2"

    if lb_name_or_arn.startswith("arn:"):
        try:
            resp = elbv2.describe_load_balancers(LoadBalancerArns=[lb_name_or_arn])
            lbs = resp.get("LoadBalancers", [])
            if lbs:
                lb_data = lbs[0]
        except ClientError:
            pass
    else:
        # Could be classic
        try:
            resp = elb_classic.describe_load_balancers(LoadBalancerNames=[lb_name_or_arn])
            descs = resp.get("LoadBalancerDescriptions", [])
            if descs:
                lb_data = descs[0]
                generation = "classic"
        except ClientError:
            pass

        if not lb_data:
            try:
                resp = elbv2.describe_load_balancers(Names=[lb_name_or_arn])
                lbs = resp.get("LoadBalancers", [])
                if lbs:
                    lb_data = lbs[0]
                    generation = "v2"
            except ClientError:
                pass

    if not lb_data:
        return None

    if generation == "classic":
        return _build_classic_detail(elb_classic, lb_data)
    return _build_v2_detail(elbv2, lb_data)


def _build_v2_detail(elbv2, lb):
    lb_arn = lb.get("LoadBalancerArn", "")
    lb_name = lb.get("LoadBalancerName", "—")
    created = lb.get("CreatedTime")
    fetch_errors = {}

    def fetch_listeners():
        try:
            paginator = elbv2.get_paginator("describe_listeners")
            listeners = []
            for page in paginator.paginate(LoadBalancerArn=lb_arn):
                for lst in page.get("Listeners", []):
                    default_action = "—"
                    for act in lst.get("DefaultActions", []):
                        t = act.get("Type", "")
                        if t == "forward":
                            tg = act.get("TargetGroupArn", "")
                            default_action = f"forward → {tg.split('/')[-2] if '/' in tg else tg}"
                        elif t == "redirect":
                            rc = act.get("RedirectConfig", {})
                            default_action = f"redirect → {rc.get('Protocol','')}{':' + rc.get('Port','') if rc.get('Port') else ''}"
                        elif t == "fixed-response":
                            fc = act.get("FixedResponseConfig", {})
                            default_action = f"fixed {fc.get('StatusCode','')}"
                        else:
                            default_action = t
                        break
                    listeners.append({
                        "arn": lst.get("ListenerArn", ""),
                        "protocol": lst.get("Protocol", "—"),
                        "port": lst.get("Port"),
                        "ssl_policy": lst.get("SslPolicy"),
                        "default_action": default_action,
                    })
            return listeners
        except Exception as e:
            fetch_errors["listeners"] = str(e)
            return []

    def fetch_target_groups():
        try:
            paginator = elbv2.get_paginator("describe_target_groups")
            tgs = []
            for page in paginator.paginate(LoadBalancerArn=lb_arn):
                for tg in page.get("TargetGroups", []):
                    healthy, unhealthy, total = 0, 0, 0
                    try:
                        th = elbv2.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
                        for t in th.get("TargetHealthDescriptions", []):
                            total += 1
                            s = t.get("TargetHealth", {}).get("State", "")
                            if s == "healthy":
                                healthy += 1
                            elif s in ("unhealthy", "draining"):
                                unhealthy += 1
                    except Exception:
                        pass
                    tgs.append({
                        "arn": tg.get("TargetGroupArn", ""),
                        "name": tg.get("TargetGroupName", "—"),
                        "protocol": tg.get("Protocol", "—"),
                        "port": tg.get("Port"),
                        "target_type": tg.get("TargetType", "—"),
                        "vpc_id": tg.get("VpcId") or "—",
                        "healthy": healthy,
                        "unhealthy": unhealthy,
                        "total": total,
                        "hc_protocol": tg.get("HealthCheckProtocol", "—"),
                        "hc_path": tg.get("HealthCheckPath", ""),
                        "hc_interval": tg.get("HealthCheckIntervalSeconds"),
                        "hc_threshold": tg.get("HealthyThresholdCount"),
                    })
            return tgs
        except Exception as e:
            fetch_errors["target_groups"] = str(e)
            return []

    def fetch_tags():
        try:
            resp = elbv2.describe_tags(ResourceArns=[lb_arn])
            for td in resp.get("TagDescriptions", []):
                return [{"key": t["Key"], "value": t["Value"]} for t in td.get("Tags", [])]
        except Exception as e:
            fetch_errors["tags"] = str(e)
        return []

    with ThreadPoolExecutor(max_workers=3) as ex:
        lst_f = ex.submit(fetch_listeners)
        tg_f  = ex.submit(fetch_target_groups)
        tag_f = ex.submit(fetch_tags)
        listeners     = lst_f.result()
        target_groups = tg_f.result()
        tags          = tag_f.result()

    return {
        "arn": lb_arn,
        "name": lb_name,
        "type": lb.get("Type", "").upper(),
        "state": lb.get("State", {}).get("Code", "unknown"),
        "state_reason": lb.get("State", {}).get("Reason"),
        "scheme": lb.get("Scheme", "—"),
        "dns": lb.get("DNSName", "—"),
        "vpc_id": lb.get("VpcId") or "—",
        "azs": _parse_az(lb.get("AvailabilityZones", [])),
        "ip_type": lb.get("IpAddressType", "—"),
        "created_at": created.isoformat() if created else None,
        "generation": "v2",
        "listeners": listeners,
        "target_groups": target_groups,
        "tags": tags,
        "fetch_errors": fetch_errors,
    }


def _build_classic_detail(elb_classic, lb):
    lb_name = lb.get("LoadBalancerName", "—")
    created = lb.get("CreatedTime")
    fetch_errors = {}

    listeners = []
    for ld in lb.get("ListenerDescriptions", []):
        lst = ld.get("Listener", {})
        listeners.append({
            "protocol": lst.get("Protocol", "—"),
            "port": lst.get("LoadBalancerPort"),
            "instance_protocol": lst.get("InstanceProtocol", "—"),
            "instance_port": lst.get("InstancePort"),
            "ssl_cert": lst.get("SSLCertificateId"),
        })

    hc = lb.get("HealthCheck", {})
    instances = []
    try:
        resp = elb_classic.describe_instance_health(LoadBalancerName=lb_name)
        for i in resp.get("InstanceStates", []):
            instances.append({
                "id": i.get("InstanceId", ""),
                "state": i.get("State", "—"),
                "description": i.get("Description", ""),
            })
    except Exception as e:
        fetch_errors["instances"] = str(e)

    tags = []
    try:
        resp = elb_classic.describe_tags(LoadBalancerNames=[lb_name])
        for td in resp.get("TagDescriptions", []):
            tags = [{"key": t["Key"], "value": t["Value"]} for t in td.get("Tags", [])]
            break
    except Exception as e:
        fetch_errors["tags"] = str(e)

    return {
        "arn": None,
        "name": lb_name,
        "type": "CLASSIC",
        "state": "active",
        "state_reason": None,
        "scheme": lb.get("Scheme", "—"),
        "dns": lb.get("DNSName", "—"),
        "vpc_id": lb.get("VPCId") or "—",
        "azs": lb.get("AvailabilityZones", []),
        "ip_type": "—",
        "created_at": created.isoformat() if created else None,
        "generation": "classic",
        "listeners": listeners,
        "target_groups": [],
        "instances": instances,
        "health_check": {
            "target": hc.get("Target", "—"),
            "interval": hc.get("Interval"),
            "timeout": hc.get("Timeout"),
            "healthy_threshold": hc.get("HealthyThreshold"),
            "unhealthy_threshold": hc.get("UnhealthyThreshold"),
        },
        "tags": tags,
        "fetch_errors": fetch_errors,
    }


# ─── DB-backed list/detail (Phase 2) ───────────────────────────────────────────

def _list_lb_from_db(region: str, db: Session):
    rows = (
        db.query(CollectedResource)
        .filter(CollectedResource.service_type == "elb", CollectedResource.region == region)
        .all()
    )
    load_balancers = []
    for r in rows:
        att = r.attributes or {}
        load_balancers.append({
            "arn": att.get("arn"),
            "name": r.name or r.resource_id,
            "type": att.get("type", "—"),
            "state": att.get("state", "unknown"),
            "scheme": att.get("scheme", "—"),
            "dns": att.get("dns", "—"),
            "vpc_id": att.get("vpc_id", "—"),
            "azs": att.get("azs", []),
            "created_at": att.get("created_at"),
            "generation": att.get("generation", "v2"),
        })
    return {"load_balancers": load_balancers, "count": len(load_balancers)}


def _detail_lb_from_db(lb_id: str, region: str, db: Session):
    r = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "elb",
            CollectedResource.region == region,
            CollectedResource.resource_id == lb_id,
        )
        .first()
    )
    if not r:
        return None
    att = r.attributes or {}
    return {
        "arn": att.get("arn"),
        "name": r.name or r.resource_id,
        "type": att.get("type", "—"),
        "state": att.get("state", "unknown"),
        "scheme": att.get("scheme", "—"),
        "dns": att.get("dns", "—"),
        "vpc_id": att.get("vpc_id", "—"),
        "azs": att.get("azs", []),
        "created_at": att.get("created_at"),
        "generation": att.get("generation", "v2"),
        "listeners": att.get("listeners", []),
        "target_groups": att.get("target_groups", []),
        "tags": att.get("tags", []),
        "fetch_errors": {},
    }


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("")
def get_load_balancers(request: Request, force: bool = False, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        return _list_lb_from_db(config.region, db)
    session, config2 = get_session_and_config(request)
    key = make_cache_key("lb", config2.access_key or "", config2.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_lbs(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh/stream")
def get_lb_refresh_stream(request: Request):
    """SSE stream: emits refresh_done when the collector for this region finishes."""
    _, config = get_session_and_config(request)
    channel = f"refresh:elb:{config.region}"
    return StreamingResponse(
        stream_refresh_done(channel),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/refresh")
def post_lb_refresh(request: Request):
    _, config = get_session_and_config(request)
    from app.tasks.collect_tasks import collect_resources
    collect_resources.delay("elb", config.region)
    return {"ok": True, "message": "Refresh started for region " + config.region}


def _fetch_lb_metrics(session, lb_id, hours):
    cw = session.client("cloudwatch")
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    period_map = {24: 300, 48: 600, 72: 900}
    period = period_map.get(hours, 300)

    # Determine type and dimensions
    namespace = "AWS/ApplicationELB"
    dimensions = []

    if lb_id.startswith("arn:"):
        # v2 (ALB, NLB)
        # arn:aws:elasticloadbalancing:region:account:loadbalancer/app/name/id
        suffix = lb_id.split("/")[-3:] # [app/net, name, id]
        namespace = "AWS/ApplicationELB" if suffix[0] == "app" else "AWS/NetworkELB"
        lb_val = "/".join(suffix)
        dimensions = [{"Name": "LoadBalancer", "Value": lb_val}]
    else:
        # Classic
        namespace = "AWS/ELB"
        dimensions = [{"Name": "LoadBalancerName", "Value": lb_id}]

    def _series(metric, stat="Average"):
        try:
            resp = cw.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric,
                Dimensions=dimensions,
                StartTime=start,
                EndTime=end,
                Period=period,
                Statistics=[stat],
            )
            pts = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
            return {p["Timestamp"].isoformat(): p[stat] for p in pts}
        except Exception:
            return {}

    with ThreadPoolExecutor(max_workers=2) as ex:
        if namespace == "AWS/ApplicationELB":
            bytes_f = ex.submit(_series, "ProcessedBytes", "Sum")
            req_f   = ex.submit(_series, "RequestCount", "Sum")
            return {
                "processed_bytes": bytes_f.result(),
                "request_count":   req_f.result(),
            }
        elif namespace == "AWS/NetworkELB":
            bytes_f = ex.submit(_series, "ProcessedBytes", "Sum")
            flow_f  = ex.submit(_series, "ActiveFlowCount", "Average")
            return {
                "processed_bytes": bytes_f.result(),
                "active_flow_count": flow_f.result(),
            }
        else: # Classic
            # Try ProcessedBytes first, then fallback to EstimatedProcessedBytes
            bytes_pts = _series("ProcessedBytes", "Sum")
            if not bytes_pts:
                bytes_pts = _series("EstimatedProcessedBytes", "Sum")
            
            req_f = ex.submit(_series, "RequestCount", "Sum")
            return {
                "processed_bytes": bytes_pts,
                "request_count":   req_f.result(),
            }


def _metrics_lb_from_db(lb_id: str, region: str, hours: int, db: Session):
    start = datetime.utcnow() - timedelta(hours=hours)
    rows = (
        db.query(CollectedMetric)
        .filter(
            CollectedMetric.service_type == "elb",
            CollectedMetric.resource_id == lb_id,
            CollectedMetric.region == region,
            CollectedMetric.timestamp >= start,
        )
        .order_by(CollectedMetric.timestamp.asc())
        .all()
    )
    key_map = {
        "ProcessedBytes": "processed_bytes",
        "EstimatedProcessedBytes": "processed_bytes",
        "RequestCount": "request_count",
        "ActiveFlowCount": "active_flow_count",
    }
    out = {}
    for m in rows:
        key = key_map.get(m.metric_name)
        if key is None:
            continue
        out.setdefault(key, {})[m.timestamp.isoformat() + "Z"] = m.value
    for k in ["processed_bytes", "request_count", "active_flow_count"]:
        out.setdefault(k, {})
    return out


@router.get("/{lb_id:path}/metrics")
def get_lb_metrics(lb_id: str, request: Request, hours: int = 24, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        return _metrics_lb_from_db(lb_id, config.region, hours, db)
    session, _ = get_session_and_config(request)
    try:
        return _fetch_lb_metrics(session, lb_id, hours)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{lb_id:path}")
def get_lb_detail(lb_id: str, request: Request, db: Session = Depends(get_db)):
    _, config = get_session_and_config(request)
    if USE_COLLECTOR_DB:
        detail = _detail_lb_from_db(lb_id, config.region, db)
        if detail is None:
            raise HTTPException(status_code=404, detail="Load balancer not found")
        return detail
    try:
        session, _ = get_session_and_config(request)
        detail = _fetch_lb_detail(session, lb_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="Load balancer not found")
        return detail
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
