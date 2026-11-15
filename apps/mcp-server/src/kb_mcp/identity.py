"""Derive the acting principal.

Identity comes from the verified access token only. When auth is disabled
(week 1-2 local development), a fixed dev identity is used instead; that path
is impossible to reach once `auth_enabled` is true.
"""

from __future__ import annotations

from mcp.server.auth.middleware.auth_context import get_access_token

from .errors import KbError
from .models import Actor
from .settings import get_settings


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.replace(",", " ").split() if part.strip())


def current_actor() -> Actor:
    settings = get_settings()
    token = get_access_token()

    if token is not None:
        claims = token.claims or {}
        raw_roles = claims.get("realm_access", {})
        roles = raw_roles.get("roles", []) if isinstance(raw_roles, dict) else []
        subject = token.subject
        if not subject:
            raise KbError("token has no subject", code="UNAUTHENTICATED", http_status=401)
        return Actor(
            sub=subject,
            username=str(claims.get("preferred_username") or "unknown"),
            department=claims.get("department"),
            roles=tuple(str(role) for role in roles),
            scopes=tuple(token.scopes),
            email=claims.get("email"),
        )

    if settings.auth_enabled:
        raise KbError("authentication required", code="UNAUTHENTICATED", http_status=401)

    return Actor(
        sub=settings.dev_sub,
        username=settings.dev_user,
        department=settings.dev_department,
        roles=_split(settings.dev_roles),
        scopes=_split(settings.dev_scopes),
    )
