"""Import extra ledgers on the chart of accounts (M1.12a), through the accounting service.

New ledgers go under groups that already exist (the default chart ships the usual ones). An account whose code
is already on the chart is skipped, never changed; system accounts and their roles are never touched.
"""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.modules.accounting import service as accounting
from app.modules.accounting.models import TYPE_NATURES, AccountType, Nature
from app.modules.accounting.schemas import AccountIn
from app.modules.imports.kinds.base import Context, Field, Option, Reader, Row, RowResult, from_validation
from app.modules.imports.values import normal_header

FIELDS = (
    Field(
        "code",
        "Account code",
        "رمز الحساب",
        ("code", "account code", "ledger code", "a/c code", "no", "رمز", "رقم الحساب"),
        required=True,
        example="6300",
    ),
    Field(
        "name_en",
        "Account name",
        "اسم الحساب",
        ("name", "account name", "ledger name", "ledger", "account", "particulars", "اسم الحساب", "الحساب"),
        required=True,
        example="Staff accommodation",
    ),
    Field(
        "name_ar",
        "Arabic name",
        "الاسم بالعربية",
        ("arabic name", "name arabic", "name ar", "الاسم بالعربي", "اسم عربي"),
        example="سكن الموظفين",
    ),
    Field(
        "parent",
        "Group",
        "المجموعة",
        ("group", "parent", "parent group", "under", "parent code", "المجموعة", "الحساب الرئيسي"),
        required=True,
        example="6000",
        hint_en="code or name of a group on the chart",
        hint_ar="رمز أو اسم مجموعة في دليل الحسابات",
    ),
    Field(
        "type",
        "Type",
        "النوع",
        ("type", "account type", "نوع", "نوع الحساب"),
        example="expense",
        hint_en="taken from the group when empty",
        hint_ar="يؤخذ من المجموعة عند تركه فارغا",
    ),
)
TYPE_WORDS = {
    normal_header(word): account_type
    for account_type in AccountType
    for word in (account_type.value, account_type.value.replace("_", " "))
} | {
    "bank account": AccountType.BANK,
    "cash in hand": AccountType.CASH,
    "debtors": AccountType.RECEIVABLE,
    "creditors": AccountType.PAYABLE,
    "income": AccountType.OTHER_INCOME,
    "direct income": AccountType.OTHER_INCOME,
    "indirect income": AccountType.OTHER_INCOME,
    "direct expense": AccountType.COGS,
    "indirect expense": AccountType.EXPENSE,
    "expenses": AccountType.EXPENSE,
    "capital": AccountType.EQUITY,
    "fixed assets": AccountType.FIXED_ASSET,
    "current assets": AccountType.CURRENT_ASSET,
    "current liabilities": AccountType.CURRENT_LIABILITY,
    "loan": AccountType.NON_CURRENT_LIABILITY,
}
DEFAULT_TYPE = {
    Nature.ASSET: AccountType.CURRENT_ASSET,
    Nature.LIABILITY: AccountType.CURRENT_LIABILITY,
    Nature.EQUITY: AccountType.EQUITY,
    Nature.INCOME: AccountType.OTHER_INCOME,
    Nature.EXPENSE: AccountType.EXPENSE,
}
# Receivable, payable and stock accounts are the control accounts the system posts to; files add others.
NOT_BY_FILE = frozenset({AccountType.RECEIVABLE, AccountType.PAYABLE, AccountType.STOCK})


@dataclass(frozen=True)
class Prepared:
    account: AccountIn


def _field_of(path: tuple[str | int, ...]) -> str | None:
    names = {
        "code": "code",
        "name_en": "name_en",
        "name_ar": "name_ar",
        "parent_id": "parent",
        "account_type": "type",
    }
    return names.get(str(path[0])) if path else None


class Accounts:
    key = "accounts"
    label_en = "Chart of accounts"
    label_ar = "دليل الحسابات"
    description_en = "Extra ledgers under the groups of your chart."
    description_ar = "حسابات إضافية تحت مجموعات دليل الحسابات."
    fields: tuple[Field, ...] = FIELDS
    options: tuple[Option, ...] = ()
    permissions: tuple[str, ...] = ("account.create",)
    order = 10

    async def check(self, ctx: Context, rows: list[Row]) -> list[RowResult]:
        chart = await accounting.chart_accounts(ctx.session, ctx.business_id)
        by_code = {a.code.lower(): a for a in chart}
        groups = {a.code.lower(): a for a in chart if a.is_group and a.archived_at is None}
        groups |= {normal_header(a.name_en): a for a in chart if a.is_group and a.archived_at is None}
        seen: dict[str, int] = {}
        results: list[RowResult] = []
        for row in rows:
            reader = Reader(row, FIELDS)
            code = reader.text("code", required=True, limit=20)
            name = reader.text("name_en", required=True, limit=200)
            arabic = reader.text("name_ar", limit=200) or name
            label = f"{code} {name}" if code and name else (code or name or "?")
            parent_text = reader.text("parent", required=True)
            kind_text = reader.text("type")
            if code and code.lower() in seen:
                reader.add(
                    "code", "duplicate_in_file", f"The code {code} is also on row {seen[code.lower()]}."
                )
            elif code:
                seen[code.lower()] = row.number
            if code and code.lower() in by_code and not reader.problems:
                results.append(reader.result("exists", label))
                continue
            results.append(
                self._build(ctx, reader, label, code, name, arabic, parent_text, kind_text, groups)
            )
        return results

    def _build(
        self,
        ctx: Context,
        reader: Reader,
        label: str,
        code: str | None,
        name: str | None,
        arabic: str | None,
        parent_text: str | None,
        kind_text: str | None,
        groups: dict[str, Any],
    ) -> RowResult:
        parent = (
            (groups.get(parent_text.lower()) or groups.get(normal_header(parent_text)))
            if parent_text
            else None
        )
        if parent_text and parent is None:
            reader.add("parent", "unknown_group", f"'{parent_text}' is not a group on the chart of accounts.")
        account_type = TYPE_WORDS.get(normal_header(kind_text)) if kind_text else None
        if kind_text and account_type is None:
            reader.add("type", "unknown_value", f"'{kind_text}' is not an account type.")
        if parent is not None and account_type is None and not kind_text:
            account_type = DEFAULT_TYPE[Nature(parent.nature)]
        if account_type in NOT_BY_FILE:
            reader.add(
                "type", "system_type", "Receivable, payable and stock accounts are created by the system."
            )
        elif (
            parent is not None
            and account_type is not None
            and Nature(parent.nature) not in TYPE_NATURES[account_type]
        ):
            reader.add(
                "type", "wrong_group", f"A {account_type.value} account cannot sit under {parent.name_en}."
            )
        if reader.problems or code is None or name is None or arabic is None or parent is None:
            return reader.result("new", label)
        try:
            data = AccountIn(
                business_id=ctx.business_id,
                code=code,
                name_en=name,
                name_ar=arabic,
                parent_id=parent.id,
                account_type=account_type,
            )
        except ValidationError as exc:
            from_validation(reader, exc, _field_of)
            return reader.result("new", label)
        return reader.result("new", label, Prepared(data))

    async def apply(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        counts = {"created": 0, "skipped": 0}
        for result in results:
            if result.status != "new":
                counts["skipped"] += 1
                continue
            await accounting.create_account(ctx.session, ctx.tenant_id, result.prepared.account)
            counts["created"] += 1
        return counts
