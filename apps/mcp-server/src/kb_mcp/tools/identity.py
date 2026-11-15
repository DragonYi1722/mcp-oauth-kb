"""Identity tool: proves which principal the server derived from the token."""

from __future__ import annotations

from mcp.server import MCPServer

from ..audit import AuditLogger
from ..identity import current_actor
from ..settings import get_settings
from .common import run_audited


def register_identity_tools(mcp: MCPServer, audit: AuditLogger) -> None:
    @mcp.tool()
    async def whoami() -> dict[str, object]:
        """Return the identity derived from the verified access token."""

        actor = current_actor()
        settings = get_settings()

        async def operation() -> dict[str, object]:
            return {
                "authenticated": settings.auth_enabled,
                "dev_mode": not settings.auth_enabled,
                "sub": actor.sub,
                "username": actor.username,
                "department": actor.department,
                "roles": list(actor.roles),
                "scopes": list(actor.scopes),
            }

        return await run_audited(audit, actor, "whoami", {}, operation)
