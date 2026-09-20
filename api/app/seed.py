from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Warehouse


DEFAULT_WAREHOUSES = (("主仓", 0), ("虎跳仓", 1))


def ensure_default_warehouses(db: Session) -> list[Warehouse]:
    existing_names = set(db.scalars(select(Warehouse.name)).all())
    for name, sort_order in DEFAULT_WAREHOUSES:
        if name not in existing_names:
            db.add(Warehouse(name=name, sort_order=sort_order))

    db.commit()
    return db.scalars(
        select(Warehouse).order_by(Warehouse.sort_order.asc(), Warehouse.id.asc())
    ).all()


def main() -> None:
    with SessionLocal() as db:
        warehouses = ensure_default_warehouses(db)

    for warehouse in warehouses:
        print(f"{warehouse.id}: {warehouse.name}")


if __name__ == "__main__":
    main()
