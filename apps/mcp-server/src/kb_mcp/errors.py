"""Domain errors and authorization helpers shared by tools."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from mcp.server.mcpserver.exceptions import ToolError


class HasScopes(Protocol):
    @property
    def scopes(self) -> Sequence[str]: ...


class KbError(ToolError):
    """An anticipated tool failure that maps to a stable error code.

    Subclassing ToolError tells the SDK to return `isError=true` with the
    message instead of logging a traceback and hiding it from the model.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "INTERNAL",
        http_status: int = 500,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.message = message
        self.code = code
        self.http_status = http_status


def require_scope(principal: HasScopes | None, scope: str) -> HasScopes:
    """Return the principal if it carries `scope`, otherwise raise KbError.

    Scope checks live in the tool layer on purpose: HTTP middleware proves
    *who* the caller is, but only the tool knows *what capability* it needs.
    """

    if principal is None:
        raise KbError("authentication required", code="UNAUTHENTICATED", http_status=401)
    if scope not in set(principal.scopes):
        raise KbError(f"missing scope: {scope}", code="FORBIDDEN_SCOPE", http_status=403)
    return principal
