from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models import Category, Product, Warehouse
from app.product_codes import backfill_product_codes, format_code_component


@pytest.fixture
def product_code_api(tmp_path) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    database_path = tmp_path / "product-codes-api.db"
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
        yield client, TestingSessionLocal

    app.dependency_overrides.clear()
    engine.dispose()


def create_category(
    client: TestClient,
    name: str,
    parent_id: int | None = None,
) -> dict[str, object]:
    response = client.post(
        "/api/categories",
        json={"name": name, "parent_id": parent_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def product_payload(category_id: int, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "category_id": category_id,
        "warehouse_id": None,
        "image_path": None,
        "size": "TEST-20cm",
        "packing_qty": 24,
        "unit": "pcs",
        "price": "1.00",
        "carton_count": 10,
        "remark": "商品编号验收",
    }
    payload.update(overrides)
    return payload


def test_number_components_expand_instead_of_truncating() -> None:
    assert format_code_component(100, minimum_width=2) == "100"
    assert format_code_component(1000, minimum_width=3) == "1000"


def create_product(
    client: TestClient,
    category_id: int,
    **overrides: object,
) -> dict[str, object]:
    response = client.post(
        "/api/products",
        json=product_payload(category_id, **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_category_codes_are_scoped_and_rename_does_not_change_them(
    product_code_api: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = product_code_api
    kitchen = create_category(client, "厨房用品")
    christmas = create_category(client, "圣诞系列")
    kitchen_plate = create_category(client, "盘子", kitchen["id"])
    kitchen_bowl = create_category(client, "碗", kitchen["id"])
    christmas_plate = create_category(client, "盘子", christmas["id"])

    assert kitchen["code"] == "01"
    assert christmas["code"] == "02"
    assert kitchen_plate["code"] == "01"
    assert kitchen_bowl["code"] == "02"
    assert christmas_plate["code"] == "01"

    renamed = client.patch(
        f"/api/categories/{kitchen['id']}",
        json={"name": "厨房餐具"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["code"] == "01"

    code_edit = client.patch(
        f"/api/categories/{kitchen['id']}",
        json={"name": "厨房餐具", "code": "99"},
    )
    assert code_edit.status_code == 422

    code_create = client.post(
        "/api/categories",
        json={"name": "不允许自定义编号", "code": "99"},
    )
    assert code_create.status_code == 422


def test_category_code_expands_after_99(
    product_code_api: tuple[TestClient, sessionmaker],
) -> None:
    client, session_factory = product_code_api
    with session_factory.begin() as db:
        db.add(Category(name="历史一级分类", code="99"))

    parent = create_category(client, "扩展一级编号")
    child = create_category(client, "扩展二级编号", parent["id"])

    assert parent["code"] == "100"
    assert child["code"] == "01"


def test_product_codes_are_sequential_and_immutable_on_category_or_warehouse_change(
    product_code_api: tuple[TestClient, sessionmaker],
) -> None:
    client, session_factory = product_code_api
    kitchen = create_category(client, "厨房用品")
    christmas = create_category(client, "圣诞系列")
    kitchen_plate = create_category(client, "盘子", kitchen["id"])
    kitchen_bowl = create_category(client, "碗", kitchen["id"])
    christmas_plate = create_category(client, "盘子", christmas["id"])

    first = create_product(client, kitchen_plate["id"])
    second = create_product(client, kitchen_plate["id"])
    other_child = create_product(client, kitchen_bowl["id"])
    other_parent = create_product(client, christmas_plate["id"])

    assert first["product_code"] == "01-01-001"
    assert second["product_code"] == "01-01-002"
    assert other_child["product_code"] == "01-02-001"
    assert other_parent["product_code"] == "02-01-001"

    with session_factory.begin() as db:
        warehouse = Warehouse(name="编号验收仓", sort_order=9)
        db.add(warehouse)
        db.flush()
        warehouse_id = warehouse.id

    changed = client.patch(
        f"/api/products/{first['id']}",
        json={"category_id": kitchen_bowl["id"], "warehouse_id": warehouse_id},
    )
    assert changed.status_code == 200
    assert changed.json()["category_id"] == kitchen_bowl["id"]
    assert changed.json()["warehouse_id"] == warehouse_id
    assert changed.json()["product_code"] == "01-01-001"

    attempted_edit = client.patch(
        f"/api/products/{first['id']}",
        json={"product_code": "99-99-999"},
    )
    assert attempted_edit.status_code == 422

    attempted_create = client.post(
        "/api/products",
        json=product_payload(kitchen_plate["id"], product_code="99-99-999"),
    )
    assert attempted_create.status_code == 422

    search = client.get("/api/products", params={"search": "01-01-001"})
    assert search.status_code == 200
    assert [item["product_code"] for item in search.json()["items"]] == [
        "01-01-001"
    ]


def test_concurrent_products_receive_unique_sequences(
    product_code_api: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = product_code_api
    parent = create_category(client, "并发验收分类")
    child = create_category(client, "测试小类", parent["id"])

    def create_one(index: int):
        return client.post(
            "/api/products",
            json=product_payload(child["id"], size=f"CONCURRENT-{index}"),
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        responses = list(executor.map(create_one, range(6)))

    assert all(response.status_code == 201 for response in responses)
    codes = sorted(response.json()["product_code"] for response in responses)
    assert codes == [
        "01-01-001",
        "01-01-002",
        "01-01-003",
        "01-01-004",
        "01-01-005",
        "01-01-006",
    ]


def test_product_sequence_expands_after_999(
    product_code_api: tuple[TestClient, sessionmaker],
) -> None:
    client, session_factory = product_code_api
    parent = create_category(client, "大序号测试")
    child = create_category(client, "小类", parent["id"])

    with session_factory.begin() as db:
        category = db.get(Category, child["id"])
        assert category is not None
        category.next_product_sequence = 1000

    product = create_product(client, child["id"])
    assert product["product_code"] == "01-01-1000"


def test_backfill_assigns_missing_codes_by_sort_order_and_is_idempotent(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'backfill-order.db').as_posix()}")
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal.begin() as db:
        root_later = Category(name="后排序一级", sort_order=5)
        root_first = Category(name="先排序一级", sort_order=1)
        db.add_all([root_later, root_first])
        db.flush()

        child_later = Category(
            name="同名小类", parent_id=root_later.id, sort_order=5
        )
        child_first = Category(
            name="同名小类", parent_id=root_first.id, sort_order=1
        )
        db.add_all([child_later, child_first])
        db.flush()

        db.add_all(
            [
                Product(
                    category_id=child_later.id,
                    size="A",
                    packing_qty=1,
                    unit="pcs",
                    price=Decimal("1.00"),
                    carton_count=0,
                ),
                Product(
                    category_id=child_later.id,
                    size="B",
                    packing_qty=1,
                    unit="pcs",
                    price=Decimal("1.00"),
                    carton_count=0,
                ),
                Product(
                    category_id=None,
                    size="未分类",
                    packing_qty=1,
                    unit="pcs",
                    price=Decimal("1.00"),
                    carton_count=0,
                ),
            ]
        )

    with TestingSessionLocal.begin() as db:
        result = backfill_product_codes(db)
        assert result == {"categories_assigned": 4, "products_assigned": 2}

    with TestingSessionLocal() as db:
        roots = {
            category.name: category.code
            for category in db.scalars(
                select(Category).where(Category.parent_id.is_(None))
            )
        }
        assert roots == {"后排序一级": "02", "先排序一级": "01"}

        children = db.scalars(select(Category).where(Category.parent_id.is_not(None))).all()
        assert [child.code for child in children].count("01") == 2
        roots_by_id = {
            category.id: category.code
            for category in db.scalars(select(Category).where(Category.parent_id.is_(None)))
        }
        assert {
            f"{roots_by_id[child.parent_id]}-{child.code}" for child in children
        } == {"02-01", "01-01"}
        product_codes = [
            product.product_code
            for product in db.scalars(select(Product).order_by(Product.id.asc()))
        ]
        assert product_codes == ["02-01-001", "02-01-002", None]

    with TestingSessionLocal.begin() as db:
        result = backfill_product_codes(db)
        assert result == {"categories_assigned": 0, "products_assigned": 0}

    with TestingSessionLocal() as db:
        child = db.scalar(
            select(Category).where(Category.parent_id == 1)
        )
        assert child is not None
        assert child.next_product_sequence == 3

    engine.dispose()


def test_backfill_preserves_existing_codes_and_does_not_recycle_sequences(
    tmp_path,
) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'backfill-preserve.db').as_posix()}")
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    with TestingSessionLocal.begin() as db:
        root = Category(name="历史分类", code="06", sort_order=0)
        db.add(root)
        db.flush()
        child = Category(
            name="历史小类",
            parent_id=root.id,
            code="03",
            next_product_sequence=12,
        )
        db.add(child)
        db.flush()
        db.add_all(
            [
                Product(
                    category_id=child.id,
                    product_code="06-03-011",
                    size="旧商品",
                    packing_qty=1,
                    unit="pcs",
                    price=Decimal("1.00"),
                    carton_count=0,
                ),
                Product(
                    category_id=child.id,
                    size="新旧数据缺码",
                    packing_qty=1,
                    unit="pcs",
                    price=Decimal("1.00"),
                    carton_count=0,
                ),
            ]
        )

    with TestingSessionLocal.begin() as db:
        result = backfill_product_codes(db)
        assert result == {"categories_assigned": 0, "products_assigned": 1}

    with TestingSessionLocal() as db:
        child = db.scalar(select(Category).where(Category.parent_id.is_not(None)))
        assert child is not None
        assert child.code == "03"
        assert child.next_product_sequence == 13
        codes = [
            product.product_code
            for product in db.scalars(select(Product).order_by(Product.id.asc()))
        ]
        assert codes == ["06-03-011", "06-03-012"]

    engine.dispose()
