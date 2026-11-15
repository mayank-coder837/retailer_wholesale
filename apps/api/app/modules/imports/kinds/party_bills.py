"""Import outstanding invoices and bills (M1.12b): open bill-wise like any invoice, against opening equity.

A customer's invoice is a debit on receivables, a supplier's bill a credit on payables. A minus sign, or a
mark opposite to the normal one (Cr on a customer, Dr on a supplier), makes it an advance instead.
Imported bills carry no VAT split: their VAT was reported in the old system's returns (question R12).
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.core.money import to_currency
from app.modules.accounting import service as accounting
from app.modules.accounting.models import AllocationType, Role
from app.modules.accounting.schemas import AllocationIn, VoucherLineIn
from app.modules.imports.kinds import opening
from app.modules.imports.kinds.base import Context, Field, KindDefaults, Option, Reader, Row, RowResult
from app.modules.imports.kinds.parties import ROLE_WORDS
from app.modules.parties import service as parties

ZERO = Decimal("0.0000")
MAX_AMOUNT = Decimal(10**13)
CUSTOMER, SUPPLIER = "customer", "supplier"
SIDE_WORDS = {
    **dict.fromkeys(("dr", "debit", "مدين"), "dr"),
    **dict.fromkeys(("cr", "credit", "دائن"), "cr"),
}
FIELDS = (
    Field(
        "party",
        "Customer or supplier",
        "العميل أو المورد",
        (
            "party",
            "party name",
            "customer",
            "supplier",
            "customer name",
            "supplier name",
            "name",
            "account",
            "ledger",
            "اسم العميل",
            "اسم المورد",
            "الطرف",
        ),
        required=True,
        example="Gulf Fabrics LLC",
        hint_en="code, TRN or name, as already imported",
        hint_ar="الرمز أو الرقم الضريبي أو الاسم كما تم استيراده",
    ),
    Field(
        "reference",
        "Invoice or bill number",
        "رقم الفاتورة",
        (
            "reference",
            "ref no",
            "ref",
            "ref. no.",
            "invoice no",
            "invoice number",
            "invoice",
            "bill no",
            "bill number",
            "bill",
            "document no",
            "voucher no",
            "رقم الفاتورة",
            "المرجع",
        ),
        required=True,
        example="INV-1093",
    ),
    Field(
        "bill_date",
        "Invoice date",
        "تاريخ الفاتورة",
        ("invoice date", "bill date", "ref date", "date", "تاريخ الفاتورة", "التاريخ"),
        example="15/02/2026",
        hint_en="the opening date when empty",
        hint_ar="تاريخ الافتتاح عند تركه فارغا",
    ),
    Field(
        "due_date",
        "Due date",
        "تاريخ الاستحقاق",
        ("due date", "due on", "due", "تاريخ الاستحقاق", "الاستحقاق"),
        example="16/03/2026",
        hint_en="invoice date plus credit days when empty",
        hint_ar="تاريخ الفاتورة مضافا إليه أيام الائتمان عند تركه فارغا",
    ),
    Field(
        "amount",
        "Amount outstanding",
        "المبلغ المستحق",
        (
            "amount",
            "outstanding",
            "pending",
            "pending amount",
            "outstanding amount",
            "balance",
            "opening balance",
            "المبلغ",
            "الرصيد",
            "المستحق",
        ),
        required=True,
        example="5250.00",
        hint_en="what is still unpaid; a minus sign makes it an advance",
        hint_ar="المبلغ غير المسدد؛ الإشارة السالبة تجعله دفعة مقدمة",
    ),
    Field(
        "side",
        "Dr / Cr",
        "مدين / دائن",
        ("dr/cr", "dr cr", "side", "debit credit", "مدين دائن"),
        hint_en="only if your file marks it",
        hint_ar="فقط إذا كان الملف يحدده",
    ),
    Field(
        "ledger",
        "Customer or supplier ledger",
        "دفتر العميل أو المورد",
        ("party type", "ledger type", "type", "customer or supplier", "النوع"),
        hint_en="for a party that is both",
        hint_ar="لطرف يجمع الصفتين",
    ),
)
OPTIONS = (
    opening.OPENING_DATE,
    opening.BRANCH,
    Option(
        "default_type",
        "A party that is both customer and supplier is taken as",
        "الطرف الذي هو عميل ومورد يعتبر",
        (CUSTOMER, SUPPLIER),
        CUSTOMER,
    ),
)


@dataclass(frozen=True)
class Bill:
    party_id: uuid.UUID
    customer: bool
    advance: bool
    reference: str
    amount: Decimal
    bill_date: date
    due_date: date


def _party_id(index: parties.PartyIndex, text: str) -> uuid.UUID | None:
    digits = re.sub(r"[\s-]", "", text)
    return (
        index.by_trn.get(digits)
        or index.by_code.get(text.lower())
        or index.by_name.get(" ".join(text.lower().split()))
    )


class PartyBills(KindDefaults):
    key = "party_bills"
    label_en = "Unpaid invoices and bills"
    label_ar = "الفواتير غير المسددة"
    description_en = "What each customer still owes you and you still owe each supplier."
    description_ar = "ما لا يزال كل عميل مدينا به لك وما لا تزال مدينا به لكل مورد."
    fields: tuple[Field, ...] = FIELDS
    options: tuple[Option, ...] = OPTIONS
    permissions: tuple[str, ...] = ("journal.create", "journal.post")
    undo_permissions: tuple[str, ...] = ("journal.cancel",)
    order = 50
    undoable = True

    async def check(self, ctx: Context, rows: list[Row]) -> list[RowResult]:
        day = await opening.check_opening_day(ctx)
        index = await parties.identity_index(ctx.session)
        roles = await accounting.role_accounts(ctx.session, ctx.business_id)
        keys = await accounting.bill_keys(ctx.session, ctx.business_id)
        terms: dict[tuple[uuid.UUID, bool], int | None] = {}
        seen: dict[tuple[uuid.UUID, str, bool], int] = {}
        results: list[RowResult] = []
        for row in rows:
            reader = Reader(row, FIELDS)
            result = await self._row(ctx, reader, row, day, index, terms, seen)
            bill = result.prepared
            if isinstance(bill, Bill) and result.status == "new":
                account = roles[Role.AR_CONTROL if bill.customer else Role.AP_CONTROL]
                if (bill.party_id, bill.reference.lower(), account) in keys:
                    result = RowResult(row.number, "exists", result.label, [], None)
            results.append(result)
        return results

    async def _row(
        self,
        ctx: Context,
        reader: Reader,
        row: Row,
        day: date,
        index: parties.PartyIndex,
        terms: dict[tuple[uuid.UUID, bool], int | None],
        seen: dict[tuple[uuid.UUID, str, bool], int],
    ) -> RowResult:
        text = reader.text("party", required=True)
        reference = reader.text("reference", required=True, limit=100)
        amount = reader.decimal("amount", required=True)
        bill_date = reader.date("bill_date")
        due_date = reader.date("due_date")
        side = reader.choice("side", SIDE_WORDS)
        ledger = reader.choice("ledger", ROLE_WORDS)
        label = f"{text or '?'} {reference or ''}".strip()
        party_id = _party_id(index, text) if text else None
        if text and party_id is None:
            reader.add(
                "party",
                "unknown_party",
                f"'{text}' is not a customer or supplier yet; import the party first.",
            )
        if amount is not None and abs(amount) >= MAX_AMOUNT:
            reader.add("amount", "too_large", "This amount is too large to be right.")
        elif amount is not None and amount != to_currency(amount, ctx.currency):
            reader.add("amount", "decimals", f"{ctx.currency} amounts have no more than 2 decimal places.")
        if bill_date is not None and bill_date > day:
            reader.add("bill_date", "after_opening", "The invoice is dated after the opening date.")
        if due_date is not None and due_date < (bill_date or day):
            reader.add("due_date", "due_before_invoice", "The due date is before the invoice date.")
        if reader.problems or party_id is None or reference is None or amount is None:
            return reader.result("new", label)
        customer = self._customer(ctx, reader, index, party_id, ledger)
        if customer is None:
            return reader.result("new", label)
        if amount == 0:
            return reader.result(
                "skipped", label, note=("zero_amount", "Nothing to import: the amount is zero.")
            )
        if amount < 0 and side is not None:
            reader.add("side", "sign_and_mark", "Use a minus sign or a Dr / Cr mark, not both.")
            return reader.result("new", label)
        normal = "dr" if customer else "cr"
        advance = amount < 0 or (side is not None and side != normal)
        key = (party_id, reference.lower(), customer)
        if key in seen:
            reader.add("reference", "duplicate_in_file", f"{reference} is also on row {seen[key]}.")
            return reader.result("new", label)
        seen[key] = row.number
        bill_on = bill_date or day
        if due_date is None:
            days = await self._days(ctx, terms, party_id, customer)
            due_date = bill_on + timedelta(days=days or 0)
        return reader.result(
            "new", label, Bill(party_id, customer, advance, reference, abs(amount), bill_on, due_date)
        )

    def _customer(
        self, ctx: Context, reader: Reader, index: parties.PartyIndex, party_id: uuid.UUID, ledger: str | None
    ) -> bool | None:
        is_customer, is_supplier = index.roles[party_id]
        wanted = ledger or (
            CUSTOMER
            if is_customer and not is_supplier
            else SUPPLIER
            if is_supplier and not is_customer
            else None
        )
        wanted = wanted or ctx.options.get("default_type", CUSTOMER)
        if wanted == "both":
            reader.add("ledger", "unknown_value", "Say whether this is a customer or a supplier bill.")
            return None
        if (wanted == CUSTOMER and not is_customer) or (wanted == SUPPLIER and not is_supplier):
            reader.add("ledger", "wrong_role", f"This party is not a {wanted}.")
            return None
        return wanted == CUSTOMER

    async def _days(
        self,
        ctx: Context,
        terms: dict[tuple[uuid.UUID, bool], int | None],
        party_id: uuid.UUID,
        customer: bool,
    ) -> int | None:
        if (party_id, customer) not in terms:
            terms[(party_id, customer)] = await parties.terms_days(
                ctx.session, party_id, ctx.business_id, customer=customer
            )
        return terms[(party_id, customer)]

    async def summary(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        bills = [r.prepared for r in results if isinstance(r.prepared, Bill) and r.status == "new"]

        def total(customer: bool, advance: bool) -> str:
            return str(
                sum((b.amount for b in bills if b.customer == customer and b.advance == advance), ZERO)
            )

        return {
            "opening_date": ctx.options["opening_date"],
            "receivable": total(True, False),
            "receivable_advances": total(True, True),
            "payable": total(False, False),
            "payable_advances": total(False, True),
        }

    async def apply(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        bills = [r.prepared for r in results if isinstance(r.prepared, Bill) and r.status == "new"]
        roles = await accounting.role_accounts(ctx.session, ctx.business_id)
        vouchers = 0
        for part in opening.chunks(bills):
            lines: list[VoucherLineIn] = []
            net = ZERO
            for bill in part:
                account = roles[Role.AR_CONTROL if bill.customer else Role.AP_CONTROL]
                debit = bill.customer != bill.advance  # a customer bill and a supplier advance are debits
                allocation = AllocationIn(
                    type=AllocationType.ADVANCE if bill.advance else AllocationType.NEW_REF,
                    amount=bill.amount,
                    reference=bill.reference,
                    due_date=None if bill.advance else bill.due_date,
                    bill_date=bill.bill_date,
                )
                lines.append(
                    VoucherLineIn(
                        account_id=account,
                        party_id=bill.party_id,
                        description=bill.reference,
                        allocations=[allocation],
                        **({"debit": bill.amount} if debit else {"credit": bill.amount}),
                    )
                )
                net += bill.amount if debit else -bill.amount
            await opening.post_journal(ctx, lines, net_debit=net)
            vouchers += 1
        return {
            "bills": sum(not b.advance for b in bills),
            "advances": sum(b.advance for b in bills),
            "exists": sum(r.status == "exists" for r in results),
            "skipped": sum(r.status == "skipped" for r in results),
            "vouchers": vouchers,
        }
