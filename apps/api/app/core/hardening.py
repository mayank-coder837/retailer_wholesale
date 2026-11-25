"""Response headers that close common browser attacks, and a cap on how large a request may be.

The cap looks at the declared ``Content-Length`` and refuses before the body is read, so a huge upload cannot
fill the disk or memory. The upload endpoints also stop reading at their own limit (chunked bodies).
"""

import json

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
)
# Room for the multipart envelope around a file that is itself at the file limit.
ENVELOPE_BYTES = 1024 * 1024


class HardeningMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes + ENVELOPE_BYTES

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                present = {name for name, _ in message.get("headers", [])}
                message.setdefault("headers", []).extend(h for h in HEADERS if h[0] not in present)
            await send(message)

        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.max_body_bytes:
            body = json.dumps(
                {
                    "type": "about:blank",
                    "title": "The request is too large",
                    "status": 413,
                    "code": "request_too_large",
                }
            ).encode()
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/problem+json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"connection", b"close"),
                    ],
                }
            )
            await send_with_headers({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send_with_headers)
