"""Per-request correlation id.

The HTTP middleware stores the id in a contextvar so the audit logger can write
it without every tool function taking an extra parameter. Clients may send
`X-Request-ID`; otherwise the server generates one and echoes it in the
response header.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from uuid import uuid4

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_request_id: ContextVar[str] = ContextVar("kb_request_id", default="-")


def get_request_id() -> str:
    return _request_id.get()


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get("x-request-id") or uuid4().hex
        token: Token[str] = _request_id.set(request_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            _request_id.reset(token)
