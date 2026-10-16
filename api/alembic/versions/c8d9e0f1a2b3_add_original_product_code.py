"""Add an optional, non-unique original product code.

Revision ID: c8d9e0f1a2b3
Revises: b2c3d4e5f6a7
"""

from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A nullable ADD COLUMN leaves existing products and child tables untouched.
    op.add_column(
        "products",
        sa.Column("original_product_code", sa.String(length=100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "original_product_code")
