import secrets as secrets_module
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.orm import Session
from ..core.database import get_db
from ..core.config import settings
from ..core.limiter import limiter
from ..core.otp_service import create_otp, verify_otp
from ..core.email_service import (
    send_manager_notification, send_otp,
    send_approval_confirmation, send_denial_notification,
    send_new_user_notification,
)
from ..core.sts_service import assume_role_for_services, ALL_SERVICES
from ..core.valkey_client import get_client as get_valkey
from ..db.models import (
    User, AccessRequest, ApprovalToken, RequestStatus, OTPPurpose, UserRole,
)

router = APIRouter(prefix="/api/requests", tags=["Requests"])

APPROVAL_TOKEN_EXPIRY_HOURS = 48


def _revoke_active_sessions(user_id, excluding_request_id, db: Session):
    """Delete STS creds from Valkey for any other active approved requests for this user."""
    valkey = get_valkey()
    now = datetime.utcnow()
    old_active = db.query(AccessRequest).filter(
        AccessRequest.user_id == user_id,
        AccessRequest.status == RequestStatus.approved,
        AccessRequest.expires_at > now,
        AccessRequest.id != excluding_request_id,
    ).all()
    for r in old_active:
        valkey.delete(f"sts:{r.id}")
        r.status = RequestStatus.expired


# ─── Models ───────────────────────────────────────────────────────────────────

class SubmitRequestBody(BaseModel):
    email: str
    services: list[str]
    duration_hours: int


class ApprovalVerifyBody(BaseModel):
    token: str          # approval token from email link
    email: str          # manager's email (for OTP)
    code: str           # OTP code
    action: str         # "approve" or "deny"
    denial_reason: Optional[str] = None


class OTPForApprovalBody(BaseModel):
    token: str          # approval token — identifies the request
    email: str          # manager's email


class VerifyAndSubmitBody(BaseModel):
    email: str
    otp_code: str
    services: list[str]
    duration_hours: int


# ─── Submit request ───────────────────────────────────────────────────────────

@router.post("")
async def submit_request(body: SubmitRequestBody, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email, User.active == True).first()  # noqa: E712
    if not user:
        if settings.is_domain_allowed(email):
            # Unknown email with a whitelisted domain — send registration OTP
            code = create_otp(db, email, OTPPurpose.registration)
            await send_otp(email, code, purpose="registration")
            return {"status": "verification_required"}
        raise HTTPException(status_code=404, detail="Email not registered. Contact your admin.")

    # Validate requested services against user's allowlist
    invalid = [s for s in body.services if s not in user.allowed_services]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Services not allowed: {', '.join(invalid)}")

    # Validate duration
    if body.duration_hours < 1 or body.duration_hours > user.max_duration_hours:
        raise HTTPException(
            status_code=400,
            detail=f"Duration must be between 1 and {user.max_duration_hours} hours",
        )

    # Cancel any existing pending requests — new one supersedes them
    db.query(AccessRequest).filter(
        AccessRequest.user_id == user.id,
        AccessRequest.status == RequestStatus.pending,
    ).update({"status": RequestStatus.denied, "denial_reason": "Superseded by a new request"})

    # Determine if auto-approval applies
    should_auto_approve = user.auto_approve

    # Create request
    access_request = AccessRequest(
        user_id=user.id,
        services=body.services,
        duration_hours=body.duration_hours,
    )
    db.add(access_request)
    db.flush()

    if should_auto_approve:
        # Auto-approve: call STS immediately, no manager notification
        try:
            creds = assume_role_for_services(
                services=body.services,
                duration_hours=body.duration_hours,
                session_name=f"dashboard-{user.email.split('@')[0]}-{access_request.id}",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"STS error: {str(e)}")

        _revoke_active_sessions(user.id, access_request.id, db)

        valkey = get_valkey()
        valkey.setex(f"sts:{access_request.id}", body.duration_hours * 3600, json.dumps(creds))

        access_request.status = RequestStatus.approved
        access_request.reviewed_at = datetime.utcnow()
        access_request.reviewed_by_email = "auto-approved"
        access_request.expires_at = datetime.utcnow() + timedelta(hours=body.duration_hours)
        db.commit()

        login_url = f"{settings.frontend_url}/login"
        await send_approval_confirmation(user.email, user.name, body.services, body.duration_hours, login_url)

        return {"success": True, "auto_approved": True, "message": "Access approved. Check your email to log in."}

    # Manual approval: create token and notify reviewer
    token_value = secrets_module.token_urlsafe(32)
    approval_token = ApprovalToken(
        request_id=access_request.id,
        token=token_value,
        expires_at=datetime.utcnow() + timedelta(hours=APPROVAL_TOKEN_EXPIRY_HOURS),
    )
    db.add(approval_token)
    db.commit()

    reviewer_email = user.manager.email if user.manager else settings.admin_email
    approval_link = f"{settings.frontend_url}/approve?token={token_value}"
    await send_manager_notification(
        manager_email=reviewer_email,
        requester_name=user.name,
        requester_email=user.email,
        services=body.services,
        duration_hours=body.duration_hours,
        approval_link=approval_link,
    )

    return {"success": True, "auto_approved": False, "message": "Request submitted. Your manager has been notified."}


# ─── Verify registration OTP and complete request submission ─────────────────

@router.post("/verify")
async def verify_and_submit(body: VerifyAndSubmitBody, db: Session = Depends(get_db)):
    """
    For new users: verify the registration OTP, create the user account,
    and submit their access request to the admin for approval.
    """
    email = body.email.strip().lower()

    if not settings.is_domain_allowed(email):
        raise HTTPException(status_code=403, detail="Email domain not whitelisted for auto-registration")

    if not verify_otp(db, email, body.otp_code, OTPPurpose.registration):
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    # Race condition guard: user may have been created between OTP send and verify
    user = db.query(User).filter(User.email == email, User.active == True).first()  # noqa: E712
    if not user:
        # Derive a display name from the email local part (e.g. "john.doe" → "John Doe")
        local = email.split("@")[0]
        name = " ".join(part.capitalize() for part in local.replace(".", " ").replace("_", " ").split())
        user = User(
            email=email,
            name=name,
            role=UserRole.employee,
            allowed_services=list(ALL_SERVICES),
            max_duration_hours=12,
            auto_approve=False,
            active=True,
        )
        db.add(user)
        db.flush()  # get user.id without committing

    # Validate services and duration
    invalid = [s for s in body.services if s not in user.allowed_services]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Services not allowed: {', '.join(invalid)}")

    if body.duration_hours < 1 or body.duration_hours > user.max_duration_hours:
        raise HTTPException(
            status_code=400,
            detail=f"Duration must be between 1 and {user.max_duration_hours} hours",
        )

    # Cancel any previous pending requests
    db.query(AccessRequest).filter(
        AccessRequest.user_id == user.id,
        AccessRequest.status == RequestStatus.pending,
    ).update({"status": RequestStatus.denied, "denial_reason": "Superseded by a new request"})

    # Create access request
    access_request = AccessRequest(
        user_id=user.id,
        services=body.services,
        duration_hours=body.duration_hours,
    )
    db.add(access_request)
    db.flush()

    # Create approval token for admin
    token_value = secrets_module.token_urlsafe(32)
    approval_token = ApprovalToken(
        request_id=access_request.id,
        token=token_value,
        expires_at=datetime.utcnow() + timedelta(hours=APPROVAL_TOKEN_EXPIRY_HOURS),
    )
    db.add(approval_token)
    db.commit()

    # Notify admin (new users have no manager — always goes to admin)
    # Email failure is non-fatal: request is already committed; admin can find it via admin panel
    admin_url = f"{settings.frontend_url}/"
    try:
        await send_new_user_notification(
            admin_email=settings.admin_email,
            new_user_email=email,
            services=body.services,
            duration_hours=body.duration_hours,
            admin_url=admin_url,
        )
    except Exception as e:
        print(f"Admin notification email failed for new user {email}: {e}")

    return {"success": True, "message": "Request submitted. Admin will review your request."}


# ─── Get request details from approval token (for the approval page) ──────────

@router.get("/approve/{token}")
def get_approval_request(token: str, db: Session = Depends(get_db)):
    approval_token = db.query(ApprovalToken).filter(
        ApprovalToken.token == token,
        ApprovalToken.used == False,  # noqa: E712
        ApprovalToken.expires_at > datetime.utcnow(),
    ).first()
    if not approval_token:
        raise HTTPException(status_code=404, detail="Invalid or expired approval link")

    req = approval_token.request
    if req.status != RequestStatus.pending:
        raise HTTPException(status_code=409, detail=f"Request already {req.status.value}")

    return {
        "request_id": str(req.id),
        "requester_name": req.user.name,
        "requester_email": req.user.email,
        "services": req.services,
        "duration_hours": req.duration_hours,
        "submitted_at": req.created_at.isoformat(),
    }


# ─── Send OTP to manager (first step of approval flow) ────────────────────────

@router.post("/approve/otp")
@limiter.limit("5/10 minute")
async def send_approval_otp(request: Request, body: OTPForApprovalBody, db: Session = Depends(get_db)):
    # Verify the approval token is valid
    approval_token = db.query(ApprovalToken).filter(
        ApprovalToken.token == body.token,
        ApprovalToken.used == False,  # noqa: E712
        ApprovalToken.expires_at > datetime.utcnow(),
    ).first()
    if not approval_token:
        raise HTTPException(status_code=404, detail="Invalid or expired approval link")

    email = body.email.strip().lower()

    # Only the assigned manager OR the admin may approve
    req = approval_token.request
    is_admin = email == settings.admin_email.lower()
    is_manager = req.user.manager and req.user.manager.email.lower() == email

    if not is_admin and not is_manager:
        raise HTTPException(status_code=403, detail="This email is not authorised to approve this request")

    code = create_otp(db, email, OTPPurpose.approval)
    await send_otp(email, code, purpose="approval")
    return {"sent": True}


# ─── Approve or deny (second step — OTP verified) ─────────────────────────────

@router.post("/approve")
async def approve_or_deny(body: ApprovalVerifyBody, db: Session = Depends(get_db)):
    email = body.email.strip().lower()

    if not verify_otp(db, email, body.code, OTPPurpose.approval):
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    approval_token = db.query(ApprovalToken).filter(
        ApprovalToken.token == body.token,
        ApprovalToken.used == False,  # noqa: E712
        ApprovalToken.expires_at > datetime.utcnow(),
    ).first()
    if not approval_token:
        raise HTTPException(status_code=404, detail="Invalid or expired approval link")

    req = approval_token.request
    if req.status != RequestStatus.pending:
        raise HTTPException(status_code=409, detail=f"Request already {req.status.value}")

    if body.action not in ("approve", "deny"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'deny'")

    approval_token.used = True
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by_email = email

    if body.action == "deny":
        req.status = RequestStatus.denied
        req.denial_reason = body.denial_reason or ""
        db.commit()
        await send_denial_notification(
            req.user.email, req.user.name, req.services, req.denial_reason
        )
        return {"success": True, "action": "denied"}

    # Approve — call STS
    try:
        creds = assume_role_for_services(
            services=req.services,
            duration_hours=req.duration_hours,
            session_name=f"dashboard-{req.user.email.split('@')[0]}-{req.id}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"STS error: {str(e)}")

    # Revoke any previously active sessions for this user
    _revoke_active_sessions(req.user_id, req.id, db)

    # Store STS creds in Valkey with TTL = duration
    valkey = get_valkey()
    ttl_seconds = req.duration_hours * 3600
    valkey.setex(f"sts:{req.id}", ttl_seconds, json.dumps(creds))

    req.status = RequestStatus.approved
    req.expires_at = datetime.utcnow() + timedelta(hours=req.duration_hours)
    db.commit()

    login_url = f"{settings.frontend_url}/login"
    await send_approval_confirmation(
        req.user.email, req.user.name, req.services, req.duration_hours, login_url
    )

    return {"success": True, "action": "approved"}


# ─── User's own request status ────────────────────────────────────────────────

@router.get("/my")
def get_my_requests(request: Request, db: Session = Depends(get_db)):
    from ..core.session import session_store, SESSION_COOKIE_NAME
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    config = session_store.get_session_config(session_id) if session_id else None
    if not config or not config.get("email"):
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = db.query(User).filter(User.email == config["email"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    reqs = (
        db.query(AccessRequest)
        .filter(AccessRequest.user_id == user.id)
        .order_by(AccessRequest.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "services": r.services,
            "duration_hours": r.duration_hours,
            "status": r.status.value,
            "created_at": r.created_at.isoformat(),
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
            "denial_reason": r.denial_reason,
        }
        for r in reqs
    ]
