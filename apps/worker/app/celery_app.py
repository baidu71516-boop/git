"""Celery configuration and task discovery only."""

from backend_core.common.logging import configure_logging
from backend_core.config import get_settings
from celery import Celery
from kombu import Queue

settings = get_settings()
configure_logging(settings.log_level)

celery_app = Celery(
    "influencer_outreach",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.health",
        "app.tasks.imports",
        "app.tasks.targeting",
        "app.tasks.content_activity",
    ],
)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_serializer="json",
    task_default_queue="default",
    task_routes={
        "imports.parse_import_job": {"queue": "import"},
        "imports.parse_import_job_file": {"queue": "import"},
        "imports.preview_import_job": {"queue": "import"},
        "imports.confirm_import_job": {"queue": "import"},
        "imports.reconcile_import_tasks": {"queue": "default"},
        "targeting.materialize_candidate_pool_run": {"queue": "targeting"},
        "targeting.materialize_candidate_pool_run_with_long_inactivity_enrichment": {
            "queue": "analytics"
        },
        "targeting.reconcile_pending_candidate_pool_runs": {"queue": "default"},
        "content_activity.refresh_request": {"queue": "analytics"},
        "content_activity.reconcile_refresh_requests": {"queue": "default"},
    },
    beat_schedule={
        "reconcile-durable-import-tasks": {
            "task": "imports.reconcile_import_tasks",
            "schedule": settings.import_task_reconcile_interval_seconds,
            "options": {"queue": "default"},
        },
        "reconcile-pending-candidate-pool-runs": {
            "task": "targeting.reconcile_pending_candidate_pool_runs",
            "schedule": 60,
            "options": {"queue": "default"},
        },
        "reconcile-content-activity-refresh-requests": {
            "task": "content_activity.reconcile_refresh_requests",
            "schedule": settings.content_activity_refresh_reconcile_interval_seconds,
            "options": {"queue": "default"},
        },
    },
    task_queues=(
        Queue("default"),
        Queue("import"),
        Queue("targeting"),
        Queue("ai"),
        Queue("email"),
        Queue("analytics"),
    ),
    task_serializer="json",
    timezone=settings.business_timezone,
    worker_concurrency=2,
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
)
