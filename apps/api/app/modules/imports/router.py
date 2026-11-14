"""Import endpoints (M1.12): ``import.view`` to read, ``import.create`` to upload and check,
``import.post`` to import — and the permission of what the import creates (``customer.create``,
``product.create``…), so an import never lets anyone do more than the screens. Scoped to the business."""

import uuid
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status

from app.core.authz import PermissionSet
from app.core.deps import PrincipalDep, SettingsDep, TenantSessionDep
from app.core.storage import Storage
from app.modules.identity.authz import get_permissions
from app.modules.imports import kinds, service
from app.modules.imports.schemas import (
    ImportBatchOut,
    ImportCheckIn,
    ImportCheckOut,
    ImportKindOut,
    ImportUploadOut,
)

router = APIRouter(tags=["imports"])
Signed = Annotated[PermissionSet, Depends(get_permissions)]


def get_storage(request: Request) -> Storage:
    storage: Storage = request.app.state.storage
    return storage


StorageDep = Annotated[Storage, Depends(get_storage)]


def _who(perms: PermissionSet, principal: PrincipalDep, business_id: uuid.UUID) -> service.Who:
    return service.Who(
        principal.user_id,
        principal.node_id,
        perms.allows("period.override_lock", business_id),
        lambda code: perms.allows(code, business_id),
    )


def _require_kind(perms: PermissionSet, kind_key: str, business_id: uuid.UUID) -> None:
    for permission in kinds.get(kind_key).permissions:
        perms.require(permission, business_id)


@router.get("/imports/kinds", operation_id="list_import_kinds")
async def list_import_kinds(perms: Signed) -> list[ImportKindOut]:
    perms.require_held("import.view")
    return service.list_kinds()


@router.get("/imports/templates/{kind}", operation_id="download_import_template", response_class=Response)
async def download_import_template(kind: str, perms: Signed, lang: Literal["en", "ar"] = "en") -> Response:
    perms.require_held("import.view")
    data = service.template_csv(kinds.get(kind), lang)
    disposition = f"attachment; filename*=UTF-8''{quote(f'{kind}-template.csv')}"
    return Response(
        content=data, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": disposition}
    )


@router.post("/imports", operation_id="upload_import", status_code=status.HTTP_201_CREATED)
async def upload_import(
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    storage: StorageDep,
    settings: SettingsDep,
    business_id: Annotated[uuid.UUID, Form()],
    kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> ImportUploadOut:
    perms.require("import.create", business_id)
    _require_kind(perms, kind, business_id)
    data = await file.read(settings.max_upload_bytes + 1)
    return await service.upload(
        session,
        storage,
        tenant_id=principal.tenant_id,
        business_id=business_id,
        kind_key=kind,
        filename=file.filename or "file",
        data=data,
        max_bytes=settings.max_upload_bytes,
    )


@router.get("/imports", operation_id="list_imports")
async def list_imports(
    perms: Signed,
    session: TenantSessionDep,
    business_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ImportBatchOut]:
    perms.require_see("import.view", business_id)
    return [ImportBatchOut.model_validate(b) for b in await service.list_batches(session, business_id, limit)]


@router.get("/imports/{batch_id}", operation_id="get_import")
async def get_import(batch_id: uuid.UUID, perms: Signed, session: TenantSessionDep) -> ImportBatchOut:
    batch = await service.get_batch(session, batch_id)
    perms.require("import.view", batch.business_id)
    return ImportBatchOut.model_validate(batch)


@router.post("/imports/{batch_id}/check", operation_id="check_import")
async def check_import(
    batch_id: uuid.UUID,
    body: ImportCheckIn,
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    storage: StorageDep,
) -> ImportCheckOut:
    batch = await service.get_batch(session, batch_id)
    perms.require("import.create", batch.business_id)
    _require_kind(perms, batch.kind, batch.business_id)
    return await service.check(
        session,
        storage,
        batch_id,
        columns=body.mapping,
        options=body.options,
        who=_who(perms, principal, batch.business_id),
    )


@router.post("/imports/{batch_id}/commit", operation_id="commit_import")
async def commit_import(
    batch_id: uuid.UUID,
    perms: Signed,
    session: TenantSessionDep,
    principal: PrincipalDep,
    storage: StorageDep,
) -> ImportBatchOut:
    batch = await service.get_batch(session, batch_id)
    perms.require("import.post", batch.business_id)
    _require_kind(perms, batch.kind, batch.business_id)
    done = await service.commit(session, storage, batch_id, who=_who(perms, principal, batch.business_id))
    return ImportBatchOut.model_validate(done)
