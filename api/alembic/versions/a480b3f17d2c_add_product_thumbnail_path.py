"""add product thumbnail path

Revision ID: a480b3f17d2c
Revises: 7c2758324ff8
Create Date: 2026-09-20 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a480b3f17d2c"
down_revision: Union[str, Sequence[str], None] = "7c2758324ff8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("thumbnail_path", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "thumbnail_path")
