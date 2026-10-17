"""Test data builders.

``seed_tenant`` creates a complete tenant (user, tenant, business, head office, warehouse, node, membership)
through the real services. ``ISOLATION_FACTORIES`` creates one extra row per tenant table in the current
tenant context; every tenant table must have an entry (tests/test_tenant_isolation.py enforces it).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs, numbering
from app.core.ids import new_id
from app.core.platform_models import Attachment, AuditLog, IdempotencyKey, IndustryPackApplication, Setting
from app.core.security import hash_password
from app.modules.accounting import accounts as accounting_accounts
from app.modules.accounting import periods as accounting_periods
from app.modules.accounting import vouchers
from app.modules.accounting.models import (
    AccountRole,
    AccountType,
    AllocationType,
    Bill,
    BillAllocation,
    FiscalPeriod,
    JournalEntry,
    JournalLine,
    VoucherAllocation,
    VoucherLine,
)
from app.modules.accounting.models import Role as LedgerRole
from app.modules.accounting.posting import VoucherType
from app.modules.accounting.schemas import AccountIn, AllocationIn, VoucherIn, VoucherLineIn
from app.modules.catalog import products as catalog_products
from app.modules.catalog import service as catalog
from app.modules.catalog import setup as catalog_setup
from app.modules.catalog.models import (
    Barcode,
    CustomFieldEntity,
    CustomFieldType,
    ProductAttribute,
    ProductUnit,
    UnitFamily,
    VariantAttributeValue,
)
from app.modules.catalog.schemas import (
    AttributeIn,
    AttributeValueIn,
    BarcodeIn,
    BrandIn,
    CategoryIn,
    CustomFieldIn,
    ProductIn,
    ProductUnitIn,
    UnitIn,
    VariantChoice,
)
from app.modules.identity.models import Membership, Role, RoleAssignment, RolePermission, UserAccount
from app.modules.parties import service as parties
from app.modules.parties.models import PartyAddress, PartyContact
from app.modules.parties.schemas import (
    AddressIn,
    ContactIn,
    CustomerGroupIn,
    CustomerProfileIn,
    PartyIn,
    SupplierProfileIn,
)
from app.modules.pricing import service as pricing
from app.modules.pricing.engine import PromotionKind
from app.modules.pricing.models import PriceListItem, PromotionScope
from app.modules.pricing.schemas import PriceListIn, PriceListItemIn, PromotionIn
from app.modules.tenancy import service as tenancy
from app.modules.tenancy.models import Emirate, Node, NodeKind
from app.modules.tenancy.schemas import BranchIn, BusinessIn, WarehouseIn

PASSWORD = "correct horse battery"


@dataclass(frozen=True)
class TenantSeed:
    tenant_id: uuid.UUID
    business_id: uuid.UUID
    branch_id: uuid.UUID
    warehouse_id: uuid.UUID
    node_id: uuid.UUID
    user_id: uuid.UUID


def business_in(name: str = "Al Noor Trading LLC") -> BusinessIn:
    return BusinessIn(legal_name_en=name, legal_name_ar="شركة النور للتجارة")


def branch_in(code: str = "HO", emirate: Emirate = Emirate.DUBAI) -> BranchIn:
    return BranchIn(code=code, name_en=f"Branch {code}", name_ar=f"فرع {code}", emirate=emirate)


async def add_user(session: AsyncSession, email: str | None = None) -> UserAccount:
    user = UserAccount(
        id=new_id(),
        email=email or f"{new_id().hex[:12]}@example.ae",
        password_hash=hash_password(PASSWORD),
        full_name="Test User",
    )
    session.add(user)
    await session.flush()
    return user


async def seed_tenant(session: AsyncSession, name: str = "Tenant") -> TenantSeed:
    """Leaves the session's transaction scoped to the new tenant."""
    user = await add_user(session)
    created = await tenancy.create_tenant(
        session, tenant_name=name, business=business_in(), head_office=branch_in(), user_id=user.id
    )
    session.add(Membership(id=new_id(), tenant_id=created.tenant_id, user_id=user.id))
    await session.flush()
    return TenantSeed(
        created.tenant_id,
        created.business_id,
        created.branch_id,
        created.warehouse_id,
        created.node_id,
        user.id,
    )


Factory = Callable[[AsyncSession, TenantSeed], Awaitable[Any]]


async def _tenant(_session: AsyncSession, seed: TenantSeed) -> Any:
    return seed.tenant_id


async def _business(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await tenancy.create_business(session, seed.tenant_id, business_in("Second Co"))).id


async def _branch(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await tenancy.create_branch(session, seed.tenant_id, seed.business_id, branch_in("B2"))).id


async def _warehouse(session: AsyncSession, seed: TenantSeed) -> Any:
    data = WarehouseIn(code="W2", name_en="Second", name_ar="الثاني")
    return (await tenancy.create_warehouse(session, seed.tenant_id, seed.branch_id, data)).id


async def _node(session: AsyncSession, seed: TenantSeed) -> Any:
    node = Node(id=new_id(), tenant_id=seed.tenant_id, kind=NodeKind.CLOUD.value, name="Cloud")
    session.add(node)
    await session.flush()
    return node.id


async def _membership(session: AsyncSession, seed: TenantSeed) -> Any:
    user = await add_user(session)
    membership = Membership(id=new_id(), tenant_id=seed.tenant_id, user_id=user.id)
    session.add(membership)
    await session.flush()
    return membership.id


async def _sync_outbox(session: AsyncSession, _seed: TenantSeed) -> Any:
    return (await session.execute(text("SELECT max(id) FROM sync_outbox"))).scalar_one()


async def _new_role(session: AsyncSession, seed: TenantSeed) -> Role:
    role = Role(id=new_id(), tenant_id=seed.tenant_id, name_en=f"Role {new_id().hex[:6]}", name_ar="دور")
    session.add(role)
    await session.flush()
    return role


async def _role(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _new_role(session, seed)).id


async def _role_permission(session: AsyncSession, seed: TenantSeed) -> Any:
    role = await _new_role(session, seed)
    row = RolePermission(
        id=new_id(), tenant_id=seed.tenant_id, role_id=role.id, permission_code="product.view"
    )
    session.add(row)
    await session.flush()
    return row.id


async def _role_assignment(session: AsyncSession, seed: TenantSeed) -> Any:
    role = await _new_role(session, seed)
    membership_id = await _membership(session, seed)
    row = RoleAssignment(
        id=new_id(),
        tenant_id=seed.tenant_id,
        membership_id=membership_id,
        role_id=role.id,
        business_id=seed.business_id,
        branch_id=seed.branch_id,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _audit_log(session: AsyncSession, seed: TenantSeed) -> Any:
    row = AuditLog(
        id=new_id(),
        tenant_id=seed.tenant_id,
        action="create",
        entity_type="business",
        entity_id=seed.business_id,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _allocate(session: AsyncSession, seed: TenantSeed) -> numbering.Allocated:
    return await numbering.allocate(
        session,
        tenant_id=seed.tenant_id,
        branch_id=seed.branch_id,
        doc_type="sales_invoice",
        fiscal_year="2026",
        node_id=seed.node_id,
        document_id=new_id(),
    )


async def _numbering_series(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _allocate(session, seed)).series_id


async def _released_number(session: AsyncSession, seed: TenantSeed) -> Any:
    allocated = await _allocate(session, seed)
    row = await numbering.release(
        session,
        tenant_id=seed.tenant_id,
        series_id=allocated.series_id,
        number=allocated.number,
        cancelled_document_id=new_id(),
    )
    return row.id


async def _outbox_event(session: AsyncSession, seed: TenantSeed) -> Any:
    return await jobs.publish(session, tenant_id=seed.tenant_id, event_type="test.noop")


async def _idempotency_key(session: AsyncSession, seed: TenantSeed) -> Any:
    row = IdempotencyKey(
        id=new_id(), tenant_id=seed.tenant_id, user_id=seed.user_id, key=new_id().hex, request_hash="0" * 64
    )
    session.add(row)
    await session.flush()
    return row.id


async def _attachment(session: AsyncSession, seed: TenantSeed) -> Any:
    row = Attachment(
        id=new_id(),
        tenant_id=seed.tenant_id,
        entity_type="business",
        entity_id=seed.business_id,
        filename="bill.pdf",
        content_type="application/pdf",
        size=1,
        sha256="0" * 64,
        storage_key=f"{seed.tenant_id}/{new_id()}",
    )
    session.add(row)
    await session.flush()
    return row.id


async def _setting(session: AsyncSession, seed: TenantSeed) -> Any:
    row = Setting(
        id=new_id(),
        tenant_id=seed.tenant_id,
        scope="business",
        scope_id=seed.business_id,
        key="ui.x",
        value=1,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _industry_pack_application(session: AsyncSession, seed: TenantSeed) -> Any:
    row = IndustryPackApplication(
        id=new_id(), tenant_id=seed.tenant_id, business_id=seed.business_id, pack_code="textile", version=1
    )
    session.add(row)
    await session.flush()
    return row.id


async def _unit_family(session: AsyncSession, seed: TenantSeed) -> Any:
    row = UnitFamily(id=new_id(), tenant_id=seed.tenant_id, code="energy", name_en="Energy", name_ar="الطاقة")
    session.add(row)
    await session.flush()
    return row.id


async def _unit(session: AsyncSession, seed: TenantSeed) -> Any:
    data = UnitIn(
        family="count",
        code="gross",
        name_en="Gross",
        name_ar="قروس",
        symbol="gr",
        decimals=0,
        factor_to_family_base=Decimal(144),
    )
    return (await catalog.create_unit(session, seed.tenant_id, data)).id


def _tag() -> str:
    return new_id().hex[-8:]


async def _category(session: AsyncSession, seed: TenantSeed) -> Any:
    out = await catalog_setup.create_category(
        session, seed.tenant_id, CategoryIn(name_en=f"Fabrics {_tag()}", name_ar="أقمشة")
    )
    return out.id


async def _brand(session: AsyncSession, seed: TenantSeed) -> Any:
    out = await catalog_setup.create_brand(
        session, seed.tenant_id, BrandIn(name_en=f"Brand {_tag()}", name_ar="علامة")
    )
    return out.id


async def _coloured_attribute(session: AsyncSession, seed: TenantSeed) -> Any:
    return await catalog_setup.create_attribute(
        session,
        seed.tenant_id,
        AttributeIn(
            code=f"color_{_tag()}",
            name_en="Colour",
            name_ar="اللون",
            values=[AttributeValueIn(value_en="Red", value_ar="أحمر")],
        ),
    )


async def _attribute(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _coloured_attribute(session, seed)).id


async def _attribute_value(session: AsyncSession, seed: TenantSeed) -> Any:
    attribute = await _coloured_attribute(session, seed)
    added = await catalog_setup.add_value(
        session, seed.tenant_id, attribute.id, AttributeValueIn(value_en="Blue", value_ar="أزرق")
    )
    return added.id


async def _custom_field_def(session: AsyncSession, seed: TenantSeed) -> Any:
    data = CustomFieldIn(
        entity_type=CustomFieldEntity.PRODUCT,
        key=f"note_{_tag()}",
        label_en="Note",
        label_ar="ملاحظة",
        data_type=CustomFieldType.TEXT,
    )
    return (await catalog_setup.create_custom_field(session, seed.tenant_id, data)).id


async def _new_product(session: AsyncSession, seed: TenantSeed, *, coloured: bool = False) -> Any:
    attributes = []
    if coloured:
        attribute = await _coloured_attribute(session, seed)
        attributes = [VariantChoice(attribute=attribute.code, values=["Red"])]
    data = ProductIn(
        sku=f"ISO-{_tag()}",
        name_en="Isolation product",
        name_ar="منتج",
        base_unit="pcs",
        units=[ProductUnitIn(unit="dozen")],
        attributes=attributes,
    )
    return await catalog_products.create_product(session, seed.tenant_id, data)


async def _product(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _new_product(session, seed)).id


async def _product_unit(session: AsyncSession, seed: TenantSeed) -> Any:
    product = await _new_product(session, seed)
    return await session.scalar(select(ProductUnit.id).where(ProductUnit.product_id == product.id).limit(1))


async def _product_attribute(session: AsyncSession, seed: TenantSeed) -> Any:
    product = await _new_product(session, seed, coloured=True)
    return await session.scalar(select(ProductAttribute.id).where(ProductAttribute.product_id == product.id))


async def _variant(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _new_product(session, seed)).variants[0].id


async def _variant_attribute_value(session: AsyncSession, seed: TenantSeed) -> Any:
    product = await _new_product(session, seed, coloured=True)
    return await session.scalar(
        select(VariantAttributeValue.id).where(VariantAttributeValue.variant_id == product.variants[0].id)
    )


async def _barcode(session: AsyncSession, seed: TenantSeed) -> Any:
    product = await _new_product(session, seed)
    await catalog_products.add_barcode(
        session, product.id, BarcodeIn(variant_id=product.variants[0].id, unit="pcs")
    )
    return await session.scalar(select(Barcode.id).where(Barcode.variant_id == product.variants[0].id))


async def _new_price_list(session: AsyncSession, seed: TenantSeed) -> Any:
    data = PriceListIn(business_id=seed.business_id, name_en=f"List {_tag()}", name_ar="قائمة")
    return await pricing.create_price_list(session, seed.tenant_id, data)


async def _price_list(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _new_price_list(session, seed)).id


async def _price_list_item(session: AsyncSession, seed: TenantSeed) -> Any:
    price_list = await _new_price_list(session, seed)
    product = await _new_product(session, seed)
    data = PriceListItemIn(variant_id=product.variants[0].id, unit="pcs", price=Decimal("2.5000"))
    await pricing.put_item(session, seed.tenant_id, price_list.id, data)
    return await session.scalar(select(PriceListItem.id).where(PriceListItem.price_list_id == price_list.id))


async def _promotion(session: AsyncSession, seed: TenantSeed) -> Any:
    product = await _new_product(session, seed)
    data = PromotionIn(
        business_id=seed.business_id,
        name_en="Eid",
        name_ar="العيد",
        kind=PromotionKind.PERCENT,
        value=Decimal(10),
        scope=PromotionScope.PRODUCT,
        scope_id=product.id,
    )
    return (await pricing.create_promotion(session, seed.tenant_id, data)).id


async def _new_party(session: AsyncSession, seed: TenantSeed, **extra: Any) -> Any:
    data = PartyIn(name_en=f"Party {_tag()}", name_ar="طرف", is_customer=True, is_supplier=True, **extra)
    return await parties.create_party(session, seed.tenant_id, data)


async def _party(session: AsyncSession, seed: TenantSeed) -> Any:
    return (await _new_party(session, seed)).id


async def _party_address(session: AsyncSession, seed: TenantSeed) -> Any:
    party = await _new_party(session, seed, addresses=[AddressIn(line1="Deira", emirate="dubai")])
    return await session.scalar(select(PartyAddress.id).where(PartyAddress.party_id == party.id))


async def _party_contact(session: AsyncSession, seed: TenantSeed) -> Any:
    party = await _new_party(session, seed, contacts=[ContactIn(name="Rashid")])
    return await session.scalar(select(PartyContact.id).where(PartyContact.party_id == party.id))


async def _customer_group(session: AsyncSession, seed: TenantSeed) -> Any:
    data = CustomerGroupIn(business_id=seed.business_id, name_en=f"Group {_tag()}", name_ar="مجموعة")
    return (await parties.create_group(session, seed.tenant_id, data)).id


async def _customer_profile(session: AsyncSession, seed: TenantSeed) -> Any:
    party = await _new_party(session, seed)
    data = CustomerProfileIn(business_id=seed.business_id, credit_limit=Decimal("5000.0000"))
    return (await parties.put_customer_profile(session, party.id, data)).customer_profiles[0].id


async def _supplier_profile(session: AsyncSession, seed: TenantSeed) -> Any:
    party = await _new_party(session, seed)
    data = SupplierProfileIn(business_id=seed.business_id, payment_terms_days=30)
    return (await parties.put_supplier_profile(session, party.id, data)).supplier_profiles[0].id


# --- accounting (M1.5) -------------------------------------------------------------------------------------


def dubai_today() -> date:
    """The business's today (posting creates the current fiscal year on demand)."""
    return datetime.now(ZoneInfo("Asia/Dubai")).date()


async def role_account(session: AsyncSession, seed: TenantSeed, role: LedgerRole) -> uuid.UUID:
    return (await accounting_accounts.role_map(session, seed.business_id))[role]


async def cash_account(session: AsyncSession, seed: TenantSeed) -> uuid.UUID:
    for account in (await accounting_accounts.accounts_by_id(session, seed.business_id)).values():
        if account.account_type == AccountType.CASH.value:
            return account.id
    raise AssertionError("the default chart has a cash account")


def charge_customer(
    seed: TenantSeed, party_id: uuid.UUID, ar: uuid.UUID, sales: uuid.UUID, amount: Decimal, reference: str
) -> VoucherIn:
    """A journal billing a customer (the shape of an opening invoice): Dr customer (new bill) / Cr sales."""
    return VoucherIn(
        doc_type=VoucherType.JOURNAL,
        business_id=seed.business_id,
        branch_id=seed.branch_id,
        doc_date=dubai_today(),
        lines=[
            VoucherLineIn(
                account_id=ar,
                party_id=party_id,
                debit=amount,
                allocations=[AllocationIn(type=AllocationType.NEW_REF, amount=amount, reference=reference)],
            ),
            VoucherLineIn(account_id=sales, credit=amount),
        ],
    )


async def post_voucher(session: AsyncSession, seed: TenantSeed, data: VoucherIn) -> uuid.UUID:
    doc = await vouchers.create_draft(session, seed.tenant_id, data)
    await vouchers.post(
        session,
        doc.id,
        node_id=seed.node_id,
        user_id=seed.user_id,
        may_override_lock=False,
        override_reason=None,
    )
    return doc.id


async def _billed_and_paid(session: AsyncSession, seed: TenantSeed) -> uuid.UUID:
    """A customer billed 1,000 and paying 400 of it: journal entries, a bill and a bill allocation."""
    party = await _new_party(session, seed)
    ar = await role_account(session, seed, LedgerRole.AR_CONTROL)
    sales = await role_account(session, seed, LedgerRole.SALES)
    invoice = await post_voucher(
        session, seed, charge_customer(seed, party.id, ar, sales, Decimal(1000), "INV-1")
    )
    bill_id = await session.scalar(select(Bill.id).where(Bill.document_id == invoice))
    receipt = VoucherIn(
        doc_type=VoucherType.RECEIPT,
        business_id=seed.business_id,
        branch_id=seed.branch_id,
        doc_date=dubai_today(),
        account_id=await cash_account(session, seed),
        lines=[
            VoucherLineIn(
                party_id=party.id,
                amount=Decimal(400),
                allocations=[
                    AllocationIn(type=AllocationType.AGAINST_REF, amount=Decimal(400), bill_id=bill_id)
                ],
            )
        ],
    )
    return await post_voucher(session, seed, receipt)


async def _account(session: AsyncSession, seed: TenantSeed) -> Any:
    accounts = await accounting_accounts.accounts_by_id(session, seed.business_id)
    group = next(a for a in accounts.values() if a.code == "6000")
    data = AccountIn(
        business_id=seed.business_id,
        code=f"6{_tag()[:6]}",
        name_en="Courier",
        name_ar="البريد السريع",
        parent_id=group.id,
        account_type=AccountType.EXPENSE,
    )
    return (await accounting_accounts.create_account(session, seed.tenant_id, data)).id


async def _account_role(session: AsyncSession, seed: TenantSeed) -> Any:
    return await session.scalar(
        select(AccountRole.id).where(AccountRole.business_id == seed.business_id).limit(1)
    )


async def _fiscal_year(session: AsyncSession, seed: TenantSeed) -> Any:
    business = await tenancy.get_business(session, seed.business_id)
    return (await accounting_periods.create_year(session, business, dubai_today())).id


async def _fiscal_period(session: AsyncSession, seed: TenantSeed) -> Any:
    year_id = await _fiscal_year(session, seed)
    return await session.scalar(
        select(FiscalPeriod.id).where(FiscalPeriod.fiscal_year_id == year_id).limit(1)
    )


async def _draft_with_allocation(session: AsyncSession, seed: TenantSeed) -> uuid.UUID:
    party = await _new_party(session, seed)
    ar = await role_account(session, seed, LedgerRole.AR_CONTROL)
    sales = await role_account(session, seed, LedgerRole.SALES)
    doc = await vouchers.create_draft(
        session, seed.tenant_id, charge_customer(seed, party.id, ar, sales, Decimal(250), "OPEN-1")
    )
    return doc.id


async def _document(session: AsyncSession, seed: TenantSeed) -> Any:
    return await _draft_with_allocation(session, seed)


async def _voucher_line(session: AsyncSession, seed: TenantSeed) -> Any:
    doc_id = await _draft_with_allocation(session, seed)
    return await session.scalar(select(VoucherLine.id).where(VoucherLine.document_id == doc_id).limit(1))


async def _voucher_allocation(session: AsyncSession, seed: TenantSeed) -> Any:
    doc_id = await _draft_with_allocation(session, seed)
    return await session.scalar(select(VoucherAllocation.id).where(VoucherAllocation.document_id == doc_id))


async def _journal_entry(session: AsyncSession, seed: TenantSeed) -> Any:
    doc_id = await _billed_and_paid(session, seed)
    return await session.scalar(select(JournalEntry.id).where(JournalEntry.source_document_id == doc_id))


async def _journal_line(session: AsyncSession, seed: TenantSeed) -> Any:
    entry_id = await _journal_entry(session, seed)
    return await session.scalar(select(JournalLine.id).where(JournalLine.entry_id == entry_id).limit(1))


async def _bill(session: AsyncSession, seed: TenantSeed) -> Any:
    await _billed_and_paid(session, seed)
    return await session.scalar(select(Bill.id).limit(1))


async def _bill_allocation(session: AsyncSession, seed: TenantSeed) -> Any:
    await _billed_and_paid(session, seed)
    return await session.scalar(select(BillAllocation.id).limit(1))


ISOLATION_FACTORIES: dict[str, Factory] = {
    "audit_log": _audit_log,
    "numbering_series": _numbering_series,
    "released_number": _released_number,
    "outbox_event": _outbox_event,
    "idempotency_key": _idempotency_key,
    "attachment": _attachment,
    "setting": _setting,
    "industry_pack_application": _industry_pack_application,
    "tenant": _tenant,
    "business": _business,
    "branch": _branch,
    "warehouse": _warehouse,
    "node": _node,
    "membership": _membership,
    "sync_outbox": _sync_outbox,
    "role": _role,
    "role_permission": _role_permission,
    "role_assignment": _role_assignment,
    "unit_family": _unit_family,
    "unit": _unit,
    "category": _category,
    "brand": _brand,
    "attribute": _attribute,
    "attribute_value": _attribute_value,
    "custom_field_def": _custom_field_def,
    "product": _product,
    "product_unit": _product_unit,
    "product_attribute": _product_attribute,
    "variant": _variant,
    "variant_attribute_value": _variant_attribute_value,
    "barcode": _barcode,
    "price_list": _price_list,
    "price_list_item": _price_list_item,
    "promotion": _promotion,
    "party": _party,
    "party_address": _party_address,
    "party_contact": _party_contact,
    "customer_group": _customer_group,
    "customer_profile": _customer_profile,
    "supplier_profile": _supplier_profile,
    "account": _account,
    "account_role": _account_role,
    "fiscal_year": _fiscal_year,
    "fiscal_period": _fiscal_period,
    "document": _document,
    "voucher_line": _voucher_line,
    "voucher_allocation": _voucher_allocation,
    "journal_entry": _journal_entry,
    "journal_line": _journal_line,
    "bill": _bill,
    "bill_allocation": _bill_allocation,
}

# Column holding the tenant id, where it is not ``tenant_id``.
TENANT_COLUMN = {"tenant": "id"}
# Tenant tables that are never synced to the cloud (local queues) and tables the app may only append to.
NOT_SYNCED = frozenset({"sync_outbox", "outbox_event", "idempotency_key"})
APPEND_ONLY = frozenset({"audit_log", "journal_entry", "journal_line", "bill_allocation"})
# Rows a trigger guards against every change but one column: claiming them is tried by inserting a copy.
GUARDED = frozenset({"bill"})
