from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import InventoryMovement
from app.schemas import (
    InventoryMovementListRead,
    InventoryMovementRead,
    StockMovementCreate,
)
from app.services.inventory import (
    MovementDirection,
    StockMovementError,
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


@router.get("/inventory-movements", response_model=InventoryMovementListRead)
def list_inventory_movements(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    product_id: Annotated[int | None, Query(ge=1)] = None,
    product_packaging_id: Annotated[int | None, Query(ge=1)] = None,
    movement_type: Annotated[Literal["IN", "OUT"] | None, Query()] = None,
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

    statement = select(InventoryMovement).options(
        joinedload(InventoryMovement.product)
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
    try:
        movement = create_stock_movement(
            db,
            product_id=product_id,
            direction=direction,
            payload=payload,
        )
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
        .options(joinedload(InventoryMovement.product))
        .where(InventoryMovement.id == movement.id)
    )
    assert refreshed_movement is not None
    return refreshed_movement
