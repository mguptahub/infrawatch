from pydantic import BaseModel
from typing import Optional

class AuthConfig(BaseModel):
    mode: str = "keys"
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    session_token: Optional[str] = None   # required for MFA / assumed-role credentials
    region: str = "us-east-1"
