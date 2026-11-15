"""Per-subject sliding-window rate limiting for tool calls.

The limit is intentionally in-process and keyed by the verified token subject:
it slows a single abusive caller without adding shared infrastructure. With
multiple replicas each process keeps its own window, so a caller can reach
`replicas × limit` per minute; a production deployment that needs a strict
global quota should move the counter to Redis.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from .settings import get_settings

Clock = Callable[[], float]


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        """Record one call for `key`; return False once the budget is spent."""

        if self.limit <= 0:
            return True

        now = self._clock()
        cutoff = now - self.window_seconds
        events = self._events.setdefault(key, deque())
        while events and events[0] <= cutoff:
            events.popleft()

        if len(events) >= self.limit:
            return False

        events.append(now)
        return True

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._events.clear()
        else:
            self._events.pop(key, None)


_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        settings = get_settings()
        _limiter = RateLimiter(settings.rate_limit_per_minute)
    return _limiter


def configure_limiter(limiter: RateLimiter | None) -> None:
    """Replace the process-wide limiter (tests, or a future config reload)."""

    global _limiter
    _limiter = limiter


def reset_limiter() -> None:
    configure_limiter(None)
