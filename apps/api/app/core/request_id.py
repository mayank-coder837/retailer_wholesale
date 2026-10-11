"""Gives every request an id (echoed as ``X-Request-ID``) that audit entries and logs carry."""

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.ids import new_id

HEADER = "x-request-id"
_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(HEADER.encode(), b"").decode("latin-1")
        request_id = incoming if _SAFE.match(incoming) else str(new_id())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((HEADER.encode(), request_id.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)
