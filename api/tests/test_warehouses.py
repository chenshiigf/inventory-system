from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.seed import ensure_default_warehouses


@pytest.fixture
def warehouse_context(tmp_path) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    database_path = tmp_path / "warehouses-test.db"
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
        with TestingSessionLocal() as db:
            ensure_default_warehouses(db)
        yield test_client, TestingSessionLocal

    app.dependency_overrides.clear()
    engine.dispose()


def warehouse_ids(client: TestClient) -> dict[str, int]:
    response = client.get("/api/warehouses")
    assert response.status_code == 200
    return {item["name"]: item["id"] for item in response.json()}


def create_category(
    client: TestClient,
    name: str,
    parent_id: int | None = None,
    sort_order: int = 0,
) -> dict[str, object]:
    response = client.post(
        "/api/categories",
        json={"name": name, "parent_id": parent_id, "sort_order": sort_order},
    )
    assert response.status_code == 201
    return response.json()


def product_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category_id": None,
        "image_path": None,
        "size": "20 cm",
        "packagings": [{"packing_qty": 24, "carton_count": 10}],
        "unit": "pcs",
        "price": "1.00",
        "remark": "测试商品",
    }
    payload.update(overrides)
    return payload


def create_product(client: TestClient, **overrides: object) -> dict[str, object]:
    response = client.post("/api/products", json=product_payload(**overrides))
    assert response.status_code == 201
    return response.json()


def test_get_warehouses_returns_defaults_in_sort_order(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context

    response = client.get("/api/warehouses")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["主仓", "虎跳仓"]
    assert [item["sort_order"] for item in response.json()] == [0, 1]


def test_default_warehouse_seed_is_idempotent(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, session_factory = warehouse_context

    with session_factory() as db:
        ensure_default_warehouses(db)
        ensure_default_warehouses(db)

    response = client.get("/api/warehouses")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["主仓", "虎跳仓"]
    assert len({item["id"] for item in response.json()}) == 2


def test_create_product_with_warehouse_succeeds(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    main_id = warehouse_ids(client)["主仓"]

    response = client.post(
        "/api/products",
        json=product_payload(warehouse_id=main_id),
    )

    assert response.status_code == 201
    assert response.json()["warehouse_id"] == main_id


def test_product_rejects_nonexistent_warehouse(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context

    response = client.post(
        "/api/products",
        json=product_payload(warehouse_id=999),
    )

    assert response.status_code == 422


def test_legacy_product_can_keep_null_warehouse(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context

    product = create_product(client)

    assert product["warehouse_id"] is None


def test_product_list_filters_main_tiger_and_all_warehouses(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    ids = warehouse_ids(client)
    main_product = create_product(client, size="主仓商品", warehouse_id=ids["主仓"])
    tiger_product = create_product(client, size="虎跳仓商品", warehouse_id=ids["虎跳仓"])
    legacy_product = create_product(client, size="未分配仓库商品")

    main_response = client.get(f"/api/products?warehouse_id={ids['主仓']}")
    tiger_response = client.get(f"/api/products?warehouse_id={ids['虎跳仓']}")
    all_response = client.get("/api/products")

    assert {item["id"] for item in main_response.json()["items"]} == {
        main_product["id"]
    }
    assert {item["id"] for item in tiger_response.json()["items"]} == {
        tiger_product["id"]
    }
    assert {item["id"] for item in all_response.json()["items"]} == {
        main_product["id"],
        tiger_product["id"],
        legacy_product["id"],
    }


def test_warehouse_and_second_level_category_filters_combine(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    ids = warehouse_ids(client)
    kitchen = create_category(client, "厨房用品")
    christmas = create_category(client, "圣诞系列")
    kitchen_plate = create_category(client, "盘子", int(kitchen["id"]))
    christmas_plate = create_category(client, "盘子", int(christmas["id"]))
    main_plate = create_product(
        client,
        warehouse_id=ids["主仓"],
        category_id=kitchen_plate["id"],
    )
    tiger_plate = create_product(
        client,
        warehouse_id=ids["虎跳仓"],
        category_id=kitchen_plate["id"],
    )
    create_product(
        client,
        warehouse_id=ids["主仓"],
        category_id=christmas_plate["id"],
    )

    main_response = client.get(
        f"/api/products?warehouse_id={ids['主仓']}&category_id={kitchen_plate['id']}"
    )
    tiger_response = client.get(
        f"/api/products?warehouse_id={ids['虎跳仓']}&category_id={kitchen_plate['id']}"
    )

    assert [item["id"] for item in main_response.json()["items"]] == [
        main_plate["id"]
    ]
    assert [item["id"] for item in tiger_response.json()["items"]] == [
        tiger_plate["id"]
    ]


def test_warehouse_and_first_level_category_include_children(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    ids = warehouse_ids(client)
    kitchen = create_category(client, "厨房用品")
    plate = create_category(client, "盘子", int(kitchen["id"]))
    bowl = create_category(client, "碗", int(kitchen["id"]))
    first_product = create_product(
        client, warehouse_id=ids["主仓"], category_id=plate["id"]
    )
    second_product = create_product(
        client, warehouse_id=ids["主仓"], category_id=bowl["id"]
    )
    create_product(client, warehouse_id=ids["虎跳仓"], category_id=plate["id"])

    response = client.get(
        f"/api/products?warehouse_id={ids['主仓']}&category_id={kitchen['id']}"
    )

    assert {item["id"] for item in response.json()["items"]} == {
        first_product["id"],
        second_product["id"],
    }


def test_warehouse_and_search_filters_combine(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    ids = warehouse_ids(client)
    main_product = create_product(
        client,
        warehouse_id=ids["主仓"],
        size="主仓蓝边盘",
        remark="筛选目标",
    )
    create_product(
        client,
        warehouse_id=ids["虎跳仓"],
        size="虎跳蓝边盘",
        remark="筛选目标",
    )

    response = client.get(
        f"/api/products?warehouse_id={ids['主仓']}&search=筛选目标"
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [main_product["id"]]


def test_warehouse_filter_supports_pagination(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    ids = warehouse_ids(client)
    for index in range(3):
        create_product(
            client,
            size=f"主仓分页商品-{index}",
            warehouse_id=ids["主仓"],
        )
    create_product(client, size="虎跳仓分页商品", warehouse_id=ids["虎跳仓"])

    response = client.get(
        f"/api/products?warehouse_id={ids['主仓']}&page=2&page_size=1"
    )

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert response.json()["page"] == 2
    assert response.json()["page_size"] == 1
    assert len(response.json()["items"]) == 1
    assert response.json()["items"][0]["warehouse_id"] == ids["主仓"]


def test_patch_product_can_change_warehouse_without_changing_stock_or_category(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    ids = warehouse_ids(client)
    kitchen = create_category(client, "厨房用品")
    plate = create_category(client, "盘子", int(kitchen["id"]))
    product = create_product(
        client,
        warehouse_id=ids["主仓"],
        category_id=plate["id"],
        packagings=[{"packing_qty": 24, "carton_count": 17}],
    )

    response = client.patch(
        f"/api/products/{product['id']}",
        json={"warehouse_id": ids["虎跳仓"]},
    )

    assert response.status_code == 200
    assert response.json()["warehouse_id"] == ids["虎跳仓"]
    assert response.json()["total_carton_count"] == 17
    assert response.json()["category_id"] == plate["id"]


def test_patch_product_rejects_nonexistent_warehouse(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    product = create_product(client)

    response = client.patch(
        f"/api/products/{product['id']}",
        json={"warehouse_id": 999},
    )

    assert response.status_code == 422


def test_warehouse_category_tree_contains_only_categories_used_there(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    ids = warehouse_ids(client)
    kitchen = create_category(client, "厨房用品", sort_order=0)
    christmas = create_category(client, "圣诞系列", sort_order=1)
    plate = create_category(client, "盘子", int(kitchen["id"]), sort_order=0)
    bowl = create_category(client, "碗", int(kitchen["id"]), sort_order=1)
    jar = create_category(client, "罐子", int(kitchen["id"]), sort_order=2)
    bulb = create_category(client, "水晶球", int(christmas["id"]), sort_order=0)
    cup = create_category(client, "杯子", int(christmas["id"]), sort_order=1)
    create_product(client, warehouse_id=ids["主仓"], category_id=plate["id"])
    create_product(client, warehouse_id=ids["主仓"], category_id=bulb["id"])
    create_product(client, warehouse_id=ids["虎跳仓"], category_id=bowl["id"])

    main_response = client.get(f"/api/categories?warehouse_id={ids['主仓']}")
    tiger_response = client.get(f"/api/categories?warehouse_id={ids['虎跳仓']}")

    main_tree = main_response.json()
    assert [item["name"] for item in main_tree] == ["厨房用品", "圣诞系列"]
    assert [item["name"] for item in main_tree[0]["children"]] == ["盘子"]
    assert [item["name"] for item in main_tree[1]["children"]] == ["水晶球"]
    tiger_tree = tiger_response.json()
    assert [item["name"] for item in tiger_tree] == ["厨房用品"]
    assert [item["name"] for item in tiger_tree[0]["children"]] == ["碗"]
    assert jar["id"] not in {item["id"] for item in main_tree[0]["children"]}
    assert cup["id"] not in {item["id"] for item in main_tree[1]["children"]}


def test_category_tree_without_warehouse_is_global_and_unfiltered(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context
    kitchen = create_category(client, "厨房用品")
    christmas = create_category(client, "圣诞系列")
    plate = create_category(client, "盘子", int(kitchen["id"]))
    bulb = create_category(client, "水晶球", int(christmas["id"]))
    create_category(client, "罐子", int(kitchen["id"]))
    ids = warehouse_ids(client)
    create_product(client, warehouse_id=ids["主仓"], category_id=plate["id"])

    response = client.get("/api/categories")

    assert response.status_code == 200
    tree = response.json()
    assert [item["name"] for item in tree] == ["厨房用品", "圣诞系列"]
    assert [item["name"] for item in tree[0]["children"]] == ["盘子", "罐子"]
    assert [item["name"] for item in tree[1]["children"]] == ["水晶球"]
    assert bulb["name"] == tree[1]["children"][0]["name"]


def test_category_tree_rejects_unknown_warehouse(
    warehouse_context: tuple[TestClient, sessionmaker],
) -> None:
    client, _session_factory = warehouse_context

    response = client.get("/api/categories?warehouse_id=999")

    assert response.status_code == 404
