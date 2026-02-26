"""
Dashboard widgets API. Widgets are keyed by user email; add-widget is scoped to session services.
"""
from datetime import datetime, timedelta
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..core.database import get_db
from ..core.session import session_store, SESSION_COOKIE_NAME
from ..db.models import DashboardWidget, DashboardPanel, CollectedMetric
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def get_dashboard_user(request: Request) -> dict:
    """Return session config with email and services; 401 if not authenticated."""
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    config = session_store.get_session_config(session_id)
    if not config:
        raise HTTPException(status_code=401, detail="Session expired")
    email = config.get("email")
    if not email:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"email": email, "services": config.get("services", [])}


# ─── Schemas ─────────────────────────────────────────────────────────────────

class WidgetCreate(BaseModel):
    service_type: str
    resource_id: str
    region: str
    title: str | None = None
    widget_type: str = "line"
    metric_names: List[str]
    layout_row: int = 0
    layout_col: int = 0
    panel_id: str


class WidgetUpdate(BaseModel):
    service_type: str | None = None
    resource_id: str | None = None
    region: str | None = None
    title: str | None = None
    metric_names: List[str] | None = None
    layout_row: int | None = None
    layout_col: int | None = None


class PanelCreate(BaseModel):
    title: str


class PanelUpdate(BaseModel):
    title: str | None = None
    collapsed: bool | None = None


class PanelReorder(BaseModel):
    panel_ids: List[str]


def _widget_to_dict(w: DashboardWidget) -> dict:
    return {
        "id": str(w.id),
        "user_email": w.user_email,
        "service_type": w.service_type,
        "resource_id": w.resource_id,
        "region": w.region,
        "title": w.title,
        "widget_type": w.widget_type,
        "metric_names": w.metric_names or [],
        "layout_row": w.layout_row,
        "layout_col": w.layout_col,
        "panel_id": str(w.panel_id) if w.panel_id else None,
        "created_at": w.created_at.isoformat(),
    }


def _panel_to_dict(p: DashboardPanel, widgets: list) -> dict:
    return {
        "id": str(p.id),
        "user_email": p.user_email,
        "title": p.title,
        "collapsed": p.collapsed,
        "sort_order": p.sort_order,
        "created_at": p.created_at.isoformat(),
        "widgets": [_widget_to_dict(w) for w in widgets],
    }


# ─── Panel Endpoints ─────────────────────────────────────────────────────────

@router.get("/panels")
def list_panels(
    request: Request,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """List all panels with nested widgets for the current user."""
    panels = (
        db.query(DashboardPanel)
        .filter(DashboardPanel.user_email == user["email"])
        .order_by(DashboardPanel.sort_order, DashboardPanel.created_at)
        .all()
    )
    panel_ids = [p.id for p in panels]
    widgets = (
        db.query(DashboardWidget)
        .filter(DashboardWidget.panel_id.in_(panel_ids))
        .order_by(DashboardWidget.created_at)
        .all()
    ) if panel_ids else []
    widgets_by_panel: dict = {}
    for w in widgets:
        widgets_by_panel.setdefault(w.panel_id, []).append(w)
    return [_panel_to_dict(p, widgets_by_panel.get(p.id, [])) for p in panels]


@router.post("/panels")
def create_panel(
    body: PanelCreate,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """Create a new panel."""
    max_order = (
        db.query(DashboardPanel.sort_order)
        .filter(DashboardPanel.user_email == user["email"])
        .order_by(DashboardPanel.sort_order.desc())
        .first()
    )
    panel = DashboardPanel(
        user_email=user["email"],
        title=body.title,
        sort_order=(max_order[0] + 1) if max_order else 0,
    )
    db.add(panel)
    db.commit()
    db.refresh(panel)
    return _panel_to_dict(panel, [])


@router.patch("/panels/{panel_id}")
def update_panel(
    panel_id: UUID,
    body: PanelUpdate,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """Update panel title or collapsed state."""
    panel = db.query(DashboardPanel).filter(DashboardPanel.id == panel_id).first()
    if not panel or panel.user_email != user["email"]:
        raise HTTPException(status_code=404, detail="Panel not found")
    if body.title is not None:
        panel.title = body.title
    if body.collapsed is not None:
        panel.collapsed = body.collapsed
    db.commit()
    db.refresh(panel)
    return {"ok": True}


@router.delete("/panels/{panel_id}")
def delete_panel(
    panel_id: UUID,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """Delete a panel and cascade-delete its widgets."""
    panel = db.query(DashboardPanel).filter(DashboardPanel.id == panel_id).first()
    if not panel or panel.user_email != user["email"]:
        raise HTTPException(status_code=404, detail="Panel not found")
    db.query(DashboardWidget).filter(DashboardWidget.panel_id == panel_id).delete()
    db.delete(panel)
    db.commit()
    return None


@router.post("/panels/reorder")
def reorder_panels(
    body: PanelReorder,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """Bulk update panel sort_order based on ordered list of panel_ids."""
    panels = (
        db.query(DashboardPanel)
        .filter(DashboardPanel.user_email == user["email"])
        .all()
    )
    panel_map = {str(p.id): p for p in panels}
    for i, pid in enumerate(body.panel_ids):
        if pid in panel_map:
            panel_map[pid].sort_order = i
    db.commit()
    return {"ok": True}


# ─── Widget Endpoints ────────────────────────────────────────────────────────

@router.get("/widgets")
def list_widgets(
    request: Request,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """List all widgets for the current user."""
    widgets = (
        db.query(DashboardWidget)
        .filter(DashboardWidget.user_email == user["email"])
        .order_by(DashboardWidget.layout_row, DashboardWidget.layout_col, DashboardWidget.created_at)
        .all()
    )
    return [_widget_to_dict(w) for w in widgets]


@router.post("/widgets")
def create_widget(
    request: Request,
    body: WidgetCreate,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """Create a widget. service_type must be in the user's approved services."""
    allowed = set(user["services"])
    if "databases" in allowed:
        allowed.add("docdb")  # DocumentDB is under Databases tab / same IAM
    if body.service_type not in allowed:
        raise HTTPException(status_code=403, detail="Service not in your approved access")
    if not body.metric_names:
        raise HTTPException(status_code=400, detail="metric_names required")
    widget = DashboardWidget(
        user_email=user["email"],
        service_type=body.service_type,
        resource_id=body.resource_id,
        region=body.region,
        title=body.title,
        widget_type=body.widget_type or "line",
        metric_names=body.metric_names,
        layout_row=body.layout_row,
        layout_col=body.layout_col,
        panel_id=body.panel_id,
    )
    db.add(widget)
    db.commit()
    db.refresh(widget)
    return _widget_to_dict(widget)


@router.patch("/widgets/{widget_id}")
def update_widget(
    widget_id: UUID,
    body: WidgetUpdate,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """Update widget: service, resource, region, metric, title, or layout. Must own the widget."""
    widget = db.query(DashboardWidget).filter(DashboardWidget.id == widget_id).first()
    if not widget or widget.user_email != user["email"]:
        raise HTTPException(status_code=404, detail="Widget not found")
    if body.service_type is not None:
        allowed = set(user["services"])
        if "databases" in allowed:
            allowed.add("docdb")
        if body.service_type not in allowed:
            raise HTTPException(status_code=403, detail="Service not in your approved access")
        widget.service_type = body.service_type
    if body.resource_id is not None:
        widget.resource_id = body.resource_id
    if body.region is not None:
        widget.region = body.region
    if body.title is not None:
        widget.title = body.title
    if body.metric_names is not None:
        if not body.metric_names:
            raise HTTPException(status_code=400, detail="metric_names must not be empty")
        widget.metric_names = body.metric_names
    if body.layout_row is not None:
        widget.layout_row = body.layout_row
    if body.layout_col is not None:
        widget.layout_col = body.layout_col
    db.commit()
    db.refresh(widget)
    return _widget_to_dict(widget)


@router.delete("/widgets/{widget_id}")
def delete_widget(
    widget_id: UUID,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """Delete a widget. Must own the widget."""
    widget = db.query(DashboardWidget).filter(DashboardWidget.id == widget_id).first()
    if not widget or widget.user_email != user["email"]:
        raise HTTPException(status_code=404, detail="Widget not found")
    db.delete(widget)
    db.commit()
    return None


# Frontend tab id -> collector service_type (collected_metrics uses the latter)
WIDGET_SERVICE_TO_COLLECTOR = {"databases": "rds"}


@router.get("/widgets/{widget_id}/data")
def get_widget_data(
    widget_id: UUID,
    request: Request,
    range: int = 24,
    user: dict = Depends(get_dashboard_user),
    db: Session = Depends(get_db),
):
    """Return time-series for this widget from collected_metrics. Shape: { metrics: { name: [{ts,v}] }, range_hours }."""
    widget = db.query(DashboardWidget).filter(DashboardWidget.id == widget_id).first()
    if not widget or widget.user_email != user["email"]:
        raise HTTPException(status_code=404, detail="Widget not found")
    range_hours = min(max(range, 1), 72)
    # Map frontend service key to collector key (e.g. databases -> rds)
    service_type = WIDGET_SERVICE_TO_COLLECTOR.get(widget.service_type, widget.service_type)
    start = datetime.utcnow() - timedelta(hours=range_hours)
    rows = (
        db.query(CollectedMetric)
        .filter(
            CollectedMetric.service_type == service_type,
            CollectedMetric.resource_id == widget.resource_id,
            CollectedMetric.region == widget.region,
            CollectedMetric.metric_name.in_(widget.metric_names or []),
            CollectedMetric.timestamp >= start,
        )
        .order_by(CollectedMetric.timestamp.asc())
        .all()
    )
    by_metric = {}
    for m in rows:
        by_metric.setdefault(m.metric_name, []).append({
            "ts": m.timestamp.isoformat() + "Z",
            "v": round(m.value, 4),
        })
    for key in by_metric:
        by_metric[key].sort(key=lambda x: x["ts"])
    return {"metrics": by_metric, "range_hours": range_hours}
