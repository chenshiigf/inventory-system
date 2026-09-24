from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


@pytest.fixture
def test_client(tmp_path) -> Generator[TestClient, None, None]:
    database_path = tmp_path / "categories-test.db"
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
    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()
    engine.dispose()


def category_payload(name: str, parent_id: int | None = None, sort_order: int = 0):
    return {"name": name, "parent_id": parent_id, "sort_order": sort_order}


def create_category(
    client: TestClient,
    name: str,
    parent_id: int | None = None,
    sort_order: int = 0,
) -> dict[str, object]:
    response = client.post(
        "/api/categories",
        json=category_payload(name, parent_id, sort_order),
    )
    assert response.status_code == 201
    return response.json()


def product_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "image_path": None,
        "size": "18 × 18 cm",
        "packagings": [{"packing_qty": 24, "carton_count": 18}],
        "unit": "pcs",
        "price": "2.80",
        "remark": "蓝边方盘",
        "category_id": None,
    }
    payload.update(overrides)
    return payload


def create_product(client: TestClient, category_id: int, **overrides: object):
    payload = product_payload(category_id=category_id, **overrides)
    response = client.post("/api/products", json=payload)
    assert response.status_code == 201
    return response.json()


def test_create_first_level_category_succeeds(test_client: TestClient) -> None:
    response = test_client.post(
        "/api/categories",
        json={"name": "厨房用品"},
    )

    assert response.status_code == 201
    category = response.json()
    assert category["name"] == "厨房用品"
    assert category["parent_id"] is None
    assert category["sort_order"] == 0
    assert category["created_at"]
    assert category["updated_at"]


def test_create_second_level_category_succeeds(test_client: TestClient) -> None:
    parent = create_category(test_client, "厨房用品")

    response = test_client.post(
        "/api/categories",
        json={"name": "盘子", "parent_id": parent["id"]},
    )

    assert response.status_code == 201
    child = response.json()
    assert child["name"] == "盘子"
    assert child["parent_id"] == parent["id"]


def test_missing_parent_category_is_rejected(test_client: TestClient) -> None:
    response = test_client.post(
        "/api/categories",
        json=category_payload("盘子", parent_id=999),
    )

    assert response.status_code == 404


def test_third_level_category_is_rejected(test_client: TestClient) -> None:
    first = create_category(test_client, "厨房用品")
    second = create_category(test_client, "盘子", parent_id=first["id"])

    response = test_client.post(
        "/api/categories",
        json=category_payload("白瓷盘", parent_id=second["id"]),
    )

    assert response.status_code == 422


def test_duplicate_first_level_name_is_rejected(test_client: TestClient) -> None:
    create_category(test_client, "厨房用品")

    response = test_client.post(
        "/api/categories",
        json={"name": "厨房用品"},
    )

    assert response.status_code == 409


def test_duplicate_child_name_under_same_parent_is_rejected(
    test_client: TestClient,
) -> None:
    parent = create_category(test_client, "厨房用品")
    create_category(test_client, "盘子", parent_id=parent["id"])

    response = test_client.post(
        "/api/categories",
        json=category_payload("盘子", parent_id=parent["id"]),
    )

    assert response.status_code == 409


def test_same_child_name_under_different_parents_is_allowed(
    test_client: TestClient,
) -> None:
    kitchen = create_category(test_client, "厨房用品")
    christmas = create_category(test_client, "圣诞系列")

    first = create_category(test_client, "盘子", parent_id=kitchen["id"])
    second = create_category(test_client, "盘子", parent_id=christmas["id"])

    assert first["id"] != second["id"]
    assert first["parent_id"] == kitchen["id"]
    assert second["parent_id"] == christmas["id"]


def test_category_tree_is_nested_and_sorted(test_client: TestClient) -> None:
    later_root = create_category(test_client, "圣诞系列", sort_order=2)
    first_root = create_category(test_client, "厨房用品", sort_order=1)
    create_category(test_client, "碗", parent_id=first_root["id"], sort_order=2)
    create_category(test_client, "盘子", parent_id=first_root["id"], sort_order=1)

    response = test_client.get("/api/categories")

    assert response.status_code == 200
    tree = response.json()
    assert [item["name"] for item in tree] == ["厨房用品", "圣诞系列"]
    assert tree[0]["id"] == first_root["id"]
    assert tree[1]["id"] == later_root["id"]
    assert [item["name"] for item in tree[0]["children"]] == ["盘子", "碗"]
    assert tree[1]["children"] == []


def test_create_product_with_second_level_category_succeeds(
    test_client: TestClient,
) -> None:
    parent = create_category(test_client, "厨房用品")
    child = create_category(test_client, "盘子", parent_id=parent["id"])

    response = test_client.post(
        "/api/products",
        json=product_payload(category_id=child["id"]),
    )

    assert response.status_code == 201
    assert response.json()["category_id"] == child["id"]


def test_product_rejects_nonexistent_category(test_client: TestClient) -> None:
    response = test_client.post(
        "/api/products",
        json=product_payload(category_id=999),
    )

    assert response.status_code == 422


def test_product_rejects_first_level_category(test_client: TestClient) -> None:
    parent = create_category(test_client, "厨房用品")

    response = test_client.post(
        "/api/products",
        json=product_payload(category_id=parent["id"]),
    )

    assert response.status_code == 422


def test_second_level_filter_matches_only_selected_category(
    test_client: TestClient,
) -> None:
    kitchen = create_category(test_client, "厨房用品")
    christmas = create_category(test_client, "圣诞系列")
    kitchen_plate = create_category(test_client, "盘子", parent_id=kitchen["id"])
    christmas_plate = create_category(
        test_client,
        "盘子",
        parent_id=christmas["id"],
    )
    kitchen_product = create_product(test_client, kitchen_plate["id"])
    create_product(test_client, christmas_plate["id"])

    response = test_client.get(
        f"/api/products?category_id={kitchen_plate['id']}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["id"] for item in payload["items"]] == [kitchen_product["id"]]


def test_first_level_filter_includes_all_direct_children(test_client: TestClient) -> None:
    kitchen = create_category(test_client, "厨房用品")
    christmas = create_category(test_client, "圣诞系列")
    kitchen_plate = create_category(test_client, "盘子", parent_id=kitchen["id"])
    kitchen_bowl = create_category(test_client, "碗", parent_id=kitchen["id"])
    christmas_cup = create_category(test_client, "杯子", parent_id=christmas["id"])
    first_product = create_product(test_client, kitchen_plate["id"])
    second_product = create_product(test_client, kitchen_bowl["id"])
    create_product(test_client, christmas_cup["id"])

    response = test_client.get(f"/api/products?category_id={kitchen['id']}")

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["items"]} == {
        first_product["id"],
        second_product["id"],
    }


def test_category_filter_works_with_search(test_client: TestClient) -> None:
    parent = create_category(test_client, "厨房用品")
    child = create_category(test_client, "盘子", parent_id=parent["id"])
    create_product(test_client, child["id"], remark="蓝边方盘")
    create_product(test_client, child["id"], remark="白色圆盘")

    response = test_client.get(
        f"/api/products?category_id={child['id']}&search=蓝边"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["remark"] == "蓝边方盘"


def test_category_filter_works_with_pagination(test_client: TestClient) -> None:
    parent = create_category(test_client, "厨房用品")
    child = create_category(test_client, "盘子", parent_id=parent["id"])
    for index in range(3):
        create_product(test_client, child["id"], size=f"尺寸 {index}")

    response = test_client.get(
        f"/api/products?category_id={child['id']}&page=2&page_size=2"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["page"] == 2
    assert payload["page_size"] == 2
    assert len(payload["items"]) == 1


def test_category_name_can_be_updated(test_client: TestClient) -> None:
    category = create_category(test_client, "厨房用品")

    response = test_client.patch(
        f"/api/categories/{category['id']}",
        json={"name": "厨房餐具"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "厨房餐具"


def test_product_category_can_be_updated(test_client: TestClient) -> None:
    parent = create_category(test_client, "厨房用品")
    first_child = create_category(test_client, "盘子", parent_id=parent["id"])
    second_child = create_category(test_client, "碗", parent_id=parent["id"])
    product = create_product(test_client, first_child["id"])

    response = test_client.patch(
        f"/api/products/{product['id']}",
        json={"category_id": second_child["id"]},
    )

    assert response.status_code == 200
    assert response.json()["category_id"] == second_child["id"]


def test_delete_missing_category_returns_not_found(test_client: TestClient) -> None:
    response = test_client.delete("/api/categories/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "分类不存在。"


def test_delete_empty_category_tree_requires_child_first(
    test_client: TestClient,
) -> None:
    parent = create_category(test_client, "待删除一级分类")
    child = create_category(test_client, "待删除二级分类", parent_id=parent["id"])

    blocked = test_client.delete(f"/api/categories/{parent['id']}")

    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "该分类下仍有子分类，请先删除子分类。"

    assert test_client.delete(f"/api/categories/{child['id']}").status_code == 200
    deleted_parent = test_client.delete(f"/api/categories/{parent['id']}")
    assert deleted_parent.status_code == 200
    assert deleted_parent.json() == {"message": "分类已删除。"}
    assert test_client.get("/api/categories").json() == []


@pytest.mark.parametrize(
    "deactivate",
    [False, True],
    ids=["active-product", "inactive-product"],
)
def test_delete_category_rejects_any_referencing_product(
    test_client: TestClient,
    deactivate: bool,
) -> None:
    parent = create_category(test_client, "商品引用一级分类")
    child = create_category(test_client, "商品引用二级分类", parent_id=parent["id"])
    product = create_product(test_client, child["id"])
    if deactivate:
        assert test_client.post(f"/api/products/{product['id']}/deactivate").status_code == 200

    response = test_client.delete(f"/api/categories/{child['id']}")

    assert response.status_code == 409
    assert response.json()["detail"] == "该分类仍有关联商品，无法删除。"
