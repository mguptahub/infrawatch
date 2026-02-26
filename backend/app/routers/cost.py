from fastapi import APIRouter, Request, HTTPException, Depends
from botocore.exceptions import ClientError
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from ..core.aws import get_session_and_config
from ..core.database import get_db
from ..core.valkey_client import get_cached, make_cache_key, CACHE_TTL
from ..db.models import CostMonthly

router = APIRouter(prefix="/api/cost", tags=["Cost"])


def _month_range(year, month):
    first = datetime(year, month, 1).date()
    last = datetime(year + 1, 1, 1).date() if month == 12 else datetime(year, month + 1, 1).date()
    return first, last


def _prev_month(year, month):
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _month_label(year, month):
    return datetime(year, month, 1).strftime("%b %Y")


def _get_account_id(session):
    """Return AWS account ID for the current session (Cost Explorer is account-scoped)."""
    sts = session.client("sts", region_name="us-east-1")
    return sts.get_caller_identity()["Account"]


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


def _fetch_one_month_from_aws(ce, year, month, end_date=None):
    """
    Fetch total and by_service for one month from AWS.
    If end_date is given (e.g. today for current month), use it as end; else use month end.
    Returns (total, by_service dict).
    """
    start, end = _month_range(year, month)
    if end_date is not None and end_date < end:
        end = end_date
    # Total
    r = ce.get_cost_and_usage(
        TimePeriod={"Start": str(start), "End": str(end)},
        Granularity="MONTHLY", Metrics=["UnblendedCost"],
    )
    total = round(float(r["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"]), 2) if r["ResultsByTime"] else 0
    by_service = _fetch_svc_map(ce, start, end)
    return total, by_service


def _load_month_from_db(db: Session, account_id: str, year: int, month: int) -> Optional[Tuple[float, dict]]:
    row = db.query(CostMonthly).filter(
        CostMonthly.account_id == account_id,
        CostMonthly.year == year,
        CostMonthly.month == month,
    ).first()
    if row is None:
        return None
    return row.total, row.by_service or {}


def _save_month_to_db(db: Session, account_id: str, year: int, month: int, total: float, by_service: dict):
    row = db.query(CostMonthly).filter(
        CostMonthly.account_id == account_id,
        CostMonthly.year == year,
        CostMonthly.month == month,
    ).first()
    if row:
        row.total = total
        row.by_service = by_service
    else:
        db.add(CostMonthly(
            account_id=account_id,
            year=year,
            month=month,
            total=total,
            by_service=by_service,
        ))
    db.commit()


def _fetch_cost(session, db: Session, account_id: str, force: bool):
    """
    Build cost summary. For past months (m1, m2) use DB if present and not force;
    otherwise fetch from AWS and save. For current month (m0) always fetch from AWS and save.
    """
    ce = session.client("ce", region_name="us-east-1")
    today = datetime.utcnow().date()

    m0_y, m0_m = today.year, today.month
    m1_y, m1_m = _prev_month(m0_y, m0_m)
    m2_y, m2_m = _prev_month(m1_y, m1_m)

    m0_start, m0_end = _month_range(m0_y, m0_m)
    m1_start, m1_end = _month_range(m1_y, m1_m)
    m2_start, m2_end = _month_range(m2_y, m2_m)

    # ── m2, m1: from DB or AWS ─────────────────────────────────────────────────
    m2_total, svc_m2 = _load_month_from_db(db, account_id, m2_y, m2_m) if not force else (None, None)
    if m2_total is None:
        m2_total, svc_m2 = _fetch_one_month_from_aws(ce, m2_y, m2_m)
        _save_month_to_db(db, account_id, m2_y, m2_m, m2_total, svc_m2)

    m1_total, svc_m1 = _load_month_from_db(db, account_id, m1_y, m1_m) if not force else (None, None)
    if m1_total is None:
        m1_total, svc_m1 = _fetch_one_month_from_aws(ce, m1_y, m1_m)
        _save_month_to_db(db, account_id, m1_y, m1_m, m1_total, svc_m1)

    # ── m0, daily, forecast: always from AWS ────────────────────────────────────
    def fetch_m0():
        return _fetch_one_month_from_aws(ce, m0_y, m0_m, end_date=today)

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

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_m0 = ex.submit(fetch_m0)
        f_day = ex.submit(fetch_daily)
        f_fc = ex.submit(fetch_forecast)

        m0_total, svc_m0 = f_m0.result()
        daily = f_day.result()
        fc_remaining = f_fc.result()

    _save_month_to_db(db, account_id, m0_y, m0_m, m0_total, svc_m0)

    # ── Projected: CE forecast when available, else simple extrapolation ───────
    days_in_month = (m0_end - m0_start).days
    days_elapsed = (today - m0_start).days or 1
    if fc_remaining is not None:
        projected = round(m0_total + fc_remaining, 2)
        projected_source = "forecast"
    elif days_elapsed > 0 and m0_total >= 0:
        projected = round((m0_total / days_elapsed) * days_in_month, 2)
        projected_source = "extrapolation"
    else:
        projected = None
        projected_source = None

    # ── Monthly history (for bar chart) ───────────────────────────────────────
    monthly_history = [
        {"label": _month_label(m2_y, m2_m), "month": str(m2_start), "total": m2_total},
        {"label": _month_label(m1_y, m1_m), "month": str(m1_start), "total": m1_total},
        {"label": _month_label(m0_y, m0_m), "month": str(m0_start), "total": m0_total, "is_mtd": True},
    ]

    # ── Service comparison ────────────────────────────────────────────────────
    all_services = sorted(set(svc_m0.keys()) | set(svc_m1.keys()) | set(svc_m2.keys()))
    service_comparison = sorted(
        [
            {"service": svc, "m2": svc_m2.get(svc, 0), "m1": svc_m1.get(svc, 0), "m0": svc_m0.get(svc, 0)}
            for svc in all_services
            if svc_m0.get(svc, 0) > 0 or svc_m1.get(svc, 0) > 0
        ],
        key=lambda x: x["m0"],
        reverse=True,
    )

    by_service = sorted(
        [{"service": k, "cost": v} for k, v in svc_m0.items()],
        key=lambda x: x["cost"], reverse=True,
    )

    today_cost = round(float(daily[-1]["cost"]), 2) if daily else None
    yesterday_cost = round(float(daily[-2]["cost"]), 2) if len(daily) >= 2 else None
    today_vs_yesterday_delta = (
        round(((today_cost - yesterday_cost) / yesterday_cost) * 100, 1)
        if today_cost is not None and yesterday_cost is not None and yesterday_cost > 0 else None
    )
    # MTD comparison: same period last month (e.g. Feb 1–5 vs Mar 1–5), not full month
    prev_same_period = None
    prev_same_period_label = None
    mom_delta_same_period = None
    if daily:
        m1_days = (m1_end - m1_start).days
        period_days = min(today.day, m1_days)
        period_end = m1_start + timedelta(days=period_days)
        same_period_costs = [
            float(d["cost"]) for d in daily
            if m1_start <= date.fromisoformat(d["date"]) < period_end
        ]
        if same_period_costs:
            prev_same_period = round(sum(same_period_costs), 2)
            if period_days == 1:
                prev_same_period_label = f"{_month_label(m1_y, m1_m)} 1"
            else:
                prev_same_period_label = f"{_month_label(m1_y, m1_m)} 1–{period_days}"
            if prev_same_period > 0:
                mom_delta_same_period = round(
                    ((m0_total - prev_same_period) / prev_same_period) * 100, 1
                )

    projected_delta = (
        round(((projected - m1_total) / m1_total) * 100, 1)
        if projected is not None and m1_total > 0 else None
    )

    return {
        "month_total": m0_total,
        "prev_month_total": m1_total,
        "prev_month_same_period": prev_same_period,
        "prev_month_same_period_label": prev_same_period_label,
        "mom_delta_same_period": mom_delta_same_period,
        "projected": projected,
        "projected_source": projected_source,
        "projected_delta": projected_delta,
        "today_cost": today_cost,
        "yesterday_cost": yesterday_cost,
        "today_vs_yesterday_delta": today_vs_yesterday_delta,
        "currency": "USD",
        "by_service": by_service,
        "daily": daily,
        "monthly_history": monthly_history,
        "service_comparison": service_comparison,
        "period": {
            "cur_month": str(m0_start),
            "prev_month": str(m1_start),
            "prev2_month": str(m2_start),
        },
    }


@router.get("/summary")
def get_cost_summary(request: Request, force: bool = False, db: Session = Depends(get_db)):
    session, config = get_session_and_config(request)
    key = make_cache_key("cost", config.access_key or "", config.region)
    try:
        account_id = _get_account_id(session)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))

    def fetcher():
        return _fetch_cost(session, db, account_id, force)

    try:
        return get_cached(key, CACHE_TTL, fetcher, force)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backfill")
def backfill_cost(request: Request, months: int = 12, db: Session = Depends(get_db)):
    """
    Fetch and save past months' cost data to the DB so future summary requests
    use stored data instead of calling AWS for those months. Default 12 months.
    """
    session, _ = get_session_and_config(request)
    try:
        account_id = _get_account_id(session)
    except ClientError as e:
        raise HTTPException(status_code=500, detail=str(e))

    ce = session.client("ce", region_name="us-east-1")
    today = datetime.utcnow().date()
    saved = 0
    y, m = today.year, today.month
    for _ in range(months):
        y, m = _prev_month(y, m)
        if _load_month_from_db(db, account_id, y, m) is not None:
            continue  # already stored
        total, by_service = _fetch_one_month_from_aws(ce, y, m)
        _save_month_to_db(db, account_id, y, m, total, by_service)
        saved += 1

    return {"ok": True, "account_id": account_id, "months_saved": saved}
