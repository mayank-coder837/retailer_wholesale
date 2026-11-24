"""The first set of tools (M1.13). Each one is a thin, typed call over an existing service: no SQL, no new
rules.

Reads and drafts run at once; the commit tier (post, cancel, apply a setup) is only ever run by the
confirmation step in ``service.py``. The business, tenant and person come from the signed-in request
(``ToolContext``)."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from app.modules.accounting import periods, vouchers
from app.modules.accounting import service as accounting
from app.modules.accounting.models import AccountType, AllocationType, Direction
from app.modules.accounting.posting import VoucherType
from app.modules.accounting.schemas import AllocationIn, VoucherIn, VoucherLineIn
from app.modules.ai.registry import Risk, ToolContext, ToolError, tool
from app.modules.documents import service as documents
from app.modules.parties import service as parties
from app.modules.platform import packs
from app.modules.tenancy import service as tenancy

Amount = Annotated[Decimal, Field(gt=0, max_digits=19, decimal_places=4)]
PartyKind = Literal["customer", "supplier"]


def _json(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_json(v) for v in value]
    return value


def _amount_text(value: Decimal) -> str:
    """Amounts as the books keep them, four places ("5000.0000", never "5000")."""
    return str(value.quantize(Decimal("0.0001")))


class Nothing(BaseModel):
    pass


# --- reads ----------------------------------------------------------------------------------------------


class FindPartyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="Part of the name, code, phone or TRN")
    kind: PartyKind = "customer"


async def _party_permission(_ctx: ToolContext, args: Any) -> str:
    return f"{args.kind}.view"


@tool(
    name="find_party",
    description="Find customers or suppliers whose name, code, phone or TRN matches.",
    risk=Risk.READ,
    permission=_party_permission,
    input=FindPartyIn,
)
async def find_party(ctx: ToolContext, args: FindPartyIn) -> dict[str, Any]:
    found = await parties.list_parties(
        ctx.session,
        q=args.name,
        customers=args.kind == "customer",
        suppliers=args.kind == "supplier",
        limit=10,
    )
    return {
        "parties": [
            {"id": str(p.id), "code": p.code, "name_en": p.name_en, "name_ar": p.name_ar} for p in found
        ]
    }


class OpenBillsIn(BaseModel):
    party_id: uuid.UUID
    kind: PartyKind = "customer"


@tool(
    name="get_open_bills",
    description="A customer's unpaid invoices, or a supplier's unpaid bills.",
    risk=Risk.READ,
    permission=_party_permission,
    input=OpenBillsIn,
)
async def get_open_bills(ctx: ToolContext, args: OpenBillsIn) -> dict[str, Any]:
    bills = await accounting.list_bills(ctx.session, ctx.business_id, args.party_id)
    owed = Direction.DEBIT if args.kind == "customer" else Direction.CREDIT
    return {
        "bills": [
            {
                "id": str(b.id),
                "reference": b.reference,
                "bill_date": b.bill_date.isoformat(),
                "due_date": b.due_date.isoformat() if b.due_date else None,
                "amount": str(b.amount),
                "amount_open": str(b.amount_open),
            }
            for b in bills
            if b.direction == owed and not b.is_advance
        ]
    }


async def _ageing(ctx: ToolContext, receivable: bool) -> dict[str, Any]:
    business = await tenancy.get_business(ctx.session, ctx.business_id)
    report = await accounting.ageing(
        ctx.session, ctx.business_id, receivable=receivable, today=periods.business_today(business)
    )
    return dict(_json(report))


@tool(
    name="get_receivables_ageing",
    description="What customers owe, by how many days past due.",
    risk=Risk.READ,
    permission="report.view",
    input=Nothing,
)
async def get_receivables_ageing(ctx: ToolContext, _args: Nothing) -> dict[str, Any]:
    return await _ageing(ctx, True)


@tool(
    name="get_payables_ageing",
    description="What is owed to suppliers, by how many days past due.",
    risk=Risk.READ,
    permission="report.view",
    input=Nothing,
)
async def get_payables_ageing(ctx: ToolContext, _args: Nothing) -> dict[str, Any]:
    return await _ageing(ctx, False)


@tool(
    name="list_industry_packs",
    description="The ready-made setups (textile, grocery…) that can be applied to this business.",
    risk=Risk.READ,
    permission="business.view",
    input=Nothing,
)
async def list_industry_packs(_ctx: ToolContext, _args: Nothing) -> dict[str, Any]:
    return {
        "packs": [
            {"code": p.code, "name_en": p.name_en, "name_ar": p.name_ar, "description": p.description}
            for p in packs.summaries()
        ]
    }


# --- drafts ---------------------------------------------------------------------------------------------


class DraftMoneyIn(BaseModel):
    party_id: uuid.UUID
    amount: Amount
    bill_reference: str | None = Field(
        default=None, max_length=100, description="The invoice or bill this settles, e.g. INV-1093"
    )
    through: Literal["cash", "bank"] = "cash"
    doc_date: date | None = Field(default=None, description="Defaults to today in the business's time zone")
    narration: str | None = Field(default=None, max_length=2000)


async def _money_account(ctx: ToolContext, through: str) -> uuid.UUID:
    """The cash (or bank) account the money moves through: the chart's first, preferring the standard code."""
    wanted = AccountType.CASH if through == "cash" else AccountType.BANK
    chart = await accounting.chart_accounts(ctx.session, ctx.business_id)
    options = [a for a in chart if a.account_type == wanted and not a.is_group and a.archived_at is None]
    if not options:
        raise ToolError("no_money_account", through=through)
    options.sort(key=lambda a: (a.code != ("1110" if through == "cash" else "1120"), a.code))
    return options[0].id


async def _head_branch(ctx: ToolContext) -> uuid.UUID:
    branches = list(await tenancy.list_branches(ctx.session, ctx.business_id))
    if not branches:
        raise ToolError("no_branch")
    branches.sort(key=lambda b: not b.is_head_office)
    return branches[0].id


async def _allocations(
    ctx: ToolContext, args: DraftMoneyIn, owed: Direction
) -> tuple[list[AllocationIn], dict[str, Any]]:
    """The named bill gets what it can hold; any rest stays on account (never an advance by guess)."""
    if args.bill_reference is None:
        return [AllocationIn(type=AllocationType.ON_ACCOUNT, amount=args.amount)], {
            "on_account": _amount_text(args.amount)
        }
    bills = await accounting.list_bills(ctx.session, ctx.business_id, args.party_id)
    wanted = args.bill_reference.strip().lower()
    match = next(
        (b for b in bills if b.reference.lower() == wanted and b.direction == owed and not b.is_advance), None
    )
    if match is None:
        raise ToolError("bill_not_found", reference=args.bill_reference)
    settled = min(args.amount, match.amount_open)
    rows = [AllocationIn(type=AllocationType.AGAINST_REF, amount=settled, bill_id=match.id)]
    if args.amount > settled:
        rows.append(AllocationIn(type=AllocationType.ON_ACCOUNT, amount=args.amount - settled))
    return rows, {
        "bill": match.reference,
        "settled": _amount_text(settled),
        "on_account": _amount_text(args.amount - settled),
    }


async def _draft(
    ctx: ToolContext, args: DraftMoneyIn, kind: VoucherType, party_kind: PartyKind
) -> dict[str, Any]:
    party = await parties.get_party(ctx.session, args.party_id)
    if not (party.is_customer if party_kind == "customer" else party.is_supplier):
        raise ToolError("wrong_party_kind", party=party.name_en, kind=party_kind)
    business = await tenancy.get_business(ctx.session, ctx.business_id)
    owed = Direction.DEBIT if party_kind == "customer" else Direction.CREDIT
    rows, facts = await _allocations(ctx, args, owed)
    data = VoucherIn(
        doc_type=kind,
        business_id=ctx.business_id,
        branch_id=await _head_branch(ctx),
        doc_date=args.doc_date or periods.business_today(business),
        narration=args.narration,
        account_id=await _money_account(ctx, args.through),
        lines=[VoucherLineIn(party_id=args.party_id, amount=args.amount, allocations=rows)],
    )
    doc = await vouchers.create_draft(ctx.session, ctx.tenant_id, data)
    return {
        "document_id": str(doc.id),
        "doc_type": kind.value,
        "party": party.name_en,
        "amount": _amount_text(args.amount),
        **facts,
    }


@tool(
    name="create_draft_receipt",
    description="Money in from a customer, cash or bank, settling a named invoice. Saved as a draft.",
    risk=Risk.DRAFT,
    permission="receipt.create",
    input=DraftMoneyIn,
)
async def create_draft_receipt(ctx: ToolContext, args: DraftMoneyIn) -> dict[str, Any]:
    return await _draft(ctx, args, VoucherType.RECEIPT, "customer")


@tool(
    name="create_draft_payment",
    description="Money out to a supplier, in cash or from the bank, settling a named bill. Saved as a draft.",
    risk=Risk.DRAFT,
    permission="payment.create",
    input=DraftMoneyIn,
)
async def create_draft_payment(ctx: ToolContext, args: DraftMoneyIn) -> dict[str, Any]:
    return await _draft(ctx, args, VoucherType.PAYMENT, "supplier")


# --- commits (run only when the person confirms) --------------------------------------------------------


class PostVoucherIn(BaseModel):
    document_id: uuid.UUID


def _in_this_business(ctx: ToolContext, business_id: uuid.UUID) -> None:
    """A document of another business is not found, whatever the person may do in this one."""
    if business_id != ctx.business_id:
        raise vouchers.VoucherNotFoundError()


async def _post_permission(ctx: ToolContext, args: Any) -> str:
    doc = await vouchers.get_document(ctx.session, args.document_id)
    _in_this_business(ctx, doc.business_id)
    return f"{doc.doc_type}.post"


@tool(
    name="post_voucher",
    description="Post a draft voucher (receipt, payment…) to the books.",
    risk=Risk.COMMIT,
    permission=_post_permission,
    input=PostVoucherIn,
)
async def post_voucher(ctx: ToolContext, args: PostVoucherIn) -> dict[str, Any]:
    _in_this_business(ctx, (await vouchers.get_document(ctx.session, args.document_id)).business_id)
    doc = await vouchers.post(
        ctx.session,
        args.document_id,
        node_id=ctx.node_id,
        user_id=ctx.user_id,
        may_override_lock=False,  # a locked period is never the assistant's call
        override_reason=None,
    )
    return {"document_id": str(doc.id), "doc_type": doc.doc_type, "number": doc.number}


class CancelIn(BaseModel):
    document_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=500)


async def _cancel_permission(ctx: ToolContext, args: Any) -> str:
    doc = await documents.get_document(ctx.session, args.document_id)
    _in_this_business(ctx, doc.business_id)
    return f"{doc.doc_type}.cancel"


@tool(
    name="cancel_document",
    description="Cancel a posted document (open period only); the books are reversed on its date.",
    risk=Risk.COMMIT,
    permission=_cancel_permission,
    input=CancelIn,
)
async def cancel_document(ctx: ToolContext, args: CancelIn) -> dict[str, Any]:
    _in_this_business(ctx, (await documents.get_document(ctx.session, args.document_id)).business_id)
    doc = await documents.cancel(ctx.session, args.document_id, user_id=ctx.user_id, reason=args.reason)
    return {"document_id": str(doc.id), "doc_type": doc.doc_type, "number": doc.number, "status": doc.status}


class ApplyPackIn(BaseModel):
    code: str = Field(min_length=1, max_length=60)


@tool(
    name="apply_industry_pack",
    description="Apply a ready-made industry setup (units, attributes, settings) to this business.",
    risk=Risk.COMMIT,
    permission="business.edit",
    input=ApplyPackIn,
)
async def apply_industry_pack(ctx: ToolContext, args: ApplyPackIn) -> dict[str, Any]:
    applied = await packs.apply(
        ctx.session, tenant_id=ctx.tenant_id, business_id=ctx.business_id, code=args.code
    )
    return dict(_json(applied))
