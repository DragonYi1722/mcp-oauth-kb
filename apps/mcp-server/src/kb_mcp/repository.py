"""Document storage.

Two implementations share one interface:

- `InMemoryRepository` lets the project run before Docker is installed and
  keeps unit tests fast.
- `PostgresRepository` is the real store used from week 3 onward.

Visibility is enforced in the repository, so it is impossible to forget the
filter in a new tool. Write permission is checked by the service layer.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Protocol
from uuid import UUID, uuid4

import asyncpg

from .errors import KbError
from .models import Actor, DocStatus, Document, Visibility, utc_now

_DOCUMENT_SELECT = """
SELECT d.id,
       d.title,
       d.content,
       d.owner_id,
       u.username AS owner_username,
       dep.name AS department,
       d.visibility::text AS visibility,
       d.status::text AS status,
       d.version,
       d.created_at,
       d.updated_at
FROM documents d
LEFT JOIN users u ON u.id = d.owner_id
LEFT JOIN departments dep ON dep.id = d.department_id
"""

# $1 = actor.sub
_VISIBLE_TO_ACTOR = """
(
    d.owner_id = $1
    OR d.visibility = 'org'
    OR (
        d.visibility = 'department'
        AND d.department_id = (SELECT department_id FROM users WHERE id = $1)
    )
)
"""


class DocumentRepository(Protocol):
    async def ensure_user(self, actor: Actor) -> None: ...

    async def search(self, actor: Actor, query: str, limit: int, offset: int) -> list[Document]: ...

    async def get(self, actor: Actor, document_id: UUID) -> Document | None: ...

    async def get_unscoped(self, document_id: UUID) -> Document | None: ...

    async def ping(self) -> None: ...

    async def create(
        self, actor: Actor, title: str, content: str, visibility: Visibility
    ) -> Document: ...

    async def update(
        self,
        document_id: UUID,
        expected_version: int,
        title: str | None,
        content: str | None,
        visibility: Visibility | None,
    ) -> Document: ...

    async def archive(self, document_id: UUID) -> Document: ...


def can_view(actor: Actor, document: Document) -> bool:
    if document.status is not DocStatus.ACTIVE:
        return False
    if document.owner_id == actor.sub:
        return True
    if document.visibility is Visibility.ORG:
        return True
    return (
        document.visibility is Visibility.DEPARTMENT
        and actor.department is not None
        and document.department == actor.department
    )


def can_write(actor: Actor, document: Document) -> bool:
    if document.owner_id == actor.sub:
        return True
    if actor.is_admin:
        return True
    return (
        actor.is_manager
        and actor.department is not None
        and document.department == actor.department
    )


class InMemoryRepository:
    """Deterministic demo data + simple substring search."""

    def __init__(self, documents: list[Document] | None = None) -> None:
        self._documents: dict[UUID, Document] = {doc.id: doc for doc in documents or []}
        self._users: dict[str, Actor] = {}

    async def ping(self) -> None:
        return None

    @classmethod
    def with_demo_data(cls) -> InMemoryRepository:
        now = utc_now()
        alice = ("dev-alice", "alice", "engineering")
        bob = ("dev-bob", "bob", "engineering")
        admin = ("dev-admin", "admin", "it")

        rows = [
            (
                "Q2 复盘",
                "本季度完成了知识库迁移与权限模型评审。",
                alice,
                "engineering",
                Visibility.DEPARTMENT,
            ),
            (
                "值班手册",
                "线上故障先看监控，再查发布记录，最后联系值班经理。",
                alice,
                "engineering",
                Visibility.DEPARTMENT,
            ),
            (
                "发布流程",
                "所有发布必须经过代码评审、CI 与灰度验证。",
                bob,
                "engineering",
                Visibility.DEPARTMENT,
            ),
            (
                "架构决策记录",
                "MCP Server 只做资源服务器，授权由 Keycloak 负责。",
                bob,
                "engineering",
                Visibility.DEPARTMENT,
            ),
            (
                "新人指南",
                "入职第一周完成环境搭建与安全培训。",
                alice,
                "engineering",
                Visibility.ORG,
            ),
            (
                "Alice 的私人笔记",
                "这是只有 alice 能看到的草稿。",
                alice,
                "engineering",
                Visibility.PRIVATE,
            ),
            ("销售话术", "先理解客户痛点，再介绍产品能力。", admin, "sales", Visibility.DEPARTMENT),
            ("客户清单", "客户信息属于销售部内部资料。", admin, "sales", Visibility.DEPARTMENT),
            ("季度目标", "本季度重点是提升续约率。", admin, "sales", Visibility.DEPARTMENT),
        ]
        documents = [
            Document(
                id=UUID(f"00000000-0000-0000-0000-{index:012d}"),
                title=title,
                content=content,
                owner_id=owner[0],
                owner_username=owner[1],
                department=department,
                visibility=visibility,
                status=DocStatus.ACTIVE,
                version=1,
                created_at=now - timedelta(days=10 - index),
                updated_at=now - timedelta(days=10 - index),
            )
            for index, (title, content, owner, department, visibility) in enumerate(rows, start=1)
        ]
        return cls(documents)

    async def ensure_user(self, actor: Actor) -> None:
        self._users[actor.sub] = actor

    async def search(self, actor: Actor, query: str, limit: int, offset: int) -> list[Document]:
        needle = query.casefold()
        matches = [
            doc
            for doc in self._documents.values()
            if can_view(actor, doc)
            and (needle in doc.title.casefold() or needle in doc.content.casefold())
        ]
        matches.sort(key=lambda doc: (doc.updated_at, str(doc.id)), reverse=True)
        return matches[offset : offset + limit]

    async def get(self, actor: Actor, document_id: UUID) -> Document | None:
        document = self._documents.get(document_id)
        if document is None or not can_view(actor, document):
            return None
        return document

    async def get_unscoped(self, document_id: UUID) -> Document | None:
        return self._documents.get(document_id)

    async def create(
        self, actor: Actor, title: str, content: str, visibility: Visibility
    ) -> Document:
        now = utc_now()
        if visibility is Visibility.DEPARTMENT and actor.department is None:
            visibility = Visibility.PRIVATE
        document = Document(
            id=uuid4(),
            title=title.strip(),
            content=content,
            owner_id=actor.sub,
            owner_username=actor.username,
            department=actor.department,
            visibility=visibility,
            status=DocStatus.ACTIVE,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._documents[document.id] = document
        return document

    async def update(
        self,
        document_id: UUID,
        expected_version: int,
        title: str | None,
        content: str | None,
        visibility: Visibility | None,
    ) -> Document:
        document = self._documents.get(document_id)
        if document is None:
            raise KbError("document not found", code="NOT_FOUND", http_status=404)
        if document.version != expected_version:
            raise KbError(
                f"version conflict: expected {document.version}",
                code="CONFLICT",
                http_status=409,
            )

        if title is not None:
            document.title = title.strip()
        if content is not None:
            document.content = content
        if visibility is not None:
            if visibility is Visibility.DEPARTMENT and document.department is None:
                visibility = Visibility.PRIVATE
            document.visibility = visibility
        document.version += 1
        document.updated_at = utc_now()
        return document

    async def archive(self, document_id: UUID) -> Document:
        document = self._documents.get(document_id)
        if document is None:
            raise KbError("document not found", code="NOT_FOUND", http_status=404)
        if document.status is DocStatus.ARCHIVED:
            return document
        document.status = DocStatus.ARCHIVED
        document.version += 1
        document.updated_at = utc_now()
        return document


class PostgresRepository:
    """asyncpg-backed store. The pool is created lazily on first use."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def ping(self) -> None:
        pool = await self.get_pool()
        await pool.fetchval("SELECT 1")

    async def ensure_user(self, actor: Actor) -> None:
        pool = await self.get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO users (id, username, email, department_id, last_seen_at)
                VALUES (
                    $1, $2, $3,
                    (SELECT id FROM departments WHERE name = $4),
                    now()
                )
                ON CONFLICT (id) DO UPDATE SET
                    username = EXCLUDED.username,
                    email = COALESCE(EXCLUDED.email, users.email),
                    department_id = COALESCE(EXCLUDED.department_id, users.department_id),
                    last_seen_at = now()
                """,
                actor.sub,
                actor.username,
                actor.email,
                actor.department,
            )

    async def search(self, actor: Actor, query: str, limit: int, offset: int) -> list[Document]:
        pool = await self.get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                f"""
                {_DOCUMENT_SELECT}
                WHERE d.status = 'active'
                  AND {_VISIBLE_TO_ACTOR}
                  AND (
                      strpos(lower(d.title), lower($2)) > 0
                      OR strpos(lower(d.content), lower($2)) > 0
                  )
                ORDER BY d.updated_at DESC, d.id DESC
                LIMIT $3 OFFSET $4
                """,
                actor.sub,
                query,
                limit,
                offset,
            )
        return [_row_to_document(row) for row in rows]

    async def get(self, actor: Actor, document_id: UUID) -> Document | None:
        pool = await self.get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_DOCUMENT_SELECT}
                WHERE d.id = $2 AND d.status = 'active' AND {_VISIBLE_TO_ACTOR}
                """,
                actor.sub,
                document_id,
            )
        return _row_to_document(row) if row else None

    async def get_unscoped(self, document_id: UUID) -> Document | None:
        pool = await self.get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(f"{_DOCUMENT_SELECT} WHERE d.id = $1", document_id)
        return _row_to_document(row) if row else None

    async def create(
        self, actor: Actor, title: str, content: str, visibility: Visibility
    ) -> Document:
        if visibility is Visibility.DEPARTMENT and actor.department is None:
            visibility = Visibility.PRIVATE
        pool = await self.get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO documents
                    (title, content, owner_id, department_id, visibility)
                VALUES (
                    $1, $2, $3,
                    (SELECT id FROM departments WHERE name = $4),
                    $5::visibility
                )
                RETURNING id
                """,
                title.strip(),
                content,
                actor.sub,
                actor.department,
                visibility.value,
            )
        document = await self.get_unscoped(row["id"])
        if document is None:  # pragma: no cover - insert just succeeded
            raise KbError("document not found after insert", code="INTERNAL")
        return document

    async def update(
        self,
        document_id: UUID,
        expected_version: int,
        title: str | None,
        content: str | None,
        visibility: Visibility | None,
    ) -> Document:
        pool = await self.get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE documents
                SET title = COALESCE($3, title),
                    content = COALESCE($4, content),
                    visibility = COALESCE($5::visibility, visibility),
                    version = version + 1,
                    updated_at = now()
                WHERE id = $1 AND version = $2
                RETURNING id
                """,
                document_id,
                expected_version,
                title.strip() if title is not None else None,
                content,
                visibility.value if visibility is not None else None,
            )
            if row is None:
                current = await connection.fetchrow(
                    "SELECT version FROM documents WHERE id = $1", document_id
                )
                if current is None:
                    raise KbError("document not found", code="NOT_FOUND", http_status=404)
                raise KbError(
                    f"version conflict: expected {current['version']}",
                    code="CONFLICT",
                    http_status=409,
                )

        document = await self.get_unscoped(document_id)
        if document is None:  # pragma: no cover - update just succeeded
            raise KbError("document not found after update", code="INTERNAL")
        return document

    async def archive(self, document_id: UUID) -> Document:
        pool = await self.get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                UPDATE documents
                SET status = 'archived',
                    version = version + 1,
                    updated_at = now()
                WHERE id = $1 AND status = 'active'
                """,
                document_id,
            )
        document = await self.get_unscoped(document_id)
        if document is None:
            raise KbError("document not found", code="NOT_FOUND", http_status=404)
        return document


def _row_to_document(row: asyncpg.Record) -> Document:
    return Document(
        id=row["id"],
        title=row["title"],
        content=row["content"],
        owner_id=row["owner_id"],
        owner_username=row["owner_username"],
        department=row["department"],
        visibility=Visibility(row["visibility"]),
        status=DocStatus(row["status"]),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
