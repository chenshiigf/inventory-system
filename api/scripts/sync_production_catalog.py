"""Windows local catalog sync. Explicit download/full gates; no SSH automation."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf8")
    sys.stderr.reconfigure(encoding="utf8")

from app.services.visual_search.production_catalog import (
    CatalogClient, CatalogError, ProductionCatalog, sync_catalog,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description="生产商品图库：SSH 隧道只读同步、本地核对与查询")
    parser.add_argument("action", choices=["sync", "status", "get"])
    parser.add_argument("--base-url", default="http://127.0.0.1:18102")
    parser.add_argument("--directory", type=Path, default=API_ROOT / "data/visual-search/production-catalog")
    parser.add_argument("--source-id", default="aliyun-inventory-production")
    parser.add_argument("--source-label", default="ecs-8.136.127.226-inventory-system")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--limit", type=int, default=10)
    scope.add_argument("--all", action="store_true", help="单独批准后的全量模式")
    parser.add_argument("--expected-total", type=int)
    parser.add_argument("--execute-download", action="store_true", help="明确批准本次图片下载；默认仅预检")
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--interval", type=float, default=.2, help="请求间隔秒数，最小 0.2（并发固定为 1）")
    args = parser.parse_args(argv)
    if args.all and args.expected_total is None:
        parser.error("--all 还需要 --expected-total；不要在小批量阶段使用")
    client = None
    try:
        if args.action == "sync":
            client = CatalogClient(args.base_url, interval=args.interval)
            result = sync_catalog(client, args.directory, args.source_id, args.source_label,
                                  limit=None if args.all else args.limit,
                                  expected_total=args.expected_total, execute=args.execute_download)
        else:
            mirror = ProductionCatalog(args.directory, args.source_id)
            if args.action == "get":
                if args.product_id is None:
                    parser.error("get 需要 --product-id")
                result = mirror.get(args.source_id, args.product_id)
            else:
                result = mirror.audit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("failed_main_images") or result.get("local_image_failures") or result.get("disk", {}).get("sufficient") is False else 0
    except (CatalogError, OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        # Unknown exception text could include data; only controlled errors are printed.
        print("操作未完成：" + (str(error) if isinstance(error, CatalogError) else type(error).__name__), file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
