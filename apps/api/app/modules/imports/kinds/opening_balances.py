"""Import the opening trial balance (M1.12b), posted as journal vouchers against opening balance equity.

Receivable, payable and stock accounts are not posted from it: their balances come from the party bills and
the opening stock, and the file's figures are kept as the totals the reconciliation expects (question R13).
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.money import to_currency
from app.modules.accounting import service as accounting
from app.modules.accounting.models import CONTROL, AccountType
from app.modules.accounting.schemas import AccountOut, VoucherLineIn
from app.modules.imports.kinds import opening
from app.modules.imports.kinds.base import Context, Field, KindDefaults, Option, Reader, Row, RowResult
from app.modules.imports.values import normal_header

ZERO = Decimal("0.0000")
CONTROL_TYPES = frozenset({*CONTROL, AccountType.STOCK})
FIELDS = (
    Field(
        "account",
        "Account",
        "الحساب",
        (
            "account",
            "account code",
            "account name",
            "ledger",
            "ledger name",
            "code",
            "particulars",
            "a/c",
            "اسم الحساب",
            "الحساب",
            "رمز الحساب",
            "البيان",
        ),
        required=True,
        example="1110",
        hint_en="code, or name in English or Arabic, as on your chart",
        hint_ar="الرمز أو الاسم بالإنجليزية أو العربية كما في دليل الحسابات",
    ),
    Field("debit", "Debit", "مدين", ("debit", "dr", "debit amount", "مدين"), example="10000.00"),
    Field("credit", "Credit", "دائن", ("credit", "cr", "credit amount", "دائن"), example=""),
    Field(
        "balance",
        "Balance",
        "الرصيد",
        ("balance", "closing balance", "opening balance", "closing", "amount", "الرصيد"),
        hint_en="instead of debit and credit: debit positive, or marked Dr / Cr",
        hint_ar="بدلا من مدين ودائن: المدين موجب، أو مع Dr / Cr",
    ),
)


@dataclass(frozen=True)
class Line:
    account: AccountOut
    debit: Decimal
    credit: Decimal
    control: bool  # not posted: the bills and the stock make this balance


def _by_name(chart: list[AccountOut]) -> dict[str, list[AccountOut]]:
    names: dict[str, list[AccountOut]] = {}
    for account in chart:
        for name in {normal_header(account.name_en), normal_header(account.name_ar)}:
            names.setdefault(name, []).append(account)
    return names


class OpeningBalances(KindDefaults):
    key = "opening_balances"
    label_en = "Opening trial balance"
    label_ar = "ميزان المراجعة الافتتاحي"
    description_en = "Each account's balance on the day the old books stop."
    description_ar = "رصيد كل حساب في اليوم الذي تتوقف فيه الدفاتر القديمة."
    fields: tuple[Field, ...] = FIELDS
    options: tuple[Option, ...] = (opening.OPENING_DATE, opening.BRANCH)
    permissions: tuple[str, ...] = ("journal.create", "journal.post")
    undo_permissions: tuple[str, ...] = ("journal.cancel",)
    order = 40
    once_per_business = True
    undoable = True

    async def check(self, ctx: Context, rows: list[Row]) -> list[RowResult]:
        await opening.check_opening_day(ctx)
        chart = await accounting.chart_accounts(ctx.session, ctx.business_id)
        by_code = {a.code.lower(): a for a in chart}
        by_name = _by_name(chart)
        equity = await opening.equity_account(ctx)
        seen: dict[str, int] = {}
        results: list[RowResult] = []
        for row in rows:
            reader = Reader(row, FIELDS)
            results.append(self._row(ctx, reader, row, by_code, by_name, equity, seen))
        return results

    def _amounts(self, ctx: Context, reader: Reader) -> tuple[Decimal, Decimal]:
        debit = reader.decimal("debit")
        credit = reader.decimal("credit")
        balance = reader.decimal("balance", balance=True)
        if balance is not None and (debit is not None or credit is not None):
            reader.add(
                "balance", "balance_and_columns", "Use either debit and credit, or one balance column."
            )
        if (debit or ZERO) < 0 or (credit or ZERO) < 0:
            reader.add(
                None, "negative", "Debit and credit are not negative; put a negative balance in credit."
            )
        if debit and credit:
            reader.add(None, "both_sides", "A row has a debit or a credit, not both.")
        if balance is not None and debit is None and credit is None:
            debit, credit = (balance, ZERO) if balance > 0 else (ZERO, -balance)
        d, c = debit or ZERO, credit or ZERO
        for key, value in (("debit", d), ("credit", c)):
            if value != to_currency(value, ctx.currency):
                reader.add(key, "decimals", f"{ctx.currency} amounts have no more than 2 decimal places.")
        return d, c

    def _row(
        self,
        ctx: Context,
        reader: Reader,
        row: Row,
        by_code: dict[str, AccountOut],
        by_name: dict[str, list[AccountOut]],
        equity: Any,
        seen: dict[str, int],
    ) -> RowResult:
        text = reader.text("account", required=True)
        label = text or "?"
        debit, credit = self._amounts(ctx, reader)
        account: AccountOut | None = None
        if text:
            account = by_code.get(text.lower())
            if account is None:
                named = by_name.get(normal_header(text), [])
                if len(named) > 1:
                    reader.add(
                        "account",
                        "ambiguous_account",
                        f"'{text}' matches more than one account; use its code.",
                    )
                elif named:
                    account = named[0]
                else:
                    reader.add(
                        "account",
                        "unknown_account",
                        f"'{text}' is not on the chart; import the account first.",
                    )
        if account is not None:
            label = f"{account.code} {account.name_en}"
            self._account_problems(reader, account, equity, seen, row.number)
        if reader.problems or account is None:
            return reader.result("new", label)
        if debit == 0 and credit == 0:
            return reader.result(
                "skipped", label, note=("zero_balance", "Nothing to import: the balance is zero.")
            )
        control = AccountType(account.account_type) in CONTROL_TYPES if account.account_type else False
        line = Line(account, debit, credit, control)
        if control:
            note = "Not posted from here: the bills and stock make this balance; kept for the reconciliation."
            return reader.result("skipped", label, line, note=("control_account", note))
        return reader.result("new", label, line)

    def _account_problems(
        self, reader: Reader, account: AccountOut, equity: Any, seen: dict[str, int], number: int
    ) -> None:
        key = str(account.id)
        if key in seen:
            reader.add("account", "duplicate_in_file", f"{account.code} is also on row {seen[key]}.")
        else:
            seen[key] = number
        if account.is_group:
            reader.add(
                "account", "is_group", f"{account.code} is a group: remove subtotal rows, keep the ledgers."
            )
        elif account.archived_at is not None:
            reader.add("account", "archived", f"{account.code} is archived.")
        elif account.id == equity:
            reader.add("account", "is_equity", "Opening balance equity is filled by the import itself.")
        elif not account.allow_manual_posting and not (
            account.account_type and AccountType(account.account_type) in CONTROL_TYPES
        ):
            reader.add("account", "system_only", f"{account.code} is posted by the system only.")

    async def summary(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        lines = [r.prepared for r in results if isinstance(r.prepared, Line)]
        file_debit = sum((ln.debit for ln in lines), ZERO)
        file_credit = sum((ln.credit for ln in lines), ZERO)
        posted = [ln for ln in lines if not ln.control]
        net = sum((ln.debit - ln.credit for ln in posted), ZERO)
        return {
            "opening_date": ctx.options["opening_date"],
            "file_debit": str(file_debit),
            "file_credit": str(file_credit),
            "file_difference": str(file_debit - file_credit),  # a trial balance has none
            "accounts_posted": len(posted),
            "controls": [
                {
                    "code": ln.account.code,
                    "name_en": ln.account.name_en,
                    "name_ar": ln.account.name_ar,
                    "debit": str(ln.debit),
                    "credit": str(ln.credit),
                }
                for ln in lines
                if ln.control
            ],
            "equity_side": next(iter(opening.equity_side(net)), None),  # None: nothing goes to equity
            "equity_amount": str(abs(net)),
        }

    async def apply(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        lines = [r.prepared for r in results if isinstance(r.prepared, Line)]
        posted = [ln for ln in lines if not ln.control]
        vouchers = 0
        for part in opening.chunks(posted):
            voucher_lines = [
                VoucherLineIn(
                    account_id=ln.account.id,
                    **({"debit": ln.debit} if ln.debit else {"credit": ln.credit}),
                )
                for ln in part
            ]
            await opening.post_journal(
                ctx, voucher_lines, net_debit=sum((ln.debit - ln.credit for ln in part), ZERO)
            )
            vouchers += 1
        ctx.record["opening_date"] = ctx.options["opening_date"]
        ctx.record["expected"] = [
            {
                "account_id": str(ln.account.id),
                "code": ln.account.code,
                "name_en": ln.account.name_en,
                "name_ar": ln.account.name_ar,
                "debit": str(ln.debit),
                "credit": str(ln.credit),
                "control": ln.control,
            }
            for ln in lines
        ]
        return {
            "accounts": len(posted),
            "controls": len(lines) - len(posted),
            "skipped": len(results) - len(lines),
            "vouchers": vouchers,
        }
