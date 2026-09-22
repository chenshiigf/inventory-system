"""add product import batches

Revision ID: e2c4d8f1a6b9
Revises: c4f6e2d9a1b7
Create Date: 2026-09-22 18:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2c4d8f1a6b9"
down_revision: Union[str, Sequence[str], None] = "c4f6e2d9a1b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_import_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("source_row_count", sa.Integer(), nullable=False),
        sa.Column("product_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_row_count >= 0",
            name="ck_product_import_batches_source_rows_nonnegative",
        ),
        sa.CheckConstraint(
            "product_count >= 0",
            name="ck_product_import_batches_products_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "file_hash",
            name="uq_product_import_batches_file_hash",
        ),
    )
    op.create_index(
        "ix_product_import_batches_file_hash",
        "product_import_batches",
        ["file_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_product_import_batches_file_hash",
        table_name="product_import_batches",
    )
    op.drop_table("product_import_batches")
