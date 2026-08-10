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
    include=["app.tasks.health"],
)
celery_app.conf.update(
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    enable_utc=True,
    result_serializer="json",
    task_default_queue="default",
    task_queues=(
        Queue("default"),
        Queue("import"),
        Queue("ai"),
        Queue("email"),
        Queue("analytics"),
    ),
    task_serializer="json",
    timezone=settings.business_timezone,
    worker_hijack_root_logger=False,
)
