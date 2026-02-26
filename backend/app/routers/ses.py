from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from botocore.exceptions import ClientError
from datetime import datetime, timedelta
from typing import List, Optional
from ..core.aws import get_session_and_config, get_current_session
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/ses", tags=["SES"])


def _fetch_ses_overview(session):
    sesv2 = session.client("sesv2")
    cw = session.client("cloudwatch")

    account = sesv2.get_account()
    send_quota = account.get("SendQuota", {})
    suppression_attrs = account.get("SuppressionAttributes", {})

    def get_ses_metric(metric_name, stat="Sum"):
        try:
            resp = cw.get_metric_statistics(
                Namespace="AWS/SES",
                MetricName=metric_name,
                StartTime=datetime.utcnow() - timedelta(hours=24),
                EndTime=datetime.utcnow(),
                Period=86400,
                Statistics=[stat],
            )
            pts = resp.get("Datapoints", [])
            return round(pts[0][stat], 2) if pts else 0
        except:
            return None

    identities_resp = sesv2.list_email_identities()
    identities = identities_resp.get("EmailIdentities", [])
    identity_summary = {
        "total": len(identities),
        "verified": sum(1 for i in identities if i.get("VerificationStatus") == "SUCCESS"),
    }

    return {
        "max_24h_send": send_quota.get("Max24HourSend", 0),
        "sent_last_24h": send_quota.get("SentLast24Hours", 0),
        "max_per_second": send_quota.get("MaxSendRate", 0),
        "suppression_reasons": suppression_attrs.get("SuppressedReasons", []),
        "sends_24h": get_ses_metric("Send"),
        "deliveries_24h": get_ses_metric("Delivery"),
        "bounces_24h": get_ses_metric("Bounce"),
        "complaints_24h": get_ses_metric("Complaint"),
        "rejects_24h": get_ses_metric("Reject"),
        "identities": identity_summary,
    }


def _fetch_ses_identities(session):
    sesv2 = session.client("sesv2")
    all_identities = []
    kwargs = {"PageSize": 1000}
    while True:
        resp = sesv2.list_email_identities(**kwargs)
        all_identities.extend(resp.get("EmailIdentities", []))
        next_token = resp.get("NextToken")
        if not next_token:
            break
        kwargs["NextToken"] = next_token

    identities = []
    for i in all_identities:
        try:
            detail = sesv2.get_email_identity(EmailIdentity=i["IdentityName"])
            dkim = detail.get("DkimAttributes", {})
            mail_from = detail.get("MailFromAttributes", {})
            identities.append({
                "identity": i["IdentityName"],
                "type": i.get("IdentityType", "—"),
                "status": i.get("VerificationStatus", "—"),
                "sending_enabled": detail.get("SendingAttributes", {}).get("SendingEnabled", False),
                "feedback_forwarding": detail.get("FeedbackForwardingStatus", False),
                "dkim_enabled": dkim.get("SigningEnabled", False),
                "dkim_status": dkim.get("Status", "—"),
                "dkim_origin": dkim.get("SigningAttributesOrigin"),
                "dkim_tokens": dkim.get("Tokens", []),
                "mail_from_domain": mail_from.get("MailFromDomain"),
                "mail_from_status": mail_from.get("MailFromDomainStatus"),
                "mail_from_mx_failure": mail_from.get("BehaviorOnMxFailure"),
                "configuration_set": detail.get("ConfigurationSetName"),
                "tags": {t["Key"]: t["Value"] for t in detail.get("Tags", [])},
            })
        except ClientError:
            identities.append({
                "identity": i["IdentityName"],
                "type": i.get("IdentityType", "—"),
                "status": i.get("VerificationStatus", "—"),
                "sending_enabled": False,
                "feedback_forwarding": False,
                "dkim_enabled": False,
                "dkim_status": "—",
                "dkim_origin": None,
                "dkim_tokens": [],
                "mail_from_domain": None,
                "mail_from_status": None,
                "mail_from_mx_failure": None,
                "configuration_set": None,
                "tags": {},
            })
    return {"identities": identities, "count": len(identities)}


@router.get("/overview")
def get_ses_overview(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("ses:overview", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_ses_overview(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/identities")
def get_ses_identities(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("ses:identities", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_ses_identities(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suppression")
def get_suppression_list(
    request: Request,
    next_token: Optional[str] = None,
    page_size: int = 100,
    reason: Optional[str] = None,
):
    # Not cached — user is actively browsing paginated data
    session = get_current_session(request)
    sesv2 = session.client("sesv2")
    try:
        kwargs = {"PageSize": min(page_size, 100)}
        if next_token:
            kwargs["NextToken"] = next_token
        if reason:
            kwargs["Reasons"] = [reason]

        resp = sesv2.list_suppressed_destinations(**kwargs)
        return {
            "entries": [
                {
                    "email": d["EmailAddress"],
                    "reason": d["Reason"],
                    "suppressed_at": d["LastUpdateTime"].isoformat(),
                } for d in resp.get("SuppressedDestinationSummaries", [])
            ],
            "next_token": resp.get("NextToken")
        }
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


class LookupRequest(BaseModel):
    email: str


class RemoveRequest(BaseModel):
    emails: List[str]


@router.post("/suppression/lookup")
def suppression_lookup(request: Request, body: LookupRequest):
    session = get_current_session(request)
    sesv2 = session.client("sesv2")
    try:
        resp = sesv2.get_suppressed_destination(EmailAddress=body.email)
        dest = resp["SuppressedDestination"]
        attrs = dest.get("Attributes", {})
        return {
            "found": True,
            "email": dest["EmailAddress"],
            "reason": dest["Reason"],
            "suppressed_at": dest["LastUpdateTime"].isoformat(),
            "feedback_id": attrs.get("FeedbackId"),
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotFoundException":
            return {"found": False, "email": body.email}
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/suppression/remove")
def suppression_remove(request: Request, body: RemoveRequest):
    session = get_current_session(request)
    sesv2 = session.client("sesv2")
    results = []
    for email in body.emails:
        try:
            sesv2.delete_suppressed_destination(EmailAddress=email)
            results.append({"email": email, "removed": True})
        except ClientError as e:
            results.append({"email": email, "removed": False, "error": str(e)})
    removed_count = sum(1 for r in results if r["removed"])
    return {
        "removed_count": removed_count,
        "failed_count": len(results) - removed_count,
        "results": results,
    }
