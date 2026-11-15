#!/usr/bin/env python3
"""Show recent audit rows from PostgreSQL.

Every tool call is written to `audit_logs` with a request id, the actor's sub,
the tool name, a SHA-256 of the arguments, result, error code and duration.
This script is the read side used in the demo: it proves a call can be traced
without exposing document bodies or tokens.

Usage:
    ./.venv/Scripts/python.exe scripts/audit_report.py
    ./.venv/Scripts/python.exe scripts/audit_report.py --user alice --result denied
    ./.venv/Scripts/python.exe scripts/audit_report.py --tool archive_document --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import asyncpg

DSN = os.getenv("KB_DATABASE_URL", "postgresql://kb:kb@localhost:5432/kb")


async def fetch(
    *, limit: int, user: str | None, tool: str | None, result: str | None
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    values: list[Any] = []
    if user:
        values.append(user)
        conditions.append(f"actor_username = ${len(values)}")
    if tool:
        values.append(tool)
        conditions.append(f"tool_name = ${len(values)}")
    if result:
        values.append(result)
        conditions.append(f"result = ${len(values)}")
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    connection = await asyncpg.connect(DSN)
    try:
        rows = await connection.fetch(
            f"""
            SELECT created_at, request_id, actor_username, tool_name,
                   result, error_code, duration_ms
            FROM audit_logs
            {where}
            ORDER BY id DESC
            LIMIT ${len(values) + 1}
            """,
            *values,
            limit,
        )
    finally:
        await connection.close()
    return [dict(row) for row in rows]


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("no audit rows matched")
        return
    header = ("time", "request_id", "user", "tool", "result", "error", "ms")
    print(
        f"{header[0]:<20} {header[1]:<14} {header[2]:<8} {header[3]:<18} "
        f"{header[4]:<7} {header[5]:<18} {header[6]:>5}"
    )
    for row in rows:
        timestamp = row["created_at"].astimezone().strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"{timestamp:<20} {str(row['request_id'])[:12]:<14} "
            f"{row['actor_username'] or '-'!s:<8} {row['tool_name']:<18} "
            f"{row['result']:<7} {row['error_code'] or '-'!s:<18} "
            f"{row['duration_ms']:>5}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--user")
    parser.add_argument("--tool")
    parser.add_argument("--result", choices=("ok", "denied", "error"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rows = asyncio.run(
        fetch(
            limit=max(1, min(args.limit, 200)),
            user=args.user,
            tool=args.tool,
            result=args.result,
        )
    )
    if args.json:
        print(json.dumps(rows, default=str, ensure_ascii=False, indent=2))
    else:
        print_table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
