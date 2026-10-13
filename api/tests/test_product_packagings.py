from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

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
    movements = client.get(
        "/api/inventory-movements",
        params={"product_id": product["id"]},
    )
    assert movements.status_code == 200
    assert movements.json()["total"] == 0


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
                },
                {"packing_qty": 144},
            ]
        },
    )

    assert response.status_code == 200
    assert [(row["packing_qty"], row["carton_count"]) for row in response.json()["packagings"]] == [
        (24, 10),
        (144, 0),
    ]
    assert response.json()["total_carton_count"] == 10


def test_update_can_edit_packaging(client: TestClient) -> None:
    created = create_product(client)
    packaging_id = created["packagings"][0]["id"]

    response = client.patch(
        f"/api/products/{created['id']}",
        json={
            "packagings": [
                {"id": packaging_id, "packing_qty": 48}
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["packagings"][0]["packing_qty"] == 48
    assert response.json()["total_carton_count"] == 10


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


def test_update_rejects_deleting_stocked_packaging(client: TestClient) -> None:
    created = create_product(
        client,
        packagings=[
            {"packing_qty": 24, "carton_count": 10},
            {"packing_qty": 144, "carton_count": 1},
        ],
    )

    original_packagings = created["packagings"]
    response = client.patch(
        f"/api/products/{created['id']}",
        json={"packagings": [packaging_write(created["packagings"][0])]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "包装规格仍有库存，请先盘点调整为 0 后再删除"
    unchanged = client.get(f"/api/products/{created['id']}").json()
    assert unchanged["packagings"] == original_packagings
    assert unchanged["total_carton_count"] == 11


def test_stocked_packaging_removal_rolls_back_other_product_and_packaging_edits(
    client: TestClient,
) -> None:
    created = create_product(
        client,
        size="修改前名称",
        remark="修改前备注",
        packagings=[
            {"packing_qty": 240, "carton_count": 10},
            {"packing_qty": 144, "carton_count": 0},
            {"packing_qty": 48, "carton_count": 0},
        ],
    )
    original_packagings = created["packagings"]

    response = client.patch(
        f"/api/products/{created['id']}",
        json={
            "size": "不应保存的新名称",
            "remark": "不应保存的新备注",
            "packagings": [
                {"id": original_packagings[1]["id"], "packing_qty": 72}
            ],
        },
    )

    assert response.status_code == 409
    unchanged = client.get(f"/api/products/{created['id']}").json()
    assert unchanged["size"] == "修改前名称"
    assert unchanged["remark"] == "修改前备注"
    assert unchanged["packagings"] == original_packagings


def test_adjust_to_zero_then_delete_packaging_keeps_movement_snapshot(
    client: TestClient,
) -> None:
    created = create_product(
        client,
        packagings=[
            {"packing_qty": 24, "carton_count": 2},
            {"packing_qty": 144, "carton_count": 0},
        ],
    )
    stocked_packaging, remaining_packaging = created["packagings"]

    adjustment = client.post(
        f"/api/products/{created['id']}/stock/adjust",
        json={
            "product_packaging_id": stocked_packaging["id"],
            "actual_carton_count": 0,
            "remark": "盘点调整为 0",
        },
    )
    assert adjustment.status_code == 201, adjustment.text
    assert adjustment.json()["movement_type"] == "ADJUST"
    movement_id = adjustment.json()["id"]

    deletion = client.patch(
        f"/api/products/{created['id']}",
        json={"packagings": [packaging_write(remaining_packaging)]},
    )

    assert deletion.status_code == 200, deletion.text
    assert [
        (row["packing_qty"], row["carton_count"])
        for row in deletion.json()["packagings"]
    ] == [(144, 0)]
    history = client.get(
        "/api/inventory-movements",
        params={"product_id": created["id"]},
    )
    assert history.status_code == 200
    assert history.json()["total"] == 1
    movement = history.json()["items"][0]
    assert movement["id"] == movement_id
    assert movement["product_packaging_id"] is None
    assert movement["packing_qty_snapshot"] == 24
    assert movement["unit_snapshot"] == "pcs"


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
                {"packing_qty": 24},
                {"packing_qty": 24},
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
        json={"packagings": [{"id": 99999, "packing_qty": 24}]},
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


def packaging_database_state(client: TestClient):
    dependency = client.app.dependency_overrides[get_db]()
    try:
        engine = next(dependency).get_bind()
        with engine.connect() as connection:
            return {
                table: [tuple(row) for row in connection.exec_driver_sql(f"SELECT * FROM {table} ORDER BY id")]
                for table in ("product_packagings", "inventory_movements")
            }
    finally:
        dependency.close()


@pytest.mark.parametrize("cartons", [0, 121])
@pytest.mark.parametrize("changes", [
    {"remark": "只新增备注"}, {"remark": None}, {"size": "更新尺寸"},
    {"unit": "set"}, {"price": "3.50"}, {"image_path": "products/main/updated.webp"},
])
def test_ordinary_patch_never_touches_packaging_or_history(client: TestClient, cartons, changes):
    created = create_product(client, packagings=[
        {"packing_qty": 24, "carton_count": cartons + 1},
        {"packing_qty": 144, "carton_count": 0},
    ])
    adjusted = client.post(f"/api/products/{created['id']}/stock/adjust", json={
        "product_packaging_id": created["packagings"][0]["id"],
        "actual_carton_count": cartons, "remark": "已有历史流水",
    })
    assert adjusted.status_code == 201
    before_product = client.get(f"/api/products/{created['id']}").json()
    before = packaging_database_state(client)
    assert len(before["inventory_movements"]) == 1
    dependency = client.app.dependency_overrides[get_db]()
    engine = next(dependency).get_bind()
    writes = []
    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) and any(
            name in statement.lower() for name in ("product_packagings", "inventory_movements")
        ):
            writes.append(statement)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = client.patch(f"/api/products/{created['id']}", json=changes)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
        dependency.close()
    assert response.status_code == 200, response.text
    assert writes == []
    assert packaging_database_state(client) == before
    result = response.json()
    assert result["packagings"] == before_product["packagings"]
    assert result["total_carton_count"] == cartons
    assert result["id"] == created["id"] and result["product_code"] == created["product_code"]
    for key, value in changes.items():
        assert result[key] == value


def test_missing_packaging_ids_still_reject_stocked_replacement_and_rollback(client: TestClient):
    created = create_product(client, packagings=[{"packing_qty": 24, "carton_count": 121}])
    before = packaging_database_state(client)
    # Reproduce the old frontend payload: an existing row's ID was lost.
    response = client.patch(f"/api/products/{created['id']}", json={
        "remark": "不得部分保存", "packagings": [{"packing_qty": 24}],
    })
    assert response.status_code == 409
    assert response.json()["detail"] == "包装规格仍有库存，请先盘点调整为 0 后再删除"
    assert packaging_database_state(client) == before
    assert client.get(f"/api/products/{created['id']}").json()["remark"] == created["remark"]


@pytest.mark.parametrize("cleared", [None, "", " \n "])
def test_quick_remark_add_modify_clear_preserves_all_other_data(client: TestClient, cleared):
    created = create_product(client, remark=None, packagings=[
        {"packing_qty": 24, "carton_count": 122}, {"packing_qty": 144, "carton_count": 0},
    ])
    assert client.post(f"/api/products/{created['id']}/stock/adjust", json={
        "product_packaging_id": created["packagings"][0]["id"],
        "actual_carton_count": 121, "remark": "历史记录不能改变",
    }).status_code == 201
    before = packaging_database_state(client)
    baseline = client.get(f"/api/products/{created['id']}").json()
    for submitted, expected in [("新增备注", "新增备注"), (" 修改备注 ", "修改备注"), (cleared, None)]:
        response = client.patch(f"/api/products/{created['id']}", json={"remark": submitted})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["remark"] == expected
        assert packaging_database_state(client) == before
        persisted = client.get(f"/api/products/{created['id']}").json()
        assert persisted["remark"] == expected
        assert {key: value for key, value in persisted.items() if key not in {"remark", "updated_at"}} == {
            key: value for key, value in baseline.items() if key not in {"remark", "updated_at"}
        }


@pytest.mark.parametrize("failure", ["too_long", "invalid_type", "missing_product", "commit"])
def test_failed_quick_remark_keeps_product_packaging_stock_and_history(client: TestClient, monkeypatch, failure):
    created = create_product(client, remark="原备注", packagings=[{"packing_qty": 24, "carton_count": 122}])
    assert client.post(f"/api/products/{created['id']}/stock/adjust", json={
        "product_packaging_id": created["packagings"][0]["id"],
        "actual_carton_count": 121, "remark": "失败保存也不得影响这条流水",
    }).status_code == 201
    before = packaging_database_state(client)
    baseline = client.get(f"/api/products/{created['id']}").json()
    payload = {"remark": "x" * 2001 if failure == "too_long" else 123 if failure == "invalid_type" else "不能保存"}
    product_id = created["id"] + 9999 if failure == "missing_product" else created["id"]
    if failure == "commit":
        def fail_commit(session):
            session.flush()  # Force the UPDATE before simulating a commit failure.
            raise RuntimeError("Simulated local database failure")
        with monkeypatch.context() as patcher:
            patcher.setattr(Session, "commit", fail_commit)
            failing_client = TestClient(client.app, raise_server_exceptions=False)
            try:
                response = failing_client.patch(f"/api/products/{product_id}", json=payload)
            finally:
                failing_client.close()
    else:
        response = client.patch(f"/api/products/{product_id}", json=payload)
    assert response.status_code == (500 if failure == "commit" else 404 if failure == "missing_product" else 422)
    assert packaging_database_state(client) == before
    assert client.get(f"/api/products/{created['id']}").json() == baseline


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
        json={
            "packagings": [
                {"id": created["packagings"][0]["id"], "packing_qty": 48}
            ]
        },
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
