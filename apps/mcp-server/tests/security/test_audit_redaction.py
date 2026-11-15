"""S-15: audit rows and structured logs carry no token, password or document body."""

from __future__ import annotations

import json
import logging

import pytest

from kb_mcp.audit import InMemoryAuditLogger, digest_args
from kb_mcp.logging_utils import JsonFormatter
from kb_mcp.models import Actor

pytestmark = pytest.mark.security

SECRET_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.signature"
SECRET_BODY = "并购计划：下季度以 1.2 亿收购竞争对手"
ACTOR = Actor(sub="dev-alice", username="alice", department="engineering")


async def test_audit_rows_only_store_a_digest() -> None:
    audit = InMemoryAuditLogger()

    await audit.record(
        actor=ACTOR,
        tool_name="create_document",
        args={"title": "并购计划", "content_len": len(SECRET_BODY), "token": SECRET_TOKEN},
        result="ok",
        error_code=None,
        duration_ms=3,
    )

    serialized = json.dumps(audit.records, ensure_ascii=False)
    assert SECRET_TOKEN not in serialized
    assert SECRET_BODY not in serialized
    assert "并购计划" not in serialized, "titles are hashed with the rest of the args"
    assert len(audit.records[0]["args_digest"]) == 32


def test_digest_is_stable_and_not_reversible() -> None:
    args = {"query": "复盘", "limit": 20}

    assert digest_args(args) == digest_args({"limit": 20, "query": "复盘"})
    assert "复盘" not in digest_args(args)


def test_json_logs_do_not_include_message_payloads_by_default() -> None:
    record = logging.LogRecord(
        name="kb_mcp.audit",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="tool %s finished with %s",
        args=("create_document", "ok"),
        exc_info=None,
    )
    record.request_id = "-"  # type: ignore[attr-defined]

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "tool create_document finished with ok"
    assert SECRET_TOKEN not in json.dumps(payload)
    assert SECRET_BODY not in json.dumps(payload, ensure_ascii=False)
