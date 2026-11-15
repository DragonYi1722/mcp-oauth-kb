#!/usr/bin/env python3
"""Week 5 demo: three users, three different views of the same knowledge base.

Logs in as alice (employee), bob (manager) and admin (administrator), calls the
real MCP endpoint with each token, and prints what each user can see. Then it
exercises the denial paths: cross-department reads return NOT_FOUND, and a
read-only token cannot create or archive documents.

Prerequisites: Keycloak + MCP server with KB_AUTH_ENABLED=true, seeded by
`bootstrap_seed.py`.

Usage:
    ./.venv/Scripts/python.exe scripts/demo_week5.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("smoke_auth", SCRIPTS / "smoke_auth.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    raise SystemExit("cannot load smoke_auth.py")
sa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sa)

failures: list[str] = []

EXPECTED = {
    "alice": {
        "Q2 复盘",
        "值班手册",
        "发布流程",
        "架构决策记录",
        "新人指南",
        "Alice 的私人笔记",
    },
    "bob": {"Q2 复盘", "值班手册", "发布流程", "架构决策记录", "新人指南"},
    "admin": {"销售话术", "客户清单", "季度目标", "新人指南"},
}


def check(label: str, condition: bool, detail: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}: {detail}")
    if not condition:
        failures.append(label)


def titles(result: Any) -> set[str]:
    if not isinstance(result, dict):
        return set()
    return {str(item["title"]) for item in result.get("items", [])}


def search(token: str, query: str) -> tuple[bool, Any]:
    return sa.call(token, "search_documents", {"query": query, "limit": 50})


def main() -> int:
    tokens = {
        "alice": sa.fetch_token("alice", "openid kb.read"),
        "bob": sa.fetch_token("bob", "openid kb.read"),
        "admin": sa.fetch_token("admin", "openid kb.read kb.write kb.admin"),
    }

    visible: dict[str, set[str]] = {}
    print("\n=== 可见文档（按 token 身份过滤） ===")
    for username, token in tokens.items():
        error, result = search(token, "。")
        visible[username] = titles(result)
        print(f"{username:6s}: {sorted(visible[username])}")
        check(
            f"{username} 可见集合",
            not error and visible[username] == EXPECTED[username],
            f"{len(visible[username])} 篇",
        )

    print("\n=== 越权读取 ===")
    _, sales = search(tokens["admin"], "销售话术")
    sales_id = sales["items"][0]["id"]
    error, result = sa.call(tokens["alice"], "get_document", {"document_id": sales_id})
    check("alice 读销售部文档", error and "NOT_FOUND" in str(result), str(result))

    _, private = search(tokens["alice"], "私人笔记")
    private_id = private["items"][0]["id"]
    error, result = sa.call(tokens["bob"], "get_document", {"document_id": private_id})
    check("bob 读 alice 私人笔记", error and "NOT_FOUND" in str(result), str(result))

    _, bob_doc = search(tokens["alice"], "发布流程")
    bob_doc_id = bob_doc["items"][0]["id"]
    error, result = sa.call(
        tokens["alice"], "get_document", {"document_id": bob_doc_id}
    )
    check(
        "alice 读同部门 bob 的文档",
        not error and isinstance(result, dict) and "document" in result,
        "允许（department 可见性）",
    )

    print("\n=== 工具级 scope ===")
    error, result = sa.call(
        tokens["alice"],
        "create_document",
        {"title": "不该创建", "content": "只读 token", "visibility": "private"},
    )
    check(
        "只读 token 创建文档", error and "FORBIDDEN_SCOPE" in str(result), str(result)
    )

    error, result = sa.call(
        tokens["admin"],
        "create_document",
        {
            "title": "演示-待归档",
            "content": "admin 创建后立即归档",
            "visibility": "private",
        },
    )
    created = result["document"]["id"] if isinstance(result, dict) else ""
    check("admin 创建文档", not error and bool(created), str(created))

    if created:
        error, result = sa.call(
            tokens["admin"],
            "archive_document",
            {"document_id": created, "reason": "week5 demo cleanup"},
        )
        check(
            "admin 归档文档",
            not error and isinstance(result, dict) and result.get("archived") is True,
            json.dumps(result, ensure_ascii=False)[:120],
        )

    if failures:
        print(f"\n{len(failures)} 项失败：{', '.join(failures)}")
        return 1
    print("\n第 5 周演示全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
