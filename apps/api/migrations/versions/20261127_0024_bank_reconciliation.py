"""bank statements and matches (P2.3)

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-27 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.core import rls

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit_columns() -> list[sa.Column]:  # type: ignore[type-arg]
    user = sa.text("nullif(current_setting('app.user_id', true), '')::uuid")
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by", sa.Uuid(), server_default=user, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by", sa.Uuid(), server_default=user, nullable=True),
        sa.Column(
            "origin_node_id",
            sa.Uuid(),
            server_default=sa.text("nullif(current_setting('app.node_id', true), '')::uuid"),
            nullable=True,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "bank_statement",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("opening_balance", sa.Numeric(19, 4), nullable=True),
        sa.Column("closing_balance", sa.Numeric(19, 4), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint("date_from <= date_to", name=op.f("ck_bank_statement_dates")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id", "account_id"],
            ["account.tenant_id", "account.business_id", "account.id"],
            name=op.f("fk_bank_statement_tenant_id_business_id_account_id_account"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], name=op.f("fk_bank_statement_tenant_id_tenant")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_statement")),
        sa.UniqueConstraint("tenant_id", "id", name=op.f("uq_bank_statement_tenant_id_id")),
    )
    op.create_index(op.f("ix_bank_statement_tenant_id"), "bank_statement", ["tenant_id"], unique=False)
    op.create_index("ix_bank_statement_account", "bank_statement", ["account_id", "date_to"], unique=False)

    op.create_table(
        "bank_statement_line",
        sa.Column("statement_id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("line_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("amount", sa.Numeric(19, 4), nullable=False),
        sa.Column("matched_line_id", sa.Uuid(), nullable=True),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("matched_by", sa.Uuid(), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint("amount <> 0", name=op.f("ck_bank_statement_line_amount_not_zero")),
        sa.ForeignKeyConstraint(
            ["tenant_id", "statement_id"],
            ["bank_statement.tenant_id", "bank_statement.id"],
            name=op.f("fk_bank_statement_line_tenant_id_statement_id_bank_statement"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "business_id", "account_id"],
            ["account.tenant_id", "account.business_id", "account.id"],
            name=op.f("fk_bank_statement_line_tenant_id_business_id_account_id_account"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "matched_line_id"],
            ["journal_line.tenant_id", "journal_line.id"],
            name=op.f("fk_bank_statement_line_tenant_id_matched_line_id_journal_line"),
        ),
        sa.ForeignKeyConstraint(
            ["matched_by"], ["user_account.id"], name=op.f("fk_bank_statement_line_matched_by_user_account")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_bank_statement_line_tenant_id_tenant")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bank_statement_line")),
        sa.UniqueConstraint("statement_id", "line_no", name=op.f("uq_bank_statement_line_statement_id")),
    )
    op.create_index(
        op.f("ix_bank_statement_line_tenant_id"), "bank_statement_line", ["tenant_id"], unique=False
    )
    op.create_index(
        "ix_bank_statement_line_account", "bank_statement_line", ["account_id", "line_date"], unique=False
    )
    op.create_index(
        "uq_bank_statement_line_matched",
        "bank_statement_line",
        ["matched_line_id"],
        unique=True,
        postgresql_where=sa.text("matched_line_id IS NOT NULL"),
    )
    rls.tenant_table(op, "bank_statement")
    rls.tenant_table(op, "bank_statement_line")


def downgrade() -> None:
    op.drop_table("bank_statement_line")
    op.drop_table("bank_statement")
