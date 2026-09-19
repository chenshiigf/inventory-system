from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Category
from app.schemas import CategoryCreate, CategoryRead, CategoryTreeNode, CategoryUpdate


router = APIRouter(prefix="/api/categories", tags=["categories"])


@router.get("", response_model=list[CategoryTreeNode])
def list_categories(db: Annotated[Session, Depends(get_db)]) -> list[CategoryTreeNode]:
    categories = db.scalars(
        select(Category).order_by(Category.sort_order.asc(), Category.id.asc())
    ).all()
    nodes = {
        category.id: CategoryTreeNode(
            id=category.id,
            name=category.name,
            parent_id=category.parent_id,
            sort_order=category.sort_order,
            children=[],
        )
        for category in categories
    }

    roots: list[CategoryTreeNode] = []
    for category in categories:
        node = nodes[category.id]
        if category.parent_id is None:
            roots.append(node)
        else:
            parent = nodes.get(category.parent_id)
            if parent is not None:
                parent.children.append(node)
    return roots


@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(
    payload: CategoryCreate,
    db: Annotated[Session, Depends(get_db)],
) -> Category:
    _validate_parent(db, payload.parent_id)
    _ensure_name_is_available(db, payload.name, payload.parent_id)

    category = Category(
        name=payload.name,
        parent_id=payload.parent_id,
        sort_order=payload.sort_order,
    )
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.patch("/{category_id}", response_model=CategoryRead)
def update_category(
    category_id: Annotated[int, Path(ge=1)],
    payload: CategoryUpdate,
    db: Annotated[Session, Depends(get_db)],
) -> Category:
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )

    _ensure_name_is_available(
        db,
        payload.name,
        category.parent_id,
        exclude_category_id=category.id,
    )
    category.name = payload.name
    category.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(category)
    return category


def _validate_parent(db: Session, parent_id: int | None) -> None:
    if parent_id is None:
        return

    parent = db.get(Category, parent_id)
    if parent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Parent category not found",
        )
    if parent.parent_id is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Only first-level categories can have children",
        )


def _ensure_name_is_available(
    db: Session,
    name: str,
    parent_id: int | None,
    exclude_category_id: int | None = None,
) -> None:
    statement = select(Category.id).where(Category.name == name)
    if parent_id is None:
        statement = statement.where(Category.parent_id.is_(None))
    else:
        statement = statement.where(Category.parent_id == parent_id)

    if exclude_category_id is not None:
        statement = statement.where(Category.id != exclude_category_id)

    if db.scalar(statement) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A category with this name already exists at this level",
        )
