"""Reading a Tally master export (XML) into the rows of an import kind (M1.12c). Pure, no database.

One export file serves every kind: the reader takes out the rows the chosen kind needs and returns them as a
``Table`` whose headers are the kind's field keys, so the columns are matched without asking. Nothing is posted
here; everything after reading is shared with the Excel and CSV imports.

Tally writes a debit balance as a *negative* number and a credit as positive. The file is checked against itself
(customers, cash, bank and stock are normally debits) and what it shows is used; a file with nothing to judge by
is read the way Tally writes. The file is untrusted: ``defusedxml`` refuses entities and external references.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from app.modules.imports.readers import Table, UnreadableFileError
from app.modules.imports.values import CellError, clean_text, is_blank, parse_date, parse_decimal

Node = Any
ZERO = Decimal(0)
SEED = Path(__file__).resolve().parents[3] / "seeds" / "import" / "tally_groups.json"
_ARABIC = re.compile(r"[؀-ۿ]")
_ILLEGAL = re.compile(r"&#(?:[0-8]|1[12]|1[4-9]|2\d|3[01]);|[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DECLARATION = re.compile(r"^\s*<\?xml[^>]*\?>")
_DR_CR = re.compile(r"\b(dr|cr)\b\.?", re.IGNORECASE)
_QTY_UNIT = re.compile(r"^\s*(-?[\d.,]+)\s*(.*?)\s*$")
_DAYS = re.compile(r"(\d+)")
TRN_TAGS = ("TAXREGISTRATIONNUMBER", "VATTINNUMBER", "PARTYGSTIN", "VATTINNO", "TRN")
EMIRATES = ("abu dhabi", "dubai", "sharjah", "ajman", "umm al quwain", "ras al khaimah", "fujairah")


@cache
def groups() -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = json.loads(SEED.read_text(encoding="utf8"))["groups"]
    return found


@dataclass
class Bill:
    name: str
    day: date | None
    days: int | None
    amount: Decimal  # debit positive


@dataclass
class Ledger:
    name: str
    arabic: str | None
    spec: dict[str, str] | None
    balance: Decimal  # debit positive
    bills: list[Bill] = field(default_factory=list)
    trn: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    state: str | None = None
    credit_days: int | None = None
    credit_limit: Decimal | None = None

    @property
    def role(self) -> str | None:
        return self.spec.get("role") if self.spec else None


@dataclass
class Allocation:
    godown: str | None
    batch: str | None
    qty: Decimal
    value: Decimal
    expiry: date | None


@dataclass
class Stock:
    name: str
    arabic: str | None
    unit: str | None
    part_number: str | None
    qty: Decimal
    value: Decimal  # positive: what the stock is worth
    batch_tracked: bool
    allocations: list[Allocation] = field(default_factory=list)


@dataclass
class Export:
    ledgers: list[Ledger]
    stock: list[Stock]
    convention: str  # "tally": a debit is written as a negative number; "plain": as a positive one
    notes: list[str] = field(default_factory=list)


# --- reading the file ------------------------------------------------------------------------------------


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8-sig", errors="replace")
    return _DECLARATION.sub("", _ILLEGAL.sub("", text), count=1)


def parse(data: bytes) -> Node:
    """The XML of a Tally export; refuses anything that is not one, and entity tricks."""
    try:
        root = fromstring(_decode(data))
    except DefusedXmlException as exc:
        raise UnreadableFileError("This XML file uses features a Tally export never does.") from exc
    except ParseError as exc:
        raise UnreadableFileError("This is not a valid XML file.", reason=str(exc)) from exc
    if root.tag != "ENVELOPE" and root.find(".//TALLYMESSAGE") is None:
        raise UnreadableFileError("This XML file is not a Tally export (Export → Masters).")
    return root


def _text(node: Node, tag: str) -> str | None:
    return clean_text(node.findtext(tag))


def _names(node: Node) -> tuple[str, str | None]:
    """The name, and the Arabic name when Tally holds one as an alternate language name."""
    listed = [
        clean_text(n.text)
        for path in ("LANGUAGENAME.LIST/NAME.LIST/NAME", "NAME.LIST/NAME")
        for n in node.findall(path)
    ]
    names = [n for n in listed if n]
    name = clean_text(node.get("NAME")) or (names[0] if names else "")
    arabic = next((n for n in names if n != name and _ARABIC.search(n)), None)
    return name, arabic


def _spec(parent: str | None, parents: dict[str, str]) -> dict[str, str] | None:
    """The mapping of a ledger's group, following custom groups up to the reserved group they sit in."""
    seen: set[str] = set()
    current = (parent or "").strip().lower()
    while current and current not in seen:
        if current in groups():
            return groups()[current]
        seen.add(current)
        current = parents.get(current, "")
    return None


def _number(text: str | None) -> Decimal | None:
    if is_blank(text):
        return None
    try:
        return parse_decimal(text)
    except CellError:
        return None


def _signed(text: str | None, convention: str) -> Decimal:
    """A Tally amount as debit positive: honours a written Dr / Cr, else the file's convention."""
    if is_blank(text):
        return ZERO
    try:
        if _DR_CR.search(text or ""):
            return parse_decimal(text, balance=True) or ZERO
        value = parse_decimal(text) or ZERO
    except CellError:
        return ZERO
    return -value if convention == "tally" else value


def _day(text: str | None) -> date | None:
    if is_blank(text):
        return None
    compact = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", (text or "").strip())
    try:
        if compact:
            return date(int(compact[1]), int(compact[2]), int(compact[3]))
        return parse_date(text)
    except (CellError, ValueError):
        return None


def _days(text: str | None) -> int | None:
    found = _DAYS.search(text or "")
    return int(found[1]) if found else None


def _quantity(text: str | None) -> tuple[Decimal, str | None]:
    """``120 m`` → (120, ``m``); a bare number has no unit."""
    match = _QTY_UNIT.match(text or "")
    if not match:
        return ZERO, None
    number = _number(match[1])
    return (abs(number) if number is not None else ZERO), (match[2] or None)


def _detect(raw_ledgers: list[tuple[dict[str, str] | None, str | None]], raw_stock: list[str | None]) -> str:
    """Which way the file writes a debit, judged by balances that are normally debits or credits."""
    debit_negative = debit_positive = 0
    for spec, text in raw_ledgers:
        value = None if _DR_CR.search(text or "") else _number(text)
        if not value or spec is None:
            continue
        kind = spec.get("type")
        if spec.get("role") == "customer" or kind in ("bank", "cash", "current_asset", "fixed_asset"):
            debit_negative, debit_positive = debit_negative + (value < 0), debit_positive + (value > 0)
        elif spec.get("role") == "supplier" or kind in ("equity", "non_current_liability", "sales"):
            debit_negative, debit_positive = debit_negative + (value > 0), debit_positive + (value < 0)
    for text in raw_stock:  # stock is an asset
        value = _number(text)
        if value:
            debit_negative, debit_positive = debit_negative + (value < 0), debit_positive + (value > 0)
    return "plain" if debit_positive > debit_negative else "tally"


def read_export(data: bytes) -> Export:
    root = parse(data)
    parents = {
        _names(g)[0].lower(): (_text(g, "PARENT") or "").lower() for g in root.iter("GROUP") if _names(g)[0]
    }
    ledger_nodes = [(n, _spec(_text(n, "PARENT"), parents)) for n in root.iter("LEDGER") if _names(n)[0]]
    stock_nodes = [n for n in root.iter("STOCKITEM") if _names(n)[0]]
    convention = _detect(
        [(spec, _text(n, "OPENINGBALANCE")) for n, spec in ledger_nodes],
        [_text(n, "OPENINGVALUE") for n in stock_nodes],
    )
    notes = [
        "Debit balances are written with a minus sign in this file (as Tally does)."
        if convention == "tally"
        else "Debit balances are written as positive numbers in this file."
    ]
    return Export(
        [_ledger(n, spec, convention) for n, spec in ledger_nodes],
        [_stock(n, convention) for n in stock_nodes],
        convention,
        notes,
    )


def _ledger(node: Node, spec: dict[str, str] | None, convention: str) -> Ledger:
    name, arabic = _names(node)
    bills = [
        Bill(
            clean_text(b.findtext("NAME")) or "",
            _day(b.findtext("BILLDATE")),
            _days(b.findtext("BILLCREDITPERIOD")),
            _signed(b.findtext("OPENINGBALANCE"), convention),
        )
        for b in node.findall("BILLALLOCATIONS.LIST")
    ]
    limit = _number(_text(node, "CREDITLIMIT"))
    lines = [clean_text(a.text) for a in node.findall("ADDRESS.LIST/ADDRESS")]
    return Ledger(
        name=name,
        arabic=arabic,
        spec=spec,
        balance=_signed(_text(node, "OPENINGBALANCE"), convention),
        bills=[b for b in bills if b.name],
        trn=next((v for tag in TRN_TAGS if (v := _text(node, tag))), None),
        phone=_text(node, "LEDGERMOBILE") or _text(node, "LEDGERPHONE"),
        email=_text(node, "EMAIL"),
        address=", ".join(line for line in lines if line) or None,
        state=_text(node, "LEDSTATENAME") or _text(node, "STATENAME"),
        credit_days=_days(_text(node, "BILLCREDITPERIOD")),
        credit_limit=abs(limit) if limit else None,
    )


def _stock(node: Node, convention: str) -> Stock:
    name, arabic = _names(node)
    qty, suffix = _quantity(_text(node, "OPENINGBALANCE"))
    allocations: list[Allocation] = []
    for a in node.findall("BATCHALLOCATIONS.LIST"):
        a_qty, _unit = _quantity(_text(a, "OPENINGBALANCE"))
        expiry = a.find("EXPIRYPERIOD")
        allocations.append(
            Allocation(
                godown=_text(a, "GODOWNNAME"),
                batch=_text(a, "BATCHNAME"),
                qty=a_qty,
                value=abs(_signed(_text(a, "OPENINGVALUE"), convention)),
                expiry=_day(expiry.get("JOBDATE") or expiry.text) if expiry is not None else None,
            )
        )
    return Stock(
        name=name,
        arabic=arabic,
        unit=_text(node, "BASEUNITS") or suffix,
        part_number=_text(node, "PARTNUMBER"),
        qty=qty,
        value=abs(_signed(_text(node, "OPENINGVALUE"), convention)),
        batch_tracked=(_text(node, "ISBATCHWISEON") or "").lower() == "yes",
        allocations=allocations,
    )


# --- the rows of each kind --------------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").upper()[:50] or "ITEM"


def sku_of(item: Stock) -> str:
    return _slug(item.part_number or item.name)


def _iso(day: date | None) -> str:
    return day.isoformat() if day else ""


def _debit_credit(amount: Decimal) -> tuple[str, str]:
    return (str(amount), "") if amount > 0 else ("", str(-amount))


def _parties(export: Export) -> tuple[list[str], list[list[str]]]:
    headers = [
        "name_en",
        "name_ar",
        "type",
        "trn",
        "phone",
        "email",
        "address",
        "emirate",
        "credit_days",
        "credit_limit",
    ]
    rows, dropped = [], 0
    for ledger in export.ledgers:
        if ledger.role not in ("customer", "supplier"):
            continue
        emirate = next((e for e in EMIRATES if e in (ledger.state or "").lower()), "")
        address = ledger.address if emirate else None  # a UAE address needs its emirate
        dropped += bool(ledger.address and not emirate)
        rows.append(
            [
                ledger.name,
                ledger.arabic or "",
                ledger.role or "",
                ledger.trn or "",
                ledger.phone or "",
                ledger.email or "",
                address or "",
                emirate,
                "" if ledger.credit_days is None else str(ledger.credit_days),
                "" if ledger.credit_limit is None else str(ledger.credit_limit),
            ]
        )
    if dropped:
        export.notes.append(
            f"{dropped} address(es) were left out because the file names no emirate for them."
        )
    return headers, rows


def _accounts(export: Export) -> tuple[list[str], list[list[str]]]:
    headers = ["code", "name_en", "name_ar", "parent", "type"]
    rows = []
    for ledger in export.ledgers:
        if ledger.role in ("customer", "supplier", "stock"):
            continue
        spec = ledger.spec or {}
        rows.append(["", ledger.name, ledger.arabic or "", spec.get("parent", ""), spec.get("type", "")])
    return headers, rows


def _opening_balances(export: Export) -> tuple[list[str], list[list[str]]]:
    headers = ["account", "debit", "credit"]
    rows: list[list[str]] = []
    control = {"customer": ZERO, "supplier": ZERO, "stock": sum((s.value for s in export.stock), ZERO)}
    for ledger in export.ledgers:
        if ledger.role in control:
            control[ledger.role or ""] += ledger.balance
        elif ledger.balance:
            rows.append([ledger.name, *_debit_credit(ledger.balance)])
    for key, role in (("customer", "@receivable"), ("supplier", "@payable"), ("stock", "@inventory")):
        if control[key]:
            rows.append([role, *_debit_credit(control[key])])
    return headers, rows


def _party_bills(export: Export) -> tuple[list[str], list[list[str]]]:
    headers = ["party", "reference", "bill_date", "due_date", "amount", "side", "ledger"]
    rows: list[list[str]] = []
    for ledger in export.ledgers:
        if ledger.role not in ("customer", "supplier"):
            continue
        listed = ZERO
        for bill in ledger.bills:
            if not bill.amount:
                continue
            listed += bill.amount
            due = bill.day + timedelta(days=bill.days) if bill.day and bill.days else None
            side = "Dr" if bill.amount > 0 else "Cr"
            rows.append(
                [
                    ledger.name,
                    bill.name,
                    _iso(bill.day),
                    _iso(due),
                    str(abs(bill.amount)),
                    side,
                    ledger.role or "",
                ]
            )
        rest = ledger.balance - listed  # a balance that is not bill-wise is carried on account
        if rest:
            side = "Dr" if rest > 0 else "Cr"
            rows.append([ledger.name, "Opening balance", "", "", str(abs(rest)), side, ledger.role or ""])
    return headers, rows


def _products(export: Export) -> tuple[list[str], list[list[str]]]:
    headers = ["sku", "name_en", "name_ar", "unit", "type", "track_batch", "track_expiry"]
    rows: list[list[str]] = []
    used: set[str] = set()
    for item in export.stock:
        sku, n = sku_of(item), 1
        while sku in used:
            n += 1
            sku = f"{sku_of(item)}-{n}"[:50]
        used.add(sku)
        expiry = any(a.expiry for a in item.allocations)
        rows.append(
            [
                sku,
                item.name,
                item.arabic or "",
                item.unit or "",
                "stock",
                "yes" if item.batch_tracked or expiry else "",
                "yes" if expiry else "",
            ]
        )
    return headers, rows


def _opening_stock(export: Export) -> tuple[list[str], list[list[str]]]:
    headers = ["item", "warehouse", "quantity", "unit", "value", "batch_no", "expiry_date"]
    rows: list[list[str]] = []
    for item in export.stock:
        unit = item.unit or ""
        real = [a for a in item.allocations if a.qty]
        if real:
            for a in real:
                godown = "" if (a.godown or "").lower() == "main location" else (a.godown or "")
                batch = "" if (a.batch or "").lower() == "primary batch" else (a.batch or "")
                rows.append([item.name, godown, str(a.qty), unit, str(a.value), batch, _iso(a.expiry)])
        elif item.qty:
            rows.append([item.name, "", str(item.qty), unit, str(item.value), "", ""])
    return headers, rows


_BUILDERS = {
    "parties": _parties,
    "accounts": _accounts,
    "opening_balances": _opening_balances,
    "party_bills": _party_bills,
    "products": _products,
    "opening_stock": _opening_stock,
}
_WHAT = {
    "parties": "customer or supplier ledgers (Sundry Debtors / Sundry Creditors)",
    "accounts": "ledgers other than customers and suppliers",
    "opening_balances": "ledgers or stock items with an opening balance",
    "party_bills": "bills (bill-wise customer or supplier balances)",
    "products": "stock items",
    "opening_stock": "stock items with an opening quantity",
}


def read_tally(data: bytes, kind: str | None) -> Table:
    """The rows of ``kind`` from a Tally master export, already matched to its fields."""
    if kind not in _BUILDERS:
        raise UnreadableFileError("Choose what to import before uploading a Tally file.")
    export = read_export(data)
    headers, rows = _BUILDERS[kind](export)
    if not rows:
        raise UnreadableFileError(f"This Tally file has no {_WHAT[kind]}.")
    return Table(
        headers, [list(r) for r in rows], list(range(2, len(rows) + 2)), canonical=True, notes=export.notes
    )
