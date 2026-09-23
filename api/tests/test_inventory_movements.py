from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import InventoryMovement, Product, ProductPackaging, Warehouse
from app.services import inventory as inventory_service


@pytest.fixture
def database_client(tmp_path) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    database_path = tmp_path / "inventory-movements-test.db"
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


def product_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "warehouse_id": None,
        "image_path": None,
        "thumbnail_path": None,
        "size": "库存流水测试商品",
        "packagings": [{"packing_qty": 240, "carton_count": 3}],
        "unit": "pcs",
        "price": "2.80",
        "remark": None,
    }
    payload.update(overrides)
    return payload


def create_product(
    client: TestClient,
    *,
    packagings: list[dict[str, object]] | None = None,
    **overrides: object,
) -> dict[str, object]:
    if packagings is not None:
        overrides["packagings"] = packagings
    response = client.post("/api/products", json=product_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def stock_request(
    client: TestClient,
    product_id: int,
    direction: str,
    *,
    packaging_id: int | None = None,
    quantity: object = 1,
    **payload: object,
):
    if packaging_id is not None:
        payload["product_packaging_id"] = packaging_id
    payload["quantity"] = quantity
    return client.post(
        f"/api/products/{product_id}/stock/{direction}",
        json=payload,
    )


def test_stock_in_updates_stock_and_writes_snapshots(database_client) -> None:
    client, session_local = database_client
    product = create_product(client)
    packaging = product["packagings"][0]

    response = stock_request(
        client,
        product["id"],
        "in",
        packaging_id=packaging["id"],
        quantity=2,
        remark="新到货  第一批\n已验货",
    )

    assert response.status_code == 201, response.text
    movement = response.json()
    assert movement["movement_type"] == "IN"
    assert movement["quantity"] == 2
    assert movement["before_carton_count"] == 3
    assert movement["after_carton_count"] == 5
    assert movement["packing_qty_snapshot"] == 240
    assert movement["unit_snapshot"] == "pcs"
    assert movement["remark"] == "新到货  第一批\n已验货"
    assert client.get(f"/api/products/{product['id']}").json()["packagings"][0][
        "carton_count"
    ] == 5
    with session_local() as db:
        assert db.scalar(select(func.count(InventoryMovement.id))) == 1


def test_stock_out_updates_stock_and_writes_movement(database_client) -> None:
    client, _session_local = database_client
    product = create_product(client)
    packaging_id = product["packagings"][0]["id"]

    response = stock_request(
        client,
        product["id"],
        "out",
        packaging_id=packaging_id,
        quantity=2,
        remark="样品出库",
    )

    assert response.status_code == 201, response.text
    assert response.json()["movement_type"] == "OUT"
    assert response.json()["before_carton_count"] == 3
    assert response.json()["after_carton_count"] == 1
    assert client.get(f"/api/products/{product['id']}").json()["total_carton_count"] == 1


def test_stock_out_cannot_exceed_inventory_or_create_a_movement(database_client) -> None:
    client, session_local = database_client
    product = create_product(client)
    packaging_id = product["packagings"][0]["id"]

    response = stock_request(
        client,
        product["id"],
        "out",
        packaging_id=packaging_id,
        quantity=4,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "出库数不能超过当前包装库存。"
    assert client.get(f"/api/products/{product['id']}").json()["total_carton_count"] == 3
    with session_local() as db:
        assert db.scalar(select(func.count(InventoryMovement.id))) == 0


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "1", True, "abc"])
def test_stock_quantity_must_be_a_strict_positive_integer(
    database_client,
    quantity: object,
) -> None:
    client, _session_local = database_client
    product = create_product(client)

    response = stock_request(
        client,
        product["id"],
        "in",
        packaging_id=product["packagings"][0]["id"],
        quantity=quantity,
    )

    assert response.status_code == 422


@pytest.mark.parametrize("direction", ["in", "out"])
def test_inactive_product_rejects_stock_operations(database_client, direction: str) -> None:
    client, session_local = database_client
    product = create_product(client)
    packaging_id = product["packagings"][0]["id"]
    assert client.post(f"/api/products/{product['id']}/deactivate").status_code == 200

    response = stock_request(
        client,
        product["id"],
        direction,
        packaging_id=packaging_id,
        quantity=1,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "已停用商品不能进行入库或出库。"
    with session_local() as db:
        assert db.scalar(select(func.count(InventoryMovement.id))) == 0
        assert db.get(ProductPackaging, packaging_id).carton_count == 3


def test_nullable_packing_qty_can_be_stocked_in_and_out_by_id(database_client) -> None:
    client, _session_local = database_client
    product = create_product(
        client,
        packagings=[{"packing_qty": None, "carton_count": 2}],
    )
    packaging_id = product["packagings"][0]["id"]

    stock_in = stock_request(
        client,
        product["id"],
        "in",
        packaging_id=packaging_id,
        quantity=1,
    )
    stock_out = stock_request(
        client,
        product["id"],
        "out",
        packaging_id=packaging_id,
        quantity=2,
    )

    assert stock_in.status_code == 201
    assert stock_in.json()["packing_qty_snapshot"] is None
    assert stock_out.status_code == 201
    assert stock_out.json()["before_carton_count"] == 3
    assert stock_out.json()["after_carton_count"] == 1


def test_multiple_packaging_changes_only_the_selected_row(database_client) -> None:
    client, _session_local = database_client
    product = create_product(
        client,
        packagings=[
            {"packing_qty": 240, "carton_count": 20},
            {"packing_qty": 144, "carton_count": 3},
        ],
    )
    first_id, second_id = [row["id"] for row in product["packagings"]]

    response = stock_request(
        client,
        product["id"],
        "in",
        packaging_id=first_id,
        quantity=5,
    )

    assert response.status_code == 201
    saved = client.get(f"/api/products/{product['id']}").json()
    assert {row["id"]: row["carton_count"] for row in saved["packagings"]} == {
        first_id: 25,
        second_id: 3,
    }
    assert response.json()["before_carton_count"] == 20
    assert response.json()["after_carton_count"] == 25


def test_stock_in_creates_and_reuses_a_new_packaging_quantity(database_client) -> None:
    client, session_local = database_client
    product = create_product(client)

    first = stock_request(
        client,
        product["id"],
        "in",
        quantity=5,
        packing_qty=96,
    )
    second = stock_request(
        client,
        product["id"],
        "in",
        quantity=2,
        packing_qty=96,
    )

    assert first.status_code == 201
    assert first.json()["product_packaging_id"] not in {
        row["id"] for row in product["packagings"]
    }
    assert first.json()["before_carton_count"] == 0
    assert first.json()["after_carton_count"] == 5
    assert second.status_code == 201
    assert second.json()["before_carton_count"] == 5
    assert second.json()["after_carton_count"] == 7
    saved = client.get(f"/api/products/{product['id']}").json()
    assert [(row["packing_qty"], row["carton_count"]) for row in saved["packagings"]] == [
        (240, 3),
        (96, 7),
    ]
    with session_local() as db:
        assert db.scalar(
            select(func.count(ProductPackaging.id)).where(
                ProductPackaging.product_id == product["id"],
                ProductPackaging.packing_qty == 96,
            )
        ) == 1


def test_stock_out_requires_existing_packaging(database_client) -> None:
    client, session_local = database_client
    product = create_product(client)

    response = stock_request(
        client,
        product["id"],
        "out",
        quantity=1,
        packing_qty=96,
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "出库必须选择已有包装规格。"
    with session_local() as db:
        assert db.scalar(select(func.count(ProductPackaging.id))) == 1
        assert db.scalar(select(func.count(InventoryMovement.id))) == 0


def test_product_patch_cannot_change_cartons_but_can_change_packaging_data(
    database_client,
) -> None:
    client, _session_local = database_client
    product = create_product(client)
    packaging_id = product["packagings"][0]["id"]

    forbidden = client.patch(
        f"/api/products/{product['id']}",
        json={
            "packagings": [
                {"id": packaging_id, "packing_qty": 48, "carton_count": 100}
            ]
        },
    )
    assert forbidden.status_code == 422

    allowed = client.patch(
        f"/api/products/{product['id']}",
        json={"packagings": [{"id": packaging_id, "packing_qty": 48}]},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["packagings"][0]["packing_qty"] == 48
    assert allowed.json()["packagings"][0]["carton_count"] == 3


def test_new_product_without_initial_stock_starts_at_zero(database_client) -> None:
    client, _session_local = database_client
    product = create_product(
        client,
        packagings=[{"packing_qty": 240}],
    )

    assert product["total_carton_count"] == 0
    assert product["packagings"][0]["carton_count"] == 0


def test_movement_failure_rolls_back_carton_change(database_client, monkeypatch) -> None:
    _client, session_local = database_client
    with session_local.begin() as db:
        product = Product(
            size="事务回滚商品",
            unit="pcs",
            packagings=[ProductPackaging(packing_qty=24, carton_count=3)],
        )
        db.add(product)
        db.flush()
        product_id = product.id
        packaging_id = product.packagings[0].id

    def fail_movement(**_kwargs):
        raise RuntimeError("模拟流水写入失败")

    monkeypatch.setattr(inventory_service, "InventoryMovement", fail_movement)
    with session_local() as db:
        payload = inventory_service.StockMovementCreate(
            product_packaging_id=packaging_id,
            quantity=2,
        )
        with pytest.raises(RuntimeError, match="模拟流水写入失败"):
            inventory_service.create_stock_movement(
                db,
                product_id=product_id,
                direction="IN",
                payload=payload,
            )
        db.rollback()

    with session_local() as db:
        assert db.get(ProductPackaging, packaging_id).carton_count == 3
        assert db.scalar(select(func.count(InventoryMovement.id))) == 0


def test_query_supports_pagination_filters_and_descending_order(database_client) -> None:
    client, _session_local = database_client
    product = create_product(client, packagings=[{"packing_qty": 240, "carton_count": 0}])
    packaging_id = product["packagings"][0]["id"]
    for _ in range(21):
        response = stock_request(
            client,
            product["id"],
            "in",
            packaging_id=packaging_id,
            quantity=1,
        )
        assert response.status_code == 201

    first_page = client.get(
        "/api/inventory-movements",
        params={"product_id": product["id"], "page": 1, "page_size": 20},
    )
    assert first_page.status_code == 200
    assert first_page.json()["total"] == 21
    assert len(first_page.json()["items"]) == 20
    assert first_page.json()["items"][0]["id"] > first_page.json()["items"][-1][
        "id"
    ]

    by_packaging = client.get(
        "/api/inventory-movements",
        params={"product_packaging_id": packaging_id, "movement_type": "IN"},
    )
    assert by_packaging.status_code == 200
    assert by_packaging.json()["total"] == 21
    assert {item["movement_type"] for item in by_packaging.json()["items"]} == {"IN"}


def test_snapshot_survives_packaging_edit_and_product_deactivation(database_client) -> None:
    client, _session_local = database_client
    product = create_product(client)
    packaging = product["packagings"][0]
    movement = stock_request(
        client,
        product["id"],
        "in",
        packaging_id=packaging["id"],
        quantity=2,
    )
    assert movement.status_code == 201

    updated = client.patch(
        f"/api/products/{product['id']}",
        json={"packagings": [{"id": packaging["id"], "packing_qty": 96}]},
    )
    assert updated.status_code == 200
    assert updated.json()["packagings"][0]["carton_count"] == 5

    history = client.get(
        "/api/inventory-movements",
        params={"product_id": product["id"]},
    )
    assert history.status_code == 200
    assert history.json()["items"][0]["packing_qty_snapshot"] == 240

    assert client.post(f"/api/products/{product['id']}/deactivate").status_code == 200
    history_after_deactivate = client.get(
        "/api/inventory-movements",
        params={"product_id": product["id"]},
    )
    assert history_after_deactivate.json()["total"] == 1
