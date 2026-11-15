"""Thin MCP client used by the CLI.

The transport is Streamable HTTP. Because the server is stateless, the only
per-request identity is the bearer token, which is attached to every request by
the shared `httpx2` client.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any, Self

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .oauth import Token

DEFAULT_SERVER_URL = "http://localhost:8000/mcp"


class ToolFailed(RuntimeError):
    """The server answered, but the tool returned an error payload."""

    def __init__(self, tool: str, payload: Any) -> None:
        super().__init__(f"{tool} failed: {json.dumps(payload, ensure_ascii=False)}")
        self.tool = tool
        self.payload = payload


class KbClient:
    def __init__(
        self, server_url: str = DEFAULT_SERVER_URL, token: Token | None = None
    ) -> None:
        self.server_url = server_url
        self.token = token
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        headers = {}
        if self.token is not None:
            headers["Authorization"] = f"Bearer {self.token.access_token}"

        stack = AsyncExitStack()
        http = await stack.enter_async_context(
            # trust_env=False: a local system proxy (Clash, v2ray, corporate
            # agents) turns http://localhost:8000 into 502 Bad Gateway.
            httpx2.AsyncClient(headers=headers, timeout=30.0, trust_env=False)
        )
        read, write = await stack.enter_async_context(
            streamable_http_client(self.server_url, http_client=http)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        self._stack = stack
        self._session = session
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("KbClient must be used inside `async with`")
        return self._session

    async def list_tools(self) -> list[dict[str, Any]]:
        tools = await self.session.list_tools()
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools.tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> Any:
        result = await self.session.call_tool(name, arguments or {})
        payload = _payload(result.structured_content, result.content)
        if result.is_error:
            raise ToolFailed(name, payload)
        return payload


def _payload(structured: Any, content: list[Any]) -> Any:
    if structured is not None:
        return structured
    texts = [block.text for block in content if getattr(block, "type", None) == "text"]
    return {"content": texts} if texts else {}
