import boto3
from fastapi import HTTPException, Request
from ..models.auth import AuthConfig
from .session import session_store, SESSION_COOKIE_NAME


def get_session(config: AuthConfig) -> boto3.Session:
    try:
        return boto3.Session(
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            aws_session_token=config.session_token or None,
            region_name=config.region
        )
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth failed: {str(e)}")


def get_session_and_config(request: Request) -> tuple[boto3.Session, AuthConfig]:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    config_dict = session_store.get_session_config(session_id)
    if not config_dict:
        raise HTTPException(status_code=401, detail="Session expired")

    config = AuthConfig(**config_dict)
    return get_session(config), config


def get_current_session(request: Request) -> boto3.Session:
    session, _ = get_session_and_config(request)
    return session
