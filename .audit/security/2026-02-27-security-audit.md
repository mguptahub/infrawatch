# Security Audit Report — AWS Dashboard

**Date:** 2026-02-27
**Auditor:** Claude Sonnet 4.6
**Scope:** Full codebase — backend (FastAPI), frontend (React), Docker infrastructure
**Status legend:** `[ ]` Open · `[~]` In Progress · `[x]` Fixed

---

## Summary

| Severity | Count | Fixed |
|----------|-------|-------|
| Critical | 4 | 3 |
| High | 4 | 0 |
| Medium | 7 | 0 |
| Low | 2 | 0 |
| **Total** | **17** | **3** |

---

## Critical

---

### C1 — OTP uses non-cryptographic RNG
- **Status:** `[x]` Fixed
- **File:** `backend/app/core/otp_service.py:11`
- **Risk:** `random.choices` is seeded from system time, not a CSPRNG. An attacker who observes email delivery timing can narrow the seed space and predict codes.

**Current code:**
```python
def generate_otp() -> str:
    return "".join(random.choices(string.digits, k=6))
```

**Fix:**
```python
import secrets
def generate_otp() -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(6))
```

---

### C2 — Legacy auth router bypasses entire OTP/approval flow
- **Status:** `[x]` Fixed
- **Files:** `backend/app/routers/auth.py`, `backend/app/main.py:47`
- **Risk:** `POST /api/auth/verify` accepts raw AWS `access_key` + `secret_key` directly in the request body and creates a valid session. Any user who knows AWS credentials can log in without OTP verification, manager approval, or any access control check. Completely nullifies the Phase 2 auth system.

**Fix:** Remove `auth.router` from `main.py` (or require an active OTP session before the legacy endpoint will work) once the Phase 2 transition is complete.

---

### C3 — Approval `action` defaults to "approve" for any non-"deny" value
- **Status:** `[x]` Fixed
- **File:** `backend/app/routers/requests_router.py:331–366`
- **Risk:** The deny check is an early return; anything that isn't exactly `"deny"` falls through to the STS AssumeRole approval path. Sending `action: "APPROVE"`, `action: ""`, or any other value approves the request.

**Current code:**
```python
if body.action == "deny":
    ...
    return {"success": True, "action": "denied"}

# Approve — call STS  ← reached for ANY action that isn't "deny"
```

**Fix:** Add an explicit guard before the if-block:
```python
if body.action not in ("approve", "deny"):
    raise HTTPException(status_code=400, detail="action must be 'approve' or 'deny'")
```

---

### C4 — No rate limiting on OTP endpoints
- **Status:** `[ ]` Open
- **Files:** `backend/app/routers/otp_auth.py:37`, `backend/app/routers/requests_router.py:280`
- **Risk:** Unlimited unauthenticated requests to OTP endpoints enables two attacks:
  1. **Email flooding** — carpet-bomb any known email address with OTP codes
  2. **Brute-force** — 6 digits = 1,000,000 combinations; within a 10-minute window an automated client can try all combinations with no lockout

**Fix:** Add per-IP + per-email rate limiting via `slowapi` (or similar). Suggested limits:
- OTP request: 5 requests per email per 10 minutes
- OTP verify: 5 attempts per email per OTP (then invalidate the code)

---

## High

---

### H1 — `secure=False` hardcoded in session cookies
- **Status:** `[ ]` Open
- **Files:** `backend/app/routers/otp_auth.py:100,145`, `backend/app/routers/auth.py:23`, `backend/app/main.py:41`
- **Risk:** All `set_cookie` calls hardcode `secure=False`. If deployed behind HTTPS, cookies are still not flagged secure, allowing potential transmission over HTTP on redirects.

**Fix:** Add `cookie_secure: bool = False` to `Settings`, set to `True` in production, and use `secure=settings.cookie_secure` in all `set_cookie` calls.

---

### H2 — Backend port 8000 publicly exposed
- **Status:** `[ ]` Open
- **File:** `docker-compose.yml:34–35`
- **Risk:** The FastAPI backend is directly reachable on the host machine, bypassing nginx entirely. All API endpoints are accessible without going through the reverse proxy, including the legacy auth endpoint.

**Current config:**
```yaml
ports:
  - "8000:8000"
```

**Fix:** Remove the `ports` block from the backend service. The backend should only be accessible within `monitor-net`. Nginx already proxies `/api/*` to it.

---

### H3 — HTML injection in email templates
- **Status:** `[ ]` Open
- **File:** `backend/app/core/email_service.py:71,104,127–128`
- **Risk:** User-controlled values are interpolated raw into HTML email bodies. Only `send_new_user_notification` correctly uses `html.escape()`. The others don't:
  - `send_manager_notification:71` — `requester_name` (from DB, originally user-supplied)
  - `send_approval_confirmation:104` — `name`
  - `send_denial_notification:127–128` — `name` and `reason` (free text from manager)

A malicious name or denial reason can inject arbitrary HTML into the rendered email.

**Fix:** Apply `from html import escape` and wrap all user-controlled fields in `escape()` in all email template functions, matching the pattern already used in `send_new_user_notification`.

---

### H4 — No OTP attempt lockout
- **Status:** `[ ]` Open
- **File:** `backend/app/core/otp_service.py:34–48`
- **Risk:** `verify_otp` has no failed-attempt tracking. A valid OTP is only marked used on success. An attacker with a target's email can make unlimited guesses within the 10-minute window.

**Fix:** Store a failed-attempt counter in Valkey (`otp_fails:{email}:{purpose}`). After 5 wrong guesses, mark all active OTPs for that email+purpose as used and return 429.

---

## Medium

---

### M1 — CORS origin hardcoded to `localhost:3000`
- **Status:** `[ ]` Open
- **File:** `backend/app/main.py:20`
- **Risk:** Not configurable via environment variable. In production with a real domain this either silently blocks requests, or someone changes it to `["*"]` as a quick fix.

**Fix:**
```python
# config.py
cors_origins: str = "http://localhost:3000"

# main.py
allow_origins=settings.cors_origins.split(","),
```

---

### M2 — `update_user` allows assigning any user as manager
- **Status:** `[ ]` Open
- **File:** `backend/app/routers/admin.py:151–156`
- **Risk:** `create_user` validates `User.role == UserRole.manager`, but the `manager_email` field in `update_user` does not — it accepts any active user regardless of role. This allows an employee to be set as another employee's manager, giving them the ability to approve that employee's AWS access requests through the email approval flow.

**Fix:** Mirror the `create_user` validation in `update_user`:
```python
manager = db.query(User).filter(
    User.email == body.manager_email.strip().lower(),
    User.role == UserRole.manager,  # ← add this
    User.active == True,
).first()
```

---

### M3 — Auto-registered users get all services with 12h max duration
- **Status:** `[ ]` Open
- **File:** `backend/app/routers/requests_router.py:191–194`
- **Risk:** New users who self-register via the domain whitelist are granted `ALL_SERVICES` and `max_duration_hours=12` by default. While admin approval is still required, this is maximally permissive by default.

**Fix:** Start with an empty allowlist:
```python
allowed_services=[],
max_duration_hours=1,
```
Admin explicitly grants services after reviewing the new user.

---

### M4 — STS error details leaked to callers
- **Status:** `[ ]` Open
- **Files:** `backend/app/routers/requests_router.py:123,348`, `backend/app/routers/admin.py:214`
- **Risk:** Raw `boto3` exception messages (which may contain the role ARN, account ID, and permission details) are returned directly to the client.

**Fix:** Log the full error server-side, return a generic message:
```python
except Exception as e:
    logger.error("STS AssumeRole failed: %s", e)
    raise HTTPException(status_code=500, detail="Failed to issue AWS credentials. Contact admin.")
```

---

### M5 — `--reload` in production Docker command
- **Status:** `[ ]` Open
- **File:** `docker-compose.yml:36`
- **Risk:** `--reload` enables file-watching and hot-reload, intended for development only. Should not be present in a production deployment.

**Fix:** Remove `--reload` from the uvicorn command, or move it to a `docker-compose.override.yml` for local dev only.

---

### M6 — Default Postgres password falls back to `changeme`
- **Status:** `[ ]` Open
- **File:** `docker-compose.yml:11`
- **Risk:** If `.env` is missing or `POSTGRES_PASSWORD` is unset, the database starts with the trivial password `changeme`.

**Fix:** Remove the default fallback so a missing variable causes startup to fail visibly:
```yaml
POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}
```

---

### M7 — Valkey has no authentication
- **Status:** `[ ]` Open
- **File:** `docker-compose.yml:22–26`
- **Risk:** Valkey is unauthenticated. All session tokens and STS credentials (`access_key`, `secret_key`, `session_token`) stored in it are readable by anyone who can reach the container on the Docker network.

**Fix:** Add `--requirepass $VALKEY_PASSWORD` to the Valkey command and update `VALKEY_URL` to `redis://:$VALKEY_PASSWORD@valkey:6379`.

---

## Low

---

### L1 — `SECRET_KEY` is loaded but never used
- **Status:** `[ ]` Open
- **File:** `backend/app/core/config.py:28`
- **Risk:** `secret_key` is defined in `Settings` but never referenced. This implies HMAC-signed sessions but sessions are just opaque random IDs stored in Valkey. Misleading to future maintainers.

**Fix:** Either wire it in (e.g. HMAC-sign the session cookie value to prevent forgery even if Valkey is bypassed), or remove it from `Settings`.

---

### L2 — Region value not validated against known AWS regions
- **Status:** `[ ]` Open
- **File:** `backend/app/routers/otp_auth.py:217–222`
- **Risk:** `PUT /api/otp/region` accepts any non-empty string as a region and stores it in the session without validation. Downstream boto3 calls use this value for all AWS API calls.

**Fix:** Validate against a fixed set of AWS region names before storing.

---

## Full Issue Index

| ID | Severity | Status | File | Description |
|----|----------|--------|------|-------------|
| C1 | Critical | `[x]` | `core/otp_service.py:11` | Non-CSPRNG used for OTP generation |
| C2 | Critical | `[x]` | `routers/auth.py`, `main.py:47` | Legacy key-auth bypasses OTP/approval |
| C3 | Critical | `[x]` | `routers/requests_router.py:331` | Non-"deny" action defaults to approve |
| C4 | Critical | `[ ]` | `routers/otp_auth.py:37` | No rate limiting on OTP endpoints |
| H1 | High | `[ ]` | Multiple cookie setters | `secure=False` hardcoded |
| H2 | High | `[ ]` | `docker-compose.yml:34` | Backend port 8000 publicly exposed |
| H3 | High | `[ ]` | `core/email_service.py:71,104,128` | HTML injection in email templates |
| H4 | High | `[ ]` | `core/otp_service.py:34` | No OTP attempt lockout |
| M1 | Medium | `[ ]` | `main.py:20` | CORS origin hardcoded, not env-configurable |
| M2 | Medium | `[ ]` | `routers/admin.py:151` | `update_user` allows any user as manager |
| M3 | Medium | `[ ]` | `routers/requests_router.py:191` | Auto-registered users get all services |
| M4 | Medium | `[ ]` | `routers/requests_router.py:123` | STS errors leaked to client |
| M5 | Medium | `[ ]` | `docker-compose.yml:36` | `--reload` in production command |
| M6 | Medium | `[ ]` | `docker-compose.yml:11` | Default password `changeme` |
| M7 | Medium | `[ ]` | `docker-compose.yml:22` | Valkey unauthenticated |
| L1 | Low | `[ ]` | `core/config.py:28` | `SECRET_KEY` unused |
| L2 | Low | `[ ]` | `routers/otp_auth.py:217` | Region not validated |
