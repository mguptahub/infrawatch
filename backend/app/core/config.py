from pydantic_settings import BaseSettings
from typing import Optional

# Ordered list — single source of truth for both validation and the /api/regions endpoint.
# Each entry is (value, human-readable label).
AWS_REGIONS_LIST = [
    # US
    ("us-east-1",      "US East (N. Virginia)"),
    ("us-east-2",      "US East (Ohio)"),
    ("us-west-1",      "US West (N. California)"),
    ("us-west-2",      "US West (Oregon)"),
    # Canada
    ("ca-central-1",   "Canada (Central)"),
    ("ca-west-1",      "Canada (West)"),
    # Europe
    ("eu-west-1",      "EU (Ireland)"),
    ("eu-west-2",      "EU (London)"),
    ("eu-west-3",      "EU (Paris)"),
    ("eu-central-1",   "EU (Frankfurt)"),
    ("eu-central-2",   "EU (Zurich)"),
    ("eu-north-1",     "EU (Stockholm)"),
    ("eu-south-1",     "EU (Milan)"),
    ("eu-south-2",     "EU (Spain)"),
    # Asia Pacific
    ("ap-east-1",      "AP (Hong Kong)"),
    ("ap-south-1",     "AP (Mumbai)"),
    ("ap-south-2",     "AP (Hyderabad)"),
    ("ap-southeast-1", "AP (Singapore)"),
    ("ap-southeast-2", "AP (Sydney)"),
    ("ap-southeast-3", "AP (Jakarta)"),
    ("ap-southeast-4", "AP (Melbourne)"),
    ("ap-northeast-1", "AP (Tokyo)"),
    ("ap-northeast-2", "AP (Seoul)"),
    ("ap-northeast-3", "AP (Osaka)"),
    # South America
    ("sa-east-1",      "SA (São Paulo)"),
    # Middle East
    ("me-central-1",   "ME (UAE)"),
    ("me-south-1",     "ME (Bahrain)"),
    # Africa
    ("af-south-1",     "Africa (Cape Town)"),
    # Israel
    ("il-central-1",   "Israel (Tel Aviv)"),
]

# Set derived from the list above — used for O(1) validation.
AWS_REGIONS = {value for value, _ in AWS_REGIONS_LIST}


class Settings(BaseSettings):
    # Admin — system management identity (no AWS access)
    admin_email: str = "admin@example.com"

    # SMTP
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@example.com"
    smtp_tls: bool = True

    # Postgres
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "awsdashboard"
    postgres_user: str = "awsdashboard"
    postgres_password: str = ""

    # Valkey
    valkey_url: str = "redis://valkey:6379"  # Include password: redis://:password@valkey:6379

    # App
    frontend_url: str = "http://localhost:3000"
    cookie_secure: bool = False  # Set to True in production (requires HTTPS)
    cors_origins: str = "http://localhost:3000"  # Comma-separated list of allowed origins
    otp_expiry_minutes: int = 10
    otp_max_attempts: int = 5

    # Power AWS keys — used server-side for STS AssumeRole
    power_aws_access_key_id: Optional[str] = None
    power_aws_secret_access_key: Optional[str] = None
    power_aws_region: str = "us-east-1"
    base_role_arn: Optional[str] = None

    # Auto-registration — comma-separated domains, e.g. "plane.so,contractor.com"
    # Leave empty to disable auto-registration entirely
    allowed_domains: str = ""

    # Refresh stream (SSE) — timeout in seconds before sending timeout event to client
    refresh_stream_timeout_seconds: int = 300

    # SES suppression search & remove — single source of truth for limits (used in router and API response)
    ses_suppression_search_limit: int = 20
    ses_bulk_remove_max: int = 20
    ses_min_search_chars: int = 3

    collect_resource_all_secs: int = 6 * 60 * 60.0
    collect_metrics_every_secs: int = 5 * 60.0
    metrics_retention_secs: int = 60 * 60.0
    collect_alarms_every_secs: int = 5 * 60.0
    collect_health_events_every_secs: int = 15 * 60.0

    # Collector — comma-separated regions to collect (EC2, EKS, LB, metrics). Empty = all regions from AWS_REGIONS_LIST.
    # Limit to regions you use to reduce API calls and avoid opt-in region errors.
    collector_regions: str = ""

    # HMAC handshake — set by the proxy binary. Empty = open mode (no HMAC check).
    infrawatch_hmac_secret: str = ""
    hmac_secret_file: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    def is_domain_allowed(self, email: str) -> bool:
        """Return True if the email's domain is in the ALLOWED_DOMAINS whitelist."""
        if not self.allowed_domains.strip():
            return False
        parts = email.split("@")
        if len(parts) != 2 or not parts[1]:
            return False
        domain = parts[1].lower()
        allowed = {d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()}
        return domain in allowed

    @property
    def collector_regions_list(self) -> list:
        """Regions the Celery collector runs in. Empty COLLECTOR_REGIONS = all regions."""
        if not self.collector_regions.strip():
            return [r[0] for r in AWS_REGIONS_LIST]
        parts = [p.strip().lower() for p in self.collector_regions.split(",") if p.strip()]
        return [p for p in parts if p in AWS_REGIONS]

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
