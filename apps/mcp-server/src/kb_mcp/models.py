"""Domain models shared by the repository, services, and MCP tools."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID


class Visibility(StrEnum):
    PRIVATE = "private"
    DEPARTMENT = "department"
    ORG = "org"


class DocStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class Actor:
    """The authenticated principal, derived only from a verified token."""

    sub: str
    username: str
    department: str | None = None
    roles: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    email: str | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def is_manager(self) -> bool:
        return "manager" in self.roles or self.is_admin


@dataclass(slots=True)
class Document:
    id: UUID
    title: str
    content: str
    owner_id: str
    owner_username: str | None
    department: str | None
    visibility: Visibility
    status: DocStatus
    version: int
    created_at: datetime
    updated_at: datetime

    def summary(self, max_chars: int = 200) -> dict[str, object]:
        snippet = " ".join(self.content.split())
        if len(snippet) > max_chars:
            snippet = snippet[: max_chars - 1] + "…"
        return {
            "id": str(self.id),
            "title": self.title,
            "snippet": snippet,
            "owner": self.owner_username or self.owner_id,
            "department": self.department,
            "visibility": self.visibility.value,
            "updated_at": _iso(self.updated_at),
        }

    def to_dict(self, *, include_content: bool = True) -> dict[str, object]:
        data: dict[str, object] = {
            "id": str(self.id),
            "title": self.title,
            "owner": self.owner_username or self.owner_id,
            "department": self.department,
            "visibility": self.visibility.value,
            "status": self.status.value,
            "version": self.version,
            "created_at": _iso(self.created_at),
            "updated_at": _iso(self.updated_at),
        }
        if include_content:
            data["content"] = self.content
        return data


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def utc_now() -> datetime:
    return datetime.now(UTC)
