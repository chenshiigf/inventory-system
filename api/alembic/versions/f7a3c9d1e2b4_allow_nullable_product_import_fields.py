"""allow nullable fields required by flexible product imports

Revision ID: f7a3c9d1e2b4
Revises: e2c4d8f1a6b9
Create Date: 2026-09-22 20:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a3c9d1e2b4"
down_revision: Union[str, Sequence[str], None] = "e2c4d8f1a6b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()

    # SQLite rebuilds a table for nullable/check-constraint changes.  The
    # products rebuild temporarily drops the parent table; with foreign keys
    # enabled that would cascade-delete product_packagings.  Keep the child
    # rows in memory and restore them after both rebuilds finish.
    packaging_rows = connection.execute(
        sa.text(
            """
            SELECT id, product_id, packing_qty, carton_count,
                   sort_order, created_at, updated_at
            FROM product_packagings
            ORDER BY id
            """
        )
    ).mappings().all()

    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_constraint("ck_products_unit_valid", type_="check")
        batch_op.create_check_constraint(
            "ck_products_unit_valid",
            "unit IS NULL OR unit IN ('pcs', 'set')",
        )
        batch_op.alter_column(
            "unit",
            existing_type=sa.String(length=3),
            nullable=True,
        )
        batch_op.alter_column(
            "price",
            existing_type=sa.Numeric(precision=12, scale=2),
            nullable=True,
        )

    with op.batch_alter_table("product_packagings") as batch_op:
        batch_op.alter_column(
            "packing_qty",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.drop_constraint(
            "ck_product_packagings_packing_qty_positive",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_product_packagings_packing_qty_positive",
            "packing_qty IS NULL OR packing_qty > 0",
        )

    if packaging_rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO product_packagings
                    (id, product_id, packing_qty, carton_count,
                     sort_order, created_at, updated_at)
                VALUES
                    (:id, :product_id, :packing_qty, :carton_count,
                     :sort_order, :created_at, :updated_at)
                """
            ),
            [dict(row) for row in packaging_rows],
        )


def downgrade() -> None:
    with op.batch_alter_table("product_packagings") as batch_op:
        batch_op.drop_constraint(
            "ck_product_packagings_packing_qty_positive",
            type_="check",
        )
        batch_op.create_check_constraint(
            "ck_product_packagings_packing_qty_positive",
            "packing_qty > 0",
        )
        batch_op.alter_column(
            "packing_qty",
            existing_type=sa.Integer(),
            nullable=False,
        )

    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column(
            "unit",
            existing_type=sa.String(length=3),
            nullable=False,
        )
        batch_op.alter_column(
            "price",
            existing_type=sa.Numeric(precision=12, scale=2),
            nullable=False,
        )
