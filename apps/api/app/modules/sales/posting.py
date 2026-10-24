"""Sales posting rules, pure (posting-rules.md rules 1 to 4 and 34; decision A8).

The service works out the values (costs from the stock ledger, goods-delivered-not-invoiced cleared per
delivery
line); these functions turn them into balanced journal lines. Nothing here reads the database.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.modules.accounting.posting import HEADER, JournalLine

ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class Accounts:
    ar_control: uuid.UUID
    sales: uuid.UUID
    sales_returns: uuid.UUID
    vat_output: uuid.UUID
    cogs: uuid.UUID
    inventory: uuid.UUID
    gdni: uuid.UUID  # goods delivered not invoiced (A8)
    cost_adjustment: uuid.UUID  # stock_cost_adjustment: rule 27 when returned goods cover negative stock
    rounding: uuid.UUID | None  # round-off on the bill total (L4, rule 34); only needed when it is on


@dataclass
class Entry:
    """Debits minus credits per account, plus what the customer owes (or the cash taken) on one line."""

    accounts: Accounts
    net: dict[uuid.UUID, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    customer: Decimal = ZERO  # + debit (they owe more), - credit (they owe less)

    def debit(self, account: uuid.UUID, amount: Decimal) -> None:
        self.net[account] += amount

    def credit(self, account: uuid.UUID, amount: Decimal) -> None:
        self.net[account] -= amount

    def revaluation(self, change: Decimal) -> None:
        """Rule 27 when returned goods arrive into negative stock."""
        self.debit(self.accounts.inventory, change)
        self.credit(self.accounts.cost_adjustment, change)

    def lines(
        self,
        party_id: uuid.UUID | None,
        description: str | None = None,
        *,
        cash_account: uuid.UUID | None = None,
    ) -> list[JournalLine]:
        """The entry's lines. A cash sale debits the cash or bank account instead of the customer."""
        out: list[JournalLine] = []
        for account, amount in self.net.items():
            if amount > 0:
                out.append(JournalLine(account, amount, ZERO, description=description))
            elif amount < 0:
                out.append(JournalLine(account, ZERO, -amount, description=description))
        if self.customer:
            debit, credit = (self.customer, ZERO) if self.customer > 0 else (ZERO, -self.customer)
            if cash_account is not None:
                out.append(JournalLine(cash_account, debit, credit, description=description))
            else:
                out.append(
                    JournalLine(
                        self.accounts.ar_control, debit, credit, party_id, description, allocation_key=HEADER
                    )
                )
        return out


def delivery(entry: Entry, cost: Decimal) -> None:
    """Rule 2: goods leave at average cost, not yet invoiced — Dr GDNI / Cr inventory."""
    entry.debit(entry.accounts.gdni, cost)
    entry.credit(entry.accounts.inventory, cost)


def sale(entry: Entry, net: Decimal, vat: Decimal) -> None:
    """Rules 1 and 3, the sale itself: Dr the customer (or cash) / Cr sales, Cr output VAT."""
    entry.credit(entry.accounts.sales, net)
    entry.credit(entry.accounts.vat_output, vat)
    entry.customer += net + vat


def cost_from_stock(entry: Entry, cost: Decimal) -> None:
    """Rule 1: no delivery note, the goods leave now — Dr COGS / Cr inventory."""
    entry.debit(entry.accounts.cogs, cost)
    entry.credit(entry.accounts.inventory, cost)


def cost_from_delivery(entry: Entry, cost: Decimal) -> None:
    """Rule 3: the delivery note's cost moves to COGS — Dr COGS / Cr GDNI."""
    entry.debit(entry.accounts.cogs, cost)
    entry.credit(entry.accounts.gdni, cost)


def sales_return(entry: Entry, net: Decimal, vat: Decimal, cost: Decimal) -> None:
    """Rule 4: Dr sales returns, Dr output VAT / Cr the customer at the invoice's price; goods back at the
    cost
    they left at — Dr inventory / Cr COGS."""
    entry.debit(entry.accounts.sales_returns, net)
    entry.debit(entry.accounts.vat_output, vat)
    entry.customer -= net + vat
    entry.debit(entry.accounts.inventory, cost)
    entry.credit(entry.accounts.cogs, cost)


def round_off(entry: Entry, adjustment: Decimal) -> None:
    """Rule 34: the bill total rounded; the customer owes the rounded total, the difference to
    ``rounding``."""
    if not adjustment:
        return
    if entry.accounts.rounding is None:
        raise ValueError("round-off is on but no account has the rounding role")
    entry.customer += adjustment
    entry.credit(entry.accounts.rounding, adjustment)


def share(open_value: Decimal, open_qty: Decimal, qty: Decimal) -> Decimal:
    """The part of a line's open value that ``qty`` takes; the last quantity takes what is left."""
    if qty >= open_qty:
        return open_value
    return fils(open_value * qty / open_qty)


def fils(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
