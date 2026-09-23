from collections.abc import Generator
from datetime import datetime

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


def create_warehouse(session_local, name: str) -> int:
    with session_local.begin() as db:
        warehouse = Warehouse(name=name)
        db.add(warehouse)
        db.flush()
        return warehouse.id


def set_product_query_fields(
    session_local,
    product_id: int,
    *,
    product_code: str,
    warehouse_id: int | None = None,
) -> None:
    with session_local.begin() as db:
        product = db.get(Product, product_id)
        assert product is not None
        product.product_code = product_code
        if warehouse_id is not None:
            product.warehouse_id = warehouse_id


def set_movement_created_at(session_local, movement_id: int, value: datetime) -> None:
    with session_local.begin() as db:
        movement = db.get(InventoryMovement, movement_id)
        assert movement is not None
        movement.created_at = value


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


def adjustment_request(
    client: TestClient,
    product_id: int,
    packaging_id: int,
    actual_carton_count: object,
    *,
    remark: object = "盘点纠正",
    **payload: object,
):
    payload.update(
        {
            "product_packaging_id": packaging_id,
            "actual_carton_count": actual_carton_count,
            "remark": remark,
        }
    )
    return client.post(
        f"/api/products/{product_id}/stock/adjust",
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


def test_query_returns_display_fields_and_latest_first(database_client) -> None:
    client, session_local = database_client
    warehouse_id = create_warehouse(session_local, "主仓")
    product = create_product(
        client,
        warehouse_id=warehouse_id,
        image_path="products/main.webp",
        thumbnail_path="products/thumb.webp",
        packagings=[{"packing_qty": 240, "carton_count": 0}],
    )
    set_product_query_fields(
        session_local,
        product["id"],
        product_code="01-03-019",
        warehouse_id=warehouse_id,
    )
    packaging_id = product["packagings"][0]["id"]

    first = stock_request(
        client,
        product["id"],
        "in",
        packaging_id=packaging_id,
        quantity=2,
        remark="先入库",
    )
    second = stock_request(
        client,
        product["id"],
        "out",
        packaging_id=packaging_id,
        quantity=1,
        remark="后出库",
    )
    assert first.status_code == 201
    assert second.status_code == 201

    response = client.get("/api/inventory-movements")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total"] == 2
    assert [item["id"] for item in data["items"]] == [
        second.json()["id"],
        first.json()["id"],
    ]
    latest = data["items"][0]
    assert latest["product_code"] == "01-03-019"
    assert latest["image_path"] == "products/main.webp"
    assert latest["thumbnail_path"] == "products/thumb.webp"
    assert latest["warehouse_id"] == warehouse_id
    assert latest["warehouse_name"] == "主仓"


def test_query_search_and_warehouse_use_movement_snapshot(database_client) -> None:
    client, session_local = database_client
    main_id = create_warehouse(session_local, "主仓")
    tiger_id = create_warehouse(session_local, "虎跳仓")
    product = create_product(
        client,
        warehouse_id=main_id,
        packagings=[{"packing_qty": 240, "carton_count": 0}],
    )
    set_product_query_fields(
        session_local,
        product["id"],
        product_code="01-03-019",
        warehouse_id=main_id,
    )
    movement = stock_request(
        client,
        product["id"],
        "in",
        packaging_id=product["packagings"][0]["id"],
        quantity=1,
    )
    assert movement.status_code == 201

    with session_local.begin() as db:
        saved_product = db.get(Product, product["id"])
        assert saved_product is not None
        saved_product.warehouse_id = tiger_id

    by_code = client.get(
        "/api/inventory-movements",
        params={"search": "01-03"},
    )
    by_snapshot_warehouse = client.get(
        "/api/inventory-movements",
        params={"search": "019", "warehouse_id": main_id},
    )
    wrong_current_warehouse = client.get(
        "/api/inventory-movements",
        params={"warehouse_id": tiger_id},
    )

    assert by_code.status_code == 200
    assert by_code.json()["total"] == 1
    assert by_code.json()["items"][0]["product_code"] == "01-03-019"
    assert by_snapshot_warehouse.status_code == 200
    assert by_snapshot_warehouse.json()["total"] == 1
    assert by_snapshot_warehouse.json()["items"][0]["warehouse_name"] == "主仓"
    assert wrong_current_warehouse.status_code == 200
    assert wrong_current_warehouse.json()["total"] == 0


def test_query_date_range_includes_the_complete_end_date(database_client) -> None:
    client, session_local = database_client
    product = create_product(
        client,
        packagings=[{"packing_qty": 240, "carton_count": 0}],
    )
    packaging_id = product["packagings"][0]["id"]
    movements = []
    for _ in range(3):
        response = stock_request(
            client,
            product["id"],
            "in",
            packaging_id=packaging_id,
            quantity=1,
        )
        assert response.status_code == 201
        movements.append(response.json()["id"])

    set_movement_created_at(
        session_local,
        movements[0],
        datetime(2026, 9, 20, 8, 0),
    )
    set_movement_created_at(
        session_local,
        movements[1],
        datetime(2026, 9, 23, 23, 59, 59),
    )
    set_movement_created_at(
        session_local,
        movements[2],
        datetime(2026, 9, 24, 0, 0),
    )

    response = client.get(
        "/api/inventory-movements",
        params={
            "start_date": "2026-09-20",
            "end_date": "2026-09-23",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert {item["id"] for item in response.json()["items"]} == set(movements[:2])


def test_query_combines_search_type_warehouse_date_and_pagination(database_client) -> None:
    client, session_local = database_client
    warehouse_id = create_warehouse(session_local, "主仓")
    product = create_product(
        client,
        warehouse_id=warehouse_id,
        packagings=[{"packing_qty": 240, "carton_count": 0}],
    )
    set_product_query_fields(
        session_local,
        product["id"],
        product_code="01-03-019",
        warehouse_id=warehouse_id,
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
        quantity=1,
    )
    first_adjustment = adjustment_request(
        client,
        product["id"],
        packaging_id,
        3,
        remark="第一次盘点",
    )
    second_adjustment = adjustment_request(
        client,
        product["id"],
        packaging_id,
        2,
        remark="第二次盘点",
    )
    assert stock_in.status_code == 201
    assert stock_out.status_code == 201
    assert first_adjustment.status_code == 201
    assert second_adjustment.status_code == 201

    set_movement_created_at(
        session_local,
        first_adjustment.json()["id"],
        datetime(2026, 9, 22, 10, 0),
    )
    set_movement_created_at(
        session_local,
        second_adjustment.json()["id"],
        datetime(2026, 9, 23, 11, 0),
    )

    params = {
        "search": "01-03",
        "movement_type": "ADJUST",
        "warehouse_id": warehouse_id,
        "start_date": "2026-09-22",
        "end_date": "2026-09-23",
        "page": 1,
        "page_size": 1,
    }
    first_page = client.get("/api/inventory-movements", params=params)
    second_page = client.get(
        "/api/inventory-movements",
        params={**params, "page": 2},
    )

    assert first_page.status_code == 200
    assert first_page.json()["total"] == 2
    assert len(first_page.json()["items"]) == 1
    assert first_page.json()["items"][0]["movement_type"] == "ADJUST"
    assert second_page.status_code == 200
    assert second_page.json()["total"] == 2
    assert len(second_page.json()["items"]) == 1
    assert second_page.json()["items"][0]["movement_type"] == "ADJUST"
    assert first_page.json()["items"][0]["id"] != second_page.json()["items"][0]["id"]


def test_adjustment_reduces_and_increases_stock_with_delta_quantity(
    database_client,
) -> None:
    client, _session_local = database_client
    product = create_product(client, packagings=[{"packing_qty": 240, "carton_count": 12}])
    packaging_id = product["packagings"][0]["id"]

    decrease = adjustment_request(
        client,
        product["id"],
        packaging_id,
        9,
        remark="  盘点纠正  ",
    )
    assert decrease.status_code == 201, decrease.text
    assert decrease.json()["movement_type"] == "ADJUST"
    assert decrease.json()["quantity"] == 3
    assert decrease.json()["before_carton_count"] == 12
    assert decrease.json()["after_carton_count"] == 9
    assert decrease.json()["packing_qty_snapshot"] == 240
    assert decrease.json()["unit_snapshot"] == "pcs"
    assert decrease.json()["remark"] == "盘点纠正"

    increase = adjustment_request(
        client,
        product["id"],
        packaging_id,
        12,
        remark="盘点发现漏记",
    )
    assert increase.status_code == 201, increase.text
    assert increase.json()["quantity"] == 3
    assert increase.json()["before_carton_count"] == 9
    assert increase.json()["after_carton_count"] == 12
    assert increase.json()["after_carton_count"] - increase.json()["before_carton_count"] == 3
    assert client.get(f"/api/products/{product['id']}").json()["total_carton_count"] == 12


def test_adjustment_accepts_zero_and_rejects_invalid_actual_counts(database_client) -> None:
    client, session_local = database_client
    product = create_product(client, packagings=[{"packing_qty": 240, "carton_count": 3}])
    packaging_id = product["packagings"][0]["id"]

    zero = adjustment_request(client, product["id"], packaging_id, 0)
    assert zero.status_code == 201, zero.text
    assert zero.json()["before_carton_count"] == 3
    assert zero.json()["after_carton_count"] == 0
    assert zero.json()["quantity"] == 3

    for actual in [-1, 1.5, "1", "abc", True]:
        invalid = adjustment_request(client, product["id"], packaging_id, actual)
        assert invalid.status_code == 422

    with session_local() as db:
        assert db.scalar(select(func.count(InventoryMovement.id))) == 1
        assert db.get(ProductPackaging, packaging_id).carton_count == 0


def test_adjustment_rejects_same_stock_and_missing_or_blank_remark(database_client) -> None:
    client, session_local = database_client
    product = create_product(client)
    packaging_id = product["packagings"][0]["id"]

    same = adjustment_request(client, product["id"], packaging_id, 3)
    assert same.status_code == 409
    assert same.json()["detail"] == "实际库存与当前库存一致，无需调整。"

    for remark in [None, "", "   "]:
        payload = {
            "product_packaging_id": packaging_id,
            "actual_carton_count": 2,
        }
        if remark is not None:
            payload["remark"] = remark
        invalid = client.post(
            f"/api/products/{product['id']}/stock/adjust",
            json=payload,
        )
        assert invalid.status_code == 422

    extra_fields = adjustment_request(
        client,
        product["id"],
        packaging_id,
        2,
        before=3,
        after=2,
        delta=-1,
    )
    assert extra_fields.status_code == 422
    with session_local() as db:
        assert db.scalar(select(func.count(InventoryMovement.id))) == 0
        assert db.get(ProductPackaging, packaging_id).carton_count == 3


def test_adjustment_only_changes_selected_packaging_and_total_is_recomputed(
    database_client,
) -> None:
    client, _session_local = database_client
    product = create_product(
        client,
        packagings=[
            {"packing_qty": 240, "carton_count": 20},
            {"packing_qty": 144, "carton_count": 3},
        ],
    )
    first_id, second_id = [row["id"] for row in product["packagings"]]

    response = adjustment_request(client, product["id"], first_id, 18)
    assert response.status_code == 201
    saved = client.get(f"/api/products/{product['id']}").json()
    assert {row["id"]: row["carton_count"] for row in saved["packagings"]} == {
        first_id: 18,
        second_id: 3,
    }
    assert saved["total_carton_count"] == 21


def test_adjustment_supports_nullable_packing_qty_by_id(database_client) -> None:
    client, _session_local = database_client
    product = create_product(
        client,
        packagings=[{"packing_qty": None, "carton_count": 5}],
    )
    packaging_id = product["packagings"][0]["id"]

    response = adjustment_request(client, product["id"], packaging_id, 2)
    assert response.status_code == 201, response.text
    assert response.json()["packing_qty_snapshot"] is None
    assert response.json()["before_carton_count"] == 5
    assert response.json()["after_carton_count"] == 2


def test_inactive_product_allows_adjustment_but_rejects_in_and_out(database_client) -> None:
    client, session_local = database_client
    product = create_product(client)
    packaging_id = product["packagings"][0]["id"]
    assert client.post(f"/api/products/{product['id']}/deactivate").status_code == 200

    adjustment = adjustment_request(client, product["id"], packaging_id, 1)
    assert adjustment.status_code == 201, adjustment.text
    assert adjustment.json()["movement_type"] == "ADJUST"

    for direction in ["in", "out"]:
        blocked = stock_request(
            client,
            product["id"],
            direction,
            packaging_id=packaging_id,
            quantity=1,
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == "已停用商品不能进行入库或出库。"

    with session_local() as db:
        assert db.scalar(select(func.count(InventoryMovement.id))) == 1
        assert db.get(ProductPackaging, packaging_id).carton_count == 1


def test_adjustment_history_query_and_type_filter(database_client) -> None:
    client, _session_local = database_client
    product = create_product(client)
    packaging_id = product["packagings"][0]["id"]
    response = adjustment_request(client, product["id"], packaging_id, 2)
    assert response.status_code == 201

    history = client.get(
        "/api/inventory-movements",
        params={"product_id": product["id"], "movement_type": "ADJUST"},
    )
    assert history.status_code == 200
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["movement_type"] == "ADJUST"

    invalid_type = client.get(
        "/api/inventory-movements",
        params={"movement_type": "INITIAL"},
    )
    assert invalid_type.status_code == 422


def test_adjustment_failure_rolls_back_carton_change(database_client, monkeypatch) -> None:
    _client, session_local = database_client
    with session_local.begin() as db:
        product = Product(
            size="调整事务回滚商品",
            unit="pcs",
            packagings=[ProductPackaging(packing_qty=24, carton_count=3)],
        )
        db.add(product)
        db.flush()
        product_id = product.id
        packaging_id = product.packagings[0].id

    def fail_movement(**_kwargs):
        raise RuntimeError("模拟调整流水写入失败")

    monkeypatch.setattr(inventory_service, "InventoryMovement", fail_movement)
    with session_local() as db:
        payload = inventory_service.StockAdjustmentCreate(
            product_packaging_id=packaging_id,
            actual_carton_count=1,
            remark="盘点纠正",
        )
        with pytest.raises(RuntimeError, match="模拟调整流水写入失败"):
            inventory_service.create_stock_adjustment(
                db,
                product_id=product_id,
                payload=payload,
            )
        db.rollback()

    with session_local() as db:
        assert db.get(ProductPackaging, packaging_id).carton_count == 3
        assert db.scalar(select(func.count(InventoryMovement.id))) == 0
