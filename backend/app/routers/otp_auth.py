import secrets as secrets_module
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from ..core.database import get_db
from ..core.config import settings, AWS_REGIONS
from ..core.limiter import limiter
from ..core.otp_service import create_otp, verify_otp
from ..core.email_service import send_otp
from ..core.session import session_store, SESSION_COOKIE_NAME, SESSION_TIMEOUT_MINUTES
from ..core.valkey_client import get_client as get_valkey
from ..db.models import OTPPurpose, User, AccessRequest, RequestStatus, AccessSession
import json

router = APIRouter(prefix="/api/otp", tags=["OTP Auth"])


class RequestOTPBody(BaseModel):
    email: str


class VerifyOTPBody(BaseModel):
    email: str
    code: str


class SwitchRegionBody(BaseModel):
    region: str


def _is_admin(email: str) -> bool:
    return email.lower() == settings.admin_email.lower()


# ─── Request OTP ──────────────────────────────────────────────────────────────

@router.post("/request")
@limiter.limit("5/10 minute")
async def request_otp(request: Request, body: RequestOTPBody, db: Session = Depends(get_db)):
    email = body.email.strip().lower()

    # Admin always gets OTP
    if _is_admin(email):
        code = create_otp(db, email, OTPPurpose.login)
        await send_otp(email, code, purpose="login")
        return {"sent": True, "type": "admin"}

    # Regular user — must exist and be active
    user = db.query(User).filter(User.email == email, User.active == True).first()  # noqa: E712
    if not user:
        raise HTTPException(status_code=403, detail="not_registered")

    # Check they have an active approved session
    now = datetime.utcnow()
    active_request = (
        db.query(AccessRequest)
        .filter(
            AccessRequest.user_id == user.id,
            AccessRequest.status == RequestStatus.approved,
            AccessRequest.expires_at > now,
        )
        .first()
    )
    if not active_request:
        raise HTTPException(
            status_code=403,
            detail="no_active_access",  # frontend uses this to redirect to request form
        )

    code = create_otp(db, email, OTPPurpose.login)
    await send_otp(email, code, purpose="login")
    return {"sent": True, "type": "user"}


# ─── Verify OTP & create session ──────────────────────────────────────────────

@router.post("/verify")
@limiter.limit("10/minute")
async def verify_otp_and_login(
    request: Request,
    body: VerifyOTPBody,
    response: Response,
    db: Session = Depends(get_db),
):
    email = body.email.strip().lower()
    is_admin = _is_admin(email)

    if not verify_otp(db, email, body.code, OTPPurpose.login):
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    if is_admin:
        # Admin session — no AWS credentials, dashboard access blocked server-side
        session_id = session_store.create_session({
            "mode": "admin",
            "email": email,
            "access_key": None,
            "secret_key": None,
            "session_token": None,
            "region": settings.power_aws_region,
        })
        response.set_cookie(
            key=SESSION_COOKIE_NAME, value=session_id,
            httponly=True, max_age=SESSION_TIMEOUT_MINUTES * 60, samesite="lax", secure=settings.cookie_secure,
        )
        return {"success": True, "role": "admin", "email": email}

    # Regular user — look up active approved request and STS creds from Valkey
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    now = datetime.utcnow()
    active_request = (
        db.query(AccessRequest)
        .filter(
            AccessRequest.user_id == user.id,
            AccessRequest.status == RequestStatus.approved,
            AccessRequest.expires_at > now,
        )
        .order_by(AccessRequest.expires_at.desc())
        .first()
    )
    if not active_request:
        raise HTTPException(status_code=403, detail="no_active_access")

    # Get STS credentials from Valkey
    valkey = get_valkey()
    creds_key = f"sts:{active_request.id}"
    creds_raw = valkey.get(creds_key)
    if not creds_raw:
        raise HTTPException(status_code=403, detail="Credentials expired. Please request access again.")

    creds = json.loads(creds_raw)
    session_id = session_store.create_session({
        "mode": "keys",
        "email": email,
        "name": user.name,
        "services": active_request.services,
        "request_id": str(active_request.id),
        "access_key": creds["access_key"],
        "secret_key": creds["secret_key"],
        "session_token": creds["session_token"],
        "region": creds["region"],
    })

    response.set_cookie(
        key=SESSION_COOKIE_NAME, value=session_id,
        httponly=True, max_age=SESSION_TIMEOUT_MINUTES * 60, samesite="lax", secure=settings.cookie_secure,
    )

    return {
        "success": True,
        "authenticated": True,
        "role": "keys",
        "email": email,
        "name": user.name,
        "services": active_request.services,
        "region": creds["region"],
    }


# ─── Session info ─────────────────────────────────────────────────────────────

@router.get("/me")
def get_me(request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    config = session_store.get_session_config(session_id)
    if not config:
        raise HTTPException(status_code=401, detail="Session expired")

    return {
        "authenticated": True,
        "role": config.get("mode"),
        "email": config.get("email"),
        "name": config.get("name"),
        "services": config.get("services", []),
        "region": config.get("region"),
    }


# ─── Terminate session (destroys STS creds, needs new approval) ───────────────

@router.post("/terminate")
def terminate_session(request: Request, response: Response, db: Session = Depends(get_db)):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    config = session_store.get_session_config(session_id)
    if config and config.get("mode") == "keys":
        request_id = config.get("request_id")
        if request_id:
            valkey = get_valkey()
            valkey.delete(f"sts:{request_id}")
            req = db.query(AccessRequest).filter(AccessRequest.id == request_id).first()
            if req and req.status == RequestStatus.approved:
                req.status = RequestStatus.expired
                db.commit()

    session_store.delete_session(session_id)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"success": True}


# ─── Switch region (updates session, all subsequent API calls use new region) ─

@router.put("/region")
def switch_region(body: SwitchRegionBody, request: Request):
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    config = session_store.get_session_config(session_id)
    if not config:
        raise HTTPException(status_code=401, detail="Session expired")

    region = body.region.strip().lower()
    if region not in AWS_REGIONS:
        raise HTTPException(status_code=400, detail=f"Invalid AWS region: {region}")

    config["region"] = region
    session_store.update_session(session_id, config)
    return {"region": region}
