from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func, literal, select
from sqlalchemy.orm import Session, aliased, joinedload

from app.database import get_db
from app.models import Category, InventoryMovement, Product, ProductPackaging, Warehouse
from app.schemas import (
    DashboardCategoryDistributionRead,
    DashboardSummaryRead,
    DashboardWarehouseDistributionRead,
    DashboardZeroStockProductRead,
)


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummaryRead)
def get_dashboard_summary(
    db: Annotated[Session, Depends(get_db)],
) -> DashboardSummaryRead:
    active_product_count = db.scalar(
        select(func.count(Product.id)).where(Product.is_active.is_(True))
    ) or 0
    inactive_product_count = db.scalar(
        select(func.count(Product.id)).where(Product.is_active.is_(False))
    ) or 0
    total_carton_count = db.scalar(
        select(func.coalesce(func.sum(ProductPackaging.carton_count), 0))
        .join(Product, Product.id == ProductPackaging.product_id)
        .where(Product.is_active.is_(True))
    ) or 0

    zero_stock_product_ids = _zero_stock_product_ids()
    zero_stock_product_count = db.scalar(
        select(func.count()).select_from(zero_stock_product_ids)
    ) or 0

    recent_movements = db.scalars(
        select(InventoryMovement)
        .options(
            joinedload(InventoryMovement.product),
            joinedload(InventoryMovement.warehouse),
        )
        .order_by(
            InventoryMovement.created_at.desc(),
            InventoryMovement.id.desc(),
        )
        .limit(5)
    ).all()

    zero_stock_products = _get_zero_stock_products(db, zero_stock_product_ids)
    category_distribution = _get_category_distribution(db)
    warehouse_distribution = _get_warehouse_distribution(db)

    return DashboardSummaryRead(
        active_product_count=active_product_count,
        total_carton_count=total_carton_count,
        zero_stock_product_count=zero_stock_product_count,
        inactive_product_count=inactive_product_count,
        recent_movements=recent_movements,
        zero_stock_products=zero_stock_products,
        category_distribution=category_distribution,
        warehouse_distribution=warehouse_distribution,
    )


def _zero_stock_product_ids():
    return (
        select(Product.id.label("product_id"))
        .outerjoin(ProductPackaging, ProductPackaging.product_id == Product.id)
        .where(Product.is_active.is_(True))
        .group_by(Product.id)
        .having(func.coalesce(func.sum(ProductPackaging.carton_count), 0) == 0)
        .subquery()
    )


def _get_zero_stock_products(
    db: Session,
    zero_stock_product_ids,
) -> list[DashboardZeroStockProductRead]:
    child_category = aliased(Category)
    parent_category = aliased(Category)
    category_name = func.coalesce(
        parent_category.name,
        child_category.name,
        literal("未分类"),
    ).label("category_name")

    rows = db.execute(
        select(
            Product.id,
            Product.product_code,
            Product.image_path,
            Product.thumbnail_path,
            category_name,
            Warehouse.name.label("warehouse_name"),
        )
        .outerjoin(child_category, Product.category_id == child_category.id)
        .outerjoin(parent_category, child_category.parent_id == parent_category.id)
        .outerjoin(Warehouse, Product.warehouse_id == Warehouse.id)
        .where(
            Product.id.in_(select(zero_stock_product_ids.c.product_id))
        )
        .order_by(
            case((Product.product_code.is_(None), 1), else_=0),
            Product.product_code.asc(),
            Product.id.asc(),
        )
        .limit(6)
    ).mappings().all()

    return [DashboardZeroStockProductRead(**row) for row in rows]


def _get_category_distribution(
    db: Session,
) -> list[DashboardCategoryDistributionRead]:
    child_category = aliased(Category)
    parent_category = aliased(Category)
    category_id = case(
        (parent_category.id.is_not(None), parent_category.id),
        (child_category.id.is_not(None), child_category.id),
        else_=None,
    ).label("category_id")
    category_name = func.coalesce(
        parent_category.name,
        child_category.name,
        literal("未分类"),
    ).label("category_name")
    product_count = func.count(Product.id).label("product_count")

    rows = db.execute(
        select(category_id, category_name, product_count)
        .select_from(Product)
        .outerjoin(child_category, Product.category_id == child_category.id)
        .outerjoin(parent_category, child_category.parent_id == parent_category.id)
        .where(Product.is_active.is_(True))
        .group_by(category_id, category_name)
        .order_by(product_count.desc(), category_name.asc())
        .limit(5)
    ).mappings().all()

    return [DashboardCategoryDistributionRead(**row) for row in rows]


def _get_warehouse_distribution(
    db: Session,
) -> list[DashboardWarehouseDistributionRead]:
    carton_count = func.coalesce(func.sum(ProductPackaging.carton_count), 0).label(
        "carton_count"
    )

    rows = db.execute(
        select(
            Warehouse.id.label("warehouse_id"),
            Warehouse.name.label("warehouse_name"),
            carton_count,
        )
        .select_from(Warehouse)
        .outerjoin(
            Product,
            and_(
                Product.warehouse_id == Warehouse.id,
                Product.is_active.is_(True),
            ),
        )
        .outerjoin(ProductPackaging, ProductPackaging.product_id == Product.id)
        .group_by(Warehouse.id)
        .order_by(
            carton_count.desc(),
            Warehouse.sort_order.asc(),
            Warehouse.id.asc(),
        )
    ).mappings().all()

    return [DashboardWarehouseDistributionRead(**row) for row in rows]
