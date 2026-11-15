"""JSON logging is opt-in and always carries the request id."""

import json
import logging
import sys

from kb_mcp.logging_utils import JsonFormatter


def test_json_formatter_shape() -> None:
    record = logging.LogRecord(
        name="kb_mcp.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="tool %s finished",
        args=("whoami",),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "kb_mcp.test"
    assert payload["message"] == "tool whoami finished"
    assert payload["request_id"] == "-"
    assert payload["timestamp"].endswith("+00:00")


def test_exception_is_serialized() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("kb_mcp.test", logging.ERROR, __file__, 1, "failed", (), None)
        record.exc_info = sys.exc_info()

    payload = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in payload["exception"]
