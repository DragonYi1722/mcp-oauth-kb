"""The correlation id must reach the audit logger without threading it through."""

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from kb_mcp.audit import InMemoryAuditLogger
from kb_mcp.models import Actor
from kb_mcp.request_context import RequestIdMiddleware, get_request_id

ACTOR = Actor(sub="dev-alice", username="alice", department="engineering")


def build_app(seen: list[str], audit: InMemoryAuditLogger | None = None) -> Starlette:
    async def endpoint(request: Request) -> JSONResponse:
        seen.append(get_request_id())
        if audit is not None:
            await audit.record(
                actor=ACTOR,
                tool_name="whoami",
                args={},
                result="ok",
                error_code=None,
                duration_ms=0,
            )
        return JSONResponse({"ok": True})

    app = Starlette(routes=[Route("/x", endpoint)])
    app.add_middleware(RequestIdMiddleware)
    return app


def test_client_supplied_request_id_is_reused() -> None:
    seen: list[str] = []
    client = TestClient(build_app(seen))

    response = client.get("/x", headers={"X-Request-ID": "trace-abc-123"})

    assert response.headers["x-request-id"] == "trace-abc-123"
    assert seen == ["trace-abc-123"]


def test_missing_header_gets_a_generated_id() -> None:
    seen: list[str] = []
    client = TestClient(build_app(seen))

    response = client.get("/x")

    assert len(response.headers["x-request-id"]) == 32
    assert seen == [response.headers["x-request-id"]]


@pytest.mark.asyncio
async def test_audit_row_carries_the_request_id() -> None:
    seen: list[str] = []
    audit = InMemoryAuditLogger()
    client = TestClient(build_app(seen, audit))

    client.get("/x", headers={"X-Request-ID": "trace-audit"})

    assert audit.records[0]["request_id"] == "trace-audit"
