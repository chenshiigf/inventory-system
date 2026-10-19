"""Issue a scoped private capability or serve an independent loopback read API."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.numbering_acceptance import create_acceptance_app, issue_capability


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['issue', 'serve'])
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--operation-id', required=True)
    parser.add_argument('--completion-sha256', required=True)
    parser.add_argument('--database', type=Path)
    parser.add_argument('--catalog-root', type=Path)
    parser.add_argument('--htpasswd-file', type=Path)
    parser.add_argument('--admin-user', action='append', default=[])
    parser.add_argument('--ttl-seconds', type=int, default=1800)
    parser.add_argument('--port', type=int, default=18114)
    args = parser.parse_args(argv)
    try:
        common = dict(database=args.database, catalog_root=args.catalog_root)
        if args.action == 'issue':
            print(json.dumps(issue_capability(args.token_file, args.operation_id, args.completion_sha256,
                                              ttl_seconds=args.ttl_seconds, **common)))
        else:
            if not args.htpasswd_file or not args.admin_user or not 1024 <= args.port <= 65535:
                raise ValueError('必须指定已有管理员凭据、管理员账号及有效本地端口')
            app = create_acceptance_app(args.token_file, args.operation_id, args.completion_sha256,
                                        htpasswd_file=args.htpasswd_file, admin_users=args.admin_user, **common)
            import uvicorn
            uvicorn.run(app, host='127.0.0.1', port=args.port, proxy_headers=False, access_log=False)
        return 0
    except Exception as error:
        print('只读验收已停止：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
