"""Audit logging. Arguments are hashed; token and document bodies never land here."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Protocol

from .models import Actor, utc_now
from .repository import PostgresRepository
from .request_context import get_request_id


class AuditLogger(Protocol):
    async def record(
        self,
        *,
        actor: Actor,
        tool_name: str,
        args: Mapping[str, object],
        result: str,
        error_code: str | None,
        duration_ms: int,
    ) -> None: ...


def digest_args(args: Mapping[str, object]) -> str:
    payload = json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class InMemoryAuditLogger:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def record(
        self,
        *,
        actor: Actor,
        tool_name: str,
        args: Mapping[str, object],
        result: str,
        error_code: str | None,
        duration_ms: int,
    ) -> None:
        self.records.append(
            {
                "created_at": utc_now().isoformat(),
                "request_id": get_request_id(),
                "actor_sub": actor.sub,
                "actor_username": actor.username,
                "tool_name": tool_name,
                "args_digest": digest_args(args),
                "result": result,
                "error_code": error_code,
                "duration_ms": duration_ms,
            }
        )


class PostgresAuditLogger:
    def __init__(self, repository: PostgresRepository) -> None:
        self._repository = repository

    async def record(
        self,
        *,
        actor: Actor,
        tool_name: str,
        args: Mapping[str, object],
        result: str,
        error_code: str | None,
        duration_ms: int,
    ) -> None:
        pool = await self._repository.get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO audit_logs
                    (request_id, actor_sub, actor_username, tool_name,
                     args_digest, result, error_code, duration_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                get_request_id(),
                actor.sub,
                actor.username,
                tool_name,
                digest_args(args),
                result,
                error_code,
                duration_ms,
            )
