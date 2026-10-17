"""Parties business rules (M1.4). Other modules call these functions, never the tables.

- One party per real-world company or person, with customer and/or supplier roles (ADR 0005).
- Terms live per business: a customer profile (price list, group, credit limit, days, policy) and a supplier
  profile (payment terms).
- The credit check runs the pure ``credit.check`` over the customer's figures. Ledger balance and the oldest
  unpaid invoice come from accounting (M1.5); open orders from sales (M1.8) and PDCs in hand from the PDC
  register (M1.9) are zero until those exist.
"""

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.modules.parties import credit
from app.modules.parties.models import (
    CustomerGroup,
    CustomerProfile,
    Party,
    PartyAddress,
    PartyContact,
    SupplierProfile,
)
from app.modules.parties.schemas import (
    AddressIn,
    AddressOut,
    ContactIn,
    ContactOut,
    CreditCheckIn,
    CreditCheckOut,
    CustomerGroupIn,
    CustomerGroupOut,
    CustomerGroupPatch,
    CustomerProfileIn,
    CustomerProfileOut,
    PartyIn,
    PartyOut,
    PartyPatch,
    PartySummary,
    SupplierProfileIn,
    SupplierProfileOut,
)
from app.modules.pricing import service as pricing
from app.modules.tenancy import service as tenancy


class PartyNotFoundError(NotFoundError):
    code = "party_not_found"
    title = "There is no such customer or supplier"


class DuplicatePartyError(ConflictError):
    code = "duplicate_party"
    title = "A customer or supplier with this code or TRN already exists"


class InvalidPartyError(UnprocessableError):
    code = "invalid_party"
    title = "The customer or supplier details are not valid"


def _prefix(is_customer: bool, is_supplier: bool) -> str:
    return "P" if is_customer and is_supplier else ("C" if is_customer else "S")


async def _next_code(session: AsyncSession, prefix: str) -> str:
    """The next free code for the prefix (C0001, C0002…); master data, so gaps are fine (A12: documents)."""
    codes = await session.scalars(select(Party.code).where(Party.code.like(f"{prefix}%")))
    numbers = [int(m.group(1)) for c in codes if (m := re.fullmatch(rf"{prefix}(\d+)", c))]
    return f"{prefix}{(max(numbers) + 1) if numbers else 1:04d}"


async def _flush(session: AsyncSession) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise DuplicatePartyError() from exc


# --- reading -----------------------------------------------------------------------------------------------


async def get_party_row(session: AsyncSession, party_id: uuid.UUID) -> Party:
    party = await session.get(Party, party_id)
    if party is None:
        raise PartyNotFoundError()
    return party


async def get_party(session: AsyncSession, party_id: uuid.UUID) -> PartyOut:
    party = await get_party_row(session, party_id)
    addresses = await session.scalars(
        select(PartyAddress).where(PartyAddress.party_id == party.id).order_by(PartyAddress.created_at)
    )
    contacts = await session.scalars(
        select(PartyContact).where(PartyContact.party_id == party.id).order_by(PartyContact.created_at)
    )
    customers = await session.scalars(
        select(CustomerProfile)
        .where(CustomerProfile.party_id == party.id)
        .order_by(CustomerProfile.created_at)
    )
    suppliers = await session.scalars(
        select(SupplierProfile)
        .where(SupplierProfile.party_id == party.id)
        .order_by(SupplierProfile.created_at)
    )
    return PartyOut(
        **PartySummary.model_validate(party).model_dump(),
        whatsapp=party.whatsapp,
        email=party.email,
        default_currency=party.default_currency,
        addresses=[AddressOut.model_validate(a) for a in addresses],
        contacts=[ContactOut.model_validate(c) for c in contacts],
        customer_profiles=[CustomerProfileOut.model_validate(p) for p in customers],
        supplier_profiles=[SupplierProfileOut.model_validate(p) for p in suppliers],
    )


async def list_parties(
    session: AsyncSession,
    *,
    q: str | None = None,
    customers: bool = True,
    suppliers: bool = True,
    include_archived: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[PartySummary]:
    if not (customers or suppliers):
        return []
    query = select(Party).order_by(func.lower(Party.name_en), Party.code).limit(limit).offset(offset)
    roles = []
    if customers:
        roles.append(Party.is_customer.is_(True))
    if suppliers:
        roles.append(Party.is_supplier.is_(True))
    query = query.where(or_(*roles))
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(
            or_(
                Party.code.ilike(pattern),
                Party.name_en.ilike(pattern),
                Party.name_ar.ilike(pattern),
                Party.trn == q.strip(),
                Party.phone.ilike(pattern),
                Party.whatsapp.ilike(pattern),
            )
        )
    if not include_archived:
        query = query.where(Party.archived_at.is_(None))
    return [PartySummary.model_validate(p) for p in await session.scalars(query)]


# --- writing -----------------------------------------------------------------------------------------------


def _check_default_addresses(addresses: list[AddressIn]) -> None:
    for kind in {a.type for a in addresses}:
        if sum(1 for a in addresses if a.type == kind and a.is_default) > 1:
            raise InvalidPartyError(f"Only one default {kind.value} address.", field="addresses")


async def create_party(session: AsyncSession, tenant_id: uuid.UUID, data: PartyIn) -> PartyOut:
    _check_default_addresses(data.addresses)
    code = data.code or await _next_code(session, _prefix(data.is_customer, data.is_supplier))
    party = Party(
        id=new_id(),
        tenant_id=tenant_id,
        code=code,
        **data.model_dump(exclude={"code", "addresses", "contacts"}),
    )
    session.add(party)
    await _flush(session)
    for address in data.addresses:
        session.add(PartyAddress(id=new_id(), tenant_id=tenant_id, party_id=party.id, **address.model_dump()))
    for contact in data.contacts:
        session.add(PartyContact(id=new_id(), tenant_id=tenant_id, party_id=party.id, **contact.model_dump()))
    await session.flush()
    return await get_party(session, party.id)


async def update_party(session: AsyncSession, party_id: uuid.UUID, data: PartyPatch) -> PartyOut:
    party = await get_party_row(session, party_id)
    fields = data.model_fields_set - {"archived"}
    for field in fields:
        value = getattr(data, field)
        if field in ("name_en", "name_ar", "kind", "is_customer", "is_supplier") and value is None:
            continue
        setattr(party, field, value)
    if not (party.is_customer or party.is_supplier):
        raise InvalidPartyError("A party is a customer, a supplier, or both.", field="is_customer")
    if data.archived is not None:
        party.archived_at = datetime.now(UTC) if data.archived else None
    await _flush(session)
    return await get_party(session, party.id)


async def _clear_default(
    session: AsyncSession, party_id: uuid.UUID, kind: str, keep: uuid.UUID | None
) -> None:
    for row in await session.scalars(
        select(PartyAddress).where(PartyAddress.party_id == party_id, PartyAddress.type == kind)
    ):
        if row.id != keep and row.is_default:
            row.is_default = False
    await session.flush()


async def add_address(session: AsyncSession, party_id: uuid.UUID, data: AddressIn) -> PartyOut:
    party = await get_party_row(session, party_id)
    row = PartyAddress(id=new_id(), tenant_id=party.tenant_id, party_id=party.id, **data.model_dump())
    if data.is_default:
        await _clear_default(session, party.id, data.type.value, None)
    session.add(row)
    await session.flush()
    return await get_party(session, party.id)


async def _address(session: AsyncSession, party_id: uuid.UUID, address_id: uuid.UUID) -> PartyAddress:
    row = await session.get(PartyAddress, address_id)
    if row is None or row.party_id != party_id:
        raise PartyNotFoundError("There is no such address.")
    return row


async def update_address(
    session: AsyncSession, party_id: uuid.UUID, address_id: uuid.UUID, data: AddressIn
) -> PartyOut:
    row = await _address(session, party_id, address_id)
    if data.is_default:
        await _clear_default(session, party_id, data.type.value, row.id)
    for field, value in data.model_dump().items():
        setattr(row, field, value)
    await session.flush()
    return await get_party(session, party_id)


async def remove_address(session: AsyncSession, party_id: uuid.UUID, address_id: uuid.UUID) -> PartyOut:
    await session.delete(await _address(session, party_id, address_id))
    await session.flush()
    return await get_party(session, party_id)


async def add_contact(session: AsyncSession, party_id: uuid.UUID, data: ContactIn) -> PartyOut:
    party = await get_party_row(session, party_id)
    session.add(PartyContact(id=new_id(), tenant_id=party.tenant_id, party_id=party.id, **data.model_dump()))
    await session.flush()
    return await get_party(session, party.id)


async def remove_contact(session: AsyncSession, party_id: uuid.UUID, contact_id: uuid.UUID) -> PartyOut:
    row = await session.get(PartyContact, contact_id)
    if row is None or row.party_id != party_id:
        raise PartyNotFoundError("There is no such contact.")
    await session.delete(row)
    await session.flush()
    return await get_party(session, party_id)


# --- customer groups ---------------------------------------------------------------------------------------


async def _check_price_list(
    session: AsyncSession, price_list_id: uuid.UUID | None, business_id: uuid.UUID
) -> None:
    if price_list_id is None:
        return
    price_list = await pricing.get_price_list(session, price_list_id)
    if price_list.business_id != business_id:
        raise InvalidPartyError("The price list belongs to another business.", field="price_list_id")


async def list_groups(session: AsyncSession, business_id: uuid.UUID | None = None) -> list[CustomerGroupOut]:
    query = select(CustomerGroup).order_by(CustomerGroup.name_en)
    if business_id is not None:
        query = query.where(CustomerGroup.business_id == business_id)
    return [CustomerGroupOut.model_validate(g) for g in await session.scalars(query)]


async def get_group(session: AsyncSession, group_id: uuid.UUID) -> CustomerGroup:
    group = await session.get(CustomerGroup, group_id)
    if group is None:
        raise PartyNotFoundError("There is no such customer group.")
    return group


async def create_group(
    session: AsyncSession, tenant_id: uuid.UUID, data: CustomerGroupIn
) -> CustomerGroupOut:
    await tenancy.get_business(session, data.business_id)
    await _check_price_list(session, data.price_list_id, data.business_id)
    group = CustomerGroup(id=new_id(), tenant_id=tenant_id, **data.model_dump())
    session.add(group)
    await _flush(session)
    return CustomerGroupOut.model_validate(group)


async def update_group(
    session: AsyncSession, group_id: uuid.UUID, data: CustomerGroupPatch
) -> CustomerGroupOut:
    group = await get_group(session, group_id)
    if "price_list_id" in data.model_fields_set:
        await _check_price_list(session, data.price_list_id, group.business_id)
        group.price_list_id = data.price_list_id
    for field in ("name_en", "name_ar"):
        if (value := getattr(data, field)) is not None:
            setattr(group, field, value)
    if data.archived is not None:
        group.archived_at = datetime.now(UTC) if data.archived else None
    await _flush(session)
    return CustomerGroupOut.model_validate(group)


# --- profiles ----------------------------------------------------------------------------------------------


async def _customer_profile(
    session: AsyncSession, party_id: uuid.UUID, business_id: uuid.UUID
) -> CustomerProfile | None:
    return (
        await session.scalars(
            select(CustomerProfile).where(
                CustomerProfile.party_id == party_id, CustomerProfile.business_id == business_id
            )
        )
    ).first()


def credit_terms_changed(current: CustomerProfile | None, data: CustomerProfileIn) -> bool:
    """Whether a profile change touches credit (credit.limit): limit, days or policy."""
    before = (
        (current.credit_limit, current.credit_days, current.credit_policy)
        if current is not None
        else (None, None, credit.CreditPolicy.WARN.value)
    )
    return before != (data.credit_limit, data.credit_days, data.credit_policy.value)


async def current_customer_profile(
    session: AsyncSession, party_id: uuid.UUID, business_id: uuid.UUID
) -> CustomerProfile | None:
    return await _customer_profile(session, party_id, business_id)


async def terms_days(
    session: AsyncSession, party_id: uuid.UUID, business_id: uuid.UUID, *, customer: bool
) -> int | None:
    """Days until a new bill is due: the customer's credit days or the supplier's payment terms."""
    if customer:
        profile = await _customer_profile(session, party_id, business_id)
        return profile.credit_days if profile else None
    supplier = (
        await session.scalars(
            select(SupplierProfile).where(
                SupplierProfile.party_id == party_id, SupplierProfile.business_id == business_id
            )
        )
    ).first()
    return supplier.payment_terms_days if supplier else None


async def put_customer_profile(
    session: AsyncSession, party_id: uuid.UUID, data: CustomerProfileIn
) -> PartyOut:
    party = await get_party_row(session, party_id)
    if not party.is_customer:
        raise InvalidPartyError("This party is not a customer.", field="party_id")
    await tenancy.get_business(session, data.business_id)
    await _check_price_list(session, data.price_list_id, data.business_id)
    if data.customer_group_id is not None:
        group = await get_group(session, data.customer_group_id)
        if group.business_id != data.business_id:
            raise InvalidPartyError(
                "The customer group belongs to another business.", field="customer_group_id"
            )
    row = await _customer_profile(session, party.id, data.business_id)
    values = data.model_dump(exclude={"business_id"}) | {"credit_policy": data.credit_policy.value}
    if row is None:
        session.add(
            CustomerProfile(
                id=new_id(),
                tenant_id=party.tenant_id,
                party_id=party.id,
                business_id=data.business_id,
                **values,
            )
        )
    else:
        for field, value in values.items():
            setattr(row, field, value)
    await _flush(session)
    return await get_party(session, party.id)


async def put_supplier_profile(
    session: AsyncSession, party_id: uuid.UUID, data: SupplierProfileIn
) -> PartyOut:
    party = await get_party_row(session, party_id)
    if not party.is_supplier:
        raise InvalidPartyError("This party is not a supplier.", field="party_id")
    await tenancy.get_business(session, data.business_id)
    row = (
        await session.scalars(
            select(SupplierProfile).where(
                SupplierProfile.party_id == party.id, SupplierProfile.business_id == data.business_id
            )
        )
    ).first()
    if row is None:
        session.add(
            SupplierProfile(
                id=new_id(),
                tenant_id=party.tenant_id,
                party_id=party.id,
                business_id=data.business_id,
                payment_terms_days=data.payment_terms_days,
            )
        )
    else:
        row.payment_terms_days = data.payment_terms_days
    await _flush(session)
    return await get_party(session, party.id)


# --- pricing: the customer's and the group's price list (pricing steps 2 and 3) -----------------------------


async def price_lists_for(
    session: AsyncSession, party_id: uuid.UUID, business_id: uuid.UUID
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(customer price list, customer group price list) of a customer in a business."""
    await get_party_row(session, party_id)
    profile = await _customer_profile(session, party_id, business_id)
    if profile is None:
        return None, None
    group_list = None
    if profile.customer_group_id is not None:
        group = await get_group(session, profile.customer_group_id)
        group_list = group.price_list_id if group.archived_at is None else None
    return profile.price_list_id, group_list


# --- credit ------------------------------------------------------------------------------------------------


async def credit_figures(
    session: AsyncSession, party_id: uuid.UUID, business_id: uuid.UUID
) -> credit.CreditFigures:
    """Ledger balance and the oldest unpaid invoice from accounting (M1.5); open orders (M1.8) and PDCs in
    hand (M1.9) are zero until those modules exist."""
    from app.modules.accounting import service as accounting  # accounting builds on parties

    balance, oldest_days = await accounting.receivable_figures(session, business_id, party_id)
    return credit.CreditFigures(ledger_balance=balance, oldest_overdue_days=oldest_days)


async def credit_check(
    session: AsyncSession, party_id: uuid.UUID, data: CreditCheckIn, *, may_override: bool
) -> CreditCheckOut:
    party = await get_party_row(session, party_id)
    if not party.is_customer:
        raise InvalidPartyError("This party is not a customer.", field="party_id")
    await tenancy.get_business(session, data.business_id)
    profile = await _customer_profile(session, party.id, data.business_id)
    terms = credit.CreditTerms(
        credit_limit=profile.credit_limit if profile else None,
        credit_days=profile.credit_days if profile else None,
        policy=credit.CreditPolicy(profile.credit_policy) if profile else credit.CreditPolicy.WARN,
    )
    figures = await credit_figures(session, party.id, data.business_id)
    result = credit.check(data.amount, figures, terms, may_override=may_override)
    return CreditCheckOut(
        decision=result.decision,
        reasons=result.reasons,
        credit_limit=terms.credit_limit,
        credit_days=terms.credit_days,
        policy=terms.policy,
        ledger_balance=figures.ledger_balance,
        open_orders=figures.open_orders,
        exposure=result.exposure,
        available=result.available,
        pdc_in_hand=result.pdc_in_hand,
        exposure_after_pdc=result.exposure_after_pdc,
        may_override=result.may_override,
    )


async def record_credit_override(
    session: AsyncSession,
    party_id: uuid.UUID,
    business_id: uuid.UUID,
    *,
    document: str,
    check: CreditCheckOut,
    reason: str,
) -> None:
    """A blocked sale was let through by someone with credit.override: who, which document, the figures, why.

    Called by sales (M1.8) when such a document is posted; the audit log keeps it for good.
    """
    if not reason.strip():
        raise InvalidPartyError("Say why the credit block is overridden.", field="reason")
    profile = await _customer_profile(session, party_id, business_id)
    if profile is None:
        raise InvalidPartyError("This customer has no terms in this business.", field="business_id")
    await audit.record(
        session,
        profile,
        "override",
        reason=reason.strip(),
        changes={
            "override": "credit",
            "document": document,
            "decision": check.decision.value,
            "reasons": [r.value for r in check.reasons],
            "exposure": str(check.exposure),
            "credit_limit": None if check.credit_limit is None else str(check.credit_limit),
            "available": None if check.available is None else str(check.available),
        },
    )
