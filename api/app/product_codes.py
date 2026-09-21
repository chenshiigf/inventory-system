from collections import defaultdict

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Category, Product


def format_code_component(number: int, minimum_width: int = 2) -> str:
    if number < 1:
        raise ValueError("Code components must be positive")
    return str(number).zfill(minimum_width)


def next_category_code(db: Session, parent_id: int | None) -> str:
    statement = select(Category.code)
    if parent_id is None:
        statement = statement.where(Category.parent_id.is_(None))
    else:
        statement = statement.where(Category.parent_id == parent_id)

    existing_codes = db.scalars(statement).all()
    largest_number = max(
        (int(code) for code in existing_codes if code and code.isdecimal()),
        default=0,
    )
    return format_code_component(largest_number + 1)


def allocate_product_code(db: Session, category_id: int) -> str:
    category = db.get(Category, category_id)
    if category is None or category.parent_id is None:
        raise ValueError("A product code requires a second-level category")

    parent = db.get(Category, category.parent_id)
    if parent is None or not parent.code or not category.code:
        raise ValueError("Category codes have not been initialized")

    allocated_sequence = db.scalar(
        update(Category)
        .where(Category.id == category.id, Category.parent_id.is_not(None))
        .values(
            next_product_sequence=func.coalesce(Category.next_product_sequence, 1) + 1
        )
        .returning(Category.next_product_sequence - 1)
    )
    if allocated_sequence is None:
        raise ValueError("Unable to allocate a product number")

    return (
        f"{parent.code}-{category.code}-"
        f"{format_code_component(allocated_sequence, minimum_width=3)}"
    )


def backfill_product_codes(db: Session) -> dict[str, int]:
    """Assign missing category/product codes without changing codes already stored."""
    categories = db.scalars(
        select(Category).order_by(Category.sort_order.asc(), Category.id.asc())
    ).all()
    grouped_categories: dict[int | None, list[Category]] = defaultdict(list)
    for category in categories:
        grouped_categories[category.parent_id].append(category)

    category_count = 0
    for parent_id, siblings in grouped_categories.items():
        largest_number = max(
            (
                int(category.code)
                for category in siblings
                if category.code and category.code.isdecimal()
            ),
            default=0,
        )
        next_number = largest_number + 1
        for category in siblings:
            if category.code is None:
                category.code = format_code_component(next_number)
                next_number += 1
                category_count += 1
            if parent_id is not None and category.next_product_sequence is None:
                category.next_product_sequence = 1

    categories_by_id = {category.id: category for category in categories}
    products_by_category: dict[int, list[Product]] = defaultdict(list)
    products = db.scalars(select(Product).order_by(Product.id.asc())).all()
    for product in products:
        if product.category_id is not None:
            products_by_category[product.category_id].append(product)

    product_count = 0
    for category in categories:
        if category.parent_id is None or category.code is None:
            continue
        parent = categories_by_id.get(category.parent_id)
        if parent is None or parent.parent_id is not None or parent.code is None:
            continue

        prefix = f"{parent.code}-{category.code}-"
        existing_sequences = [
            int(product.product_code[len(prefix) :])
            for product in products_by_category.get(category.id, [])
            if product.product_code
            and product.product_code.startswith(prefix)
            and product.product_code[len(prefix) :].isdecimal()
        ]
        next_sequence = max(
            category.next_product_sequence or 1,
            max(existing_sequences, default=0) + 1,
        )

        for product in products_by_category.get(category.id, []):
            if product.product_code is not None:
                continue
            product.product_code = (
                f"{prefix}{format_code_component(next_sequence, minimum_width=3)}"
            )
            next_sequence += 1
            product_count += 1

        category.next_product_sequence = next_sequence

    db.flush()
    return {"categories_assigned": category_count, "products_assigned": product_count}
