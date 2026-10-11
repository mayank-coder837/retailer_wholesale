"""M0.4 acceptance: numbers gap-free under concurrency and rollback; a released number is offered again."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import numbering
from app.core.db import apply_context
from app.core.ids import new_id
from tests.factories import TenantSeed, seed_tenant

pytestmark = pytest.mark.usefixtures("clean_db")


async def _seed(sm: async_sessionmaker[AsyncSession]) -> TenantSeed:
    async with sm() as s, s.begin():
        return await seed_tenant(s)


async def _allocate(
    sm: async_sessionmaker[AsyncSession],
    seed: TenantSeed,
    *,
    commit: bool = True,
    reuse: int | None = None,
    doc_type: str = "sales_invoice",
    node_id: object = None,
) -> numbering.Allocated:
    async with sm() as s:
        await apply_context(s, tenant_id=seed.tenant_id, node_id=seed.node_id)
        allocated = await numbering.allocate(
            s,
            tenant_id=seed.tenant_id,
            branch_id=seed.branch_id,
            doc_type=doc_type,
            fiscal_year="2026",
            node_id=node_id or seed.node_id,  # type: ignore[arg-type]
            document_id=new_id(),
            reuse=reuse,
        )
        await (s.commit() if commit else s.rollback())
    return allocated


async def test_numbers_are_sequential_and_formatted(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed(app_sessionmaker)

    first = await _allocate(app_sessionmaker, seed)
    second = await _allocate(app_sessionmaker, seed)
    receipt = await _allocate(app_sessionmaker, seed, doc_type="receipt")

    assert (first.formatted, second.formatted) == ("INV-2026-00001", "INV-2026-00002")
    assert receipt.formatted == "RV-2026-00001", "each document type has its own series"


async def test_a_rolled_back_post_returns_its_number(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed(app_sessionmaker)

    lost = await _allocate(app_sessionmaker, seed, commit=False)
    kept = await _allocate(app_sessionmaker, seed)

    assert lost.number == kept.number == 1


async def test_concurrent_posts_never_skip_or_repeat_a_number(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed(app_sessionmaker)
    outcomes = [i % 3 != 0 for i in range(24)]  # every third post rolls back

    results = await asyncio.gather(*(_allocate(app_sessionmaker, seed, commit=c) for c in outcomes))

    committed = sorted(r.number for r, c in zip(results, outcomes, strict=True) if c)
    assert committed == list(range(1, len(committed) + 1)), "gap-free and no duplicates"
    after = await _allocate(app_sessionmaker, seed)
    assert after.number == len(committed) + 1


async def test_a_cancelled_number_is_offered_and_reused_once(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed(app_sessionmaker)
    numbers = [await _allocate(app_sessionmaker, seed) for _ in range(3)]
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        await numbering.release(
            s,
            tenant_id=seed.tenant_id,
            series_id=numbers[1].series_id,
            number=2,
            cancelled_document_id=new_id(),
        )
        assert await numbering.offered_numbers(s, numbers[1].series_id) == [2]

    reused = await _allocate(app_sessionmaker, seed, reuse=2)
    with pytest.raises(numbering.NumberNotAvailableError):
        await _allocate(app_sessionmaker, seed, reuse=2)
    nxt = await _allocate(app_sessionmaker, seed)

    assert (reused.number, reused.reused, reused.formatted) == (2, True, "INV-2026-00002")
    assert nxt.number == 4, "reusing a number does not move the series"
    async with app_sessionmaker() as s, s.begin():
        await apply_context(s, tenant_id=seed.tenant_id)
        assert await numbering.offered_numbers(s, numbers[1].series_id) == []


async def test_only_the_owner_node_numbers_a_series(
    app_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    seed = await _seed(app_sessionmaker)
    await _allocate(app_sessionmaker, seed)

    with pytest.raises(numbering.NotNumberOwnerError):
        await _allocate(app_sessionmaker, seed, node_id=new_id())


async def test_unknown_document_type_is_rejected(app_sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    seed = await _seed(app_sessionmaker)

    with pytest.raises(numbering.UnknownDocumentTypeError):
        await _allocate(app_sessionmaker, seed, doc_type="spaceship")
