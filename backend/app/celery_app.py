"""
Celery app for collector tasks. Broker and result backend use Valkey (Redis-compatible).
"""
import os
from celery import Celery
from app.core.config import settings

# Use VALKEY_URL as broker if CELERY_BROKER_URL not set (same store as app sessions)
broker_url = os.environ.get("CELERY_BROKER_URL") or os.environ.get("VALKEY_URL") or "redis://localhost:6379/0"
result_backend = os.environ.get("CELERY_RESULT_BACKEND") or broker_url

app = Celery(
    "infrawatch",
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
    # Metrics: every 5 min.
    beat_schedule={
        "collect-resources-per-schedule": {
            "task": "app.tasks.collect_tasks.collect_resources_all",
            "schedule": settings.collect_resource_all_secs,
        },
        "collect-metrics-per-schedule": {
            "task": "app.tasks.collect_tasks.collect_metrics_all",
            "schedule": settings.collect_metrics_every_secs,
        },
        "metrics-retention-per-schedule": {
            "task": "app.tasks.collect_tasks.apply_metrics_retention",
            "schedule": settings.metrics_retention_secs,
        },
        "collect-alarms-per-schedule": {
            "task": "app.tasks.collect_tasks.collect_alarms",
            "schedule": settings.collect_alarms_every_secs,
        },
        "collect-health-events-per-schedule": {
            "task": "app.tasks.collect_tasks.collect_health_events",
            "schedule": settings.collect_health_events_every_secs,
        },
    },
)
