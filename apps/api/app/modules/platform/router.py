"""Audit trail, attachments, settings and industry-pack endpoints (M0.4)."""

import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, SettingsDep, TenantSessionDep
from app.core.platform_models import AuditLog
from app.core.storage import Storage
from app.modules.identity.authz import get_permissions, require
from app.modules.platform import attachments, packs
from app.modules.platform import settings as settings_service
from app.modules.tenancy import service as tenancy

router = APIRouter()
Perms = Annotated[PermissionSet, Depends(get_permissions)]


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


StorageDep = Annotated[Storage, Depends(get_storage)]


# --- Audit ---


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    at: datetime
    actor_user_id: uuid.UUID | None
    via_ai: bool
    on_behalf_of_user_id: uuid.UUID | None
    client: str | None
    request_id: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID
    changes: dict[str, Any] | None
    snapshot: dict[str, Any] | None
    reason: str | None


@router.get("/audit", tags=["audit"], operation_id="list_audit")
async def list_audit(
    perms: Annotated[PermissionSet, require("audit.view")],
    session: TenantSessionDep,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditOut]:
    perms.require("audit.view")
    stmt = select(AuditLog).order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    return [AuditOut.model_validate(r) for r in await session.scalars(stmt)]


# --- Attachments ---


@router.post(
    "/attachments",
    tags=["attachments"],
    operation_id="upload_attachment",
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    perms: Perms,
    session: TenantSessionDep,
    principal: PrincipalDep,
    storage: StorageDep,
    settings: SettingsDep,
    entity_type: Annotated[str, Form()],
    entity_id: Annotated[uuid.UUID, Form()],
    file: Annotated[UploadFile, File()],
) -> attachments.AttachmentOut:
    attachments.check_entity_type(entity_type)
    perms.require_held(f"{entity_type}.edit")
    data = await file.read(settings.max_upload_bytes + 1)
    row = await attachments.upload(
        session,
        storage,
        tenant_id=principal.tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        filename=file.filename or "file",
        content_type=file.content_type or "application/octet-stream",
        data=data,
        max_bytes=settings.max_upload_bytes,
    )
    return attachments.AttachmentOut.model_validate(row)


@router.get("/attachments", tags=["attachments"], operation_id="list_attachments")
async def list_attachments(
    perms: Perms, session: TenantSessionDep, entity_type: str, entity_id: uuid.UUID
) -> list[attachments.AttachmentOut]:
    attachments.check_entity_type(entity_type)
    perms.require_held(f"{entity_type}.view")
    rows = await attachments.list_for(session, entity_type, entity_id)
    return [attachments.AttachmentOut.model_validate(r) for r in rows]


@router.get(
    "/attachments/{attachment_id}/content",
    tags=["attachments"],
    operation_id="download_attachment",
    response_class=Response,
)
async def download_attachment(
    attachment_id: uuid.UUID, perms: Perms, session: TenantSessionDep, storage: StorageDep
) -> Response:
    row = await attachments.get(session, attachment_id)
    perms.require_held(f"{row.entity_type}.view")
    data = await storage.get(row.storage_key)
    disposition = f"attachment; filename*=UTF-8''{quote(row.filename)}"
    return Response(content=data, media_type=row.content_type, headers={"Content-Disposition": disposition})


@router.delete(
    "/attachments/{attachment_id}",
    tags=["attachments"],
    operation_id="delete_attachment",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(attachment_id: uuid.UUID, perms: Perms, session: TenantSessionDep) -> None:
    row = await attachments.get(session, attachment_id)
    perms.require_held(f"{row.entity_type}.edit")
    await attachments.remove(session, row)


# --- Settings ---


@router.get("/settings", tags=["settings"], operation_id="list_settings")
async def list_settings(
    perms: Perms,
    session: TenantSessionDep,
    principal: PrincipalDep,
    business_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
) -> list[settings_service.EffectiveSetting]:
    if branch_id is not None:
        branch = await tenancy.get_branch(session, branch_id)
        business_id = branch.business_id
    if business_id is not None:
        await tenancy.get_business(session, business_id)
    values = await settings_service.effective(
        session, user_id=principal.user_id, business_id=business_id, branch_id=branch_id
    )
    return list(values.values())


@router.put("/settings/{key}", tags=["settings"], operation_id="put_setting")
async def put_setting(
    key: str,
    body: settings_service.SettingIn,
    perms: Perms,
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> settings_service.EffectiveSetting:
    settings_service.definition(key)
    scope_id = principal.user_id if body.scope == "user" and body.scope_id is None else body.scope_id
    business_of: uuid.UUID | None = None
    if body.scope == "business" and scope_id is not None:
        await tenancy.get_business(session, scope_id)
    if body.scope == "branch" and scope_id is not None:
        business_of = (await tenancy.get_branch(session, scope_id)).business_id
    settings_service.check_can_set(perms, principal, body.scope, scope_id, business_of)
    await settings_service.put_value(
        session, tenant_id=principal.tenant_id, key=key, scope=body.scope, scope_id=scope_id, value=body.value
    )
    values = await settings_service.effective(
        session,
        user_id=principal.user_id,
        business_id=scope_id if body.scope == "business" else business_of,
        branch_id=scope_id if body.scope == "branch" else None,
    )
    return values[key]


# --- Industry packs ---


class ApplyPackIn(BaseModel):
    code: str


@router.get("/industry-packs", tags=["industry packs"], operation_id="list_industry_packs")
async def list_industry_packs(
    perms: Annotated[PermissionSet, require("business.view")],
) -> list[packs.PackSummary]:
    return packs.summaries()


@router.get(
    "/businesses/{business_id}/industry-packs", tags=["industry packs"], operation_id="list_applied_packs"
)
async def list_applied_packs(
    business_id: uuid.UUID,
    perms: Annotated[PermissionSet, require("business.view")],
    session: TenantSessionDep,
) -> list[packs.ApplicationOut]:
    await tenancy.get_business(session, business_id)
    perms.require_see("business.view", business_id)
    return await packs.applications(session, business_id)


@router.post(
    "/businesses/{business_id}/industry-packs", tags=["industry packs"], operation_id="apply_industry_pack"
)
async def apply_industry_pack(
    business_id: uuid.UUID,
    body: ApplyPackIn,
    perms: Annotated[PermissionSet, require("business.edit")],
    session: TenantSessionDep,
    principal: PrincipalDep,
) -> packs.ApplicationOut:
    await tenancy.get_business(session, business_id)
    perms.require("business.edit", business_id)
    return await packs.apply(session, tenant_id=principal.tenant_id, business_id=business_id, code=body.code)
