"""OAuth 2.1 resource-server plumbing for Keycloak-issued JWTs."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError, PyJWTError
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings

from .settings import Settings

logger = logging.getLogger(__name__)

# Keycloak signs access tokens with RS256 by default. Never accept "none"
# or an algorithm that lets an attacker reuse a public key as an HMAC secret.
ALLOWED_ALGORITHMS = ("RS256",)


class SigningKey(Protocol):
    key: Any


class JwksClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> SigningKey: ...


class KeycloakTokenVerifier(TokenVerifier):
    """Verify a bearer token against a fixed issuer and resource.

    The issuer, audience and JWKS URI all come from configuration. Nothing in
    the token (for example `jku` or `x5u`) may redirect key lookup, which would
    turn verification into an SSRF / key-substitution primitive.
    """

    def __init__(
        self,
        *,
        issuer: str,
        resource: str,
        jwks_uri: str,
        jwks_client: JwksClient | None = None,
    ) -> None:
        self._issuer = issuer
        self._resource = resource
        # The client is injectable so tests can verify signature and claim
        # handling without running an identity provider.
        self._jwks = jwks_client or PyJWKClient(jwks_uri, cache_keys=True, lifespan=3600)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
        except PyJWKClientConnectionError:
            # The identity provider is down. Do not treat this as "valid".
            logger.warning("JWKS endpoint unavailable; rejecting token")
            return None
        except (PyJWKClientError, PyJWTError):
            return None

        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=list(ALLOWED_ALGORITHMS),
                audience=self._resource,
                issuer=self._issuer,
                leeway=30,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except PyJWTError:
            return None

        raw_scopes = claims.get("scope", "")
        scopes = raw_scopes.split() if isinstance(raw_scopes, str) else list(raw_scopes)

        return AccessToken(
            token=token,
            client_id=str(claims.get("azp", "")),
            scopes=scopes,
            expires_at=int(claims["exp"]) if claims.get("exp") else None,
            # RFC 8707 / MCP: the token must have been issued for this server.
            resource=self._resource,
            subject=claims["sub"],
            claims=claims,
        )


def build_token_verifier(settings: Settings) -> KeycloakTokenVerifier:
    return KeycloakTokenVerifier(
        issuer=settings.issuer,
        resource=settings.resource_url,
        jwks_uri=settings.jwks_uri,
    )


def build_auth_settings(settings: Settings) -> AuthSettings:
    return AuthSettings.model_validate(
        {
            "issuer_url": settings.issuer,
            "resource_server_url": settings.resource_url,
            "required_scopes": ["kb.read"],
            "validate_token_resource": True,
        }
    )
