"""Controlled local indexing and labelled evaluation; real calls require an explicit flag."""
import argparse
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
    parser.add_argument("action", choices=["status", "index", "evaluate"])
    parser.add_argument("--max-items", type=int, default=10)
    parser.add_argument("--execute-paid", action="store_true", help="明确同意本次有限付费调用；默认 dry-run")
    parser.add_argument("--manifest", type=Path, help="JSON: [{image,product_id,crop?}]，路径相对 manifest")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.max_items <= 100:
        parser.error("--max-items 必须为 1 至 100")

    from app.database import SessionLocal, UPLOADS_DIRECTORY
    from app.services.visual_search.config import VisualConfig, visual_directory
    from app.services.visual_search.images import MAX_BYTES, VisualError
    from app.services.visual_search.service import VisualService

    service = VisualService(visual_directory(), UPLOADS_DIRECTORY, VisualConfig.from_env())
    try:
        with SessionLocal() as db:
            if args.action == "status":
                result = service.status(db)
            elif args.action == "index":
                result = service.rebuild(db, "local-cli", args.max_items, not args.execute_paid, args.execute_paid, background=False)
            else:
                if not args.manifest:
                    parser.error("评测需要 --manifest")
                labels = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
                if not isinstance(labels, list) or not labels:
                    parser.error("manifest 需要非空数组")
                labels = labels[:args.max_items]
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
        return 0
    except (VisualError, OSError, ValueError, KeyError) as error:
        print(f"操作未完成：{error}", file=sys.stderr)
        return 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
