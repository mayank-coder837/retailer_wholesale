"""Files on any record: bills, cheques, delivery photos (concept v2 → Platform features)."""

import hashlib
import uuid
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import jobs
from app.core.errors import NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.core.permissions import RESOURCES
from app.core.platform_models import Attachment, OutboxEvent
from app.core.storage import Storage

ALLOWED_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/heic",
        "text/plain",
        "text/csv",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
)
DELETE_FILE_EVENT = "attachment.delete_file"


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    filename: str
    content_type: str
    size: int
    sha256: str
    created_at: datetime


class UnsupportedFileError(UnprocessableError):
    code = "unsupported_file"
    title = "This file type cannot be attached"


class FileTooLargeError(UnprocessableError):
    code = "file_too_large"
    title = "The file is too large"


class UnknownEntityTypeError(UnprocessableError):
    code = "unknown_entity_type"
    title = "Files cannot be attached to this kind of record"


def check_entity_type(entity_type: str) -> None:
    if entity_type not in RESOURCES:
        raise UnknownEntityTypeError(entity_type=entity_type)


async def upload(
    session: AsyncSession,
    storage: Storage,
    *,
    tenant_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    filename: str,
    content_type: str,
    data: bytes,
    max_bytes: int,
) -> Attachment:
    check_entity_type(entity_type)
    if content_type not in ALLOWED_TYPES:
        raise UnsupportedFileError(content_type=content_type)
    if len(data) > max_bytes:
        raise FileTooLargeError(max_bytes=max_bytes)
    attachment_id = new_id()
    key = f"{tenant_id}/{attachment_id}"
    # Written before the row commits: a rolled-back upload leaves an orphan file, never a row without a file.
    await storage.put(key, data)
    row = Attachment(
        id=attachment_id,
        tenant_id=tenant_id,
        entity_type=entity_type,
        entity_id=entity_id,
        filename=filename[:255] or "file",
        content_type=content_type,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=key,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get(session: AsyncSession, attachment_id: uuid.UUID) -> Attachment:
    row = await session.get(Attachment, attachment_id)
    if row is None:
        raise NotFoundError("Attachment not found.")
    return row


async def list_for(session: AsyncSession, entity_type: str, entity_id: uuid.UUID) -> Sequence[Attachment]:
    stmt = (
        select(Attachment)
        .where(Attachment.entity_type == entity_type, Attachment.entity_id == entity_id)
        .order_by(Attachment.created_at)
    )
    return (await session.scalars(stmt)).all()


async def remove(session: AsyncSession, row: Attachment) -> None:
    """Deletes the record now; the file is removed by a job once the deletion has committed."""
    await jobs.publish(
        session,
        tenant_id=row.tenant_id,
        event_type=DELETE_FILE_EVENT,
        payload={"storage_key": row.storage_key},
        aggregate_type="attachment",
        aggregate_id=row.id,
    )
    await session.delete(row)
    await session.flush()


_storage: dict[str, Storage] = {}


def configure(storage: Storage) -> None:
    """Called at app start-up: the job handler below deletes files from this storage."""
    _storage["default"] = storage


@jobs.handler(DELETE_FILE_EVENT)
async def delete_file(_session: AsyncSession, event: OutboxEvent) -> None:
    await _storage["default"].delete(event.payload["storage_key"])
