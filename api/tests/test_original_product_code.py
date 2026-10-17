"""Original codes are editable labels, independent of product/stock identity."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.database import Base
from app.models import Product
from .test_packaging_identity import context, migration_revisions, seed_legacy_rows  # noqa: F401
from .test_products import create_category_pair, product_payload


def create(client, **overrides):
    response = client.post("/api/products", json=product_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("fields", [
    {}, {"original_product_code": None}, {"original_product_code": ""},
    {"original_product_code": "  \t\n"}, {"original_product_code": " " * 101},
])
def test_create_empty_original_code_is_stored_as_null(context, fields):
    client, sessions = context
    product = create(client, **fields)
    assert product["original_product_code"] is None
    with sessions() as db:
        assert db.get(Product, product["id"]).original_product_code is None
    assert client.get(f"/api/products/{product['id']}").json()["original_product_code"] is None


def test_create_trims_original_code_and_returns_it_in_list_and_detail(context):
    client, sessions = context
    product = create(client, original_product_code="  2677-35  ")
    assert product["original_product_code"] == "2677-35"
    assert client.get("/api/products").json()["items"][0]["original_product_code"] == "2677-35"
    assert client.get(f"/api/products/{product['id']}").json()["original_product_code"] == "2677-35"
    with sessions() as db:
        assert db.get(Product, product["id"]).original_product_code == "2677-35"


@pytest.mark.parametrize("cleared", [None, "", " \t ", " " * 101])
def test_patch_modify_omit_and_clear_original_code(context, cleared):
    client, sessions = context
    product = create(client, original_product_code="before")
    url = f"/api/products/{product['id']}"
    assert client.patch(url, json={"remark": "changed"}).json()["original_product_code"] == "before"
    response = client.patch(url, json={"original_product_code": "  after  "})
    assert response.status_code == 200
    assert response.json()["original_product_code"] == "after"
    response = client.patch(url, json={"original_product_code": cleared})
    assert response.status_code == 200, response.text
    assert response.json()["original_product_code"] is None
    assert client.get(url).json()["original_product_code"] is None
    with sessions() as db:
        assert db.get(Product, product["id"]).original_product_code is None


@pytest.mark.parametrize("value", ["x" * 101, 123, True, ["code"]])
def test_original_code_rejects_invalid_values_without_changing_product(context, value):
    client, _ = context
    response = client.post("/api/products", json=product_payload(original_product_code=value))
    assert response.status_code == 422
    product = create(client, original_product_code="valid")
    url = f"/api/products/{product['id']}"
    response = client.patch(url, json={"original_product_code": value})
    assert response.status_code == 422
    assert client.get(url).json()["original_product_code"] == "valid"


def test_original_code_maximum_length_and_unknown_fields(context):
    client, _ = context
    product = create(client, original_product_code=" " + "货" * 100 + " ")
    assert product["original_product_code"] == "货" * 100
    url = f"/api/products/{product['id']}"
    assert client.patch(url, json={"original_product_code": "x" * 100}).status_code == 200
    assert client.patch(url, json={"product_code": "MANUAL"}).status_code == 422
    assert client.post("/api/products", json=product_payload(unknown_field="value")).status_code == 422


def test_duplicate_original_codes_search_all_products_with_correct_pagination(context):
    client, _ = context
    _, category_id = create_category_pair(client)
    first = create(client, category_id=category_id, original_product_code="2677-35", size="unique-size")
    second = create(client, category_id=category_id, original_product_code="2677-35", remark="unique-remark")
    create(client, category_id=category_id, original_product_code="other")
    assert first["product_code"] != second["product_code"]
    ids = []
    for page in (1, 2):
        response = client.get("/api/products", params={
            "search": "677-3", "category_id": category_id, "page": page, "page_size": 1,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert body["page"] == page
        ids.extend(item["id"] for item in body["items"])
    assert ids == [first["id"], second["id"]]
    for search, expected_id in [
        (first["product_code"], first["id"]), ("unique-size", first["id"]),
        ("unique-remark", second["id"]),
    ]:
        body = client.get("/api/products", params={"search": search}).json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == expected_id


def test_original_code_edit_preserves_product_packaging_stock_and_movement(context):
    client, sessions = context
    _, category_id = create_category_pair(client)
    product = create(client, category_id=category_id, original_product_code="before")
    response = client.post(f"/api/products/{product['id']}/stock/in", json={
        "product_packaging_id": product["packagings"][0]["id"], "quantity": 2,
    })
    assert response.status_code == 201, response.text
    with sessions() as db:
        before = db.execute(text("SELECT * FROM products")).mappings().one()
        packagings = db.execute(text("SELECT * FROM product_packagings")).all()
        movements = db.execute(text("SELECT * FROM inventory_movements")).all()
    response = client.patch(f"/api/products/{product['id']}", json={"original_product_code": "after"})
    assert response.status_code == 200
    with sessions() as db:
        after = db.execute(text("SELECT * FROM products")).mappings().one()
        assert {k: v for k, v in before.items() if k not in {"original_product_code", "updated_at"}} == {
            k: v for k, v in after.items() if k not in {"original_product_code", "updated_at"}
        }
        assert db.execute(text("SELECT * FROM product_packagings")).all() == packagings
        assert db.execute(text("SELECT * FROM inventory_movements")).all() == movements


def test_original_code_migration_preserves_old_rows_indexes_and_links():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            migration_context = MigrationContext.configure(connection)
            revisions = migration_revisions("c8d9e0f1a2b3")
            assert revisions[-1].revision == "c8d9e0f1a2b3"
            assert revisions[-1].down_revision == "b2c3d4e5f6a7"
            with Operations.context(migration_context):
                for revision in revisions[:-1]:
                    revision.module.upgrade()
                seed_legacy_rows(connection)
                before = connection.execute(text("SELECT * FROM products")).mappings().all()
                packagings = connection.execute(text("SELECT * FROM product_packagings")).all()
                movements = connection.execute(text("SELECT * FROM inventory_movements")).all()
                indexes = connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='products' ORDER BY name")).all()
                revisions[-1].module.upgrade()
                column = next(col for col in inspect(connection).get_columns("products") if col["name"] == "original_product_code")
                assert column["nullable"] and column["type"].length == 100
                after = connection.execute(text("SELECT * FROM products")).mappings().all()
                assert all(row["original_product_code"] is None for row in after)
                assert [{k: v for k, v in row.items() if k != "original_product_code"} for row in after] == [dict(row) for row in before]
                # Exercise rollback only in this disposable database, then restore head.
                revisions[-1].module.downgrade()
                assert connection.execute(text("SELECT * FROM products")).mappings().all() == before
                revisions[-1].module.upgrade()
                for revision in migration_revisions()[len(revisions):]:
                    revision.module.upgrade()
            assert connection.execute(text("SELECT * FROM product_packagings")).all() == packagings
            assert connection.execute(text("SELECT * FROM inventory_movements")).all() == movements
            assert connection.execute(text("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='products' ORDER BY name")).all() == indexes
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert compare_metadata(migration_context, Base.metadata) == []
    finally:
        engine.dispose()
