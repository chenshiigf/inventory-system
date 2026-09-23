from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Warehouse


@pytest.fixture
def detail_context(tmp_path) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    database_path = tmp_path / "product-detail-test.db"
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
    with TestClient(app) as test_client:
        yield test_client, testing_session_local

    app.dependency_overrides.clear()
    engine.dispose()


def create_category_tree(client: TestClient) -> int:
    primary = client.post(
        "/api/categories",
        json={"name": "详情分类", "parent_id": None, "sort_order": 0},
    )
    assert primary.status_code == 201, primary.text
    child = client.post(
        "/api/categories",
        json={
            "name": "详情子类",
            "parent_id": primary.json()["id"],
            "sort_order": 0,
        },
    )
    assert child.status_code == 201, child.text
    return int(child.json()["id"])


def create_warehouse(session_local: sessionmaker) -> int:
    with session_local.begin() as db:
        warehouse = Warehouse(name="详情仓")
        db.add(warehouse)
        db.flush()
        return warehouse.id


def create_product(
    client: TestClient,
    category_id: int,
    warehouse_id: int,
) -> dict[str, object]:
    response = client.post(
        "/api/products",
        json={
            "category_id": category_id,
            "warehouse_id": warehouse_id,
            "image_path": "products/detail.webp",
            "thumbnail_path": "products/detail-thumb.webp",
            "size": "详情测试尺寸",
            "packagings": [
                {"packing_qty": 24, "carton_count": 3},
                {"packing_qty": None, "carton_count": 2},
            ],
            "unit": "pcs",
            "price": "5.20",
            "remark": "详情备注",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_detail_returns_display_names_multi_total_and_null_packaging(
    detail_context,
) -> None:
    client, session_local = detail_context
    category_id = create_category_tree(client)
    warehouse_id = create_warehouse(session_local)
    created = create_product(client, category_id, warehouse_id)

    response = client.get(f"/api/products/{created['id']}")

    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["category_name"] == "详情子类"
    assert detail["warehouse_name"] == "详情仓"
    assert detail["total_carton_count"] == 5
    assert [row["packing_qty"] for row in detail["packagings"]] == [24, None]


def test_inactive_product_detail_remains_available(detail_context) -> None:
    client, session_local = detail_context
    category_id = create_category_tree(client)
    warehouse_id = create_warehouse(session_local)
    created = create_product(client, category_id, warehouse_id)

    deactivated = client.post(f"/api/products/{created['id']}/deactivate")
    detail = client.get(f"/api/products/{created['id']}")

    assert deactivated.status_code == 200
    assert detail.status_code == 200
    assert detail.json()["is_active"] is False


def test_detail_uses_product_scoped_latest_movement_query(detail_context) -> None:
    client, session_local = detail_context
    category_id = create_category_tree(client)
    warehouse_id = create_warehouse(session_local)
    created = create_product(client, category_id, warehouse_id)
    packaging_id = created["packagings"][0]["id"]

    first = client.post(
        f"/api/products/{created['id']}/stock/in",
        json={"product_packaging_id": packaging_id, "quantity": 1},
    )
    second = client.post(
        f"/api/products/{created['id']}/stock/out",
        json={"product_packaging_id": packaging_id, "quantity": 1},
    )
    response = client.get(
        "/api/inventory-movements",
        params={"product_id": created["id"], "page": 1, "page_size": 5},
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 2
    assert data["items"][0]["id"] == second.json()["id"]
    assert data["items"][1]["id"] == first.json()["id"]


def test_missing_detail_returns_404(detail_context) -> None:
    client, _session_local = detail_context

    response = client.get("/api/products/999999")

    assert response.status_code == 404
