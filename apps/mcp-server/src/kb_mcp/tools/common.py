"""Shared tool plumbing: audit wrapper and error classification."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping

from ..audit import AuditLogger
from ..errors import KbError
from ..models import Actor
from ..ratelimit import get_limiter

logger = logging.getLogger(__name__)
_DENIED_STATUSES = frozenset({401, 403, 404})


async def run_audited[T](
    audit: AuditLogger,
    actor: Actor,
    tool_name: str,
    args: Mapping[str, object],
    operation: Callable[[], Awaitable[T]],
) -> T:
    started = time.perf_counter()
    if not get_limiter().allow(actor.sub):
        await _record_safely(
            audit,
            actor=actor,
            tool_name=tool_name,
            args=args,
            result="denied",
            error_code="RATE_LIMITED",
            duration_ms=_elapsed_ms(started),
        )
        raise KbError(
            "too many tool calls in this window; retry later",
            code="RATE_LIMITED",
            http_status=429,
        )

    try:
        result = await operation()
    except KbError as exc:
        await _record_safely(
            audit,
            actor=actor,
            tool_name=tool_name,
            args=args,
            result="denied" if exc.http_status in _DENIED_STATUSES else "error",
            error_code=exc.code,
            duration_ms=_elapsed_ms(started),
        )
        raise
    except Exception:
        await _record_safely(
            audit,
            actor=actor,
            tool_name=tool_name,
            args=args,
            result="error",
            error_code="INTERNAL",
            duration_ms=_elapsed_ms(started),
        )
        raise

    await _record_safely(
        audit,
        actor=actor,
        tool_name=tool_name,
        args=args,
        result="ok",
        error_code=None,
        duration_ms=_elapsed_ms(started),
    )
    return result


async def _record_safely(
    audit: AuditLogger,
    *,
    actor: Actor,
    tool_name: str,
    args: Mapping[str, object],
    result: str,
    error_code: str | None,
    duration_ms: int,
) -> None:
    """Audit write failures must not turn a successful tool call into an error."""

    try:
        await audit.record(
            actor=actor,
            tool_name=tool_name,
            args=args,
            result=result,
            error_code=error_code,
            duration_ms=duration_ms,
        )
    except Exception:  # noqa: BLE001 - log and keep the tool result
        logger.exception("audit write failed for %s", tool_name)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
