"""Only generates and renumbers synthetic SQLite fixtures under the OS temp root."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.product_code_rehearsal import RehearsalError, _temporary_path, create_fixture, rehearse


def main(argv=None):
    parser = argparse.ArgumentParser(description="商品编号隔离预演，不接受业务数据库")
    commands = parser.add_subparsers(dest="action", required=True)
    fixture = commands.add_parser("create-fixture", help="在系统临时目录的新目录生成合成数据")
    fixture.add_argument("--directory", required=True, type=Path)
    fixture.add_argument("--conflict", action="store_true", help="生成范围外编号冲突场景")
    plan = commands.add_parser("plan", help="默认只读预演")
    plan.add_argument("--database", required=True, type=Path)
    plan.add_argument("--through-product-id", type=int)
    plan.add_argument("--execute", action="store_true")
    plan.add_argument("--confirm-isolated-rehearsal", action="store_true")
    plan.add_argument("--expected-plan-sha256")
    args = parser.parse_args(argv)
    try:
        if args.action == "create-fixture":
            directory = _temporary_path(args.directory)
            os.environ["INVENTORY_DATA_DIR"] = str(directory / "runtime")
            os.environ["DATABASE_URL"] = f"sqlite:///{(directory / 'fixture.sqlite3').as_posix()}"
            result = {"synthetic_database": str(create_fixture(args.directory, conflict=args.conflict))}
        else:
            result = rehearse(args.database, through_product_id=args.through_product_id,
                              execute=args.execute, confirm_isolated=args.confirm_isolated_rehearsal,
                              expected_plan_sha256=args.expected_plan_sha256)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("can_execute", True) else 2
    except (RehearsalError, OSError, sqlite3.Error, KeyError, ValueError) as error:
        print("预演未完成：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
