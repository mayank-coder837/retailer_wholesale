"""outbox transaction id: sync batches never split a database transaction (M2.1a)

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-24 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sync_outbox",
        sa.Column("txid", sa.BigInteger(), server_default=sa.text("txid_current()"), nullable=False),
    )
    # Rows written before this: the inserting transaction is recorded in the row's own xmin.
    op.execute("UPDATE sync_outbox SET txid = xmin::text::bigint")
    op.create_index("ix_sync_outbox_txid", "sync_outbox", ["txid"])


def downgrade() -> None:
    op.drop_index("ix_sync_outbox_txid", table_name="sync_outbox")
    op.drop_column("sync_outbox", "txid")
