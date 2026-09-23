"""add transactional inventory movements

Revision ID: d8f4c2a1b6e0
Revises: a6d7e8f9b0c1
Create Date: 2026-09-23 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d8f4c2a1b6e0"
down_revision: Union[str, Sequence[str], None] = "a6d7e8f9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("product_packaging_id", sa.Integer(), nullable=True),
        sa.Column("warehouse_id", sa.Integer(), nullable=True),
        sa.Column("movement_type", sa.String(length=3), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("before_carton_count", sa.Integer(), nullable=False),
        sa.Column("after_carton_count", sa.Integer(), nullable=False),
        sa.Column("packing_qty_snapshot", sa.Integer(), nullable=True),
        sa.Column("unit_snapshot", sa.String(length=3), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "movement_type IN ('IN', 'OUT')",
            name="ck_inventory_movements_type_valid",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_inventory_movements_quantity_positive",
        ),
        sa.CheckConstraint(
            "before_carton_count >= 0",
            name="ck_inventory_movements_before_nonnegative",
        ),
        sa.CheckConstraint(
            "after_carton_count >= 0",
            name="ck_inventory_movements_after_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_inventory_movements_product_id_products",
        ),
        sa.ForeignKeyConstraint(
            ["product_packaging_id"],
            ["product_packagings.id"],
            name="fk_inventory_movements_product_packaging_id_product_packagings",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name="fk_inventory_movements_warehouse_id_warehouses",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_movements_product_id",
        "inventory_movements",
        ["product_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_movements_product_packaging_id",
        "inventory_movements",
        ["product_packaging_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_movements_warehouse_id",
        "inventory_movements",
        ["warehouse_id"],
        unique=False,
    )
    op.create_index(
        "ix_inventory_movements_created_at",
        "inventory_movements",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inventory_movements_created_at",
        table_name="inventory_movements",
    )
    op.drop_index(
        "ix_inventory_movements_warehouse_id",
        table_name="inventory_movements",
    )
    op.drop_index(
        "ix_inventory_movements_product_packaging_id",
        table_name="inventory_movements",
    )
    op.drop_index(
        "ix_inventory_movements_product_id",
        table_name="inventory_movements",
    )
    op.drop_table("inventory_movements")
