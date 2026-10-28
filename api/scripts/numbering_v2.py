"""Run from api/: python -B scripts/numbering_v2.py {plan,execute,verify,restore}."""
import argparse
import json
from pathlib import Path
import sys
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import numbering_v2  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='人工维护窗口内的一次性编号切换；不管理服务、不调用模型')
    commands = parser.add_subparsers(dest='command', required=True)
    planning = commands.add_parser('plan', help='只读生成完整映射与批准摘要')
    for name in ('database', 'backup-database', 'backup-catalog', 'visual-root', 'catalog-root', 'namespace', 'index-key', 'output'):
        planning.add_argument('--' + name, required=True)
    evidence = planning.add_mutually_exclusive_group(required=True)
    evidence.add_argument('--offline-test', action='store_true', help='仅在独立测试副本验证程序，一致性仍未验证')
    evidence.add_argument('--consistency-evidence', help='经人工审查且绑定四库 SHA256 的一致备份证据 JSON')
    execution = commands.add_parser('execute', help='同时事务换号业务与已有 AI 镜像；必须先停止全部写入')
    execution.add_argument('--plan', required=True)
    execution.add_argument('--confirm-sha256', required=True)
    execution.add_argument('--confirm-writers-stopped', action='store_true')
    execution.add_argument('--output', required=True)
    checking = commands.add_parser('verify', help='只读核验；提交后报告失败也只使用此命令，不重执行')
    checking.add_argument('--plan', required=True)
    recovery = commands.add_parser('restore', help='仅逆向恢复编号；发现切换后任何新业务写入即拒绝')
    recovery.add_argument('--plan', required=True)
    recovery.add_argument('--confirm-sha256', required=True)
    recovery.add_argument('--confirm-writers-stopped', action='store_true')
    recovery.add_argument('--output', required=True)
    args = vars(parser.parse_args())
    command = args.pop('command')
    try:
        if command == 'plan':
            output = args.pop('output')
            args = {k:v for k,v in args.items() if v is not None}
            report = numbering_v2.plan(args, output)
            result = {'state': 'planned', 'plan_sha256': report['plan_sha256'], 'products': len(report['mapping']),
                      'scope': report['scope'],
                      'warnings': report['warnings'], 'ai_missing_ids': report['ai']['missing_from_mirror']}
        elif command == 'execute':
            result = numbering_v2.execute(args['plan'], args['confirm_sha256'],
                                         writers_stopped=args['confirm_writers_stopped'], output=args['output'])
        elif command == 'restore':
            result = numbering_v2.restore(args['plan'], args['confirm_sha256'],
                                         writers_stopped=args['confirm_writers_stopped'], output=args['output'])
        else:
            result = numbering_v2.verify(args['plan'])
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3 if result['state'] == 'committed_report_failed' else 0
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({'state': 'stopped', 'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
