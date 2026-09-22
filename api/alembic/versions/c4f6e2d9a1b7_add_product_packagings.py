"""move product stock quantities into product packaging rows

Revision ID: c4f6e2d9a1b7
Revises: a480b3f17d2c
Create Date: 2026-09-22 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4f6e2d9a1b7"
down_revision: Union[str, Sequence[str], None] = "a480b3f17d2c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_product_packagings_table() -> None:
    op.create_table(
        "product_packagings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("packing_qty", sa.Integer(), nullable=False),
        sa.Column("carton_count", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "packing_qty > 0",
            name="ck_product_packagings_packing_qty_positive",
        ),
        sa.CheckConstraint(
            "carton_count >= 0",
            name="ck_product_packagings_carton_count_nonnegative",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_product_packagings_sort_order_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_product_packagings_product_id_products",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "product_id",
            "packing_qty",
            name="uq_product_packagings_product_packing_qty",
        ),
    )
    op.create_index(
        "ix_product_packagings_product_id",
        "product_packagings",
        ["product_id"],
        unique=False,
    )


def upgrade() -> None:
    connection = op.get_bind()
    legacy_rows = connection.execute(
        sa.text(
            """
            SELECT id, packing_qty, carton_count, created_at, updated_at
            FROM products
            ORDER BY id
            """
        )
    ).mappings().all()

    # Read the old quantities before rebuilding products. The new child table
    # is intentionally created afterwards: rebuilding a SQLite parent table
    # would otherwise trigger ON DELETE CASCADE on the freshly backfilled rows.
    with op.batch_alter_table("products", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "ck_products_packing_qty_positive", type_="check"
        )
        batch_op.drop_constraint(
            "ck_products_carton_count_nonnegative", type_="check"
        )
        batch_op.drop_column("packing_qty")
        batch_op.drop_column("carton_count")

    _create_product_packagings_table()

    if legacy_rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO product_packagings
                    (product_id, packing_qty, carton_count, sort_order, created_at, updated_at)
                VALUES
                    (:id, :packing_qty, :carton_count, 0, :created_at, :updated_at)
                """
            ),
            [dict(row) for row in legacy_rows],
        )


def downgrade() -> None:
    # A downgrade keeps the first packaging row for each product. Additional
    # packaging rows cannot be represented by the legacy one-row shape.
    connection = op.get_bind()
    packaging_rows = connection.execute(
        sa.text(
            """
            SELECT product_id, packing_qty, carton_count
            FROM product_packagings
            ORDER BY product_id, sort_order, id
            """
        )
    ).mappings().all()
    first_packaging_by_product: dict[int, dict[str, object]] = {}
    for row in packaging_rows:
        first_packaging_by_product.setdefault(int(row["product_id"]), dict(row))

    with op.batch_alter_table("products", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("packing_qty", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("carton_count", sa.Integer(), nullable=True))

    for product_id, row in first_packaging_by_product.items():
        connection.execute(
            sa.text(
                """
                UPDATE products
                SET packing_qty = :packing_qty, carton_count = :carton_count
                WHERE id = :product_id
                """
            ),
            {
                "product_id": product_id,
                "packing_qty": row["packing_qty"],
                "carton_count": row["carton_count"],
            },
        )

    with op.batch_alter_table("products", recreate="always") as batch_op:
        batch_op.alter_column(
            "packing_qty",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.alter_column(
            "carton_count",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_products_packing_qty_positive", "packing_qty > 0"
        )
        batch_op.create_check_constraint(
            "ck_products_carton_count_nonnegative", "carton_count >= 0"
        )

    op.drop_index("ix_product_packagings_product_id", table_name="product_packagings")
    op.drop_table("product_packagings")
