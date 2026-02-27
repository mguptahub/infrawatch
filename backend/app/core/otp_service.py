import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..db.models import OTPCode, OTPPurpose

OTP_EXPIRY_MINUTES = 10


def generate_otp() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(6))


def create_otp(db: Session, email: str, purpose: OTPPurpose) -> str:
    # Invalidate any existing unused OTPs for this email+purpose
    db.query(OTPCode).filter(
        OTPCode.email == email,
        OTPCode.purpose == purpose,
        OTPCode.used == False,  # noqa: E712
    ).update({"used": True})

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
    otp = db.query(OTPCode).filter(
        OTPCode.email == email,
        OTPCode.code == code,
        OTPCode.purpose == purpose,
        OTPCode.used == False,  # noqa: E712
        OTPCode.expires_at > datetime.utcnow(),
    ).first()

    if not otp:
        return False

    otp.used = True
    db.commit()
    return True
