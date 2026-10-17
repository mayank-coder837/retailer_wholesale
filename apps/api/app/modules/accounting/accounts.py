"""The chart of accounts and account roles (docs/milestones/m1.5-accounting.md → Chart of accounts).

- Every business gets the default chart (``chart.py``) when it is created.
- A child takes its group's nature; only ledgers take postings; a ledger with postings keeps its code and
  type.
- Nothing is deleted: an account is archived, and only when no role points at it.
- Roles point at ledgers of the right type; the inventory account is never posted by hand (posting rule 14).
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.modules.accounting import ledger
from app.modules.accounting.chart import chart_rows
from app.modules.accounting.models import TYPE_NATURES, Account, AccountRole, AccountType, Nature, Role
from app.modules.accounting.schemas import AccountIn, AccountOut, AccountPatch, AccountRoleOut

# Roles whose account must be of one type, so control accounts and VAT stay where reports expect them.
ROLE_TYPES: dict[Role, AccountType] = {
    Role.AR_CONTROL: AccountType.RECEIVABLE,
    Role.AP_CONTROL: AccountType.PAYABLE,
    Role.INVENTORY: AccountType.STOCK,
    Role.VAT_INPUT: AccountType.TAX,
    Role.VAT_OUTPUT: AccountType.TAX,
}


class AccountNotFoundError(NotFoundError):
    code = "account_not_found"
    title = "There is no such account"


class InvalidAccountError(UnprocessableError):
    code = "invalid_account"
    title = "The account details are not valid"


class DuplicateAccountError(ConflictError):
    code = "duplicate_account"
    title = "An account with this code already exists"


async def install_chart(session: AsyncSession, tenant_id: uuid.UUID, business_id: uuid.UUID) -> None:
    """The default chart and its roles for a new business (seed data, written by the system)."""
    if await session.scalar(select(Account.id).where(Account.business_id == business_id).limit(1)):
        return
    accounts, roles = chart_rows(tenant_id, business_id)
    await session.execute(insert(Account), accounts)
    await session.execute(insert(AccountRole), roles)


async def get_account(session: AsyncSession, account_id: uuid.UUID) -> Account:
    account = await session.get(Account, account_id)
    if account is None:
        raise AccountNotFoundError()
    return account


async def accounts_by_id(session: AsyncSession, business_id: uuid.UUID) -> dict[uuid.UUID, Account]:
    rows = await session.scalars(select(Account).where(Account.business_id == business_id))
    return {a.id: a for a in rows}


async def role_map(session: AsyncSession, business_id: uuid.UUID) -> dict[Role, uuid.UUID]:
    rows = await session.execute(
        select(AccountRole.role, AccountRole.account_id).where(AccountRole.business_id == business_id)
    )
    return {Role(role): account_id for role, account_id in rows}


async def account_for(session: AsyncSession, business_id: uuid.UUID, role: Role) -> uuid.UUID:
    found = (await role_map(session, business_id)).get(role)
    if found is None:
        raise InvalidAccountError(f"No account has the role {role.value}.", role=role.value)
    return found


def _out(account: Account, roles: list[Role], balance: Decimal) -> AccountOut:
    return AccountOut.model_validate(account).model_copy(update={"roles": roles, "balance": balance})


async def list_accounts(
    session: AsyncSession, business_id: uuid.UUID, *, include_archived: bool = False
) -> list[AccountOut]:
    """Tree order (each group followed by its children, by code), with balances; a group shows its total."""
    accounts = await accounts_by_id(session, business_id)
    balances = await ledger.account_balances(session, business_id)
    roles: dict[uuid.UUID, list[Role]] = {}
    for role, account_id in sorted((await role_map(session, business_id)).items()):
        roles.setdefault(account_id, []).append(role)
    children: dict[uuid.UUID | None, list[Account]] = {}
    for a in accounts.values():
        children.setdefault(a.parent_id, []).append(a)

    totals: dict[uuid.UUID, Decimal] = {}

    def total(a: Account) -> Decimal:
        value = balances.get(a.id, Decimal(0)) + sum((total(c) for c in children.get(a.id, [])), Decimal(0))
        totals[a.id] = value
        return value

    out: list[AccountOut] = []

    def walk(parent: uuid.UUID | None) -> None:
        for a in sorted(children.get(parent, []), key=lambda x: x.code):
            if include_archived or a.archived_at is None:
                out.append(_out(a, roles.get(a.id, []), totals[a.id]))
            walk(a.id)

    for root in children.get(None, []):
        total(root)
    walk(None)
    return out


async def get_account_out(session: AsyncSession, account_id: uuid.UUID) -> AccountOut:
    account = await get_account(session, account_id)
    for item in await list_accounts(session, account.business_id, include_archived=True):
        if item.id == account.id:
            return item
    raise AccountNotFoundError()


async def _group(session: AsyncSession, business_id: uuid.UUID, group_id: uuid.UUID) -> Account:
    group = await session.get(Account, group_id)
    if group is None or group.business_id != business_id:
        raise InvalidAccountError("There is no such group in this business.", field="parent_id")
    if not group.is_group or group.archived_at is not None:
        raise InvalidAccountError("Accounts sit in an active group.", field="parent_id")
    return group


def _check_type(account_type: AccountType, nature: Nature) -> None:
    if nature not in TYPE_NATURES[account_type]:
        raise InvalidAccountError(
            f"A {account_type.value} account cannot sit under {nature.value}.", field="account_type"
        )


async def _flush(session: AsyncSession) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateAccountError() from exc


async def create_account(session: AsyncSession, tenant_id: uuid.UUID, data: AccountIn) -> AccountOut:
    group = await _group(session, data.business_id, data.parent_id)
    nature = Nature(group.nature)
    if data.account_type is not None:
        _check_type(data.account_type, nature)
    account = Account(
        tenant_id=tenant_id,
        business_id=data.business_id,
        code=data.code,
        name_en=data.name_en,
        name_ar=data.name_ar,
        parent_id=group.id,
        is_group=data.is_group,
        nature=nature.value,
        account_type=data.account_type.value if data.account_type else None,
        allow_manual_posting=data.allow_manual_posting,
    )
    session.add(account)
    await _flush(session)
    return await get_account_out(session, account.id)


async def _roles_of(session: AsyncSession, account_id: uuid.UUID) -> list[Role]:
    rows = await session.scalars(select(AccountRole.role).where(AccountRole.account_id == account_id))
    return [Role(r) for r in rows]


async def update_account(session: AsyncSession, account_id: uuid.UUID, data: AccountPatch) -> AccountOut:
    account = await get_account(session, account_id)
    roles = await _roles_of(session, account.id)
    posted = await ledger.has_postings(session, account.id)
    changes = data.model_dump(exclude_unset=True)

    if "code" in changes and data.code != account.code:
        if posted:
            raise InvalidAccountError("An account with postings keeps its code.", field="code")
        account.code = data.code  # type: ignore[assignment]
    for name in ("name_en", "name_ar"):
        if changes.get(name) is not None:
            setattr(account, name, changes[name])
    if changes.get("parent_id") is not None and data.parent_id != account.parent_id:
        await _move(session, account, data.parent_id)  # type: ignore[arg-type]
    if changes.get("account_type") is not None and data.account_type != account.account_type:
        if account.is_group:
            raise InvalidAccountError("A group has no account type.", field="account_type")
        if posted:
            raise InvalidAccountError("An account with postings keeps its type.", field="account_type")
        assert data.account_type is not None
        _check_type(data.account_type, Nature(account.nature))
        for role in roles:
            if role in ROLE_TYPES and ROLE_TYPES[role] is not data.account_type:
                raise InvalidAccountError(
                    f"The {role.value} account must stay {ROLE_TYPES[role].value}.", field="account_type"
                )
        account.account_type = data.account_type.value
    if changes.get("allow_manual_posting") is not None:
        if data.allow_manual_posting and Role.INVENTORY in roles:
            raise InvalidAccountError(
                "The inventory account is posted by stock documents only.", field="allow_manual_posting"
            )
        account.allow_manual_posting = bool(data.allow_manual_posting)
    if changes.get("archived") is not None:
        await _archive(session, account, archived=bool(data.archived), roles=roles)
    await _flush(session)
    return await get_account_out(session, account.id)


async def _move(session: AsyncSession, account: Account, parent_id: uuid.UUID) -> None:
    group = await _group(session, account.business_id, parent_id)
    if group.nature != account.nature:
        raise InvalidAccountError("An account moves only within its nature.", field="parent_id")
    walker: Account | None = group
    while walker is not None:
        if walker.id == account.id:
            raise InvalidAccountError("A group cannot sit inside itself.", field="parent_id")
        walker = await session.get(Account, walker.parent_id) if walker.parent_id else None
    account.parent_id = group.id


async def _archive(session: AsyncSession, account: Account, *, archived: bool, roles: list[Role]) -> None:
    if not archived:
        account.archived_at = None
        return
    if account.archived_at is not None:
        return
    if roles:
        raise InvalidAccountError(
            "Point its roles at another account first.", roles=[r.value for r in roles], field="archived"
        )
    active_children = await session.scalar(
        select(Account.id).where(Account.parent_id == account.id, Account.archived_at.is_(None)).limit(1)
    )
    if active_children is not None:
        raise InvalidAccountError("Archive or move the accounts inside the group first.", field="archived")
    account.archived_at = datetime.now(UTC)


async def list_roles(session: AsyncSession, business_id: uuid.UUID) -> list[AccountRoleOut]:
    return [
        AccountRoleOut(role=r, account_id=a)
        for r, a in sorted((await role_map(session, business_id)).items())
    ]


async def set_role(
    session: AsyncSession, business_id: uuid.UUID, role: Role, account_id: uuid.UUID
) -> list[AccountRoleOut]:
    """Later postings for ``role`` go to ``account_id``; earlier ones stay where they are (audited)."""
    account = await get_account(session, account_id)
    if account.business_id != business_id or account.is_group or account.archived_at is not None:
        raise InvalidAccountError("A role points at an active ledger of this business.", field="account_id")
    expected = ROLE_TYPES.get(role)
    if expected is not None and account.account_type != expected.value:
        raise InvalidAccountError(f"The {role.value} account must be {expected.value}.", field="account_id")
    row = (
        await session.scalars(
            select(AccountRole).where(AccountRole.business_id == business_id, AccountRole.role == role.value)
        )
    ).first()
    if row is None:
        session.add(
            AccountRole(
                tenant_id=account.tenant_id, business_id=business_id, role=role.value, account_id=account.id
            )
        )
    else:
        row.account_id = account.id
    if role is Role.INVENTORY:
        account.allow_manual_posting = False
    await session.flush()
    return await list_roles(session, business_id)
