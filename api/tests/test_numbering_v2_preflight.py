"""Preflight regression tests for the exact class of production blocker from 2026-10-28."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import sys
import pytest

from scripts.numbering_v2_preflight import audit as _audit


def _snapshot(root):
    return {str(p.relative_to(root)): _sha(p) for p in root.rglob("*") if p.is_file()}


def audit(database, visual):
    """Every scenario proves all input files and directory entries unchanged."""
    before = _snapshot(database.parent)
    result = _audit(database, visual)
    assert before == _snapshot(database.parent)
    for info in result["paths"].values():
        if "sha256_before" in info:
            assert info["sha256_before"] == info["sha256_after"]
    return result


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _setup(tmp_path):
    business = tmp_path / "data" / "inventory.db"
    business.parent.mkdir()
    with closing(sqlite3.connect(business)) as db, db:
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
    with closing(sqlite3.connect(catalog / "catalog.sqlite")) as db, db:
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
        "format": "production-visual-index-v1",
        "source_id": identity["source_id"],
        "source_identity": identity,
        "index_namespace": namespace,
    }), encoding="utf-8")
    with closing(sqlite3.connect(index / "index.db")) as db, db:
        db.executescript("""
        CREATE TABLE alembic_version(version_num TEXT);
        INSERT INTO alembic_version VALUES ('vs02');
        CREATE TABLE visual_source_context(index_key TEXT,source_id TEXT,source_identity TEXT,
          catalog_fingerprint TEXT,model TEXT,dimension INTEGER,version TEXT,preprocessing_version TEXT,updated_at TEXT);
        CREATE TABLE visual_embeddings(product_id INTEGER,index_key TEXT,image_identifier TEXT,image_hash TEXT,
          embedding_model TEXT,embedding_dimension INTEGER,embedding_version TEXT,preprocessing_version TEXT,
          embedding_vector BLOB,indexed_at TEXT,indexing_status TEXT,error TEXT);
        CREATE TABLE visual_image_vectors(index_key TEXT,image_hash TEXT,embedding_vector BLOB,created_at TEXT);
        CREATE TABLE visual_jobs(id TEXT,index_key TEXT,status TEXT);
        CREATE TABLE visual_calls(id INTEGER,index_key TEXT,usage TEXT);
        CREATE TABLE visual_queries(id TEXT,index_key TEXT,candidate_ids TEXT);
        CREATE TABLE visual_feedback(query_id TEXT,selected_product_id INTEGER,no_match INTEGER);
        """)
        from app.services.visual_search.config import VisualConfig
        index_key = VisualConfig(source_identity=namespace).index_key
        db.execute("INSERT INTO visual_source_context VALUES(?,?,?,?,?,?,?,?,?)",
                   (index_key, identity["source_id"], namespace, "a" * 64,
                    "qwen3-vl-embedding", 2560, "v1", "exif-rgb-white-jpeg90-1024-v1", "2026-10-28"))
    with closing(sqlite3.connect(visual / "daily-budget.sqlite3")) as db, db:
        db.executescript("""
        PRAGMA user_version=1;
        CREATE TABLE daily_budget(day TEXT PRIMARY KEY,call_count INTEGER,budgeted_cny TEXT,actual_cny TEXT,unknown_calls INTEGER);
        CREATE TABLE call_reservations(id TEXT PRIMARY KEY,day TEXT,reserve_cny TEXT,actual_cny TEXT,state TEXT);
        INSERT INTO daily_budget VALUES('2026-10-28',2,'0.02','0.01',0);
        INSERT INTO call_reservations VALUES('synthetic','2026-10-28','0.01','0.005','completed');
        """)
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
    assert result["status"] == "WARNING"
    assert result["checks"]["budget"] == "PASS"
    assert result["checks"]["runtime_freeze"] == "PENDING"
    assert result["checks"]["production_permissions"] == "PENDING"
    assert result["runtime_checks"]
    assert len([v for v in result["paths"].values() if "sha256_before" in v]) == 8
    assert before == [_sha(p) for p in tracked]


def test_old_mirror_code_drift_is_reported_before_downtime(tmp_path):
    business, visual, catalog = _setup(tmp_path)
    with closing(sqlite3.connect(catalog / "catalog.sqlite")) as db, db:
        row = json.loads(db.execute("SELECT document FROM mirror_products").fetchone()[0])
        row["product_code"] = "01-03-999"
        db.execute("UPDATE mirror_products SET document=?", (json.dumps(row),))
    result = audit(business, visual)
    assert result["old_code_mismatches"]["count"] == 1
    assert result["old_code_mismatches"]["example_ids"] == [10]
    assert any("旧编号" in v for v in result["warnings"])


def test_unknown_ai_product_is_fatal(tmp_path):
    business, visual, catalog = _setup(tmp_path)
    with closing(sqlite3.connect(catalog / "catalog.sqlite")) as db, db:
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


def _databases(business, visual, catalog):
    return {"business": business, "mirror": catalog / "catalog.sqlite",
            "index": next((visual / "production-indices").glob("*/index.db")),
            "budget": visual / "daily-budget.sqlite3"}


@pytest.mark.parametrize("target", ["business", "mirror", "index", "budget"])
@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_all_database_sidecars_are_blocked_and_preserved(tmp_path, target, suffix):
    business, visual, catalog = _setup(tmp_path)
    database = _databases(business, visual, catalog)[target]
    Path(str(database) + suffix).write_bytes(b"synthetic sidecar - never recover")
    result = audit(business, visual)
    assert result["status"] == "BLOCKED"
    assert result["sqlite"][str(database)]["journal_mode"] is None
    assert any("SQLite 日志" in b for b in result["blockers"])


def test_wal_header_without_sidecars_is_not_opened(tmp_path):
    business, visual, _ = _setup(tmp_path)
    with closing(sqlite3.connect(business)) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    assert not Path(str(business) + "-wal").exists()
    result = audit(business, visual)
    assert result["status"] == "BLOCKED"
    assert result["sqlite"][str(business)]["journal_mode"] == "wal"


def test_null_duplicate_blank_and_abnormal_old_codes_are_bounded_warnings(tmp_path):
    business, visual, _ = _setup(tmp_path)
    with closing(sqlite3.connect(business)) as db, db:
        db.executemany("INSERT INTO products VALUES(?,?)", [(30, None), (40, " "), (50, "oops")]
                       + [(i, "01-03-001") for i in range(100, 130)])
    result = audit(business, visual)
    assert result["blockers"] == []
    assert result["checks"]["business"] == "WARNING"
    assert result["numbering"]["null"] == {"count": 1, "example_ids": [30]}
    assert result["numbering"]["blank"]["count"] == 1
    assert result["numbering"]["abnormal"]["count"] == 2
    assert result["numbering"]["duplicates"]["count"] == 1
    assert result["numbering"]["duplicates"]["rows"] == 31
    assert len(result["numbering"]["duplicates"]["example_ids"]) == 20


@pytest.mark.parametrize("key,value", [("last_report", []), ("last_report", None),
    ("last_report", {"scope": "full", "selected_products": 1, "catalog_fingerprint": "bad"}),
    ("last_report", {"scope": "full", "selected_products": True, "catalog_fingerprint": "a" * 64}),
    ("identity", {"source_id": "wrong"})])
def test_corrupt_mirror_metadata_is_a_structured_blocker(tmp_path, key, value):
    business, visual, catalog = _setup(tmp_path)
    with closing(sqlite3.connect(catalog / "catalog.sqlite")) as db, db:
        db.execute("UPDATE mirror_meta SET document=? WHERE key=?", (json.dumps(value), key))
    result = audit(business, visual)
    assert result["status"] == "BLOCKED"
    assert result["checks"]["index"] == "PENDING"


@pytest.mark.parametrize("payload", ["[]", "null", "{bad-json", '{"source_id":"wrong"}'])
def test_invalid_source_json_does_not_crash(tmp_path, payload):
    business, visual, catalog = _setup(tmp_path)
    (catalog / "source.json").write_text(payload, encoding="utf8")
    result = audit(business, visual)
    assert result["status"] == "BLOCKED"
    assert result["checks"]["budget"] == "PASS"


@pytest.mark.parametrize("target", ["business", "mirror", "index", "budget", "source", "binding", "lock"])
def test_missing_required_files_are_reported_without_creating_them(tmp_path, target):
    business, visual, catalog = _setup(tmp_path)
    paths = _databases(business, visual, catalog)
    paths.update(source=catalog / "source.json", binding=paths["index"].parent / "source-binding.json",
                 lock=visual / "calls.lock")
    paths[target].unlink()
    result = audit(business, visual)
    assert result["status"] == "BLOCKED"
    assert not paths[target].exists()


@pytest.mark.parametrize("target,table", [("business", "product_packagings"), ("mirror", "mirror_meta"),
    ("index", "visual_image_vectors"), ("budget", "call_reservations")])
def test_missing_schema_is_blocked(tmp_path, target, table):
    business, visual, catalog = _setup(tmp_path)
    with closing(sqlite3.connect(_databases(business, visual, catalog)[target])) as db:
        db.execute(f'DROP TABLE "{table}"')
    assert audit(business, visual)["status"] == "BLOCKED"


@pytest.mark.parametrize("target", ["business", "mirror", "index", "budget"])
def test_corrupt_databases_remain_untouched(tmp_path, target):
    business, visual, catalog = _setup(tmp_path)
    _databases(business, visual, catalog)[target].write_bytes(b"not a sqlite database")
    assert audit(business, visual)["status"] == "BLOCKED"


@pytest.mark.parametrize("target", ["budget", "lock", "directory"])
def test_local_permission_failure_does_not_pass(tmp_path, monkeypatch, target):
    import scripts.numbering_v2_preflight as module
    business, visual, _ = _setup(tmp_path)
    denied = {"budget": visual / "daily-budget.sqlite3", "lock": visual / "calls.lock", "directory": visual}[target]
    original = module.os.access
    monkeypatch.setattr(module.os, "access", lambda path, mode: False if path == denied else original(path, mode))
    result = audit(business, visual)
    assert result["status"] == "BLOCKED"
    assert result["checks"]["production_permissions"] == "PENDING"
    assert any("当前用户无法" in b for b in result["blockers"])


def test_read_only_copy_does_not_claim_production_write_access(tmp_path, monkeypatch):
    import scripts.numbering_v2_preflight as module
    business, visual, _ = _setup(tmp_path)
    original = module.os.access
    monkeypatch.setattr(module.os, "access", lambda path, mode: False if mode == module.os.W_OK else original(path, mode))
    result = audit(business, visual)
    assert result["status"] == "WARNING"
    assert result["blockers"] == []
    assert all(p.get("production_permission_status") != "PASS" for p in result["paths"].values())


@pytest.mark.parametrize("field,value", [("source_identity", "wrong"), ("catalog_fingerprint", "b" * 64),
                                        ("model", "wrong"), ("dimension", 0), ("index_key", "wrong")])
def test_index_context_drift_is_blocked(tmp_path, field, value):
    business, visual, catalog = _setup(tmp_path)
    with closing(sqlite3.connect(_databases(business, visual, catalog)["index"])) as db, db:
        db.execute(f'UPDATE visual_source_context SET "{field}"=?', (value,))
    assert audit(business, visual)["checks"]["mirror_index"] == "BLOCKED"


def test_budget_version_and_lock_file_type_are_checked(tmp_path):
    business, visual, _ = _setup(tmp_path)
    with closing(sqlite3.connect(visual / "daily-budget.sqlite3")) as db:
        db.execute("PRAGMA user_version=999")
    (visual / "calls.lock").unlink()
    (visual / "calls.lock").mkdir()
    result = audit(business, visual)
    assert result["checks"]["budget"] == "BLOCKED"
    assert any("类型不符" in b for b in result["blockers"])


def test_cli_stdout_is_json_and_summary_is_chinese(tmp_path):
    business, visual, _ = _setup(tmp_path)
    before = _snapshot(business.parent)
    script = Path(__file__).resolve().parents[1] / "scripts" / "numbering_v2_preflight.py"
    result = subprocess.run([sys.executable, "-B", str(script), "--database", str(business), "--visual-root", str(visual)],
                            capture_output=True, text=True, encoding="utf8", env={**os.environ, "PYTHONIOENCODING": "utf8"})
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "WARNING"
    assert "只读预检 WARNING" in result.stderr
    assert before == _snapshot(business.parent)
    business.unlink()
    result = subprocess.run([sys.executable, "-B", str(script), "--database", str(business), "--visual-root", str(visual)],
                            capture_output=True, text=True, encoding="utf8", env={**os.environ, "PYTHONIOENCODING": "utf8"})
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "BLOCKED"
