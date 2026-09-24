from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import begin_write_transaction, get_db
from app.models import InventoryMovement, Product, ProductPackaging, Warehouse
from app.schemas import (
    WarehouseCreate,
    WarehouseRead,
    WarehouseSummaryRead,
    WarehouseUpdate,
)


router = APIRouter(prefix="/api/warehouses", tags=["warehouses"])


@router.get("/summary", response_model=list[WarehouseSummaryRead])
def list_warehouse_summaries(
    db: Annotated[Session, Depends(get_db)],
) -> list[WarehouseSummaryRead]:
    statement = (
        select(
            Warehouse.id,
            Warehouse.name,
            Warehouse.sort_order,
            func.count(func.distinct(Product.id)).label("product_count"),
            func.coalesce(func.sum(ProductPackaging.carton_count), 0).label(
                "carton_count"
            ),
        )
        .outerjoin(Product, Product.warehouse_id == Warehouse.id)
        .outerjoin(ProductPackaging, ProductPackaging.product_id == Product.id)
        .group_by(Warehouse.id, Warehouse.name, Warehouse.sort_order)
        .order_by(Warehouse.sort_order.asc(), Warehouse.id.asc())
    )
    rows = db.execute(statement).all()
    return [
        WarehouseSummaryRead(
            id=row.id,
            name=row.name,
            sort_order=row.sort_order,
            product_count=row.product_count,
            carton_count=row.carton_count,
        )
        for row in rows
    ]


@router.get("", response_model=list[WarehouseRead])
def list_warehouses(db: Annotated[Session, Depends(get_db)]) -> list[Warehouse]:
    return db.scalars(
        select(Warehouse).order_by(Warehouse.sort_order.asc(), Warehouse.id.asc())
    ).all()


@router.post("", response_model=WarehouseRead, status_code=status.HTTP_201_CREATED)
def create_warehouse(
    payload: WarehouseCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Warehouse:
    begin_write_transaction(db)
    _ensure_name_is_available(db, payload.name)
    max_sort_order = db.scalar(select(func.max(Warehouse.sort_order)))
    warehouse = Warehouse(
        name=payload.name,
        sort_order=(max_sort_order if max_sort_order is not None else -10) + 10,
    )
    db.add(warehouse)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise _duplicate_name_error() from error
    db.refresh(warehouse)
    return warehouse


@router.patch("/{warehouse_id}", response_model=WarehouseRead)
def update_warehouse(
    warehouse_id: Annotated[int, Path(ge=1)],
    payload: WarehouseUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> Warehouse:
    begin_write_transaction(db)
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="仓库不存在。",
        )

    _ensure_name_is_available(db, payload.name, exclude_warehouse_id=warehouse.id)
    warehouse.name = payload.name
    warehouse.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise _duplicate_name_error() from error
    db.refresh(warehouse)
    return warehouse


@router.delete("/{warehouse_id}")
def delete_warehouse(
    warehouse_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, str]:
    begin_write_transaction(db)
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="仓库不存在。",
        )

    product_exists = db.scalar(
        select(Product.id)
        .where(Product.warehouse_id == warehouse.id)
        .limit(1)
    )
    if product_exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该仓库仍有关联商品，无法删除。",
        )

    movement_exists = db.scalar(
        select(InventoryMovement.id)
        .where(InventoryMovement.warehouse_id == warehouse.id)
        .limit(1)
    )
    if movement_exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该仓库已有库存流水，无法删除。",
        )

    db.delete(warehouse)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该仓库仍有关联数据，无法删除。",
        ) from error

    return {"message": "仓库已删除。"}


def _ensure_name_is_available(
    db: Session,
    name: str,
    exclude_warehouse_id: int | None = None,
) -> None:
    statement = select(Warehouse.id).where(func.lower(Warehouse.name) == name.lower())
    if exclude_warehouse_id is not None:
        statement = statement.where(Warehouse.id != exclude_warehouse_id)
    if db.scalar(statement) is not None:
        raise _duplicate_name_error()


def _duplicate_name_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="仓库名称已存在。",
    )
