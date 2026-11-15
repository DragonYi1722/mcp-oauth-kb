"""Token verification tests that run without Keycloak.

A locally generated RSA key stands in for Keycloak's signing key, and the JWKS
client is stubbed. This covers the security-critical claim checks: issuer,
audience, expiry, subject, algorithm pinning, and JWKS failure handling.
"""

import base64
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from kb_mcp.auth import KeycloakTokenVerifier

pytestmark = pytest.mark.security

ISSUER = "http://localhost:8080/realms/kb"
RESOURCE = "http://localhost:8000/mcp"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"
KID = "test-key"


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class StubJwks:
    def __init__(self, public_key: object, error: Exception | None = None) -> None:
        self._public_key = public_key
        self._error = error

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        if self._error is not None:
            raise self._error
        return SimpleNamespace(key=self._public_key)


def make_verifier(
    private_key: rsa.RSAPrivateKey, error: Exception | None = None
) -> KeycloakTokenVerifier:
    return KeycloakTokenVerifier(
        issuer=ISSUER,
        resource=RESOURCE,
        jwks_uri=JWKS_URI,
        jwks_client=StubJwks(private_key.public_key(), error),
    )


def claims(**overrides: object) -> dict[str, object]:
    now = int(time.time())
    payload: dict[str, object] = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "1f2e3d4c-user",
        "azp": "mcp-inspector",
        "scope": "openid profile kb.read kb.write",
        "department": "engineering",
        "exp": now + 300,
        "iat": now,
    }
    payload.update(overrides)
    return payload


def sign(
    private_key: rsa.RSAPrivateKey,
    payload: dict[str, object],
    *,
    algorithm: str = "RS256",
    key: object | None = None,
) -> str:
    return jwt.encode(
        payload,
        key if key is not None else private_key,
        algorithm=algorithm,
        headers={"kid": KID},
    )


async def test_valid_token_maps_claims(private_key: rsa.RSAPrivateKey) -> None:
    access = await make_verifier(private_key).verify_token(sign(private_key, claims()))

    assert access is not None
    assert access.subject == "1f2e3d4c-user"
    assert access.client_id == "mcp-inspector"
    assert access.resource == RESOURCE
    assert access.scopes == ["openid", "profile", "kb.read", "kb.write"]
    assert access.claims is not None
    assert access.claims["department"] == "engineering"


async def test_wrong_audience_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    token = sign(private_key, claims(aud="http://localhost:9000/mcp"))
    assert await make_verifier(private_key).verify_token(token) is None


async def test_wrong_issuer_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    token = sign(private_key, claims(iss="http://evil.example/realms/kb"))
    assert await make_verifier(private_key).verify_token(token) is None


async def test_expired_token_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    token = sign(private_key, claims(exp=int(time.time()) - 120))
    assert await make_verifier(private_key).verify_token(token) is None


async def test_missing_subject_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    payload = claims()
    del payload["sub"]
    assert await make_verifier(private_key).verify_token(sign(private_key, payload)) is None


async def test_signature_from_another_key_is_rejected(
    private_key: rsa.RSAPrivateKey,
) -> None:
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = sign(attacker_key, claims())
    assert await make_verifier(private_key).verify_token(token) is None


async def test_algorithm_confusion_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    token = jwt.encode(
        claims(),
        "public-key-as-hmac-secret-0123456789abcdef",
        algorithm="HS256",
        headers={"kid": KID},
    )
    assert await make_verifier(private_key).verify_token(token) is None


async def test_unknown_kid_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    verifier = make_verifier(private_key, error=PyJWKClientError("unknown kid"))
    assert await verifier.verify_token(sign(private_key, claims())) is None


async def test_jwks_outage_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    verifier = make_verifier(private_key, error=PyJWKClientConnectionError("jwks unreachable"))
    assert await verifier.verify_token(sign(private_key, claims())) is None


async def test_scope_list_is_normalized(private_key: rsa.RSAPrivateKey) -> None:
    token = sign(private_key, claims(scope=["kb.read", "kb.admin"]))
    access = await make_verifier(private_key).verify_token(token)

    assert access is not None
    assert access.scopes == ["kb.read", "kb.admin"]


def _base64url(payload: dict[str, object]) -> str:
    raw = json.dumps(payload).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


async def test_alg_none_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    unsigned = f"{_base64url({'alg': 'none', 'kid': KID})}.{_base64url(claims())}."

    assert await make_verifier(private_key).verify_token(unsigned) is None


async def test_rs512_is_rejected(private_key: rsa.RSAPrivateKey) -> None:
    token = sign(private_key, claims(), algorithm="RS512")

    assert await make_verifier(private_key).verify_token(token) is None


@contextmanager
def jwks_server(keys: list[dict[str, Any]]) -> Iterator[SimpleNamespace]:
    """Serve a JWKS and record the headers of every request it receives."""

    hits: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            hits.append({key.lower(): value for key, value in self.headers.items()})
            body = json.dumps({"keys": keys}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield SimpleNamespace(url=f"http://127.0.0.1:{port}/certs", hits=hits)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


async def test_jku_cannot_redirect_key_lookup_or_forward_the_token(
    private_key: rsa.RSAPrivateKey,
) -> None:
    """T2/T3: key lookup is pinned to configured JWKS, and no bearer token leaks."""

    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})

    with jwks_server([jwk]) as trusted, jwks_server([]) as attacker:
        token = jwt.encode(
            claims(),
            private_key,
            algorithm="RS256",
            headers={"kid": KID, "jku": attacker.url},
        )
        verifier = KeycloakTokenVerifier(issuer=ISSUER, resource=RESOURCE, jwks_uri=trusted.url)

        access = await verifier.verify_token(token)

        assert access is not None
        assert attacker.hits == [], "the jku header must never drive key lookup"
        assert trusted.hits, "the configured JWKS should have been used"
        assert "authorization" not in trusted.hits[0]
