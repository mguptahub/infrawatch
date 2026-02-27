from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Admin — system management identity (no AWS access)
    admin_email: str = "devops@plane.so"

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
    postgres_password: str = "changeme"

    # Valkey
    valkey_url: str = "redis://valkey:6379"

    # App
    secret_key: str = "change-this-to-a-random-secret"
    frontend_url: str = "http://localhost:3000"

    # Power AWS keys — used server-side for STS AssumeRole
    power_aws_access_key_id: Optional[str] = None
    power_aws_secret_access_key: Optional[str] = None
    power_aws_region: str = "us-east-1"
    base_role_arn: Optional[str] = None

    # Auto-registration — comma-separated domains, e.g. "plane.so,contractor.com"
    # Leave empty to disable auto-registration entirely
    allowed_domains: str = ""

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

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
