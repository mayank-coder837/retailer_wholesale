"""Purchase posting rules, pure (posting-rules.md rules 7 to 9; decision A4).

The service works out the values (stock values from the cost pools, GRNI cleared from the receipt lines);
these functions turn them into balanced journal lines by account. Nothing here reads the database.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.modules.accounting.posting import HEADER, JournalLine

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Accounts:
    inventory: uuid.UUID
    grni: uuid.UUID
    vat_input: uuid.UUID
    ap_control: uuid.UUID
    price_variance: uuid.UUID
    cost_adjustment: uuid.UUID  # stock_cost_adjustment: rule 27 and purchase-return differences


@dataclass
class Entry:
    """Debits minus credits per account, plus the supplier's line; turned into journal lines at the end."""

    accounts: Accounts
    net: dict[uuid.UUID, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    supplier: Decimal = ZERO  # + debit (we owe less), - credit (we owe more)

    def debit(self, account: uuid.UUID, amount: Decimal) -> None:
        self.net[account] += amount

    def credit(self, account: uuid.UUID, amount: Decimal) -> None:
        self.net[account] -= amount

    def revaluation(self, change: Decimal) -> None:
        """Rule 27 on arrival: the change to inventory value against stock cost adjustment."""
        self.debit(self.accounts.inventory, change)
        self.credit(self.accounts.cost_adjustment, change)

    def lines(self, party_id: uuid.UUID | None, description: str | None = None) -> list[JournalLine]:
        out: list[JournalLine] = []
        for account, amount in self.net.items():
            if amount > 0:
                out.append(JournalLine(account, amount, ZERO, description=description))
            elif amount < 0:
                out.append(JournalLine(account, ZERO, -amount, description=description))
        if self.supplier:
            debit, credit = (self.supplier, ZERO) if self.supplier > 0 else (ZERO, -self.supplier)
            out.append(
                JournalLine(
                    self.accounts.ap_control, debit, credit, party_id, description, allocation_key=HEADER
                )
            )
        return out


def receipt(entry: Entry, value: Decimal) -> None:
    """Rule 7: goods in, bill not yet received — Dr inventory / Cr GRNI."""
    entry.debit(entry.accounts.inventory, value)
    entry.credit(entry.accounts.grni, value)


def bill_matched(entry: Entry, net: Decimal, vat: Decimal, grni_cleared: Decimal) -> None:
    """Rule 8: a bill line for goods already received — Dr GRNI (what was received), the price difference to
    the variance account (A4), Dr input VAT / Cr the supplier."""
    entry.debit(entry.accounts.grni, grni_cleared)
    entry.debit(entry.accounts.price_variance, net - grni_cleared)
    entry.debit(entry.accounts.vat_input, vat)
    entry.supplier -= net + vat


def bill_direct(entry: Entry, debit_account: uuid.UUID, net: Decimal, vat: Decimal) -> None:
    """A bill line without a receipt: Dr inventory (stock comes in now) or the expense account."""
    entry.debit(debit_account, net)
    entry.debit(entry.accounts.vat_input, vat)
    entry.supplier -= net + vat


def return_to_supplier(entry: Entry, net: Decimal, vat: Decimal, stock_value: Decimal) -> None:
    """Rule 9 against a bill: Dr the supplier (price + VAT) / Cr inventory (the cost it leaves at), Cr input
    VAT, the difference between price and cost to stock cost adjustment."""
    entry.supplier += net + vat
    entry.credit(entry.accounts.vat_input, vat)
    entry.credit(entry.accounts.inventory, stock_value)
    entry.credit(entry.accounts.cost_adjustment, net - stock_value)


def return_unbilled(entry: Entry, grni_cleared: Decimal, stock_value: Decimal) -> None:
    """Rule 9 against an unbilled receipt: Dr GRNI / Cr inventory, the difference to stock cost adjustment."""
    entry.debit(entry.accounts.grni, grni_cleared)
    entry.credit(entry.accounts.inventory, stock_value)
    entry.credit(entry.accounts.cost_adjustment, grni_cleared - stock_value)


def share(open_value: Decimal, open_qty: Decimal, qty: Decimal) -> Decimal:
    """The part of a receipt line's open GRNI that ``qty`` clears; the last quantity takes what is left."""
    if qty >= open_qty:
        return open_value
    return fils(open_value * qty / open_qty)


def fils(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
