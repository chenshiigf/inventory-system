from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def client(tmp_path) -> Generator[TestClient, None, None]:
    database_path = tmp_path / "product-packagings-test.db"
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
        yield test_client

    app.dependency_overrides.clear()
    engine.dispose()


def product_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category_id": None,
        "warehouse_id": None,
        "image_path": None,
        "thumbnail_path": None,
        "size": "包装测试商品",
        "packagings": [{"packing_qty": 24, "carton_count": 10}],
        "unit": "pcs",
        "price": "2.80",
        "remark": "包装规格回归",
    }
    payload.update(overrides)
    return payload


def create_product(client: TestClient, **overrides: object) -> dict[str, object]:
    response = client.post("/api/products", json=product_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def packaging_write(packaging: dict[str, object]) -> dict[str, object]:
    return {
        "id": packaging["id"],
        "packing_qty": packaging["packing_qty"],
        "carton_count": packaging["carton_count"],
    }


def test_create_single_packaging_returns_nested_row_and_total(client: TestClient) -> None:
    product = create_product(client)

    assert product["packagings"] == [
        {
            "id": product["packagings"][0]["id"],
            "packing_qty": 24,
            "carton_count": 10,
            "sort_order": 0,
        }
    ]
    assert product["total_carton_count"] == 10
    assert "packing_qty" not in product
    assert "carton_count" not in product


def test_create_multiple_packagings_preserves_client_order(client: TestClient) -> None:
    product = create_product(
        client,
        packagings=[
            {"packing_qty": 240, "carton_count": 21},
            {"packing_qty": 144, "carton_count": 1},
        ],
    )

    assert [row["packing_qty"] for row in product["packagings"]] == [240, 144]
    assert [row["sort_order"] for row in product["packagings"]] == [0, 1]
    assert product["total_carton_count"] == 22


def test_create_requires_at_least_one_packaging(client: TestClient) -> None:
    response = client.post("/api/products", json=product_payload(packagings=[]))

    assert response.status_code == 422


def test_create_rejects_missing_packagings(client: TestClient) -> None:
    payload = product_payload()
    payload.pop("packagings")

    response = client.post("/api/products", json=payload)

    assert response.status_code == 422


def test_create_rejects_legacy_top_level_packaging_fields(client: TestClient) -> None:
    payload = product_payload()
    payload["packing_qty"] = 24
    payload["carton_count"] = 10

    response = client.post("/api/products", json=payload)

    assert response.status_code == 422


def test_create_rejects_nonpositive_packing_quantity(client: TestClient) -> None:
    response = client.post(
        "/api/products",
        json=product_payload(packagings=[{"packing_qty": 0, "carton_count": 1}]),
    )

    assert response.status_code == 422


def test_create_rejects_negative_carton_count(client: TestClient) -> None:
    response = client.post(
        "/api/products",
        json=product_payload(packagings=[{"packing_qty": 24, "carton_count": -1}]),
    )

    assert response.status_code == 422


def test_create_rejects_duplicate_packing_quantity(client: TestClient) -> None:
    response = client.post(
        "/api/products",
        json=product_payload(
            packagings=[
                {"packing_qty": 24, "carton_count": 2},
                {"packing_qty": 24, "carton_count": 3},
            ]
        ),
    )

    assert response.status_code == 422


def test_create_rejects_client_sort_order_field(client: TestClient) -> None:
    response = client.post(
        "/api/products",
        json=product_payload(
            packagings=[{"packing_qty": 24, "carton_count": 2, "sort_order": 0}]
        ),
    )

    assert response.status_code == 422


def test_get_product_returns_packagings_and_computed_total(client: TestClient) -> None:
    created = create_product(
        client,
        packagings=[
            {"packing_qty": 240, "carton_count": 21},
            {"packing_qty": 144, "carton_count": 1},
        ],
    )

    response = client.get(f"/api/products/{created['id']}")

    assert response.status_code == 200
    assert response.json()["packagings"] == created["packagings"]
    assert response.json()["total_carton_count"] == 22


def test_update_can_add_packaging(client: TestClient) -> None:
    created = create_product(client)
    original = created["packagings"][0]

    response = client.patch(
        f"/api/products/{created['id']}",
        json={
            "packagings": [
                {
                    "id": original["id"],
                    "packing_qty": 24,
                    "carton_count": 10,
                },
                {"packing_qty": 144, "carton_count": 1},
            ]
        },
    )

    assert response.status_code == 200
    assert [(row["packing_qty"], row["carton_count"]) for row in response.json()["packagings"]] == [
        (24, 10),
        (144, 1),
    ]
    assert response.json()["total_carton_count"] == 11


def test_update_can_edit_packaging(client: TestClient) -> None:
    created = create_product(client)
    packaging_id = created["packagings"][0]["id"]

    response = client.patch(
        f"/api/products/{created['id']}",
        json={
            "packagings": [
                {"id": packaging_id, "packing_qty": 48, "carton_count": 7}
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["packagings"][0]["packing_qty"] == 48
    assert response.json()["total_carton_count"] == 7


def test_update_can_delete_zero_stock_packaging(client: TestClient) -> None:
    created = create_product(
        client,
        packagings=[
            {"packing_qty": 24, "carton_count": 10},
            {"packing_qty": 144, "carton_count": 0},
        ],
    )

    response = client.patch(
        f"/api/products/{created['id']}",
        json={"packagings": [packaging_write(created["packagings"][0])]},
    )

    assert response.status_code == 200
    assert [row["packing_qty"] for row in response.json()["packagings"]] == [24]


def test_update_can_delete_stocked_packaging_after_ui_confirmation(client: TestClient) -> None:
    created = create_product(
        client,
        packagings=[
            {"packing_qty": 24, "carton_count": 10},
            {"packing_qty": 144, "carton_count": 1},
        ],
    )

    response = client.patch(
        f"/api/products/{created['id']}",
        json={"packagings": [packaging_write(created["packagings"][0])]},
    )

    assert response.status_code == 200
    assert response.json()["total_carton_count"] == 10


def test_update_rejects_empty_final_packaging_list(client: TestClient) -> None:
    created = create_product(client)

    response = client.patch(
        f"/api/products/{created['id']}",
        json={"packagings": []},
    )

    assert response.status_code == 422


def test_update_rejects_duplicate_final_packing_quantity(client: TestClient) -> None:
    created = create_product(client)

    response = client.patch(
        f"/api/products/{created['id']}",
        json={
            "packagings": [
                {"packing_qty": 24, "carton_count": 1},
                {"packing_qty": 24, "carton_count": 2},
            ]
        },
    )

    assert response.status_code == 422


def test_update_rejects_other_products_packaging_id(client: TestClient) -> None:
    first = create_product(client, size="第一商品")
    second = create_product(client, size="第二商品")
    second_packaging = second["packagings"][0]

    response = client.patch(
        f"/api/products/{first['id']}",
        json={"packagings": [packaging_write(second_packaging)]},
    )

    assert response.status_code == 422
    unchanged = client.get(f"/api/products/{first['id']}").json()
    assert unchanged["packagings"][0]["id"] == first["packagings"][0]["id"]


def test_update_rejects_unknown_packaging_id(client: TestClient) -> None:
    created = create_product(client)

    response = client.patch(
        f"/api/products/{created['id']}",
        json={"packagings": [{"id": 99999, "packing_qty": 24, "carton_count": 1}]},
    )

    assert response.status_code == 422


def test_update_without_packagings_preserves_rows(client: TestClient) -> None:
    created = create_product(
        client,
        packagings=[
            {"packing_qty": 24, "carton_count": 10},
            {"packing_qty": 144, "carton_count": 1},
        ],
    )

    response = client.patch(
        f"/api/products/{created['id']}",
        json={"remark": "只改备注"},
    )

    assert response.status_code == 200
    assert response.json()["packagings"] == created["packagings"]
    assert response.json()["remark"] == "只改备注"


def test_update_reorders_rows_by_payload_order(client: TestClient) -> None:
    created = create_product(
        client,
        packagings=[
            {"packing_qty": 24, "carton_count": 10},
            {"packing_qty": 144, "carton_count": 1},
        ],
    )
    first, second = created["packagings"]

    response = client.patch(
        f"/api/products/{created['id']}",
        json={
            "packagings": [packaging_write(second), packaging_write(first)]
        },
    )

    assert response.status_code == 200
    assert [row["packing_qty"] for row in response.json()["packagings"]] == [144, 24]
    assert [row["sort_order"] for row in response.json()["packagings"]] == [0, 1]


def test_update_does_not_change_product_identity(client: TestClient) -> None:
    created = create_product(client)
    response = client.patch(
        f"/api/products/{created['id']}",
        json={"packagings": [{"packing_qty": 48, "carton_count": 3}]},
    )

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["product_code"] == created["product_code"]


def test_product_pagination_has_no_duplicate_rows(client: TestClient) -> None:
    for index in range(5):
        create_product(
            client,
            size=f"分页商品 {index}",
            packagings=[
                {"packing_qty": 24, "carton_count": index},
                {"packing_qty": 144, "carton_count": 1},
            ],
        )

    first_page = client.get("/api/products?page=1&page_size=3").json()
    second_page = client.get("/api/products?page=2&page_size=3").json()

    first_ids = [item["id"] for item in first_page["items"]]
    second_ids = [item["id"] for item in second_page["items"]]
    assert first_page["total"] == 5
    assert len(first_ids) == 3
    assert len(second_ids) == 2
    assert set(first_ids).isdisjoint(second_ids)


def test_search_still_matches_product_text(client: TestClient) -> None:
    create_product(client, size="蓝边 240", remark="尾货搜索目标")
    create_product(client, size="白色 144", remark="其他商品")

    response = client.get("/api/products?search=尾货搜索目标")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["remark"] == "尾货搜索目标"


@pytest.mark.parametrize(
    "payload, expected_status",
    [
        ({"packagings": [{"packing_qty": 1.5, "carton_count": 0}]}, 422),
        ({"packagings": [{"packing_qty": 1, "carton_count": 1.5}]}, 422),
        ({"packagings": [{"packing_qty": True, "carton_count": 0}]}, 422),
    ],
)
def test_packaging_counts_are_strict_integers(
    client: TestClient,
    payload: dict[str, object],
    expected_status: int,
) -> None:
    response = client.post("/api/products", json=product_payload(**payload))

    assert response.status_code == expected_status
