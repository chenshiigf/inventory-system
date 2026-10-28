"""Preflight regression tests for the exact class of production blocker from 2026-10-28."""
import hashlib
import json
from pathlib import Path
import sqlite3

from scripts.numbering_v2_preflight import audit


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _setup(tmp_path):
    business = tmp_path / "data" / "inventory.db"
    business.parent.mkdir()
    with sqlite3.connect(business) as db:
        db.executescript("""
        CREATE TABLE alembic_version(version_num TEXT);
        INSERT INTO alembic_version VALUES ('c8d9e0f1a2b3');
        CREATE TABLE products(id INTEGER PRIMARY KEY, product_code TEXT);
        INSERT INTO products VALUES (10,'01-03-001'),(20,'02-04-002');
        CREATE TABLE product_packagings(product_id INTEGER, carton_count INTEGER);
        INSERT INTO product_packagings VALUES (10,5),(20,0);
        """)
    visual = tmp_path / "data" / "visual-search"
    catalog = visual / "production-catalog"
    catalog.mkdir(parents=True)
    (visual / "calls.lock").write_bytes(b"0")
    (catalog / "calls.lock").write_bytes(b"0")
    identity = {
        "format": "inventory-production-catalog-v1",
        "source_id": "aliyun-inventory-production",
        "source_label": "SYNTHETIC ONLY",
    }
    (catalog / "source.json").write_text(json.dumps(identity), encoding="utf-8")
    with sqlite3.connect(catalog / "catalog.sqlite") as db:
        db.executescript("""
        CREATE TABLE mirror_meta(key TEXT PRIMARY KEY,document TEXT);
        CREATE TABLE mirror_products(source_id TEXT,id INTEGER,document TEXT);
        """)
        for key, value in {
            "identity": identity,
            "last_report": {
                "scope": "full",
                "selected_products": 1,
                "catalog_fingerprint": "a" * 64,
            },
        }.items():
            db.execute("INSERT INTO mirror_meta VALUES(?,?)", (key, json.dumps(value)))
        row = {
            "id": 10,
            "source_id": identity["source_id"],
            "product_key": identity["source_id"] + ":10",
            "product_code": "01-03-001",
            "source_present": True,
        }
        db.execute("INSERT INTO mirror_products VALUES(?,?,?)",
                   (identity["source_id"], 10, json.dumps(row)))
    namespace = "production-" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    index = visual / "production-indices" / namespace
    index.mkdir(parents=True)
    (index / "source-binding.json").write_text(json.dumps({
        "source_id": identity["source_id"],
        "source_identity": identity,
        "index_namespace": namespace,
    }), encoding="utf-8")
    with sqlite3.connect(index / "index.db") as db:
        db.executescript("""
        CREATE TABLE alembic_version(version_num TEXT);
        INSERT INTO alembic_version VALUES ('vs02');
        CREATE TABLE visual_source_context(source_id TEXT,source_identity TEXT);
        """)
        db.execute("INSERT INTO visual_source_context VALUES(?,?)",
                   (identity["source_id"], namespace))
    return business, visual, catalog


def test_missing_manifest_and_smaller_ai_mirror_are_not_blockers(tmp_path):
    business, visual, catalog = _setup(tmp_path)
    tracked = [business, catalog / "catalog.sqlite"]
    before = [_sha(p) for p in tracked]
    result = audit(business, visual)
    assert result["blockers"] == []
    assert result["main_revision"] == "c8d9e0f1a2b3"
    assert result["products"] == 2
    assert result["mirror_products"] == 1
    assert result["missing_from_mirror"] == 1
    assert result["manifest_files"] == 0
    assert result["total_cartons"] == 5
    assert result["old_code_mismatches"]["count"] == 0
    assert before == [_sha(p) for p in tracked]


def test_old_mirror_code_drift_is_reported_before_downtime(tmp_path):
    business, visual, catalog = _setup(tmp_path)
    with sqlite3.connect(catalog / "catalog.sqlite") as db:
        row = json.loads(db.execute("SELECT document FROM mirror_products").fetchone()[0])
        row["product_code"] = "01-03-999"
        db.execute("UPDATE mirror_products SET document=?", (json.dumps(row),))
    result = audit(business, visual)
    assert result["old_code_mismatches"]["count"] == 1
    assert result["old_code_mismatches"]["example_ids"] == [10]
    assert any("旧编号" in v for v in result["warnings"])


def test_unknown_ai_product_is_fatal(tmp_path):
    business, visual, catalog = _setup(tmp_path)
    with sqlite3.connect(catalog / "catalog.sqlite") as db:
        row = json.loads(db.execute("SELECT document FROM mirror_products").fetchone()[0])
        row["id"] = 999
        row["product_key"] = row["source_id"] + ":999"
        db.execute("UPDATE mirror_products SET id=?,document=?",
                   (999, json.dumps(row)))
    result = audit(business, visual)
    assert result["unknown_mirror_ids"] == [999]
    assert result["blockers"]


def test_sqlite_sidecar_is_refused_without_recovery(tmp_path):
    business, visual, _ = _setup(tmp_path)
    sidecar = Path(str(business) + "-wal")
    sidecar.write_bytes(b"do not remove")
    result = audit(business, visual)
    assert result["blockers"]
    assert sidecar.read_bytes() == b"do not remove"
