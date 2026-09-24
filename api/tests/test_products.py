from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    database_path = tmp_path / "products-test.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    engine.dispose()


def product_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "image_path": None,
        "size": "18 × 18 cm",
        "packagings": [{"packing_qty": 24, "carton_count": 18}],
        "unit": "pcs",
        "price": "2.80",
        "remark": "蓝边方盘",
    }
    payload.update(overrides)
    return payload


def create_category_pair(client: TestClient, suffix: str = "") -> tuple[int, int]:
    parent = client.post(
        "/api/categories",
        json={"name": f"批量一级{suffix}", "parent_id": None},
    )
    assert parent.status_code == 201, parent.text
    child = client.post(
        "/api/categories",
        json={"name": f"批量二级{suffix}", "parent_id": parent.json()["id"]},
    )
    assert child.status_code == 201, child.text
    return parent.json()["id"], child.json()["id"]


def create_batch_product(
    client: TestClient,
    category_id: int,
    *,
    carton_count: int = 18,
) -> dict[str, object]:
    response = client.post(
        "/api/products",
        json=product_payload(
            category_id=category_id,
            packagings=[{"packing_qty": 24, "carton_count": carton_count}],
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_create_product_succeeds_with_exact_decimal_price(client: TestClient) -> None:
    response = client.post("/api/products", json=product_payload(price="2.80"))

    assert response.status_code == 201
    product = response.json()
    assert product["id"] > 0
    assert product["price"] == "2.80"
    assert product["unit"] == "pcs"
    assert product["image_path"] is None
    assert product["created_at"]
    assert product["updated_at"]


def test_product_unit_must_be_pcs_or_set(client: TestClient) -> None:
    response = client.post("/api/products", json=product_payload(unit="box"))

    assert response.status_code == 422


def test_carton_count_cannot_be_negative(client: TestClient) -> None:
    response = client.post(
        "/api/products",
        json=product_payload(packagings=[{"packing_qty": 24, "carton_count": -1}]),
    )

    assert response.status_code == 422


def test_product_list_is_paginated(client: TestClient) -> None:
    for index in range(3):
        response = client.post(
            "/api/products",
            json=product_payload(size=f"尺寸 {index}"),
        )
        assert response.status_code == 201

    response = client.get("/api/products?page=2&page_size=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["page"] == 2
    assert payload["page_size"] == 2
    assert len(payload["items"]) == 1


def test_product_list_rejects_page_size_over_100(client: TestClient) -> None:
    response = client.get("/api/products?page_size=101")

    assert response.status_code == 422


def test_product_search_matches_size_and_remark(client: TestClient) -> None:
    client.post(
        "/api/products",
        json=product_payload(size="18 cm", remark="蓝边方盘"),
    )
    client.post(
        "/api/products",
        json=product_payload(size="24 cm", remark="白色圆盘"),
    )

    response = client.get("/api/products?search=蓝边")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["remark"] == "蓝边方盘"


def test_missing_product_returns_404(client: TestClient) -> None:
    response = client.get("/api/products/999")

    assert response.status_code == 404


def test_patch_product_succeeds(client: TestClient) -> None:
    created = client.post("/api/products", json=product_payload())
    product_id = created.json()["id"]

    response = client.patch(
        f"/api/products/{product_id}",
        json={"price": "3.15", "remark": "调整后的备注"},
    )

    assert response.status_code == 200
    product = response.json()
    assert product["price"] == "3.15"
    assert product["remark"] == "调整后的备注"
    assert product["total_carton_count"] == 18
    assert product["packagings"][0]["carton_count"] == 18


@pytest.mark.parametrize(
    "image_path",
    ["../outside.png", "data:image/png;base64,abc"],
)
def test_image_path_rejects_traversal_and_base64(
    client: TestClient,
    image_path: str,
) -> None:
    response = client.post(
        "/api/products",
        json=product_payload(image_path=image_path),
    )

    assert response.status_code == 422


def test_batch_category_updates_products_without_changing_codes_or_stock(
    client: TestClient,
) -> None:
    _parent_id, source_category_id = create_category_pair(client, "分类源")
    _target_parent_id, target_category_id = create_category_pair(client, "分类目标")
    first = create_batch_product(client, source_category_id, carton_count=18)
    second = create_batch_product(client, source_category_id, carton_count=7)
    original_codes = {first["id"]: first["product_code"], second["id"]: second["product_code"]}

    response = client.post(
        "/api/products/batch/category",
        json={
            "product_ids": [first["id"], first["id"], second["id"]],
            "category_id": target_category_id,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"updated_count": 2}
    for product_id, carton_count in [(first["id"], 18), (second["id"], 7)]:
        product = client.get(f"/api/products/{product_id}").json()
        assert product["category_id"] == target_category_id
        assert product["product_code"] == original_codes[product_id]
        assert product["total_carton_count"] == carton_count


def test_batch_category_allows_active_and_inactive_products(client: TestClient) -> None:
    _parent_id, source_category_id = create_category_pair(client, "混合源")
    _target_parent_id, target_category_id = create_category_pair(client, "混合目标")
    active = create_batch_product(client, source_category_id)
    inactive = create_batch_product(client, source_category_id)
    deactivated = client.post(f"/api/products/{inactive['id']}/deactivate")
    assert deactivated.status_code == 200

    response = client.post(
        "/api/products/batch/category",
        json={"product_ids": [active["id"], inactive["id"]], "category_id": target_category_id},
    )

    assert response.status_code == 200
    assert client.get(f"/api/products/{active['id']}").json()["category_id"] == target_category_id
    assert client.get(f"/api/products/{inactive['id']}").json()["category_id"] == target_category_id


def test_batch_category_rejects_parent_category_and_missing_product_atomically(
    client: TestClient,
) -> None:
    parent_id, source_category_id = create_category_pair(client, "原子源")
    _target_parent_id, target_category_id = create_category_pair(client, "原子目标")
    product = create_batch_product(client, source_category_id)

    parent_response = client.post(
        "/api/products/batch/category",
        json={"product_ids": [product["id"]], "category_id": parent_id},
    )
    assert parent_response.status_code == 422
    assert parent_response.json()["detail"] == "请选择二级分类"

    missing_response = client.post(
        "/api/products/batch/category",
        json={
            "product_ids": [product["id"], 999999],
            "category_id": target_category_id,
        },
    )
    assert missing_response.status_code == 404
    assert client.get(f"/api/products/{product['id']}").json()["category_id"] == source_category_id


def test_batch_requests_reject_empty_invalid_and_over_limit_ids(client: TestClient) -> None:
    for product_ids in ([], [0], list(range(1, 102))):
        response = client.post(
            "/api/products/batch/deactivate",
            json={"product_ids": product_ids},
        )
        assert response.status_code == 422


def test_batch_deactivate_preserves_stock_and_creates_no_movement(
    client: TestClient,
) -> None:
    _parent_id, category_id = create_category_pair(client, "停用")
    first = create_batch_product(client, category_id, carton_count=3)
    second = create_batch_product(client, category_id, carton_count=5)

    response = client.post(
        "/api/products/batch/deactivate",
        json={"product_ids": [first["id"], second["id"]]},
    )

    assert response.status_code == 200
    assert response.json() == {"updated_count": 2}
    for product_id, carton_count in [(first["id"], 3), (second["id"], 5)]:
        product = client.get(f"/api/products/{product_id}").json()
        assert product["is_active"] is False
        assert product["total_carton_count"] == carton_count
        movements = client.get(
            f"/api/inventory-movements?product_id={product_id}"
        ).json()
        assert movements["total"] == 0


def test_batch_deactivate_rejects_inactive_product_atomically(client: TestClient) -> None:
    _parent_id, category_id = create_category_pair(client, "停用原子")
    active = create_batch_product(client, category_id)
    inactive = create_batch_product(client, category_id)
    assert client.post(f"/api/products/{inactive['id']}/deactivate").status_code == 200

    response = client.post(
        "/api/products/batch/deactivate",
        json={"product_ids": [active["id"], inactive["id"]]},
    )

    assert response.status_code == 409
    assert client.get(f"/api/products/{active['id']}").json()["is_active"] is True
    assert client.get(f"/api/products/{inactive['id']}").json()["is_active"] is False


def test_batch_activate_updates_only_inactive_products(client: TestClient) -> None:
    _parent_id, category_id = create_category_pair(client, "启用")
    first = create_batch_product(client, category_id)
    second = create_batch_product(client, category_id)
    assert client.post(f"/api/products/{first['id']}/deactivate").status_code == 200
    assert client.post(f"/api/products/{second['id']}/deactivate").status_code == 200

    response = client.post(
        "/api/products/batch/activate",
        json={"product_ids": [first["id"], second["id"]]},
    )

    assert response.status_code == 200
    assert response.json() == {"updated_count": 2}
    assert client.get(f"/api/products/{first['id']}").json()["is_active"] is True
    assert client.get(f"/api/products/{second['id']}").json()["is_active"] is True


def test_batch_activate_rejects_active_product_atomically(client: TestClient) -> None:
    _parent_id, category_id = create_category_pair(client, "启用原子")
    active = create_batch_product(client, category_id)
    inactive = create_batch_product(client, category_id)
    assert client.post(f"/api/products/{inactive['id']}/deactivate").status_code == 200

    response = client.post(
        "/api/products/batch/activate",
        json={"product_ids": [active["id"], inactive["id"]]},
    )

    assert response.status_code == 409
    assert client.get(f"/api/products/{active['id']}").json()["is_active"] is True
    assert client.get(f"/api/products/{inactive['id']}").json()["is_active"] is False
