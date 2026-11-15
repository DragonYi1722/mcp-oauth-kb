import pytest
from mcp.server.auth.provider import AccessToken

from kb_mcp.errors import KbError, require_scope


def make_token(scopes: list[str]) -> AccessToken:
    return AccessToken(token="test-token", client_id="test-client", scopes=scopes)


def test_require_scope_accepts_present_scope() -> None:
    token = make_token(["kb.read"])
    assert require_scope(token, "kb.read") is token


def test_require_scope_rejects_missing_scope() -> None:
    with pytest.raises(KbError) as exc:
        require_scope(make_token(["kb.read"]), "kb.write")
    assert exc.value.code == "FORBIDDEN_SCOPE"
    assert exc.value.http_status == 403


def test_require_scope_rejects_missing_token() -> None:
    with pytest.raises(KbError) as exc:
        require_scope(None, "kb.read")
    assert exc.value.code == "UNAUTHENTICATED"
    assert exc.value.http_status == 401
