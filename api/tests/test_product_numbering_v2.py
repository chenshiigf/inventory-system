from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app import product_codes
from app.models import Product, ProductCodeMonth
from .test_product_codes import create_product, product_code_api  # noqa: F401


def test_uncategorized_first_product_and_original_code(product_code_api):
    client, sessions = product_code_api
    first = create_product(client, None, original_product_code="工厂-001")
    second = create_product(client, None)
    assert [first["product_code"], second["product_code"]] == ["261001", "261002"]
    assert first["original_product_code"] == "工厂-001"
    with sessions() as db:
        assert db.get(ProductCodeMonth, "202610").next_sequence == 3


def test_beijing_boundary_and_reopening_month_does_not_reset(product_code_api, monkeypatch):
    client, sessions = product_code_api
    clock = [datetime(2026, 10, 31, 15, 59, 59, tzinfo=timezone.utc)]
    monkeypatch.setattr(product_codes, "product_creation_time", lambda: clock[0])
    first = create_product(client, None)
    clock[0] = datetime(2026, 10, 31, 16, tzinfo=timezone.utc)
    second = create_product(client, None)
    assert [first["product_code"], second["product_code"]] == ["261001", "261101"]
    assert second["created_at"].startswith("2026-10-31T16:00:00")
    clock[0] = datetime(2026, 10, 30, tzinfo=timezone.utc)
    assert create_product(client, None)["product_code"] == "261002"
    with sessions() as db:
        assert db.get(ProductCodeMonth, "202611").next_sequence == 2


def test_99_100_101_and_deleted_row_never_recycles(product_code_api):
    client, sessions = product_code_api
    with sessions.begin() as db:
        db.add(ProductCodeMonth(period_ym="202610", next_sequence=99))
    products = [create_product(client, None) for _ in range(3)]
    assert [p["product_code"] for p in products] == ["261099", "2610100", "2610101"]
    # Even physical deletion in a disposable fixture cannot rewind the counter.
    with sessions.begin() as db:
        db.delete(db.get(Product, products[-1]["id"]))
    assert create_product(client, None)["product_code"] == "2610102"


def test_existing_numeric_collision_and_inactive_rows_seed_counter(product_code_api):
    client, sessions = product_code_api
    with sessions.begin() as db:
        db.add_all([Product(product_code="261001", is_active=False),
                    Product(product_code="2610100"), Product(product_code="01-01-001")])
    assert create_product(client, None)["product_code"] == "2610101"
    with sessions() as db:
        assert db.get(ProductCodeMonth, "202610").next_sequence == 102


def test_history_prefix_is_not_assumed_disjoint(product_code_api, monkeypatch):
    client, sessions = product_code_api
    monkeypatch.setattr(product_codes, "product_creation_time", lambda: datetime(2025, 10, 1, tzinfo=timezone.utc))
    with sessions.begin() as db:
        db.add(Product(product_code="251001", is_active=False))
    assert create_product(client, None)["product_code"] == "251002"


def test_sort_mixed_formats_long_suffixes_search_and_pagination(product_code_api):
    client, sessions = product_code_api
    expected = ["01-02-003", "01-02-100", "02-01-001", "250001", "250010", "251001", "251300", "252523",
                "261001", "261099", "2610100", "2610101", "261099999999999999999999999",
                "261101", "270101"]
    with sessions.begin() as db:
        db.add_all(Product(product_code=code, size="排序") for code in reversed(expected))
        db.add_all([Product(product_code="INVALID"), Product(product_code=None)])
    got = []
    for page in range(1, 10):
        response = client.get("/api/products", params={"page": page, "page_size": 2})
        assert response.status_code == 200
        got.extend(p["product_code"] for p in response.json()["items"])
    assert got[:len(expected)] == expected
    for query, match in [("2610100", "2610100"), ("01-02-003", "01-02-003")]:
        response = client.get("/api/products", params={"search": query})
        assert [p["product_code"] for p in response.json()["items"]] == [match]


def test_old_backfill_refused_without_writes(product_code_api):
    _, sessions = product_code_api
    with sessions.begin() as db:
        db.add(Product(product_code=None))
    with sessions.begin() as db, pytest.raises(ValueError, match="旧三段式回填"):
        product_codes.backfill_product_codes(db)
    with sessions() as db:
        assert db.scalar(select(Product.product_code)) is None
        assert db.scalar(select(ProductCodeMonth)) is None


def test_stock_packaging_and_activation_do_not_renumber(product_code_api):
    client, _ = product_code_api
    product = create_product(client, None)
    pid, packaging = product["id"], product["packagings"][0]
    assert client.patch(f"/api/products/{pid}", json={"packagings": [{"id": packaging["id"], "packing_qty": 48}]}).status_code == 200
    for path in ("in", "out"):
        response = client.post(f"/api/products/{pid}/stock/{path}", json={"product_packaging_id": packaging["id"], "quantity": 1})
        assert response.status_code == 201, response.text
    assert client.post(f"/api/products/{pid}/deactivate").status_code == 200
    assert client.post(f"/api/products/{pid}/activate").status_code == 200
    assert client.get(f"/api/products/{pid}").json()["product_code"] == product["product_code"]


def test_missing_schema_fails_clearly_without_changing_existing_rows(product_code_api):
    client, sessions = product_code_api
    ProductCodeMonth.__table__.drop(sessions.kw["bind"])
    response = client.post("/api/products", json={"packagings": [{"packing_qty": 1, "carton_count": 0}]})
    assert response.status_code == 409
    assert "结构迁移" in response.json()["detail"]


def test_failed_validation_does_not_allocate(product_code_api):
    client, sessions = product_code_api
    assert client.post("/api/products", json={"category_id": 999, "packagings": [{"packing_qty": 1, "carton_count": 0}]}).status_code == 422
    assert create_product(client, None)["product_code"] == "261001"


def test_two_independent_clients_share_counter(product_code_api):
    from concurrent.futures import ThreadPoolExecutor
    from app.database import begin_write_transaction
    from app.schemas import ProductCreate
    from app.services.products import create_product_record
    _, sessions = product_code_api

    def create_one(_):
        with sessions() as db:
            begin_write_transaction(db)
            product = create_product_record(db, ProductCreate(packagings=[{"packing_qty": 1, "carton_count": 0}]))
            code = product.product_code
            db.commit()
            return code

    with ThreadPoolExecutor(max_workers=8) as workers:
        codes = list(workers.map(create_one, range(24)))
    assert set(codes) == {f"2610{n:02d}" for n in range(1, 25)}


def test_independent_processes_share_sqlite_write_reservation(product_code_api):
    import json
    import subprocess
    import sys
    _, sessions = product_code_api
    url = str(sessions.kw["bind"].url)
    script = """
import json, sys
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app import product_codes
from app.database import begin_write_transaction
from app.schemas import ProductCreate
from app.services.products import create_product_record
product_codes.product_creation_time = lambda: datetime(2026, 10, 17, tzinfo=timezone.utc)
engine = create_engine(sys.argv[1])
codes = []
for _ in range(6):
    with Session(engine) as db:
        begin_write_transaction(db)
        row = create_product_record(db, ProductCreate(packagings=[{'packing_qty': 1, 'carton_count': 0}]))
        codes.append(row.product_code)
        db.commit()
engine.dispose()
print(json.dumps(codes))
"""
    processes = [subprocess.Popen([sys.executable, "-c", script, url], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf8") for _ in range(2)]
    codes = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            codes.extend(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert set(codes) == {f"2610{n:02d}" for n in range(1, 13)}


@pytest.mark.parametrize("next_sequence", [product_codes.MAX_SEQUENCE + 1])
def test_counter_exhaustion_fails_without_consuming_number(product_code_api, next_sequence):
    client, sessions = product_code_api
    with sessions.begin() as db:
        db.add(ProductCodeMonth(period_ym="202610", next_sequence=next_sequence))
    response = client.post("/api/products", json={"packagings": [{"packing_qty": 1, "carton_count": 0}]})
    assert response.status_code == 409
    with sessions() as db:
        assert db.get(ProductCodeMonth, "202610").next_sequence == next_sequence
        assert db.scalar(select(Product)) is None
