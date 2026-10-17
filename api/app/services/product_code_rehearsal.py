"""Synthetic, temporary-database-only rehearsal. Never accepts a business DB."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from uuid import uuid4

MARKER = "numbering-rehearsal.json"


class RehearsalError(ValueError):
    pass


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _temporary_path(path: Path) -> Path:
    path = Path(path).absolute()
    root = Path(tempfile.gettempdir()).resolve()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise RehearsalError("拒绝链接或 junction 路径")
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise RehearsalError("仅允许系统临时目录内由本工具新建的合成数据库")
    return resolved


def create_fixture(directory: Path, *, conflict: bool = False) -> Path:
    """Create new synthetic rows, never copy a database or any product image."""
    directory = _temporary_path(directory)
    directory.mkdir(parents=False, exist_ok=False)
    path = directory / "fixture.sqlite3"
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Category, InventoryMovement, Product, ProductPackaging, Warehouse

    engine = create_engine(f"sqlite:///{path.as_posix()}")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as db, db.begin():
            db.add_all([Category(id=1, name="合成分类", code="01"),
                        Category(id=2, name="合成小类", code="01", parent_id=1),
                        Warehouse(id=1, name="合成仓库")])
            db.flush()
            codes = ["250002", "250001", "01-01-003", None, "261099", "旧号"]
            for index, code in enumerate(codes, 1):
                db.add(Product(id=index * 2, product_code=code, original_product_code=f"原货号-{index}",
                               category_id=2 if index != 4 else None, warehouse_id=1,
                               is_active=index != 2, size=f"合成商品-{index}", unit="pcs",
                               packagings=[ProductPackaging(id=index * 10, packing_qty=24,
                                                            carton_count=index)]))
            db.add(Product(id=100, product_code="250003" if conflict else "2610100",
                           size="保留的新商品", packagings=[ProductPackaging(id=1000, packing_qty=12, carton_count=7)]))
            db.flush()
            db.add(InventoryMovement(product_id=2, product_packaging_id=10, warehouse_id=1,
                                     movement_type="IN", quantity=1, before_carton_count=0,
                                     after_carton_count=1, packing_qty_snapshot=24, unit_snapshot="pcs"))
    finally:
        engine.dispose()
    token = uuid4().hex
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE numbering_rehearsal_guard(token TEXT PRIMARY KEY, executed INTEGER NOT NULL)")
        db.execute("INSERT INTO numbering_rehearsal_guard VALUES (?,0)", (token,))
    marker = {"format": "synthetic-numbering-rehearsal-v1", "database": str(path), "token": token,
              "through_product_id": 12}
    (directory / MARKER).write_text(json.dumps(marker), encoding="utf8")
    return path


def _validate(path: Path) -> tuple[Path, dict]:
    path = _temporary_path(path)
    if (path.parent / "offline-backup-rehearsal.json").exists():
        raise RehearsalError("真实备份副本不得使用合成数据入口")
    marker_path = path.parent / MARKER
    _temporary_path(marker_path)
    if not path.is_file() or not marker_path.is_file():
        raise RehearsalError("缺少工具生成的合成数据库或身份标记")
    if path.stat().st_nlink != 1 or marker_path.stat().st_nlink != 1:
        raise RehearsalError("拒绝硬链接")
    marker = json.loads(marker_path.read_text(encoding="utf8"))
    if (marker.get("format") != "synthetic-numbering-rehearsal-v1" or
            marker.get("database") != str(path)):
        raise RehearsalError("合成数据库身份不匹配")
    return path, marker


def _connect(path: Path, marker: dict, *, write=False):
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='offline_numbering_run'").fetchone():
            raise RehearsalError("真实备份副本不得伪造合成身份")
        rows = db.execute("SELECT token,executed FROM numbering_rehearsal_guard").fetchall()
        if len(rows) != 1 or rows[0]["token"] != marker["token"]:
            raise RehearsalError("合成数据库内部身份不匹配")
        return db
    except BaseException:
        db.close()
        raise


def _snapshot(db, *, invariant=False):
    result = {}
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') ORDER BY name")]
    for table in tables:
        if invariant and table in {"product_code_months", "numbering_rehearsal_guard"}:
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        rows = []
        for row in db.execute(f"SELECT * FROM {quoted}"):
            value = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in dict(row).items()
                     if not (invariant and table == "products" and k == "product_code")}
            rows.append(value)
        result[table] = sorted(rows, key=lambda value: json.dumps(value, sort_keys=True))
    return result


def _plan(db, marker, through_product_id):
    cutoff = marker["through_product_id"] if through_product_id is None else through_product_id
    if type(cutoff) is not int or cutoff < 1:
        raise RehearsalError("必须指定正整数历史 ID 截止值")
    products = [dict(r) for r in db.execute("SELECT id,product_code,is_active FROM products ORDER BY id")]
    selected = [r for r in products if r["id"] <= cutoff]
    if not selected or len(selected) > 9999:
        raise RehearsalError("历史商品数量必须为 1 至 9999")
    mapping = [{"product_id": row["id"], "old_code": row["product_code"],
                "new_code": f"25{index:04d}", "is_active": bool(row["is_active"])}
               for index, row in enumerate(selected, 1)]
    targets = {r["new_code"] for r in mapping}
    conflicts = [r for r in products if r["id"] > cutoff and r["product_code"] in targets]
    invariants = _snapshot(db, invariant=True)
    return {"dry_run": True, "through_product_id": cutoff, "mapping": mapping,
            "conflicts": conflicts, "can_execute": not conflicts,
            "already_executed": bool(db.execute("SELECT executed FROM numbering_rehearsal_guard").fetchone()[0]),
            "plan_sha256": digest({"snapshot": _snapshot(db), "mapping": mapping}),
            "invariants_sha256": digest(invariants),
            "counts": {name: len(invariants.get(name, [])) for name in
                       ("products", "product_packagings", "inventory_movements")},
            "carton_count": sum(r["carton_count"] for r in invariants["product_packagings"])}


def rehearse(path: Path, *, through_product_id=None, execute=False,
             confirm_isolated=False, expected_plan_sha256=None):
    path, marker = _validate(path)
    if execute and (not confirm_isolated or not expected_plan_sha256):
        raise RehearsalError("执行需要显式隔离预演确认和上次 dry-run 的 plan_sha256")
    db = _connect(path, marker, write=execute)
    try:
        db.execute("BEGIN IMMEDIATE" if execute else "BEGIN")
        plan = _plan(db, marker, through_product_id)
        if not execute:
            return plan
        if plan["already_executed"]:
            raise RehearsalError("该合成数据库已完成预演，拒绝重复换号")
        if plan["plan_sha256"] != expected_plan_sha256:
            raise RehearsalError("数据或迁移清单已变化，请重新 dry-run")
        if plan["conflicts"]:
            raise RehearsalError("目标编号被历史范围外商品占用，拒绝换号")
        expected_codes = dict(db.execute("SELECT id,product_code FROM products"))
        expected_codes.update((row["product_id"], row["new_code"]) for row in plan["mapping"])
        # Exclusive creation and fsync: the old/new map must exist before writes.
        mapping_path = path.parent / f"mapping-{plan['plan_sha256']}.csv"
        with mapping_path.open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["product_id", "old_code", "new_code", "is_active"])
            writer.writeheader()
            writer.writerows(plan["mapping"])
            stream.flush()
            os.fsync(stream.fileno())
        # Preserve numeric high-water marks before freeing old numeric codes.
        codes = [r[0] for r in db.execute("SELECT product_code FROM products WHERE product_code IS NOT NULL")]
        codes.extend(r["new_code"] for r in plan["mapping"])
        for code in codes:
            if re.fullmatch(r"[0-9]{6,64}", code) and "01" <= code[2:4] <= "12":
                next_sequence = int(code[4:]) + 1
                if next_sequence >= 2**63:
                    raise RehearsalError("原编号超出计数器范围")
                db.execute("INSERT INTO product_code_months VALUES (?,?) ON CONFLICT(period_ym) DO UPDATE SET next_sequence=max(next_sequence,excluded.next_sequence)",
                           ("20" + code[:4], next_sequence))
        token = uuid4().hex
        for row in plan["mapping"]:
            db.execute("UPDATE products SET product_code=? WHERE id=?", (f"__rehearsal_{token}_{row['product_id']}", row["product_id"]))
        for row in plan["mapping"]:
            db.execute("UPDATE products SET product_code=? WHERE id=?", (row["new_code"], row["product_id"]))
        if dict(db.execute("SELECT id,product_code FROM products")) != expected_codes:
            raise RehearsalError("最终编号与已批准清单不一致，已取消")
        if digest(_snapshot(db, invariant=True)) != plan["invariants_sha256"]:
            raise RehearsalError("商品非编号字段、包装、库存或流水不一致，已取消")
        if db.execute("PRAGMA foreign_key_check").fetchall() or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RehearsalError("数据库完整性检查失败")
        db.execute("UPDATE numbering_rehearsal_guard SET executed=1")
        db.commit()
        return {**plan, "dry_run": False, "executed": True, "invariants_preserved": True,
                "mapping_file": str(mapping_path), "finished_at": datetime.now(timezone.utc).isoformat()}
    finally:
        db.rollback()
        db.close()
