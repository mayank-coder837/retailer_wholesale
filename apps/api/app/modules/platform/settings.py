"""Settings store: values per tenant, business, branch or user; effective value by precedence."""

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PermissionDeniedError, PermissionSet
from app.core.errors import NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.core.platform_models import Setting
from app.core.security import Principal
from app.core.settings_registry import SETTINGS, SettingDef


class InvalidSettingError(UnprocessableError):
    code = "invalid_setting"
    title = "This value is not valid for the setting"


class SettingIn(BaseModel):
    scope: str = Field(pattern="^(tenant|business|branch|user)$")
    scope_id: uuid.UUID | None = Field(
        default=None, description="Business or branch id; omit for tenant/user"
    )
    value: Any


class EffectiveSetting(BaseModel):
    key: str
    value: Any
    source: str = Field(description="default, tenant, business, branch or user")
    scopes: list[str]
    description: str


def definition(key: str) -> SettingDef:
    found = SETTINGS.get(key)
    if found is None:
        raise NotFoundError("Unknown setting.", key=key)
    return found


async def effective(
    session: AsyncSession,
    *,
    user_id: uuid.UUID | None = None,
    business_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
) -> dict[str, EffectiveSetting]:
    targets = {"tenant": None, "business": business_id, "branch": branch_id, "user": user_id}
    rows = (await session.scalars(select(Setting))).all()
    found = {(r.scope, r.scope_id, r.key): r.value for r in rows}
    out: dict[str, EffectiveSetting] = {}
    for key, d in SETTINGS.items():
        value, source = d.default, "default"
        for scope in ("tenant", "business", "branch", "user"):  # later scopes override earlier ones
            if scope in d.scopes and (scope == "tenant" or targets[scope] is not None):
                hit = (scope, targets[scope], key)
                if hit in found:
                    value, source = found[hit], scope
        out[key] = EffectiveSetting(
            key=key, value=value, source=source, scopes=list(d.scopes), description=d.description
        )
    return out


async def put_value(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key: str,
    scope: str,
    scope_id: uuid.UUID | None,
    value: Any,
) -> None:
    """Validates and upserts one value. Callers check permissions first (see ``check_can_set``)."""
    d = definition(key)
    if scope not in d.scopes:
        raise InvalidSettingError(f"{key} cannot be set per {scope}.", allowed_scopes=list(d.scopes))
    if (scope == "tenant") != (scope_id is None):
        raise InvalidSettingError(
            "scope_id is required for business, branch and user settings, and only for them."
        )
    try:
        value = d.validate(value)
    except ValueError as exc:
        raise InvalidSettingError(str(exc), key=key) from exc
    row = (
        await session.scalars(
            select(Setting).where(Setting.scope == scope, Setting.scope_id == scope_id, Setting.key == key)
            if scope_id is not None
            else select(Setting).where(Setting.scope == scope, Setting.scope_id.is_(None), Setting.key == key)
        )
    ).first()
    if row is None:
        session.add(
            Setting(id=new_id(), tenant_id=tenant_id, scope=scope, scope_id=scope_id, key=key, value=value)
        )
    else:
        row.value = value
    await session.flush()


def check_can_set(
    perms: PermissionSet,
    principal: Principal,
    scope: str,
    scope_id: uuid.UUID | None,
    business_of: uuid.UUID | None,
) -> None:
    """User: only your own. Tenant: ``tenant.edit``. Business or branch: ``business.edit`` in that scope."""
    if scope == "user":
        if scope_id != principal.user_id:
            raise PermissionDeniedError("You can only change your own preferences.")
    elif scope == "tenant":
        perms.require("tenant.edit")
    elif scope == "business":
        perms.require("business.edit", scope_id)
    else:
        perms.require("business.edit", business_of, scope_id)
