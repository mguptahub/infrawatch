import csv
import io
import time
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from botocore.exceptions import ClientError
from sqlalchemy.orm import Session

from ..core.aws import get_current_session, get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL
from ..core.sse_refresh import stream_refresh_done
from ..core.database import get_db
from ..db.models import CollectedResource

router = APIRouter(prefix="/api/iam", tags=["IAM"])
USE_COLLECTOR_DB = True
IAM_REGION = "global"


def _to_iso(dt):
    return dt.isoformat() if dt else None


def _credential_report_console_map(iam):
    """Return {username: bool} from IAM credential report, or None if unavailable."""
    try:
        state = iam.generate_credential_report().get("State")
        if state == "STARTED":
            # Credential report generation is async; poll briefly.
            for _ in range(5):
                time.sleep(0.3)
                state = iam.generate_credential_report().get("State")
                if state != "STARTED":
                    break

        report = iam.get_credential_report()
        content = report.get("Content", b"")
        text = content.decode("utf-8") if isinstance(content, (bytes, bytearray)) else str(content)

        mapping = {}
        for row in csv.DictReader(io.StringIO(text)):
            username = row.get("user")
            if not username or username in ("<root_account>", "root_account"):
                continue
            enabled = (row.get("password_enabled") or "").strip().lower()
            if enabled == "true":
                mapping[username] = True
            elif enabled == "false":
                mapping[username] = False
            else:
                mapping[username] = None
        return mapping
    except Exception:
        return None


def _fetch_users(session):
    iam = session.client("iam")
    paginator = iam.get_paginator("list_users")
    users = []
    console_map = _credential_report_console_map(iam)

    for page in paginator.paginate():
        for u in page.get("Users", []):
            username = u.get("UserName")
            password_last_used = _to_iso(u.get("PasswordLastUsed"))
            console_access = console_map.get(username) if console_map is not None else None

            # Fallback when credential report is unavailable or missing this user.
            if console_access is None:
                try:
                    if username:
                        iam.get_login_profile(UserName=username)
                        console_access = True
                except ClientError as e:
                    code = e.response.get("Error", {}).get("Code")
                    if code == "NoSuchEntity":
                        console_access = False
                    else:
                        console_access = None

            # Additional inference: if AWS reports password was used,
            # this user has had console password access.
            if console_access is None and password_last_used:
                console_access = True

            users.append({
                "username": username,
                "user_id": u.get("UserId"),
                "arn": u.get("Arn"),
                "path": u.get("Path", "/"),
                "created_at": _to_iso(u.get("CreateDate")),
                "password_last_used": password_last_used,
                "console_access": console_access,
            })

    users.sort(key=lambda x: (x.get("username") or "").lower())
    return {"users": users, "count": len(users)}


def _fetch_user_detail(session, username: str):
    iam = session.client("iam")
    user = iam.get_user(UserName=username)["User"]

    groups_resp = iam.list_groups_for_user(UserName=username)
    attached_resp = iam.list_attached_user_policies(UserName=username)
    inline_resp = iam.list_user_policies(UserName=username)
    mfa_resp = iam.list_mfa_devices(UserName=username)
    keys_resp = iam.list_access_keys(UserName=username)

    access_keys = []
    for k in keys_resp.get("AccessKeyMetadata", []):
        key_id = k.get("AccessKeyId")
        last_used = None
        if key_id:
            try:
                used = iam.get_access_key_last_used(AccessKeyId=key_id).get("AccessKeyLastUsed", {})
                last_used = {
                    "date": _to_iso(used.get("LastUsedDate")),
                    "service": used.get("ServiceName"),
                    "region": used.get("Region"),
                }
            except ClientError:
                last_used = None
        access_keys.append({
            "id": key_id,
            "status": k.get("Status"),
            "created_at": _to_iso(k.get("CreateDate")),
            "last_used": last_used,
        })

    group_details = [
        {"name": g.get("GroupName"), "arn": g.get("Arn")}
        for g in groups_resp.get("Groups", [])
    ]

    return {
        "user": {
            "username": user.get("UserName"),
            "user_id": user.get("UserId"),
            "arn": user.get("Arn"),
            "path": user.get("Path", "/"),
            "created_at": _to_iso(user.get("CreateDate")),
            "password_last_used": _to_iso(user.get("PasswordLastUsed")),
            "permissions_boundary_arn": (user.get("PermissionsBoundary") or {}).get("PermissionsBoundaryArn"),
        },
        "groups": group_details,
        "attached_policies": [
            {"name": p.get("PolicyName"), "arn": p.get("PolicyArn")}
            for p in attached_resp.get("AttachedPolicies", [])
        ],
        "inline_policies": inline_resp.get("PolicyNames", []),
        "mfa_devices": [
            {
                "serial_number": m.get("SerialNumber"),
                "enabled_at": _to_iso(m.get("EnableDate")),
            }
            for m in mfa_resp.get("MFADevices", [])
        ],
        "access_keys": access_keys,
    }


def _list_iam_from_db(db: Session):
    """Return { users, count } from collected_resources (IAM is stored under region global)."""
    rows = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "iam",
            CollectedResource.region == IAM_REGION,
        )
        .all()
    )
    users = []
    for r in rows:
        att = r.attributes or {}
        list_item = att.get("list")
        if list_item:
            users.append(list_item)
    users.sort(key=lambda x: (x.get("username") or "").lower())
    return {"users": users, "count": len(users)}


def _detail_iam_from_db(username: str, db: Session):
    """Return user detail dict from collected_resources or None."""
    r = (
        db.query(CollectedResource)
        .filter(
            CollectedResource.service_type == "iam",
            CollectedResource.region == IAM_REGION,
            CollectedResource.resource_id == username,
        )
        .first()
    )
    if not r:
        return None
    att = r.attributes or {}
    return att.get("detail")


@router.get("/users")
def get_iam_users(request: Request, force: bool = False, db: Session = Depends(get_db)):
    if USE_COLLECTOR_DB:
        return _list_iam_from_db(db)
    session, config = get_session_and_config(request)
    key = make_cache_key("iam-users", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_users(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/refresh/stream")
def get_iam_refresh_stream(request: Request):
    """SSE stream: emits refresh_done when the IAM collector (global) finishes."""
    _, config = get_session_and_config(request)
    channel = f"refresh:iam:{IAM_REGION}"
    return StreamingResponse(
        stream_refresh_done(channel),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.post("/refresh")
def post_iam_refresh(request: Request):
    from app.tasks.collect_tasks import collect_resources
    collect_resources.delay("iam", IAM_REGION)
    return {"ok": True, "message": "Refresh started for IAM (global)"}


@router.get("/users/{username}")
def get_iam_user_detail(request: Request, username: str, db: Session = Depends(get_db)):
    if USE_COLLECTOR_DB:
        detail = _detail_iam_from_db(username, db)
        if not detail:
            raise HTTPException(status_code=404, detail="User not found")
        return detail
    session = get_current_session(request)
    try:
        return _fetch_user_detail(session, username)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
