#!/usr/bin/env python3
"""End-to-end smoke test for the authenticated MCP server.

It logs in through Keycloak (headless authorization code + PKCE), then calls
the real HTTP endpoint and checks the four guarantees that matter most:

  1. no token            -> 401 with a resource_metadata pointer
  2. whoami              -> identity comes from the token, not the arguments
  3. department ACL      -> alice cannot see sales documents
  4. scope authorization -> a read-only token cannot archive

Prerequisites: Keycloak on :8080, MCP server on :8000 with
KB_AUTH_ENABLED=true and KB_STORAGE_BACKEND=postgres, seeded by
`bootstrap_seed.py`.

Usage:
    ./.venv/Scripts/python.exe scripts/smoke_auth.py
"""

from __future__ import annotations

import importlib.util
import json
import secrets
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

SERVER = "http://127.0.0.1:8000"
MCP_URL = f"{SERVER}/mcp"
REDIRECT_URI = "http://localhost:8765/callback"
PROTOCOL_VERSION = "2026-07-28"
META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
}

SCRIPTS = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("get_token", SCRIPTS / "get_token.py")
if _spec is None or _spec.loader is None:  # pragma: no cover - defensive
    raise SystemExit("cannot load get_token.py")
gt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gt)


def fetch_token(username: str, scope: str) -> str:
    """Run authorization code + PKCE without a browser."""

    issuer = gt.DEFAULT_ISSUER
    metadata = gt.fetch_json(f"{issuer}/.well-known/openid-configuration")
    verifier, challenge = gt.pkce_pair()
    state = secrets.token_urlsafe(16)
    url = (
        metadata["authorization_endpoint"]
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": "mcp-cli",
                "redirect_uri": REDIRECT_URI,
                "response_type": "code",
                "scope": scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )
    response = gt.headless_code(url, username, "password", 8765, 30)
    if response.get("state") != state:
        raise SystemExit("state mismatch during headless login")
    tokens = gt.post_form(
        metadata["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": response["code"],
            "redirect_uri": REDIRECT_URI,
            "client_id": "mcp-cli",
            "code_verifier": verifier,
        },
    )
    return str(tokens["access_token"])


def rpc(token: str | None, method: str, params: dict[str, Any]) -> httpx.Response:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Method": method,
    }
    if method == "tools/call":
        headers["Mcp-Name"] = str(params["name"])
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.post(
        MCP_URL,
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {**params, "_meta": META},
        },
        timeout=20,
        trust_env=False,  # never send localhost traffic through a system proxy
    )


def tool_result(response: httpx.Response) -> tuple[bool, Any]:
    """Return (is_error, payload) from a JSON or SSE tool response."""

    response.raise_for_status()
    body: Any
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data_lines = [
            line[5:].strip()
            for line in response.text.splitlines()
            if line.startswith("data:")
        ]
        body = json.loads(data_lines[-1])
    else:
        body = response.json()

    result = body["result"]
    is_error = bool(result.get("isError"))
    structured = result.get("structuredContent")
    if structured is not None:
        return is_error, structured
    text = result.get("content", [{}])[0].get("text", "{}")
    try:
        return is_error, json.loads(text)
    except json.JSONDecodeError:
        return is_error, text


def call(token: str, name: str, arguments: dict[str, Any]) -> tuple[bool, Any]:
    return tool_result(rpc(token, "tools/call", {"name": name, "arguments": arguments}))


def check(label: str, condition: bool, detail: str) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}: {detail}")
    if not condition:
        failures.append(label)


failures: list[str] = []


def main() -> int:
    unauth = rpc(None, "tools/list", {})
    header = unauth.headers.get("www-authenticate", "")
    check(
        "unauthenticated request",
        unauth.status_code == 401,
        f"HTTP {unauth.status_code}",
    )
    check(
        "resource metadata pointer",
        "resource_metadata=" in header,
        header[:120] or "(missing header)",
    )

    read_token = fetch_token("alice", "openid kb.read")
    admin_token = fetch_token("admin", "openid kb.read kb.write kb.admin")

    error, who = call(read_token, "whoami", {})
    subject = who.get("sub") if isinstance(who, dict) else None
    check(
        "whoami uses token identity",
        not error and isinstance(subject, str) and len(subject) == 36,
        json.dumps(who, ensure_ascii=False)[:160],
    )

    error, found = call(read_token, "search_documents", {"query": "复盘"})
    titles = (
        [item["title"] for item in found.get("items", [])]
        if isinstance(found, dict)
        else []
    )
    check(
        "search returns department documents",
        not error and "Q2 复盘" in titles,
        f"{len(titles)} hit(s): {titles}",
    )

    error, sales = call(admin_token, "search_documents", {"query": "销售话术"})
    sales_items = sales.get("items", []) if isinstance(sales, dict) else []
    if not sales_items:
        check("find a sales document", False, "admin search returned nothing")
        return _summary()

    error, hidden = call(
        read_token, "get_document", {"document_id": sales_items[0]["id"]}
    )
    check(
        "cross-department read is hidden",
        error and "NOT_FOUND" in str(hidden),
        str(hidden)[:160],
    )

    error, denied = call(
        read_token,
        "archive_document",
        {"document_id": sales_items[0]["id"], "reason": "smoke test"},
    )
    check(
        "read-only token cannot archive",
        error and "FORBIDDEN_SCOPE" in str(denied),
        str(denied)[:160],
    )

    return _summary()


def _summary() -> int:
    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
