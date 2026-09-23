from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import begin_write_transaction, get_db
from app.models import Category, Product, ProductPackaging, Warehouse
from app.schemas import (
    ProductCreate,
    ProductDetailRead,
    ProductListRead,
    ProductRead,
    ProductUpdate,
)
from app.services.products import ProductCreationError, create_product_record


router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=ProductListRead)
def list_products(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(max_length=100)] = None,
    category_id: Annotated[int | None, Query(ge=1)] = None,
    warehouse_id: Annotated[int | None, Query(ge=1)] = None,
    product_status: Annotated[
        Literal["active", "inactive", "all"], Query(alias="status")
    ] = "active",
    stock_status: Annotated[
        Literal["all", "in_stock", "zero"], Query()
    ] = "all",
) -> ProductListRead:
    statement = select(Product).options(selectinload(Product.packagings))
    count_statement = select(func.count(Product.id))
    filters = []

    normalized_search = search.strip() if search else ""
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(
            or_(
                Product.product_code.ilike(pattern),
                Product.size.ilike(pattern),
                Product.remark.ilike(pattern),
            )
        )

    if warehouse_id is not None:
        _validate_product_warehouse(db, warehouse_id)
        filters.append(Product.warehouse_id == warehouse_id)

    if category_id is not None:
        category = db.get(Category, category_id)
        if category is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Category not found",
            )

        if category.parent_id is None:
            child_ids = db.scalars(
                select(Category.id).where(Category.parent_id == category.id)
            ).all()
            filters.append(Product.category_id.in_([category.id, *child_ids]))
        else:
            filters.append(Product.category_id == category.id)

    if product_status != "all":
        filters.append(Product.is_active.is_(product_status == "active"))

    total_stock = (
        select(func.coalesce(func.sum(ProductPackaging.carton_count), 0))
        .where(ProductPackaging.product_id == Product.id)
        .scalar_subquery()
    )
    if stock_status == "in_stock":
        filters.append(total_stock > 0)
    elif stock_status == "zero":
        filters.append(total_stock == 0)

    if filters:
        statement = statement.where(*filters)
        count_statement = count_statement.where(*filters)

    total = db.scalar(count_statement) or 0
    items = db.scalars(
        statement.order_by(Product.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return ProductListRead(items=items, total=total, page=page, page_size=page_size)


@router.post("/{product_id}/deactivate", response_model=ProductRead)
def deactivate_product(
    product_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> Product:
    return _set_product_active(db, product_id, is_active=False)


@router.post("/{product_id}/activate", response_model=ProductRead)
def activate_product(
    product_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> Product:
    return _set_product_active(db, product_id, is_active=True)


@router.get("/{product_id}", response_model=ProductDetailRead)
def get_product(
    product_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> Product:
    product = db.scalar(
        select(Product)
        .options(
            selectinload(Product.packagings),
            joinedload(Product.category),
            joinedload(Product.warehouse),
        )
        .where(Product.id == product_id)
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Product:
    begin_write_transaction(db)
    try:
        product = create_product_record(db, payload)
        product_id = product.id
        db.commit()
    except ProductCreationError as error:
        db.rollback()
        raise HTTPException(
            status_code=error.status_code,
            detail=error.detail,
        ) from error
    refreshed_product = db.scalar(
        select(Product)
        .options(selectinload(Product.packagings))
        .where(Product.id == product_id)
    )
    assert refreshed_product is not None
    return refreshed_product


@router.patch("/{product_id}", response_model=ProductRead)
def update_product(
    product_id: Annotated[int, Path(ge=1)],
    payload: ProductUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> Product:
    begin_write_transaction(db)
    product = db.scalar(
        select(Product)
        .options(selectinload(Product.packagings))
        .where(Product.id == product_id)
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    updates = payload.model_dump(exclude_unset=True, exclude={"packagings"})
    if "category_id" in updates:
        _validate_product_category(db, updates["category_id"])
    if "warehouse_id" in updates:
        _validate_product_warehouse(db, updates["warehouse_id"])

    if payload.packagings is not None:
        _replace_product_packagings(db, product, payload.packagings)

    for field_name, value in updates.items():
        setattr(product, field_name, value)
    product.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    refreshed_product = db.scalar(
        select(Product)
        .options(selectinload(Product.packagings))
        .where(Product.id == product_id)
    )
    assert refreshed_product is not None
    return refreshed_product


def _set_product_active(db: Session, product_id: int, *, is_active: bool) -> Product:
    begin_write_transaction(db)
    product = db.scalar(
        select(Product)
        .options(selectinload(Product.packagings))
        .where(Product.id == product_id)
    )
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    product.is_active = is_active
    product.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    refreshed_product = db.scalar(
        select(Product)
        .options(selectinload(Product.packagings))
        .where(Product.id == product_id)
    )
    assert refreshed_product is not None
    return refreshed_product


def _replace_product_packagings(
    db: Session,
    product: Product,
    packagings: list,
) -> None:
    existing_ids = {packaging.id for packaging in product.packagings}
    incoming_ids = {
        packaging.id for packaging in packagings if packaging.id is not None
    }
    if incoming_ids - existing_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="packaging id does not belong to this product",
        )

    existing_by_id = {packaging.id: packaging for packaging in product.packagings}

    # Validate identities before replacing rows so a mistaken cross-product id
    # cannot remove any packaging from this product.
    for packaging in list(product.packagings):
        db.delete(packaging)
    db.flush()

    product.packagings = [
        ProductPackaging(
            packing_qty=packaging.packing_qty,
            # Stock is intentionally not part of a normal product edit. Keep
            # the existing count for rows identified by id; a newly added
            # packaging starts at zero and must be stocked through IN.
            carton_count=(
                existing_by_id[packaging.id].carton_count
                if packaging.id is not None and packaging.id in existing_by_id
                else 0
            ),
            sort_order=sort_order,
        )
        for sort_order, packaging in enumerate(packagings)
    ]


def _validate_product_category(db: Session, category_id: int | None) -> None:
    if category_id is None:
        return

    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="category_id does not reference an existing category",
        )
    if category.parent_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Products must be assigned to a second-level category",
        )


def _validate_product_warehouse(db: Session, warehouse_id: int | None) -> None:
    if warehouse_id is None:
        return

    if db.get(Warehouse, warehouse_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="warehouse_id does not reference an existing warehouse",
        )
