"""allow ADJUST inventory movements

Revision ID: e1f2a3b4c5d6
Revises: d8f4c2a1b6e0
Create Date: 2026-09-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d8f4c2a1b6e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite cannot alter a CHECK constraint in place. Alembic's batch
    # recreation copies every existing row, foreign key, and index while
    # replacing only the movement type constraint and column declaration.
    with op.batch_alter_table("inventory_movements", recreate="always") as batch_op:
        batch_op.alter_column(
            "movement_type",
            existing_type=sa.String(length=3),
            type_=sa.String(length=6),
            existing_nullable=False,
        )
        batch_op.drop_constraint(
            "ck_inventory_movements_type_valid",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_inventory_movements_type_valid",
            "movement_type IN ('IN', 'OUT', 'ADJUST')",
        )


def downgrade() -> None:
    connection = op.get_bind()
    existing_adjustment = connection.execute(
        sa.text(
            "SELECT 1 FROM inventory_movements "
            "WHERE movement_type = 'ADJUST' LIMIT 1"
        )
    ).first()
    if existing_adjustment is not None:
        raise RuntimeError(
            "Cannot downgrade while ADJUST inventory movements exist."
        )

    with op.batch_alter_table("inventory_movements", recreate="always") as batch_op:
        batch_op.alter_column(
            "movement_type",
            existing_type=sa.String(length=6),
            type_=sa.String(length=3),
            existing_nullable=False,
        )
        batch_op.drop_constraint(
            "ck_inventory_movements_type_valid",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_inventory_movements_type_valid",
            "movement_type IN ('IN', 'OUT')",
        )
