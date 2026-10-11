"""Explicit full-run authorization gate; never contacts the production server."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf8")
    sys.stderr.reconfigure(encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description="生产图库全量视觉索引：本地、串行、10 元停止保护")
    parser.add_argument("--execute-authorized-full", action="store_true")
    parser.add_argument("--load-local-env", action="store_true")
    args = parser.parse_args()
    if not args.execute_authorized_full:
        parser.error("本工具仅供已获全量授权后执行，需要 --execute-authorized-full")
    if args.load_local_env:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    from app.services.visual_search.config import VisualConfig, visual_directory
    from app.services.visual_search.factory import production_service
    from app.services.visual_search.full_index import execute_full_run, SafetyStop
    from app.services.visual_search.lock import CallLock
    root = visual_directory()
    lock = CallLock(root / "full-run-control")
    if not lock.acquire():
        print("已有全量索引运行，未开始调用", file=sys.stderr)
        return 2
    service = None
    try:
        service = production_service(root, replace(VisualConfig.from_env(), retries=0))
        acceptance = json.loads((root / "phase17/acceptance.json").read_text(encoding="utf8"))
        expected = acceptance["status"]
        actual = {**service.source.context(), "model": service.config.model, "dimension": service.config.dimension,
                  "version": service.config.version, "index_key": service.config.index_key}
        if any(actual[k] != expected[k] for k in ("source", "source_id", "index_namespace", "catalog_fingerprint",
                                                   "model", "dimension", "version", "index_key")):
            raise SafetyStop("来源、命名空间或模型与已验收的 10 款索引不一致")
        rows = service.store.rows()
        accepted = acceptance["accepted_products"]
        if len(accepted) != 10 or any(r["product_id"] not in rows or
                                       rows[r["product_id"]]["image_hash"] != r["image_sha256"] or
                                       rows[r["product_id"]]["indexing_status"] != "ready" for r in accepted):
            raise SafetyStop("原有 10 款成功索引缺失或变化")
        state = execute_full_run(service, root / "phase17/full", protected_paths=[root.parent / "inventory.db", root / "phase15/index.db"])
        print(json.dumps({k: state.get(k) for k in ("status", "stop_reason", "usage", "newly_generated_vectors", "shared_image_reuses", "active_seconds", "validation")}, ensure_ascii=False, indent=2))
        return 0 if state["status"] == "completed" else 2
    except (SafetyStop, OSError, ValueError) as error:
        print("全量索引未启动：" + (str(error) if isinstance(error, SafetyStop) else type(error).__name__), file=sys.stderr)
        return 2
    finally:
        if service:
            service.close()
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
