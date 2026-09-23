"""add product active status

Revision ID: a6d7e8f9b0c1
Revises: f7a3c9d1e2b4
Create Date: 2026-09-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a6d7e8f9b0c1"
down_revision: Union[str, Sequence[str], None] = "f7a3c9d1e2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Adding a NOT NULL column with a server default avoids rebuilding the
    # SQLite products table, so product_packagings cannot be cascade-deleted.
    op.add_column(
        "products",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    op.drop_column("products", "is_active")
