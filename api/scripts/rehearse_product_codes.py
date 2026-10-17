"""Separate synthetic fixtures and explicitly sourced offline backup rehearsals."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.product_code_rehearsal import RehearsalError, _temporary_path, create_fixture, rehearse
from app.services.product_code_offline import inspect_backup, offline_plan, prepare_copy


def main(argv=None):
    parser = argparse.ArgumentParser(description="商品编号隔离预演；真实备份仅复制到仓库外临时副本")
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
    inspect = commands.add_parser("offline-inspect", help="只读核验用户指定备份及 manifest")
    prepare = commands.add_parser("offline-prepare", help="新建隔离工作副本；不换号")
    for command in (inspect, prepare):
        command.add_argument("--source-backup", required=True, type=Path)
        command.add_argument("--source-manifest", required=True, type=Path)
    prepare.add_argument("--work-directory", required=True, type=Path)
    prepare.add_argument("--upgrade-copy", action="store_true")
    offline = commands.add_parser("offline-plan", help="默认只读；也用于提交状态检查和报告恢复")
    offline.add_argument("--work-directory", required=True, type=Path)
    offline.add_argument("--export-reports", action="store_true")
    offline.add_argument("--execute", action="store_true")
    offline.add_argument("--confirm-offline-copy", action="store_true")
    offline.add_argument("--expected-plan-sha256")
    args = parser.parse_args(argv)
    try:
        if args.action == "create-fixture":
            directory = _temporary_path(args.directory)
            os.environ["INVENTORY_DATA_DIR"] = str(directory / "runtime")
            os.environ["DATABASE_URL"] = f"sqlite:///{(directory / 'fixture.sqlite3').as_posix()}"
            result = {"synthetic_database": str(create_fixture(args.directory, conflict=args.conflict))}
        elif args.action == "plan":
            result = rehearse(args.database, through_product_id=args.through_product_id,
                              execute=args.execute, confirm_isolated=args.confirm_isolated_rehearsal,
                              expected_plan_sha256=args.expected_plan_sha256)
        elif args.action == "offline-inspect":
            result = inspect_backup(args.source_backup, args.source_manifest)
        elif args.action == "offline-prepare":
            result = prepare_copy(args.source_backup, args.source_manifest, args.work_directory,
                                  upgrade_copy=args.upgrade_copy)
        else:
            result = offline_plan(args.work_directory, execute=args.execute,
                                  confirm_offline_copy=args.confirm_offline_copy,
                                  expected_plan_sha256=args.expected_plan_sha256,
                                  export_reports=args.export_reports)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("state") == "committed_report_failed":
            return 3
        if result.get("state") == "committed":
            return 0
        return 0 if result.get("can_execute", True) else 2
    except (RehearsalError, OSError, sqlite3.Error, KeyError, ValueError) as error:
        print("预演未完成：" + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
