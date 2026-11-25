"""The branch side of sync (M2.1b): sign in to the cloud, pull what others changed, push what this PC did.

Pull comes first so that rows a new local record refers to (a customer added in the browser) are here
before the record that uses them is sent. Offline only delays: any network failure ends the cycle quietly
and the next one carries on from the saved position. A batch the cloud *refuses* (409) stops sync for this
PC until someone looks: data is never skipped.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import apply_context
from app.modules.sync import service
from app.modules.sync.service import Entry

log = logging.getLogger(__name__)

# Rows that came from the cloud are recorded here as their origin, so this PC never sends them back.
CLOUD_ORIGIN = uuid.UUID(int=0)
PAGE = 500


@dataclass
class CycleResult:
    pulled: int = 0
    pushed: int = 0
    offline: bool = False
    halted: str | None = None  # the cloud's reason, when it refused a batch


class CloudSync:
    def __init__(
        self,
        http: httpx.AsyncClient,
        sessions: async_sessionmaker[AsyncSession],
        *,
        tenant_id: uuid.UUID,
        node_id: uuid.UUID,
        email: str,
        password: str,
    ) -> None:
        self.http = http
        self.sessions = sessions
        self.tenant_id = tenant_id
        self.node_id = node_id
        self._credentials = {"email": email, "password": password, "client": "desktop"}
        self._token: str | None = None
        self.halted: str | None = None

    async def _headers(self, *, fresh: bool = False) -> dict[str, str]:
        if self._token is None or fresh:
            response = await self.http.post("/auth/login", json=self._credentials)
            response.raise_for_status()
            body = response.json()
            if uuid.UUID(body["tenant_id"]) != self.tenant_id:
                raise PermissionError("the cloud account belongs to another tenant")
            self._token = str(body["access_token"])
        return {"Authorization": f"Bearer {self._token}"}

    async def _call(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        response = await self.http.request(method, url, headers=await self._headers(), **kwargs)
        if response.status_code == httpx.codes.UNAUTHORIZED:  # the token expired: sign in again, once
            response = await self.http.request(method, url, headers=await self._headers(fresh=True), **kwargs)
        return response

    async def _scoped(self, session: AsyncSession) -> None:
        await apply_context(session, tenant_id=self.tenant_id, node_id=self.node_id)

    async def pull_all(self) -> int:
        total = 0
        while True:
            async with self.sessions() as s, s.begin():
                await self._scoped(s)
                state = await service.state(s, self.tenant_id, self.node_id)
                after = state.pulled_seq if state else 0
            response = await self._call(
                "GET", "/sync/pull", params={"node_id": str(self.node_id), "after": after, "limit": PAGE}
            )
            response.raise_for_status()
            body = response.json()
            entries = [
                Entry(
                    e["seq"],
                    e["node_id"] and uuid.UUID(e["node_id"]),
                    e["table"],
                    uuid.UUID(e["row_id"]),
                    e["op"],
                    e["payload"],
                    e.get("changed"),
                )
                for e in body["entries"]
            ]
            if body["cursor"] <= after:
                return total
            async with self.sessions() as s, s.begin():
                await self._scoped(s)
                await service.apply(
                    s,
                    self.tenant_id,
                    entries,
                    sender=self.node_id,
                    fallback_origin=CLOUD_ORIGIN,
                    track=False,
                    cursor=body["cursor"],
                )
            total += len(entries)

    async def push_all(self) -> int:
        total = 0
        while True:
            async with self.sessions() as s, s.begin():
                await self._scoped(s)
                batch = await service.pending(s, self.tenant_id, self.node_id, limit=PAGE)
            if not batch:
                return total
            payload = {
                "node_id": str(self.node_id),
                "entries": [
                    {
                        "seq": e.seq,
                        "node_id": e.node_id and str(e.node_id),
                        "table": e.table,
                        "row_id": str(e.row_id),
                        "op": e.op,
                        "payload": e.payload,
                        "changed": e.changed,
                    }
                    for e in batch
                ],
            }
            response = await self._call("POST", "/sync/push", json=payload)
            if response.status_code == httpx.codes.CONFLICT:
                self.halted = str(response.json().get("code", "sync_batch_rejected"))
                return total
            response.raise_for_status()
            async with self.sessions() as s, s.begin():
                await self._scoped(s)
                await service.mark_pushed(s, self.tenant_id, self.node_id, int(response.json()["last_seq"]))
            total += len(batch)

    async def cycle(self) -> CycleResult:
        if self.halted:
            return CycleResult(halted=self.halted)
        result = CycleResult()
        try:
            result.pulled = await self.pull_all()
            result.pushed = await self.push_all()
        except (httpx.TransportError, httpx.HTTPStatusError) as error:
            log.info("sync paused: %s", error)
            result.offline = True
        result.halted = self.halted
        return result

    async def run(self, interval_s: float) -> None:
        """Every ``interval_s`` seconds, for as long as the app runs."""
        while True:
            try:
                await self.cycle()
            except Exception:
                log.exception("sync cycle failed")  # try again next time: nothing was skipped
            await asyncio.sleep(interval_s)
