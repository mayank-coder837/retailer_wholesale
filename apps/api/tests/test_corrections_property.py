"""Property: whatever is posted, cancelled and reversed, the stock ledger and the books still agree (M1.7b).

Random stock documents (in, out, below zero, transfers, Item Transfers, backdated) and purchasing documents
are cancelled or reversed at random through the real services. Afterwards, as for M1.6 and M1.7:

- the trial balance balances, and stock value = the inventory account;
- the cached pools and balances equal a rebuild from stock_move, and a fresh replay finds nothing to recost;
- GRNI = what is received and not yet billed or returned; the supplier's balance = the open payable bills.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import DomainError
from app.modules.accounting import service as accounting
from app.modules.accounting.models import Role
from app.modules.documents import service as corrections
from app.modules.documents.models import Document, DocumentStatus, LineDirection
from app.modules.inventory import service as inventory
from app.modules.inventory import stock
from app.modules.inventory.models import AverageCost, StockBalance, StockMove
from app.modules.inventory.schemas import StockDocIn, StockDocType, StockLineIn
from app.modules.purchasing import service as purchasing
from app.modules.purchasing.schemas import PurchaseDocIn, PurchaseDocType, PurchaseLineIn
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.schemas import WarehouseIn
from tests.factories import (
    TenantSeed,
    _new_party,
    adjustment,
    dubai_today,
    post_stock,
    seed_tenant,
    stock_product,
)

pytestmark = pytest.mark.usefixtures("clean_db")
D = Decimal

qty = st.decimals(min_value=D(1), max_value=D(40), places=0)
cost = st.decimals(min_value=D(0), max_value=D("99.99"), places=2)
item = st.integers(min_value=0, max_value=2)
day = st.integers(min_value=0, max_value=5)
pick = st.integers(min_value=0, max_value=50)
undo = st.one_of(
    st.tuples(st.just("cancel"), pick),
    st.tuples(st.just("reverse"), pick, st.integers(min_value=0, max_value=3)),
)
stock_operation = st.one_of(
    st.tuples(st.just("in"), item, qty, cost, day),
    st.tuples(st.just("out"), item, qty, day),
    st.tuples(st.just("transfer"), item, qty, day),
    st.tuples(st.just("item_transfer"), item, item, qty, day),
    undo,
)


async def _undo(s: AsyncSession, seed: TenantSeed, op: tuple[object, ...], posted: list[uuid.UUID]) -> None:
    """Cancels or reverses one of the posted documents; refusals (dependants, already undone) are fine."""
    if not posted:
        return
    doc_id = posted[int(op[1]) % len(posted)]  # type: ignore[call-overload]
    doc = await s.get(Document, doc_id)
    assert doc is not None
    try:
        async with s.begin_nested():
            if op[0] == "cancel":
                await corrections.cancel(s, doc_id, user_id=seed.user_id, reason="property")
            else:
                await corrections.reverse(
                    s,
                    doc_id,
                    node_id=seed.node_id,
                    user_id=seed.user_id,
                    reason="property",
                    day=doc.doc_date + timedelta(days=int(op[2])),  # type: ignore[call-overload]
                    may_override_lock=False,
                    override_reason=None,
                )
    except DomainError:
        pass  # refused (followed by later documents, already undone, a reversal): nothing was written


async def _stock_agrees(s: AsyncSession, seed: TenantSeed, variants: list[uuid.UUID]) -> None:
    valuation = await inventory.valuation(s, seed.business_id)
    assert valuation.difference == 0, valuation
    tb = await accounting.trial_balance(s, seed.business_id, dubai_today() + timedelta(days=30))
    assert tb.total_debit == tb.total_credit
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


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=st.lists(stock_operation, min_size=1, max_size=14))
async def test_cancelled_and_reversed_stock_documents_keep_the_ledger_and_the_books_equal(
    app_sessionmaker: async_sessionmaker[AsyncSession], operations: list[tuple[object, ...]]
) -> None:
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        second = await tenancy.create_warehouse(
            s, seed.tenant_id, seed.branch_id, WarehouseIn(code="W2", name_en="Back", name_ar="خلفي")
        )
        variants = [(await stock_product(s, seed)).variants[0].id for _ in range(3)]
        today = dubai_today()
        posted: list[uuid.UUID] = []

        def header(kind: StockDocType, when: object, lines: list[StockLineIn], **extra: object) -> StockDocIn:
            return StockDocIn(
                doc_type=kind,
                business_id=seed.business_id,
                branch_id=seed.branch_id,
                doc_date=when,  # type: ignore[arg-type]
                warehouse_id=seed.warehouse_id,
                lines=lines,
                **extra,  # type: ignore[arg-type]
            )

        for op in operations:
            kind = op[0]
            if kind in ("cancel", "reverse"):
                await _undo(s, seed, op, posted)
                continue
            when = today + timedelta(days=int(op[-1]))  # type: ignore[call-overload]
            if kind == "in":
                line = StockLineIn(
                    variant_id=variants[op[1]],  # type: ignore[index]
                    unit="pcs",
                    qty=D(str(op[2])),
                    direction=LineDirection.IN,
                    unit_cost=D(str(op[3])),
                )
                posted.append(await post_stock(s, seed, adjustment(seed, [line], when)))
            elif kind == "out":
                line = StockLineIn(
                    variant_id=variants[op[1]],  # type: ignore[index]
                    unit="pcs",
                    qty=D(str(op[2])),
                    direction=LineDirection.OUT,
                )
                posted.append(await post_stock(s, seed, adjustment(seed, [line], when)))
            elif kind == "transfer":
                line = StockLineIn(variant_id=variants[op[1]], unit="pcs", qty=D(str(op[2])))  # type: ignore[index]
                doc = header(StockDocType.STOCK_TRANSFER, when, [line], to_warehouse_id=second.id)
                posted.append(await post_stock(s, seed, doc))
            elif op[1] != op[2]:
                lines = [
                    StockLineIn(
                        variant_id=variants[op[1]],  # type: ignore[index]
                        unit="pcs",
                        qty=D(str(op[3])),
                        direction=LineDirection.OUT,
                    ),
                    StockLineIn(
                        variant_id=variants[op[2]],  # type: ignore[index]
                        unit="pcs",
                        qty=D(str(op[3])),
                        direction=LineDirection.IN,
                    ),
                ]
                posted.append(await post_stock(s, seed, header(StockDocType.ITEM_TRANSFER, when, lines)))

        await _stock_agrees(s, seed, variants)


price = st.decimals(min_value=D("0.50"), max_value=D("99.99"), places=2)
purchase_operation = st.one_of(
    st.tuples(st.just("receive"), st.integers(min_value=1, max_value=30), price),
    st.tuples(st.just("bill"), pick, st.integers(min_value=1, max_value=30), price),
    st.tuples(st.just("return_bill"), pick, st.integers(min_value=1, max_value=30)),
    undo,
)


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=st.lists(purchase_operation, min_size=1, max_size=12))
async def test_cancelled_and_reversed_purchases_keep_grni_and_payables_in_line(
    app_sessionmaker: async_sessionmaker[AsyncSession], operations: list[tuple[object, ...]]
) -> None:
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        supplier = (await _new_party(s, seed)).id
        variant = (await stock_product(s, seed)).variants[0].id
        today = dubai_today()
        posted: list[uuid.UUID] = []
        receipts: list[uuid.UUID] = []  # receipt lines
        bills: list[uuid.UUID] = []  # bill lines

        async def post(kind: PurchaseDocType, line: PurchaseLineIn, **extra: object) -> uuid.UUID:
            data = PurchaseDocIn(
                doc_type=kind,
                business_id=seed.business_id,
                branch_id=seed.branch_id,
                doc_date=today,
                party_id=supplier,
                warehouse_id=seed.warehouse_id if kind is not PurchaseDocType.PURCHASE_BILL else None,
                lines=[line],
                **extra,  # type: ignore[arg-type]
            )
            doc = await purchasing.create_draft(s, seed.tenant_id, data)
            await purchasing.post(
                s,
                doc.id,
                node_id=seed.node_id,
                user_id=seed.user_id,
                may_override_lock=False,
                override_reason=None,
            )
            posted.append(doc.id)
            return (await purchasing.get_purchase_document(s, doc.id)).lines[0].id

        for op in operations:
            if op[0] in ("cancel", "reverse"):
                await _undo(s, seed, op, posted)
                continue
            if op[0] == "receive":
                line = PurchaseLineIn(
                    variant_id=variant, unit="pcs", qty=D(int(op[1])), unit_price=D(str(op[2]))
                )  # type: ignore[call-overload]
                receipts.append(await post(PurchaseDocType.GOODS_RECEIPT, line))
                continue
            pool = receipts if op[0] == "bill" else bills
            live = [x for x in pool if await _open(s, x) > 0]
            if not live:
                continue
            source = live[int(op[1]) % len(live)]  # type: ignore[call-overload]
            take = D(min(int(op[2]), int(await _open(s, source))))  # type: ignore[call-overload]
            if op[0] == "bill":
                line = PurchaseLineIn(
                    variant_id=variant, unit="pcs", qty=take, unit_price=D(str(op[3])), source_line_id=source
                )
                bills.append(await post(PurchaseDocType.PURCHASE_BILL, line, supplier_ref=f"S-{len(bills)}"))
            else:
                line = PurchaseLineIn(variant_id=variant, unit="pcs", qty=take, source_line_id=source)
                await post(PurchaseDocType.PURCHASE_RETURN, line)

        await _stock_agrees(s, seed, [variant])
        roles = await accounting.role_accounts(s, seed.business_id)
        grni = await accounting.account_balance(s, seed.business_id, roles[Role.GRNI])
        still_open = D(0)
        for line_id in receipts:
            row = await s.get(purchasing.DocumentLine, line_id)
            assert row is not None
            doc = await s.get(Document, row.document_id)
            assert doc is not None
            if doc.status == DocumentStatus.POSTED:
                opened = await purchasing.open_on(s, [(row, PurchaseDocType.GOODS_RECEIPT.value)])
                still_open += opened[line_id].value or 0
        assert -grni == still_open, "GRNI must equal what is received and not yet billed or returned"
        statement = await accounting.party_ledger(s, seed.business_id, supplier, None, None)
        assert statement.on_account == 0, statement


async def _open(s: AsyncSession, line_id: uuid.UUID) -> Decimal:
    row = await s.get(purchasing.DocumentLine, line_id)
    assert row is not None
    doc = await purchasing.get_document(s, row.document_id)
    if doc.status != DocumentStatus.POSTED:
        return D(0)
    return (await purchasing.open_on(s, [(row, doc.doc_type)]))[line_id].qty
