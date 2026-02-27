import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from ..core.database import get_db
from ..core.config import settings
from ..core.session import session_store, SESSION_COOKIE_NAME
from ..core.sts_service import ALL_SERVICES, assume_role_for_services
from ..core.valkey_client import get_client as get_valkey
from ..db.models import User, UserRole, AccessRequest, RequestStatus
import json
from datetime import timedelta

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# ─── Admin auth guard ─────────────────────────────────────────────────────────

def require_admin(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    config = session_store.get_session_config(session_id)
    if not config or config.get("mode") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return config


# ─── Models ───────────────────────────────────────────────────────────────────

class CreateUserBody(BaseModel):
    email: str
    name: str
    role: str = "employee"      # "employee" or "manager"
    manager_email: Optional[str] = None
    allowed_services: list[str] = []
    max_duration_hours: int = 1
    auto_approve: bool = False


class UpdateUserBody(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    manager_email: Optional[str] = None
    allowed_services: Optional[list[str]] = None
    max_duration_hours: Optional[int] = None
    auto_approve: Optional[bool] = None
    active: Optional[bool] = None


class AdminActionBody(BaseModel):
    action: str             # "approve" or "deny"
    denial_reason: Optional[str] = None


# ─── Services list ────────────────────────────────────────────────────────────

@router.get("/services")
def list_services(_=Depends(require_admin)):
    return {"services": ALL_SERVICES}


# ─── Users ────────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(require_admin)):
    users = db.query(User).order_by(User.name).all()
    return [_user_dict(u) for u in users]


@router.post("/users")
def create_user(body: CreateUserBody, db: Session = Depends(get_db), _=Depends(require_admin)):
    email = body.email.strip().lower()
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    if body.max_duration_hours > 12:
        raise HTTPException(status_code=400, detail="Max duration cannot exceed 12 hours (AWS STS limit)")

    # Validate services
    invalid = [s for s in body.allowed_services if s not in ALL_SERVICES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unknown services: {', '.join(invalid)}")

    manager = None
    if body.manager_email:
        manager = db.query(User).filter(
            User.email == body.manager_email.strip().lower(),
            User.role == UserRole.manager,
            User.active == True,  # noqa: E712
        ).first()
        if not manager:
            raise HTTPException(status_code=404, detail="Manager not found or not a manager role")

    try:
        role = UserRole(body.role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid role")

    user = User(
        email=email,
        name=body.name,
        role=role,
        manager_id=manager.id if manager else None,
        allowed_services=body.allowed_services,
        max_duration_hours=body.max_duration_hours,
        auto_approve=body.auto_approve,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UpdateUserBody,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.name is not None:
        user.name = body.name
    if body.auto_approve is not None:
        user.auto_approve = body.auto_approve
    if body.active is not None:
        user.active = body.active
    if body.max_duration_hours is not None:
        if body.max_duration_hours > 12:
            raise HTTPException(status_code=400, detail="Max duration cannot exceed 12 hours (AWS STS limit)")
        user.max_duration_hours = body.max_duration_hours
    if body.allowed_services is not None:
        invalid = [s for s in body.allowed_services if s not in ALL_SERVICES]
        if invalid:
            raise HTTPException(status_code=400, detail=f"Unknown services: {', '.join(invalid)}")
        user.allowed_services = body.allowed_services
    if body.role is not None:
        try:
            user.role = UserRole(body.role)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid role")
    if body.manager_email is not None:
        if body.manager_email == "":
            user.manager_id = None
        else:
            manager = db.query(User).filter(
                User.email == body.manager_email.strip().lower(),
                User.role == UserRole.manager,
                User.active == True,  # noqa: E712
            ).first()
            if not manager:
                raise HTTPException(status_code=404, detail="Manager not found or not a manager role")
            user.manager_id = manager.id

    db.commit()
    db.refresh(user)
    return _user_dict(user)


# ─── Requests ─────────────────────────────────────────────────────────────────

@router.get("/requests")
def list_requests(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    q = db.query(AccessRequest).order_by(AccessRequest.created_at.desc())
    if status:
        try:
            q = q.filter(AccessRequest.status == RequestStatus(status))
        except ValueError:
            pass
    reqs = q.limit(200).all()
    return [_request_dict(r) for r in reqs]


@router.post("/requests/{request_id}/action")
async def admin_action(
    request_id: str,
    body: AdminActionBody,
    db: Session = Depends(get_db),
    admin=Depends(require_admin),
):
    req = db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")
    if req.status != RequestStatus.pending:
        raise HTTPException(status_code=409, detail=f"Request already {req.status.value}")

    admin_email = admin.get("email", settings.admin_email)
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by_email = admin_email

    if body.action == "deny":
        req.status = RequestStatus.denied
        req.denial_reason = body.denial_reason or ""
        db.commit()
        from ..core.email_service import send_denial_notification
        await send_denial_notification(req.user.email, req.user.name, req.services, req.denial_reason)
        return {"success": True, "action": "denied"}

    if body.action == "approve":
        try:
            creds = assume_role_for_services(
                services=req.services,
                duration_hours=req.duration_hours,
                session_name=f"dashboard-{req.user.email.split('@')[0]}-{req.id}",
            )
        except Exception as e:
            logger.error("STS AssumeRole failed: %s", e)
            raise HTTPException(status_code=500, detail="Failed to issue AWS credentials. Contact your admin.")

        # Revoke any previously active sessions for this user
        now = datetime.utcnow()
        old_active = db.query(AccessRequest).filter(
            AccessRequest.user_id == req.user_id,
            AccessRequest.status == RequestStatus.approved,
            AccessRequest.expires_at > now,
            AccessRequest.id != req.id,
        ).all()
        valkey = get_valkey()
        for old in old_active:
            valkey.delete(f"sts:{old.id}")
            old.status = RequestStatus.expired

        valkey.setex(f"sts:{req.id}", req.duration_hours * 3600, json.dumps(creds))

        req.status = RequestStatus.approved
        req.expires_at = datetime.utcnow() + timedelta(hours=req.duration_hours)
        db.commit()

        from ..core.email_service import send_approval_confirmation
        login_url = f"{settings.frontend_url}/login"
        await send_approval_confirmation(
            req.user.email, req.user.name, req.services, req.duration_hours, login_url
        )
        return {"success": True, "action": "approved"}

    raise HTTPException(status_code=400, detail="action must be 'approve' or 'deny'")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _user_dict(u: User) -> dict:
    return {
        "id": str(u.id),
        "email": u.email,
        "name": u.name,
        "role": u.role.value,
        "manager_email": u.manager.email if u.manager else None,
        "manager_name": u.manager.name if u.manager else None,
        "allowed_services": u.allowed_services or [],
        "max_duration_hours": u.max_duration_hours,
        "auto_approve": u.auto_approve,
        "active": u.active,
        "created_at": u.created_at.isoformat(),
    }


def _request_dict(r: AccessRequest) -> dict:
    return {
        "id": str(r.id),
        "user_email": r.user.email,
        "user_name": r.user.name,
        "services": r.services,
        "duration_hours": r.duration_hours,
        "status": r.status.value,
        "created_at": r.created_at.isoformat(),
        "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
        "reviewed_by": r.reviewed_by_email,
        "denial_reason": r.denial_reason,
        "expires_at": r.expires_at.isoformat() if r.expires_at else None,
    }
