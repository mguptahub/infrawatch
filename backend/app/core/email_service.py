import smtplib
import asyncio
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from .config import settings


def _send_sync(to: str, subject: str, html: str, text: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    if settings.smtp_tls:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port)
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port)

    if settings.smtp_user:
        server.login(settings.smtp_user, settings.smtp_password)

    server.sendmail(settings.smtp_from, to, msg.as_string())
    server.quit()


async def send_email(to: str, subject: str, html: str, text: str = ""):
    await asyncio.to_thread(_send_sync, to, subject, html, text or subject)


# ─── Email templates ──────────────────────────────────────────────────────────

async def send_otp(to: str, code: str, purpose: str = "login"):
    if purpose == "login":
        action = "log in to the AWS Dashboard"
    elif purpose == "approval":
        action = "verify your identity to approve a request"
    else:
        action = "verify your email address"
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
      <h2 style="color:#1a1a2e;margin-bottom:8px">Your verification code</h2>
      <p style="color:#555;margin-bottom:24px">Use this code to {action}.</p>
      <div style="background:#f4f4f8;border-radius:8px;padding:24px;text-align:center;
                  font-size:36px;font-weight:700;letter-spacing:8px;color:#1a1a2e">
        {code}
      </div>
      <p style="color:#999;font-size:13px;margin-top:24px">
        This code expires in 10 minutes. Do not share it with anyone.
      </p>
    </div>
    """
    await send_email(to, "Your AWS Dashboard verification code", html, f"Your verification code: {code}")


async def send_manager_notification(
    manager_email: str,
    requester_name: str,
    requester_email: str,
    services: list,
    duration_hours: int,
    approval_link: str,
):
    services_str = ", ".join(s.upper() for s in services)
    html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px">
      <h2 style="color:#1a1a2e">New Access Request</h2>
      <p style="color:#555"><strong>{requester_name}</strong> ({requester_email}) has requested
         temporary AWS access.</p>
      <table style="width:100%;border-collapse:collapse;margin:20px 0">
        <tr><td style="padding:8px;color:#999;width:140px">Services</td>
            <td style="padding:8px;font-weight:600">{services_str}</td></tr>
        <tr style="background:#f9f9f9">
            <td style="padding:8px;color:#999">Duration</td>
            <td style="padding:8px;font-weight:600">{duration_hours} hour(s)</td></tr>
      </table>
      <a href="{approval_link}"
         style="display:inline-block;background:#2563eb;color:#fff;padding:12px 28px;
                border-radius:6px;text-decoration:none;font-weight:600;margin-top:8px">
        Review Request
      </a>
      <p style="color:#999;font-size:12px;margin-top:24px">
        You will be asked to verify your identity before approving or denying this request.
      </p>
    </div>
    """
    await send_email(
        manager_email,
        f"Access request from {requester_name}",
        html,
        f"{requester_name} requested access to {services_str} for {duration_hours}h. Review: {approval_link}",
    )


async def send_approval_confirmation(to: str, name: str, services: list, duration_hours: int, login_url: str):
    services_str = ", ".join(s.upper() for s in services)
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
      <h2 style="color:#16a34a">Access Approved ✓</h2>
      <p style="color:#555">Hi {name}, your access request has been approved.</p>
      <table style="width:100%;border-collapse:collapse;margin:20px 0">
        <tr><td style="padding:8px;color:#999;width:140px">Services</td>
            <td style="padding:8px;font-weight:600">{services_str}</td></tr>
        <tr style="background:#f9f9f9">
            <td style="padding:8px;color:#999">Duration</td>
            <td style="padding:8px;font-weight:600">{duration_hours} hour(s)</td></tr>
      </table>
      <a href="{login_url}"
         style="display:inline-block;background:#16a34a;color:#fff;padding:12px 28px;
                border-radius:6px;text-decoration:none;font-weight:600;margin-top:8px">
        Log In Now
      </a>
    </div>
    """
    await send_email(to, "Your AWS access has been approved", html,
                     f"Your access to {services_str} for {duration_hours}h has been approved. Log in: {login_url}")


async def send_denial_notification(to: str, name: str, services: list, reason: str = ""):
    services_str = ", ".join(s.upper() for s in services)
    reason_block = f"<p style='color:#555'><strong>Reason:</strong> {reason}</p>" if reason else ""
    html = f"""
    <div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px">
      <h2 style="color:#dc2626">Access Request Denied</h2>
      <p style="color:#555">Hi {name}, your request for access to <strong>{services_str}</strong>
         has been denied.</p>
      {reason_block}
      <p style="color:#999;font-size:13px">Please contact your manager if you have questions.</p>
    </div>
    """
    await send_email(to, "Your AWS access request was denied", html,
                     f"Your request for {services_str} was denied. {reason}")


async def send_new_user_notification(
    admin_email: str,
    new_user_email: str,
    services: list,
    duration_hours: int,
    admin_url: str,
):
    """Notify admin that a new user auto-registered and submitted an access request."""
    from html import escape
    safe_email = escape(new_user_email)
    services_str = ", ".join(s.upper() for s in services)
    safe_services = escape(services_str)
    safe_url = escape(admin_url)
    html = f"""
    <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px">
      <h2 style="color:#1a1a2e">New User Registered</h2>
      <p style="color:#555"><strong>{safe_email}</strong> has self-registered via email
         verification and submitted an access request.</p>
      <table style="width:100%;border-collapse:collapse;margin:20px 0">
        <tr><td style="padding:8px;color:#999;width:140px">Email</td>
            <td style="padding:8px;font-weight:600">{safe_email}</td></tr>
        <tr style="background:#f9f9f9">
            <td style="padding:8px;color:#999">Services</td>
            <td style="padding:8px;font-weight:600">{safe_services}</td></tr>
        <tr><td style="padding:8px;color:#999">Duration</td>
            <td style="padding:8px;font-weight:600">{duration_hours} hour(s)</td></tr>
      </table>
      <a href="{safe_url}"
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
