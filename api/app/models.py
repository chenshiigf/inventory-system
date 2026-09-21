from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now() -> datetime:
    # SQLite stores these as UTC values without a timezone suffix.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_categories_sort_order_nonnegative"),
        Index(
            "uq_categories_primary_code",
            "code",
            unique=True,
            sqlite_where=text("parent_id IS NULL AND code IS NOT NULL"),
        ),
        Index(
            "uq_categories_child_parent_code",
            "parent_id",
            "code",
            unique=True,
            sqlite_where=text("parent_id IS NOT NULL AND code IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", name="fk_categories_parent_id_categories"),
        nullable=True,
        index=True,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    next_product_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class Warehouse(Base):
    __tablename__ = "warehouses"
    __table_args__ = (
        CheckConstraint("sort_order >= 0", name="ck_warehouses_sort_order_nonnegative"),
        UniqueConstraint("name", name="uq_warehouses_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("packing_qty > 0", name="ck_products_packing_qty_positive"),
        CheckConstraint("unit IN ('pcs', 'set')", name="ck_products_unit_valid"),
        CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
        CheckConstraint("carton_count >= 0", name="ck_products_carton_count_nonnegative"),
        Index(
            "uq_products_product_code",
            "product_code",
            unique=True,
            sqlite_where=text("product_code IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", name="fk_products_category_id_categories"),
        nullable=True,
        index=True,
    )
    warehouse_id: Mapped[int | None] = mapped_column(
        ForeignKey("warehouses.id", name="fk_products_warehouse_id_warehouses"),
        nullable=True,
        index=True,
    )
    product_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    thumbnail_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    packing_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(3), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2, asdecimal=True), nullable=False)
    carton_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )
