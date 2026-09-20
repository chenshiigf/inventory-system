from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Warehouse
from app.schemas import WarehouseRead


router = APIRouter(prefix="/api/warehouses", tags=["warehouses"])


@router.get("", response_model=list[WarehouseRead])
def list_warehouses(db: Annotated[Session, Depends(get_db)]) -> list[Warehouse]:
    return db.scalars(
        select(Warehouse).order_by(Warehouse.sort_order.asc(), Warehouse.id.asc())
    ).all()
