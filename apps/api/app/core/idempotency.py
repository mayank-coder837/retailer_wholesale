"""Idempotent writes (CLAUDE.md rule 9; architecture.md lifecycle step 2).

For an authenticated ``POST/PUT/PATCH/DELETE`` carrying ``Idempotency-Key``:

1. the middleware opens the request's transaction and inserts the key row first. A concurrent request with the
   same key blocks on the unique index until the first one finishes;
2. the endpoint runs inside that same transaction (``get_tenant_session`` reuses it);
3. 2xx/3xx: the response is stored in the same commit as the business change, so a crash can never leave one
   without the other. 4xx: the business work is rolled back and the 4xx stored. 5xx: nothing is stored, so a
   retry runs again.

A repeat returns the stored response with ``Idempotent-Replayed: true``; the same key with a different request
returns 409 ``idempotency_key_reused``. Keys expire after 24 hours.
"""

import hashlib
import re

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.audit import set_context
from app.core.config import Settings
from app.core.db import apply_context
from app.core.deps import IDEMPOTENT_SESSION, audit_context
from app.core.errors import PROBLEM_JSON
from app.core.ids import new_id
from app.core.platform_models import IdempotencyKey
from app.core.security import Principal, decode_access_token

HEADER = "idempotency-key"
REPLAYED = "idempotent-replayed"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
KEPT_HEADERS = ("content-type", "location")
_KEY = re.compile(r"^[\x21-\x7e]{1,200}$")  # printable ASCII, no spaces


def request_hash(method: str, path: str, query: bytes, body: bytes) -> str:
    digest = hashlib.sha256()
    for part in (method.encode(), path.encode(), query, body):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _problem(status: int, code: str, title: str, path: str) -> Response:
    body = {"type": f"https://errors.smartbiz.local/{code}", "title": title, "status": status, "code": code}
    return JSONResponse(body | {"instance": path}, status_code=status, media_type=PROBLEM_JSON)


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._applies(scope):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        key = headers.get(HEADER, "")
        if not _KEY.match(key):
            await _problem(422, "invalid_idempotency_key", "Invalid Idempotency-Key", scope["path"])(
                scope, receive, send
            )
            return
        principal = self._principal(headers)
        if principal is None:  # no valid token: the endpoint itself answers 401
            await self.app(scope, receive, send)
            return

        body = await _read_body(receive)
        fingerprint = request_hash(scope["method"], scope["path"], scope.get("query_string", b""), body)
        sessionmaker: async_sessionmaker[AsyncSession] = scope["app"].state.sessionmaker
        state = scope.setdefault("state", {})

        async with sessionmaker() as session:
            await _scope(session, principal, state.get("request_id"))
            claimed = await self._claim(session, principal, key, fingerprint)
            if not claimed:
                await session.rollback()
                await self._replay(session, principal, key, fingerprint, scope, receive, send)
                return

            state[IDEMPOTENT_SESSION] = session
            captured = _Captured()
            try:
                await self.app(scope, _replay_body(body), captured.send)
            except BaseException:
                await session.rollback()
                raise
            finally:
                state.pop(IDEMPOTENT_SESSION, None)

            if captured.status < 400:
                await self._store(session, principal, key, captured)
                await session.commit()
            elif captured.status < 500:
                await session.rollback()
                await _scope(session, principal, state.get("request_id"))
                await self._claim(session, principal, key, fingerprint)
                await self._store(session, principal, key, captured)
                await session.commit()
            else:
                await session.rollback()
        await captured.replay(send)

    def _applies(self, scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] not in WRITE_METHODS:
            return False
        if scope["path"].startswith("/auth/"):
            return False  # auth endpoints manage their own transactions
        return Headers(scope=scope).get(HEADER) is not None

    def _principal(self, headers: Headers) -> Principal | None:
        auth = headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        return decode_access_token(token, self.settings)

    async def _claim(self, session: AsyncSession, principal: Principal, key: str, fingerprint: str) -> bool:
        await session.execute(
            delete(IdempotencyKey).where(
                IdempotencyKey.user_id == principal.user_id,
                IdempotencyKey.key == key,
                IdempotencyKey.expires_at < func.now(),
            )
        )
        inserted = await session.execute(
            insert(IdempotencyKey)
            .values(
                id=new_id(),
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                key=key,
                request_hash=fingerprint,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "user_id", "key"])
            .returning(IdempotencyKey.id)
        )
        return inserted.first() is not None

    async def _store(
        self, session: AsyncSession, principal: Principal, key: str, captured: "_Captured"
    ) -> None:
        row = (
            await session.scalars(
                select(IdempotencyKey).where(
                    IdempotencyKey.user_id == principal.user_id, IdempotencyKey.key == key
                )
            )
        ).one()
        row.status_code = captured.status
        row.response_body = captured.body.decode("utf-8", errors="replace")
        row.response_headers = {k: v for k, v in captured.headers.items() if k in KEPT_HEADERS}
        await session.flush()

    async def _replay(
        self,
        session: AsyncSession,
        principal: Principal,
        key: str,
        fingerprint: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        await _scope(session, principal, None)
        row = (
            await session.scalars(
                select(IdempotencyKey).where(
                    IdempotencyKey.user_id == principal.user_id, IdempotencyKey.key == key
                )
            )
        ).one()
        # Copy the stored response out before the rollback expires the row's attributes.
        stored_hash, status_code = row.request_hash, row.status_code
        body, headers = row.response_body, dict(row.response_headers or {})
        await session.rollback()
        if stored_hash != fingerprint:
            response = _problem(
                409,
                "idempotency_key_reused",
                "This Idempotency-Key was used for a different request",
                scope["path"],
            )
        else:
            response = Response(
                content=body or b"",
                status_code=status_code or 200,
                headers={**headers, REPLAYED: "true"},
            )
        await response(scope, receive, send)


async def _scope(session: AsyncSession, principal: Principal, request_id: str | None) -> None:
    await apply_context(
        session, tenant_id=principal.tenant_id, user_id=principal.user_id, node_id=principal.node_id
    )
    set_context(session, audit_context(principal, request_id))


async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                return b"".join(chunks)
        elif message["type"] == "http.disconnect":
            return b"".join(chunks)


def _replay_body(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    return receive


class _Captured:
    """Buffers the endpoint's response so it is sent only after the transaction outcome is decided."""

    def __init__(self) -> None:
        self.status = 500
        self.raw_headers: list[tuple[bytes, bytes]] = []
        self.body = b""

    @property
    def headers(self) -> dict[str, str]:
        return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in self.raw_headers}

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.raw_headers = list(message.get("headers", []))
        elif message["type"] == "http.response.body":
            self.body += message.get("body", b"")

    async def replay(self, send: Send) -> None:
        await send({"type": "http.response.start", "status": self.status, "headers": self.raw_headers})
        await send({"type": "http.response.body", "body": self.body, "more_body": False})
