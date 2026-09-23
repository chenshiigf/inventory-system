"""Transactional stock movement rules."""

from __future__ import annotations

from typing import Literal

from fastapi import status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import begin_write_transaction
from app.models import InventoryMovement, Product, ProductPackaging
from app.schemas import StockMovementCreate


MovementDirection = Literal["IN", "OUT"]


class StockMovementError(Exception):
    def __init__(self, detail: str, status_code: int) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def create_stock_movement(
    db: Session,
    *,
    product_id: int,
    direction: MovementDirection,
    payload: StockMovementCreate,
) -> InventoryMovement:
    """Apply one movement and flush it without committing the transaction."""

    # SQLite's write reservation makes the read/validate/update sequence
    # reliable for the intentionally low-concurrency household workflow.
    begin_write_transaction(db)
    product = db.scalar(
        select(Product)
        .options(selectinload(Product.packagings))
        .where(Product.id == product_id)
    )
    if product is None:
        raise StockMovementError("Product not found", status.HTTP_404_NOT_FOUND)
    if not product.is_active:
        raise StockMovementError(
            "已停用商品不能进行入库或出库。",
            status.HTTP_409_CONFLICT,
        )

    packaging = _resolve_packaging(db, product, direction, payload)
    before = packaging.carton_count
    if direction == "OUT" and payload.quantity > before:
        raise StockMovementError(
            "出库数不能超过当前包装库存。",
            status.HTTP_409_CONFLICT,
        )

    after = (
        before + payload.quantity
        if direction == "IN"
        else before - payload.quantity
    )
    if after < 0:
        raise StockMovementError(
            "库存不能为负数。",
            status.HTTP_409_CONFLICT,
        )

    packaging.carton_count = after
    movement = InventoryMovement(
        product_id=product.id,
        product_packaging_id=packaging.id,
        warehouse_id=product.warehouse_id,
        movement_type=direction,
        quantity=payload.quantity,
        before_carton_count=before,
        after_carton_count=after,
        packing_qty_snapshot=packaging.packing_qty,
        unit_snapshot=product.unit,
        remark=payload.remark,
    )
    db.add(movement)
    # Flush is part of the same request transaction. Any constraint or write
    # failure is raised before the router commits, so the carton update rolls
    # back together with the movement.
    db.flush()
    return movement


def _resolve_packaging(
    db: Session,
    product: Product,
    direction: MovementDirection,
    payload: StockMovementCreate,
) -> ProductPackaging:
    if payload.product_packaging_id is not None:
        packaging = next(
            (
                row
                for row in product.packagings
                if row.id == payload.product_packaging_id
            ),
            None,
        )
        if packaging is None:
            raise StockMovementError(
                "product_packaging_id 不属于该商品。",
                status.HTTP_422_UNPROCESSABLE_CONTENT,
            )
        return packaging

    if direction == "OUT":
        raise StockMovementError(
            "出库必须选择已有包装规格。",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    if payload.packing_qty is None:
        raise StockMovementError(
            "入库请选择已有包装规格或填写新的装箱数。",
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    existing = next(
        (
            row
            for row in product.packagings
            if row.packing_qty == payload.packing_qty
        ),
        None,
    )
    if existing is not None:
        return existing

    packaging = ProductPackaging(
        product_id=product.id,
        packing_qty=payload.packing_qty,
        carton_count=0,
        sort_order=max(
            (row.sort_order for row in product.packagings),
            default=-1,
        )
        + 1,
    )
    db.add(packaging)
    db.flush()
    return packaging
