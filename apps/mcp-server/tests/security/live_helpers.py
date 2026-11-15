"""Helpers for the live security tests (Keycloak on :8080, MCP server on :8000)."""

from __future__ import annotations

import importlib.util
import json
import secrets
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = ROOT / "scripts"

SERVER = "http://127.0.0.1:8000"
MCP_URL = f"{SERVER}/mcp"
ISSUER = "http://localhost:8080/realms/kb"
CLIENT_ID = "mcp-cli"
REDIRECT_URI = "http://localhost:8765/callback"
PROTOCOL_VERSION = "2026-07-28"
DEFAULT_SCOPE = "openid kb.read kb.write kb.admin"
META = {
    "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
    "io.modelcontextprotocol/clientCapabilities": {},
}


def _load_get_token() -> Any:
    spec = importlib.util.spec_from_file_location("get_token", SCRIPTS / "get_token.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("cannot load scripts/get_token.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gt = _load_get_token()


def services_ready() -> bool:
    try:
        httpx.get(f"{SERVER}/healthz", timeout=3).raise_for_status()
        metadata = gt.fetch_json(f"{ISSUER}/.well-known/openid-configuration")
    except Exception:  # noqa: BLE001 - any failure means "not running"
        return False
    return metadata.get("issuer") == ISSUER


def metadata() -> dict[str, Any]:
    payload: dict[str, Any] = gt.fetch_json(f"{ISSUER}/.well-known/openid-configuration")
    return payload


def login_code(username: str, scope: str = DEFAULT_SCOPE) -> tuple[str, str]:
    """Drive the login form and return (code_verifier, authorization_code)."""

    verifier, challenge = gt.pkce_pair()
    state = secrets.token_urlsafe(16)
    url = (
        metadata()["authorization_endpoint"]
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": CLIENT_ID,
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
        raise RuntimeError("state mismatch during headless login")
    return verifier, str(response["code"])


def fetch_token(username: str, scope: str = DEFAULT_SCOPE) -> str:
    verifier, code = login_code(username, scope)
    tokens = gt.post_form(
        metadata()["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        },
    )
    return str(tokens["access_token"])


def token_request(form: dict[str, str]) -> httpx.Response:
    return httpx.post(metadata()["token_endpoint"], data=form, timeout=15)


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
        timeout=30,
    )


def parse_tool(response: httpx.Response) -> tuple[bool, Any]:
    response.raise_for_status()
    body: Any
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        data_lines = [
            line[5:].strip() for line in response.text.splitlines() if line.startswith("data:")
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
    return parse_tool(rpc(token, "tools/call", {"name": name, "arguments": arguments}))
