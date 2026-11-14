"""Import customers and suppliers (M1.12a), through the parties service like the Parties screen.

A party already in the books (same TRN, else the same name in English or Arabic) is skipped, never changed.
"""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.modules.imports.kinds.base import (
    Context,
    Field,
    Option,
    Reader,
    Row,
    RowResult,
    from_validation,
)
from app.modules.imports.values import normal_header
from app.modules.parties import service as parties
from app.modules.parties.schemas import AddressIn, CustomerProfileIn, PartyIn, SupplierProfileIn
from app.modules.tenancy.models import Emirate

CUSTOMER, SUPPLIER, BOTH = "customer", "supplier", "both"
ROLE_WORDS = {
    "customer": CUSTOMER,
    "customers": CUSTOMER,
    "client": CUSTOMER,
    "debtor": CUSTOMER,
    "debtors": CUSTOMER,
    "sundry debtors": CUSTOMER,
    "عميل": CUSTOMER,
    "عملاء": CUSTOMER,
    "زبون": CUSTOMER,
    "مدين": CUSTOMER,
    "supplier": SUPPLIER,
    "suppliers": SUPPLIER,
    "vendor": SUPPLIER,
    "creditor": SUPPLIER,
    "creditors": SUPPLIER,
    "sundry creditors": SUPPLIER,
    "مورد": SUPPLIER,
    "موردين": SUPPLIER,
    "دائن": SUPPLIER,
    "both": BOTH,
    "customer supplier": BOTH,
    "كلاهما": BOTH,
}
EMIRATE_WORDS = {
    **{normal_header(e.value): e for e in Emirate},
    "abudhabi": Emirate.ABU_DHABI,
    "auh": Emirate.ABU_DHABI,
    "shj": Emirate.SHARJAH,
    "uaq": Emirate.UMM_AL_QUWAIN,
    "umm al qaiwain": Emirate.UMM_AL_QUWAIN,
    "rak": Emirate.RAS_AL_KHAIMAH,
    "ras al khaima": Emirate.RAS_AL_KHAIMAH,
    "fuj": Emirate.FUJAIRAH,
    "دبي": Emirate.DUBAI,
    "ابوظبي": Emirate.ABU_DHABI,
    "ابو ظبي": Emirate.ABU_DHABI,
    "الشارقه": Emirate.SHARJAH,
    "عجمان": Emirate.AJMAN,
    "ام القيوين": Emirate.UMM_AL_QUWAIN,
    "راس الخيمه": Emirate.RAS_AL_KHAIMAH,
    "رأس الخيمه": Emirate.RAS_AL_KHAIMAH,
    "الفجيره": Emirate.FUJAIRAH,
}
FIELDS = (
    Field(
        "name_en",
        "Name",
        "الاسم",
        (
            "name",
            "party name",
            "customer name",
            "supplier name",
            "ledger name",
            "ledger",
            "party",
            "اسم",
            "اسم العميل",
            "اسم المورد",
            "الاسم بالانجليزي",
        ),
        required=True,
        example="Gulf Fabrics LLC",
        hint_en="English or Arabic; the Arabic name copies it when empty",
        hint_ar="بالإنجليزية أو العربية؛ ينسخ إلى الاسم العربي عند تركه فارغا",
    ),
    Field(
        "name_ar",
        "Arabic name",
        "الاسم بالعربية",
        ("arabic name", "name arabic", "name ar", "الاسم بالعربي", "اسم عربي"),
        example="الخليج للأقمشة",
    ),
    Field(
        "type",
        "Customer or supplier",
        "عميل أو مورد",
        ("type", "party type", "group", "category", "under", "نوع", "المجموعة"),
        example="customer",
        hint_en="customer, supplier or both",
        hint_ar="عميل أو مورد أو كلاهما",
    ),
    Field(
        "code",
        "Code",
        "الرمز",
        ("code", "party code", "customer code", "supplier code", "id", "رمز", "كود"),
        example="C0100",
    ),
    Field(
        "trn",
        "TRN",
        "الرقم الضريبي",
        (
            "trn",
            "tax registration number",
            "tax id",
            "vat number",
            "vat no",
            "gstin",
            "الرقم الضريبي",
            "رقم التسجيل الضريبي",
        ),
        example="100123456700003",
        hint_en="15 digits",
        hint_ar="15 رقما",
    ),
    Field(
        "phone",
        "Phone",
        "الهاتف",
        ("phone", "mobile", "tel", "telephone", "contact number", "هاتف", "جوال", "الجوال"),
    ),
    Field("email", "Email", "البريد الإلكتروني", ("email", "e mail", "mail", "البريد", "بريد الكتروني")),
    Field("address", "Address", "العنوان", ("address", "street", "address 1", "address line 1", "عنوان")),
    Field("city", "City", "المدينة", ("city", "town", "area", "مدينه")),
    Field(
        "emirate",
        "Emirate",
        "الإمارة",
        ("emirate", "state", "region", "الامارة", "امارة"),
        hint_en="Dubai, Abu Dhabi, Sharjah…",
        hint_ar="دبي، أبوظبي، الشارقة…",
    ),
    Field(
        "credit_days",
        "Credit days",
        "أيام الائتمان",
        ("credit days", "credit period", "payment terms", "terms", "days", "مدة الائتمان"),
    ),
    Field(
        "credit_limit",
        "Credit limit",
        "حد الائتمان",
        ("credit limit", "limit", "حد الائتمان", "الحد الائتماني"),
    ),
)
OPTIONS = (
    Option(
        "default_type",
        "When the file does not say, these are",
        "عندما لا يحدد الملف، هؤلاء",
        (CUSTOMER, SUPPLIER, BOTH),
        CUSTOMER,
    ),
)
_ADDRESS_FIELD = {"line1": "address", "city": "city", "emirate": "emirate"}


@dataclass(frozen=True)
class Prepared:
    party: PartyIn
    customer: CustomerProfileIn | None
    supplier: SupplierProfileIn | None


def _field_of(path: tuple[str | int, ...]) -> str | None:
    if path and path[0] == "addresses":
        return _ADDRESS_FIELD.get(str(path[2]), "emirate") if len(path) > 2 else "emirate"
    if path and path[0] in {f.key for f in FIELDS}:
        return str(path[0])
    return None


def _has_arabic(text: str) -> bool:
    return bool(re.search(r"[؀-ۿ]", text))


class _CodeSequence:
    """Party codes for rows without one (C0001, S0001, P0001), numbered once for the whole file.

    Typed codes are created first and counted, so an automatic code never meets a typed one, and the numbering
    does not read every existing code again for each party.
    """

    def __init__(self, top: dict[str, int]) -> None:
        self.top = top

    @classmethod
    async def start(cls, ctx: Context, results: list[RowResult]) -> "_CodeSequence":
        top = dict.fromkeys("CSP", 0)
        known = set((await parties.identity_index(ctx.session)).by_code)
        known |= {
            r.prepared.party.code.lower() for r in results if r.status == "new" and r.prepared.party.code
        }
        for code in known:
            if (match := re.fullmatch(r"([csp])(\d+)", code)) and int(match[2]) > top[match[1].upper()]:
                top[match[1].upper()] = int(match[2])
        return cls(top)

    def code_for(self, party: PartyIn) -> str:
        if party.code:
            return party.code
        prefix = "P" if party.is_customer and party.is_supplier else ("C" if party.is_customer else "S")
        self.top[prefix] += 1
        return f"{prefix}{self.top[prefix]:04d}"


class Parties:
    key = "parties"
    label_en = "Customers and suppliers"
    label_ar = "العملاء والموردون"
    description_en = "Names, TRN, contact details, credit terms."
    description_ar = "الأسماء والرقم الضريبي وبيانات الاتصال وشروط الائتمان."
    fields: tuple[Field, ...] = FIELDS
    options: tuple[Option, ...] = OPTIONS
    permissions: tuple[
        str, ...
    ] = ()  # checked per row: customers need customer.create, suppliers supplier.create
    order = 20

    async def check(self, ctx: Context, rows: list[Row]) -> list[RowResult]:
        index = await parties.identity_index(ctx.session)
        default = ctx.options.get("default_type", CUSTOMER)
        seen: dict[str, int] = {}
        results: list[RowResult] = []
        for row in rows:
            reader = Reader(row, FIELDS)
            results.append(self._row(ctx, reader, row, index, default, seen))
        return results

    def _row(
        self,
        ctx: Context,
        reader: Reader,
        row: Row,
        index: parties.PartyIndex,
        default: str,
        seen: dict[str, int],
    ) -> RowResult:
        name = reader.text("name_en", required=True, limit=200)
        arabic = reader.text("name_ar", limit=200)
        if name and arabic is None:
            arabic = name
        elif arabic and not name:
            name = arabic
        role = reader.choice("type", ROLE_WORDS, default=default)
        trn = reader.text("trn")
        if trn:
            trn = re.sub(r"[\s-]", "", trn)
        code = reader.text("code", limit=30)
        label = name or "?"
        for needs, permission, word in (
            (CUSTOMER, "customer.create", "customers"),
            (SUPPLIER, "supplier.create", "suppliers"),
        ):
            if role in (needs, BOTH) and not ctx.allows(permission):
                reader.add("type", "not_allowed", f"You may not create {word}.")
        if code and f"code:{code.lower()}" in seen:
            reader.add(
                "code", "duplicate_in_file", f"The code {code} is also on row {seen[f'code:{code.lower()}']}."
            )
        elif code:
            seen[f"code:{code.lower()}"] = row.number
        if reader.problems or name is None or arabic is None:
            return reader.result("new", label)

        keys = [f"trn:{trn}"] if trn else []
        keys += [f"name:{' '.join(n.lower().split())}" for n in {name, arabic}]
        for key in keys:
            if key in seen:
                reader.add(None, "duplicate_in_file", f"The same party is on row {seen[key]}.")
                return reader.result("new", label)
        for key in keys:
            seen.setdefault(key, row.number)

        matched = (trn and index.by_trn.get(trn)) or index.by_name.get(" ".join(name.lower().split()))
        matched = matched or index.by_name.get(" ".join(arabic.lower().split()))
        if matched:
            return reader.result("exists", label)
        if code and code.lower() in index.by_code:
            reader.add("code", "code_taken", f"The code {code} belongs to another party.")
            return reader.result("new", label)
        return self._build(ctx, reader, label, name, arabic, role or default, trn, code)

    def _build(
        self,
        ctx: Context,
        reader: Reader,
        label: str,
        name: str,
        arabic: str,
        role: str,
        trn: str | None,
        code: str | None,
    ) -> RowResult:
        emirate_text = reader.text("emirate")
        emirate = EMIRATE_WORDS.get(normal_header(emirate_text)) if emirate_text else None
        if emirate_text and emirate is None:
            reader.add("emirate", "unknown_value", f"'{emirate_text}' is not an emirate of the UAE.")
        line, city = reader.text("address", limit=200), reader.text("city", limit=100)
        credit_days = reader.decimal("credit_days")
        credit_limit = reader.decimal("credit_limit")
        if credit_days is not None and credit_days != credit_days.to_integral():
            reader.add("credit_days", "not_whole", "Credit days is a whole number.")
        if reader.problems:
            return reader.result("new", label)
        addresses: list[AddressIn] = []
        if line or city:
            try:
                addresses = [AddressIn(line1=line or city or "", city=city, emirate=emirate, is_default=True)]
            except ValidationError as exc:  # a UAE address needs its emirate
                from_validation(reader, exc, lambda _path: "emirate")
                return reader.result("new", label)
        try:
            customer = role in (CUSTOMER, BOTH)
            party = PartyIn(
                code=code,
                name_en=name,
                name_ar=arabic,
                trn=trn,
                is_customer=customer,
                is_supplier=role in (SUPPLIER, BOTH),
                phone=reader.text("phone", limit=30),
                email=reader.text("email"),
                addresses=addresses,
            )
            days = int(credit_days) if credit_days is not None else None
            profile = (
                CustomerProfileIn(business_id=ctx.business_id, credit_limit=credit_limit, credit_days=days)
                if customer and (credit_limit is not None or days is not None)
                else None
            )
            terms = (
                SupplierProfileIn(business_id=ctx.business_id, payment_terms_days=days)
                if party.is_supplier and days is not None
                else None
            )
        except ValidationError as exc:
            from_validation(reader, exc, _field_of)
            return reader.result("new", label)
        return reader.result("new", label, Prepared(party, profile, terms))

    async def apply(self, ctx: Context, results: list[RowResult]) -> dict[str, Any]:
        counts = {"created": 0, "customers": 0, "suppliers": 0, "skipped": 0}
        codes = await _CodeSequence.start(ctx, results)
        for result in results:
            if result.status != "new":
                counts["skipped"] += 1
                continue
            prepared: Prepared = result.prepared
            party = prepared.party.model_copy(update={"code": codes.code_for(prepared.party)})
            created = await parties.create_party(ctx.session, ctx.tenant_id, party)
            if prepared.customer is not None:
                await parties.put_customer_profile(ctx.session, created.id, prepared.customer)
            if prepared.supplier is not None:
                await parties.put_supplier_profile(ctx.session, created.id, prepared.supplier)
            counts["created"] += 1
            counts["customers"] += prepared.party.is_customer
            counts["suppliers"] += prepared.party.is_supplier
        return counts
