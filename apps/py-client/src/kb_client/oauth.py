"""Authorization code + PKCE for the `mcp-cli` public client.

The flow mirrors what the Inspector does: discovery, S256 challenge, a loopback
redirect, a state check, and a code exchange. Tokens are cached on disk with the
refresh token so later runs do not open a browser.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar


@dataclass
class Token:
    access_token: str
    refresh_token: str | None = None
    expires_at: int = 0
    scope: str = ""

    def expired(self, *, leeway: int = 30) -> bool:
        return self.expires_at <= int(time.time()) + leeway

    def to_json(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Token:
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=payload.get("refresh_token"),
            expires_at=int(payload.get("expires_at", 0)),
            scope=str(payload.get("scope", "")),
        )


class CallbackHandler(BaseHTTPRequestHandler):
    result: ClassVar[dict[str, str]] = {}

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        CallbackHandler.result = dict(urllib.parse.parse_qsl(parsed.query))
        body = b"Login complete. You can close this tab."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def load_token(path: Path) -> Token | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        return Token.from_json(payload)
    except (KeyError, TypeError, ValueError):
        return None


def save_token(path: Path, token: Token) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token.to_json(), indent=2), encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=15) as response:
        payload: dict[str, Any] = json.load(response)
    return payload


def _post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(form).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload: dict[str, Any] = json.load(response)
            return payload
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"token endpoint returned {exc.code}: {detail}") from exc


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _token_from_response(payload: dict[str, Any]) -> Token:
    return Token(
        access_token=str(payload["access_token"]),
        refresh_token=payload.get("refresh_token"),
        expires_at=int(time.time()) + int(payload.get("expires_in", 0)),
        scope=str(payload.get("scope", "")),
    )


def login(
    *,
    issuer: str,
    client_id: str,
    redirect_uri: str,
    scope: str,
    open_browser: bool = True,
) -> Token:
    issuer = issuer.rstrip("/")
    metadata = _fetch_json(f"{issuer}/.well-known/openid-configuration")
    if metadata.get("issuer") != issuer:
        raise RuntimeError(f"issuer mismatch: {metadata.get('issuer')!r}")

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    authorize_url = (
        metadata["authorization_endpoint"]
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    port = urllib.parse.urlparse(redirect_uri).port or 8765
    CallbackHandler.result = {}
    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = 300
    try:
        print(
            f"Open this URL if the browser does not open:\n{authorize_url}\n",
            flush=True,
        )
        if open_browser:
            webbrowser.open(authorize_url)
        server.handle_request()
    finally:
        server.server_close()

    response = CallbackHandler.result
    if response.get("error"):
        raise RuntimeError(
            f"authorization failed: {response['error']} "
            f"({response.get('error_description', 'no description')})"
        )
    if response.get("state") != state:
        raise RuntimeError("state mismatch; the callback may be forged")
    if "code" not in response:
        raise RuntimeError("callback did not include a code")

    tokens = _post_form(
        metadata["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": response["code"],
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    return _token_from_response(tokens)


def refresh(*, issuer: str, client_id: str, token: Token) -> Token:
    if not token.refresh_token:
        raise RuntimeError("no refresh token available; run `kb-client login` again")
    metadata = _fetch_json(f"{issuer.rstrip('/')}/.well-known/openid-configuration")
    payload = _post_form(
        metadata["token_endpoint"],
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": token.refresh_token,
        },
    )
    return _token_from_response(payload)


def token_from_env() -> Token | None:
    value = os.environ.get("KB_ACCESS_TOKEN")
    if not value:
        return None
    return Token(access_token=value, expires_at=int(time.time()) + 3600)
