"""Property: whatever stock documents are posted, the stock ledger and the books agree (roadmap M1.6).

Random sequences of adjustments in and out (going below zero), warehouse transfers, Item Transfers and
backdated documents go through the real services. Afterwards:

- Σ stock_move.value = the inventory account's balance (CLAUDE.md rule 5);
- the cached pools and balances equal a rebuild from stock_move;
- a fresh replay of every item finds nothing left to recost;
- every Item Transfer moved exactly as much value in as out.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.documents.models import LineDirection
from app.modules.inventory import service as inventory
from app.modules.inventory import stock
from app.modules.inventory.models import AverageCost, MoveType, StockBalance, StockMove
from app.modules.inventory.schemas import StockDocIn, StockDocType, StockLineIn
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.schemas import WarehouseIn
from tests.factories import adjustment, dubai_today, post_stock, seed_tenant, stock_product

pytestmark = pytest.mark.usefixtures("clean_db")
D = Decimal

qty = st.decimals(min_value=D(1), max_value=D(40), places=0)
cost = st.decimals(min_value=D(0), max_value=D("99.99"), places=2)
item = st.integers(min_value=0, max_value=2)
day = st.integers(min_value=0, max_value=5)  # days after today: later documents may be dated earlier
operation = st.one_of(
    st.tuples(st.just("in"), item, qty, cost, day),
    st.tuples(st.just("out"), item, qty, day),
    st.tuples(st.just("transfer"), item, qty, day),
    st.tuples(st.just("item_transfer"), item, item, qty, day),
)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=st.lists(operation, min_size=1, max_size=12))
async def test_random_stock_documents_keep_the_stock_ledger_and_the_books_equal(
    app_sessionmaker: async_sessionmaker[AsyncSession], operations: list[tuple[object, ...]]
) -> None:
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        second = await tenancy.create_warehouse(
            s, seed.tenant_id, seed.branch_id, WarehouseIn(code="W2", name_en="Back", name_ar="خلفي")
        )
        variants = [(await stock_product(s, seed)).variants[0].id for _ in range(3)]
        today = dubai_today()

        for op in operations:
            kind = op[0]
            when = today + timedelta(days=int(op[-1]))  # type: ignore[call-overload]
            if kind == "in":
                line = StockLineIn(
                    variant_id=variants[op[1]],  # type: ignore[index]
                    unit="pcs",
                    qty=D(str(op[2])),
                    direction=LineDirection.IN,
                    unit_cost=D(str(op[3])),
                )
                await post_stock(s, seed, adjustment(seed, [line], when))
            elif kind == "out":
                line = StockLineIn(
                    variant_id=variants[op[1]], unit="pcs", qty=D(str(op[2])), direction=LineDirection.OUT
                )  # type: ignore[index]
                await post_stock(s, seed, adjustment(seed, [line], when))
            elif kind == "transfer":
                doc = StockDocIn(
                    doc_type=StockDocType.STOCK_TRANSFER,
                    business_id=seed.business_id,
                    branch_id=seed.branch_id,
                    doc_date=when,
                    warehouse_id=seed.warehouse_id,
                    to_warehouse_id=second.id,
                    lines=[StockLineIn(variant_id=variants[op[1]], unit="pcs", qty=D(str(op[2])))],  # type: ignore[index]
                )
                await post_stock(s, seed, doc)
            elif op[1] != op[2]:
                doc = StockDocIn(
                    doc_type=StockDocType.ITEM_TRANSFER,
                    business_id=seed.business_id,
                    branch_id=seed.branch_id,
                    doc_date=when,
                    warehouse_id=seed.warehouse_id,
                    lines=[
                        StockLineIn(
                            variant_id=variants[op[1]],
                            unit="pcs",
                            qty=D(str(op[3])),
                            direction=LineDirection.OUT,
                        ),  # type: ignore[index]
                        StockLineIn(
                            variant_id=variants[op[2]],
                            unit="pcs",
                            qty=D(str(op[3])),
                            direction=LineDirection.IN,
                        ),  # type: ignore[index]
                    ],
                )
                await post_stock(s, seed, doc)

        valuation = await inventory.valuation(s, seed.business_id)
        assert valuation.difference == 0, valuation

        for variant in variants:
            moves = (
                await s.execute(
                    select(
                        func.coalesce(func.sum(StockMove.qty_base), 0),
                        func.coalesce(func.sum(StockMove.value), 0),
                    ).where(StockMove.business_id == seed.business_id, StockMove.variant_id == variant)
                )
            ).one()
            pool = (await s.scalars(select(AverageCost).where(AverageCost.variant_id == variant))).first()
            if pool is not None:
                assert (pool.qty_base, pool.value) == (moves[0], moves[1])
            balances = (
                await s.execute(
                    select(
                        func.coalesce(func.sum(StockBalance.qty_base), 0),
                        func.coalesce(func.sum(StockBalance.value), 0),
                    ).where(StockBalance.variant_id == variant)
                )
            ).one()
            assert tuple(balances) == tuple(moves)
            replayed = await stock.replay(s, seed.business_id, variant)
            assert replayed.corrections == [], "posting and a full replay disagree"

        transfers = await s.execute(
            select(StockMove.document_id, StockMove.move_type, func.sum(StockMove.value))
            .where(
                StockMove.move_type.in_(
                    [MoveType.ITEM_TRANSFER_OUT.value, MoveType.ITEM_TRANSFER_IN.value, MoveType.RECOST.value]
                )
            )
            .group_by(StockMove.document_id, StockMove.move_type)
        )
        per_doc: dict[object, Decimal] = {}
        item_docs: set[object] = set()
        for doc_id, move_type, value in transfers:
            if move_type != MoveType.RECOST.value:
                item_docs.add(doc_id)
                per_doc[doc_id] = per_doc.get(doc_id, D(0)) + value
        for doc_id in item_docs:
            assert per_doc[doc_id] == 0, "an Item Transfer moved different values in and out"
