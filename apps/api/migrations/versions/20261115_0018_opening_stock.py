"""opening stock: a stock document type for goods in on the day the books start (M1.12b-2)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-15 02:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = "'quotation', 'sales_order', 'delivery_note', 'proforma_invoice', 'sales_invoice', 'sales_return', 'purchase_order', 'goods_receipt', 'purchase_bill', 'purchase_return', 'receipt', 'payment', 'contra', 'journal', 'expense', 'credit_note', 'debit_note', 'stock_adjustment', 'stock_transfer', 'item_transfer'"


def _set(types: str) -> None:
    op.drop_constraint(op.f("ck_document_doc_type"), "document", type_="check")
    op.create_check_constraint(op.f("ck_document_doc_type"), "document", f"doc_type IN ({types})")


ACTIONS = ("view", "create", "edit", "delete", "approve", "post", "cancel", "reverse", "export")
NEW_PERMISSIONS = [f"opening_stock.{a}" for a in ACTIONS] + ["import.view", "import.create", "import.post"]


def upgrade() -> None:
    _set(BEFORE + ", 'opening_stock'")
    # Roles keep their permissions as rows, so tenants that already exist would not have the permissions of the
    # import wizards: give them to the Manager and Accountant roles (the Owner holds everything).
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO role_permission (id, tenant_id, role_id, permission_code)
            SELECT gen_random_uuid(), r.tenant_id, r.id, p.code
            FROM role r CROSS JOIN unnest(CAST(:codes AS text[])) AS p(code)
            WHERE r.system_key IN ('manager', 'accountant')
            ON CONFLICT (role_id, permission_code) DO NOTHING
            """
        ),
        {"codes": NEW_PERMISSIONS},
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permission WHERE permission_code LIKE 'opening_stock.%'")
    _set(BEFORE)
