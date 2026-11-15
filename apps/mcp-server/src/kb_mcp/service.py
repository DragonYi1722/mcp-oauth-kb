"""Business rules: pagination, ownership, write permission, and archive policy."""

from __future__ import annotations

import base64
import json
from uuid import UUID

from .errors import KbError
from .models import Actor, Document, Visibility
from .repository import DocumentRepository, can_write

MAX_LIMIT = 50


def parse_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise KbError(
            "document_id must be a UUID", code="VALIDATION_ERROR", http_status=400
        ) from exc


def encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        offset = int(payload["offset"])
        if offset < 0:
            raise ValueError("negative offset")
        return offset
    except Exception as exc:  # noqa: BLE001 - any malformed cursor is a 400
        raise KbError("invalid cursor", code="VALIDATION_ERROR", http_status=400) from exc


async def search(
    actor: Actor,
    repository: DocumentRepository,
    *,
    query: str,
    limit: int,
    cursor: str | None,
) -> dict[str, object]:
    limit = max(1, min(limit, MAX_LIMIT))
    offset = decode_cursor(cursor)
    documents = await repository.search(actor, query, limit + 1, offset)
    has_more = len(documents) > limit
    page = documents[:limit]
    return {
        "items": [document.summary() for document in page],
        "next_cursor": encode_cursor(offset + limit) if has_more else None,
    }


async def get(
    actor: Actor, repository: DocumentRepository, *, document_id: str
) -> dict[str, object]:
    document = await repository.get(actor, parse_uuid(document_id))
    if document is None:
        # 404 for both "missing" and "not allowed": do not leak existence.
        raise KbError("document not found", code="NOT_FOUND", http_status=404)
    return {"document": document.to_dict()}


async def create(
    actor: Actor,
    repository: DocumentRepository,
    *,
    title: str,
    content: str,
    visibility: Visibility,
) -> dict[str, object]:
    document = await repository.create(actor, title, content, visibility)
    return {"document": document.to_dict(include_content=False)}


async def update(
    actor: Actor,
    repository: DocumentRepository,
    *,
    document_id: str,
    expected_version: int,
    title: str | None,
    content: str | None,
    visibility: Visibility | None,
) -> dict[str, object]:
    if title is None and content is None and visibility is None:
        raise KbError(
            "at least one of title, content, visibility is required",
            code="VALIDATION_ERROR",
            http_status=400,
        )

    parsed_id = parse_uuid(document_id)
    await _load_writable(actor, repository, parsed_id)

    updated = await repository.update(parsed_id, expected_version, title, content, visibility)
    return {"document": updated.to_dict(include_content=False)}


async def archive(
    actor: Actor, repository: DocumentRepository, *, document_id: str
) -> dict[str, object]:
    if not actor.is_admin:
        raise KbError("admin role required", code="FORBIDDEN_ROLE", http_status=403)

    parsed_id = parse_uuid(document_id)
    document = await repository.get(actor, parsed_id)
    if document is None:
        # Admins may archive a document they cannot read, as long as they know
        # its id; the action is audited either way.
        document = await repository.get_unscoped(parsed_id)
    if document is None:
        raise KbError("document not found", code="NOT_FOUND", http_status=404)

    archived = await repository.archive(parsed_id)
    return {"archived": True, "document": archived.to_dict(include_content=False)}


async def _load_writable(
    actor: Actor, repository: DocumentRepository, document_id: UUID
) -> Document:
    document = await repository.get(actor, document_id)
    if document is None and actor.is_admin:
        document = await repository.get_unscoped(document_id)
    if document is None:
        raise KbError("document not found", code="NOT_FOUND", http_status=404)
    if not can_write(actor, document):
        # Same status as missing: object-level denial must not confirm existence.
        raise KbError("document not found", code="NOT_FOUND", http_status=404)
    return document
