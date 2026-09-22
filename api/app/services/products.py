"""Shared product creation rules for API and controlled imports."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Category, Product, ProductPackaging, Warehouse
from app.product_codes import allocate_product_code
from app.schemas import ProductCreate


class ProductCreationError(Exception):
    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def create_product_record(db: Session, payload: ProductCreate) -> Product:
    """Create and flush one product without owning the surrounding transaction."""

    _validate_category(db, payload.category_id)
    _validate_warehouse(db, payload.warehouse_id)
    product = Product(
        **payload.model_dump(exclude={"packagings"}),
        packagings=[
            ProductPackaging(
                packing_qty=packaging.packing_qty,
                carton_count=packaging.carton_count,
                sort_order=sort_order,
            )
            for sort_order, packaging in enumerate(payload.packagings)
        ],
    )
    if payload.category_id is not None:
        try:
            product.product_code = allocate_product_code(db, payload.category_id)
        except ValueError as error:
            raise ProductCreationError(str(error), status_code=409) from error
    db.add(product)
    db.flush()
    return product


def _validate_category(db: Session, category_id: int | None) -> None:
    if category_id is None:
        return
    category = db.get(Category, category_id)
    if category is None:
        raise ProductCreationError(
            "category_id does not reference an existing category",
            status_code=422,
        )
    if category.parent_id is None:
        raise ProductCreationError(
            "Products must be assigned to a second-level category",
            status_code=422,
        )


def _validate_warehouse(db: Session, warehouse_id: int | None) -> None:
    if warehouse_id is None:
        return
    if db.get(Warehouse, warehouse_id) is None:
        raise ProductCreationError(
            "warehouse_id does not reference an existing warehouse",
            status_code=422,
        )
