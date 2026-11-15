"""Readiness semantics: storage and JWKS must both answer."""

from typing import Any

import pytest

from kb_mcp.health import collect_checks, is_ready


class FakeRepository:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def ping(self) -> None:
        if self.fail:
            raise RuntimeError("database is down")


async def jwks_ok(url: str) -> bool:
    return True


async def jwks_down(url: str) -> bool:
    return False


@pytest.mark.asyncio
async def test_ready_when_storage_and_jwks_answer() -> None:
    checks = await collect_checks(
        FakeRepository(),  # type: ignore[arg-type]
        auth_enabled=True,
        jwks_uri="http://keycloak/certs",
        jwks_check=jwks_ok,
    )

    assert checks == {"storage": "ok", "jwks": "ok"}
    assert is_ready(checks)


@pytest.mark.asyncio
async def test_storage_failure_is_reported() -> None:
    checks = await collect_checks(
        FakeRepository(fail=True),  # type: ignore[arg-type]
        auth_enabled=False,
        jwks_uri="http://keycloak/certs",
    )

    assert checks == {"storage": "unavailable"}
    assert not is_ready(checks)


@pytest.mark.asyncio
async def test_jwks_outage_marks_not_ready() -> None:
    checks = await collect_checks(
        FakeRepository(),  # type: ignore[arg-type]
        auth_enabled=True,
        jwks_uri="http://keycloak/certs",
        jwks_check=jwks_down,
    )

    assert checks["jwks"] == "unavailable"
    assert not is_ready(checks)


@pytest.mark.asyncio
async def test_jwks_is_skipped_when_auth_is_disabled() -> None:
    calls: list[Any] = []

    async def spy(url: str) -> bool:
        calls.append(url)
        return True

    checks = await collect_checks(
        FakeRepository(),  # type: ignore[arg-type]
        auth_enabled=False,
        jwks_uri="http://keycloak/certs",
        jwks_check=spy,
    )

    assert "jwks" not in checks
    assert calls == []
