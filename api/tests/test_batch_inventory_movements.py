from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import InventoryMovement, ProductPackaging, Warehouse


@pytest.fixture
def database_client() -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
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


def create_product(
    client: TestClient,
    sessions: sessionmaker,
    *,
    name: str,
    packagings: list[tuple[int, int]],
) -> dict:
    with sessions.begin() as db:
        warehouse = Warehouse(name=name, sort_order=0)
        db.add(warehouse)
        db.flush()
        warehouse_id = warehouse.id

    response = client.post(
        "/api/products",
        json={
            "category_id": None,
            "warehouse_id": warehouse_id,
            "image_path": None,
            "thumbnail_path": None,
            "size": f"{name} 测试商品",
            "packagings": [
                {"packing_qty": packing_qty, "carton_count": carton_count}
                for packing_qty, carton_count in packagings
            ],
            "unit": "pcs",
            "price": "2.80",
            "remark": None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def packaging_stock(sessions: sessionmaker, packaging_id: int) -> int:
    with sessions() as db:
        packaging = db.get(ProductPackaging, packaging_id)
        assert packaging is not None
        return packaging.carton_count


def test_batch_outbound_previews_latest_product_and_each_packaging(database_client):
    client, sessions = database_client
    product = create_product(
        client,
        sessions,
        name="主仓",
        packagings=[(240, 10), (144, 1)],
    )
    packaging_ids = [item["id"] for item in product["packagings"]]

    first_preview = client.post(
        "/api/inventory/batch-outbound/preview",
        json={"product_ids": [product["id"]]},
    )
    assert first_preview.status_code == 200, first_preview.text
    assert first_preview.json()["products"][0]["warehouse_name"] == "主仓"
    assert [
        row["carton_count"]
        for row in first_preview.json()["products"][0]["packagings"]
    ] == [10, 1]

    with sessions.begin() as db:
        db.get(ProductPackaging, packaging_ids[0]).carton_count = 5

    refreshed_preview = client.post(
        "/api/inventory/batch-outbound/preview",
        json={"product_ids": [product["id"]]},
    )
    assert refreshed_preview.status_code == 200, refreshed_preview.text
    assert [
        row["carton_count"]
        for row in refreshed_preview.json()["products"][0]["packagings"]
    ] == [5, 1]


def test_single_stock_out_keeps_existing_movement_rules(database_client):
    client, sessions = database_client
    product = create_product(
        client,
        sessions,
        name="主仓",
        packagings=[(240, 3)],
    )
    packaging_id = product["packagings"][0]["id"]

    successful = client.post(
        f"/api/products/{product['id']}/stock/out",
        json={
            "product_packaging_id": packaging_id,
            "quantity": 2,
            "remark": "原单件出库",
        },
    )
    assert successful.status_code == 201, successful.text
    assert successful.json()["before_carton_count"] == 3
    assert successful.json()["after_carton_count"] == 1
    assert successful.json()["remark"] == "原单件出库"

    rejected = client.post(
        f"/api/products/{product['id']}/stock/out",
        json={
            "product_packaging_id": packaging_id,
            "quantity": 2,
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "出库数不能超过当前包装库存。"
    assert packaging_stock(sessions, packaging_id) == 1
    with sessions() as db:
        movements = db.scalars(select(InventoryMovement)).all()
        assert len(movements) == 1
        assert movements[0].movement_type == "OUT"


def test_batch_outbound_commits_multiple_products_and_package_rows(database_client):
    client, sessions = database_client
    first = create_product(
        client,
        sessions,
        name="主仓",
        packagings=[(240, 5), (144, 2)],
    )
    second = create_product(
        client,
        sessions,
        name="虎跳仓",
        packagings=[(12, 4)],
    )
    first_full_packaging = first["packagings"][0]["id"]
    first_zero_packaging = first["packagings"][1]["id"]
    second_packaging = second["packagings"][0]["id"]

    response = client.post(
        "/api/inventory/batch-outbound/commit",
        json={
            "items": [
                {
                    "product_id": first["id"],
                    "product_packaging_id": first_full_packaging,
                    "quantity": 5,
                },
                {
                    "product_id": second["id"],
                    "product_packaging_id": second_packaging,
                    "quantity": 1,
                },
            ],
            "remark": "客户样品出库",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"movement_count": 2, "total_cartons": 6}
    assert packaging_stock(sessions, first_full_packaging) == 0
    assert packaging_stock(sessions, first_zero_packaging) == 2
    assert packaging_stock(sessions, second_packaging) == 3

    with sessions() as db:
        movements = db.scalars(
            select(InventoryMovement).order_by(InventoryMovement.id)
        ).all()
        assert [movement.product_packaging_id for movement in movements] == [
            first_full_packaging,
            second_packaging,
        ]
        assert [movement.quantity for movement in movements] == [5, 1]
        assert [
            (movement.before_carton_count, movement.after_carton_count)
            for movement in movements
        ] == [(5, 0), (4, 3)]
        assert all(movement.movement_type == "OUT" for movement in movements)
        assert all(movement.remark == "客户样品出库" for movement in movements)


def test_batch_outbound_rechecks_current_stock_and_rolls_back_all_items(database_client):
    client, sessions = database_client
    first = create_product(
        client,
        sessions,
        name="主仓",
        packagings=[(240, 10)],
    )
    second = create_product(
        client,
        sessions,
        name="虎跳仓",
        packagings=[(12, 2)],
    )
    first_packaging = first["packagings"][0]["id"]
    second_packaging = second["packagings"][0]["id"]

    preview = client.post(
        "/api/inventory/batch-outbound/preview",
        json={"product_ids": [first["id"], second["id"]]},
    )
    assert preview.status_code == 200
    with sessions.begin() as db:
        db.get(ProductPackaging, first_packaging).carton_count = 5

    response = client.post(
        "/api/inventory/batch-outbound/commit",
        json={
            "items": [
                {
                    "product_id": first["id"],
                    "product_packaging_id": first_packaging,
                    "quantity": 3,
                },
                {
                    "product_id": second["id"],
                    "product_packaging_id": second_packaging,
                    "quantity": 3,
                },
            ],
            "remark": "并发库存测试",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "出库数不能超过当前包装库存。"
    assert packaging_stock(sessions, first_packaging) == 5
    assert packaging_stock(sessions, second_packaging) == 2
    with sessions() as db:
        assert db.scalar(select(InventoryMovement.id)) is None


def test_batch_outbound_rolls_back_if_a_later_ledger_insert_fails(database_client):
    client, sessions = database_client
    first = create_product(
        client,
        sessions,
        name="主仓",
        packagings=[(240, 5)],
    )
    second = create_product(
        client,
        sessions,
        name="虎跳仓",
        packagings=[(12, 4)],
    )
    first_packaging = first["packagings"][0]["id"]
    second_packaging = second["packagings"][0]["id"]
    with sessions.begin() as db:
        db.connection().exec_driver_sql(
            "CREATE TRIGGER reject_second_batch_movement "
            "BEFORE INSERT ON inventory_movements "
            f"WHEN NEW.product_packaging_id = {second_packaging} "
            "BEGIN SELECT RAISE(ABORT, 'batch ledger failure'); END"
        )

    response = client.post(
        "/api/inventory/batch-outbound/commit",
        json={
            "items": [
                {
                    "product_id": first["id"],
                    "product_packaging_id": first_packaging,
                    "quantity": 2,
                },
                {
                    "product_id": second["id"],
                    "product_packaging_id": second_packaging,
                    "quantity": 1,
                },
            ],
            "remark": "流水异常回滚测试",
        },
    )

    assert response.status_code == 409
    assert packaging_stock(sessions, first_packaging) == 5
    assert packaging_stock(sessions, second_packaging) == 4
    with sessions() as db:
        assert db.scalar(select(InventoryMovement.id)) is None


def test_batch_outbound_rejects_invalid_rows_and_inactive_products(database_client):
    client, sessions = database_client
    first = create_product(
        client,
        sessions,
        name="主仓",
        packagings=[(240, 5)],
    )
    other = create_product(
        client,
        sessions,
        name="虎跳仓",
        packagings=[(12, 4)],
    )
    with sessions.begin() as db:
        product = db.get(ProductPackaging, other["packagings"][0]["id"]).product
        product.is_active = False

    invalid_packaging = client.post(
        "/api/inventory/batch-outbound/commit",
        json={
            "items": [
                {
                    "product_id": first["id"],
                    "product_packaging_id": other["packagings"][0]["id"],
                    "quantity": 1,
                }
            ]
        },
    )
    assert invalid_packaging.status_code == 409
    assert invalid_packaging.json()["detail"] == "包装规格已变化，请刷新工作台后重试。"

    inactive = client.post(
        "/api/inventory/batch-outbound/commit",
        json={
            "items": [
                {
                    "product_id": other["id"],
                    "product_packaging_id": other["packagings"][0]["id"],
                    "quantity": 1,
                }
            ]
        },
    )
    assert inactive.status_code == 409
    assert inactive.json()["detail"] == "已停用商品不能进行入库或出库。"
    assert packaging_stock(sessions, first["packagings"][0]["id"]) == 5
    assert packaging_stock(sessions, other["packagings"][0]["id"]) == 4
    with sessions() as db:
        assert db.scalar(select(InventoryMovement.id)) is None


def test_batch_outbound_rejects_duplicate_packaging_rows(database_client):
    client, sessions = database_client
    product = create_product(
        client,
        sessions,
        name="主仓",
        packagings=[(240, 5)],
    )
    packaging_id = product["packagings"][0]["id"]
    duplicate = {
        "product_id": product["id"],
        "product_packaging_id": packaging_id,
        "quantity": 1,
    }

    response = client.post(
        "/api/inventory/batch-outbound/commit",
        json={"items": [duplicate, duplicate]},
    )

    assert response.status_code == 422
    assert packaging_stock(sessions, packaging_id) == 5
    with sessions() as db:
        assert db.scalar(select(InventoryMovement.id)) is None
