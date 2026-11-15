#!/usr/bin/env python3
"""End-to-end acceptance for the two CLI clients (week 7).

Each client is started in its own login mode with `--no-browser`. The script
reads the authorize URL that the client prints, posts the Keycloak login form
the way a browser would, and lets the redirect land on the client's own
loopback server. That exercises the client's real discovery, PKCE, state check,
code exchange and token cache -- not a token minted elsewhere.

Then it calls `whoami` from the cached token, forces an expiry, and checks that
the refresh-token path silently renews it.

Usage:
    python scripts/verify_clients.py
    python scripts/verify_clients.py --skip-refresh
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
PY_CLIENT_SRC = ROOT / "apps" / "py-client" / "src"
TS_CLIENT_DIR = ROOT / "apps" / "ts-client"
TOKEN_KEYS = ("expires_at", "expiresAt")


class CheckFailed(RuntimeError):
    pass


def load_get_token() -> ModuleType:
    path = ROOT / "scripts" / "get_token.py"
    spec = importlib.util.spec_from_file_location("get_token", path)
    if spec is None or spec.loader is None:
        raise CheckFailed("cannot load scripts/get_token.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def authorize_url(proc: subprocess.Popen[str]) -> str:
    assert proc.stdout is not None
    seen: list[str] = []
    for line in proc.stdout:
        seen.append(line)
        if "/protocol/openid-connect/auth" in line:
            return line.strip()
    raise CheckFailed(
        "the client exited without printing an authorize URL: " + "".join(seen)[-500:]
    )


def login_through_form(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    username: str,
    password: str,
    get_token: ModuleType,
) -> str:
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        url = authorize_url(proc)
        time.sleep(0.6)  # let the client's loopback server listen
        get_token.submit_login_form(url, username, password)
        output, _ = proc.communicate(timeout=60)
    except Exception:
        proc.kill()
        proc.communicate()
        raise
    if proc.returncode != 0:
        raise CheckFailed(f"login exited {proc.returncode}: {output.strip()}")
    return output


def run_json(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise CheckFailed(
            f"{' '.join(cmd)} exited {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        payload: dict[str, object] = json.loads(completed.stdout)
    except ValueError as exc:
        raise CheckFailed(
            f"client did not print JSON: {completed.stdout[:200]}"
        ) from exc
    return payload


def force_expiry(token_file: Path) -> None:
    payload = json.loads(token_file.read_text(encoding="utf-8"))
    for key in TOKEN_KEYS:
        if key in payload:
            payload[key] = 0
    token_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def expiry(token_file: Path) -> int:
    payload = json.loads(token_file.read_text(encoding="utf-8"))
    for key in TOKEN_KEYS:
        if key in payload:
            return int(payload[key])
    return 0


def check_client(
    label: str,
    *,
    login_cmd: list[str],
    whoami_cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    token_env: str,
    username: str,
    password: str,
    get_token: ModuleType,
    skip_refresh: bool,
) -> list[str]:
    results: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"kb-{label}-") as tmp:
        token_file = Path(tmp) / "token.json"
        env = {**env, token_env: str(token_file)}
        login_through_form(
            login_cmd,
            cwd=cwd,
            env=env,
            username=username,
            password=password,
            get_token=get_token,
        )
        if not token_file.exists():
            raise CheckFailed(f"{label}: login did not write a token cache")
        results.append(f"{label}: PKCE login + token cache")

        whoami = run_json([*whoami_cmd, "whoami"], cwd=cwd, env=env)
        if whoami.get("username") != username:
            raise CheckFailed(f"{label}: whoami returned {whoami.get('username')!r}")
        if whoami.get("dev_mode") is not False:
            raise CheckFailed(f"{label}: token was not verified")
        results.append(f"{label}: whoami -> {username} / {whoami.get('department')}")

        if skip_refresh:
            return results

        force_expiry(token_file)
        renewed = run_json([*whoami_cmd, "whoami"], cwd=cwd, env=env)
        if renewed.get("username") != username:
            raise CheckFailed(f"{label}: whoami after refresh failed")
        if expiry(token_file) <= int(time.time()):
            raise CheckFailed(f"{label}: token cache was not refreshed")
        results.append(f"{label}: expired token refreshed silently")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="alice")
    parser.add_argument("--password", default="password")
    parser.add_argument("--skip-refresh", action="store_true")
    parser.add_argument("--node", default="node")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()

    get_token = load_get_token()
    py_env = {
        **os.environ,
        "PYTHONPATH": str(PY_CLIENT_SRC),
        "PYTHONIOENCODING": "utf-8",
    }
    ts_env = dict(os.environ)
    ts_entry = TS_CLIENT_DIR / "dist" / "index.js"
    if not ts_entry.exists():
        raise SystemExit(
            f"{ts_entry} is missing; run `npm run build` in apps/ts-client first"
        )

    checks = [
        (
            "python-client",
            [args.python, "-m", "kb_client", "--no-browser", "login"],
            [args.python, "-m", "kb_client"],
            ROOT,
            py_env,
            "KB_TOKEN_FILE",
        ),
        (
            "ts-client",
            [args.node, "dist/index.js", "--no-browser", "login"],
            [args.node, "dist/index.js"],
            TS_CLIENT_DIR,
            ts_env,
            "KB_TS_TOKEN_FILE",
        ),
    ]

    failures = 0
    for label, login_cmd, whoami_cmd, cwd, env, token_env in checks:
        try:
            for line in check_client(
                label,
                login_cmd=login_cmd,
                whoami_cmd=whoami_cmd,
                cwd=cwd,
                env=env,
                token_env=token_env,
                username=args.username,
                password=args.password,
                get_token=get_token,
                skip_refresh=args.skip_refresh,
            ):
                print(f"PASS {line}")
        except Exception as exc:  # noqa: BLE001 - report every client
            failures += 1
            print(f"FAIL {label}: {exc}")

    if failures:
        print(f"\n{failures} client check(s) failed")
        return 1
    print("\nAll client checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
