"""The VAT return draft (M1.10b): facts from the documents and accounting services, boxes from ``returns``."""

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableError
from app.modules.tax import returns
from app.modules.tax.schemas import VatBoxLineOut, VatBoxOut, VatReturnOut
from app.modules.tenancy import service as tenancy

ZERO = Decimal("0.0000")


class InvalidVatPeriodError(UnprocessableError):
    code = "invalid_vat_period"
    title = "The VAT period is not valid"


class UnknownVatBoxError(UnprocessableError):
    code = "unknown_vat_box"
    title = "There is no such VAT 201 box"


async def _facts(
    session: AsyncSession, business_id: uuid.UUID, date_from: date, date_to: date
) -> list[returns.VatFact]:
    # The modules that own the rows read them (one door); the return only adds them up.
    from app.modules.accounting import service as accounting
    from app.modules.documents import service as documents

    if date_to < date_from:
        raise InvalidVatPeriodError("The period ends before it starts.", field="to")
    await tenancy.get_business(session, business_id)
    emirates = {b.id: b.emirate for b in await tenancy.list_branches(session, business_id)}
    facts = await documents.vat_facts(session, business_id, date_from, date_to, emirates)
    return facts + await accounting.vat_facts(session, business_id, date_from, date_to, emirates)


async def draft(
    session: AsyncSession, business_id: uuid.UUID, date_from: date, date_to: date
) -> VatReturnOut:
    values = returns.build(await _facts(session, business_id, date_from, date_to))
    by_box = {v.box.box: v for v in values}
    return VatReturnOut(
        business_id=business_id,
        date_from=date_from,
        date_to=date_to,
        boxes=[
            VatBoxOut(
                box=v.box.box,
                label_en=v.box.label_en,
                label_ar=v.box.label_ar,
                kind=v.box.kind,
                amount=v.amount,
                vat=v.vat,
            )
            for v in values
        ],
        payable=by_box["14"].vat,
    )


async def box_lines(
    session: AsyncSession, business_id: uuid.UUID, date_from: date, date_to: date, box: str
) -> list[VatBoxLineOut]:
    """The documents behind one box, each once with what it adds there; they sum to the box."""
    from app.modules.parties import service as parties

    if box not in {b.box for b in returns.boxes()}:
        raise UnknownVatBoxError(field="box")
    facts = await _facts(session, business_id, date_from, date_to)
    totals: dict[uuid.UUID, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    first: dict[uuid.UUID, returns.VatFact] = {}
    for fact, amount, vat in returns.facts_in(facts, box):
        totals[fact.document_id][0] += amount
        totals[fact.document_id][1] += vat
        first.setdefault(fact.document_id, fact)
    names: dict[uuid.UUID, tuple[str, str]] = {}
    out: list[VatBoxLineOut] = []
    for doc_id, fact in sorted(first.items(), key=lambda kv: (kv[1].doc_date, kv[1].number or "")):
        if fact.party_id is not None and fact.party_id not in names:
            party = await parties.get_party_row(session, fact.party_id)
            names[fact.party_id] = (party.name_en, party.name_ar)
        name = names.get(fact.party_id) if fact.party_id else None
        out.append(
            VatBoxLineOut(
                document_id=doc_id,
                number=fact.number,
                doc_type=fact.doc_type,
                doc_date=fact.doc_date,
                party_id=fact.party_id,
                party_name_en=name[0] if name else None,
                party_name_ar=name[1] if name else None,
                amount=totals[doc_id][0],
                vat=totals[doc_id][1],
            )
        )
    return out
