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
    include=["app.tasks.health", "app.tasks.imports"],
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
        "imports.confirm_import_job": {"queue": "import"},
    },
    task_queues=(
        Queue("default"),
        Queue("import"),
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
