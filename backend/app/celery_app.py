"""
Celery app for collector tasks. Broker and result backend use Valkey (Redis-compatible).
"""
import os
from celery import Celery

# Use VALKEY_URL as broker if CELERY_BROKER_URL not set (same store as app sessions)
broker_url = os.environ.get("CELERY_BROKER_URL") or os.environ.get("VALKEY_URL") or "redis://localhost:6379/0"
result_backend = os.environ.get("CELERY_RESULT_BACKEND") or broker_url

app = Celery(
    "aws_dashboard",
    broker=broker_url,
    backend=result_backend,
    include=["app.tasks.collect_tasks"],
)

app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Resources: every 6h (describe_* is relatively static; use Refresh for on-demand).
    # Metrics: every 2 min for fresher dashboards; tune to 5–10 min if CloudWatch cost is a concern.
    beat_schedule={
        "collect-resources-every-6h": {
            "task": "app.tasks.collect_tasks.collect_resources_all",
            "schedule": 6 * 60 * 60.0,  # 6 hours
        },
        "collect-metrics-every-2min": {
            "task": "app.tasks.collect_tasks.collect_metrics_all",
            "schedule": 5 * 60.0,  # 5 minutes
        },
        "metrics-retention-72h": {
            "task": "app.tasks.collect_tasks.apply_metrics_retention",
            "schedule": 60 * 60.0,  # every hour
        },
    },
)
