"""Property: whatever is ordered, delivered, invoiced, returned, cancelled or reversed, the books agree.
(M1.8).

Random orders delivered in parts, invoices after delivery and direct (on credit and for cash), returns against
invoices, with random cancels and reversals, go through the real services. Afterwards:

- the trial balance balances, and stock value = the inventory account;
- the GDNI account = the cost still open on every posted delivery line (A8);
- the customer's balance = their open bills, with nothing left unexplained on account.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import DomainError
from app.modules.accounting import service as accounting
from app.modules.accounting.models import Account, Role
from app.modules.documents import service as corrections
from app.modules.documents.models import Document, DocumentLine, DocumentStatus, LineDirection
from app.modules.inventory import service as inventory
from app.modules.inventory.schemas import StockLineIn
from app.modules.sales import service as sales
from app.modules.sales.schemas import SalesDocIn, SalesDocType, SalesLineIn
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

qty = st.integers(min_value=1, max_value=20)
price = st.decimals(min_value=D("0.50"), max_value=D("99.99"), places=2)
pick = st.integers(min_value=0, max_value=50)
operation = st.one_of(
    st.tuples(st.just("order"), qty, price),
    st.tuples(st.just("deliver"), pick, qty),
    st.tuples(st.just("invoice_delivery"), pick, qty),
    st.tuples(st.just("invoice_direct"), qty, price, st.booleans()),
    st.tuples(st.just("return"), pick, qty),
    st.tuples(st.just("receive_stock"), qty, price),
    st.tuples(st.just("cancel"), pick),
    st.tuples(st.just("reverse"), pick, st.integers(min_value=0, max_value=2)),
)


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=st.lists(operation, min_size=1, max_size=14))
async def test_random_sales_keep_gdni_stock_and_receivables_in_line(
    app_sessionmaker: async_sessionmaker[AsyncSession], operations: list[tuple[object, ...]]
) -> None:
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        customer = (await _new_party(s, seed)).id
        variant = (await stock_product(s, seed)).variants[0].id
        today = dubai_today()
        cash = (
            await s.scalars(
                select(Account.id).where(Account.business_id == seed.business_id, Account.code == "1110")
            )
        ).one()
        stock_in = StockLineIn(
            variant_id=variant, unit="pcs", qty=D(50), direction=LineDirection.IN, unit_cost=D(7)
        )
        await post_stock(s, seed, adjustment(seed, [stock_in], today))
        posted: list[uuid.UUID] = []
        orders: list[uuid.UUID] = []  # order lines
        deliveries: list[uuid.UUID] = []  # delivery lines
        invoices: list[uuid.UUID] = []  # invoice lines

        async def post(kind: SalesDocType, line: SalesLineIn, **extra: object) -> uuid.UUID:
            data = SalesDocIn(
                doc_type=kind,
                business_id=seed.business_id,
                branch_id=seed.branch_id,
                doc_date=today,
                party_id=customer,
                warehouse_id=seed.warehouse_id,
                lines=[line],
                **extra,  # type: ignore[arg-type]
            )
            doc = await sales.create_draft(s, seed.tenant_id, data)
            await sales.post(
                s,
                doc.id,
                node_id=seed.node_id,
                user_id=seed.user_id,
                may_override_lock=False,
                override_reason=None,
                may_override_min=True,
                may_override_credit=True,
                credit_override_reason="property",
            )
            posted.append(doc.id)
            return (await sales.get_sales_document(s, doc.id)).lines[0].id

        for op in operations:
            kind = op[0]
            if kind == "order":
                line = SalesLineIn(
                    variant_id=variant, unit="pcs", qty=D(int(op[1])), unit_price=D(str(op[2]))
                )  # type: ignore[call-overload]
                orders.append(await post(SalesDocType.SALES_ORDER, line))
            elif kind == "invoice_direct":
                line = SalesLineIn(
                    variant_id=variant, unit="pcs", qty=D(int(op[1])), unit_price=D(str(op[2]))
                )  # type: ignore[call-overload]
                extra = {"cash_account_id": cash} if op[3] else {}
                invoices.append(await post(SalesDocType.SALES_INVOICE, line, **extra))
            elif kind == "receive_stock":
                more = StockLineIn(
                    variant_id=variant,
                    unit="pcs",
                    qty=D(int(op[1])),
                    direction=LineDirection.IN,
                    unit_cost=D(str(op[2])),  # type: ignore[call-overload]
                )
                await post_stock(s, seed, adjustment(seed, [more], today))
            elif kind in ("cancel", "reverse"):
                await _undo(s, seed, op, posted)
            else:
                pool, follows, source_type = {
                    "deliver": (orders, SalesDocType.DELIVERY_NOTE, SalesDocType.SALES_ORDER),
                    "invoice_delivery": (deliveries, SalesDocType.SALES_INVOICE, SalesDocType.DELIVERY_NOTE),
                    "return": (invoices, SalesDocType.SALES_RETURN, SalesDocType.SALES_INVOICE),
                }[str(kind)]
                live = [x for x in pool if await _open(s, x, source_type.value) > 0]
                if not live:
                    continue
                source = live[int(op[1]) % len(live)]  # type: ignore[call-overload]
                take = D(min(int(op[2]), int(await _open(s, source, source_type.value))))  # type: ignore[call-overload]
                line = SalesLineIn(variant_id=variant, unit="pcs", qty=take, source_line_id=source)
                created = await post(follows, line)
                if follows is SalesDocType.DELIVERY_NOTE:
                    deliveries.append(created)
                elif follows is SalesDocType.SALES_INVOICE:
                    invoices.append(created)

        tb = await accounting.trial_balance(s, seed.business_id, today + timedelta(days=5))
        assert tb.total_debit == tb.total_credit
        assert (await inventory.valuation(s, seed.business_id)).difference == 0
        roles = await accounting.role_accounts(s, seed.business_id)
        gdni = await accounting.account_balance(s, seed.business_id, roles[Role.GOODS_DELIVERED_NOT_INVOICED])
        still = D(0)
        for line_id in deliveries:
            row = await s.get(DocumentLine, line_id)
            assert row is not None
            doc = await s.get(Document, row.document_id)
            assert doc is not None
            if doc.status == DocumentStatus.POSTED:
                still += (await sales.open_on(s, [(row, SalesDocType.DELIVERY_NOTE.value)]))[
                    line_id
                ].value or 0
        assert gdni == still, "GDNI must equal the cost delivered and not yet invoiced"
        statement = await accounting.party_ledger(s, seed.business_id, customer, None, None)
        assert statement.on_account == 0, statement


async def _open(s: AsyncSession, line_id: uuid.UUID, doc_type: str) -> Decimal:
    row = await s.get(DocumentLine, line_id)
    assert row is not None
    doc = await s.get(Document, row.document_id)
    assert doc is not None
    if doc.status != DocumentStatus.POSTED:
        return D(0)
    return (await sales.open_on(s, [(row, doc_type)]))[line_id].qty


async def _undo(s: AsyncSession, seed: TenantSeed, op: tuple[object, ...], posted: list[uuid.UUID]) -> None:
    """Cancels or reverses one of the posted documents; refusals (followed, already undone, cancel-only) are
    fine."""
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
        pass  # refused before anything was written
