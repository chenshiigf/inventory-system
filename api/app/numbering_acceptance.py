"""Separate, loopback-only read API. Never imports app.main or app.database.

Business and AI catalogs may live on different hosts. Run the same scoped reader
on each host with its own capability; do not download a database to co-locate it.
"""
from contextlib import closing
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from app.numbering_maintenance import Maintenance, MaintenanceError, checked_path, database_identity
from app.services import product_code_cutover as business
from app.services.visual_search.auth import require_visual_admin
from app.services.visual_search.catalog_metadata import catalog_context
from app.services.visual_search.config import VisualConfig
from app.services.visual_search import numbering_cutover as catalog


def proofs(operation_id, completion_sha256, *, database=None, catalog_root=None):
    result = {}
    if database is not None:
        path = checked_path(database, existing=True)
        state = Maintenance(path.parent).require(operation_id, path)
        with closing(business.connect(path)) as db:
            business.check(db)
            row = db.execute(f'SELECT operation_id,state,document FROM {business.CONTROL} WHERE id=1').fetchone()
            if not row or row['operation_id'] != operation_id or row['state'] != 'committed':
                raise MaintenanceError('只读验收要求业务换号已提交且维护继续保持')
            record = json.loads(row['document'])
            if (record['maintenance'] != state or record['database_identity'] != database_identity(path) or
                    record['completion_sha256'] != completion_sha256):
                raise MaintenanceError('业务完成摘要或维护身份不符')
            business._completion(db, record)
        result['business'] = database_identity(path)
    if catalog_root is not None:
        root = checked_path(catalog_root)
        context = catalog_context(root)
        with closing(catalog.connect(root / 'catalog.sqlite')) as db:
            record = catalog._meta(db).get(catalog.CONTROL)
            if (not record or record['operation_id'] != operation_id or
                    record['business_completion_sha256'] != completion_sha256 or record['namespace'] != context['index_namespace']):
                raise MaintenanceError('AI 镜像尚未完成本次换号或身份不符')
            catalog.verify_committed(db, record)
        visual_root = Path(record['protected']['budget']['path']).parent
        if os.path.lexists(checked_path(visual_root / 'runtime/ai-search.enabled')):
            raise MaintenanceError('只读验收期间必须继续关闭 AI 搜索')
        # Metadata only: do not instantiate VisualService, access an index,
        # inspect a budget ledger, initialize jobs, or read a product image.
        result['catalog'] = {'database': database_identity(root / 'catalog.sqlite'),
                             'version': context, 'commit_sha256': record['commit_sha256']}
    if not result:
        raise MaintenanceError('必须明确指定业务或 AI 镜像验收范围')
    return result


def issue_capability(path, operation_id, completion_sha256, *, database=None, catalog_root=None, ttl_seconds=1800):
    if type(ttl_seconds) is not int or not 60 <= ttl_seconds <= 3600:
        raise MaintenanceError('验收凭证有效期仅允许 60 至 3600 秒')
    path = checked_path(path)
    repository = Path(__file__).resolve().parents[2]
    if path.is_relative_to(repository) or path.exists():
        raise MaintenanceError('验收凭证必须存放于仓库外的私有新文件，不允许覆盖')
    binding = proofs(operation_id, completion_sha256, database=database, catalog_root=catalog_root)
    forbidden_roots = []
    if database is not None:
        forbidden_roots.append(checked_path(database, existing=True).parent)
    if catalog_root is not None:
        forbidden_roots.append(checked_path(catalog_root))
        with closing(catalog.connect(Path(catalog_root) / 'catalog.sqlite')) as db:
            forbidden_roots.append(checked_path(Path(catalog._meta(db)[catalog.CONTROL]['protected']['budget']['path']).parent))
    if any(path.is_relative_to(root) for root in forbidden_roots):
        raise MaintenanceError('私有凭证不能存入业务、图片或 AI 数据目录')
    value = {'format': 'numbering-readonly-capability-v1', 'operation_id': operation_id,
             'completion_sha256': completion_sha256, 'bindings': binding,
             'expires_at': int(time.time()) + ttl_seconds, 'token': secrets.token_hex(32)}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return {'token_file': str(path), 'expires_at': value['expires_at']}


def create_acceptance_app(token_file, operation_id, completion_sha256, *, htpasswd_file,
                          admin_users, database=None, catalog_root=None):
    token_file = checked_path(token_file, existing=True)
    if os.name != 'nt' and token_file.stat().st_mode & 0o077:
        raise MaintenanceError('验收凭证文件必须为私有权限 600')
    capability = json.loads(token_file.read_text(encoding='utf8'))
    expected = proofs(operation_id, completion_sha256, database=database, catalog_root=catalog_root)
    if (capability.get('format') != 'numbering-readonly-capability-v1' or capability.get('operation_id') != operation_id or
            capability.get('completion_sha256') != completion_sha256 or capability.get('bindings') != expected or
            capability.get('expires_at', 0) <= time.time() or len(capability.get('token', '')) != 64):
        raise MaintenanceError('验收凭证过期、范围不符或目标状态变化')
    app = FastAPI(title='编号切换只读验收', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.visual_config = VisualConfig(htpasswd_file=str(checked_path(htpasswd_file, existing=True)),
                                          admin_users=tuple(admin_users))

    @app.middleware('http')
    async def readonly_gate(request, call_next):
        if request.method not in {'GET', 'HEAD'}:
            return JSONResponse({'detail': '维护验收入口仅允许读取'}, status_code=503)
        if not request.client or request.client.host not in {'127.0.0.1', '::1'}:
            return JSONResponse({'detail': '验收仅允许本机回环访问'}, status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'private, no-store'
        return response

    def approved(request: Request, admin=Depends(require_visual_admin)):
        try:
            live = json.loads(checked_path(token_file, existing=True).read_text(encoding='utf8'))
            if live != capability or live['expires_at'] <= time.time():
                raise ValueError()
            token = request.headers.get('X-Numbering-Acceptance', '')
            if not hmac.compare_digest(token, capability['token']):
                raise HTTPException(403, '需要本次操作的私有验收凭证')
            if proofs(operation_id, completion_sha256, database=database, catalog_root=catalog_root) != expected:
                raise ValueError()
        except HTTPException:
            raise
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
            raise HTTPException(503, '验收状态无法核验，维护继续保持') from error

    @app.get('/acceptance/health', dependencies=[Depends(approved)])
    def health():
        return {'state': 'maintenance_held_readonly', 'operation_id': operation_id, 'scopes': list(expected)}

    if database is not None:
        @app.get('/acceptance/products', dependencies=[Depends(approved)])
        def products(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100)):
            with closing(business.connect(database)) as db:
                rows = [dict(r) for r in db.execute('SELECT * FROM products ORDER BY id LIMIT ? OFFSET ?',
                                                    (page_size, (page - 1) * page_size))]
                return {'total': db.execute('SELECT count(*) FROM products').fetchone()[0], 'items': rows}

        @app.get('/acceptance/products/{product_id}', dependencies=[Depends(approved)])
        def product(product_id: int):
            with closing(business.connect(database)) as db:
                row = db.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
                if row is None:
                    raise HTTPException(404, '商品不存在')
                return {**dict(row), 'packagings': [dict(r) for r in db.execute(
                    'SELECT * FROM product_packagings WHERE product_id=? ORDER BY id', (product_id,))]}

        @app.get('/acceptance/stock', dependencies=[Depends(approved)])
        def stock():
            with closing(business.connect(database)) as db:
                summary = business.evidence(db)
            return {k: summary[k] for k in ('stock_by_product', 'stock_by_warehouse', 'total_cartons')}

    if catalog_root is not None:
        @app.get('/acceptance/catalog', dependencies=[Depends(approved)])
        def metadata():
            return catalog_context(catalog_root)

        @app.get('/acceptance/catalog/products', dependencies=[Depends(approved)])
        def mirror_products():
            with closing(catalog.connect(Path(catalog_root) / 'catalog.sqlite')) as db:
                return {'items': catalog._rows(db)}

    return app
