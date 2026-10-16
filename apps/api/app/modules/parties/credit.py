"""The credit check (M1.4, domain-model.md → Parties, decision A2). Pure: no database, Decimal only.

A sale is over the limit when the customer's ledger balance + open orders + this document exceed the credit
limit, and overdue when any bill is older than the credit days. Either way the customer's policy decides: warn
(the sale goes ahead with a warning) or block (only someone with ``credit.override`` may pass it, and
that is audited). Post-dated cheques in hand are shown beside the balance and never reduce it (A2): until a
cheque clears it is not money.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class CreditPolicy(StrEnum):
    WARN = "warn"
    BLOCK = "block"


class CreditDecision(StrEnum):
    OK = "ok"
    WARN = "warn"  # go ahead, show the reasons
    BLOCK = "block"  # stop, unless overridden


class CreditReason(StrEnum):
    OVER_LIMIT = "over_limit"
    OVERDUE = "overdue"


@dataclass(frozen=True)
class CreditFigures:
    ledger_balance: Decimal = Decimal(0)  # receivable per the ledger (M1.5)
    open_orders: Decimal = Decimal(0)  # confirmed, not yet invoiced (M1.8)
    oldest_overdue_days: int | None = None  # age of the oldest unpaid bill past its due date (M1.5)
    pdc_in_hand: Decimal = Decimal(0)  # received, not yet cleared (M1.9): shown, never subtracted


@dataclass(frozen=True)
class CreditTerms:
    credit_limit: Decimal | None  # None: no limit
    credit_days: int | None  # None: overdue bills never block
    policy: CreditPolicy = CreditPolicy.WARN


@dataclass(frozen=True)
class CreditResult:
    decision: CreditDecision
    reasons: list[CreditReason] = field(default_factory=list)
    exposure: Decimal = Decimal(0)  # balance + open orders + this document
    available: Decimal | None = None  # limit - exposure (negative when over); None without a limit
    pdc_in_hand: Decimal = Decimal(0)
    # For information only (A2): what the exposure would be if the PDCs cleared.
    exposure_after_pdc: Decimal = Decimal(0)
    may_override: bool = False  # whether this user may pass a block (credit.override)


def check(
    amount: Decimal, figures: CreditFigures, terms: CreditTerms, *, may_override: bool = False
) -> CreditResult:
    exposure = figures.ledger_balance + figures.open_orders + amount
    reasons: list[CreditReason] = []
    if terms.credit_limit is not None and exposure > terms.credit_limit:
        reasons.append(CreditReason.OVER_LIMIT)
    if (
        terms.credit_days is not None
        and figures.oldest_overdue_days is not None
        and figures.oldest_overdue_days > terms.credit_days
    ):
        reasons.append(CreditReason.OVERDUE)
    if not reasons:
        decision = CreditDecision.OK
    else:
        decision = CreditDecision.BLOCK if terms.policy is CreditPolicy.BLOCK else CreditDecision.WARN
    return CreditResult(
        decision=decision,
        reasons=reasons,
        exposure=exposure,
        available=None if terms.credit_limit is None else terms.credit_limit - exposure,
        pdc_in_hand=figures.pdc_in_hand,
        exposure_after_pdc=exposure - figures.pdc_in_hand,
        may_override=may_override,
    )
