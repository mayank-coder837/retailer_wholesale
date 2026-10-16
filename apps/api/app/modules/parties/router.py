"""Parties endpoints (M1.4): customers and suppliers, addresses, contacts, per-business terms, groups, credit.

One party may be a customer, a supplier, or both (ADR 0005): each side needs its own permission
(``customer.*`` / ``supplier.*``), and changing credit terms needs ``credit.limit``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.authz import PermissionDeniedError, PermissionSet
from app.core.deps import PrincipalDep, TenantSessionDep
from app.modules.identity.authz import get_permissions, require
from app.modules.parties import service
from app.modules.parties.models import Party
from app.modules.parties.schemas import (
    AddressIn,
    ContactIn,
    CreditCheckIn,
    CreditCheckOut,
    CustomerGroupIn,
    CustomerGroupOut,
    CustomerGroupPatch,
    CustomerProfileIn,
    PartyIn,
    PartyOut,
    PartyPatch,
    PartySummary,
    SupplierProfileIn,
)

router = APIRouter(tags=["parties"])
Created = status.HTTP_201_CREATED

# Which permission applies depends on the party's roles: loaded here, checked in each route.
Signed = Annotated[PermissionSet, Depends(get_permissions)]
CustomerView = Annotated[PermissionSet, require("customer.view")]
CustomerCreate = Annotated[PermissionSet, require("customer.create")]
CustomerEdit = Annotated[PermissionSet, require("customer.edit")]
SupplierEdit = Annotated[PermissionSet, require("supplier.edit")]


def _roles(is_customer: bool, is_supplier: bool) -> list[str]:
    return [r for r, on in (("customer", is_customer), ("supplier", is_supplier)) if on]


def _need(perms: PermissionSet, roles: list[str], action: str) -> None:
    """Tenant-wide: every role the party has (or gets) needs its own permission."""
    for role in roles:
        perms.require_held(f"{role}.{action}")


async def _editable(session: TenantSessionDep, perms: PermissionSet, party_id: uuid.UUID) -> Party:
    party = await service.get_party_row(session, party_id)
    _need(perms, _roles(party.is_customer, party.is_supplier), "edit")
    return party


@router.get("/parties", operation_id="list_parties")
async def list_parties(
    perms: Signed,
    session: TenantSessionDep,
    q: Annotated[str | None, Query(max_length=100, description="Code, name, TRN or phone")] = None,
    role: Annotated[str | None, Query(pattern="^(customer|supplier)$")] = None,
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PartySummary]:
    customers = perms.holds("customer.view") and role in (None, "customer")
    suppliers = perms.holds("supplier.view") and role in (None, "supplier")
    if not (perms.holds("customer.view") or perms.holds("supplier.view")):
        raise PermissionDeniedError(permission="customer.view")
    return await service.list_parties(
        session,
        q=q,
        customers=customers,
        suppliers=suppliers,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


@router.post("/parties", operation_id="create_party", status_code=Created)
async def create_party(
    body: PartyIn, perms: Signed, session: TenantSessionDep, principal: PrincipalDep
) -> PartyOut:
    _need(perms, _roles(body.is_customer, body.is_supplier), "create")
    return await service.create_party(session, principal.tenant_id, body)


@router.get("/parties/{party_id}", operation_id="get_party")
async def get_party(party_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> PartyOut:
    party = await service.get_party(session, party_id)
    visible = (party.is_customer and perms.holds("customer.view")) or (
        party.is_supplier and perms.holds("supplier.view")
    )
    if not visible:
        raise PermissionDeniedError(permission="customer.view")
    return party


@router.patch("/parties/{party_id}", operation_id="update_party")
async def update_party(
    party_id: uuid.UUID, body: PartyPatch, perms: Signed, session: TenantSessionDep
) -> PartyOut:
    party = await _editable(session, perms, party_id)
    gained = _roles(
        bool(body.is_customer) and not party.is_customer, bool(body.is_supplier) and not party.is_supplier
    )
    _need(perms, gained, "create")  # making a supplier a customer too is creating a customer
    return await service.update_party(session, party_id, body)


@router.post("/parties/{party_id}/addresses", operation_id="add_party_address", status_code=Created)
async def add_address(
    party_id: uuid.UUID, body: AddressIn, perms: Signed, session: TenantSessionDep
) -> PartyOut:
    await _editable(session, perms, party_id)
    return await service.add_address(session, party_id, body)


@router.put("/parties/{party_id}/addresses/{address_id}", operation_id="update_party_address")
async def update_address(
    party_id: uuid.UUID, address_id: uuid.UUID, body: AddressIn, perms: Signed, session: TenantSessionDep
) -> PartyOut:
    await _editable(session, perms, party_id)
    return await service.update_address(session, party_id, address_id, body)


@router.delete("/parties/{party_id}/addresses/{address_id}", operation_id="remove_party_address")
async def remove_address(
    party_id: uuid.UUID, address_id: uuid.UUID, perms: Signed, session: TenantSessionDep
) -> PartyOut:
    await _editable(session, perms, party_id)
    return await service.remove_address(session, party_id, address_id)


@router.post("/parties/{party_id}/contacts", operation_id="add_party_contact", status_code=Created)
async def add_contact(
    party_id: uuid.UUID, body: ContactIn, perms: Signed, session: TenantSessionDep
) -> PartyOut:
    await _editable(session, perms, party_id)
    return await service.add_contact(session, party_id, body)


@router.delete("/parties/{party_id}/contacts/{contact_id}", operation_id="remove_party_contact")
async def remove_contact(
    party_id: uuid.UUID, contact_id: uuid.UUID, perms: Signed, session: TenantSessionDep
) -> PartyOut:
    await _editable(session, perms, party_id)
    return await service.remove_contact(session, party_id, contact_id)


@router.put("/parties/{party_id}/customer-profile", operation_id="put_customer_profile")
async def put_customer_profile(
    party_id: uuid.UUID, body: CustomerProfileIn, perms: CustomerEdit, session: TenantSessionDep
) -> PartyOut:
    perms.require("customer.edit", body.business_id)
    current = await service.current_customer_profile(session, party_id, body.business_id)
    if service.credit_terms_changed(current, body):
        perms.require("credit.limit", body.business_id)
    return await service.put_customer_profile(session, party_id, body)


@router.put("/parties/{party_id}/supplier-profile", operation_id="put_supplier_profile")
async def put_supplier_profile(
    party_id: uuid.UUID, body: SupplierProfileIn, perms: SupplierEdit, session: TenantSessionDep
) -> PartyOut:
    perms.require("supplier.edit", body.business_id)
    return await service.put_supplier_profile(session, party_id, body)


@router.get("/customer-groups", operation_id="list_customer_groups")
async def list_customer_groups(
    perms: CustomerView, session: TenantSessionDep, business_id: uuid.UUID | None = None
) -> list[CustomerGroupOut]:
    return [
        g
        for g in await service.list_groups(session, business_id)
        if perms.allows("customer.view", g.business_id)
    ]


@router.post("/customer-groups", operation_id="create_customer_group", status_code=Created)
async def create_customer_group(
    body: CustomerGroupIn, perms: CustomerCreate, session: TenantSessionDep, principal: PrincipalDep
) -> CustomerGroupOut:
    perms.require("customer.create", body.business_id)
    return await service.create_group(session, principal.tenant_id, body)


@router.patch("/customer-groups/{group_id}", operation_id="update_customer_group")
async def update_customer_group(
    group_id: uuid.UUID, body: CustomerGroupPatch, perms: CustomerEdit, session: TenantSessionDep
) -> CustomerGroupOut:
    perms.require("customer.edit", (await service.get_group(session, group_id)).business_id)
    return await service.update_group(session, group_id, body)


@router.post("/parties/{party_id}/credit-check", operation_id="credit_check")
async def credit_check(
    party_id: uuid.UUID, body: CreditCheckIn, perms: CustomerView, session: TenantSessionDep
) -> CreditCheckOut:
    """Whether a sale of this amount may go ahead: ok, warn or block, the reasons and the figures."""
    perms.require("customer.view", body.business_id)
    may_override = perms.allows("credit.override", body.business_id)
    return await service.credit_check(session, party_id, body, may_override=may_override)
