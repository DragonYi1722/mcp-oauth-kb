"""Static checks on the Keycloak realm import.

These assertions catch the configuration mistakes that are expensive to debug
in a browser: a missing audience mapper, a scope that names the wrong resource,
a client that still allows the implicit flow, or a user without a department.
"""

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
REALM = json.loads((REPO_ROOT / "infra" / "keycloak" / "realm-kb.json").read_text(encoding="utf-8"))
RESOURCE = "http://localhost:8000/mcp"
BUILTIN_SCOPES = {
    "profile",
    "email",
    "roles",
    "web-origins",
    "acr",
    "basic",
    "offline_access",
}


def client_scopes() -> dict[str, dict[str, Any]]:
    return {scope["name"]: scope for scope in REALM["clientScopes"]}


def audience_mappers() -> list[dict[str, Any]]:
    return [
        mapper
        for scope in REALM["clientScopes"]
        for mapper in scope.get("protocolMappers", [])
        if mapper["protocolMapper"] == "oidc-audience-mapper"
    ]


def test_realm_and_users() -> None:
    assert REALM["realm"] == "kb"
    assert REALM["enabled"] is True

    users = {user["username"]: user for user in REALM["users"]}
    assert set(users) == {"alice", "bob", "admin"}
    assert all(user["attributes"]["department"] for user in users.values())
    assert all(user["enabled"] for user in users.values())


def test_user_roles_exist() -> None:
    realm_roles = {role["name"] for role in REALM["roles"]["realm"]}
    for user in REALM["users"]:
        assert set(user.get("realmRoles", [])) <= realm_roles


def test_every_scope_required_by_the_server_exists() -> None:
    names = set(client_scopes())
    assert {"kb.read", "kb.write", "kb.admin", "kb.profile"} <= names

    for name in ("kb.read", "kb.write", "kb.admin"):
        assert client_scopes()[name]["attributes"]["include.in.token.scope"] == "true"


def test_audience_mappers_bind_the_mcp_resource() -> None:
    mappers = audience_mappers()
    assert len(mappers) == 3
    for mapper in mappers:
        config = mapper["config"]
        assert config["included.custom.audience"] == RESOURCE
        assert config["access.token.claim"] == "true"


def test_profile_scope_carries_every_claim_the_server_reads() -> None:
    profile = client_scopes()["kb.profile"]
    claims = {mapper["config"]["claim.name"] for mapper in profile["protocolMappers"]}
    assert {
        "department",
        "preferred_username",
        "email",
        "realm_access.roles",
    } <= claims


def test_clients_have_self_contained_default_scopes() -> None:
    for client in REALM["clients"]:
        # `basic` is redefined in this realm because `--import-realm` does not
        # create Keycloak's built-in scopes. It carries the `sub` claim.
        assert client["defaultClientScopes"] == ["kb.profile", "basic"]


def test_basic_scope_binds_the_subject_claim() -> None:
    mappers = client_scopes()["basic"]["protocolMappers"]
    assert any(
        mapper["protocolMapper"] == "oidc-sub-mapper"
        and mapper["config"]["access.token.claim"] == "true"
        for mapper in mappers
    )


def test_clients_are_public_pkce_clients() -> None:
    for client in REALM["clients"]:
        assert client["publicClient"] is True
        assert client["standardFlowEnabled"] is True
        assert client["implicitFlowEnabled"] is False
        assert client["directAccessGrantsEnabled"] is False
        assert client["attributes"]["pkce.code.challenge.method"] == "S256"


def test_full_scope_allowed_keeps_realm_roles_in_the_token() -> None:
    # With fullScopeAllowed=false Keycloak drops every realm role the client
    # has not been explicitly granted, so realm_access.roles would be empty.
    for client in REALM["clients"]:
        assert client["fullScopeAllowed"] is True


def test_client_scope_references_resolve() -> None:
    known = set(client_scopes()) | BUILTIN_SCOPES
    for client in REALM["clients"]:
        referenced = set(client["defaultClientScopes"]) | set(client["optionalClientScopes"])
        assert referenced <= known


def test_inspector_can_request_write_scopes() -> None:
    inspector = next(c for c in REALM["clients"] if c["clientId"] == "mcp-inspector")
    assert {"kb.read", "kb.write", "kb.admin"} <= set(inspector["optionalClientScopes"])


def test_clients_allow_offline_access_for_refresh_tokens() -> None:
    # Inspector always asks for offline_access; without it the authorization
    # endpoint fails the whole request with invalid_scope.
    for client in REALM["clients"]:
        assert "offline_access" in client["optionalClientScopes"]


def test_demo_users_may_obtain_offline_tokens() -> None:
    # Keycloak also requires the realm role, otherwise login succeeds but the
    # token request fails with "Offline tokens not allowed for the user".
    for user in REALM["users"]:
        assert "offline_access" in user["realmRoles"]
