"""S-14: a single subject cannot exceed its tool-call budget."""

from typing import Any

import pytest

from kb_mcp.errors import KbError
from kb_mcp.models import Actor
from kb_mcp.ratelimit import RateLimiter
from kb_mcp.tools.common import run_audited

pytestmark = pytest.mark.security

ACTOR = Actor(sub="dev-alice", username="alice", department="engineering")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class RecordingAudit:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.records.append(kwargs)


def test_budget_is_per_key() -> None:
    limiter = RateLimiter(limit=2, clock=Clock())

    assert limiter.allow("alice")
    assert limiter.allow("alice")
    assert not limiter.allow("alice")
    assert limiter.allow("bob"), "one user's traffic must not block another"


def test_window_slides() -> None:
    clock = Clock()
    limiter = RateLimiter(limit=1, window_seconds=60, clock=clock)

    assert limiter.allow("alice")
    assert not limiter.allow("alice")

    clock.now += 59
    assert not limiter.allow("alice")

    clock.now += 2
    assert limiter.allow("alice")


def test_zero_limit_disables_the_limiter() -> None:
    limiter = RateLimiter(limit=0, clock=Clock())

    assert all(limiter.allow("alice") for _ in range(200))


async def test_denied_call_is_audited_and_does_not_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = RateLimiter(limit=1, clock=Clock())
    monkeypatch.setattr("kb_mcp.tools.common.get_limiter", lambda: limiter)
    audit = RecordingAudit()
    ran = False

    async def operation() -> dict[str, bool]:
        nonlocal ran
        ran = True
        return {"ok": True}

    assert await run_audited(audit, ACTOR, "search_documents", {}, operation) == {"ok": True}

    with pytest.raises(KbError) as excinfo:
        await run_audited(audit, ACTOR, "search_documents", {}, operation)

    assert excinfo.value.code == "RATE_LIMITED"
    assert excinfo.value.http_status == 429
    assert ran, "the first call should have run"
    assert audit.records[-1]["result"] == "denied"
    assert audit.records[-1]["error_code"] == "RATE_LIMITED"
