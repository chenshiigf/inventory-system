"""Stable packaging identity across edits and previously opened stock forms."""

from collections.abc import Generator
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import InventoryMovement, ProductPackaging
from app.routers.products import update_product
from app.schemas import ProductUpdate


@pytest.fixture
def context() -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            yield client, sessions
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def create_product(client, quantities=((24, 10), (36, 0))):
    response = client.post(
        "/api/products",
        json={
            "size": "identity test",
            "unit": "pcs",
            "price": "2.00",
            "remark": "before",
            "packagings": [
                {"packing_qty": qty, "carton_count": stock}
                for qty, stock in quantities
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def writes(product):
    return [
        {"id": row["id"], "packing_qty": row["packing_qty"]}
        for row in product["packagings"]
    ]


def patch(client, product, **fields):
    response = client.patch(f"/api/products/{product['id']}", json=fields)
    assert response.status_code == 200, response.text
    return response.json()


def stock(client, product_id, packaging_id, action):
    if action == "batch":
        return client.post(
            "/api/inventory/batch-outbound/commit",
            json={"items": [{
                "product_id": product_id,
                "product_packaging_id": packaging_id,
                "quantity": 1,
            }]},
        )
    payload = {"product_packaging_id": packaging_id}
    payload.update(
        {"actual_carton_count": 8, "remark": "identity regression"}
        if action == "adjust" else {"quantity": 1}
    )
    return client.post(f"/api/products/{product_id}/stock/{action}", json=payload)


@pytest.mark.parametrize("fields", [
    {"remark": "after"},
    {"size": "edited", "unit": "set", "price": "3.50",
     "image_path": "products/main/example.webp", "thumbnail_path": "products/thumbs/example.webp"},
])
def test_editor_payload_keeps_all_existing_ids(context, fields):
    client, _ = context
    product = create_product(client)
    result = patch(client, product, packagings=writes(product), **fields)
    assert result["packagings"] == product["packagings"]
    for key, value in fields.items():
        assert result[key] == value


def test_edit_quantity_in_place_and_keep_history_snapshot(context):
    client, sessions = context
    product = create_product(client)
    original = product["packagings"][0]
    movement = stock(client, product["id"], original["id"], "out")
    assert movement.status_code == 201
    rows = writes(product)
    rows[0]["packing_qty"] = 48
    result = patch(client, product, packagings=rows, unit="set")
    assert result["packagings"][0] == {
        "id": original["id"], "packing_qty": 48, "carton_count": 9, "sort_order": 0,
    }
    with sessions() as db:
        historical = db.get(InventoryMovement, movement.json()["id"])
        assert historical.product_packaging_id == original["id"]
        assert (historical.packing_qty_snapshot, historical.unit_snapshot) == (24, "pcs")
        assert (historical.before_carton_count, historical.after_carton_count) == (10, 9)


def test_reorder_changes_only_display_order(context):
    client, _ = context
    product = create_product(client, ((24, 10), (36, 5)))
    result = patch(client, product, packagings=list(reversed(writes(product))))
    assert result["packagings"] == [
        {**product["packagings"][1], "sort_order": 0},
        {**product["packagings"][0], "sort_order": 1},
    ]


def test_swap_quantities_without_unique_conflict_or_identity_swap(context):
    client, _ = context
    product = create_product(client, ((24, 10), (36, 5)))
    rows = writes(product)
    rows[0]["packing_qty"], rows[1]["packing_qty"] = 36, 24
    result = patch(client, product, packagings=rows)
    assert [(r["id"], r["packing_qty"], r["carton_count"]) for r in result["packagings"]] == [
        (product["packagings"][0]["id"], 36, 10),
        (product["packagings"][1]["id"], 24, 5),
    ]


def test_add_preserves_rows_and_starts_new_stock_at_zero(context):
    client, _ = context
    product = create_product(client)
    result = patch(client, product, packagings=[*writes(product), {"packing_qty": 48}])
    assert result["packagings"][:2] == product["packagings"]
    assert result["packagings"][2]["id"] > max(r["id"] for r in product["packagings"])
    assert result["packagings"][2]["carton_count"] == 0


def test_delete_only_zero_stock_row_and_keep_other_history_link(context):
    client, sessions = context
    product = create_product(client)
    first, removed = product["packagings"]
    movement = stock(client, product["id"], first["id"], "out")
    assert movement.status_code == 201
    result = patch(client, product, packagings=writes(product)[:1])
    assert result["packagings"] == [{**first, "carton_count": 9}]
    with sessions() as db:
        assert db.get(ProductPackaging, removed["id"]) is None
        assert db.get(InventoryMovement, movement.json()["id"]).product_packaging_id == first["id"]


@pytest.mark.parametrize("invalid", ["stocked_removal", "foreign_id", "missing_id", "duplicate_id", "stock_write"])
def test_invalid_diff_leaves_all_rows_unchanged(context, invalid):
    client, _ = context
    product = create_product(client)
    rows = writes(product)
    expected = 422
    if invalid == "stocked_removal":
        rows = rows[1:]
        rows[0]["packing_qty"] = 72
        expected = 409
    elif invalid == "foreign_id":
        other = create_product(client)
        rows[0]["id"] = other["packagings"][0]["id"]
    elif invalid == "missing_id":
        rows[0]["id"] = 999999
    elif invalid == "duplicate_id":
        rows[1]["id"] = rows[0]["id"]
    else:
        rows[0]["carton_count"] = 999
    response = client.patch(
        f"/api/products/{product['id']}", json={"remark": "must rollback", "packagings": rows},
    )
    assert response.status_code == expected
    result = client.get(f"/api/products/{product['id']}").json()
    assert result["packagings"] == product["packagings"]
    assert result["remark"] == "before"


@pytest.mark.parametrize("action", ["in", "out", "adjust", "batch"])
def test_open_stock_form_still_targets_same_row_after_edit(context, action):
    client, sessions = context
    product = create_product(client, ((24, 10), (36, 5)))
    preview = client.post("/api/inventory/batch-outbound/preview", json={"product_ids": [product["id"]]})
    assert preview.status_code == 200
    old_id = preview.json()["products"][0]["packagings"][0]["id"]
    patch(client, product, remark="edited", packagings=list(reversed(writes(product))))
    response = stock(client, product["id"], old_id, action)
    assert response.status_code in (200, 201), response.text
    with sessions() as db:
        target = db.get(ProductPackaging, old_id)
        assert target.packing_qty == 24
        assert target.carton_count == {"in": 11, "out": 9, "adjust": 8, "batch": 9}[action]
        assert db.get(ProductPackaging, product["packagings"][1]["id"]).carton_count == 5


@pytest.mark.parametrize("action", ["in", "out", "adjust", "batch"])
@pytest.mark.parametrize("creation", ["edit", "product", "inbound"])
def test_deleted_highest_id_is_not_reused_by_any_creation_entry(context, action, creation):
    client, sessions = context
    product = create_product(client)
    deleted_id = product["packagings"][-1]["id"]
    retained = patch(client, product, packagings=writes(product)[:1])
    if creation == "edit":
        patch(client, product, packagings=[*writes(retained), {"packing_qty": 48}])
    elif creation == "product":
        create_product(client, ((48, 3),))
    else:
        response = client.post(f"/api/products/{product['id']}/stock/in", json={"packing_qty": 48, "quantity": 3})
        assert response.status_code == 201
    with sessions() as db:
        before = [(r.id, r.carton_count) for r in db.scalars(select(ProductPackaging).order_by(ProductPackaging.id))]
        assert db.get(ProductPackaging, deleted_id) is None
        assert max(row[0] for row in before) > deleted_id
    response = stock(client, product["id"], deleted_id, action)
    assert response.status_code in (404, 409, 422)
    with sessions() as db:
        assert [(r.id, r.carton_count) for r in db.scalars(select(ProductPackaging).order_by(ProductPackaging.id))] == before


def test_mid_update_exception_rolls_back_fields_rows_links_and_releases_transaction(context):
    client, sessions = context
    product = create_product(client)
    removed_id = product["packagings"][-1]["id"]
    # A real historical link on the zero-stock row must also survive rollback.
    inbound = stock(client, product["id"], removed_id, "in")
    assert inbound.status_code == 201
    outbound = stock(client, product["id"], removed_id, "out")
    assert outbound.status_code == 201
    with sessions.begin() as db:
        db.execute(text(
            "CREATE TRIGGER reject_identity_insert BEFORE INSERT ON product_packagings "
            "WHEN NEW.packing_qty = 96 BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        ))
    payload = ProductUpdate.model_validate({
        "remark": "must rollback",
        "packagings": [{"id": product["packagings"][0]["id"], "packing_qty": 48}, {"packing_qty": 96}],
    })
    with sessions() as db:
        with pytest.raises(IntegrityError, match="injected failure"):
            update_product(product["id"], payload, db)
        assert db.is_active
        assert not db.in_transaction()
        assert db.get(InventoryMovement, inbound.json()["id"]).product_packaging_id == removed_id
    result = client.get(f"/api/products/{product['id']}").json()
    assert result["packagings"] == product["packagings"]
    assert result["remark"] == "before"
    patch(client, product, remark="later valid request", packagings=writes(product))


def test_batch_deleted_id_rolls_back_an_earlier_valid_item(context):
    client, sessions = context
    product = create_product(client)
    removed_id = product["packagings"][-1]["id"]
    result = patch(client, product, packagings=[writes(product)[0], {"packing_qty": 48}])
    response = client.post("/api/inventory/batch-outbound/commit", json={"items": [
        {"product_id": product["id"], "product_packaging_id": result["packagings"][0]["id"], "quantity": 1},
        {"product_id": product["id"], "product_packaging_id": removed_id, "quantity": 1},
    ]})
    assert response.status_code == 409
    with sessions() as db:
        assert db.get(ProductPackaging, result["packagings"][0]["id"]).carton_count == 10
        assert db.scalar(select(InventoryMovement.id)) is None


def migration_revisions():
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    return list(reversed(list(ScriptDirectory.from_config(config).walk_revisions())))


def seed_legacy_rows(connection):
    connection.execute(text(
        "INSERT INTO products (id, size, unit, price, is_active, created_at, updated_at) "
        "VALUES (1, 'old', 'pcs', 2, 1, '2026-10-01', '2026-10-01')"
    ))
    connection.execute(text(
        "INSERT INTO product_packagings (id, product_id, packing_qty, carton_count, sort_order, created_at, updated_at) "
        "VALUES (3, 1, NULL, 9, 0, '2026-10-01', '2026-10-01'), "
        "(7, 1, 24, 0, 1, '2026-10-01', '2026-10-01')"
    ))
    connection.execute(text(
        "INSERT INTO inventory_movements (id, product_id, product_packaging_id, movement_type, quantity, "
        "before_carton_count, after_carton_count, packing_qty_snapshot, unit_snapshot, created_at) "
        "VALUES (1, 1, 7, 'OUT', 2, 2, 0, 24, 'pcs', '2026-10-01')"
    ))


@pytest.mark.parametrize("populated", [False, True])
def test_migration_preserves_existing_rows_links_constraints_and_prevents_reuse(populated):
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            revisions = migration_revisions()
            with Operations.context(context):
                for revision in revisions[:-1]:
                    revision.module.upgrade()
                assert revisions[-1].revision == "b2c3d4e5f6a7"
                if populated:
                    seed_legacy_rows(connection)
                rows_before = connection.execute(text("SELECT * FROM product_packagings")).all()
                movements_before = connection.execute(text("SELECT * FROM inventory_movements")).all()
                revisions[-1].module.upgrade()
            assert connection.execute(text("SELECT * FROM product_packagings")).all() == rows_before
            assert connection.execute(text("SELECT * FROM inventory_movements")).all() == movements_before
            ddl = connection.scalar(text("SELECT sql FROM sqlite_master WHERE name = 'product_packagings'"))
            assert "AUTOINCREMENT" in ddl
            assert compare_metadata(context, Base.metadata) == []
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            if populated:
                connection.execute(text("DELETE FROM product_packagings WHERE id = 7"))
                assert connection.scalar(text("SELECT product_packaging_id FROM inventory_movements")) is None
                connection.execute(text(
                    "INSERT INTO product_packagings (product_id, packing_qty, carton_count, sort_order, created_at, updated_at) "
                    "VALUES (1, 36, 0, 0, '2026-10-01', '2026-10-01')"
                ))
                assert connection.scalar(text("SELECT max(id) FROM product_packagings")) > 7
    finally:
        engine.dispose()


def test_failed_migration_rolls_back_rebuild_and_history_links(monkeypatch):
    engine = create_engine("sqlite://")
    revisions = migration_revisions()
    try:
        with engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                for revision in revisions[:-1]:
                    revision.module.upgrade()
            seed_legacy_rows(connection)
            before_rows = connection.execute(text("SELECT * FROM product_packagings")).all()
            before_movements = connection.execute(text("SELECT * FROM inventory_movements")).all()
            before_ddl = connection.scalar(text("SELECT sql FROM sqlite_master WHERE name = 'product_packagings'"))
        with pytest.raises(RuntimeError, match="injected link restoration failure"):
            with engine.begin() as connection:
                original_execute = connection.execute

                def fail_link_restoration(statement, *args, **kwargs):
                    if str(statement).startswith("UPDATE inventory_movements"):
                        raise RuntimeError("injected link restoration failure")
                    return original_execute(statement, *args, **kwargs)

                with monkeypatch.context() as patcher:
                    patcher.setattr(connection, "execute", fail_link_restoration)
                    with Operations.context(MigrationContext.configure(connection)):
                        revisions[-1].module.upgrade()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM product_packagings")).all() == before_rows
            assert connection.execute(text("SELECT * FROM inventory_movements")).all() == before_movements
            assert connection.scalar(text("SELECT sql FROM sqlite_master WHERE name = 'product_packagings'")) == before_ddl
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()
