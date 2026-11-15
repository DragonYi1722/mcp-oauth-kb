"""MCP server entrypoint.

Week 2 wires the document tools to a storage backend. Authentication is still
disabled by default so the tools can be exercised before Keycloak is running;
set KB_AUTH_ENABLED=true in week 4.
"""

from __future__ import annotations

import logging

import uvicorn
from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .audit import AuditLogger, InMemoryAuditLogger, PostgresAuditLogger
from .auth import build_auth_settings, build_token_verifier
from .health import collect_checks, is_ready
from .logging_utils import configure_json_logging
from .repository import DocumentRepository, InMemoryRepository, PostgresRepository
from .request_context import RequestIdMiddleware
from .settings import get_settings
from .tools import register_tools

settings = get_settings()
auth_enabled = settings.auth_enabled

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
if settings.log_json:
    configure_json_logging()

logger = logging.getLogger(__name__)


def _build_repository() -> DocumentRepository:
    if settings.storage_backend == "postgres":
        return PostgresRepository(settings.database_url)
    if settings.seed_demo_data:
        return InMemoryRepository.with_demo_data()
    return InMemoryRepository()


def _build_audit(repository: DocumentRepository) -> AuditLogger:
    if isinstance(repository, PostgresRepository):
        return PostgresAuditLogger(repository)
    return InMemoryAuditLogger()


mcp = MCPServer(
    name="kb",
    title="Secure Knowledge Base",
    version="0.2.0",
    instructions=(
        "Enterprise knowledge base tools. Every call acts on behalf of the "
        "authenticated user; never trust a user id supplied in tool arguments."
    ),
    token_verifier=build_token_verifier(settings) if auth_enabled else None,
    auth=build_auth_settings(settings) if auth_enabled else None,
    log_level="INFO",
)

repository = _build_repository()
audit = _build_audit(repository)
register_tools(mcp, repository, audit)


@mcp.tool()
async def echo(message: str) -> dict[str, str]:
    """Echo a message back. Used to verify the MCP connection."""

    return {"message": message}


@mcp.custom_route("/healthz", methods=["GET"])  # type: ignore[untyped-decorator]
async def healthz(request: Request) -> Response:
    """Liveness probe. Custom routes are intentionally unauthenticated."""

    return JSONResponse(
        {
            "status": "ok",
            "version": "0.2.0",
            "auth_enabled": auth_enabled,
            "storage_backend": settings.storage_backend,
        }
    )


@mcp.custom_route("/readyz", methods=["GET"])  # type: ignore[untyped-decorator]
async def readyz(request: Request) -> Response:
    """Readiness probe: storage reachable, and JWKS reachable when auth is on."""

    checks = await collect_checks(
        repository,
        auth_enabled=auth_enabled,
        jwks_uri=settings.jwks_uri,
    )
    ready = is_ready(checks)
    return JSONResponse(
        {
            "status": "ready" if ready else "not_ready",
            "checks": checks,
        },
        status_code=200 if ready else 503,
    )


def main() -> None:
    logger.info(
        "starting kb-mcp on %s:%s (auth_enabled=%s, storage=%s, resource=%s)",
        settings.host,
        settings.port,
        auth_enabled,
        settings.storage_backend,
        settings.resource_url,
    )
    # Build the app explicitly (instead of mcp.run) so the request-id
    # middleware wraps every route, including the custom probes.
    app = mcp.streamable_http_app(
        host=settings.host,
        streamable_http_path="/mcp",
        # MCP 2026-07-28 is stateless: any replica can answer any request.
        stateless_http=True,
    )
    app.add_middleware(RequestIdMiddleware)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
