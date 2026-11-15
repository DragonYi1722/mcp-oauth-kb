"""Readiness checks: the process is alive (`/healthz`) vs. able to serve.

Readiness covers the storage backend and, when authentication is enabled, the
JWKS endpoint. A missing JWKS is treated as not ready because no new token can
be verified; already cached keys may still work, but the deployment is degraded.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .repository import DocumentRepository

JwksCheck = Callable[[str], Awaitable[bool]]


async def jwks_reachable(url: str, *, max_wait: float = 2.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=max_wait) as client:
            response = await client.get(url)
        if response.status_code != 200:
            return False
        payload: Any = response.json()
        return bool(payload.get("keys"))
    except (httpx.HTTPError, ValueError):
        return False


async def collect_checks(
    repository: DocumentRepository,
    *,
    auth_enabled: bool,
    jwks_uri: str,
    jwks_check: JwksCheck = jwks_reachable,
) -> dict[str, str]:
    checks: dict[str, str] = {}
    try:
        await repository.ping()
        checks["storage"] = "ok"
    except Exception:  # noqa: BLE001 - readiness must never raise
        checks["storage"] = "unavailable"

    if auth_enabled:
        checks["jwks"] = "ok" if await jwks_check(jwks_uri) else "unavailable"

    return checks


def is_ready(checks: dict[str, str]) -> bool:
    return all(status == "ok" for status in checks.values())
