from fastapi import APIRouter, Request, HTTPException
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from ..core.aws import get_session_and_config
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL

router = APIRouter(prefix="/api/cost", tags=["Cost"])


def _month_range(year, month):
    first = datetime(year, month, 1).date()
    last = datetime(year + 1, 1, 1).date() if month == 12 else datetime(year, month + 1, 1).date()
    return first, last


def _prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_label(year, month):
    return datetime(year, month, 1).strftime("%b %Y")


def _fetch_svc_map(ce, start, end):
    """Return {service: cost} for a date range (full month or MTD)."""
    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": str(start), "End": str(end)},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    result = {}
    if resp["ResultsByTime"]:
        for g in resp["ResultsByTime"][0]["Groups"]:
            cost = round(float(g["Metrics"]["UnblendedCost"]["Amount"]), 2)
            if cost > 0:
                result[g["Keys"][0]] = cost
    return result


def _fetch_cost(session):
    ce = session.client("ce", region_name="us-east-1")

    today = datetime.utcnow().date()

    # Build last-3-month windows
    # m0 = current, m1 = prev, m2 = 2 months ago
    m0_y, m0_m = today.year, today.month
    m1_y, m1_m = _prev_month(m0_y, m0_m)
    m2_y, m2_m = _prev_month(m1_y, m1_m)

    m0_start, m0_end = _month_range(m0_y, m0_m)
    m1_start, m1_end = _month_range(m1_y, m1_m)
    m2_start, m2_end = _month_range(m2_y, m2_m)

    # ── Parallel fetches ──────────────────────────────────────────────────────
    def fetch_mtd_total():
        r = ce.get_cost_and_usage(
            TimePeriod={"Start": str(m0_start), "End": str(today)},
            Granularity="MONTHLY", Metrics=["UnblendedCost"],
        )
        return float(r["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]) if r["ResultsByTime"] else 0

    def fetch_m1_total():
        r = ce.get_cost_and_usage(
            TimePeriod={"Start": str(m1_start), "End": str(m1_end)},
            Granularity="MONTHLY", Metrics=["UnblendedCost"],
        )
        return float(r["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]) if r["ResultsByTime"] else 0

    def fetch_m2_total():
        r = ce.get_cost_and_usage(
            TimePeriod={"Start": str(m2_start), "End": str(m2_end)},
            Granularity="MONTHLY", Metrics=["UnblendedCost"],
        )
        return float(r["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]) if r["ResultsByTime"] else 0

    def fetch_m0_svc():
        return _fetch_svc_map(ce, m0_start, today)

    def fetch_m1_svc():
        return _fetch_svc_map(ce, m1_start, m1_end)

    def fetch_m2_svc():
        return _fetch_svc_map(ce, m2_start, m2_end)

    def fetch_daily():
        r = ce.get_cost_and_usage(
            TimePeriod={"Start": str(today - timedelta(days=60)), "End": str(today)},
            Granularity="DAILY", Metrics=["UnblendedCost"],
        )
        return [
            {"date": x["TimePeriod"]["Start"], "cost": round(float(x["Total"]["UnblendedCost"]["Amount"]), 2)}
            for x in r["ResultsByTime"]
        ]

    def fetch_forecast():
        try:
            if today < m0_end - timedelta(days=1):
                r = ce.get_cost_forecast(
                    TimePeriod={"Start": str(today), "End": str(m0_end)},
                    Metric="UNBLENDED_COST", Granularity="MONTHLY",
                )
                return float(r["Total"]["Amount"])
        except Exception:
            pass
        return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        f_m0  = ex.submit(fetch_mtd_total)
        f_m1  = ex.submit(fetch_m1_total)
        f_m2  = ex.submit(fetch_m2_total)
        f_s0  = ex.submit(fetch_m0_svc)
        f_s1  = ex.submit(fetch_m1_svc)
        f_s2  = ex.submit(fetch_m2_svc)
        f_day = ex.submit(fetch_daily)
        f_fc  = ex.submit(fetch_forecast)

        m0_total = round(f_m0.result(), 2)
        m1_total = round(f_m1.result(), 2)
        m2_total = round(f_m2.result(), 2)
        svc_m0   = f_s0.result()
        svc_m1   = f_s1.result()
        svc_m2   = f_s2.result()
        daily    = f_day.result()
        fc_remaining = f_fc.result()

    # ── Projected ─────────────────────────────────────────────────────────────
    projected = round(m0_total + fc_remaining, 2) if fc_remaining is not None else None

    # ── Monthly history (for bar chart) ───────────────────────────────────────
    monthly_history = [
        {"label": _month_label(m2_y, m2_m), "month": str(m2_start), "total": m2_total},
        {"label": _month_label(m1_y, m1_m), "month": str(m1_start), "total": m1_total},
        {"label": _month_label(m0_y, m0_m), "month": str(m0_start), "total": m0_total, "is_mtd": True},
    ]

    # ── Service comparison (all services seen in any of the 3 months) ─────────
    all_services = sorted(
        set(svc_m0.keys()) | set(svc_m1.keys()) | set(svc_m2.keys())
    )
    service_comparison = sorted(
        [
            {
                "service": svc,
                "m2": svc_m2.get(svc, 0),
                "m1": svc_m1.get(svc, 0),
                "m0": svc_m0.get(svc, 0),
            }
            for svc in all_services
            if svc_m0.get(svc, 0) > 0 or svc_m1.get(svc, 0) > 0
        ],
        key=lambda x: x["m0"],
        reverse=True,
    )

    # ── by_service for current month ──────────────────────────────────────────
    by_service = sorted(
        [{"service": k, "cost": v} for k, v in svc_m0.items()],
        key=lambda x: x["cost"], reverse=True,
    )

    # ── Today's cost ──────────────────────────────────────────────────────────
    today_cost = daily[-1]["cost"] if daily else None

    # ── MoM delta % ──────────────────────────────────────────────────────────
    mom_delta = round(((m0_total - m1_total) / m1_total) * 100, 1) if m1_total > 0 else None

    return {
        "month_total": m0_total,
        "prev_month_total": m1_total,
        "projected": projected,
        "today_cost": today_cost,
        "mom_delta": mom_delta,
        "currency": "USD",
        "by_service": by_service,
        "daily": daily,
        "monthly_history": monthly_history,
        "service_comparison": service_comparison,
        "period": {
            "cur_month":  str(m0_start),
            "prev_month": str(m1_start),
            "prev2_month": str(m2_start),
        },
    }


@router.get("/summary")
def get_cost_summary(request: Request, force: bool = False):
    session, config = get_session_and_config(request)
    key = make_cache_key("cost", config.access_key or "", config.region)
    try:
        return get_cached(key, CACHE_TTL, lambda: _fetch_cost(session), force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))
