"""sales: quotation validity, where a line's price came from, and the proforma permissions (M1.8a)

A cash sale keeps its cash or bank account in ``document.account_id`` (as a voucher does), so it needs no column.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-25 00:30:45.543598
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("document", sa.Column("valid_until", sa.Date(), nullable=True))
    op.add_column("document_line", sa.Column("price_source", sa.String(length=80), nullable=True))
    op.add_column(
        "document_line",
        sa.Column("below_min", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # New permissions: proforma_invoice.<action>. Every existing role reaches proformas as it reaches sales
    # invoices (new tenants get them from seeds/default_roles.json).
    op.execute(
        """
        INSERT INTO role_permission (id, tenant_id, role_id, permission_code)
        SELECT gen_random_uuid(), tenant_id, role_id,
               'proforma_invoice.' || split_part(permission_code, '.', 2)
        FROM role_permission
        WHERE permission_code LIKE 'sales_invoice.%'
        ON CONFLICT (role_id, permission_code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission_code LIKE 'proforma_invoice.%'")
    op.drop_column("document_line", "below_min")
    op.drop_column("document_line", "price_source")
    op.drop_column("document", "valid_until")
