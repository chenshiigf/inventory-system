"""One-off plan/execute/verify. No services, deployment state or network clients.

Manual maintenance is mandatory. Main + attached catalog use SQLite's rollback
journal multi-database transaction; WAL is deliberately refused.
"""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from . import numbering_catalog as catalog
from .numbering_io import binding, checked_path, connect, digest, existing_lock, integrity, no_sidecars

REVISION = 'd1e2f3a4b5c6'
REGISTRY = 'numbering_v2_runs'
RESTORE = 'numbering_v2_restores'


def _registry_schema():
    definitions = {REGISTRY: f'CREATE TABLE {REGISTRY} (id INTEGER PRIMARY KEY CHECK(id=1), document TEXT NOT NULL)'}
    for action in ('UPDATE', 'DELETE'):
        definitions['numbering_mapping_no_' + action.lower()] = (
            f"CREATE TRIGGER numbering_mapping_no_{action.lower()} BEFORE {action} ON {REGISTRY} "
            "BEGIN SELECT RAISE(ABORT,'immutable numbering mapping'); END")
    definitions['numbering_mapping_no_replace'] = (
        f"CREATE TRIGGER numbering_mapping_no_replace BEFORE INSERT ON {REGISTRY} "
        f"WHEN EXISTS(SELECT 1 FROM {REGISTRY}) BEGIN SELECT RAISE(ABORT,'immutable numbering mapping'); END")
    return definitions


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def snapshot(db):
    integrity(db)
    schema = [list(r) for r in db.execute(
        'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name') if r[2] not in {REGISTRY, RESTORE}]
    tables = {}
    counts = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        if name in {REGISTRY, RESTORE, 'product_code_months'}:
            continue
        values = [dict(r) for r in db.execute('SELECT * FROM ' + _quote(name))]
        counts[name] = len(values)
        if name == 'products':
            for row in values:
                row.pop('product_code')
        for row in values:
            for key, value in row.items():
                if isinstance(value, bytes):
                    row[key] = {'blob_hex': value.hex()}
        tables[name] = digest(sorted(values, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False)))
    stock = [list(r) for r in db.execute('''SELECT p.warehouse_id,p.id,coalesce(sum(k.carton_count),0)
        FROM products p LEFT JOIN product_packagings k ON k.product_id=p.id
        GROUP BY p.warehouse_id,p.id ORDER BY p.id''')]
    return {'schema_sha256': digest(schema), 'table_sha256': tables, 'counts': counts,
            'product_ids': [r[0] for r in db.execute('SELECT id FROM products ORDER BY id')],
            'stock_by_product': stock, 'total_cartons': sum(r[2] for r in stock)}


def _counters(db, mapping):
    # Retired old aliases must not consume newly issued monthly numbers.
    return dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months'))


def _mapping(db):
    products = list(db.execute('SELECT * FROM products ORDER BY id'))
    if not products:
        raise ValueError('没有可换号商品')
    categories = dict(db.execute('SELECT id,name FROM categories'))
    warehouses = dict(db.execute('SELECT id,name FROM warehouses'))
    result = []
    for rank, row in enumerate(products, 1):
        packages = [dict(r) for r in db.execute('SELECT * FROM product_packagings WHERE product_id=? ORDER BY id', (row['id'],))]
        result.append({'product_id': row['id'], 'old_code': row['product_code'], 'new_code': str(250000 + rank),
                       'original_product_code': row['original_product_code'],
                       'category': categories.get(row['category_id']), 'warehouse': warehouses.get(row['warehouse_id']),
                       'is_active': row['is_active'], 'image_path': row['image_path'],
                       'packagings': packages, 'cartons': sum(p['carton_count'] for p in packages)})
    return result


def _backup_pair(source, backup):
    a, b = binding(source), binding(backup)
    if (a['device'], a['inode']) == (b['device'], b['inode']) or a['sha256'] != b['sha256']:
        raise ValueError('备份必须是当前数据库的独立且内容相同的文件')
    no_sidecars(backup)
    with closing(connect(backup)) as db:
        integrity(db)
    return a, b


@contextmanager
def session(options, *, write=False):
    root, cat = checked_path(options['visual_root']), checked_path(options['catalog_root'])
    target = checked_path(options['database'], existing=True)
    mirror = checked_path(cat / 'catalog.sqlite', existing=True)
    if os.path.samefile(target, mirror):
        raise ValueError('业务与镜像不能指向同一数据库')
    if target.stat().st_dev != mirror.stat().st_dev:
        raise ValueError('跨库提交要求业务与镜像位于同一本地文件系统；文件系统能力仍需人工确认')
    with existing_lock(root / 'calls.lock'), existing_lock(cat / 'calls.lock'):
        if os.path.lexists(root / 'runtime' / 'ai-search.enabled'):
            raise ValueError('先关闭 AI 并等待在途任务排空')
        with closing(connect(target, write=write)) as db:
            no_sidecars(mirror)
            mode = '?mode=rw' if write else '?mode=ro'
            db.execute('ATTACH DATABASE ? AS ai', (mirror.as_uri() + mode,))
            for schema in ('main', 'ai'):
                if db.execute(f'PRAGMA {schema}.journal_mode').fetchone()[0].lower() != 'delete':
                    raise ValueError('联接原子事务要求两个数据库均为 DELETE journal 模式；本工具不自动转换')
                if write:
                    db.execute(f'PRAGMA {schema}.synchronous=FULL')
            try:
                db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
                yield db
            finally:
                db.rollback()


def prepare(db, options):
    if db.execute('SELECT version_num FROM alembic_version').fetchone()[0] != REVISION:
        raise ValueError('请先用正常 Alembic 迁移链升级到 ' + REVISION)
    if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?", (REGISTRY,)).fetchone()[0]:
        raise ValueError('已有一次性换号登记，禁止再次执行；请使用 verify')
    main, backup_main = _backup_pair(options['database'], options['backup_database'])
    ai, backup_ai = _backup_pair(Path(options['catalog_root']) / 'catalog.sqlite', options['backup_catalog'])
    mapping = _mapping(db)
    old = {r['old_code'] for r in mapping}
    anomalies = [r['product_id'] for r in mapping if not re.fullmatch(r'(?:[0-9]+-[0-9]+-[0-9]+|[0-9]{6,64})', r['old_code'] or '')]
    prepared_ai = catalog.plan(db, options, mapping)
    evidence = None
    if options.get('consistency_evidence'):
        path = checked_path(options['consistency_evidence'], existing=True)
        evidence = json.loads(path.read_text(encoding='utf8'))
        hashes = {'business': main['sha256'], 'catalog': ai['sha256'],
                  'index': prepared_ai['protected'][str(Path('production-indices') / options['namespace'] / 'index.db')]['sha256'],
                  'budget': prepared_ai['protected']['daily-budget.sqlite3']['sha256']}
        if (not isinstance(evidence, dict) or evidence.get('format') != 'numbering-v2-consistency-v1' or
                evidence.get('reviewed') is not True or not evidence.get('backup_set') or not evidence.get('method') or
                evidence.get('sha256') != hashes):
            raise ValueError('必须提供人工审查且与当前四库哈希绑定的一致备份证据')
    elif options.get('offline_test') is not True:
        raise ValueError('跨库一致性证据不足；仅显式 offline-test 可在独立测试副本验证程序，不代表正式切换验收')
    body = {'format': 'numbering-v2-rework-v1', 'options': options,
            'scope': 'offline-test' if options.get('offline_test') else 'reviewed-offline-backup',
            'consistency_evidence': evidence,
            'files': {'database': main, 'catalog': ai, 'backup_database': backup_main, 'backup_catalog': backup_ai},
            'business': snapshot(db), 'mapping': mapping, 'counters_after': _counters(db, mapping),
            'warnings': {'empty_or_abnormal_code_ids': anomalies,
                         'target_code_overlap_ids': [r['product_id'] for r in mapping if r['new_code'] in old]},
            'ai': prepared_ai}
    return {**body, 'plan_sha256': digest(body)}


def export(report, output):
    output = checked_path(output)
    repository = Path(__file__).resolve().parents[3]
    roots = [repository, checked_path(report['options']['visual_root']),
             checked_path(report['options']['catalog_root']), Path(report['options']['database']).parent]
    if any(output.is_relative_to(root) for root in roots):
        raise ValueError('报告必须放在仓库及业务/AI 数据目录之外')
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    def save(name, value):
        with (output / name).open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    save('report.json', report)
    save('checksums.json', {'report.json': binding(output / 'report.json')['sha256']})
    return str(output)


def load_plan(path, confirmation=None):
    report = json.loads(checked_path(path, existing=True).read_text(encoding='utf8'))
    expected = digest({k:v for k,v in report.items() if k != 'plan_sha256'})
    if report.get('format') != 'numbering-v2-rework-v1' or report.get('plan_sha256') != expected:
        raise ValueError('计划摘要不符')
    if confirmation is not None and confirmation != expected:
        raise ValueError('必须提供已审核的完整计划摘要')
    return report


def plan(options, output):
    options = {k: str(checked_path(v)) if k not in {'namespace', 'index_key', 'offline_test'} else v for k,v in options.items()}
    with session(options) as db:
        report = prepare(db, options)
        export(report, output)
        return report


def _check_result(db, report, *, restore=False):
    actual_schema = dict(db.execute('SELECT name,sql FROM sqlite_master WHERE tbl_name=?', (REGISTRY,)))
    if actual_schema != _registry_schema():
        raise ValueError('持久映射的不可变表/触发器结构异常')
    if snapshot(db) != report['business']:
        raise ValueError('业务 ID、非编号字段、库存、包装、流水或导入记录变化')
    actual = [list(r) for r in db.execute('SELECT id,product_code FROM products ORDER BY id')]
    if actual != [[r['product_id'], r['old_code' if restore else 'new_code']] for r in report['mapping']]:
        raise ValueError('编号非空、唯一、排名或商品集合核对失败')
    if dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months')) != report['counters_after']:
        raise ValueError('计数器不符')
    catalog.verify(db, report['ai'], report['plan_sha256'], restore=restore)
    if catalog.protected(report['options'])[1] != report['ai']['protected']:
        raise ValueError('向量、索引、费用账本或来源文件变化')
    for key in ('backup_database', 'backup_catalog'):
        if binding(report['options'][key]) != report['files'][key]:
            raise ValueError('原始备份发生变化')
    for key, path in [('database', report['options']['database']), ('catalog', Path(report['options']['catalog_root']) / 'catalog.sqlite')]:
        current = binding(path)
        if any(current[k] != report['files'][key][k] for k in ('path', 'device', 'inode')):
            raise ValueError('目标数据库文件被替换')
    if os.path.lexists(Path(report['options']['visual_root']) / 'runtime' / 'ai-search.enabled'):
        raise ValueError('核验期间 AI 被重新启用')


def execute(path, confirmation, *, writers_stopped=False, output, _fault=lambda stage: None):
    if not writers_stopped:
        raise ValueError('必须显式确认维护窗口中全部业务、AI 和同步写入已停止')
    report = load_plan(path, confirmation)
    with session(report['options'], write=True) as db:
        current = prepare(db, report['options'])
        if current != report:
            raise ValueError('数据库、备份、映射或目录已变化，停止；需重新审核 plan')
        # Persist the approved mapping before any data write. The input plan is
        # also retained, so verify can recover evidence after a report I/O failure.
        export(report, output)
        temp_prefix = '__nv2_' + uuid4().hex + '_'
        for row in report['mapping']:
            db.execute('UPDATE products SET product_code=? WHERE id=?', (temp_prefix + str(row['product_id']), row['product_id']))
        _fault('temporary_codes')
        for row in report['mapping']:
            db.execute('UPDATE products SET product_code=? WHERE id=?', (row['new_code'], row['product_id']))
        _fault('business_updated')
        for period, sequence in report['counters_after'].items():
            db.execute('''INSERT INTO product_code_months VALUES (?,?) ON CONFLICT(period_ym)
                DO UPDATE SET next_sequence=excluded.next_sequence''', (period, sequence))
        catalog.apply(db, report['ai'], report['plan_sha256'])
        _fault('mirror_updated')
        for statement in _registry_schema().values():
            db.execute(statement)
        db.execute(f'INSERT INTO {REGISTRY} VALUES (1,?)', (json.dumps(report, ensure_ascii=False),))
        _check_result(db, report)
        _fault('before_commit')
        db.commit()  # main + attached catalog + one-shot registry, one transaction.
    result = {'state': 'committed', 'plan_sha256': report['plan_sha256'], 'scope': report['scope']}
    try:
        receipt = Path(output) / 'result.json'
        with receipt.open('x', encoding='utf8') as stream:
            json.dump(result, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        result = {**result, 'state': 'committed_report_failed', 'error': type(error).__name__}
    return result


def verify(path):
    report = load_plan(path)
    with session(report['options']) as db:
        record = db.execute(f'SELECT document FROM {REGISTRY} WHERE id=1').fetchone()
        if record is None or json.loads(record[0]) != report:
            raise ValueError('一次性完成登记与批准计划不符')
        restored = bool(db.execute("SELECT count(*) FROM sqlite_master WHERE name=?", (RESTORE,)).fetchone()[0])
        if restored and db.execute(f'SELECT plan_sha256 FROM {RESTORE} WHERE id=1').fetchone()[0] != report['plan_sha256']:
            raise ValueError('恢复登记与批准计划不符')
        _check_result(db, report, restore=restored)
    return {'state': 'restored_verified' if restored else 'verified', 'plan_sha256': report['plan_sha256'],
            'scope': report['scope'],
            'products': len(report['mapping']), 'total_cartons': report['business']['total_cartons'],
            'ai_missing_ids': report['ai']['missing_from_mirror'], 'model_calls': 0}


def restore(path, confirmation, *, writers_stopped=False, output, _fault=lambda stage: None):
    """Inverse numbering only. Never copy old DB files over later business writes."""
    if not writers_stopped:
        raise ValueError('恢复也必须确认全部写入停止')
    report = load_plan(path, confirmation)
    with session(report['options'], write=True) as db:
        record = db.execute(f'SELECT document FROM {REGISTRY} WHERE id=1').fetchone()
        if record is None or json.loads(record[0]) != report:
            raise ValueError('持久映射与批准计划不符')
        if db.execute("SELECT count(*) FROM sqlite_master WHERE name=?", (RESTORE,)).fetchone()[0]:
            raise ValueError('已恢复，禁止重复恢复')
        # Includes every table digest, per-product/warehouse stock, all non-code
        # fields, monthly counters and protected AI files. Any new write blocks.
        _check_result(db, report)
        export(report, output)
        prefix = '__nv2r_' + uuid4().hex + '_'
        for row in report['mapping']:
            db.execute('UPDATE products SET product_code=? WHERE id=?', (prefix + str(row['product_id']), row['product_id']))
        for row in report['mapping']:
            db.execute('UPDATE products SET product_code=? WHERE id=?', (row['old_code'], row['product_id']))
        catalog.apply(db, report['ai'], report['plan_sha256'], restore=True)
        _fault('restore_updated')
        _check_result(db, report, restore=True)
        db.execute(f'CREATE TABLE {RESTORE} (id INTEGER PRIMARY KEY CHECK(id=1), plan_sha256 TEXT NOT NULL)')
        db.execute(f'INSERT INTO {RESTORE} VALUES(1,?)', (report['plan_sha256'],))
        db.commit()
    return {'state': 'restored', 'plan_sha256': report['plan_sha256'], 'scope': report['scope']}
