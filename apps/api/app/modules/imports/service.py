"""The life of an import batch (M1.12): upload → check → import. Other modules never call this.

- **Upload** stores the file and lists its columns; nothing else is written.
- **Check** reads every row against the chosen columns and reports new / already there / wrong; nothing is
  written to the books. It can be repeated until the file is clean.
- **Import** reads and checks the file again and, if every row is fine, writes all of it in the request's one
  transaction — any problem and nothing is imported. Importing a batch twice returns the first result; so does
  uploading the same file again (it is refused).
"""

import asyncio
import csv
import hashlib
import io
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.core.ids import new_id
from app.core.storage import Storage
from app.modules.documents import service as documents
from app.modules.imports import kinds, mapping
from app.modules.imports.kinds.base import Context, Kind, Option, RowResult
from app.modules.imports.models import BatchStatus, ImportBatch
from app.modules.imports.readers import Table, read_table
from app.modules.imports.schemas import (
    ImportBatchOut,
    ImportCheckOut,
    ImportFieldOut,
    ImportKindOut,
    ImportNoteOut,
    ImportOptionOut,
    ImportRowOut,
    ImportUploadOut,
)
from app.modules.imports.values import CellError, clean_text, parse_date
from app.modules.tenancy import service as tenancy

LISTED_ERRORS = 500
LISTED_OTHERS = 100
SAMPLE_ROWS = 5


class BatchNotFoundError(NotFoundError):
    code = "import_not_found"
    title = "There is no such import"


class ImportClosedError(ConflictError):
    code = "import_closed"
    title = "This import is finished; upload the file again to start another"


class NotCheckedError(ConflictError):
    code = "import_not_checked"
    title = "Check the file before importing it"


class AlreadyImportedError(ConflictError):
    code = "already_imported"
    title = "This file was already imported"


class ImportHasErrorsError(UnprocessableError):
    code = "import_has_errors"
    title = "The file has rows with problems; nothing was imported"


class InvalidOptionError(UnprocessableError):
    code = "invalid_import_option"
    title = "That choice is not valid for this import"


class OpeningAlreadyImportedError(ConflictError):
    code = "opening_already_imported"
    title = "An opening trial balance is already imported: undo it first"


class NotUndoableError(UnprocessableError):
    code = "not_undoable"
    title = "This kind of import cannot be undone"


class NotCommittedError(ConflictError):
    code = "import_not_committed"
    title = "Only an import that was done can be undone"


class ImportFileTooLargeError(UnprocessableError):
    code = "file_too_large"
    title = "The file is too large"


def kind_out(kind: Kind) -> ImportKindOut:
    return ImportKindOut(
        key=kind.key,
        label_en=kind.label_en,
        label_ar=kind.label_ar,
        description_en=kind.description_en,
        description_ar=kind.description_ar,
        order=kind.order,
        fields=[
            ImportFieldOut(
                key=f.key,
                label_en=f.label_en,
                label_ar=f.label_ar,
                required=f.required,
                example=f.example,
                hint_en=f.hint_en,
                hint_ar=f.hint_ar,
            )
            for f in kind.fields
        ],
        options=[ImportOptionOut(**vars(o)) for o in kind.options],
        permissions=list(kind.permissions),
    )


def list_kinds() -> list[ImportKindOut]:
    return [kind_out(k) for k in sorted(kinds.KINDS.values(), key=lambda k: k.order)]


async def get_batch(session: AsyncSession, batch_id: uuid.UUID, *, lock: bool = False) -> ImportBatch:
    stmt = select(ImportBatch).where(ImportBatch.id == batch_id).execution_options(populate_existing=True)
    if lock:
        stmt = stmt.with_for_update()
    batch = (await session.scalars(stmt)).first()
    if batch is None:
        raise BatchNotFoundError()
    return batch


async def list_batches(session: AsyncSession, business_id: uuid.UUID, limit: int = 50) -> list[ImportBatch]:
    stmt = (
        select(ImportBatch)
        .where(ImportBatch.business_id == business_id)
        .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
        .limit(limit)
    )
    return list((await session.scalars(stmt)).all())


async def _imported_before(session: AsyncSession, batch: ImportBatch) -> uuid.UUID | None:
    return await session.scalar(
        select(ImportBatch.id).where(
            ImportBatch.business_id == batch.business_id,
            ImportBatch.kind == batch.kind,
            ImportBatch.sha256 == batch.sha256,
            ImportBatch.status == BatchStatus.COMMITTED.value,
            ImportBatch.id != batch.id,
        )
    )


def _text(cell: Any) -> str:
    return clean_text(cell) or ""


async def upload(
    session: AsyncSession,
    storage: Storage,
    *,
    tenant_id: uuid.UUID,
    business_id: uuid.UUID,
    kind_key: str,
    filename: str,
    data: bytes,
    max_bytes: int,
) -> ImportUploadOut:
    kind = kinds.get(kind_key)
    await tenancy.get_business(session, business_id)
    if len(data) > max_bytes:
        raise ImportFileTooLargeError(max_bytes=max_bytes)
    table = await asyncio.to_thread(read_table, filename, data, kind.key)  # slow work: off the event loop
    batch_id = new_id()
    key = f"{tenant_id}/imports/{batch_id}"
    # Written before the row commits: a rolled-back upload leaves an orphan file, never a row without a file.
    await storage.put(key, data)
    batch = ImportBatch(
        id=batch_id,
        tenant_id=tenant_id,
        business_id=business_id,
        kind=kind.key,
        status=BatchStatus.UPLOADED.value,
        filename=(filename.replace("\\", "/").rsplit("/", 1)[-1] or "file")[:255],
        file_key=key,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        headers=table.headers,
        row_count=len(table.rows),
    )
    session.add(batch)
    await session.flush()
    await session.refresh(batch)
    return ImportUploadOut(
        **ImportBatchOut.model_validate(batch).model_dump(),
        suggested_mapping=mapping.suggest(table.headers, kind.fields, canonical=table.canonical),
        notes=[ImportNoteOut(code=n.code, message=n.message, count=n.count) for n in table.notes],
        sample=[[_text(c) for c in row] for row in table.rows[:SAMPLE_ROWS]],
        duplicate_of=await _imported_before(session, batch),
    )


async def _branch(session: AsyncSession, business_id: uuid.UUID, value: str) -> str:
    """The branch an opening voucher belongs to: the one chosen, else the head office."""
    branches = await tenancy.list_branches(session, business_id)
    if value:
        try:
            wanted = uuid.UUID(value)
        except ValueError as exc:
            raise InvalidOptionError("Choose one of this business's branches.", option="branch") from exc
        if all(b.id != wanted for b in branches):
            raise InvalidOptionError("That branch belongs to another business.", option="branch")
        return str(wanted)
    head = next((b for b in branches if b.is_head_office), None) or next(iter(branches), None)
    if head is None:
        raise InvalidOptionError("The business has no branch.", option="branch")
    return str(head.id)


async def _options(
    session: AsyncSession, kind: Kind, given: dict[str, str], business_id: uuid.UUID
) -> dict[str, str]:
    """The file-wide choices, each checked against what the kind allows; missing ones take their default."""
    known: dict[str, Option] = {o.key: o for o in kind.options}
    for key in given:
        if key not in known:
            raise InvalidOptionError(f"There is no choice '{key}' in this import.", option=key)
    chosen: dict[str, str] = {}
    for option in kind.options:
        value = (given.get(option.key) or option.default).strip()
        if option.kind == "choice" and value not in option.values:
            raise InvalidOptionError(
                f"'{value}' is not one of: {', '.join(option.values)}.", option=option.key
            )
        if option.kind == "date":
            try:
                day: date | None = parse_date(value)
            except CellError as exc:
                raise InvalidOptionError(str(exc), option=option.key) from exc
            if day is None:
                raise InvalidOptionError("Choose a date.", option=option.key)
            value = day.isoformat()
        if option.kind == "branch":
            value = await _branch(session, business_id, value)
        chosen[option.key] = value
    return chosen


class Who:
    """The signed-in person an import acts for."""

    def __init__(
        self,
        user_id: uuid.UUID | None,
        node_id: uuid.UUID | None,
        may_override_lock: bool,
        allows: Callable[[str], bool],
    ) -> None:
        self.user_id = user_id
        self.node_id = node_id
        self.may_override_lock = may_override_lock
        self.allows = allows


async def _context(session: AsyncSession, batch: ImportBatch, options: dict[str, str], who: Who) -> Context:
    business = await tenancy.get_business(session, batch.business_id)
    return Context(
        session=session,
        tenant_id=batch.tenant_id,
        business_id=batch.business_id,
        branch_id=uuid.UUID(options["branch"]) if "branch" in options else None,
        user_id=who.user_id,
        node_id=who.node_id,
        options=options,
        may_override_lock=who.may_override_lock,
        allows=who.allows,
        batch_id=batch.id,
        currency=business.base_currency,
        filename=batch.filename,
    )


async def _read(storage: Storage, batch: ImportBatch) -> Table:
    return await asyncio.to_thread(read_table, batch.filename, await storage.get(batch.file_key), batch.kind)


async def _ensure_single(session: AsyncSession, batch: ImportBatch, kind: Kind) -> None:
    """An opening trial balance is imported once per business; Undo makes room for another."""
    if not kind.once_per_business:
        return
    # Two imports of the same kind committing at once must not both pass: one waits for the other.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"import:{batch.business_id}:{batch.kind}"},
    )
    other = await session.scalar(
        select(ImportBatch.id).where(
            ImportBatch.business_id == batch.business_id,
            ImportBatch.kind == batch.kind,
            ImportBatch.status == BatchStatus.COMMITTED.value,
            ImportBatch.id != batch.id,
        )
    )
    if other is not None:
        raise OpeningAlreadyImportedError(imported_batch=str(other))


async def _results(
    session: AsyncSession, storage: Storage, batch: ImportBatch, who: Who
) -> tuple[Kind, Context, list[RowResult]]:
    kind = kinds.get(batch.kind)
    table = await _read(storage, batch)
    chosen = mapping.check_mapping(batch.mapping, kind.fields, len(table.headers))
    ctx = await _context(session, batch, await _options(session, kind, batch.options, batch.business_id), who)
    return kind, ctx, await kind.check(ctx, mapping.rows_of(table, chosen))


def _count(results: list[RowResult]) -> dict[str, int]:
    return {
        "total": len(results),
        "new": sum(r.status == "new" for r in results),
        "exists": sum(r.status == "exists" for r in results),
        "skipped": sum(r.status == "skipped" for r in results),
        "errors": sum(r.status == "error" for r in results),
    }


def _listing(results: list[RowResult]) -> tuple[list[ImportRowOut], bool]:
    bad = [r for r in results if r.status == "error"]
    rest = [r for r in results if r.status != "error"]
    shown = bad[:LISTED_ERRORS] + rest[:LISTED_OTHERS]
    rows = [
        ImportRowOut(
            number=r.number,
            status=r.status,
            label=r.label,
            problems=r.problems,
            note=r.note,
            note_code=r.note_code,
        )
        for r in shown
    ]
    return rows, len(shown) < len(results)


async def check(
    session: AsyncSession,
    storage: Storage,
    batch_id: uuid.UUID,
    *,
    columns: mapping.Mapping,
    options: dict[str, str],
    who: Who,
) -> ImportCheckOut:
    batch = await get_batch(session, batch_id, lock=True)
    if batch.status not in (BatchStatus.UPLOADED.value, BatchStatus.CHECKED.value):
        raise ImportClosedError(batch_status=batch.status)
    kind = kinds.get(batch.kind)
    await _ensure_single(session, batch, kind)
    table = await _read(storage, batch)
    batch.mapping = mapping.check_mapping(columns, kind.fields, len(table.headers))
    batch.options = await _options(session, kind, options, batch.business_id)
    ctx = await _context(session, batch, batch.options, who)
    results = await kind.check(ctx, mapping.rows_of(table, batch.mapping))
    counts = _count(results)
    summary = await kind.summary(ctx, results)
    batch.counts = {**counts, "summary": summary}
    batch.status = BatchStatus.CHECKED.value
    await session.flush()
    rows, truncated = _listing(results)
    return ImportCheckOut(batch_id=batch.id, rows=rows, truncated=truncated, summary=summary, **counts)


async def commit(session: AsyncSession, storage: Storage, batch_id: uuid.UUID, *, who: Who) -> ImportBatch:
    batch = await get_batch(session, batch_id, lock=True)
    if batch.status == BatchStatus.COMMITTED.value:
        return batch  # importing twice is importing once
    if batch.status != BatchStatus.CHECKED.value:
        raise NotCheckedError(batch_status=batch.status)
    earlier = await _imported_before(session, batch)
    if earlier is not None:
        raise AlreadyImportedError(duplicate_of=str(earlier))
    await _ensure_single(session, batch, kinds.get(batch.kind))
    audit.use_client(session, "import")
    kind, ctx, results = await _results(session, storage, batch, who)
    counts = _count(results)
    if counts["errors"]:
        raise ImportHasErrorsError(errors=counts["errors"], total=counts["total"])
    applied = await kind.apply(ctx, results)
    batch.counts = {**counts, "summary": await kind.summary(ctx, results)}
    batch.result = {"counts": applied, "rows": counts, **ctx.record}
    batch.status = BatchStatus.COMMITTED.value
    batch.committed_at = datetime.now(UTC)
    await session.flush()
    return batch


async def undo(session: AsyncSession, batch_id: uuid.UUID, *, who: Who) -> ImportBatch:
    """Cancels the vouchers an opening import posted (only while their periods are open), so the same file
    can be imported again. All or nothing. A payment already allocated to an opening bill is kept: it becomes
    an advance on that payment, as when any invoice is cancelled."""
    batch = await get_batch(session, batch_id, lock=True)
    if not kinds.get(batch.kind).undoable:
        raise NotUndoableError(kind=batch.kind)
    if batch.status != BatchStatus.COMMITTED.value:
        raise NotCommittedError(batch_status=batch.status)
    audit.use_client(session, "import")
    for document_id in reversed(batch.result.get("documents", [])):
        await documents.cancel(
            session,
            uuid.UUID(document_id),
            user_id=who.user_id,
            reason=f"Import of {batch.filename} undone",
        )
    batch.status = BatchStatus.UNDONE.value
    await session.flush()
    return batch


def template_csv(kind: Kind, lang: str) -> bytes:
    """Headers and one example row, as a CSV Excel opens correctly (UTF-8 with a BOM, so Arabic is kept)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([f.label_ar if lang == "ar" else f.label_en for f in kind.fields])
    writer.writerow([f.example for f in kind.fields])
    return ("﻿" + out.getvalue()).encode("utf-8")
