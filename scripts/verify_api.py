"""Verify that the installed MCP SDK exposes the APIs this project relies on.

Run with the project virtualenv:

    ./.venv/Scripts/python.exe scripts/verify_api.py

SDK 2.x renamed FastMCP to MCPServer and moved several transport options.
This script fails fast instead of letting a stale tutorial produce a confusing
ImportError deep inside the server.
"""

from __future__ import annotations

import importlib
import inspect
import sys

CHECKS = [
    ("mcp.server", "MCPServer"),
    ("mcp.server.auth.provider", "TokenVerifier"),
    ("mcp.server.auth.provider", "AccessToken"),
    ("mcp.server.auth.settings", "AuthSettings"),
    ("mcp.server.auth.middleware.auth_context", "get_access_token"),
    ("mcp.server.mcpserver.exceptions", "ToolError"),
]


def main() -> int:
    failures = 0
    for module_name, attr in CHECKS:
        try:
            module = importlib.import_module(module_name)
            getattr(module, attr)
            print(f"OK   {module_name}.{attr}")
        except Exception as exc:  # noqa: BLE001 - report every missing API
            failures += 1
            print(f"FAIL {module_name}.{attr}: {exc!r}")

    try:
        from importlib.metadata import version

        print(f"\nmcp version: {version('mcp')}")
        from mcp.server import MCPServer

        print("MCPServer.__init__:", inspect.signature(MCPServer.__init__))
        print("run_streamable_http_async:", inspect.signature(MCPServer.run_streamable_http_async))
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"FAIL introspection: {exc!r}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
