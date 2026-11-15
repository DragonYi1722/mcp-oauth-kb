"""The audit wrapper records outcomes but never breaks the tool call."""

from typing import Any

import pytest

from kb_mcp.errors import KbError
from kb_mcp.models import Actor
from kb_mcp.tools.common import run_audited

ACTOR = Actor(sub="dev-alice", username="alice", department="engineering")


class RecordingAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


class FailingAudit:
    async def record(self, **kwargs: Any) -> None:
        raise RuntimeError("audit database is down")


async def ok_operation() -> dict[str, bool]:
    return {"ok": True}


async def denied_operation() -> dict[str, bool]:
    raise KbError("missing scope: kb.write", code="FORBIDDEN_SCOPE", http_status=403)


@pytest.mark.asyncio
async def test_successful_call_is_recorded() -> None:
    audit = RecordingAudit()

    result = await run_audited(audit, ACTOR, "whoami", {}, ok_operation)

    assert result == {"ok": True}
    assert audit.records[0]["result"] == "ok"
    assert audit.records[0]["error_code"] is None


@pytest.mark.asyncio
async def test_denied_call_records_the_error_code() -> None:
    audit = RecordingAudit()

    with pytest.raises(KbError):
        await run_audited(audit, ACTOR, "create_document", {}, denied_operation)

    assert audit.records[0]["result"] == "denied"
    assert audit.records[0]["error_code"] == "FORBIDDEN_SCOPE"


@pytest.mark.asyncio
async def test_audit_outage_does_not_fail_a_successful_call() -> None:
    result = await run_audited(
        FailingAudit(),  # type: ignore[arg-type]
        ACTOR,
        "whoami",
        {},
        ok_operation,
    )

    assert result == {"ok": True}
