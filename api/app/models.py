from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utc_now() -> datetime:
    # SQLite stores these as UTC values without a timezone suffix.
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("packing_qty > 0", name="ck_products_packing_qty_positive"),
        CheckConstraint("unit IN ('pcs', 'set')", name="ck_products_unit_valid"),
        CheckConstraint("price >= 0", name="ck_products_price_nonnegative"),
        CheckConstraint("carton_count >= 0", name="ck_products_carton_count_nonnegative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
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
