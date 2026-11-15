"""The five document tools, with scope checks and audit logging around each call."""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server import MCPServer
from pydantic import Field

from .. import service
from ..audit import AuditLogger
from ..errors import require_scope
from ..identity import current_actor
from ..models import Visibility
from ..repository import DocumentRepository
from .common import run_audited

VisibilityLiteral = Literal["private", "department", "org"]


def register_document_tools(
    mcp: MCPServer, repository: DocumentRepository, audit: AuditLogger
) -> None:
    @mcp.tool()
    async def search_documents(
        query: Annotated[str, Field(min_length=1, max_length=512)],
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """Search documents the current user is allowed to see."""

        actor = current_actor()
        await repository.ensure_user(actor)
        args: dict[str, object] = {"query": query, "limit": limit}

        async def operation() -> dict[str, object]:
            require_scope(actor, "kb.read")
            return await service.search(actor, repository, query=query, limit=limit, cursor=cursor)

        return await run_audited(audit, actor, "search_documents", args, operation)

    @mcp.tool()
    async def get_document(document_id: str) -> dict[str, object]:
        """Read one document by id. Missing and forbidden both return NOT_FOUND."""

        actor = current_actor()
        await repository.ensure_user(actor)
        args = {"document_id": document_id}

        async def operation() -> dict[str, object]:
            require_scope(actor, "kb.read")
            return await service.get(actor, repository, document_id=document_id)

        return await run_audited(audit, actor, "get_document", args, operation)

    @mcp.tool()
    async def create_document(
        title: Annotated[str, Field(min_length=1, max_length=200)],
        content: Annotated[str, Field(min_length=1, max_length=100_000)],
        visibility: VisibilityLiteral = "department",
    ) -> dict[str, object]:
        """Create a document. The owner is always the authenticated subject."""

        actor = current_actor()
        await repository.ensure_user(actor)
        args: dict[str, object] = {
            "title": title,
            "content_len": len(content),
            "visibility": visibility,
        }

        async def operation() -> dict[str, object]:
            require_scope(actor, "kb.write")
            return await service.create(
                actor,
                repository,
                title=title,
                content=content,
                visibility=Visibility(visibility),
            )

        return await run_audited(audit, actor, "create_document", args, operation)

    @mcp.tool()
    async def update_document(
        document_id: str,
        version: Annotated[int, Field(ge=1)],
        title: Annotated[str, Field(min_length=1, max_length=200)] | None = None,
        content: Annotated[str, Field(min_length=1, max_length=100_000)] | None = None,
        visibility: VisibilityLiteral | None = None,
    ) -> dict[str, object]:
        """Update a document using optimistic concurrency (`version`)."""

        actor = current_actor()
        await repository.ensure_user(actor)
        args: dict[str, object] = {
            "document_id": document_id,
            "version": version,
            "fields": [
                name
                for name, value in (
                    ("title", title),
                    ("content", content),
                    ("visibility", visibility),
                )
                if value is not None
            ],
        }

        async def operation() -> dict[str, object]:
            require_scope(actor, "kb.write")
            return await service.update(
                actor,
                repository,
                document_id=document_id,
                expected_version=version,
                title=title,
                content=content,
                visibility=Visibility(visibility) if visibility is not None else None,
            )

        return await run_audited(audit, actor, "update_document", args, operation)

    @mcp.tool()
    async def archive_document(
        document_id: str,
        reason: Annotated[str, Field(min_length=3, max_length=200)],
    ) -> dict[str, object]:
        """Archive a document. Requires both the kb.admin scope and the admin role."""

        actor = current_actor()
        await repository.ensure_user(actor)
        args = {"document_id": document_id, "reason": reason}

        async def operation() -> dict[str, object]:
            require_scope(actor, "kb.admin")
            return await service.archive(actor, repository, document_id=document_id)

        return await run_audited(audit, actor, "archive_document", args, operation)
