"""
Alerts API: CloudWatch alarms + AWS Health events.
All results scoped to user's approved services from session.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.session import session_store, SESSION_COOKIE_NAME
from ..db.models import CollectedAlarm, CollectedHealthEvent

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _user_services(request: Request) -> list:
    """Extract approved services from session; raise 401 if not authenticated."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    config = session_store.get_session_config(session_id)
    if not config:
        raise HTTPException(status_code=401, detail="Session expired")
    return config.get("services", [])


def _alarm_to_dict(a: CollectedAlarm) -> dict:
    return {
        "id": str(a.id),
        "alarm_name": a.alarm_name,
        "alarm_arn": a.alarm_arn,
        "service_type": a.service_type,
        "resource_id": a.resource_id,
        "region": a.region,
        "state": a.state,
        "state_reason": a.state_reason,
        "state_updated_at": a.state_updated_at.isoformat() if a.state_updated_at else None,
        "metric_name": a.metric_name,
        "namespace": a.namespace,
        "dimensions": a.dimensions,
        "collected_at": a.collected_at.isoformat() if a.collected_at else None,
    }


def _health_to_dict(e: CollectedHealthEvent) -> dict:
    return {
        "id": str(e.id),
        "event_arn": e.event_arn,
        "service": e.service,
        "service_type": e.service_type,
        "region": e.region,
        "event_type": e.event_type,
        "category": e.category,
        "status": e.status,
        "title": e.title,
        "description": e.description,
        "start_time": e.start_time.isoformat() if e.start_time else None,
        "end_time": e.end_time.isoformat() if e.end_time else None,
        "last_updated": e.last_updated.isoformat() if e.last_updated else None,
        "collected_at": e.collected_at.isoformat() if e.collected_at else None,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/summary")
def alerts_summary(request: Request, db: Session = Depends(get_db)):
    """Alarm + health counts grouped by service, scoped to user's approved services."""
    services = _user_services(request)

    # --- Alarm counts (state = ALARM only) ---
    alarm_rows = (
        db.query(CollectedAlarm.service_type, func.count(CollectedAlarm.id))
        .filter(
            CollectedAlarm.state == "ALARM",
            CollectedAlarm.service_type.in_(services),
        )
        .group_by(CollectedAlarm.service_type)
        .all()
    )
    alarm_by_service = {svc: cnt for svc, cnt in alarm_rows}
    alarm_total = sum(alarm_by_service.values())

    # --- Health counts (open or upcoming) ---
    health_q = (
        db.query(CollectedHealthEvent.service_type, func.count(CollectedHealthEvent.id))
        .filter(CollectedHealthEvent.status.in_(["open", "upcoming"]))
    )
    # Include service_type IS NULL (general/account-wide) + user's services
    from sqlalchemy import or_
    health_q = health_q.filter(
        or_(
            CollectedHealthEvent.service_type.is_(None),
            CollectedHealthEvent.service_type.in_(services),
        )
    )
    health_rows = health_q.group_by(CollectedHealthEvent.service_type).all()
    health_by_service = {}
    for svc, cnt in health_rows:
        key = svc if svc is not None else "general"
        health_by_service[key] = cnt
    health_total = sum(health_by_service.values())

    return {
        "alarms": {"total": alarm_total, "by_service": alarm_by_service},
        "health": {"total": health_total, "by_service": health_by_service},
    }


@router.get("/alarms")
def list_alarms(
    request: Request,
    service_type: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List CloudWatch alarms scoped to user's approved services."""
    services = _user_services(request)

    q = db.query(CollectedAlarm).filter(CollectedAlarm.service_type.in_(services))
    if service_type:
        q = q.filter(CollectedAlarm.service_type == service_type)
    if state:
        q = q.filter(CollectedAlarm.state == state)
    q = q.order_by(CollectedAlarm.state_updated_at.desc()).limit(200)

    return [_alarm_to_dict(a) for a in q.all()]


@router.get("/health")
def list_health(
    request: Request,
    service_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """List AWS Health events scoped to user's approved services + general events."""
    services = _user_services(request)

    from sqlalchemy import or_
    q = db.query(CollectedHealthEvent).filter(
        or_(
            CollectedHealthEvent.service_type.is_(None),
            CollectedHealthEvent.service_type.in_(services),
        )
    )
    if service_type:
        if service_type == "general":
            q = db.query(CollectedHealthEvent).filter(CollectedHealthEvent.service_type.is_(None))
        else:
            q = db.query(CollectedHealthEvent).filter(CollectedHealthEvent.service_type == service_type)
    if status:
        q = q.filter(CollectedHealthEvent.status == status)
    q = q.order_by(CollectedHealthEvent.start_time.desc()).limit(200)

    return [_health_to_dict(e) for e in q.all()]


@router.get("/resource/{resource_id}")
def resource_alarms(
    request: Request,
    resource_id: str,
    db: Session = Depends(get_db),
):
    """Alarms for a specific resource, scoped to user's approved services."""
    services = _user_services(request)

    q = (
        db.query(CollectedAlarm)
        .filter(
            CollectedAlarm.resource_id == resource_id,
            CollectedAlarm.service_type.in_(services),
        )
        .order_by(CollectedAlarm.state_updated_at.desc())
    )

    return [_alarm_to_dict(a) for a in q.all()]
