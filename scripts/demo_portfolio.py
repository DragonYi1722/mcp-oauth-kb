#!/usr/bin/env python3
"""Run the three-minute demo story against the live stack.

Order matches docs/05-演示脚本.md:

  1. no token          -> 401 + RFC 9728 metadata pointer
  2. alice logs in     -> authorization code + PKCE (headless)
  3. whoami            -> identity comes from the token
  4. search            -> only department documents
  5. create            -> owner is the token subject
  6. cross-department  -> NOT_FOUND
  7. archive with read -> FORBIDDEN_SCOPE
  8. audit             -> command to show the denied rows

Prerequisites: Keycloak on :8080, MCP server on :8000 with
KB_AUTH_ENABLED=true and KB_STORAGE_BACKEND=postgres, seeded by
`bootstrap_seed.py`.

    ./.venv/Scripts/python.exe scripts/demo_portfolio.py
    DEMO_PAUSE=0 ./.venv/Scripts/python.exe scripts/demo_portfolio.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("smoke_auth", SCRIPTS / "smoke_auth.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    raise SystemExit("cannot load smoke_auth.py")
smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(smoke)

PAUSE = float(os.environ.get("DEMO_PAUSE", "0.8"))


def section(number: int, title: str) -> None:
    print(f"\n[{number}] {title}")
    print("-" * 64)


def pause() -> None:
    if PAUSE > 0:
        time.sleep(PAUSE)


def show(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def main() -> int:
    section(1, "未登录：401 与受保护资源元数据")
    unauth = smoke.rpc(None, "tools/list", {})
    print(f"HTTP {unauth.status_code}")
    print(unauth.headers.get("www-authenticate", "(缺少 WWW-Authenticate)").strip())
    pause()

    section(2, "alice 用授权码 + PKCE 登录")
    read_token = smoke.fetch_token("alice", "openid kb.read kb.write")
    admin_token = smoke.fetch_token("admin", "openid kb.read kb.write kb.admin")
    print("拿到 access token（alice: kb.read + kb.write，没有 kb.admin）")
    pause()

    section(3, "whoami：身份来自 token，不来自参数")
    err_who, who = smoke.call(read_token, "whoami", {"user_id": "admin"})
    if err_who:
        print(f"whoami 失败: {show(who)}")
        return 1
    print(f"username   : {who.get('username')}")
    print(f"department : {who.get('department')}")
    print(f"scopes     : {' '.join(who.get('scopes', []))}")
    print(f"sub        : {who.get('sub')}")
    pause()

    section(4, "检索：只返回工程部可见的文档")
    err_search, found = smoke.call(read_token, "search_documents", {"query": "复盘"})
    titles = (
        [item["title"] for item in found.get("items", [])]
        if isinstance(found, dict)
        else []
    )
    print(f"{len(titles)} 条命中: {titles}")
    pause()

    section(5, "新建文档：owner 永远是 token 的 sub")
    err_create, created = smoke.call(
        read_token,
        "create_document",
        {
            "title": "演示：周会纪要",
            "content": "这是演示脚本创建的文档，owner 来自 token 的 sub。",
            "visibility": "department",
        },
    )
    if err_create:
        print(f"创建失败: {show(created)}")
        return 1
    document = created.get("document", {}) if isinstance(created, dict) else {}
    document_id = str(document.get("id", ""))
    print(f"document_id: {document_id}")
    print(f"owner      : {document.get('owner')}")
    pause()

    section(6, "跨部门读取：返回 NOT_FOUND，不暴露文档是否存在")
    _, sales = smoke.call(admin_token, "search_documents", {"query": "销售话术"})
    items = sales.get("items", []) if isinstance(sales, dict) else []
    if not items:
        print("admin 没有搜到销售部文档，请先运行 bootstrap_seed.py --reset")
        return 1
    sales_id = str(items[0]["id"])
    err_get, hidden = smoke.call(read_token, "get_document", {"document_id": sales_id})
    print(f"读取销售部文档 {sales_id[:8]}... -> {show(hidden)[:160]}")
    pause()

    section(7, "只读 token 归档：FORBIDDEN_SCOPE")
    err_archive, denied = smoke.call(
        read_token,
        "archive_document",
        {"document_id": sales_id, "reason": "演示越权"},
    )
    print(f"结果: {show(denied)[:160]}")
    pause()

    section(8, "审计：每次调用都可追溯")
    print("运行下面这条命令查看刚才的 ok / denied 记录：")
    print("  python scripts/audit_report.py --limit 6")

    ok = (
        unauth.status_code == 401
        and not err_who
        and not err_search
        and not err_create
        and err_get
        and err_archive
        and bool(titles)
        and bool(document_id)
        and "NOT_FOUND" in show(hidden)
        and "FORBIDDEN_SCOPE" in show(denied)
    )
    print("\n演示流程完成。" if ok else "\n有步骤未达到预期，请检查服务端日志。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
