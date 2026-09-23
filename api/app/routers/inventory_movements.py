from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import InventoryMovement, Product
from app.schemas import (
    InventoryMovementListRead,
    InventoryMovementRead,
    StockAdjustmentCreate,
    StockMovementCreate,
)
from app.services.inventory import (
    MovementDirection,
    StockMovementError,
    create_stock_adjustment,
    create_stock_movement,
)


router = APIRouter(prefix="/api", tags=["inventory-movements"])


@router.post(
    "/products/{product_id}/stock/in",
    response_model=InventoryMovementRead,
    status_code=status.HTTP_201_CREATED,
)
def stock_in(
    product_id: Annotated[int, Path(ge=1)],
    payload: StockMovementCreate,
    db: Annotated[Session, Depends(get_db)],
) -> InventoryMovement:
    return _apply_stock_movement(
        db,
        product_id=product_id,
        direction="IN",
        payload=payload,
    )


@router.post(
    "/products/{product_id}/stock/out",
    response_model=InventoryMovementRead,
    status_code=status.HTTP_201_CREATED,
)
def stock_out(
    product_id: Annotated[int, Path(ge=1)],
    payload: StockMovementCreate,
    db: Annotated[Session, Depends(get_db)],
) -> InventoryMovement:
    return _apply_stock_movement(
        db,
        product_id=product_id,
        direction="OUT",
        payload=payload,
    )


@router.post(
    "/products/{product_id}/stock/adjust",
    response_model=InventoryMovementRead,
    status_code=status.HTTP_201_CREATED,
)
def stock_adjust(
    product_id: Annotated[int, Path(ge=1)],
    payload: StockAdjustmentCreate,
    db: Annotated[Session, Depends(get_db)],
) -> InventoryMovement:
    return _apply_movement_operation(
        db,
        lambda: create_stock_adjustment(
            db,
            product_id=product_id,
            payload=payload,
        ),
    )


@router.get("/inventory-movements", response_model=InventoryMovementListRead)
def list_inventory_movements(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    product_id: Annotated[int | None, Query(ge=1)] = None,
    product_packaging_id: Annotated[int | None, Query(ge=1)] = None,
    movement_type: Annotated[
        Literal["IN", "OUT", "ADJUST"] | None, Query()
    ] = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    warehouse_id: Annotated[int | None, Query(ge=1)] = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> InventoryMovementListRead:
    filters = []
    if product_id is not None:
        filters.append(InventoryMovement.product_id == product_id)
    if product_packaging_id is not None:
        filters.append(
            InventoryMovement.product_packaging_id == product_packaging_id
        )
    if movement_type is not None:
        filters.append(InventoryMovement.movement_type == movement_type)
    if search is not None and search.strip():
        filters.append(
            InventoryMovement.product.has(
                Product.product_code.ilike(f"%{search.strip()}%")
            )
        )
    if warehouse_id is not None:
        filters.append(InventoryMovement.warehouse_id == warehouse_id)
    if start_date is not None:
        filters.append(
            InventoryMovement.created_at
            >= datetime.combine(start_date, time.min)
        )
    if end_date is not None:
        filters.append(
            InventoryMovement.created_at
            < datetime.combine(end_date + timedelta(days=1), time.min)
        )

    statement = select(InventoryMovement).options(
        joinedload(InventoryMovement.product),
        joinedload(InventoryMovement.warehouse),
    )
    count_statement = select(func.count(InventoryMovement.id))
    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)

    total = db.scalar(count_statement) or 0
    items = db.scalars(
        statement
        .order_by(
            InventoryMovement.created_at.desc(),
            InventoryMovement.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return InventoryMovementListRead(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


def _apply_stock_movement(
    db: Session,
    *,
    product_id: int,
    direction: MovementDirection,
    payload: StockMovementCreate,
) -> InventoryMovement:
    return _apply_movement_operation(
        db,
        lambda: create_stock_movement(
            db,
            product_id=product_id,
            direction=direction,
            payload=payload,
        ),
    )


def _apply_movement_operation(
    db: Session,
    operation: Callable[[], InventoryMovement],
) -> InventoryMovement:
    try:
        movement = operation()
        db.commit()
    except StockMovementError as error:
        db.rollback()
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="库存操作未能保存，请重试。",
        ) from error
    except Exception:
        db.rollback()
        raise

    refreshed_movement = db.scalar(
        select(InventoryMovement)
        .options(
            joinedload(InventoryMovement.product),
            joinedload(InventoryMovement.warehouse),
        )
        .where(InventoryMovement.id == movement.id)
    )
    assert refreshed_movement is not None
    return refreshed_movement
