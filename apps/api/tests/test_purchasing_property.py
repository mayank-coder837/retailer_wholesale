"""Property: whatever is ordered, received, billed and returned, the books agree (roadmap M1.7).

Random orders received in parts, bills at changed prices, returns against bills and against unbilled
receipts go through the real services. Afterwards:

- the trial balance balances;
- the GRNI account = the GRNI still open on every receipt line (received and not yet billed or returned);
- stock value = the inventory account;
- the supplier's balance = the open payable bills, with nothing left unexplained on account.
"""

import uuid
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.accounting import service as accounting
from app.modules.accounting.models import Role
from app.modules.inventory import service as inventory
from app.modules.purchasing import service as purchasing
from app.modules.purchasing.schemas import PurchaseDocIn, PurchaseDocType, PurchaseLineIn
from tests.factories import TenantSeed, _new_party, dubai_today, seed_tenant, stock_product

pytestmark = pytest.mark.usefixtures("clean_db")
D = Decimal

qty = st.integers(min_value=1, max_value=30)
price = st.decimals(min_value=D("0.50"), max_value=D("99.99"), places=2)
pick = st.integers(min_value=0, max_value=50)
operation = st.one_of(
    st.tuples(st.just("receive"), qty, price, st.integers(min_value=0, max_value=100)),
    st.tuples(st.just("bill"), pick, qty, price),
    st.tuples(st.just("return_bill"), pick, qty),
    st.tuples(st.just("return_receipt"), pick, qty),
)


async def _post(s: AsyncSession, seed: TenantSeed, data: PurchaseDocIn) -> uuid.UUID:
    doc = await purchasing.create_draft(s, seed.tenant_id, data)
    await purchasing.post(
        s, doc.id, node_id=seed.node_id, user_id=seed.user_id, may_override_lock=False, override_reason=None
    )
    return doc.id


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=st.lists(operation, min_size=1, max_size=10))
async def test_random_purchasing_keeps_grni_stock_and_payables_in_line(
    app_sessionmaker: async_sessionmaker[AsyncSession], operations: list[tuple[object, ...]]
) -> None:
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        supplier = (await _new_party(s, seed)).id
        variant = (await stock_product(s, seed)).variants[0].id
        today = dubai_today()

        def doc(kind: PurchaseDocType, lines: list[PurchaseLineIn], **extra: object) -> PurchaseDocIn:
            return PurchaseDocIn(
                doc_type=kind,
                business_id=seed.business_id,
                branch_id=seed.branch_id,
                doc_date=today,
                party_id=supplier,
                warehouse_id=seed.warehouse_id if kind is not PurchaseDocType.PURCHASE_BILL else None,
                lines=lines,
                **extra,  # type: ignore[arg-type]
            )

        receipts: list[uuid.UUID] = []  # receipt lines
        bills: list[uuid.UUID] = []  # bill lines
        for op in operations:
            if op[0] == "receive":
                ordered, unit_price, percent = int(op[1]), D(str(op[2])), int(op[3])  # type: ignore[call-overload]
                order = await _post(
                    s,
                    seed,
                    doc(
                        PurchaseDocType.PURCHASE_ORDER,
                        [
                            PurchaseLineIn(
                                variant_id=variant,
                                unit="pcs",
                                qty=D(ordered),
                                unit_price=unit_price,
                                vat_rate=D(5),
                            )
                        ],
                    ),
                )
                order_line = (await purchasing.get_purchase_document(s, order)).lines[0].id
                taken = max(1, ordered * percent // 100)
                receipt = await _post(
                    s,
                    seed,
                    doc(
                        PurchaseDocType.GOODS_RECEIPT,
                        [
                            PurchaseLineIn(
                                variant_id=variant, unit="pcs", qty=D(taken), source_line_id=order_line
                            )
                        ],
                    ),
                )
                receipts.append((await purchasing.get_purchase_document(s, receipt)).lines[0].id)
                continue
            pool = receipts if op[0] in ("bill", "return_receipt") else bills
            if not pool:
                continue
            source = pool[int(op[1]) % len(pool)]  # type: ignore[call-overload]
            open_qty = await _open(s, source)
            if open_qty <= 0:
                continue
            take = D(min(int(op[2]), int(open_qty)))  # type: ignore[call-overload]
            if take <= 0:
                continue
            if op[0] == "bill":
                bill = await _post(
                    s,
                    seed,
                    doc(
                        PurchaseDocType.PURCHASE_BILL,
                        [
                            PurchaseLineIn(
                                variant_id=variant,
                                unit="pcs",
                                qty=take,
                                unit_price=D(str(op[3])),
                                source_line_id=source,
                            )
                        ],
                        supplier_ref=f"S-{len(bills)}",
                    ),
                )
                bills.append((await purchasing.get_purchase_document(s, bill)).lines[0].id)
            else:
                await _post(
                    s,
                    seed,
                    doc(
                        PurchaseDocType.PURCHASE_RETURN,
                        [PurchaseLineIn(variant_id=variant, unit="pcs", qty=take, source_line_id=source)],
                    ),
                )

        tb = await accounting.trial_balance(s, seed.business_id, today)
        assert tb.total_debit == tb.total_credit
        roles = await accounting.role_accounts(s, seed.business_id)
        grni = await accounting.account_balance(s, seed.business_id, roles[Role.GRNI])
        still_open = D(0)
        for line in receipts:
            row = await s.get(purchasing.DocumentLine, line)
            assert row is not None
            still_open += (await purchasing.open_on(s, [(row, PurchaseDocType.GOODS_RECEIPT.value)]))[
                line
            ].value or 0
        assert -grni == still_open, "GRNI must equal what is received and not yet billed or returned"
        assert (await inventory.valuation(s, seed.business_id)).difference == 0
        statement = await accounting.party_ledger(s, seed.business_id, supplier, None, None)
        assert statement.on_account == 0, statement


async def _open(s: AsyncSession, line_id: uuid.UUID) -> Decimal:
    row = await s.get(purchasing.DocumentLine, line_id)
    assert row is not None
    doc = await purchasing.get_document(s, row.document_id)
    return (await purchasing.open_on(s, [(row, doc.doc_type)]))[line_id].qty
