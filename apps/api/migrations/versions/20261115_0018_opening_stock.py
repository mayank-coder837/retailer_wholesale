"""opening stock: a stock document type for goods in on the day the books start (M1.12b-2)

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-15 02:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = "'quotation', 'sales_order', 'delivery_note', 'proforma_invoice', 'sales_invoice', 'sales_return', 'purchase_order', 'goods_receipt', 'purchase_bill', 'purchase_return', 'receipt', 'payment', 'contra', 'journal', 'expense', 'credit_note', 'debit_note', 'stock_adjustment', 'stock_transfer', 'item_transfer'"


def _set(types: str) -> None:
    op.drop_constraint(op.f("ck_document_doc_type"), "document", type_="check")
    op.create_check_constraint(op.f("ck_document_doc_type"), "document", f"doc_type IN ({types})")


def upgrade() -> None:
    _set(BEFORE + ", 'opening_stock'")


def downgrade() -> None:
    _set(BEFORE)
