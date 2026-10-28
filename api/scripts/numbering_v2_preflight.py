"""Read-only audit of a frozen offline copy of the existing production schema.

No project imports, provider calls, migrations, repairs or output files.
Usage:
  python -B api/scripts/numbering_v2_preflight.py --database /path/inventory.db --visual-root /path/visual-search
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
import sqlite3
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256


SOURCE = "aliyun-inventory-production"
OLD_REVISION = "c8d9e0f1a2b3"


def _checked(path: Path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("路径包含符号链接: " + str(path))
    return path


@contextmanager
def _connect(path: Path, report: dict):
    _checked(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("数据库文件不存在或为符号链接: " + str(path))
    # Never open live WAL/journal databases for a supposedly read-only audit:
    # opening them could initiate recovery or create a -shm file.
    sidecars = [str(path) + suffix for suffix in ("-wal", "-shm", "-journal")
                if os.path.lexists(str(path) + suffix)]
    report["sqlite"][str(path)] = {"sidecars": sidecars, "journal_mode": None}
    if sidecars:
        raise ValueError("存在 SQLite 日志，未读取数据库: " + ", ".join(sidecars))
    with path.open("rb") as file:
        header = file.read(20)
    if header[:16] == b"SQLite format 3\0" and 2 in header[18:20]:
        report["sqlite"][str(path)]["journal_mode"] = "wal"
        raise ValueError("WAL 数据库即使无 sidecar 也不能直接用于离线跨库原子事务；未打开: " + str(path))
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        report["sqlite"][str(path)]["journal_mode"] = mode
        if mode not in {"delete", "truncate", "persist"}:
            raise ValueError("离线跨库原子事务不接受当前 journal_mode: " + str(path) + "=" + mode)
        if [tuple(r) for r in db.execute("PRAGMA quick_check")] != [("ok",)]:
            raise ValueError("SQLite 完整性检查失败")
        yield db
    finally:
        db.close()


def _schema(db, expected):
    for table, required in expected.items():
        columns = {r["name"] for r in db.execute(f'PRAGMA table_info("{table}")')}
        if not set(required.split()).issubset(columns):
            raise ValueError("必要表或字段缺失: " + table)


def _object(value, label):
    if not isinstance(value, dict):
        raise ValueError(label + " 必须是 JSON 对象")
    return value


def _fetch_json(path: Path):
    _checked(path)
    if not path.is_file() or path.is_symlink():
        raise ValueError("来源标识文件不存在或为符号链接: " + str(path))
    return _object(json.loads(path.read_text(encoding="utf-8")), str(path))


def audit(database: Path, visual_root: Path) -> dict:
    """Report obstacles before development or cutover. This never writes."""
    database = database.expanduser().absolute()
    visual_root = visual_root.expanduser().absolute()
    catalog = visual_root / "production-catalog"
    report = {
        "format": "numbering-v2-preflight-v1",
        "snapshot_provenance": "UNVERIFIED: 调用者须另行证明副本来源、完整性和一致性",
        "main_revision": None,
        "products": None,
        "total_cartons": None,
        "mirror_products": None,
        "missing_from_mirror": None,
        "old_code_mismatches": None,
        "unknown_mirror_ids": None,
        "manifest_files": None,
        "ai_namespace": None,
        "ai_index_contexts": None,
        "paths": {},
        "blockers": [],
        "warnings": [],
        "checks": {"business": "PENDING", "mirror": "PENDING", "index": "PENDING", "budget": "PENDING",
                   "production_permissions": "PENDING", "runtime_freeze": "PENDING", "future_transaction": "PENDING"},
        "sqlite": {},
        "numbering": {},
        "execution_identity": {"scope": "仅当前离线副本进程，不代表生产执行用户",
                               "uid": os.geteuid() if hasattr(os, "geteuid") else None,
                               "gid": os.getegid() if hasattr(os, "getegid") else None,
                               "groups": os.getgroups() if hasattr(os, "getgroups") else None},
        "runtime_checks": [
            "确认冻结副本与业务库、AI 镜像、索引、预算账本来自同一一致备份，并验证备份清单/哈希",
            "确认正式执行用户及其附加组、ACL、父目录遍历、文件读写、创建/原子替换日志文件的权限；副本属主不证明生产权限",
            "确认 API/worker、AI 同步/索引/搜索任务、备份 timer 及其他写入者均按正式方案冻结；锁文件存在不代表锁已释放",
            "确认取得现有两类调用锁和业务维护锁；本预检不获取或创建锁",
            "在未来执行器的实际 ATTACH 连接核实主库/镜像均为持久磁盘库、非 WAL、synchronous 非 OFF、文件系统支持跨库原子提交；本连接 PRAGMA 不证明未来事务配置",
            "确认预检期间副本无并发写入；前后哈希相同不等于历史时点、备份完整性或跨库一致性已验证",
            "WAL 的 -wal/-shm 与回滚日志 -journal 均须由受控正常停写/备份流程处理；禁止删除 sidecar 或在预检中恢复",
        ],
    }

    def record(key, path):
        try:
            _checked(path)
            st = path.stat()
            is_directory = key.endswith("_dir") or key == "visual_root"
            if (is_directory and not stat.S_ISDIR(st.st_mode)) or (not is_directory and not stat.S_ISREG(st.st_mode)):
                raise ValueError("文件/目录类型不符: " + key)
            info = {
                "exists": True, "mode": oct(st.st_mode & 0o777),
                "uid": st.st_uid, "gid": st.st_gid,
                "path": str(path), "is_directory": stat.S_ISDIR(st.st_mode),
                "local_readable": os.access(path, os.R_OK),
                "local_writable": os.access(path, os.W_OK),
                "local_searchable": os.access(path, os.X_OK) if path.is_dir() else None,
                "production_permission_status": "PENDING",
            }
            if path.is_file():
                info["sha256_before"] = sha256(path.read_bytes()).hexdigest()
            report["paths"][key] = info
            if not info["local_readable"] or (path.is_dir() and not info["local_searchable"]):
                report["blockers"].append("当前用户无法读取/遍历: " + key)
            if not info["local_writable"]:
                report["warnings"].append("当前副本用户无写权限，正式执行用户权限仍待确认: " + key)
            if os.name == "posix" and st.st_mode & stat.S_IWOTH:
                report["warnings"].append("路径允许其他用户写入，需审查: " + key)
        except (OSError, ValueError) as exc:
            report["paths"][key] = {"exists": False, "path": str(path)}
            report["blockers"].append("路径不可用 " + key + ": " + str(exc))

    def finish():
        for info in report["paths"].values():
            if "sha256_before" in info:
                try:
                    info["sha256_after"] = sha256(_checked(Path(info["path"])).read_bytes()).hexdigest()
                    if info["sha256_before"] != info["sha256_after"]:
                        raise ValueError("检查期间文件发生变化；不能证明一致快照或只读结果")
                except (OSError, ValueError) as exc:
                    report["blockers"].append(str(exc))
        report["status_rules"] = {"BLOCKED": "发现缺失/损坏/来源不符/不安全事务条件等确定阻塞",
                                  "WARNING": "无确定阻塞但有异常或运行时待确认项",
                                  "PASS": "仅已实际验证且无异常的单项；不表示可上线"}
        report["status"] = "BLOCKED" if report["blockers"] else "WARNING"
        report["warnings"].append("离线结果不证明生产权限、服务停写或未来事务条件；正式切换检查清单全部待确认")
        return report

    for key, path in (("business_dir", database.parent), ("business_db", database)):
        record(key, path)
    codes = None

    try:
        with _connect(database, report) as db:
            _schema(db, {"alembic_version": "version_num", "products": "id product_code",
                         "product_packagings": "product_id carton_count"})
            revisions = db.execute("SELECT version_num FROM alembic_version").fetchall()
            if len(revisions) != 1:
                raise ValueError("业务库没有 Alembic 版本")
            revision = revisions[0]
            report["main_revision"] = revision[0]
            if revision[0] != OLD_REVISION:
                raise ValueError("数据库版本不是已知原版，需要重新审查迁移链")
            rows = db.execute("SELECT id, product_code FROM products ORDER BY id").fetchall()
            codes = {row["id"]: row["product_code"] for row in rows}
            report["products"] = len(codes)
            if len(codes) != len(rows) or len(codes) > 9999 or not codes:
                raise ValueError("商品 ID 或历史编号容量异常")
            stored = [v for v in codes.values() if v is not None]
            duplicates = [code for code, count in Counter(stored).items() if count > 1]
            if duplicates:
                report["warnings"].append("旧自家编号重复: " + str(len(duplicates)) + " 种；生成计划时必须保留 product_id 映射")
            nulls = [pid for pid, code in codes.items() if code is None]
            blanks = [pid for pid, code in codes.items() if isinstance(code, str) and not code.strip()]
            abnormal = [pid for pid, code in codes.items() if code is not None and
                        (not isinstance(code, str) or not re.fullmatch(r"(?:[0-9]+-[0-9]+-[0-9]+|[0-9]{6,})", code))]
            report["numbering"] = {
                "null": {"count": len(nulls), "example_ids": nulls[:20]},
                "blank": {"count": len(blanks), "example_ids": blanks[:20]},
                "abnormal": {"count": len(abnormal), "example_ids": abnormal[:20]},
                "duplicates": {"count": len(duplicates), "rows": sum(Counter(stored)[v] for v in duplicates),
                               "example_ids": [pid for pid, code in codes.items() if code in duplicates][:20]},
            }
            if nulls or abnormal:
                report["warnings"].append("旧自家编号有空值或异常格式；按 product_id 审查，不自动修复")
            report["total_cartons"] = db.execute(
                "SELECT coalesce(sum(carton_count),0) FROM product_packagings"
            ).fetchone()[0]
            report["paths"]["business_db"]["read_only_open"] = True
            report["checks"]["business"] = "WARNING" if duplicates or nulls or abnormal else "PASS"
    except (sqlite3.Error, ValueError, OSError) as exc:
        report["blockers"].append("业务数据库读取失败: " + str(exc))
        report["checks"]["business"] = "BLOCKED"

    for key, path in (
        ("visual_root", visual_root),
        ("ai_catalog_dir", catalog),
        ("ai_catalog_db", catalog / "catalog.sqlite"),
        ("ai_catalog_lock", catalog / "calls.lock"),
        ("ai_calls_lock", visual_root / "calls.lock"),
        ("ai_budget", visual_root / "daily-budget.sqlite3"),
        ("ai_source", catalog / "source.json"),
    ):
        record(key, path)
    for key in ("ai_catalog_lock", "ai_calls_lock"):
        if not report["paths"][key]["exists"]:
            report["blockers"].append("缺少已有锁文件: " + key)
    if report["paths"]["ai_catalog_dir"]["exists"]:
        report["warnings"].append(
            "AI 目录权限需在演练时按实际写入用户确认；禁止递归修改目录所有权"
        )
    reports = catalog / "manifests"
    try:
        _checked(reports)
        report["manifest_files"] = len(list(reports.glob("*.json"))) if reports.is_dir() else 0
    except (OSError, ValueError) as exc:
        report["warnings"].append("可选 manifest 目录未验证: " + str(exc))
    if report["manifest_files"] == 0:
        report["warnings"].append("原始 AI 同步清单缺失；新方案不得要求或伪造该清单")

    try:
        identity = _fetch_json(catalog / "source.json")
        if identity.get("source_id") != SOURCE or identity.get("format") != "inventory-production-catalog-v1":
            raise ValueError("AI 来源标识不匹配")
        namespace = "production-" + sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        report["ai_namespace"] = namespace
        with _connect(catalog / "catalog.sqlite", report) as db:
            _schema(db, {"mirror_meta": "key document", "mirror_products": "source_id id document"})
            meta = {r["key"]: json.loads(r["document"]) for r in db.execute(
                "SELECT key, document FROM mirror_meta"
            )}
            if meta.get("identity") != identity:
                raise ValueError("AI 镜像元数据来源不符")
            last = _object(meta["last_report"], "last_report")
            if last.get("scope") not in ("full", "sample"):
                raise ValueError("AI 同步范围不明")
            if (not isinstance(last.get("catalog_fingerprint"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", last["catalog_fingerprint"]) or
                type(last.get("selected_products")) is not int or last["selected_products"] < 0):
                raise ValueError("AI 同步元数据指纹/数量损坏")
            mirrored = db.execute(
                "SELECT source_id,id,document FROM mirror_products ORDER BY id"
            ).fetchall()
            seen = set()
            mismatches = []
            unknown = []
            for item in mirrored:
                doc = _object(json.loads(item["document"]), "mirror_products.document")
                pid = item["id"]
                if (item["source_id"] != SOURCE or doc.get("source_id") != SOURCE
                    or type(pid) is not int or pid < 1 or type(doc.get("id")) is not int
                    or doc.get("id") != pid or doc.get("product_key") != SOURCE + ":" + str(pid)
                    or type(doc.get("source_present")) is not bool or "product_code" not in doc
                    or (doc["product_code"] is not None and not isinstance(doc["product_code"], str))
                    or pid in seen):
                    raise ValueError("AI 镜像出现重复或无效商品身份")
                seen.add(pid)
                if codes is None:
                    continue
                if pid not in codes:
                    unknown.append(pid)
                elif doc.get("product_code") != codes[pid]:
                    mismatches.append(pid)
            report["mirror_products"] = len(mirrored)
            report["unknown_mirror_ids"] = unknown[:20]
            report["unknown_mirror_count"] = len(unknown) if codes is not None else None
            report["missing_from_mirror"] = len(set(codes) - seen) if codes is not None else None
            report["old_code_mismatches"] = {
                "count": len(mismatches), "example_ids": mismatches[:20]
            }
            if unknown:
                report["blockers"].append("AI 镜像包含业务库不存在的商品 ID: " + str(unknown[:20]))
            if mismatches:
                report["warnings"].append("AI 镜像的旧编号与业务库不同，需在离线副本上人工审查")
            if codes is None:
                report["old_code_mismatches"] = None
                report["unknown_mirror_ids"] = None
            count_mismatch = last.get("scope") == "full" and last.get("selected_products") != sum(
                json.loads(item["document"]).get("source_present") is True for item in mirrored
            )
            if count_mismatch:
                report["warnings"].append("全量同步数量与现有 source_present 数量不符")
            report["checks"]["mirror"] = ("BLOCKED" if unknown else "WARNING" if mismatches or count_mismatch or codes is None else "PASS")
        index_root = visual_root / "production-indices" / namespace
        for key, path in (("ai_indices_dir", index_root.parent), ("ai_index_dir", index_root),
                          ("ai_binding", index_root / "source-binding.json"), ("ai_index_db", index_root / "index.db")):
            record(key, path)
        binding = _fetch_json(index_root / "source-binding.json")
        if (binding.get("format") != "production-visual-index-v1" or binding.get("source_id") != SOURCE or binding.get("source_identity") != identity
            or binding.get("index_namespace") != namespace):
            raise ValueError("AI 向量索引来源绑定不符")
        with _connect(index_root / "index.db", report) as db:
            _schema(db, {"alembic_version": "version_num",
                         "visual_source_context": "index_key source_id source_identity catalog_fingerprint model dimension version preprocessing_version updated_at",
                         "visual_embeddings": "product_id index_key image_identifier image_hash embedding_model embedding_dimension embedding_version preprocessing_version embedding_vector indexed_at indexing_status error",
                         "visual_image_vectors": "index_key image_hash embedding_vector created_at",
                         "visual_jobs": "id index_key status", "visual_calls": "id index_key usage",
                         "visual_queries": "id index_key candidate_ids", "visual_feedback": "query_id selected_product_id no_match"})
            index_revisions = [r[0] for r in db.execute("SELECT version_num FROM alembic_version")]
            if index_revisions != ["vs02"]:
                raise ValueError("AI 向量索引不是 vs02")
            contexts = db.execute(
                "SELECT count(*) FROM visual_source_context WHERE source_id=? AND source_identity=?",
                (SOURCE, namespace),
            ).fetchone()[0]
            report["ai_index_contexts"] = contexts
            if contexts < 1:
                report["blockers"].append("没有匹配的 AI 索引上下文")
            for row in db.execute("SELECT * FROM visual_source_context"):
                expected_key = sha256((f"qwen3-vl-embedding|2560|v1|cn-beijing|"
                    f"https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding|"
                    f"exif-rgb-white-jpeg90-1024-v1|source:{namespace}").encode()).hexdigest()
                if (row["source_id"] != SOURCE or row["source_identity"] != namespace or
                    row["index_key"] != expected_key or
                    row["catalog_fingerprint"] != last["catalog_fingerprint"] or
                    row["model"] != "qwen3-vl-embedding" or row["dimension"] != 2560 or
                    row["version"] != "v1" or row["preprocessing_version"] != "exif-rgb-white-jpeg90-1024-v1"):
                    raise ValueError("索引上下文来源/模型/原始同步指纹不一致")
            report["checks"]["index"] = "PASS" if contexts else "BLOCKED"
    except (sqlite3.Error, ValueError, OSError, TypeError, KeyError) as exc:
        report["blockers"].append("AI 镜像/索引读取失败: " + str(exc))
        report["checks"]["mirror_index"] = "BLOCKED"

    try:
        with _connect(visual_root / "daily-budget.sqlite3", report) as db:
            _schema(db, {"daily_budget": "day call_count budgeted_cny actual_cny unknown_calls",
                         "call_reservations": "id day reserve_cny actual_cny state"})
            if db.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise ValueError("预算库 user_version 不是 1")
            report["checks"]["budget"] = "PASS"
    except (sqlite3.Error, ValueError, OSError) as exc:
        report["blockers"].append("AI 预算库读取失败: " + str(exc))
        report["checks"]["budget"] = "BLOCKED"
    return finish()


def main():
    parser = argparse.ArgumentParser(description="旧版生产数据只读预检；不停止服务、不调用 AI")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--visual-root", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.database, args.visual_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"只读预检 {report['status']}：{len(report['blockers'])} 项阻塞，"
          f"{len(report['warnings'])} 项警告；生产运行时条件待确认。", file=sys.stderr)
    return 2 if report["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
