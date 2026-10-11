"""Controlled local indexing and labelled evaluation; real calls require an explicit flag."""
import argparse
from contextlib import nullcontext
from dataclasses import replace
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf8")
    sys.stderr.reconfigure(encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description="商品视觉搜索：预检、受控索引、人工标注评测")
    parser.add_argument("action", choices=["init", "status", "index", "evaluate"])
    parser.add_argument("--source", choices=["development", "production-catalog"], default="development")
    parser.add_argument("--load-local-env", action="store_true", help="仅在此本地进程读取 api/.env，不输出密钥")
    parser.add_argument("--no-retries", action="store_true", help="真实小样本关闭付费自动重试")
    parser.add_argument("--confirm-large-batch", action="store_true", help="另行确认超过 10 款的生产图库批次")
    parser.add_argument("--max-items", type=int, default=10)
    parser.add_argument("--execute-paid", action="store_true", help="明确同意本次有限付费调用；默认 dry-run")
    parser.add_argument("--manifest", type=Path, help="JSON: [{image,product_id,crop?}]，路径相对 manifest")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.max_items <= 100:
        parser.error("--max-items 必须为 1 至 100")
    if args.source == "production-catalog" and args.execute_paid and args.max_items > 10 and not args.confirm_large_batch:
        parser.error("生产图库超过 10 款的付费批次还需 --confirm-large-batch")
    if args.action == "init" and args.source != "production-catalog":
        parser.error("init 仅初始化独立生产索引；不会迁移开发或 Phase 1.5 索引")
    if args.load_local_env:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

    from app.services.visual_search.config import VisualConfig, visual_directory
    from app.services.visual_search.factory import production_service
    from app.services.visual_search.images import MAX_BYTES, VisualError
    from app.services.visual_search.service import VisualService

    config = VisualConfig.from_env()
    if args.no_retries:
        config = replace(config, retries=0)
    service = None
    try:
        if args.source == "production-catalog":
            service = production_service(visual_directory(), config, initialize=args.action == "init")
            session = nullcontext(None)
        else:
            from app.database import SessionLocal, UPLOADS_DIRECTORY
            service = VisualService(visual_directory(), UPLOADS_DIRECTORY, config)
            session = SessionLocal()
        with session as db:
            if args.action in {"init", "status"}:
                result = service.status(db)
            elif args.action == "index":
                result = service.rebuild(db, "local-cli", args.max_items, not args.execute_paid, args.execute_paid, background=False,
                                         confirm_large_batch=args.confirm_large_batch)
            else:
                if not args.manifest:
                    parser.error("评测需要 --manifest")
                labels = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
                if not isinstance(labels, list) or not labels:
                    parser.error("manifest 需要非空数组")
                labels = labels[:args.max_items]
                if service.source.production and any(label.get("source_id") != service.source.source_id for label in labels):
                    parser.error("生产图库评测标签必须明确标注匹配的 source_id，不接受裸开发商品 ID")
                if not args.execute_paid:
                    result = {"dry_run": True, "labelled_images": len(labels), "max_api_calls_without_retries": len(labels), "api_calls": 0}
                else:
                    rows, totals = [], {"api_calls": 0, "input_tokens": 0, "image_tokens": 0, "total_tokens": 0, "estimated_cost_cny": 0.0}
                    for label in labels:
                        time.sleep(service.config.interval)
                        path = (args.manifest.parent / label["image"]).resolve()
                        with path.open("rb") as file:
                            data = file.read(MAX_BYTES + 1)
                        found = service.search(db, "local-cli", data, tuple(label["crop"]) if label.get("crop") else None)
                        ids = [c["product"]["id"] for c in found["candidates"]]
                        row = {"image": label["image"], "correct_product_id": label["product_id"], "candidate_ids": ids,
                               "query_id": found["query_id"], "elapsed_ms": found["elapsed_ms"], "usage": found["call_summary"]}
                        rows.append(row)
                        for key in totals:
                            value = row["usage"].get(key)
                            if value is not None:
                                totals[key] += value
                    result = {"samples": len(rows), **{f"top_{k}_hit_rate": sum(r["correct_product_id"] in r["candidate_ids"][:k] for r in rows) / len(rows) for k in [1, 3, 5]},
                              "average_elapsed_ms": sum(r["elapsed_ms"] for r in rows) / len(rows), "usage": totals, "results": rows,
                              "note": "仅表示此人工标注样本的命中率；失败调用及账单以 status 记录和百炼账单为准"}
                    if service.config.model != "qwen3-vl-embedding":
                        result["usage"]["estimated_cost_cny"] = None
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf8")
        print(rendered)
        return 2 if result.get("status") in {"failed", "interrupted"} or result.get("failed", 0) and args.action == "index" and args.execute_paid else 0
    except (VisualError, OSError, ValueError, KeyError) as error:
        print(f"操作未完成：{error}", file=sys.stderr)
        return 1
    finally:
        if service:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
