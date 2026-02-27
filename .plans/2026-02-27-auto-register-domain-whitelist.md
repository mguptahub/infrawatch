# Auto-Register with Domain Whitelist — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow unregistered employees with a whitelisted email domain to self-register via OTP confirmation when submitting their first access request, removing the need for admin to pre-create every user.

**Architecture:** `POST /api/requests` detects unknown emails with whitelisted domains, sends a registration OTP instead of a 404, and returns `{ status: "verification_required" }`. A new `POST /api/requests/verify` endpoint verifies the OTP, creates the user with all services allowed, and completes the request submission. Frontend adds an OTP stage to `RequestPage` and redirects `not_registered` errors to `RequestPage` instead of showing a dead-end message.

**Tech Stack:** FastAPI (Python), SQLAlchemy (PostgreSQL), React 18. No test infrastructure exists in this project — verify by running the docker-compose stack and exercising each path manually.

---

### Task 1: Add ALLOWED_DOMAINS to config

**Files:**
- Modify: `backend/app/core/config.py`

**Step 1: Add the setting and domain-check helper**

In `config.py`, after the `base_role_arn` field and before the `database_url` property, add:

```python
    # Auto-registration — comma-separated domains, e.g. "plane.so,contractor.com"
    # Leave empty to disable auto-registration entirely
    allowed_domains: str = ""
```

Also add this method inside the `Settings` class, after the `database_url` property:

```python
    def is_domain_allowed(self, email: str) -> bool:
        """Return True if the email's domain is in the ALLOWED_DOMAINS whitelist."""
        if not self.allowed_domains.strip():
            return False
        domain = email.split("@")[-1].lower()
        return domain in [d.strip().lower() for d in self.allowed_domains.split(",")]
```

**Step 2: Add to .env.example**

Open `.env.example` (or `.env` if `.env.example` doesn't exist) and add after the existing entries:

```
# Auto-registration whitelist (optional — leave blank to disable)
# ALLOWED_DOMAINS=plane.so,contractor.com
```

**Step 3: Commit**

```bash
git add backend/app/core/config.py .env.example
git commit -m "feat: add ALLOWED_DOMAINS config for auto-registration"
```

---

### Task 2: Extend OTPPurpose enum and migrate the column

The `otp_codes.purpose` column is a PostgreSQL ENUM type. Adding a new value requires a one-time `ALTER TYPE` statement. The plan also changes the SQLAlchemy column definition from `SAEnum(OTPPurpose)` to `String(20)` so future purpose additions never need a migration again.

**Files:**
- Modify: `backend/app/db/models.py:25-28,88`
- Modify: `backend/app/core/database.py:22-33`

**Step 1: Update the Python enum in models.py**

Change lines 25–28 from:

```python
class OTPPurpose(str, enum.Enum):
    login = "login"
    approval = "approval"
```

To:

```python
class OTPPurpose(str, enum.Enum):
    login = "login"
    approval = "approval"
    registration = "registration"
```

**Step 2: Change the OTPCode.purpose column to String**

On line 88, change:

```python
    purpose = Column(SAEnum(OTPPurpose), nullable=False)
```

To:

```python
    purpose = Column(String(20), nullable=False)
```

You can also remove `Enum as SAEnum` from the imports on line 6 if it's no longer used anywhere else. Check: search the file for `SAEnum` — if the only remaining usages are `UserRole` and `RequestStatus`, leave those alone and just remove the alias from the import line (change `Enum as SAEnum` to `Enum`... actually just keep the import as-is since the other models still use it for `UserRole` and `RequestStatus`).

**Step 3: Add a startup migration in database.py**

In `init_db()`, before `Base.metadata.create_all(...)`, add the ALTER statements to convert the existing PostgreSQL ENUM column to VARCHAR:

```python
def init_db():
    import time
    from ..db import models  # noqa: F401 — registers all models
    for attempt in range(10):
        try:
            # Migrate otp_codes.purpose from ENUM to VARCHAR if needed
            with engine.connect() as conn:
                conn.execute(text(
                    "ALTER TABLE otp_codes "
                    "ALTER COLUMN purpose TYPE VARCHAR(20) "
                    "USING purpose::text"
                ))
                conn.commit()
        except Exception:
            pass  # Column already VARCHAR or table doesn't exist yet — safe to ignore

        for attempt in range(10):
            try:
                Base.metadata.create_all(bind=engine)
                return
            except Exception as e:
                if attempt == 9:
                    raise
                print(f"DB not ready (attempt {attempt + 1}/10): {e}. Retrying in 3s…")
                time.sleep(3)
```

Wait — the nested loop is wrong. Here is the correct replacement for the entire `init_db` function:

```python
def init_db():
    import time
    from sqlalchemy import text
    from ..db import models  # noqa: F401 — registers all models

    # One-time migration: convert otp_codes.purpose from PG ENUM to VARCHAR
    # Safe to run every startup — silently skipped if already VARCHAR or table absent
    try:
        with engine.connect() as conn:
            conn.execute(text(
                "ALTER TABLE otp_codes "
                "ALTER COLUMN purpose TYPE VARCHAR(20) "
                "USING purpose::text"
            ))
            conn.commit()
    except Exception:
        pass

    for attempt in range(10):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except Exception as e:
            if attempt == 9:
                raise
            print(f"DB not ready (attempt {attempt + 1}/10): {e}. Retrying in 3s…")
            time.sleep(3)
```

**Step 4: Commit**

```bash
git add backend/app/db/models.py backend/app/core/database.py
git commit -m "feat: extend OTPPurpose with registration, migrate purpose column to VARCHAR"
```

---

### Task 3: Add admin new-user notification email

**Files:**
- Modify: `backend/app/core/email_service.py`

**Step 1: Add the function at the end of email_service.py**

```python
async def send_new_user_notification(
    admin_email: str,
    new_user_email: str,
    services: list,
    duration_hours: int,
    admin_url: str,
):
    """Notify admin that a new user auto-registered and submitted an access request."""
    services_str = ", ".join(s.upper() for s in services)
    html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px">
      <h2 style="color:#1a1a2e">New User Registered</h2>
      <p style="color:#555"><strong>{new_user_email}</strong> has self-registered via email
         verification and submitted an access request.</p>
      <table style="width:100%;border-collapse:collapse;margin:20px 0">
        <tr><td style="padding:8px;color:#999;width:140px">Email</td>
            <td style="padding:8px;font-weight:600">{new_user_email}</td></tr>
        <tr style="background:#f9f9f9">
            <td style="padding:8px;color:#999">Services</td>
            <td style="padding:8px;font-weight:600">{services_str}</td></tr>
        <tr><td style="padding:8px;color:#999">Duration</td>
            <td style="padding:8px;font-weight:600">{duration_hours} hour(s)</td></tr>
      </table>
      <a href="{admin_url}"
         style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;
                border-radius:6px;text-decoration:none;font-weight:600;margin-top:8px">
        Review in Admin Panel
      </a>
      <p style="color:#999;font-size:12px;margin-top:24px">
        You can approve the request and configure the user's settings from the admin panel.
      </p>
    </div>
    """
    await send_email(
        admin_email,
        f"New user registered: {new_user_email}",
        html,
        f"{new_user_email} auto-registered and is requesting {services_str} for {duration_hours}h. "
        f"Review: {admin_url}",
    )
```

**Step 2: Commit**

```bash
git add backend/app/core/email_service.py
git commit -m "feat: add send_new_user_notification email for auto-registered users"
```

---

### Task 4: Modify POST /api/requests and add POST /api/requests/verify

This is the core backend change.

**Files:**
- Modify: `backend/app/routers/requests_router.py`

**Step 1: Update imports at the top of requests_router.py**

Add `send_new_user_notification` to the email service import on lines 11–14:

```python
from ..core.email_service import (
    send_manager_notification, send_otp,
    send_approval_confirmation, send_denial_notification,
    send_new_user_notification,
)
```

Add `ALL_SERVICES` to the sts_service import on line 15:

```python
from ..core.sts_service import assume_role_for_services, ALL_SERVICES
```

Add `UserRole` to the models import on lines 17–19:

```python
from ..db.models import (
    User, AccessRequest, ApprovalToken, RequestStatus, OTPPurpose, UserRole,
)
```

**Step 2: Add VerifyAndSubmitBody model**

After the existing `OTPForApprovalBody` model (after line 60), add:

```python
class VerifyAndSubmitBody(BaseModel):
    email: str
    otp_code: str
    services: list[str]
    duration_hours: int
```

**Step 3: Modify submit_request to handle unregistered whitelisted emails**

Replace lines 66–69 (the user lookup and 404 raise) with:

```python
    email = body.email.strip().lower()
    user = db.query(User).filter(User.email == email, User.active == True).first()  # noqa: E712

    if not user:
        if settings.is_domain_allowed(email):
            # Unknown email but domain is whitelisted — send registration OTP
            code = create_otp(db, email, OTPPurpose.registration)
            await send_otp(email, code, purpose="registration")
            return {"status": "verification_required"}
        raise HTTPException(status_code=404, detail="Email not registered. Contact your admin.")
```

The rest of `submit_request` (service validation, duration check, auto-approve logic, manual approval logic) stays exactly as-is.

**Step 4: Add the verify_and_submit endpoint**

Add this new endpoint after the `submit_request` function (before the `get_approval_request` route at line 154):

```python
# ─── Verify registration OTP and complete request submission ─────────────────

@router.post("/verify")
async def verify_and_submit(body: VerifyAndSubmitBody, db: Session = Depends(get_db)):
    """
    For new users: verify the registration OTP, create the user account,
    and submit their access request to the admin for approval.
    """
    import secrets as secrets_module
    email = body.email.strip().lower()

    if not settings.is_domain_allowed(email):
        raise HTTPException(status_code=403, detail="Email domain not whitelisted for auto-registration")

    if not verify_otp(db, email, body.otp_code, OTPPurpose.registration):
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    # Race condition guard: user may have been created between OTP send and verify
    user = db.query(User).filter(User.email == email).first()
    if not user:
        # Derive a display name from the email local part (e.g. "john.doe" → "John Doe")
        local = email.split("@")[0]
        name = " ".join(part.capitalize() for part in local.replace(".", " ").replace("_", " ").split())
        user = User(
            email=email,
            name=name,
            role=UserRole.employee,
            allowed_services=list(ALL_SERVICES),
            max_duration_hours=1,
            auto_approve=False,
            active=True,
        )
        db.add(user)
        db.flush()  # get user.id without committing

    # Validate services and duration (same rules as submit_request)
    invalid = [s for s in body.services if s not in user.allowed_services]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Services not allowed: {', '.join(invalid)}")

    if body.duration_hours < 1 or body.duration_hours > user.max_duration_hours:
        raise HTTPException(
            status_code=400,
            detail=f"Duration must be between 1 and {user.max_duration_hours} hours",
        )

    # Cancel any previous pending requests
    db.query(AccessRequest).filter(
        AccessRequest.user_id == user.id,
        AccessRequest.status == RequestStatus.pending,
    ).update({"status": RequestStatus.denied, "denial_reason": "Superseded by a new request"})

    # Create access request
    access_request = AccessRequest(
        user_id=user.id,
        services=body.services,
        duration_hours=body.duration_hours,
    )
    db.add(access_request)
    db.flush()

    # Create approval token for admin
    token_value = secrets_module.token_urlsafe(32)
    approval_token = ApprovalToken(
        request_id=access_request.id,
        token=token_value,
        expires_at=datetime.utcnow() + timedelta(hours=APPROVAL_TOKEN_EXPIRY_HOURS),
    )
    db.add(approval_token)
    db.commit()

    # Always notify admin (new users have no manager)
    admin_url = f"{settings.frontend_url}/"
    await send_new_user_notification(
        admin_email=settings.admin_email,
        new_user_email=email,
        services=body.services,
        duration_hours=body.duration_hours,
        admin_url=admin_url,
    )

    return {"success": True, "message": "Request submitted. Admin will review your request."}
```

**Step 5: Update send_otp email to handle registration purpose**

In `email_service.py`, the `send_otp` function on line 36 currently handles `"login"` and `"approval"`. Update the `action` string to cover `"registration"`:

```python
async def send_otp(to: str, code: str, purpose: str = "login"):
    if purpose == "login":
        action = "log in to the AWS Dashboard"
    elif purpose == "approval":
        action = "verify your identity to approve a request"
    else:
        action = "verify your email address"
```

**Step 6: Commit**

```bash
git add backend/app/routers/requests_router.py backend/app/core/email_service.py
git commit -m "feat: auto-register unregistered users with whitelisted domain on request submission"
```

---

### Task 5: Add verifyAndSubmitRequest to frontend API client

**Files:**
- Modify: `frontend/src/api/client.js`

**Step 1: Add the method to the api object**

In `client.js`, after the `submitRequest` line (line 39), add:

```javascript
  verifyAndSubmitRequest: (email, otp_code, services, duration_hours) =>
    req("/api/requests/verify", {
      method: "POST",
      body: JSON.stringify({ email, otp_code, services, duration_hours }),
    }),
```

**Step 2: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "feat: add verifyAndSubmitRequest API method"
```

---

### Task 6: LoginPage — redirect not_registered to RequestPage

Currently, when a user enters an unregistered email, `LoginPage` shows a dead-end error: "This email is not registered. Contact your admin to get access." With auto-registration active, that user should instead be sent to `RequestPage`.

**Files:**
- Modify: `frontend/src/pages/LoginPage.jsx:25-27`

**Step 1: Change the not_registered handler**

Replace lines 25–27:

```javascript
      } else if (err.message === "not_registered") {
        setError("This email is not registered. Contact your admin to get access.");
      } else {
```

With:

```javascript
      } else if (err.message === "not_registered") {
        setError(null);
        onRequestAccess(email.trim().toLowerCase());
      } else {
```

This is identical to the `no_active_access` handling directly above it — the user is redirected to `RequestPage` with their email pre-filled.

**Step 2: Update the login footer hint**

On line 117, update the footer text so it no longer implies they must contact admin just to start:

```jsx
        <p className="login-footer">
          Don't have access? Use the Request Access button below.
        </p>
```

**Step 3: Commit**

```bash
git add frontend/src/pages/LoginPage.jsx
git commit -m "feat: redirect unregistered emails to RequestPage instead of dead-end error"
```

---

### Task 7: RequestPage — add OTP verification stage for new users

**Files:**
- Modify: `frontend/src/pages/RequestPage.jsx`

**Step 1: Add stage state and registrationOtp state**

The component currently uses `submission` state to track the post-submit screen. Add a new state for the registration verification stage. Add these two new state variables alongside the existing ones (after line 22):

```javascript
  const [regStage, setRegStage] = useState(null); // null | "verify"
  const [regOtp, setRegOtp] = useState("");
  const [regLoading, setRegLoading] = useState(false);
  const [regError, setRegError] = useState(null);
  // Store form values for use in verify step
  const [pendingServices, setPendingServices] = useState([]);
  const [pendingDuration, setPendingDuration] = useState(4);
```

**Step 2: Update handleSubmit to handle verification_required**

Replace the existing `handleSubmit` function (lines 30–45) with:

```javascript
  async function handleSubmit(e) {
    e.preventDefault();
    if (!services.length) { setError("Select at least one service"); return; }
    setLoading(true);
    setError(null);
    try {
      const normalizedEmail = email.trim().toLowerCase();
      const result = await api.submitRequest(normalizedEmail, services, duration);
      if (result.status === "verification_required") {
        // New user — need OTP email verification before account is created
        setEmail(normalizedEmail);
        setPendingServices(services);
        setPendingDuration(duration);
        setRegStage("verify");
      } else {
        setEmail(normalizedEmail);
        setSubmission(result);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }
```

**Step 3: Add handleVerifyRegistration function**

Add this function after `handleSubmit`:

```javascript
  async function handleVerifyRegistration(e) {
    e.preventDefault();
    setRegLoading(true);
    setRegError(null);
    try {
      const result = await api.verifyAndSubmitRequest(
        email, regOtp.trim(), pendingServices, pendingDuration
      );
      setRegStage(null);
      setSubmission(result);
    } catch (err) {
      setRegError(err.message);
      setRegOtp("");
    } finally {
      setRegLoading(false);
    }
  }
```

**Step 4: Add the verification stage render block**

Add this render block before the `if (submission)` block (before line 74):

```javascript
  if (regStage === "verify") {
    return (
      <div className="login-page">
        <div className="login-card">
          <div className="login-logo">
            <span className="logo-icon">⬡</span>
            <h1>Verify Your Email</h1>
            <p>We sent a 6-digit code to confirm your address.</p>
          </div>
          <form onSubmit={handleVerifyRegistration} className="login-form">
            <p className="otp-hint">
              Check your inbox at <strong>{email}</strong>
            </p>
            <div className="field">
              <label>Verification Code</label>
              <input
                type="text"
                inputMode="numeric"
                placeholder="000000"
                value={regOtp}
                onChange={(e) => setRegOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
                className="otp-input"
                autoFocus
                required
              />
            </div>
            {regError && <div className="login-error">{regError}</div>}
            <button type="submit" className="login-btn" disabled={regLoading || regOtp.length !== 6}>
              {regLoading ? "Verifying…" : "Confirm & Submit Request"}
            </button>
            <button
              type="button"
              className="login-secondary-btn"
              onClick={() => { setRegStage(null); setRegOtp(""); setRegError(null); }}
            >
              ← Back
            </button>
          </form>
          <p className="login-footer">Code expires in 10 minutes.</p>
        </div>
      </div>
    );
  }
```

**Step 5: Commit**

```bash
git add frontend/src/pages/RequestPage.jsx
git commit -m "feat: add OTP verification stage for new user auto-registration"
```

---

## Manual Verification Checklist

After all tasks are implemented, verify these paths end-to-end using the running docker-compose stack:

**Happy path — new user with whitelisted domain:**
1. Set `ALLOWED_DOMAINS=plane.so` in `.env`, restart backend
2. Go to login page, enter a new `*@plane.so` email → should redirect to RequestPage
3. Select services and duration, click Submit Request
4. Should show "Verify Your Email" OTP stage
5. Enter the OTP received at that email → should show "Request Submitted" confirmation
6. Log into admin panel → should see new user in Users list and pending request in Requests tab
7. Approve the request → user should receive approval email
8. User logs in via LoginPage OTP → dashboard shows approved services

**Non-whitelisted domain:**
1. On RequestPage, enter a `*@gmail.com` email → Submit
2. Should show error "Email not registered. Contact your admin."

**Whitelisted domain, ALLOWED_DOMAINS unset:**
1. Remove `ALLOWED_DOMAINS` from `.env` (or leave blank), restart backend
2. Enter any new email on LoginPage → should redirect to RequestPage
3. Submit request → should show error "Email not registered. Contact your admin."

**Pre-existing registered user:**
1. Enter a known registered user's email on RequestPage → Submit
2. Should proceed directly to "Request Submitted" or "Access Approved" screen (no OTP stage)

**Expired OTP:**
1. Get to the OTP verification stage
2. Wait 10 minutes (or manually expire in DB: `UPDATE otp_codes SET expires_at = NOW() - interval '1 minute'`)
3. Enter any code → should show "Invalid or expired verification code"
4. Click Back → return to form, resubmit to get a fresh OTP
