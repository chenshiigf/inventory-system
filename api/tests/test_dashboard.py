from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Category, Product, ProductPackaging, Warehouse


@pytest.fixture
def dashboard_client(tmp_path) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    database_path = tmp_path / "dashboard-test.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        with testing_session_local() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client, testing_session_local
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def add_product(
    db,
    *,
    code: str,
    active: bool = True,
    category_id: int | None = None,
    warehouse_id: int | None = None,
    packagings: list[tuple[int | None, int]] | None = None,
    image_path: str | None = None,
    thumbnail_path: str | None = None,
) -> tuple[int, list[int]]:
    product = Product(
        product_code=code,
        is_active=active,
        category_id=category_id,
        warehouse_id=warehouse_id,
        image_path=image_path,
        thumbnail_path=thumbnail_path,
        size=f"测试商品 {code}",
        packagings=[
            ProductPackaging(packing_qty=packing_qty, carton_count=carton_count)
            for packing_qty, carton_count in (packagings or [])
        ],
    )
    db.add(product)
    db.flush()
    return product.id, [packaging.id for packaging in product.packagings]


def seed_primary_dashboard_data(session_local) -> dict[str, object]:
    with session_local.begin() as db:
        kitchen = Category(name="厨房用品", code="KITCHEN", sort_order=1)
        holiday = Category(name="节日装饰", code="HOLIDAY", sort_order=2)
        db.add_all([kitchen, holiday])
        db.flush()

        kitchen_child = Category(
            name="餐具",
            parent_id=kitchen.id,
            code="TABLEWARE",
            sort_order=1,
        )
        holiday_child = Category(
            name="节庆摆件",
            parent_id=holiday.id,
            code="ORNAMENT",
            sort_order=1,
        )
        db.add_all([kitchen_child, holiday_child])

        main_warehouse = Warehouse(name="主仓", sort_order=1)
        tiger_warehouse = Warehouse(name="虎跳仓", sort_order=2)
        empty_warehouse = Warehouse(name="空置仓", sort_order=3)
        db.add_all([main_warehouse, tiger_warehouse, empty_warehouse])
        db.flush()

        primary_id, primary_packaging_ids = add_product(
            db,
            code="01-01-001",
            category_id=kitchen_child.id,
            warehouse_id=main_warehouse.id,
            packagings=[(240, 20), (144, 3)],
            image_path="products/main.webp",
            thumbnail_path="products/thumb.webp",
        )
        zero_id, _ = add_product(
            db,
            code="01-02-001",
            category_id=holiday_child.id,
            warehouse_id=main_warehouse.id,
            packagings=[(240, 0), (144, 0)],
        )
        partial_id, _ = add_product(
            db,
            code="02-01-001",
            category_id=kitchen_child.id,
            warehouse_id=tiger_warehouse.id,
            packagings=[(240, 0), (144, 2)],
        )
        add_product(
            db,
            code="03-01-001",
            category_id=None,
            warehouse_id=tiger_warehouse.id,
            packagings=[(24, 1)],
        )
        empty_product_id, _ = add_product(
            db,
            code="04-01-001",
            category_id=holiday_child.id,
            warehouse_id=None,
            packagings=[],
        )
        inactive_id, inactive_packaging_ids = add_product(
            db,
            code="99-01-001",
            active=False,
            category_id=kitchen_child.id,
            warehouse_id=main_warehouse.id,
            packagings=[(240, 100)],
            image_path="products/inactive.webp",
            thumbnail_path="products/inactive-thumb.webp",
        )

    return {
        "primary_id": primary_id,
        "primary_packaging_id": primary_packaging_ids[0],
        "zero_id": zero_id,
        "partial_id": partial_id,
        "empty_product_id": empty_product_id,
        "inactive_id": inactive_id,
        "inactive_packaging_id": inactive_packaging_ids[0],
    }


def post_adjustment(
    client: TestClient,
    product_id: int,
    packaging_id: int,
    actual_carton_count: int,
) -> None:
    response = client.post(
        f"/api/products/{product_id}/stock/adjust",
        json={
            "product_packaging_id": packaging_id,
            "actual_carton_count": actual_carton_count,
            "remark": "Dashboard 测试调整",
        },
    )
    assert response.status_code == 201, response.text


def test_dashboard_summary_preserves_active_stock_and_distribution_metrics(
    dashboard_client,
) -> None:
    client, session_local = dashboard_client
    seeded = seed_primary_dashboard_data(session_local)
    primary_id = seeded["primary_id"]
    primary_packaging_id = seeded["primary_packaging_id"]
    inactive_id = seeded["inactive_id"]
    inactive_packaging_id = seeded["inactive_packaging_id"]

    post_adjustment(client, inactive_id, inactive_packaging_id, 101)
    post_adjustment(client, inactive_id, inactive_packaging_id, 100)
    response = client.post(
        f"/api/products/{primary_id}/stock/in",
        json={
            "product_packaging_id": primary_packaging_id,
            "quantity": 5,
            "remark": "Dashboard 测试入库",
        },
    )
    assert response.status_code == 201, response.text
    post_adjustment(client, inactive_id, inactive_packaging_id, 101)
    response = client.post(
        f"/api/products/{primary_id}/stock/out",
        json={
            "product_packaging_id": primary_packaging_id,
            "quantity": 2,
            "remark": "Dashboard 测试出库",
        },
    )
    assert response.status_code == 201, response.text
    post_adjustment(client, inactive_id, inactive_packaging_id, 100)
    post_adjustment(client, primary_id, primary_packaging_id, 30)

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {
        "active_product_count",
        "total_carton_count",
        "zero_stock_product_count",
        "category_distribution",
        "warehouse_distribution",
    }
    assert payload["active_product_count"] == 5
    assert payload["total_carton_count"] == 36
    assert payload["zero_stock_product_count"] == 2

    assert payload["category_distribution"] == [
        {"category_id": 1, "category_name": "厨房用品", "product_count": 2},
        {"category_id": 2, "category_name": "节日装饰", "product_count": 2},
        {"category_id": None, "category_name": "未分类", "product_count": 1},
    ]
    assert payload["warehouse_distribution"] == [
        {"warehouse_id": 1, "warehouse_name": "主仓", "carton_count": 33},
        {"warehouse_id": 2, "warehouse_name": "虎跳仓", "carton_count": 3},
        {"warehouse_id": 3, "warehouse_name": "空置仓", "carton_count": 0},
    ]


def test_dashboard_category_distribution_includes_all_eight_root_categories(
    dashboard_client,
) -> None:
    client, session_local = dashboard_client

    with session_local.begin() as db:
        for category_index in range(1, 9):
            parent = Category(
                name=f"一级分类{category_index}",
                code=f"ROOT{category_index}",
                sort_order=category_index,
            )
            db.add(parent)
            db.flush()
            child = Category(
                name=f"二级分类{category_index}",
                parent_id=parent.id,
                code=f"CHILD{category_index}",
                sort_order=1,
            )
            db.add(child)
            db.flush()
            for product_index in range(category_index):
                add_product(
                    db,
                    code=f"{category_index:02d}-{product_index:02d}",
                    category_id=child.id,
                    packagings=[(24, 1)],
                )

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200, response.text
    categories = response.json()["category_distribution"]
    assert len(categories) == 8
    assert [item["category_name"] for item in categories] == [
        f"一级分类{index}" for index in range(8, 0, -1)
    ]
    assert [item["product_count"] for item in categories] == list(range(8, 0, -1))
