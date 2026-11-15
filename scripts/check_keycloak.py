#!/usr/bin/env python3
"""Wait for Keycloak and verify the `kb` realm is ready for the MCP server.

Checks that discovery works, the issuer matches what the server expects, the
authorization endpoint advertises PKCE S256, and the JWKS exposes an RSA key.

    python scripts/check_keycloak.py
    python scripts/check_keycloak.py --wait 120

Exit code 0 means the realm is ready; 1 means something is still wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_ISSUER = "http://localhost:8080/realms/kb"


def get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload: dict[str, Any] = json.load(response)
    return payload


def wait_for_discovery(issuer: str, wait_seconds: int) -> dict[str, Any] | None:
    url = f"{issuer}/.well-known/openid-configuration"
    deadline = time.monotonic() + wait_seconds
    attempt = 0
    while True:
        attempt += 1
        try:
            return get_json(url)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                return None
            if attempt == 1:
                print(f"Waiting for Keycloak at {url} ...")
            print(".", end="", flush=True)
            time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issuer", default=DEFAULT_ISSUER)
    parser.add_argument("--wait", type=int, default=60)
    args = parser.parse_args()

    issuer = args.issuer.rstrip("/")
    metadata = wait_for_discovery(issuer, args.wait)
    if metadata is None:
        print(
            f"\nFAIL: no discovery document at {issuer}. "
            "Is `docker compose -f infra/docker-compose.yml up -d keycloak` running?",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    print("\nRealm discovery: OK")
    print(f"  issuer                : {metadata.get('issuer')}")
    if metadata.get("issuer") != issuer:
        failures.append(
            f"discovery issuer {metadata.get('issuer')!r} != expected {issuer!r}"
        )

    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = metadata.get(field)
        print(f"  {field:<21}: {value}")
        if not value:
            failures.append(f"discovery is missing {field}")

    methods = metadata.get("code_challenge_methods_supported", [])
    print(f"  PKCE methods          : {methods}")
    if "S256" not in methods:
        failures.append("authorization server does not advertise PKCE S256")

    if metadata.get("jwks_uri"):
        try:
            jwks = get_json(str(metadata["jwks_uri"]))
            keys = jwks.get("keys", [])
            rsa_keys = [
                key
                for key in keys
                if key.get("kty") == "RSA" and key.get("alg", "RS256") == "RS256"
            ]
            print(f"  JWKS keys             : {len(keys)} total, {len(rsa_keys)} RSA/RS256")
            if not rsa_keys:
                failures.append("JWKS has no RSA/RS256 signing key")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            failures.append(f"JWKS is not reachable: {exc}")

    scopes = metadata.get("scopes_supported", [])
    print(f"  scopes_supported      : {' '.join(scopes) or '(not advertised)'}")

    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(
        "\nNext steps:\n"
        "  1. python scripts/get_token.py        # browser login, prints aud/scope\n"
        "  2. python scripts/bootstrap_seed.py   # (after postgres is up) real subs\n"
        "  3. KB_AUTH_ENABLED=true KB_PORT=8000 python -m kb_mcp.server\n"
    )
    print("Realm is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
