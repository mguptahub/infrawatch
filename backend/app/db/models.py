import uuid
import enum
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Boolean, DateTime, Float,
    ForeignKey, JSON, Text, Enum as SAEnum, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from ..core.database import Base


class UserRole(str, enum.Enum):
    employee = "employee"
    manager = "manager"


class RequestStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"
    expired = "expired"


class OTPPurpose(str, enum.Enum):
    login = "login"
    approval = "approval"
    registration = "registration"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    role = Column(SAEnum(UserRole), nullable=False, default=UserRole.employee)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    allowed_services = Column(JSON, nullable=False, default=list)
    max_duration_hours = Column(Integer, nullable=False, default=1)
    auto_approve = Column(Boolean, nullable=False, default=False)
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    manager = relationship("User", remote_side="User.id", foreign_keys=[manager_id])
    requests = relationship("AccessRequest", back_populates="user", foreign_keys="AccessRequest.user_id")


class AccessRequest(Base):
    __tablename__ = "requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    services = Column(JSON, nullable=False)
    duration_hours = Column(Integer, nullable=False)
    status = Column(SAEnum(RequestStatus), nullable=False, default=RequestStatus.pending)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
    reviewed_by_email = Column(String, nullable=True)   # stores reviewer email (manager or admin)
    denial_reason = Column(Text, nullable=True)
    expires_at = Column(DateTime, nullable=True)        # set on approval: now + duration_hours

    user = relationship("User", back_populates="requests", foreign_keys=[user_id])
    approval_tokens = relationship("ApprovalToken", back_populates="request")
    access_sessions = relationship("AccessSession", back_populates="request")


class ApprovalToken(Base):
    """One-time token embedded in the manager notification email link."""
    __tablename__ = "approval_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("requests.id"), nullable=False)
    token = Column(String, unique=True, nullable=False, index=True)
    used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    request = relationship("AccessRequest", back_populates="approval_tokens")


class OTPCode(Base):
    """6-digit OTP — used for login and manager approval verification."""
    __tablename__ = "otp_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, nullable=False, index=True)
    code = Column(String(6), nullable=False)
    purpose = Column(String(20), nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class AccessSession(Base):
    """Tracks active access sessions — STS creds live in Valkey, keyed by session_token."""
    __tablename__ = "access_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("requests.id"), nullable=False)
    session_token = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

    request = relationship("AccessRequest", back_populates="access_sessions")


# ─── Collector storage (Phase 1: Celery populates these) ───────────────────────

class CollectedResource(Base):
    """
    Latest snapshot of an AWS resource. Celery overwrites on each collect.
    Keyed by (service_type, region, resource_id).
    """
    __tablename__ = "collected_resources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_type = Column(String(32), nullable=False, index=True)   # ec2, eks, databases, elb, etc.
    region = Column(String(32), nullable=False, index=True)
    account_id = Column(String(32), nullable=True)
    resource_id = Column(String(256), nullable=False, index=True)
    name = Column(String(512), nullable=True)
    attributes = Column(JSON, nullable=False, default=dict)         # service-specific payload
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("service_type", "region", "resource_id", name="uq_collected_resource"),
    )


class CollectedMetric(Base):
    """
    Time-series of CloudWatch metrics. Celery inserts; retention (e.g. 72h) applied by task.
    Incremental pull: fetch only from last stored timestamp to now (max 72h).
    """
    __tablename__ = "collected_metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_type = Column(String(32), nullable=False, index=True)
    resource_id = Column(String(256), nullable=False, index=True)
    region = Column(String(32), nullable=False, index=True)
    metric_name = Column(String(128), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    value = Column(Float, nullable=False)
    unit = Column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "service_type", "resource_id", "region", "metric_name", "timestamp",
            name="uq_collected_metrics_series_ts",
        ),
    )


class DashboardPanel(Base):
    """Collapsible section container for dashboard widgets."""
    __tablename__ = "dashboard_panels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_email = Column(String(256), nullable=False, index=True)
    title = Column(String(256), nullable=False)
    collapsed = Column(Boolean, nullable=False, default=False)
    sort_order = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class DashboardWidget(Base):
    """Per-user dashboard widget. Keyed by user email; layout and metrics config."""
    __tablename__ = "dashboard_widgets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_email = Column(String(256), nullable=False, index=True)
    service_type = Column(String(32), nullable=False)
    resource_id = Column(String(256), nullable=False)
    region = Column(String(32), nullable=False)
    title = Column(String(256), nullable=True)
    widget_type = Column(String(32), nullable=False, default="line")
    metric_names = Column(JSON, nullable=False)
    layout_row = Column(Integer, nullable=False, default=0)
    layout_col = Column(Integer, nullable=False, default=0)
    panel_id = Column(UUID(as_uuid=True), ForeignKey("dashboard_panels.id", ondelete="CASCADE"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CostMonthly(Base):
    """
    Stored cost per month per account. Past months are immutable; use for summary
    so we don't re-fetch from AWS on every refresh. Keyed by (account_id, year, month).
    """
    __tablename__ = "cost_monthly"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(String(32), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    month = Column(Integer, nullable=False)
    total = Column(Float, nullable=False)
    by_service = Column(JSON, nullable=False, default=dict)  # {"Service Name": cost, ...}
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("account_id", "year", "month", name="uq_cost_monthly"),
    )


class CollectedAlarm(Base):
    """CloudWatch alarm snapshot. Celery overwrites on each collect."""
    __tablename__ = "collected_alarms"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alarm_name = Column(String(256), nullable=False)
    alarm_arn = Column(String(512), nullable=False, unique=True)
    service_type = Column(String(32), nullable=True, index=True)
    resource_id = Column(String(256), nullable=True, index=True)
    region = Column(String(32), nullable=False, index=True)
    state = Column(String(32), nullable=False)
    state_reason = Column(Text, nullable=True)
    state_updated_at = Column(DateTime, nullable=True)
    metric_name = Column(String(128), nullable=True)
    namespace = Column(String(128), nullable=True)
    dimensions = Column(JSON, nullable=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class CollectedHealthEvent(Base):
    """AWS Health event snapshot. Celery overwrites on each collect."""
    __tablename__ = "collected_health_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_arn = Column(String(512), nullable=False, unique=True)
    service = Column(String(64), nullable=True, index=True)
    service_type = Column(String(32), nullable=True, index=True)
    region = Column(String(32), nullable=True, index=True)
    event_type = Column(String(64), nullable=True)
    category = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False)
    title = Column(String(512), nullable=True)
    description = Column(Text, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    last_updated = Column(DateTime, nullable=True)
    collected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
