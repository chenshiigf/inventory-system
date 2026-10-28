"""Read-only audit of the *existing* production schema, before any downtime.

No project imports, provider calls, migrations, repairs or output files.
Usage:
  python -B api/scripts/numbering_v2_preflight.py --database /path/inventory.db --visual-root /path/visual-search
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
from collections import Counter


SOURCE = "aliyun-inventory-production"
OLD_REVISION = "c8d9e0f1a2b3"


def _connect(path: Path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("数据库文件不存在或为符号链接: " + str(path))
    # Never open live WAL/journal databases for a supposedly read-only audit:
    # opening them could initiate recovery or create a -shm file.
    sidecars = [str(path) + suffix for suffix in ("-wal", "-shm", "-journal")
                if os.path.lexists(str(path) + suffix)]
    if sidecars:
        raise ValueError("存在 SQLite 日志，未读取数据库: " + ", ".join(sidecars))
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _fetch_json(path: Path):
    if not path.is_file() or path.is_symlink():
        raise ValueError("来源标识文件不存在或为符号链接: " + str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def audit(database: Path, visual_root: Path) -> dict:
    """Report obstacles before development or cutover. This never writes."""
    database = database.expanduser().absolute()
    visual_root = visual_root.expanduser().absolute()
    catalog = visual_root / "production-catalog"
    report = {
        "format": "numbering-v2-preflight-v1",
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
    }

    def record(key, path):
        if path.exists():
            st = path.stat()
            report["paths"][key] = {
                "exists": True, "mode": oct(st.st_mode & 0o777),
                "uid": st.st_uid, "gid": st.st_gid,
            }
        else:
            report["paths"][key] = {"exists": False}

    try:
        with _connect(database) as db:
            revision = db.execute("SELECT version_num FROM alembic_version").fetchone()
            if not revision:
                raise ValueError("业务库没有 Alembic 版本")
            report["main_revision"] = revision[0]
            if revision[0] != OLD_REVISION:
                report["blockers"].append("数据库版本不是已知原版，需要重新审查迁移链")
            rows = db.execute("SELECT id, product_code FROM products ORDER BY id").fetchall()
            codes = {row["id"]: row["product_code"] for row in rows}
            report["products"] = len(codes)
            if len(codes) != len(rows) or len(codes) > 9999 or not codes:
                report["blockers"].append("商品 ID 或历史编号容量异常")
            stored = [v for v in codes.values() if v is not None]
            duplicates = [code for code, count in Counter(stored).items() if count > 1]
            if duplicates:
                report["warnings"].append("原货号重复: " + str(len(duplicates)) + " 种；生成计划时必须保留映射")
            report["total_cartons"] = db.execute(
                "SELECT coalesce(sum(carton_count),0) FROM product_packagings"
            ).fetchone()[0]
            report["paths"]["business_db"] = {"exists": True, "read_only_open": True}
    except (sqlite3.Error, ValueError, OSError) as exc:
        report["blockers"].append("业务数据库读取失败: " + str(exc))
        return report

    for key, path in (
        ("ai_catalog_dir", catalog),
        ("ai_catalog_db", catalog / "catalog.sqlite"),
        ("ai_catalog_lock", catalog / "calls.lock"),
        ("ai_calls_lock", visual_root / "calls.lock"),
        ("ai_budget", visual_root / "daily-budget.sqlite3"),
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
    report["manifest_files"] = len(list(reports.glob("*.json"))) if reports.is_dir() else 0
    if report["manifest_files"] == 0:
        report["warnings"].append("原始 AI 同步清单缺失；新方案不得要求或伪造该清单")

    try:
        identity = _fetch_json(catalog / "source.json")
        if identity.get("source_id") != SOURCE or identity.get("format") != "inventory-production-catalog-v1":
            raise ValueError("AI 来源标识不匹配")
        from hashlib import sha256
        namespace = "production-" + sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        report["ai_namespace"] = namespace
        with _connect(catalog / "catalog.sqlite") as db:
            meta = {r["key"]: json.loads(r["document"]) for r in db.execute(
                "SELECT key, document FROM mirror_meta"
            )}
            if meta.get("identity") != identity:
                raise ValueError("AI 镜像元数据来源不符")
            last = meta["last_report"]
            if last.get("scope") not in ("full", "sample"):
                raise ValueError("AI 同步范围不明")
            mirrored = db.execute(
                "SELECT source_id,id,document FROM mirror_products ORDER BY id"
            ).fetchall()
            seen = set()
            mismatches = []
            unknown = []
            for item in mirrored:
                doc = json.loads(item["document"])
                pid = item["id"]
                if (item["source_id"] != SOURCE or doc.get("source_id") != SOURCE
                    or doc.get("id") != pid or doc.get("product_key") != SOURCE + ":" + str(pid)
                    or pid in seen):
                    raise ValueError("AI 镜像出现重复或无效商品身份")
                seen.add(pid)
                if pid not in codes:
                    unknown.append(pid)
                elif doc.get("product_code") != codes[pid]:
                    mismatches.append(pid)
            report["mirror_products"] = len(mirrored)
            report["unknown_mirror_ids"] = unknown[:20]
            report["missing_from_mirror"] = len(set(codes) - seen)
            report["old_code_mismatches"] = {
                "count": len(mismatches), "example_ids": mismatches[:20]
            }
            if unknown:
                report["blockers"].append("AI 镜像包含业务库不存在的商品 ID: " + str(unknown[:20]))
            if mismatches:
                report["warnings"].append("AI 镜像的旧编号与业务库不同，需在离线副本上人工审查")
            if last.get("scope") == "full" and last.get("selected_products") != sum(
                json.loads(item["document"]).get("source_present") is True for item in mirrored
            ):
                report["warnings"].append("全量同步数量与现有 source_present 数量不符")
        index_root = visual_root / "production-indices" / namespace
        binding = _fetch_json(index_root / "source-binding.json")
        if (binding.get("source_id") != SOURCE or binding.get("source_identity") != identity
            or binding.get("index_namespace") != namespace):
            raise ValueError("AI 向量索引来源绑定不符")
        with _connect(index_root / "index.db") as db:
            index_revision = db.execute("SELECT version_num FROM alembic_version").fetchone()
            if not index_revision or index_revision[0] != "vs02":
                raise ValueError("AI 向量索引不是 vs02")
            contexts = db.execute(
                "SELECT count(*) FROM visual_source_context WHERE source_id=? AND source_identity=?",
                (SOURCE, namespace),
            ).fetchone()[0]
            report["ai_index_contexts"] = contexts
            if contexts < 1:
                report["blockers"].append("没有匹配的 AI 索引上下文")
    except (sqlite3.Error, ValueError, OSError, TypeError, KeyError) as exc:
        report["blockers"].append("AI 镜像/索引读取失败: " + str(exc))

    return report


def main():
    parser = argparse.ArgumentParser(description="旧版生产数据只读预检；不停止服务、不调用 AI")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--visual-root", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.database, args.visual_root)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
