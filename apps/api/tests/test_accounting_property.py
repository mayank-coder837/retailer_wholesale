"""Property: whatever balanced vouchers are posted, the books stay consistent (roadmap M1.5).

Random sequences of invoices (journals opening a bill), receipts (against a bill, as an advance, or on
account), credit notes with VAT and cash expenses go through the real services. Afterwards:

- the trial balance balances (Σ debit = Σ credit);
- each bill's open amount is what an independent model of the settlements expects;
- the customer's ledger balance equals the signed open bills plus the money left on account.
"""

from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.accounting import ledger, vouchers
from app.modules.accounting import service as accounting
from app.modules.accounting.models import AllocationType, Bill
from app.modules.accounting.models import Role as LedgerRole
from app.modules.accounting.posting import VoucherType, vat_of
from app.modules.accounting.schemas import AllocationIn, VoucherIn, VoucherLineIn
from tests.factories import (
    TenantSeed,
    _new_party,
    cash_account,
    charge_customer,
    dubai_today,
    post_voucher,
    role_account,
    seed_tenant,
)

pytestmark = pytest.mark.usefixtures("clean_db")
D = Decimal

amount = st.decimals(min_value=D("0.01"), max_value=D("5000.00"), places=2)
operation = st.one_of(
    st.tuples(st.just("invoice"), amount),
    st.tuples(st.just("receipt"), amount, st.sampled_from(["against", "advance", "on_account"])),
    st.tuples(st.just("credit_note"), amount, st.sampled_from([D(0), D(5)])),
    st.tuples(st.just("expense"), amount),
)


@dataclass
class Model:
    """What the books should say, kept without looking at them."""

    bills: dict[str, Decimal] = field(default_factory=dict)  # reference -> signed open amount (debit +)
    on_account: Decimal = Decimal("0")

    def oldest_open_invoice(self) -> str | None:
        return next((ref for ref, open_ in self.bills.items() if open_ > 0), None)


async def _bill_id(s: AsyncSession, reference: str) -> object:
    return await s.scalar(select(Bill.id).where(Bill.reference == reference))


def _receipt(
    seed: TenantSeed, cash: object, party: object, value: Decimal, items: list[AllocationIn]
) -> VoucherIn:
    return VoucherIn(
        doc_type=VoucherType.RECEIPT,
        business_id=seed.business_id,
        branch_id=seed.branch_id,
        doc_date=dubai_today(),
        account_id=cash,  # type: ignore[arg-type]
        lines=[VoucherLineIn(party_id=party, amount=value, allocations=items)],  # type: ignore[arg-type]
    )


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(operations=st.lists(operation, min_size=1, max_size=10))
async def test_random_vouchers_keep_the_books_consistent(
    app_sessionmaker: async_sessionmaker[AsyncSession], operations: list[tuple[object, ...]]
) -> None:
    model = Model()
    async with app_sessionmaker() as s, s.begin():
        seed = await seed_tenant(s)
        party = (await _new_party(s, seed)).id
        ar = await role_account(s, seed, LedgerRole.AR_CONTROL)
        sales = await role_account(s, seed, LedgerRole.SALES)
        cash = await cash_account(s, seed)
        expense = await role_account(s, seed, LedgerRole.BANK_CHARGES)

        for n, op in enumerate(operations):
            kind, value = str(op[0]), D(str(op[1]))
            if kind == "invoice":
                reference = f"INV-{n}"
                await post_voucher(s, seed, charge_customer(seed, party, ar, sales, value, reference))
                model.bills[reference] = value
            elif kind == "receipt":
                mode = op[2]
                target = model.oldest_open_invoice()
                if mode == "against" and target is not None:
                    settled = min(value, model.bills[target])
                    items = [
                        AllocationIn(
                            type=AllocationType.AGAINST_REF, amount=settled, bill_id=await _bill_id(s, target)
                        )
                    ]  # type: ignore[arg-type]
                    if value > settled:
                        items.append(AllocationIn(type=AllocationType.ON_ACCOUNT, amount=value - settled))
                    model.bills[target] -= settled
                    model.on_account -= value - settled
                elif mode == "advance":
                    items = [AllocationIn(type=AllocationType.ADVANCE, amount=value, reference=f"ADV-{n}")]
                    model.bills[f"ADV-{n}"] = -value
                else:
                    items = []
                    model.on_account -= value
                await post_voucher(s, seed, _receipt(seed, cash, party, value, items))
            elif kind == "credit_note":
                rate = D(str(op[2]))
                total = value + vat_of(value, rate)
                note = VoucherIn(
                    doc_type=VoucherType.CREDIT_NOTE,
                    business_id=seed.business_id,
                    branch_id=seed.branch_id,
                    doc_date=dubai_today(),
                    party_id=party,
                    lines=[VoucherLineIn(amount=value, vat_rate=rate)],
                )
                await post_voucher(s, seed, note)
                model.on_account -= total
            else:
                spend = VoucherIn(
                    doc_type=VoucherType.EXPENSE,
                    business_id=seed.business_id,
                    branch_id=seed.branch_id,
                    doc_date=dubai_today(),
                    account_id=cash,
                    lines=[VoucherLineIn(account_id=expense, amount=value)],
                )
                await post_voucher(s, seed, spend)

        tb = await accounting.trial_balance(s, seed.business_id, dubai_today())
        assert tb.total_debit == tb.total_credit
        bills = {
            b.reference: ledger.signed_open(b)
            for b in await ledger.open_bills(s, seed.business_id, party, include_settled=True)
        }
        assert bills == {ref: open_ for ref, open_ in model.bills.items()}
        statement = await accounting.party_ledger(s, seed.business_id, party, None, None)
        assert statement.on_account == model.on_account
        assert statement.closing == sum(model.bills.values(), D(0)) + model.on_account
        voucher_count = len(
            await vouchers.list_vouchers(s, seed.business_id, doc_types=list(VoucherType), limit=200)
        )
        assert voucher_count == len(operations)
