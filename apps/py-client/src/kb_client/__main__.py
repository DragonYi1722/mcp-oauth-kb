"""Command line entry point: `kb-client whoami`, `kb-client search 复盘`, ..."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from .client import DEFAULT_SERVER_URL, KbClient, ToolFailed
from .oauth import Token, load_token, login, refresh, save_token, token_from_env

DEFAULT_ISSUER = "http://localhost:8080/realms/kb"
DEFAULT_CLIENT_ID = "mcp-cli"
DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"
DEFAULT_SCOPE = "openid kb.read kb.write kb.admin"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb-client", description="OAuth 2.1 client for kb-mcp"
    )
    parser.add_argument(
        "--server-url", default=os.environ.get("KB_MCP_URL", DEFAULT_SERVER_URL)
    )
    parser.add_argument("--issuer", default=os.environ.get("KB_ISSUER", DEFAULT_ISSUER))
    parser.add_argument("--client-id", default=DEFAULT_CLIENT_ID)
    parser.add_argument("--redirect-uri", default=DEFAULT_REDIRECT_URI)
    parser.add_argument("--scope", default=DEFAULT_SCOPE)
    parser.add_argument(
        "--token", help="use this access token instead of the token cache"
    )
    parser.add_argument("--token-file", type=Path, help="token cache path")
    parser.add_argument(
        "--no-browser", action="store_true", help="never open a browser or login"
    )

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("login", help="run the browser flow and cache the tokens")
    commands.add_parser("tools", help="list the tools the server exposes")
    commands.add_parser("whoami", help="show the identity the token maps to")

    search = commands.add_parser("search", help="search documents visible to the token")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=20)

    get = commands.add_parser("get", help="read one document")
    get.add_argument("document_id")

    create = commands.add_parser("create", help="create a document")
    create.add_argument("title")
    create.add_argument("--content", required=True)
    create.add_argument(
        "--visibility", choices=("private", "department", "org"), default="department"
    )

    update = commands.add_parser(
        "update", help="update a document (optimistic concurrency)"
    )
    update.add_argument("document_id")
    update.add_argument("--version", type=int, required=True)
    update.add_argument("--title")
    update.add_argument("--content")
    update.add_argument("--visibility", choices=("private", "department", "org"))

    archive = commands.add_parser("archive", help="archive a document")
    archive.add_argument("document_id")
    archive.add_argument("--reason", default="archived from kb-client")

    call = commands.add_parser(
        "call", help="call any tool with a raw JSON argument object"
    )
    call.add_argument("tool")
    call.add_argument("--args", default="{}")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except ToolFailed as exc:
        print(json.dumps(exc.payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - the CLI reports and exits
        print(f"error: {exc}", file=sys.stderr)
        return 1


async def _run(args: argparse.Namespace) -> int:
    token_file = args.token_file or _default_token_file()

    if args.command == "login":
        fresh = login(
            issuer=args.issuer,
            client_id=args.client_id,
            redirect_uri=args.redirect_uri,
            scope=args.scope,
            open_browser=not args.no_browser,
        )
        save_token(token_file, fresh)
        print(json.dumps({"saved": str(token_file), "scope": fresh.scope}, indent=2))
        return 0

    token = _resolve_token(args, token_file)
    async with KbClient(args.server_url, token) as client:
        payload = await _dispatch(client, args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


async def _dispatch(client: KbClient, args: argparse.Namespace) -> object:
    if args.command == "tools":
        return {"tools": await client.list_tools()}
    if args.command == "whoami":
        return await client.call_tool("whoami")
    if args.command == "search":
        return await client.call_tool(
            "search_documents", {"query": args.query, "limit": args.limit}
        )
    if args.command == "get":
        return await client.call_tool("get_document", {"document_id": args.document_id})
    if args.command == "create":
        return await client.call_tool(
            "create_document",
            {
                "title": args.title,
                "content": args.content,
                "visibility": args.visibility,
            },
        )
    if args.command == "update":
        arguments: dict[str, object] = {
            "document_id": args.document_id,
            "version": args.version,
        }
        for key in ("title", "content", "visibility"):
            value = getattr(args, key)
            if value is not None:
                arguments[key] = value
        return await client.call_tool("update_document", arguments)
    if args.command == "archive":
        return await client.call_tool(
            "archive_document", {"document_id": args.document_id, "reason": args.reason}
        )
    if args.command == "call":
        return await client.call_tool(args.tool, json.loads(args.args))
    raise RuntimeError(f"unknown command: {args.command}")


def _resolve_token(args: argparse.Namespace, token_file: Path) -> Token:
    if args.token:
        return Token(access_token=args.token, expires_at=int(time.time()) + 3600)
    env_token = token_from_env()
    if env_token is not None:
        return env_token

    token = load_token(token_file)
    if token is not None and not token.expired():
        return token
    if token is not None and token.refresh_token:
        try:
            token = refresh(issuer=args.issuer, client_id=args.client_id, token=token)
        except Exception as exc:  # noqa: BLE001 - fall back to a fresh login
            print(f"token refresh failed: {exc}", file=sys.stderr)
        else:
            save_token(token_file, token)
            return token
    if args.no_browser:
        raise RuntimeError(
            f"no usable token in {token_file}; run `kb-client login` or pass --token"
        )

    token = login(
        issuer=args.issuer,
        client_id=args.client_id,
        redirect_uri=args.redirect_uri,
        scope=args.scope,
        open_browser=True,
    )
    save_token(token_file, token)
    return token


def _default_token_file() -> Path:
    override = os.environ.get("KB_TOKEN_FILE")
    if override:
        return Path(override)
    return Path.home() / ".kb-client" / "token.json"


if __name__ == "__main__":
    raise SystemExit(main())
