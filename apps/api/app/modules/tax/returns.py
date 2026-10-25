"""The VAT 201 return, pure (M1.10b; decisions-round-1 VAT 201 table, A10; accountant review open, R3).

Every posted line with a tax code becomes a ``VatFact``: signed (a credit note, return, debit note or reversal
counts negative, in its own period), with the side, code and the emirate of the branch that made it. The boxes
come from the codes; Box 1 splits by that emirate, never the customer's.
"""

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache

from app.modules.tax.codes import SEEDS, Side, Treatment, get

ZERO = Decimal("0.0000")


@dataclass(frozen=True, slots=True)
class Box:
    box: str
    label_en: str
    label_ar: str
    kind: str  # amount_vat, amount, total, manual
    emirate: str | None = None


@dataclass(frozen=True, slots=True)
class VatFact:
    """One taxed line as the return sees it; ``net`` and ``vat`` already carry the sign."""

    document_id: uuid.UUID  # the document whose date puts it in the period (a reversal: the reversal)
    number: str | None
    doc_type: str
    doc_date: date
    party_id: uuid.UUID | None
    side: Side
    code: str
    net: Decimal
    vat: Decimal
    emirate: str


@cache
def boxes(country: str = "AE") -> tuple[Box, ...]:
    data = json.loads((SEEDS / f"{country.lower()}.json").read_text(encoding="utf-8"))
    return tuple(
        Box(b["box"], b["label_en"], b["label_ar"], b["kind"], b.get("emirate")) for b in data["boxes"]
    )


def _box_one(emirate: str) -> str:
    return next(b.box for b in boxes() if b.emirate == emirate)


def boxes_of(fact: VatFact) -> list[tuple[str, Decimal, Decimal]]:
    """(box, amount, VAT) the fact adds to. Standard-rated sales go to the branch's emirate line of Box 1; the
    reverse charge adds the VAT due (3 or 6) and the same VAT recovered (10)."""
    code = get(fact.side, fact.code)
    if code is None:
        return []
    out: list[tuple[str, Decimal, Decimal]] = []
    if code.net_box is not None:
        box = _box_one(fact.emirate) if code.net_box == "1" else code.net_box
        out.append((box, fact.net, fact.vat if code.vat_box else ZERO))
    if code.treatment is Treatment.REVERSE_CHARGE and code.recovered_box is not None:
        out.append((code.recovered_box, fact.net, fact.vat))
    return out


OUTPUT_BOXES = ("1a", "1b", "1c", "1d", "1e", "1f", "1g", "2", "3", "4", "5", "6", "7")
INPUT_BOXES = ("9", "10")


@dataclass(frozen=True, slots=True)
class BoxValue:
    box: Box
    amount: Decimal
    vat: Decimal


def build(facts: list[VatFact]) -> list[BoxValue]:
    """Every box with its amount and VAT; 8 and 11 total the output and input boxes, 12 is the VAT due, 13
    the VAT
    recoverable, 14 what is payable (negative: refundable)."""
    amount: dict[str, Decimal] = defaultdict(lambda: ZERO)
    vat: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for fact in facts:
        for box, a, v in boxes_of(fact):
            amount[box] += a
            vat[box] += v
    for total, parts in (("8", OUTPUT_BOXES), ("11", INPUT_BOXES)):
        amount[total] = sum((amount[p] for p in parts), ZERO)
        vat[total] = sum((vat[p] for p in parts), ZERO)
    vat["12"], vat["13"] = vat["8"], vat["11"]
    vat["14"] = vat["12"] - vat["13"]
    return [BoxValue(b, amount[b.box], vat[b.box]) for b in boxes()]


def facts_in(facts: list[VatFact], box: str) -> list[tuple[VatFact, Decimal, Decimal]]:
    """The drill-down: each fact behind a box, with what it adds there."""
    return [(f, a, v) for f in facts for b, a, v in boxes_of(f) if b == box]
