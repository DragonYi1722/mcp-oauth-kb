"""Test-wide isolation: every test starts with an empty rate-limit window."""

import pytest

from kb_mcp.ratelimit import reset_limiter


@pytest.fixture(autouse=True)
def _fresh_rate_limiter() -> None:
    reset_limiter()
    yield
    reset_limiter()
