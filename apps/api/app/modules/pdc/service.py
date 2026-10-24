"""The PDC register (M1.9; decision A2; posting-rules.md rules 15 to 22).

A cheque is a memo until the bank clears it. Clearance posts a receipt (received) or payment (issued) voucher
on
the clearance date through the accounting service — the same checks, numbering, period lock and audit as a
voucher
typed by hand — allocated bill-wise against the bills the cheque was meant for, as far as they are still
open; the
rest is an advance. A bounce changes only the status, plus an optional bank-charge expense voucher.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.modules.accounting import service as accounting
from app.modules.accounting.models import AccountType, AllocationType, Direction, Role
from app.modules.accounting.posting import VoucherType
from app.modules.accounting.schemas import AllocationIn, VoucherIn, VoucherLineIn
from app.modules.documents.models import Document
from app.modules.parties import service as parties
from app.modules.pdc import state
from app.modules.pdc.models import Pdc, PdcDirection, PdcIntendedBill, PdcStatus
from app.modules.pdc.schemas import BounceIn, ClearIn, DepositIn, IntendedBillOut, PdcIn, PdcOut
from app.modules.tenancy import service as tenancy

ZERO = Decimal("0.0000")


class PdcNotFoundError(NotFoundError):
    code = "pdc_not_found"
    title = "There is no such cheque"


class InvalidPdcError(UnprocessableError):
    code = "invalid_pdc"
    title = "The cheque is not valid"


class InvalidPdcTransitionError(ConflictError):
    code = "invalid_pdc_transition"
    title = "The cheque cannot do that from where it stands"


class PdcNotEditableError(ConflictError):
    code = "pdc_not_editable"
    title = "Only a cheque in hand (or issued, not cleared) can change"


def _invalid(message: str, **where: object) -> InvalidPdcError:
    return InvalidPdcError(message, **where)


async def get_pdc(session: AsyncSession, pdc_id: uuid.UUID, *, lock: bool = False) -> Pdc:
    stmt = select(Pdc).where(Pdc.id == pdc_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    pdc = (await session.scalars(stmt)).first()
    if pdc is None:
        raise PdcNotFoundError()
    return pdc


async def _check(session: AsyncSession, data: PdcIn) -> None:
    business = await tenancy.get_business(session, data.business_id)
    branch = await tenancy.get_branch(session, data.branch_id)
    if branch.business_id != business.id:
        raise _invalid("The branch belongs to another business.", field="branch_id")
    party = await parties.get_party_row(session, data.party_id)
    received = data.direction is PdcDirection.RECEIVED
    if received and not party.is_customer:
        raise _invalid("A cheque received comes from a customer.", field="party_id")
    if not received and not party.is_supplier:
        raise _invalid("A cheque issued goes to a supplier.", field="party_id")
    if not received and data.deposit_account_id is None:
        raise _invalid("Choose the bank account the cheque is drawn on.", field="deposit_account_id")
    if data.deposit_account_id is not None:
        if received:
            raise _invalid("A cheque received is given its bank when deposited.", field="deposit_account_id")
        await accounting.bank_account(session, business.id, data.deposit_account_id, "deposit_account_id")
    owed = Direction.DEBIT if received else Direction.CREDIT
    kind = AccountType.RECEIVABLE if received else AccountType.PAYABLE
    total = Decimal(0)
    seen: set[uuid.UUID] = set()
    for i, item in enumerate(data.intended_bills):
        where = f"intended_bills[{i}].bill_id"
        bill = await accounting.get_bill(session, item.bill_id)
        if (
            bill is None
            or item.bill_id in seen
            or bill.party_id != data.party_id
            or bill.direction != owed
            or await accounting.bill_account_type(session, bill.id) != kind
        ):
            raise _invalid("Choose an open bill of this party, each once.", field=where)
        if item.amount > bill.amount_open:
            raise _invalid(
                "That is more than the bill has open.", field=where, amount_open=str(bill.amount_open)
            )
        seen.add(item.bill_id)
        total += item.amount
    if total > data.amount:
        raise _invalid("The bills take more than the cheque's amount.", field="intended_bills")


async def _write_intended(session: AsyncSession, pdc: Pdc, data: PdcIn) -> None:
    await session.execute(delete(PdcIntendedBill).where(PdcIntendedBill.pdc_id == pdc.id))
    for item in data.intended_bills:
        session.add(
            PdcIntendedBill(tenant_id=pdc.tenant_id, pdc_id=pdc.id, bill_id=item.bill_id, amount=item.amount)
        )
    await session.flush()


def _details(pdc: Pdc, data: PdcIn) -> None:
    pdc.branch_id = data.branch_id
    pdc.party_id = data.party_id
    pdc.cheque_no = data.cheque_no.strip()
    pdc.bank_name = data.bank_name
    pdc.cheque_date = data.cheque_date
    pdc.amount = data.amount
    pdc.deposit_account_id = data.deposit_account_id
    pdc.notes = data.notes


async def create(session: AsyncSession, tenant_id: uuid.UUID, data: PdcIn) -> Pdc:
    """Rules 15 and 20: the cheque goes into the register; nothing reaches the ledger."""
    await _check(session, data)
    business = await tenancy.get_business(session, data.business_id)
    pdc = Pdc(
        tenant_id=tenant_id,
        business_id=data.business_id,
        direction=data.direction.value,
        currency=business.base_currency,
        status=state.FIRST[data.direction].value,
    )
    _details(pdc, data)
    session.add(pdc)
    await session.flush()
    await _write_intended(session, pdc, data)
    return pdc


async def update(session: AsyncSession, pdc_id: uuid.UUID, data: PdcIn) -> Pdc:
    pdc = await get_pdc(session, pdc_id, lock=True)
    if PdcStatus(pdc.status) not in state.EDITABLE:
        raise PdcNotEditableError(pdc_status=pdc.status)
    if data.business_id != pdc.business_id or data.direction.value != pdc.direction:
        raise _invalid("A cheque keeps its business and direction.", field="direction")
    await _check(session, data)
    _details(pdc, data)
    await session.flush()
    await _write_intended(session, pdc, data)
    return pdc


async def _move(session: AsyncSession, pdc_id: uuid.UUID, action: state.Action) -> tuple[Pdc, PdcStatus]:
    pdc = await get_pdc(session, pdc_id, lock=True)
    try:
        target = state.next_status(PdcDirection(pdc.direction), PdcStatus(pdc.status), action)
    except state.InvalidTransitionError as exc:
        raise InvalidPdcTransitionError(str(exc), pdc_status=pdc.status, action=action.value) from exc
    return pdc, target


async def deposit(session: AsyncSession, pdc_id: uuid.UUID, data: DepositIn) -> Pdc:
    """Rule 16: the cheque goes to the bank; still nothing in the ledger."""
    pdc, target = await _move(session, pdc_id, state.Action.DEPOSIT)
    await accounting.bank_account(session, pdc.business_id, data.bank_account_id)
    pdc.deposit_account_id = data.bank_account_id
    pdc.deposited_on = data.date
    pdc.status = target.value
    await session.flush()
    return pdc


async def clear(
    session: AsyncSession,
    pdc_id: uuid.UUID,
    data: ClearIn,
    *,
    node_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    may_override_lock: bool,
) -> Pdc:
    """Rules 17 and 21: the receipt or payment is posted on the clearance date and settles the intended
    bills."""
    pdc, target = await _move(session, pdc_id, state.Action.CLEAR)
    if data.bank_account_id is not None:
        if pdc.status == PdcStatus.DEPOSITED.value or pdc.direction == PdcDirection.ISSUED.value:
            raise _invalid("The cheque already has its bank account.", field="bank_account_id")
        await accounting.bank_account(session, pdc.business_id, data.bank_account_id)
        pdc.deposit_account_id = data.bank_account_id
    if pdc.deposit_account_id is None:
        raise _invalid("Choose the bank account the cheque cleared into.", field="bank_account_id")
    allocations: list[AllocationIn] = []
    left = pdc.amount
    for item in await _intended(session, pdc.id):
        bill = await accounting.get_bill(session, item.bill_id)
        take = min(item.amount, bill.amount_open if bill else ZERO, left)
        if take > 0:
            allocations.append(
                AllocationIn(type=AllocationType.AGAINST_REF, amount=take, bill_id=item.bill_id)
            )
            left -= take
    if left > 0:
        allocations.append(
            AllocationIn(type=AllocationType.ADVANCE, amount=left, reference=f"PDC {pdc.cheque_no}")
        )
    kind = VoucherType.RECEIPT if pdc.direction == PdcDirection.RECEIVED.value else VoucherType.PAYMENT
    voucher = await accounting.post_voucher(
        session,
        pdc.tenant_id,
        VoucherIn(
            doc_type=kind,
            business_id=pdc.business_id,
            branch_id=pdc.branch_id,
            doc_date=data.date,
            reference=f"PDC {pdc.cheque_no}",
            narration=f"Cheque {pdc.cheque_no}" + (f", {pdc.bank_name}" if pdc.bank_name else ""),
            account_id=pdc.deposit_account_id,
            lines=[VoucherLineIn(party_id=pdc.party_id, amount=pdc.amount, allocations=allocations)],
        ),
        node_id=node_id,
        user_id=user_id,
        may_override_lock=may_override_lock,
        override_reason=data.override_lock_reason,
    )
    pdc.voucher_id = voucher.id
    pdc.cleared_on = data.date
    pdc.status = target.value
    await session.flush()
    return pdc


async def bounce(
    session: AsyncSession,
    pdc_id: uuid.UUID,
    data: BounceIn,
    *,
    node_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> Pdc:
    """Rules 18 and 22: only the status changes; a bank charge, if given, is an expense voucher."""
    pdc, target = await _move(session, pdc_id, state.Action.BOUNCE)
    if data.charge is not None:
        if pdc.deposit_account_id is None:
            raise _invalid("A bank charge needs the bank account the cheque went through.", field="charge")
        roles = await accounting.role_accounts(session, pdc.business_id)
        if Role.BANK_CHARGES not in roles:
            raise _invalid("Give the bank charges role an account first.", roles=[Role.BANK_CHARGES.value])
        charge = await accounting.post_voucher(
            session,
            pdc.tenant_id,
            VoucherIn(
                doc_type=VoucherType.EXPENSE,
                business_id=pdc.business_id,
                branch_id=pdc.branch_id,
                doc_date=data.date,
                reference=f"PDC {pdc.cheque_no}",
                narration=f"Bounced cheque {pdc.cheque_no}: {data.reason}",
                account_id=pdc.deposit_account_id,
                lines=[VoucherLineIn(account_id=roles[Role.BANK_CHARGES], amount=data.charge)],
            ),
            node_id=node_id,
            user_id=user_id,
        )
        pdc.charge_voucher_id = charge.id
    pdc.bounced_on = data.date
    pdc.bounce_reason = data.reason
    pdc.status = target.value
    await session.flush()
    return pdc


async def give_back(session: AsyncSession, pdc_id: uuid.UUID, action: state.Action) -> Pdc:
    """Rule 19 (returned to the customer before deposit) and rule 22 (an issued cheque cancelled)."""
    pdc, target = await _move(session, pdc_id, action)
    pdc.status = target.value
    await session.flush()
    return pdc


# --- reading -----------------------------------------------------------------------------------------------


async def _intended(session: AsyncSession, pdc_id: uuid.UUID) -> list[PdcIntendedBill]:
    stmt = (
        select(PdcIntendedBill).where(PdcIntendedBill.pdc_id == pdc_id).order_by(PdcIntendedBill.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def pdc_out(session: AsyncSession, pdc: Pdc) -> PdcOut:
    party = await parties.get_party_row(session, pdc.party_id)
    intended: list[IntendedBillOut] = []
    for item in await _intended(session, pdc.id):
        bill = await accounting.get_bill(session, item.bill_id)
        intended.append(
            IntendedBillOut(
                bill_id=item.bill_id,
                reference=bill.reference if bill else "",
                amount=item.amount,
                amount_open=bill.amount_open if bill else ZERO,
            )
        )
    voucher = await session.get(Document, pdc.voucher_id) if pdc.voucher_id else None
    return PdcOut(
        id=pdc.id,
        business_id=pdc.business_id,
        branch_id=pdc.branch_id,
        direction=PdcDirection(pdc.direction),
        party_id=pdc.party_id,
        party_name_en=party.name_en,
        party_name_ar=party.name_ar,
        cheque_no=pdc.cheque_no,
        bank_name=pdc.bank_name,
        cheque_date=pdc.cheque_date,
        amount=pdc.amount,
        currency=pdc.currency,
        status=PdcStatus(pdc.status),
        deposit_account_id=pdc.deposit_account_id,
        deposited_on=pdc.deposited_on,
        cleared_on=pdc.cleared_on,
        bounced_on=pdc.bounced_on,
        bounce_reason=pdc.bounce_reason,
        voucher_id=pdc.voucher_id,
        voucher_number=voucher.number if voucher else None,
        charge_voucher_id=pdc.charge_voucher_id,
        notes=pdc.notes,
        intended_bills=intended,
    )


async def list_pdcs(
    session: AsyncSession,
    business_id: uuid.UUID,
    *,
    direction: PdcDirection | None = None,
    status: PdcStatus | None = None,
    party_id: uuid.UUID | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    limit: int = 200,
) -> list[PdcOut]:
    """The register by maturity (the calendar): earliest cheque date first."""
    stmt = select(Pdc).where(Pdc.business_id == business_id)
    if direction is not None:
        stmt = stmt.where(Pdc.direction == direction.value)
    if status is not None:
        stmt = stmt.where(Pdc.status == status.value)
    if party_id is not None:
        stmt = stmt.where(Pdc.party_id == party_id)
    if due_from is not None:
        stmt = stmt.where(Pdc.cheque_date >= due_from)
    if due_to is not None:
        stmt = stmt.where(Pdc.cheque_date <= due_to)
    stmt = stmt.order_by(Pdc.cheque_date, Pdc.created_at).limit(limit)
    return [await pdc_out(session, p) for p in (await session.scalars(stmt)).all()]


# --- for other modules: credit (M1.4), statements and ageing -----------------------------------------------


async def pending_amounts(
    session: AsyncSession, business_id: uuid.UUID, party_id: uuid.UUID | None = None
) -> dict[tuple[uuid.UUID, PdcDirection], Decimal]:
    """Per party and direction: cheques not yet cleared (received in hand or deposited, issued not
    cleared)."""
    stmt = (
        select(Pdc.party_id, Pdc.direction, func.sum(Pdc.amount))
        .where(Pdc.business_id == business_id, Pdc.status.in_([s.value for s in state.PENDING]))
        .group_by(Pdc.party_id, Pdc.direction)
    )
    if party_id is not None:
        stmt = stmt.where(Pdc.party_id == party_id)
    return {
        (party, PdcDirection(direction)): total for party, direction, total in await session.execute(stmt)
    }
