"""Offline backup rehearsal only. No application startup, network or model imports.

This is deliberately a separate trust boundary from synthetic fixtures. A source
backup is never opened writable; only a newly created OS-temp copy can be changed.
"""
from __future__ import annotations

import csv
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from uuid import uuid4

from app.services.product_code_rehearsal import RehearsalError, _snapshot, digest

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parent
HEAD_REVISION = "d1e2f3a4b5c6"
REVISIONS = {"b2c3d4e5f6a7", "c8d9e0f1a2b3", HEAD_REVISION}
CONTROL = "offline_numbering_run"
MARKER = "offline-backup-rehearsal.json"
FORMAT = "offline-backup-rehearsal-v1"


def safe_path(value: Path, *, temporary=False, file=False) -> Path:
    """Reject aliases before resolve, including every existing parent component."""
    path = Path(value).absolute()
    if str(path).startswith(("\\\\", "//")):
        raise RehearsalError("拒绝网络路径")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise RehearsalError("拒绝符号链接、junction 或 reparse point")
    path = path.resolve()
    forbidden = [REPO_ROOT, Path("/var/lib/inventory-system"), Path("/srv/inventory-system")]
    data_dir = os.environ.get("INVENTORY_DATA_DIR")
    # The test runner may use a separate temp data dir; never accept that DB either.
    defaults = [API_ROOT / "data" / "inventory.db",
                Path("D:/inventory-local-data/inventory.db")]
    if data_dir:
        defaults.append(Path(data_dir).absolute() / "inventory.db")
    configured = os.environ.get("DATABASE_URL", "")
    if configured.startswith("sqlite:///") and configured != "sqlite:///:memory:":
        defaults.append(Path(configured[len("sqlite:///"):]).absolute())
    if any(path == root.resolve() or path.is_relative_to(root.resolve()) for root in forbidden):
        raise RehearsalError("拒绝项目仓库或已知生产数据路径；真实数据必须位于仓库外")
    if any(path == item.resolve() for item in defaults):
        raise RehearsalError("拒绝默认业务数据库")
    if temporary:
        root = Path(tempfile.gettempdir()).resolve()
        if path == root or not path.is_relative_to(root):
            raise RehearsalError("工作副本必须位于系统临时目录的新独立目录")
    if file:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RehearsalError("拒绝硬链接或非普通文件")
    return path


def file_sha256(path: Path) -> str:
    path = safe_path(path, file=True)
    before = path.stat()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino) or opened.st_nlink != 1:
            raise RehearsalError("文件身份发生变化")
        result = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
    safe_path(path, file=True)
    current = path.stat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if identity(before) != identity(after) or identity(before) != identity(current):
        raise RehearsalError("读取期间文件发生变化")
    return result


def _no_sidecars(path):
    # lexists also rejects dangling links: SQLite must never create a journal
    # through a pre-positioned alias whose target does not exist yet.
    if any(os.path.lexists(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal")):
        raise RehearsalError("拒绝带 WAL/SHM/journal 的非独立快照，请提供完整离线备份")


def _open(path, *, write=False):
    path = safe_path(path, temporary=write, file=True)
    _no_sidecars(path)
    db = sqlite3.connect(path.as_uri() + ("?mode=rw" if write else "?mode=ro&immutable=1"),
                         uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA foreign_keys=ON")
        if not write:
            db.execute("PRAGMA query_only=ON")
        return db
    except BaseException:
        db.close()
        raise


def _check(db):
    if [tuple(r) for r in db.execute("PRAGMA integrity_check")] != [("ok",)]:
        raise RehearsalError("SQLite integrity_check 失败")
    if db.execute("PRAGMA foreign_key_check").fetchall():
        raise RehearsalError("SQLite foreign_key_check 失败")
    versions = [r[0] for r in db.execute("SELECT version_num FROM alembic_version")]
    if len(versions) != 1 or versions[0] not in REVISIONS:
        raise RehearsalError("不支持或不唯一的 Alembic revision")
    return versions[0]


def inspect_backup(source: Path, manifest: Path) -> dict:
    """Explicit source and checksum manifest only; no discovery or default DB."""
    source = safe_path(source, file=True)
    manifest = safe_path(manifest, file=True)
    _no_sidecars(source)
    manifest_hash = file_sha256(manifest)
    metadata = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if not isinstance(metadata, dict):
        raise RehearsalError("备份 manifest 必须为 JSON 对象")
    name = metadata.get("database_filename", metadata.get("database"))
    expected = metadata.get("database_sha256", metadata.get("sha256"))
    size = metadata.get("database_size", metadata.get("size"))
    if name != source.name or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise RehearsalError("备份 manifest 文件名或 SHA256 无效")
    checksum = file_sha256(source)
    if checksum != expected or type(size) is not int or source.stat().st_size != size:
        raise RehearsalError("备份摘要或大小与 manifest 不符")
    with closing(_open(source)) as db:
        revision = _check(db)
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if CONTROL in names or "numbering_rehearsal_guard" in names:
            raise RehearsalError("来源不能是已有预演数据库或伪造合成身份")
        baseline = _business_snapshot(db)
    if checksum != file_sha256(source) or manifest_hash != file_sha256(manifest):
        raise RehearsalError("核验期间备份发生变化")
    return {"source": str(source), "manifest": str(manifest), "source_sha256": checksum,
            "manifest_sha256": manifest_hash, "source_size": size, "source_revision": revision,
            "source_business_sha256": digest(baseline)}


def _schema(db):
    return [list(r) for r in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
        "AND name != ? ORDER BY type,name", (CONTROL,))]


def _typed_rows(db):
    # Preserve SQLite storage types as well as values. A BLOB must not compare
    # equal to a TEXT column containing the same hexadecimal representation.
    result = {}
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                      "AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') ORDER BY name")]
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        rows = [{key: ({"sqlite_blob_hex": value.hex()} if isinstance(value, bytes) else value)
                 for key, value in dict(row).items()} for row in db.execute(f"SELECT * FROM {quoted}")]
        result[table] = rows
    return result


def _business_snapshot(db, *, invariant=False):
    data = _typed_rows(db)
    data.pop(CONTROL, None)
    data.pop("alembic_version", None)
    data.pop("product_code_months", None)
    # ADD COLUMN original_product_code is the only allowed old-schema row change.
    for row in data.get("products", []):
        row.setdefault("original_product_code", None)
        if invariant:
            row.pop("product_code", None)
    for rows in data.values():
        rows.sort(key=lambda row: json.dumps(row, sort_keys=True))
    return data


def _evidence(db):
    data = _business_snapshot(db, invariant=True)
    stocks = {str(r["id"]): 0 for r in data["products"]}
    warehouses = {str(r["id"]): 0 for r in data["warehouses"]}
    by_id = {r["id"]: r for r in data["products"]}
    if any(row["warehouse_id"] is None for row in by_id.values()):
        warehouses["None"] = 0
    packs_by_id = {r["id"]: r for r in data["product_packagings"]}
    for movement in data["inventory_movements"]:
        packaging = packs_by_id.get(movement["product_packaging_id"])
        if packaging and packaging["product_id"] != movement["product_id"]:
            raise RehearsalError("库存流水关联的包装不属于该商品")
    for row in data["product_packagings"]:
        stocks[str(row["product_id"])] += row["carton_count"]
        key = str(by_id[row["product_id"]]["warehouse_id"])
        warehouses[key] = warehouses.get(key, 0) + row["carton_count"]
    return {"invariants_sha256": digest(data),
            "table_sha256": {k: digest(v) for k, v in data.items()},
            "counts": {k: len(v) for k, v in data.items()},
            "product_ids": sorted(by_id), "stock_by_product": stocks,
            "stock_by_warehouse": warehouses, "total_cartons": sum(stocks.values())}


def _counters(db, mapping):
    counters = dict(db.execute("SELECT period_ym,next_sequence FROM product_code_months"))
    for period, value in counters.items():
        if not re.fullmatch(r"20[0-9]{2}(0[1-9]|1[0-2])", period) or type(value) is not int or not 1 <= value < 2**63:
            raise RehearsalError("月份计数器异常")
    for code in [r[0] for r in db.execute("SELECT product_code FROM products")] + [r["new_code"] for r in mapping]:
        if isinstance(code, str) and re.fullmatch(r"[0-9]{6,64}", code) and "01" <= code[2:4] <= "12":
            sequence = int(code[4:]) + 1
            if sequence >= 2**63:
                raise RehearsalError("编号超出计数器安全范围")
            period = "20" + code[:4]
            counters[period] = max(counters.get(period, 1), sequence)
    return counters


def _mapping(db):
    products = [dict(r) for r in db.execute("SELECT * FROM products ORDER BY id")]
    if not products or len(products) > 9999:
        raise RehearsalError("历史商品数量必须为 1 至 9999；超出四位历史空间须另行决策")
    old_codes = [row["product_code"] for row in products if row["product_code"] is not None]
    if len(set(old_codes)) != len(old_codes):
        raise RehearsalError("旧货号重复，须先核实备份异常")
    categories = {r["id"]: dict(r) for r in db.execute("SELECT * FROM categories")}
    warehouses = {r["id"]: r["name"] for r in db.execute("SELECT * FROM warehouses")}
    packagings = {}
    for row in db.execute("SELECT * FROM product_packagings ORDER BY product_id,sort_order,id"):
        packagings.setdefault(row["product_id"], []).append(dict(row))
    result = []
    for index, row in enumerate(products, 1):
        if type(row["id"]) is not int or row["id"] <= 0 or row["is_active"] not in (0, 1):
            raise RehearsalError("商品 ID 或启停状态异常")
        old = row["product_code"]
        if old is not None and not isinstance(old, str):
            raise RehearsalError("旧货号不是文本")
        category = categories.get(row["category_id"])
        names, visited = [], set()
        while category:
            if category["id"] in visited:
                raise RehearsalError("分类关系存在循环")
            visited.add(category["id"])
            names.insert(0, category["name"])
            category = categories.get(category["parent_id"])
        packs = packagings.get(row["id"], [])
        image = row["image_path"]
        result.append({"product_id": row["id"], "old_code": old, "new_code": f"25{index:04d}",
                       "original_product_code": row["original_product_code"],
                       "category": " / ".join(names), "warehouse": warehouses.get(row["warehouse_id"]),
                       "is_active": bool(row["is_active"]), "image_path": image,
                       "thumbnail_path": row["thumbnail_path"],
                       "image_availability": "图片不可用：未提供离线图片" if image else "无主图引用",
                       "packagings": json.dumps(packs, ensure_ascii=False, sort_keys=True),
                       "carton_count": sum(p["carton_count"] for p in packs),
                       "old_code_anomaly": "空货号" if not old else
                           ("" if re.fullmatch(r"(?:[0-9]+-[0-9]+-[0-9]+|[0-9]{6,64})", old) else "格式异常"),
                       "old_code_is_null": old is None})
    targets = [r["new_code"] for r in result]
    if len(set(targets)) != len(result) or any(not re.fullmatch(r"25[0-9]{4}", c) for c in targets):
        raise RehearsalError("目标编号冲突或格式异常")
    return result


def _upgrade_copy(path):
    """Use the normal Alembic graph in an isolated process, never stamp."""
    runtime = path.parent / "migration-runtime"
    runtime.mkdir(exist_ok=False)
    env = os.environ.copy()
    env.update(INVENTORY_DATA_DIR=str(runtime), DATABASE_URL=f"sqlite:///{path.as_posix()}",
               DASHSCOPE_API_KEY="", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    code = ("import sys; from alembic.config import Config; from alembic import command; "
            "c=Config(); c.set_main_option('script_location',sys.argv[1]); "
            "c.set_main_option('sqlalchemy.url',sys.argv[2].replace('%','%%')); "
            "command.upgrade(c,sys.argv[3])")
    process = subprocess.run([sys.executable, "-c", code, str(API_ROOT / "alembic"),
                              f"sqlite:///{path.as_posix()}", HEAD_REVISION],
                             cwd=API_ROOT, env=env, capture_output=True, text=True, encoding="utf8")
    if process.returncode:
        raise RehearsalError("仅工作副本的 Alembic 升级失败；弃用该副本重新准备：" + process.stderr[-2000:])


def _write_json(path, value):
    safe_path(path, temporary=True)
    with path.open("x", encoding="utf8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def prepare_copy(source: Path, manifest: Path, directory: Path, *, upgrade_copy=False):
    binding = inspect_backup(source, manifest)
    directory = safe_path(directory, temporary=True)
    if directory.exists() or directory.is_relative_to(Path(binding["source"]).parent):
        raise RehearsalError("工作目录必须全新且与来源备份目录分离")
    if binding["source_revision"] != HEAD_REVISION and not upgrade_copy:
        raise RehearsalError("旧 revision 仅允许显式 --upgrade-copy 在工作副本按 Alembic 链升级")
    directory.mkdir(parents=False, exist_ok=False)
    path = directory / "work.sqlite3"
    # Raw byte copy of a checked, standalone snapshot, not a connection to live data.
    with Path(binding["source"]).open("rb") as source_stream, path.open("xb") as target:
        for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    if os.path.samefile(binding["source"], path) or file_sha256(path) != binding["source_sha256"]:
        raise RehearsalError("副本隔离或摘要检查失败")
    # A standalone backup can retain WAL mode in its header without any WAL file.
    # Normalize only the copy before migration, so later hashes cover all rows.
    with closing(_open(path, write=True)) as db:
        if db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise RehearsalError("无法将工作副本切换为独立 rollback journal 模式")
    if binding["source_revision"] != HEAD_REVISION:
        _upgrade_copy(path)
    with closing(_open(path, write=True)) as db, db:
        if _check(db) != HEAD_REVISION or digest(_business_snapshot(db)) != binding["source_business_sha256"]:
            raise RehearsalError("结构升级改变了业务数据，拒绝继续")
        # A distinct identity table prevents masquerading as a synthetic fixture.
        binding.update(format=FORMAT, run_id=uuid4().hex, database=str(path),
                       directory=str(directory), prepared_database_sha256=file_sha256(path))
        baseline = {"schema": _schema(db), "evidence": _evidence(db),
                    "counters": dict(db.execute("SELECT * FROM product_code_months"))}
        mapping = _mapping(db)
        plan = {"mapping": mapping, "expected_counters": _counters(db, mapping)}
        db.execute(f"CREATE TABLE {CONTROL}(id INTEGER PRIMARY KEY CHECK(id=1), binding_json TEXT NOT NULL, "
                   "baseline_json TEXT NOT NULL, plan_json TEXT NOT NULL, state TEXT NOT NULL "
                   "CHECK(state IN ('prepared','committed')), result_json TEXT)")
        db.execute(f"INSERT INTO {CONTROL} VALUES (1,?,?,?,'prepared',NULL)",
                   tuple(json.dumps(v, ensure_ascii=False, sort_keys=True) for v in (binding, baseline, plan)))
    # Recheck the original after every copy/migration, without opening it writable.
    if inspect_backup(source, manifest)["source_sha256"] != binding["source_sha256"]:
        raise RehearsalError("原备份已变化，副本不可使用")
    _write_json(directory / MARKER, binding)
    return offline_plan(directory)


def _load(directory, *, write=False):
    directory = safe_path(directory, temporary=True)
    marker = safe_path(directory / MARKER, temporary=True, file=True)
    binding = json.loads(marker.read_text(encoding="utf8"))
    path = safe_path(directory / "work.sqlite3", temporary=True, file=True)
    if binding.get("format") != FORMAT or binding.get("database") != str(path) or binding.get("directory") != str(directory):
        raise RehearsalError("工作副本身份不匹配")
    actual = inspect_backup(Path(binding["source"]), Path(binding["manifest"]))
    if any(actual[k] != binding[k] for k in actual) or os.path.samefile(path, binding["source"]):
        raise RehearsalError("原备份身份或摘要已变化")
    db = _open(path, write=write)
    try:
        rows = db.execute(f"SELECT * FROM {CONTROL}").fetchall()
        if len(rows) != 1 or json.loads(rows[0]["binding_json"]) != binding:
            raise RehearsalError("工作副本内部身份不匹配")
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='numbering_rehearsal_guard'").fetchone():
            raise RehearsalError("真实副本不得伪装为合成数据")
        return path, binding, db
    except BaseException:
        db.close()
        raise


def _current_plan(db, binding):
    if _check(db) != HEAD_REVISION:
        raise RehearsalError("工作副本 revision 不符")
    control = dict(db.execute(f"SELECT * FROM {CONTROL}").fetchone())
    baseline = json.loads(control["baseline_json"])
    frozen = json.loads(control["plan_json"])
    if _schema(db) != baseline["schema"] or _evidence(db) != baseline["evidence"]:
        raise RehearsalError("商品非编号字段、ID、包装、库存、流水、图片或导入批次已变化")
    codes = dict(db.execute("SELECT id,product_code FROM products"))
    expected = {r["product_id"]: r["new_code"] if control["state"] == "committed" else r["old_code"]
                for r in frozen["mapping"]}
    counters = frozen["expected_counters"] if control["state"] == "committed" else baseline["counters"]
    if codes != expected or dict(db.execute("SELECT * FROM product_code_months")) != counters:
        raise RehearsalError("编号、计数器或执行状态不符，必须重新准备副本")
    snapshot = {"rows": _snapshot(db), "schema": _schema(db), "binding": binding}
    return {"dry_run": True, "state": control["state"], "can_execute": control["state"] == "prepared",
            "work_database": binding["database"], "source_sha256": binding["source_sha256"],
            "source_revision": binding["source_revision"], "revision": HEAD_REVISION,
            "plan_sha256": digest(snapshot), **frozen, **baseline["evidence"],
            "committed_result": json.loads(control["result_json"]) if control["result_json"] else None}


def _export(directory, report):
    reports = safe_path(directory / ("reports-" + uuid4().hex), temporary=True)
    reports.mkdir(exist_ok=False)
    mapping = report["mapping"]
    # CSV has no type metadata. All fields are quoted; import code columns as TEXT
    # in Excel, never double-click. JSON retains exact NULL vs empty and text types.
    csv_path = reports / "mapping.csv"
    with csv_path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(mapping[0]), quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(mapping)
        stream.flush()
        os.fsync(stream.fileno())
    _write_json(reports / "report.json", report)
    _write_json(reports / "checksums.json", {"mapping.csv": file_sha256(csv_path),
                                            "report.json": file_sha256(reports / "report.json")})
    return str(reports)


def offline_plan(directory: Path, *, execute=False, confirm_offline_copy=False,
                 expected_plan_sha256=None, export_reports=False):
    if execute and (not confirm_offline_copy or not expected_plan_sha256):
        raise RehearsalError("执行离线副本换号需要显式确认和本副本 dry-run 摘要")
    path, binding, db = _load(directory, write=execute)
    committed = False
    try:
        db.execute("BEGIN IMMEDIATE" if execute else "BEGIN")
        plan = _current_plan(db, binding)
        if execute:
            if not plan["can_execute"]:
                raise RehearsalError("该工作副本已提交，拒绝重复迁移；可只读检查或重新导出报告")
            if plan["plan_sha256"] != expected_plan_sha256:
                raise RehearsalError("确认摘要与当前工作副本不符")
            # Mapping/evidence must be durable before touching any product code.
            pre_reports = _export(path.parent, plan)
            for period, value in plan["expected_counters"].items():
                db.execute("INSERT INTO product_code_months VALUES (?,?) ON CONFLICT(period_ym) "
                           "DO UPDATE SET next_sequence=excluded.next_sequence", (period, value))
            token = uuid4().hex
            for row in plan["mapping"]:
                db.execute("UPDATE products SET product_code=? WHERE id=?",
                           (f"__offline_{token}_{row['product_id']}", row["product_id"]))
            for row in plan["mapping"]:
                db.execute("UPDATE products SET product_code=? WHERE id=?", (row["new_code"], row["product_id"]))
            result = {"dry_run": False, "state": "committed", "approved_plan_sha256": plan["plan_sha256"],
                      "invariants_preserved": True, "precommit_reports": pre_reports}
            db.execute(f"UPDATE {CONTROL} SET state='committed',result_json=? WHERE id=1",
                       (json.dumps(result, ensure_ascii=False, sort_keys=True),))
            final = _current_plan(db, binding)
            # Source hash/path/manifest are checked again before the commit point.
            actual = inspect_backup(Path(binding["source"]), Path(binding["manifest"]))
            if any(actual[k] != binding[k] for k in actual):
                raise RehearsalError("提交前原备份发生变化")
            safe_path(path, temporary=True, file=True)
            safe_path(path.parent / MARKER, temporary=True, file=True)
            db.commit()
            committed = True
            plan = {**final, **result, "can_execute": False}
        if export_reports or execute:
            try:
                plan["reports_directory"] = _export(path.parent, plan)
            except OSError as error:
                if not committed:
                    raise
                # Do not claim rollback after COMMIT. Status lives in the same DB
                # transaction; a subsequent read-only plan can regenerate reports.
                plan.update(state="committed_report_failed", report_error=str(error))
        return plan
    finally:
        db.rollback()
        db.close()
