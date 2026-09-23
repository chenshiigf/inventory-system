from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Category, Product, ProductPackaging, Warehouse


@pytest.fixture
def lifecycle_context(
    tmp_path,
) -> Generator[tuple[TestClient, sessionmaker, dict[str, int]], None, None]:
    database_path = tmp_path / "product-lifecycle-test.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with session_factory.begin() as db:
        main_warehouse = Warehouse(name="主仓", sort_order=0)
        tiger_warehouse = Warehouse(name="虎跳仓", sort_order=1)
        kitchen = Category(
            name="厨房用品",
            code="01",
            sort_order=0,
            next_product_sequence=None,
        )
        christmas = Category(
            name="圣诞系列",
            code="02",
            sort_order=1,
            next_product_sequence=None,
        )
        db.add_all([main_warehouse, tiger_warehouse, kitchen, christmas])
        db.flush()
        plate = Category(
            name="盘子",
            parent_id=kitchen.id,
            code="01",
            next_product_sequence=1,
        )
        bowl = Category(
            name="碗",
            parent_id=kitchen.id,
            code="02",
            next_product_sequence=1,
        )
        bulb = Category(
            name="水晶球",
            parent_id=christmas.id,
            code="01",
            next_product_sequence=1,
        )
        db.add_all([plate, bowl, bulb])
        db.flush()
        ids = {
            "main_warehouse": main_warehouse.id,
            "tiger_warehouse": tiger_warehouse.id,
            "plate": plate.id,
            "bowl": bowl.id,
            "bulb": bulb.id,
        }

    def override_get_db():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, session_factory, ids
    app.dependency_overrides.clear()
    engine.dispose()


def product_payload(ids: dict[str, int], **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category_id": ids["plate"],
        "warehouse_id": ids["main_warehouse"],
        "image_path": None,
        "size": "20 cm",
        "packagings": [{"packing_qty": 24, "carton_count": 0}],
        "unit": "pcs",
        "price": "1.00",
        "remark": "测试商品",
    }
    payload.update(overrides)
    return payload


def create_product(
    client: TestClient,
    ids: dict[str, int],
    **overrides: object,
) -> dict[str, object]:
    response = client.post("/api/products", json=product_payload(ids, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def test_new_product_defaults_to_active(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context

    product = create_product(client, ids)

    assert product["is_active"] is True


def test_deactivate_allows_stock_and_preserves_identity_packaging_and_cartons(
    lifecycle_context,
) -> None:
    client, session_factory, ids = lifecycle_context
    product = create_product(
        client,
        ids,
        packagings=[
            {"packing_qty": 24, "carton_count": 6},
            {"packing_qty": 12, "carton_count": 2},
        ],
    )
    product_id = int(product["id"])
    product_code = product["product_code"]

    response = client.post(f"/api/products/{product_id}/deactivate")

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert response.json()["product_code"] == product_code
    assert response.json()["total_carton_count"] == 8
    assert [
        (packaging["packing_qty"], packaging["carton_count"])
        for packaging in response.json()["packagings"]
    ] == [(24, 6), (12, 2)]
    with session_factory() as db:
        assert db.scalar(select(func.count(ProductPackaging.id))) == 2
        saved = db.get(Product, product_id)
        assert saved is not None
        assert saved.is_active is False
        assert saved.product_code == product_code
        assert saved.total_carton_count == 8


def test_activate_restores_active_status_and_default_listing(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    product = create_product(client, ids)
    product_id = int(product["id"])
    assert client.post(f"/api/products/{product_id}/deactivate").status_code == 200

    response = client.post(f"/api/products/{product_id}/activate")

    assert response.status_code == 200
    assert response.json()["is_active"] is True
    active = client.get("/api/products")
    assert [item["id"] for item in active.json()["items"]] == [product_id]


def test_default_listing_only_returns_active_products(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    active = create_product(client, ids, size="在用商品")
    inactive = create_product(client, ids, size="已停用商品")
    assert client.post(f"/api/products/{inactive['id']}/deactivate").status_code == 200

    response = client.get("/api/products")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [active["id"]]


def test_inactive_and_all_status_filters(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    active = create_product(client, ids, size="在用商品")
    inactive = create_product(client, ids, size="已停用商品")
    assert client.post(f"/api/products/{inactive['id']}/deactivate").status_code == 200

    inactive_response = client.get("/api/products?status=inactive")
    all_response = client.get("/api/products?status=all")

    assert [item["id"] for item in inactive_response.json()["items"]] == [inactive["id"]]
    assert {item["id"] for item in all_response.json()["items"]} == {
        active["id"],
        inactive["id"],
    }


def test_status_combines_with_warehouse_filter(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    target = create_product(
        client,
        ids,
        warehouse_id=ids["tiger_warehouse"],
        size="停用仓库目标",
    )
    other = create_product(
        client,
        ids,
        warehouse_id=ids["main_warehouse"],
        size="停用仓库其他",
    )
    assert client.post(f"/api/products/{target['id']}/deactivate").status_code == 200
    assert client.post(f"/api/products/{other['id']}/deactivate").status_code == 200

    response = client.get(
        "/api/products",
        params={"status": "inactive", "warehouse_id": ids["tiger_warehouse"]},
    )

    assert [item["id"] for item in response.json()["items"]] == [target["id"]]


def test_status_combines_with_category_filter(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    target = create_product(client, ids, category_id=ids["bowl"], size="停用分类目标")
    other = create_product(client, ids, category_id=ids["plate"], size="停用分类其他")
    assert client.post(f"/api/products/{target['id']}/deactivate").status_code == 200
    assert client.post(f"/api/products/{other['id']}/deactivate").status_code == 200

    response = client.get(
        "/api/products",
        params={"status": "inactive", "category_id": ids["bowl"]},
    )

    assert [item["id"] for item in response.json()["items"]] == [target["id"]]


def test_status_combines_with_search_filter(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    target = create_product(client, ids, size="停用搜索目标")
    other = create_product(client, ids, size="停用搜索其他")
    assert client.post(f"/api/products/{target['id']}/deactivate").status_code == 200
    assert client.post(f"/api/products/{other['id']}/deactivate").status_code == 200

    response = client.get(
        "/api/products",
        params={"status": "inactive", "search": "搜索目标"},
    )

    assert [item["id"] for item in response.json()["items"]] == [target["id"]]


def test_status_filter_supports_pagination(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    products = [create_product(client, ids, size=f"停用分页-{index}") for index in range(3)]
    for product in products:
        assert client.post(f"/api/products/{product['id']}/deactivate").status_code == 200

    response = client.get(
        "/api/products",
        params={"status": "inactive", "page": 2, "page_size": 1},
    )

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert response.json()["page"] == 2
    assert len(response.json()["items"]) == 1


def test_inactive_product_detail_and_edit_are_still_available(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    product = create_product(client, ids)
    product_id = int(product["id"])
    assert client.post(f"/api/products/{product_id}/deactivate").status_code == 200

    detail = client.get(f"/api/products/{product_id}")
    edited = client.patch(
        f"/api/products/{product_id}",
        json={"remark": "停用后仍可编辑"},
    )

    assert detail.status_code == 200
    assert detail.json()["is_active"] is False
    assert edited.status_code == 200
    assert edited.json()["is_active"] is False
    assert edited.json()["remark"] == "停用后仍可编辑"


def test_deactivate_does_not_add_delete_endpoint(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    product = create_product(client, ids)

    response = client.delete(f"/api/products/{product['id']}")

    assert response.status_code in {404, 405}


def test_product_code_is_not_reused_after_deactivation(lifecycle_context) -> None:
    client, _session_factory, ids = lifecycle_context
    first = create_product(client, ids)
    assert client.post(f"/api/products/{first['id']}/deactivate").status_code == 200

    second = create_product(client, ids)

    assert first["product_code"] == "01-01-001"
    assert second["product_code"] == "01-01-002"


def test_existing_product_rows_created_by_current_schema_are_active(lifecycle_context) -> None:
    client, session_factory, ids = lifecycle_context
    product = create_product(client, ids)

    with session_factory() as db:
        saved = db.get(Product, int(product["id"]))
        assert saved is not None
        assert saved.is_active is True
