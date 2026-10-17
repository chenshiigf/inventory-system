"""Explicit Linux production entry. No default target and no local bypass switches."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.numbering_environment import ProductionEnvironment
from app.services import product_code_cutover as cutover


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['maintenance-enter', 'plan', 'status', 'recover-report', 'recover-transaction', 'maintenance-release'])
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--operation-id', required=True)
    parser.add_argument('--release-sha')
    parser.add_argument('--backup-database', type=Path)
    parser.add_argument('--backup-manifest', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-production-numbering', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    parser.add_argument('--export-reports', action='store_true')
    parser.add_argument('--acceptance', type=Path)
    args = parser.parse_args(argv)
    try:
        context = ProductionEnvironment(args.database)
        if args.action != 'plan' and (args.execute or args.confirm_production_numbering or args.expected_plan_sha256):
            raise ValueError('执行授权参数只适用于 plan')
        if args.action == 'maintenance-enter':
            if not args.release_sha:
                raise ValueError('maintenance-enter 必须绑定 release-sha')
            with context.locked():
                result = cutover.enter(context, args.operation_id, args.release_sha)
        elif args.action == 'plan':
            if not args.backup_database or not args.backup_manifest:
                raise ValueError('必须指定维护窗口内的完整备份')
            result = cutover.plan(context, args.operation_id, args.backup_database, args.backup_manifest,
                                  execute=args.execute, confirm_production_numbering=args.confirm_production_numbering,
                                  expected_plan_sha256=args.expected_plan_sha256, export_reports=args.export_reports)
        elif args.action == 'recover-transaction':
            result = cutover.recover_transaction(context, args.operation_id)
        else:
            if args.action == 'maintenance-release' and not args.acceptance:
                raise ValueError('解除维护需要完整验收记录')
            acceptance = json.loads(args.acceptance.read_text(encoding='utf8')) if args.action == 'maintenance-release' else None
            result = cutover.status(context, args.operation_id, recover_report=args.action == 'recover-report', acceptance=acceptance)
            if args.action == 'maintenance-release' and result['state'] != 'committed':
                raise ValueError('尚未换号，禁止解除维护')
        # Never print business rows to deployment/CI logs. Full report is private.
        print(json.dumps({k: result[k] for k in ('state', 'plan_sha256', 'completion_sha256', 'reports_directory') if k in result}))
        return 3 if result.get('state') == 'committed_report_failed' else 0
    except Exception as error:
        print('换号操作已停止：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
