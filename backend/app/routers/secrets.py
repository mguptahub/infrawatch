import json
from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from datetime import datetime
from ..core.aws import get_session_and_config, get_current_session
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/secrets", tags=["Secrets"])


def _fetch_secrets(session):
    sm = session.client("secretsmanager")
    paginator = sm.get_paginator("list_secrets")
    secrets = []

    for page in paginator.paginate():
        for s in page.get("SecretList", []):
            last_accessed = s.get("LastAccessedDate")
            last_changed = s.get("LastChangedDate")
            last_rotated = s.get("LastRotatedDate")

            rotation_enabled = s.get("RotationEnabled", False)
            rotation_days = None
            if rotation_enabled and s.get("RotationRules"):
                rotation_days = s["RotationRules"].get("AutomaticallyAfterDays")

            age_days = None
            ref_date = last_rotated or last_changed or s.get("CreatedDate")
            if ref_date:
                age_days = (datetime.utcnow().replace(tzinfo=ref_date.tzinfo) - ref_date).days

            secrets.append({
                "name": s["Name"],
                "arn": s["ARN"],
                "description": s.get("Description", ""),
                "rotation_enabled": rotation_enabled,
                "rotation_days": rotation_days,
                "age_days": age_days,
                "last_accessed": last_accessed.isoformat() if last_accessed else None,
                "last_changed": last_changed.isoformat() if last_changed else None,
                "last_rotated": last_rotated.isoformat() if last_rotated else None,
                "kms_key": s.get("KmsKeyId", "Default"),
                "tags": {t["Key"]: t["Value"] for t in s.get("Tags", [])},
                "stale": (not rotation_enabled and age_days is not None and age_days > 90),
            })

    secrets.sort(key=lambda x: (not x["stale"], x["name"]))
    stale_count = sum(1 for s in secrets if s["stale"])
    return {"secrets": secrets, "count": len(secrets), "stale_count": stale_count}


@router.get("/value")
def get_secret_value(request: Request, arn: str):
    """Fetch the actual secret string/JSON for a given secret ARN. Never cached."""
    session = get_current_session(request)
    sm = session.client("secretsmanager")
    try:
        resp = sm.get_secret_value(SecretId=arn)
        secret_string = resp.get("SecretString")
        if secret_string is None:
            return {"type": "binary", "value": None}
        try:
            parsed = json.loads(secret_string)
            return {"type": "json", "value": parsed}
        except (json.JSONDecodeError, ValueError):
            return {"type": "string", "value": secret_string}
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def get_secrets(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("secrets", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_secrets(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
