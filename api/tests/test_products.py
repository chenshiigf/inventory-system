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
