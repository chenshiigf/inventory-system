import hashlib
import sqlite3
from pathlib import Path

import pytest

from app.services.product_code_rehearsal import RehearsalError, create_fixture, rehearse


def test_rehearsal_preserves_all_noncode_rows_and_exports_map(tmp_path):
    path = create_fixture(tmp_path / "synthetic")
    before_bytes = path.read_bytes()
    before_files = set(path.parent.iterdir())
    plan = rehearse(path)
    assert path.read_bytes() == before_bytes
    assert set(path.parent.iterdir()) == before_files
    assert plan["counts"] == {"products": 7, "product_packagings": 7, "inventory_movements": 1}
    assert plan["carton_count"] == 28
    assert [r["new_code"] for r in plan["mapping"]] == [f"25{n:04d}" for n in range(1, 7)]
    assert plan["mapping"][1]["is_active"] is False
    result = rehearse(path, execute=True, confirm_isolated=True, expected_plan_sha256=plan["plan_sha256"])
    assert result["invariants_preserved"]
    assert Path(result["mapping_file"]).is_file()
    assert "250002" in Path(result["mapping_file"]).read_text(encoding="utf-8-sig")
    after = rehearse(path)
    assert after["invariants_sha256"] == plan["invariants_sha256"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT product_code FROM products WHERE id=100").fetchone()[0] == "2610100"
        assert db.execute("SELECT next_sequence FROM product_code_months WHERE period_ym='202610'").fetchone()[0] == 101
    with pytest.raises(RehearsalError, match="已完成"):
        rehearse(path, execute=True, confirm_isolated=True, expected_plan_sha256=after["plan_sha256"])


def test_range_conflict_rejected_with_zero_database_changes(tmp_path):
    path = create_fixture(tmp_path / "conflict", conflict=True)
    before = path.read_bytes()
    plan = rehearse(path)
    assert not plan["can_execute"] and plan["conflicts"][0]["id"] == 100
    with pytest.raises(RehearsalError, match="范围外"):
        rehearse(path, execute=True, confirm_isolated=True, expected_plan_sha256=plan["plan_sha256"])
    assert path.read_bytes() == before


def test_execute_needs_flags_and_matching_snapshot(tmp_path):
    path = create_fixture(tmp_path / "guards")
    plan = rehearse(path)
    for kwargs in ({"execute": True}, {"execute": True, "confirm_isolated": True}):
        with pytest.raises(RehearsalError, match="显式"):
            rehearse(path, **kwargs)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE product_packagings SET carton_count=carton_count+1 WHERE id=10")
    with pytest.raises(RehearsalError, match="已变化"):
        rehearse(path, execute=True, confirm_isolated=True, expected_plan_sha256=plan["plan_sha256"])


def test_arbitrary_database_and_workspace_path_rejected(tmp_path):
    path = tmp_path / "ordinary.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE products(id INTEGER)")
    before = hashlib.sha256(path.read_bytes()).digest()
    with pytest.raises(RehearsalError):
        rehearse(path)
    with pytest.raises(RehearsalError, match="临时目录"):
        rehearse(Path(__file__).resolve().parents[1] / "data" / "inventory.db")
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_failure_rolls_back_two_stage_renumbering(tmp_path):
    path = create_fixture(tmp_path / "rollback")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER fail_rehearsal BEFORE UPDATE OF product_code ON products WHEN new.product_code='250004' BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        before = db.execute("SELECT * FROM products ORDER BY id").fetchall()
    plan = rehearse(path)
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        rehearse(path, execute=True, confirm_isolated=True, expected_plan_sha256=plan["plan_sha256"])
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM products ORDER BY id").fetchall() == before
        assert db.execute("SELECT * FROM product_code_months").fetchall() == []
        assert db.execute("SELECT executed FROM numbering_rehearsal_guard").fetchone()[0] == 0
    assert len(list(path.parent.glob("mapping-*.csv"))) == 1


def test_cli_default_readonly_and_explicit_execution(tmp_path):
    import json
    import subprocess
    import sys
    script = Path(__file__).resolve().parents[1] / "scripts" / "rehearse_product_codes.py"

    def call(*args):
        return subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, encoding="utf8")

    created = call("create-fixture", "--directory", tmp_path / "cli")
    assert created.returncode == 0, created.stderr
    path = Path(json.loads(created.stdout)["synthetic_database"])
    before = path.read_bytes()
    plan = call("plan", "--database", path)
    assert plan.returncode == 0, plan.stderr
    assert path.read_bytes() == before
    result = call("plan", "--database", path, "--execute", "--confirm-isolated-rehearsal",
                  "--expected-plan-sha256", json.loads(plan.stdout)["plan_sha256"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["invariants_preserved"]


def test_legacy_cli_refuses_before_database_import():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-m", "app.backfill_product_codes"], capture_output=True, encoding="utf8")
    assert result.returncode != 0
    assert "已停用" in result.stderr


def test_unexpected_code_trigger_rolls_back_outside_changes(tmp_path):
    path = create_fixture(tmp_path / "unexpected-code")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TRIGGER alter_outside AFTER UPDATE OF product_code ON products WHEN new.id=12 BEGIN UPDATE products SET product_code='unexpected' WHERE id=100; END")
    plan = rehearse(path)
    with pytest.raises(RehearsalError, match="最终编号"):
        rehearse(path, execute=True, confirm_isolated=True, expected_plan_sha256=plan["plan_sha256"])
    assert rehearse(path)["plan_sha256"] == plan["plan_sha256"]


def test_synthetic_production_scale_preserves_2523_products_2573_packagings_187_movements(tmp_path):
    path = create_fixture(tmp_path / "production-scale-synthetic")
    with sqlite3.connect(path) as db:
        for pid in range(101, 2617):
            db.execute("""INSERT INTO products
                (id,product_code,original_product_code,category_id,warehouse_id,is_active,size,unit,created_at,updated_at)
                SELECT ?,?,NULL,category_id,warehouse_id,1,'合成批量商品','pcs',created_at,updated_at
                FROM products WHERE id=2""", (pid, f"LEGACY-{pid}"))
            db.execute("""INSERT INTO product_packagings
                (product_id,packing_qty,carton_count,sort_order,created_at,updated_at)
                SELECT ?,24,3,0,created_at,updated_at FROM products WHERE id=2""", (pid,))
        for pid in range(101, 151):
            db.execute("""INSERT INTO product_packagings
                (product_id,packing_qty,carton_count,sort_order,created_at,updated_at)
                SELECT ?,48,2,1,created_at,updated_at FROM products WHERE id=2""", (pid,))
        for index in range(186):
            incoming = index % 2 == 0
            db.execute("""INSERT INTO inventory_movements
                (product_id,product_packaging_id,warehouse_id,movement_type,quantity,before_carton_count,
                 after_carton_count,packing_qty_snapshot,unit_snapshot,created_at)
                SELECT 2,10,1,?,1,?,?,24,'pcs',created_at FROM products WHERE id=2""",
                       ("IN" if incoming else "OUT", 1 if incoming else 2, 2 if incoming else 1))
    plan = rehearse(path, through_product_id=2616)
    assert plan["counts"] == {"products": 2523, "product_packagings": 2573, "inventory_movements": 187}
    assert plan["carton_count"] == 7676
    result = rehearse(path, through_product_id=2616, execute=True, confirm_isolated=True,
                      expected_plan_sha256=plan["plan_sha256"])
    after = rehearse(path, through_product_id=2616)
    assert result["invariants_preserved"]
    assert after["counts"] == plan["counts"]
    assert after["carton_count"] == plan["carton_count"]
    assert after["invariants_sha256"] == plan["invariants_sha256"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT product_code FROM products WHERE id=2").fetchone()[0] == "250001"
        assert db.execute("SELECT product_code FROM products ORDER BY id DESC LIMIT 1").fetchone()[0] == "252523"
