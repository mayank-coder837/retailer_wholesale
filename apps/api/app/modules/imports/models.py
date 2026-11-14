"""The work record of an import (M1.12): one batch per uploaded file, from upload to import (and undo).

Business data is never stored here — it is written through the services. A batch keeps the file (in the files
store), who uploaded it, the column mapping, and the result, so an import can be explained and never repeated.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantScoped


class BatchStatus(StrEnum):
    UPLOADED = "uploaded"  # the file is stored and its columns listed
    CHECKED = "checked"  # a mapping was chosen and every row checked (nothing written to the books)
    COMMITTED = "committed"  # imported
    UNDONE = "undone"  # what it imported was cancelled (opening balances, M1.12b)


class ImportBatch(TenantScoped, Base):
    __tablename__ = "import_batch"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(["tenant_id", "business_id"], ["business.tenant_id", "business.id"]),
        CheckConstraint(
            "status IN ('uploaded', 'checked', 'committed', 'undone')",
            name="status",
        ),
        CheckConstraint(
            "(status IN ('committed', 'undone')) = (committed_at IS NOT NULL)", name="committed_at"
        ),
        # The same file is imported once per business and kind (idempotent re-import).
        Index(
            "uq_import_batch_committed_file",
            "business_id",
            "kind",
            "sha256",
            unique=True,
            postgresql_where=text("status = 'committed'"),
        ),
        Index("ix_import_batch_business", "business_id", "created_at"),
    )

    business_id: Mapped[uuid.UUID]
    kind: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10), server_default=BatchStatus.UPLOADED.value)
    filename: Mapped[str] = mapped_column(String(255))
    file_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    headers: Mapped[list[str]] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(Integer)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
