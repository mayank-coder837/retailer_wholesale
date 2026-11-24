"""Sync engine (M2.1a, ADR 0010): a branch database and a separate "cloud" database.

Real data is made through the API on the branch (a business, an invoice, a receipt); the engine moves it
to the cloud in batches. What must hold: the cloud ends with the same books (counts and sums), a repeated
batch changes nothing, a batch the cloud refuses changes nothing, a branch never gets its own rows back,
and money stays exact.
"""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import apply_context
from app.modules.sync import service
from app.modules.sync.service import Entry, SyncRejectedError
from tests.test_accounting_api import Books, books
from tests.test_pricing_api import ok

Sessions = async_sessionmaker[AsyncSession]


async def identity(b: Books) -> tuple[uuid.UUID, uuid.UUID]:
    me = await ok(await b.client.get("/me", headers=b.h))
    return uuid.UUID(me["tenant"]["id"]), uuid.UUID(me["node_id"])


async def scoped(sm: Sessions, tenant: uuid.UUID, node: uuid.UUID | None = None) -> AsyncSession:
    session = sm()
    await session.begin()
    await apply_context(session, tenant_id=tenant, node_id=node)
    return session


async def send_all(
    branch: Sessions, cloud: Sessions, tenant: uuid.UUID, node: uuid.UUID, limit: int = 40
) -> int:
    """What the branch's pusher does: batches in order until nothing is left. Returns entries sent."""
    sent = 0
    while True:
        async with branch() as s, s.begin():
            await apply_context(s, tenant_id=tenant, node_id=node)
            batch = await service.pending(s, tenant, node, limit=limit)
        if not batch:
            return sent
        async with cloud() as c, c.begin():
            await apply_context(c, tenant_id=tenant, node_id=node)
            last = await service.apply(c, tenant, batch, sender=node)
        async with branch() as s, s.begin():
            await apply_context(s, tenant_id=tenant, node_id=node)
            await service.mark_pushed(s, tenant, node, last)
        sent += len(batch)


async def apply_on(
    cloud: Sessions,
    tenant: uuid.UUID,
    node: uuid.UUID,
    batch: list[Entry],
    *,
    as_tenant: uuid.UUID | None = None,
) -> int:
    """The cloud applying a batch in one transaction (``as_tenant``: who the sender is authenticated as)."""
    async with cloud() as c, c.begin():
        return await service.apply(c, as_tenant or tenant, batch, sender=node)


async def books_summary(sm: Sessions, tenant: uuid.UUID) -> dict[str, Any]:
    async with sm() as s, s.begin():
        await apply_context(s, tenant_id=tenant)
        counts = {
            t: (await s.scalar(text(f"SELECT count(*) FROM {t}")))
            for t in ("business", "branch", "party", "document", "journal_entry", "journal_line", "account")
        }
        sums = (
            await s.execute(
                text("SELECT coalesce(sum(debit), 0), coalesce(sum(credit), 0) FROM journal_line")
            )
        ).one()
        return {"counts": counts, "debit": Decimal(sums[0]), "credit": Decimal(sums[1])}


async def made_something(client: AsyncClient, email: str) -> tuple[Books, uuid.UUID, uuid.UUID]:
    b = await books(client, email)
    await b.invoice("1250.5000", "INV-1")
    await b.post(
        b.voucher(
            "receipt",
            [
                {
                    "party_id": b.customer,
                    "amount": "500",
                    "allocations": [
                        {"type": "against_ref", "amount": "500", "bill_id": (await b.bills())[0]["id"]}
                    ],
                }
            ],
            account_id=b.cash,
        )
    )
    tenant, node = await identity(b)
    return b, tenant, node


async def test_the_cloud_ends_with_the_same_books_and_a_repeat_changes_nothing(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions
) -> None:
    _b, tenant, node = await made_something(client, "sync1@shop.ae")
    sent = await send_all(app_sessionmaker, cloud, tenant, node)
    assert sent > 20
    here, there = await books_summary(app_sessionmaker, tenant), await books_summary(cloud, tenant)
    assert here == there
    assert there["counts"]["journal_line"] >= 4
    assert there["debit"] == there["credit"] > 0
    assert there["debit"] >= Decimal("1250.5000")

    assert await send_all(app_sessionmaker, cloud, tenant, node) == 0  # nothing new
    # The same batch again (a lost acknowledgement): applied at most once.
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=tenant, node_id=node)
        await s.execute(text("UPDATE sync_state SET pushed_seq = 0 WHERE node_id = :n"), {"n": node})
    assert await send_all(app_sessionmaker, cloud, tenant, node) > 0  # resent from the start
    assert await books_summary(cloud, tenant) == there  # and nothing doubled


async def test_a_posted_row_the_cloud_already_has_is_not_rewritten(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions
) -> None:
    _b, tenant, node = await made_something(client, "sync2@shop.ae")
    await send_all(app_sessionmaker, cloud, tenant, node)
    async with cloud() as c, c.begin():
        await apply_context(c, tenant_id=tenant)
        before = await c.scalar(text("SELECT count(*) FROM audit_log"))
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=tenant, node_id=node)
        await s.execute(text("UPDATE sync_state SET pushed_seq = 0 WHERE node_id = :n"), {"n": node})
    await send_all(app_sessionmaker, cloud, tenant, node)
    async with cloud() as c, c.begin():
        await apply_context(c, tenant_id=tenant)
        assert await c.scalar(text("SELECT count(*) FROM audit_log")) == before


async def test_the_cloud_records_what_it_received_as_the_senders_so_it_is_relayed_not_echoed(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions
) -> None:
    _b, tenant, node = await made_something(client, "sync3@shop.ae")
    await send_all(app_sessionmaker, cloud, tenant, node)
    other = uuid.uuid4()
    async with cloud() as c, c.begin():
        await apply_context(c, tenant_id=tenant)
        for_other, cursor = await service.pull(c, tenant, other, 0, limit=10_000)
        for_sender, sender_cursor = await service.pull(c, tenant, node, 0, limit=10_000)
    assert len(for_other) > 20
    assert {e.node_id for e in for_other if e.table != "user_account"} == {node}
    assert for_sender == []  # the sender never gets its own rows back
    assert sender_cursor == cursor > 0  # but its cursor still moves past them


async def test_a_batch_the_cloud_refuses_changes_nothing_and_does_not_move_the_position(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions
) -> None:
    _b, tenant, node = await made_something(client, "sync4@shop.ae")
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=tenant, node_id=node)
        batch = await service.pending(s, tenant, node, limit=5)
    good = batch[0]
    bad = Entry(
        good.seq + 1000,
        node,
        "business",
        uuid.uuid4(),
        "insert",
        f'{{"id": "{uuid.uuid4()}", "tenant_id": "{tenant}", "trn": "not-a-trn", '
        '"legal_name_en": "x", "base_currency": "AED"}',
    )
    with pytest.raises(SyncRejectedError):
        await apply_on(cloud, tenant, node, [*batch, bad])
    async with cloud() as c, c.begin():
        await apply_context(c, tenant_id=tenant)
        assert await c.scalar(text("SELECT count(*) FROM tenant")) == 0
        assert await c.scalar(text("SELECT count(*) FROM sync_state")) == 0


async def test_an_unbalanced_entry_is_refused_by_the_cloud_itself(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions
) -> None:
    _b, tenant, node = await made_something(client, "sync5@shop.ae")
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=tenant, node_id=node)
        everything = await service.pending(s, tenant, node, limit=10_000)
    broken = [
        e
        for e in everything
        if e.table != "journal_line" or e is not next(x for x in everything if x.table == "journal_line")
    ]  # one line of one entry goes missing on the way
    assert len(broken) == len(everything) - 1
    with pytest.raises(SyncRejectedError):
        await apply_on(cloud, tenant, node, broken)
    async with cloud() as c, c.begin():
        await apply_context(c, tenant_id=tenant)
        assert await c.scalar(text("SELECT count(*) FROM journal_line")) == 0


async def test_batches_never_split_a_transaction(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions
) -> None:
    _b, tenant, node = await made_something(client, "sync6@shop.ae")
    # One entry asked for per batch: each batch is still a whole transaction, so each applies alone.
    assert await send_all(app_sessionmaker, cloud, tenant, node, limit=1) > 20
    assert await books_summary(app_sessionmaker, tenant) == await books_summary(cloud, tenant)


async def test_a_node_of_one_tenant_cannot_write_rows_of_another(
    client: AsyncClient, app_sessionmaker: Sessions, cloud: Sessions
) -> None:
    _b, tenant, node = await made_something(client, "sync7@shop.ae")
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=tenant, node_id=node)
        batch = await service.pending(s, tenant, node, limit=50)
    theirs = uuid.uuid4()  # the tenant this node is authenticated for
    with pytest.raises(SyncRejectedError):
        async with cloud() as c, c.begin():
            await service.apply(
                c, theirs, batch, sender=node
            )  # rows say another tenant: row-level security says no
    async with cloud() as c, c.begin():
        await apply_context(c, tenant_id=tenant)
        assert await c.scalar(text("SELECT count(*) FROM tenant")) == 0


async def test_an_unknown_table_is_refused(app_sessionmaker: Sessions, cloud: Sessions) -> None:
    tenant, node = uuid.uuid4(), uuid.uuid4()
    entry = Entry(1, node, "pg_authid", uuid.uuid4(), "insert", "{}")
    with pytest.raises(service.UnknownTableError):
        await apply_on(cloud, tenant, node, [entry])
