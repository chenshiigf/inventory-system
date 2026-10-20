from collections import defaultdict
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import func, inspect, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from app.models import Category, Product, ProductCodeMonth

BEIJING = timezone(timedelta(hours=8))
MAX_SEQUENCE = 2**63 - 2


def product_creation_time() -> datetime:
    return datetime.now(timezone.utc)


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


def allocate_product_code(db: Session, *, created_at: datetime) -> str:
    """Caller owns BEGIN IMMEDIATE and commit; failed creations consume no number."""
    if created_at.tzinfo is None:
        raise ValueError("建档时间必须包含时区")
    local_time = created_at.astimezone(BEIJING)
    # YY has a finite namespace. Never silently wrap it into an earlier century.
    if not 2000 <= local_time.year <= 2099:
        raise ValueError("当前编号规则仅支持 2000 至 2099 年")
    period = local_time.strftime("%Y%m")
    prefix = local_time.strftime("%y%m")
    if not inspect(db.connection()).has_table(ProductCodeMonth.__tablename__):
        raise ValueError("月份计数器尚未初始化，请先在指定数据库应用结构迁移")

    # Existing numeric codes may have come from an import or an earlier scheme.
    # Include inactive products and never move an existing high-water mark back.
    largest = 0
    for code in db.scalars(select(Product.product_code).where(Product.product_code.startswith(prefix))).all():
        if re.fullmatch(r"[0-9]{6,64}", code or ""):
            largest = max(largest, int(code[4:]))
    if largest + 1 > MAX_SEQUENCE:
        raise ValueError("本月编号流水号超出安全范围")
    statement = insert(ProductCodeMonth).values(period_ym=period, next_sequence=largest + 2)
    sequence = db.scalar(statement.on_conflict_do_update(
        index_elements=[ProductCodeMonth.period_ym],
        set_={"next_sequence": func.max(ProductCodeMonth.next_sequence, largest + 1) + 1},
        where=ProductCodeMonth.next_sequence <= MAX_SEQUENCE,
    ).returning(ProductCodeMonth.next_sequence - 1))
    if sequence is None:
        raise ValueError("本月编号流水号超出安全范围")
    return prefix + format_code_component(sequence)


def backfill_product_codes(db: Session) -> dict[str, int]:
    """Assign missing category/product codes without changing codes already stored."""
    if inspect(db.connection()).has_table(ProductCodeMonth.__tablename__):
        raise ValueError("月份编号结构已启用，旧三段式回填工具已禁用；历史换号须单独预演和授权")
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
