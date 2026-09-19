from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category, Product
from app.schemas import ProductCreate, ProductListRead, ProductRead, ProductUpdate


router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=ProductListRead)
def list_products(
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(max_length=100)] = None,
    category_id: Annotated[int | None, Query(ge=1)] = None,
) -> ProductListRead:
    statement = select(Product)
    count_statement = select(func.count(Product.id))
    filters = []

    normalized_search = search.strip() if search else ""
    if normalized_search:
        pattern = f"%{normalized_search}%"
        filters.append(or_(Product.size.ilike(pattern), Product.remark.ilike(pattern)))

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


@router.get("/{product_id}", response_model=ProductRead)
def get_product(
    product_id: Annotated[int, Path(ge=1)],
    db: Annotated[Session, Depends(get_db)],
) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return product


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Product:
    _validate_product_category(db, payload.category_id)
    product = Product(**payload.model_dump())
    db.add(product)
    db.commit()
    db.refresh(product)
    return product


@router.patch("/{product_id}", response_model=ProductRead)
def update_product(
    product_id: Annotated[int, Path(ge=1)],
    payload: ProductUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    updates = payload.model_dump(exclude_unset=True)
    if "category_id" in updates:
        _validate_product_category(db, updates["category_id"])

    for field_name, value in updates.items():
        setattr(product, field_name, value)
    product.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.commit()
    db.refresh(product)
    return product


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
