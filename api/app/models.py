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
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
        CheckConstraint("unit IN ('pcs', 'set')", name="ck_products_unit_valid"),
        CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
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
    unit: Mapped[str] = mapped_column(String(3), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2, asdecimal=True), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )
    packagings: Mapped[list["ProductPackaging"]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="ProductPackaging.sort_order",
        lazy="selectin",
    )

    @property
    def total_carton_count(self) -> int:
        return sum(packaging.carton_count for packaging in self.packagings)


class ProductPackaging(Base):
    __tablename__ = "product_packagings"
    __table_args__ = (
        CheckConstraint(
            "packing_qty > 0",
            name="ck_product_packagings_packing_qty_positive",
        ),
        CheckConstraint(
            "carton_count >= 0",
            name="ck_product_packagings_carton_count_nonnegative",
        ),
        CheckConstraint(
            "sort_order >= 0",
            name="ck_product_packagings_sort_order_nonnegative",
        ),
        UniqueConstraint(
            "product_id",
            "packing_qty",
            name="uq_product_packagings_product_packing_qty",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey(
            "products.id",
            name="fk_product_packagings_product_id_products",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    packing_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    carton_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )
    product: Mapped[Product] = relationship(back_populates="packagings")


class ProductImportBatch(Base):
    __tablename__ = "product_import_batches"
    __table_args__ = (
        UniqueConstraint("file_hash", name="uq_product_import_batches_file_hash"),
        CheckConstraint(
            "source_row_count >= 0",
            name="ck_product_import_batches_source_rows_nonnegative",
        ),
        CheckConstraint(
            "product_count >= 0",
            name="ck_product_import_batches_products_nonnegative",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(), nullable=False, default=utc_now)
