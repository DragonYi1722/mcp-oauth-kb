"""Optional JSON log lines for production; plain text stays the dev default."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from .request_context import get_request_id


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_json_logging() -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
