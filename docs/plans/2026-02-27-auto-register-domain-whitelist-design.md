# Auto-Register with Domain Whitelist — Design

**Date:** 2026-02-27
**Status:** Approved

## Overview

Remove the manual "admin adds user first" requirement by allowing employees with a whitelisted email domain to self-register via OTP email confirmation when they submit their first access request.

If `ALLOWED_DOMAINS` is not set, the feature is off and all existing behaviour is unchanged.

---

## Configuration

```env
ALLOWED_DOMAINS=plane.so,contractor.com   # optional, comma-separated
```

- If unset or empty: feature disabled, unknown emails get 403 "not_registered" as today
- Multiple domains supported

---

## Full User Flows

### Pre-registered user (existing behaviour, unchanged)

```
Enter email → OTP sent → verify → dashboard
  (or if no active access → RequestPage → submit → approval flow)
```

### New user — whitelisted domain

```
1. Enter email on LoginPage
2. POST /api/otp/request → "not_registered"
   → frontend redirects to RequestPage (same as "no_active_access")
3. User fills: email, services, duration → Submit
4. POST /api/requests
   → backend: email not in DB + domain whitelisted
   → sends OTP (purpose="registration") to email
   → returns { status: "verification_required" }
5. Frontend: show OTP input ("Check your email to confirm your address")
6. User enters OTP → POST /api/requests/verify
   → backend: verifies OTP, creates user, creates request, notifies admin
   → returns { success, message }
7. Frontend: "Request submitted — admin will review"
```

### New user — non-whitelisted domain

```
RequestPage submit → 403 "not_registered" → "Contact your admin"
```

---

## Backend Changes

### 1. `otp_codes` table — new purpose value

Add `"registration"` to the purpose enum.
**Migration strategy:** Switch the `purpose` column from a PostgreSQL ENUM to `VARCHAR(20)` to avoid `ALTER TYPE` complexity. This is a one-time schema change handled in a startup migration helper.

### 2. `POST /api/requests` (modified)

Current: validates user exists first, 403 if not.
New logic:

```
if user exists:
    → existing logic unchanged
elif email domain in ALLOWED_DOMAINS:
    → create_otp(email, purpose="registration")
    → send registration OTP email
    → return { status: "verification_required" }
else:
    → 403 "not_registered"
```

### 3. `POST /api/requests/verify` (new endpoint)

**Body:** `{ email, otp_code, services, duration_hours }`

**Steps:**
1. Verify OTP (purpose=`"registration"`, single-use, 10-min expiry)
2. Create user:
   - `role = "employee"`
   - `allowed_services = [all available services]`
   - `max_duration_hours = 1`
   - `auto_approve = False`
   - `manager_id = None` → falls back to admin for approval
   - `active = True`
3. Validate services and duration against user settings (using same helper as existing request creation)
4. Create access request (same logic as existing `POST /api/requests` after user lookup)
5. Send admin notification email:
   `"New user john@plane.so auto-registered and requesting [ec2, ses] for 2h"`
   with link to admin panel
6. Return `{ success: true, message: "Request submitted — admin will review" }`

**Error cases:**
- Invalid/expired/used OTP → 400
- Email domain not whitelisted (safety check) → 403
- User already exists (race condition) → proceed as normal request creation

### 4. Admin notification email

Single combined email: new registration + access request details.
Reuses existing `send_manager_notification` pattern, adapted for admin-only routing.

---

## Frontend Changes

### `LoginPage`

Redirect to `RequestPage` on `"not_registered"` error — same behaviour already in place for `"no_active_access"`.

### `RequestPage`

Add a new stage after form submission:

```
stages: form → [otp_verification (new users only)] → confirmation
```

- After submit: if response has `status === "verification_required"` → show OTP input
- OTP submit → `POST /api/requests/verify` → show confirmation screen
- Existing `auto_approved` and `pending` confirmation screens unchanged

### No other pages change

`AdminPage`, `LoginPage` (OTP stage), `ApprovalPage` — all unchanged.

---

## Admin Experience

- Admin receives one email per new user registration: user identity + requested services + duration
- Admin can approve/deny from the admin panel (existing request management UI)
- Admin can update the user's settings at any time via `PATCH /api/admin/users/{id}`:
  - `allowed_services`, `max_duration_hours`, `manager_id`, `auto_approve`, `active`
- No new admin UI needed

---

## Edge Cases

| Scenario | Handling |
|---|---|
| User submits request, OTP expires before entry | 400 error, re-submit the form to get a new OTP |
| Two users race to register same email | Second `verify` hits "user already exists" → treated as existing user request |
| Domain whitelisted but user never verifies OTP | No user record created (OTP expires, nothing persists) |
| User already registered, hits RequestPage | Existing flow: request created directly, no OTP step |
| `ALLOWED_DOMAINS` not set | Feature fully disabled, zero behaviour change |
| Admin removes domain from whitelist mid-flight | Pending OTPs still valid (10 min), new submissions blocked |

---

## What Does Not Change

- All existing auth flows (login, approval, admin OTP)
- Manager approval workflow
- STS credential handling
- Session management
- All existing API endpoints (only `POST /api/requests` gets modified; `POST /api/requests/verify` is additive)
