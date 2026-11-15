"""Negative security tests against the real Keycloak + MCP stack.

Run with the stack up:

    pytest -m "security and integration" tests/security/test_live_security.py

These tests reproduce the attack paths from docs/03-安全设计.md: missing and
tampered tokens, scope escalation, IDOR, identity injection, SQL injection,
PKCE downgrade, redirect abuse, oversized input and rate limiting.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import live_helpers as live
import pytest

pytestmark = [pytest.mark.security, pytest.mark.integration]


@pytest.fixture(scope="module")
def stack() -> None:
    if not live.services_ready():
        pytest.skip("Keycloak :8080 or MCP server :8000 is not running")


@pytest.fixture(scope="module")
def alice(stack: None) -> str:
    return live.fetch_token("alice")


@pytest.fixture(scope="module")
def alice_read(stack: None) -> str:
    return live.fetch_token("alice", "openid kb.read")


@pytest.fixture(scope="module")
def bob(stack: None) -> str:
    return live.fetch_token("bob")


@pytest.fixture(scope="module")
def admin(stack: None) -> str:
    return live.fetch_token("admin")


def test_s01_no_token_returns_401_with_prm(stack: None) -> None:
    response = live.rpc(None, "tools/list", {})

    assert response.status_code == 401
    assert "resource_metadata=" in response.headers.get("www-authenticate", "")


def test_s02_tampered_signature_is_rejected(stack: None, alice: str) -> None:
    replacement = "A" if alice[-1] != "A" else "B"
    tampered = alice[:-1] + replacement

    response = live.rpc(tampered, "tools/call", {"name": "whoami", "arguments": {}})

    assert response.status_code == 401


def test_s06_read_only_token_cannot_archive(stack: None, alice_read: str) -> None:
    error, payload = live.call(
        alice_read,
        "archive_document",
        {"document_id": str(uuid4()), "reason": "security test"},
    )

    assert error and "FORBIDDEN_SCOPE" in str(payload)


def test_s07_bob_cannot_read_alices_private_note(stack: None, alice: str, bob: str) -> None:
    error, found = live.call(alice, "search_documents", {"query": "私人笔记"})
    items = found.get("items", []) if isinstance(found, dict) else []
    assert not error and items, f"seed data missing: {found}"

    error, payload = live.call(bob, "get_document", {"document_id": items[0]["id"]})

    assert error and "NOT_FOUND" in str(payload)
    assert items[0]["title"] not in str(payload), "the response must not leak the title"


def test_s08_cross_department_read_is_hidden(stack: None, alice: str, admin: str) -> None:
    error, sales = live.call(admin, "search_documents", {"query": "销售话术"})
    items = sales.get("items", []) if isinstance(sales, dict) else []
    assert not error and items, f"seed data missing: {sales}"

    error, payload = live.call(alice, "get_document", {"document_id": items[0]["id"]})

    assert error and "NOT_FOUND" in str(payload)


def test_s09_identity_fields_in_arguments_are_ignored(
    stack: None, alice: str, bob: str, admin: str
) -> None:
    title = f"security-identity-injection-{uuid4().hex[:8]}"
    arguments = {
        "title": title,
        "content": "attempt to spoof the owner",
        "visibility": "private",
        "owner_id": "admin",
        "sub": "admin",
        "username": "admin",
    }

    error, payload = live.call(alice, "create_document", arguments)

    if error:
        # Strict argument schemas are an acceptable (stronger) outcome.
        assert "unexpected" in str(payload).lower() or "extra" in str(payload).lower()
        return

    document = payload["document"]
    assert document["owner"] == "alice"

    # The document is private to alice: bob must not be able to read it.
    error, hidden = live.call(bob, "get_document", {"document_id": document["id"]})
    assert error and "NOT_FOUND" in str(hidden)

    live.call(
        admin,
        "archive_document",
        {"document_id": document["id"], "reason": "security test cleanup"},
    )


def test_s10_sql_injection_returns_no_rows(stack: None, alice: str) -> None:
    error, payload = live.call(alice, "search_documents", {"query": "' OR 1=1 --"})

    assert not error, payload
    assert payload.get("items") == []


def test_s11_code_without_verifier_is_rejected(stack: None) -> None:
    _, code = live.login_code("alice", "openid kb.read")

    response = live.token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": live.REDIRECT_URI,
            "client_id": live.CLIENT_ID,
        }
    )

    assert response.status_code == 400
    assert response.json().get("error") == "invalid_grant"


def test_s12_unregistered_redirect_uri_is_rejected(stack: None) -> None:
    challenge = "a" * 43
    response = httpx.get(
        live.metadata()["authorization_endpoint"],
        params={
            "client_id": live.CLIENT_ID,
            "redirect_uri": "https://evil.example/cb",
            "response_type": "code",
            "scope": "openid kb.read",
            "state": "security-test",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
        timeout=15,
    )

    assert response.status_code == 400
    assert "evil.example" not in response.headers.get("location", "")


def test_s13_oversized_content_is_rejected(stack: None, alice: str) -> None:
    title = f"oversize-{uuid4().hex[:8]}"
    response = live.rpc(
        alice,
        "tools/call",
        {
            "name": "create_document",
            "arguments": {"title": title, "content": "x" * 1_000_000},
        },
    )

    if response.status_code >= 400:
        rejected = True
    else:
        rejected, _ = live.parse_tool(response)
    assert rejected, "1 MB of content must not be accepted"

    error, found = live.call(alice, "search_documents", {"query": title})
    assert not error and found.get("items") == []


def test_s14_per_subject_rate_limit(stack: None, bob: str) -> None:
    succeeded = 0
    denied: object = None
    for _ in range(70):
        error, payload = live.call(bob, "whoami", {})
        if error:
            denied = payload
            break
        succeeded += 1

    assert denied is not None, "the limiter never triggered"
    assert "RATE_LIMITED" in str(denied)
    assert 50 <= succeeded <= 60, f"unexpected budget: {succeeded} calls"
