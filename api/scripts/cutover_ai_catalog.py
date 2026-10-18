"""Explicit approved mirror metadata update. Does not load .env or an AI client."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.visual_search.numbering_cutover import Inputs, plan, status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'status', 'recover-report', 'recover-transaction'])
    for name in ('visual-root', 'catalog-root', 'result', 'mapping', 'manifest', 'report-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    for name in ('operation-id', 'completion-sha256', 'mapping-sha256', 'namespace', 'index-key'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-metadata-only', action='store_true')
    parser.add_argument('--expected-plan-sha256')
    parser.add_argument('--export-reports', action='store_true')
    args = parser.parse_args(argv)
    try:
        values = vars(args).copy()
        for key in ('action', 'execute', 'confirm_metadata_only', 'expected_plan_sha256', 'export_reports'):
            values.pop(key)
        inputs = Inputs(**values)
        if args.action == 'plan':
            result = plan(inputs, execute=args.execute, confirm_metadata_only=args.confirm_metadata_only,
                          expected_plan_sha256=args.expected_plan_sha256, export_reports=args.export_reports)
        else:
            if args.execute or args.confirm_metadata_only or args.expected_plan_sha256:
                raise ValueError('恢复或状态检查不接受换号执行参数')
            result = status(inputs, recover_report=args.action == 'recover-report', recover_transaction=args.action == 'recover-transaction')
        print(json.dumps({k: result[k] for k in ('state', 'plan_sha256', 'commit_sha256', 'reports_directory',
                                                'business_products', 'mirror_products') if k in result}))
        return 3 if result['state'] == 'committed_report_failed' else 0
    except Exception as error:
        print('AI 镜像换号已停止：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
