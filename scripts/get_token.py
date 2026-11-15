#!/usr/bin/env python3
"""Obtain a Keycloak access token with OAuth 2.1 authorization code + PKCE.

This is the same flow an MCP client performs, without any third-party OAuth
library: discovery, S256 PKCE, a loopback redirect, and a code exchange. It is
meant for local debugging against the `mcp-cli` public client.

The token is not printed by default; claims are. Pass --show-token only when
you deliberately want to paste it into another tool.

Usage:
    python scripts/get_token.py
    python scripts/get_token.py --show-token
    python scripts/get_token.py --no-browser
    python scripts/get_token.py --headless --username alice --password password

`--headless` posts the Keycloak login form directly (no browser). It is for
local automation and CI only: it demonstrates the same authorization code +
PKCE exchange but skips the human. Never use it against a real user account.

Requires Keycloak on http://localhost:8080 with the `kb` realm imported.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import http.cookiejar
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

DEFAULT_ISSUER = os.environ.get("KB_ISSUER", "http://localhost:8080/realms/kb")
DEFAULT_RESOURCE = os.environ.get("KB_RESOURCE_URL", "http://localhost:8000/mcp")
DEFAULT_SCOPE = "openid kb.read kb.write kb.admin"


class CallbackHandler(BaseHTTPRequestHandler):
    """Collect the authorization response on the loopback redirect."""

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


def fetch_json(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=15) as response:
        payload: dict[str, Any] = json.load(response)
    return payload


def post_form(url: str, form: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(form).encode()
    request = urllib.request.Request(
        url,
        data=data,
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
        raise SystemExit(f"token endpoint returned {exc.code}: {detail}") from exc


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def decode_payload(token: str) -> dict[str, Any]:
    """Decode the JWT payload without verifying it. Display only."""

    parts = token.split(".")
    if len(parts) != 3:
        raise SystemExit("access token is not a JWT")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
    return payload


def wait_for_code(port: int, timeout: int) -> dict[str, str]:
    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = timeout
    print(f"Waiting for the callback on http://127.0.0.1:{port}/callback ...")
    server.handle_request()
    server.server_close()
    if not CallbackHandler.result:
        raise SystemExit("timed out waiting for the authorization response")
    return CallbackHandler.result


def submit_login_form(authorize_url: str, username: str, password: str) -> None:
    """Post the Keycloak login form and follow the redirect to the callback.

    No loopback server is started here: the caller is expected to listen on the
    redirect URI already (the CLI clients do). This keeps the helper reusable
    by `scripts/verify_clients.py`.
    """

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(authorize_url, timeout=15) as response:
        page = response.read().decode("utf-8", errors="replace")

    match = re.search(r'action="([^"]+)"', page)
    if not match:
        raise SystemExit("login form not found; check the realm and client")

    fields = {"username": username, "password": password}
    for tag in re.findall(r"<input\b[^>]*>", page):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag))
        if attrs.get("type") == "hidden" and attrs.get("name"):
            fields[attrs["name"]] = html.unescape(attrs.get("value", ""))

    # Keycloak marks its session cookies Secure even on http://localhost.
    # Python's cookie jar refuses to replay those over http, while browsers
    # make an exception for localhost, so send them explicitly.
    cookie_header = "; ".join(f"{cookie.name}={cookie.value}" for cookie in jar)
    request = urllib.request.Request(
        html.unescape(match.group(1)),
        data=urllib.parse.urlencode(fields).encode(),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": cookie_header,
        },
    )
    try:
        with urllib.request.build_opener().open(request, timeout=15) as response:
            response.read()  # the redirect chain ends at the loopback callback
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"login form returned {exc.code}: {detail[:500]}") from exc


def headless_code(
    authorize_url: str, username: str, password: str, port: int, timeout: int
) -> dict[str, str]:
    """Post the login form instead of waiting for a human in a browser."""

    CallbackHandler.result = {}
    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = timeout
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    submit_login_form(authorize_url, username, password)

    thread.join(timeout=5)
    server.server_close()
    if not CallbackHandler.result:
        raise SystemExit("headless login did not reach the callback; check credentials")
    return CallbackHandler.result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issuer", default=DEFAULT_ISSUER)
    parser.add_argument("--client-id", default="mcp-cli")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--show-token", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", default="password")
    args = parser.parse_args()

    issuer = args.issuer.rstrip("/")
    redirect_uri = f"http://localhost:{args.port}/callback"

    print(f"Discovery: {issuer}/.well-known/openid-configuration")
    metadata = fetch_json(f"{issuer}/.well-known/openid-configuration")
    if metadata.get("issuer") != issuer:
        raise SystemExit(
            f"issuer mismatch: discovery returned {metadata.get('issuer')!r}"
        )

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    authorize_url = (
        metadata["authorization_endpoint"]
        + "?"
        + urllib.parse.urlencode(
            {
                "client_id": args.client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": args.scope,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
    )

    print(f"Scopes: {args.scope}")
    if args.headless:
        print(f"Logging in headlessly as {args.username} ...")
        response = headless_code(
            authorize_url, args.username, args.password, args.port, args.timeout
        )
    else:
        print(f"Open this URL if the browser does not open:\n{authorize_url}\n")
        if not args.no_browser:
            webbrowser.open(authorize_url)
        response = wait_for_code(args.port, args.timeout)
    if response.get("error"):
        raise SystemExit(
            f"authorization failed: {response['error']} "
            f"({response.get('error_description', 'no description')})"
        )
    if response.get("state") != state:
        raise SystemExit("state mismatch; possible CSRF, aborting")
    if "code" not in response:
        raise SystemExit(f"callback did not include a code: {response}")

    tokens = post_form(
        metadata["token_endpoint"],
        {
            "grant_type": "authorization_code",
            "code": response["code"],
            "redirect_uri": redirect_uri,
            "client_id": args.client_id,
            "code_verifier": verifier,
        },
    )
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str):
        raise SystemExit(f"token response had no access_token: {tokens}")

    payload = decode_payload(access_token)
    audience = payload.get("aud", [])
    audiences = [audience] if isinstance(audience, str) else list(audience)
    scopes = str(payload.get("scope", "")).split()
    expires_at = time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(payload.get("exp", 0)))
    )

    print("\n--- access token claims ---")
    print(f"issuer     : {payload.get('iss')}")
    print(f"subject    : {payload.get('sub')}")
    print(f"username   : {payload.get('preferred_username')}")
    print(f"department : {payload.get('department')}")
    print(f"roles      : {', '.join(payload.get('realm_access', {}).get('roles', []))}")
    print(f"client     : {payload.get('azp')}")
    print(f"audience   : {', '.join(audiences)}")
    print(f"scopes     : {' '.join(scopes)}")
    print(f"expires    : {expires_at}")

    if DEFAULT_RESOURCE not in audiences:
        print(
            f"\nWARNING: audience does not contain {DEFAULT_RESOURCE}. "
            "The MCP server will reject this token; check the per-scope "
            "Audience mapper in the realm.",
            file=sys.stderr,
        )
    if args.show_token:
        print(f"\n--- access token ---\n{access_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
