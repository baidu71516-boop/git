"""Structured JSON logging with conservative secret redaction."""

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

SENSITIVE_FRAGMENTS = (
    "password",
    "secret",
    "token",
    "credential",
    "authorization",
    "api_key",
    "master_key",
)


def redact(value: Any) -> Any:
    """Recursively redact mappings whose keys may contain credentials."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if any(fragment in str(key).lower() for fragment in SENSITIVE_FRAGMENTS)
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter suitable for container logs."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False)


def configure_logging(level: str) -> None:
    """Configure root logging once for an API or worker process."""

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
