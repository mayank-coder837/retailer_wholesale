"""Voucher posting rules, pure (posting-rules.md rules 5, 6, 10 to 14 and 34).

A voucher is the lines the user typed plus, for single-entry types, the header's cash/bank account or party.
``journal_for`` returns the balanced journal lines it posts. Nothing here reads the database: accounts are
already resolved, so every rule is tested with plain values.
"""

import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.core.money import currency_decimals
from app.modules.tax.codes import Treatment

HEADER = -1  # the allocation key of the header party's line (credit and debit notes)


class VoucherType(StrEnum):
    RECEIPT = "receipt"
    PAYMENT = "payment"
    CONTRA = "contra"
    JOURNAL = "journal"
    EXPENSE = "expense"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"


# Single-entry types: the side every typed line is on (the header takes the other side).
LINE_SIDE: dict[VoucherType, str] = {
    VoucherType.RECEIPT: "credit",
    VoucherType.PAYMENT: "debit",
    VoucherType.EXPENSE: "debit",
    VoucherType.CREDIT_NOTE: "debit",
    VoucherType.DEBIT_NOTE: "credit",
}
DOUBLE_ENTRY = frozenset({VoucherType.CONTRA, VoucherType.JOURNAL})
WITH_CASH_ACCOUNT = frozenset({VoucherType.RECEIPT, VoucherType.PAYMENT, VoucherType.EXPENSE})
WITH_PARTY = frozenset({VoucherType.CREDIT_NOTE, VoucherType.DEBIT_NOTE})
WITH_VAT = frozenset({VoucherType.EXPENSE, VoucherType.CREDIT_NOTE, VoucherType.DEBIT_NOTE})
# VAT rates a voucher line may carry until tax codes arrive (M1.10): UAE zero rate and standard rate.
VAT_RATES = (Decimal(0), Decimal(5))


class UnbalancedError(ValueError):
    """The lines of a journal or contra do not add up."""


@dataclass(frozen=True, slots=True)
class Line:
    account_id: uuid.UUID
    debit: Decimal
    credit: Decimal
    party_id: uuid.UUID | None = None
    vat_rate: Decimal = Decimal(0)
    description: str | None = None
    tax_code: str | None = None
    treatment: Treatment = (
        Treatment.RECOVERABLE
    )  # charged VAT: input (expense, debit note) or output (credit note)

    @property
    def amount(self) -> Decimal:
        return self.debit or self.credit


@dataclass(frozen=True, slots=True)
class Voucher:
    doc_type: VoucherType
    lines: tuple[Line, ...]
    currency: str = "AED"
    account_id: uuid.UUID | None = None  # receipt, payment, expense: the cash or bank account
    party_id: uuid.UUID | None = None  # credit / debit note: the customer or supplier
    party_account_id: uuid.UUID | None = None  # its control account (receivable / payable)
    narration: str | None = None


@dataclass(frozen=True, slots=True)
class Accounts:
    """The role accounts voucher posting needs."""

    vat_input: uuid.UUID
    vat_output: uuid.UUID
    rounding: uuid.UUID


@dataclass(frozen=True, slots=True)
class JournalLine:
    account_id: uuid.UUID
    debit: Decimal
    credit: Decimal
    party_id: uuid.UUID | None = None
    description: str | None = None
    allocation_key: int | None = None  # index of the voucher line (or HEADER) whose bill-wise choices apply


@dataclass(frozen=True, slots=True)
class Posted:
    lines: tuple[JournalLine, ...]
    total: Decimal  # the voucher's amount: cash/bank moved, the party's total, or the journal's debits
    vat_total: Decimal
    line_vat: tuple[Decimal, ...] = field(default=())
    round_off: Decimal = Decimal(0)


def vat_of(net: Decimal, rate: Decimal, currency: str = "AED") -> Decimal:
    """VAT of one line, half-up to the currency's smallest unit (decision A3)."""
    exponent = Decimal(1).scaleb(-currency_decimals(currency))
    return (net * rate / 100).quantize(exponent, rounding=ROUND_HALF_UP)


def _side(
    account_id: uuid.UUID,
    amount: Decimal,
    side: str,
    *,
    party_id: uuid.UUID | None = None,
    description: str | None = None,
    allocation_key: int | None = None,
) -> JournalLine:
    debit, credit = (amount, Decimal(0)) if side == "debit" else (Decimal(0), amount)
    return JournalLine(account_id, debit, credit, party_id, description, allocation_key)


def _other(side: str) -> str:
    return "credit" if side == "debit" else "debit"


def journal_for(voucher: Voucher, accounts: Accounts, *, round_off: Decimal = Decimal(0)) -> Posted:
    """The journal lines of a voucher. ``round_off`` (receipts and payments, rule 34) is what the cash or bank
    amount differs from the lines' total; it posts to the round-off account so nothing disappears."""
    if voucher.doc_type in DOUBLE_ENTRY:
        return _double_entry(voucher)
    side = LINE_SIDE[voucher.doc_type]
    out: list[JournalLine] = []
    vats: list[Decimal] = []
    charged = blocked = self_assessed = Decimal(0)
    for i, line in enumerate(voucher.lines):
        vat = (
            vat_of(line.amount, line.vat_rate, voucher.currency)
            if voucher.doc_type in WITH_VAT
            else Decimal(0)
        )
        vats.append(vat)
        if line.treatment is Treatment.BLOCKED:  # SR-B: the VAT is part of the expense (rule 8)
            blocked += vat
        elif line.treatment is Treatment.REVERSE_CHARGE:  # rule 29: self-assessed, not paid to anyone
            self_assessed += vat
        else:
            charged += vat
        out.append(
            _side(
                line.account_id,
                line.amount + (vat if line.treatment is Treatment.BLOCKED else Decimal(0)),
                side,
                party_id=line.party_id,
                description=line.description,
                allocation_key=i if line.party_id else None,
            )
        )
    net = sum((line.amount for line in voucher.lines), Decimal(0))
    if charged:
        vat_account = (
            accounts.vat_output if voucher.doc_type is VoucherType.CREDIT_NOTE else accounts.vat_input
        )
        out.append(_side(vat_account, charged, side, description="VAT"))
    if self_assessed:
        out.append(_side(accounts.vat_input, self_assessed, side, description="VAT, reverse charge"))
        out.append(_side(accounts.vat_output, self_assessed, _other(side), description="VAT, reverse charge"))
    vat = charged + blocked + self_assessed
    total = net + charged + blocked

    if voucher.doc_type in WITH_PARTY:
        assert voucher.party_account_id is not None
        assert voucher.party_id is not None
        out.append(
            _side(
                voucher.party_account_id,
                total,
                _other(side),
                party_id=voucher.party_id,
                description=voucher.narration,
                allocation_key=HEADER,
            )
        )
        return Posted(tuple(out), total, vat, tuple(vats))

    assert voucher.account_id is not None
    cash = total + round_off
    if cash <= 0:
        raise ValueError("a voucher moves more than zero")
    out.append(_side(voucher.account_id, cash, _other(side), description=voucher.narration))
    if round_off:
        # The cash line is on the other side; the difference goes on the side that balances it.
        balancing = side if round_off > 0 else _other(side)
        out.append(_side(accounts.rounding, abs(round_off), balancing, description="Round-off"))
    return Posted(tuple(out), cash, vat, tuple(vats), round_off)


def _double_entry(voucher: Voucher) -> Posted:
    debit = sum((line.debit for line in voucher.lines), Decimal(0))
    credit = sum((line.credit for line in voucher.lines), Decimal(0))
    if debit != credit:
        raise UnbalancedError(f"debits {debit} and credits {credit} differ by {debit - credit}")
    lines = tuple(
        JournalLine(
            line.account_id,
            line.debit,
            line.credit,
            party_id=line.party_id,
            description=line.description,
            allocation_key=i if line.party_id else None,
        )
        for i, line in enumerate(voucher.lines)
    )
    return Posted(lines, debit, Decimal(0), tuple(Decimal(0) for _ in voucher.lines))
