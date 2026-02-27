import secrets
from datetime import datetime, timedelta
from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..db.models import OTPCode, OTPPurpose
from .valkey_client import get_client as get_valkey

OTP_EXPIRY_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def _fail_key(email: str, purpose: OTPPurpose) -> str:
    return f"otp_fails:{email}:{purpose.value}"


def generate_otp() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


def create_otp(db: Session, email: str, purpose: OTPPurpose) -> str:
    # Invalidate any existing unused OTPs for this email+purpose
    db.query(OTPCode).filter(
        OTPCode.email == email,
        OTPCode.purpose == purpose,
        OTPCode.used == False,  # noqa: E712
    ).update({"used": True})

    # Reset the failed-attempt counter when a fresh OTP is issued
    get_valkey().delete(_fail_key(email, purpose))

    code = generate_otp()
    otp = OTPCode(
        email=email,
        code=code,
        purpose=purpose,
        expires_at=datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES),
    )
    db.add(otp)
    db.commit()
    return code


def verify_otp(db: Session, email: str, code: str, purpose: OTPPurpose) -> bool:
    valkey = get_valkey()
    fail_key = _fail_key(email, purpose)

    # Reject immediately if already locked out
    attempts = valkey.get(fail_key)
    if attempts and int(attempts) >= OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many incorrect attempts. Request a new code.",
        )

    otp = db.query(OTPCode).filter(
        OTPCode.email == email,
        OTPCode.code == code,
        OTPCode.purpose == purpose,
        OTPCode.used == False,  # noqa: E712
        OTPCode.expires_at > datetime.utcnow(),
    ).first()

    if not otp:
        # Increment failure counter, expire it with the OTP window
        new_count = valkey.incr(fail_key)
        valkey.expire(fail_key, OTP_EXPIRY_MINUTES * 60)
        if new_count >= OTP_MAX_ATTEMPTS:
            # Invalidate all active OTPs for this email+purpose
            db.query(OTPCode).filter(
                OTPCode.email == email,
                OTPCode.purpose == purpose,
                OTPCode.used == False,  # noqa: E712
            ).update({"used": True})
            db.commit()
            raise HTTPException(
                status_code=429,
                detail="Too many incorrect attempts. Request a new code.",
            )
        return False

    otp.used = True
    db.commit()
    valkey.delete(fail_key)  # Clean up counter on success
    return True
