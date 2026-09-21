"""add stable product codes

Revision ID: 7c2758324ff8
Revises: 0bd66b1b03e4
Create Date: 2026-09-20 15:28:59.100681

"""
from typing import Sequence, Union
from collections import defaultdict

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c2758324ff8'
down_revision: Union[str, Sequence[str], None] = '0bd66b1b03e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _format_component(number: int, minimum_width: int = 2) -> str:
    return str(number).zfill(minimum_width)


def _backfill_existing_codes(connection: sa.Connection) -> None:
    categories = sa.table(
        "categories",
        sa.column("id", sa.Integer),
        sa.column("parent_id", sa.Integer),
        sa.column("sort_order", sa.Integer),
        sa.column("code", sa.String),
        sa.column("next_product_sequence", sa.Integer),
    )
    products = sa.table(
        "products",
        sa.column("id", sa.Integer),
        sa.column("category_id", sa.Integer),
        sa.column("product_code", sa.String),
    )

    category_rows = [
        dict(row)
        for row in connection.execute(
            sa.select(categories).order_by(categories.c.sort_order, categories.c.id)
        ).mappings().all()
    ]
    siblings_by_parent: dict[int | None, list[dict[str, object]]] = defaultdict(list)
    for row in category_rows:
        siblings_by_parent[row["parent_id"]].append(row)

    for parent_id, siblings in siblings_by_parent.items():
        largest = max(
            (
                int(row["code"])
                for row in siblings
                if row["code"] is not None and str(row["code"]).isdecimal()
            ),
            default=0,
        )
        next_number = largest + 1
        for row in siblings:
            values: dict[str, object] = {}
            if row["code"] is None:
                values["code"] = _format_component(next_number)
                row["code"] = values["code"]
                next_number += 1
            if parent_id is not None and row["next_product_sequence"] is None:
                values["next_product_sequence"] = 1
                row["next_product_sequence"] = 1
            if values:
                connection.execute(
                    categories.update()
                    .where(categories.c.id == row["id"])
                    .values(**values)
                )

    category_by_id = {row["id"]: row for row in category_rows}
    products_by_category: dict[int, list[dict[str, object]]] = defaultdict(list)
    product_rows = connection.execute(
        sa.select(products).order_by(products.c.id)
    ).mappings().all()
    for product_row in product_rows:
        if product_row["category_id"] is not None:
            products_by_category[product_row["category_id"]].append(dict(product_row))

    for category in category_rows:
        parent_id = category["parent_id"]
        if parent_id is None or category["code"] is None:
            continue
        parent = category_by_id.get(parent_id)
        if parent is None or parent["parent_id"] is not None or parent["code"] is None:
            continue

        prefix = f"{parent['code']}-{category['code']}-"
        category_products = products_by_category.get(category["id"], [])
        used_sequences = [
            int(product["product_code"][len(prefix) :])
            for product in category_products
            if product["product_code"] is not None
            and str(product["product_code"]).startswith(prefix)
            and str(product["product_code"])[len(prefix) :].isdecimal()
        ]
        next_sequence = max(
            int(category["next_product_sequence"] or 1),
            max(used_sequences, default=0) + 1,
        )
        for product in category_products:
            if product["product_code"] is not None:
                continue
            code = f"{prefix}{_format_component(next_sequence, minimum_width=3)}"
            connection.execute(
                products.update()
                .where(products.c.id == product["id"])
                .values(product_code=code)
            )
            next_sequence += 1

        connection.execute(
            categories.update()
            .where(categories.c.id == category["id"])
            .values(next_product_sequence=next_sequence)
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('categories', sa.Column('code', sa.String(length=20), nullable=True))
    op.add_column('categories', sa.Column('next_product_sequence', sa.Integer(), nullable=True))
    op.add_column('products', sa.Column('product_code', sa.String(length=64), nullable=True))

    _backfill_existing_codes(op.get_bind())

    op.create_index('uq_categories_child_parent_code', 'categories', ['parent_id', 'code'], unique=True, sqlite_where=sa.text('parent_id IS NOT NULL AND code IS NOT NULL'))
    op.create_index('uq_categories_primary_code', 'categories', ['code'], unique=True, sqlite_where=sa.text('parent_id IS NULL AND code IS NOT NULL'))
    op.create_index('uq_products_product_code', 'products', ['product_code'], unique=True, sqlite_where=sa.text('product_code IS NOT NULL'))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_products_product_code', table_name='products')
    op.drop_column('products', 'product_code')
    op.drop_index('uq_categories_primary_code', table_name='categories')
    op.drop_index('uq_categories_child_parent_code', table_name='categories')
    op.drop_column('categories', 'next_product_sequence')
    op.drop_column('categories', 'code')
