"""The cloud side of sync (M2.1b, ADR 0010): ``POST /sync/push`` and ``GET /sync/pull``.

Both need a signed-in person holding ``sync.post`` (push) or ``sync.view`` (pull) for the whole tenant: the
branch's pusher signs in as such an account. The tenant always comes from that sign-in, never from the batch:
a row that names another tenant is refused by row-level security. A rejected batch is a 409 and changes
nothing.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.core.errors import ConflictError
from app.modules.identity.authz import get_permissions
from app.modules.sync import service
from app.modules.sync.service import Entry

router = APIRouter(prefix="/sync", tags=["sync"])
Signed = Annotated[PermissionSet, Depends(get_permissions)]


class UnknownNodeError(ConflictError):
    code = "sync_unknown_node"
    title = "This node is not a branch of this account"


class EntryIO(BaseModel):
    seq: int = Field(ge=1)
    node_id: uuid.UUID | None
    table: str = Field(min_length=1, max_length=63)
    row_id: uuid.UUID
    op: str = Field(pattern="^(insert|update|delete)$")
    payload: str = Field(description="The row as JSON text; read by the database, never as floating point")


class PushIn(BaseModel):
    node_id: uuid.UUID = Field(description="The branch node sending")
    entries: list[EntryIO] = Field(max_length=2000)


class PushOut(BaseModel):
    last_seq: int = Field(description="Everything up to here is safely applied; the branch may move on")


class PullOut(BaseModel):
    entries: list[EntryIO]
    cursor: int = Field(description="Ask again after this")


def to_entries(items: list[EntryIO]) -> list[Entry]:
    return [Entry(e.seq, e.node_id, e.table, e.row_id, e.op, e.payload) for e in items]


def to_io(entries: list[Entry]) -> list[EntryIO]:
    return [
        EntryIO(seq=e.seq, node_id=e.node_id, table=e.table, row_id=e.row_id, op=e.op, payload=e.payload)
        for e in entries
    ]


@router.post("/push", operation_id="sync_push")
async def push(body: PushIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep) -> PushOut:
    perms.require("sync.post")
    last = await service.apply(
        session, principal.tenant_id, to_entries(body.entries), sender=body.node_id, track=True
    )
    known = await session.scalar(
        text("SELECT 1 FROM node WHERE id = :n AND kind = 'branch_main_pc'"), {"n": body.node_id}
    )
    if known is None:
        raise UnknownNodeError()  # rolls the whole batch back
    return PushOut(last_seq=last)


@router.get("/pull", operation_id="sync_pull")
async def pull(
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    node_id: uuid.UUID,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> PullOut:
    perms.require("sync.view")
    entries, cursor = await service.pull(session, principal.tenant_id, node_id, after, limit=limit)
    return PullOut(entries=to_io(entries), cursor=cursor)
