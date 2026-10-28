"""Existing mirror numbering only; original sync facts and vectors stay intact."""
from contextlib import closing
import json
from pathlib import Path

from .numbering_io import binding, checked_path, connect, digest, integrity, no_sidecars
from .visual_search.catalog_metadata import MAGIC, SOURCE_ID, fingerprint

CONTROL = 'numbering_cutover'


def meta(db):
    return {k: json.loads(v) for k, v in db.execute('SELECT key,document FROM ai.mirror_meta')}


def rows(db):
    result = []
    for source, pid, document in db.execute('SELECT source_id,id,document FROM ai.mirror_products ORDER BY source_id,id'):
        row = json.loads(document)
        if (not isinstance(row, dict) or source != SOURCE_ID or row.get('source_id') != source or
                type(row.get('id')) is not int or row['id'] != pid or
                row.get('product_key') != f'{source}:{pid}' or 'product_code' not in row):
            raise ValueError('镜像包含未知来源、身份异常或缺失字段')
        result.append(row)
    return result


def snapshot(db):
    integrity(db, 'ai')
    result = {}
    for (name,) in db.execute("SELECT name FROM ai.sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        values = [list(r) for r in db.execute(f'SELECT * FROM ai.{quoted}')]
        if name == 'mirror_products':
            values = [[a, b, {k: v for k, v in json.loads(c).items() if k != 'product_code'}] for a, b, c in values]
        if name == 'mirror_meta':
            values = [[k, json.loads(v)] for k, v in values if k != CONTROL]
        result[name] = digest(sorted(values, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False)))
    schema = [list(r) for r in db.execute('SELECT type,name,tbl_name,sql FROM ai.sqlite_master ORDER BY type,name')]
    return {'schema': digest(schema), 'tables': result}


def protected(options):
    root, catalog = Path(options['visual_root']), Path(options['catalog_root'])
    identity = json.loads(checked_path(catalog / 'source.json', existing=True).read_text(encoding='utf8'))
    namespace = options['namespace']
    if (identity.get('format') != MAGIC or identity.get('source_id') != SOURCE_ID or
            namespace != 'production-' + fingerprint(identity)):
        raise ValueError('source identity/namespace 不符')
    index = checked_path(root / 'production-indices' / namespace)
    marker = json.loads(checked_path(index / 'source-binding.json', existing=True).read_text(encoding='utf8'))
    if marker != {'format': 'production-visual-index-v1', 'source_id': SOURCE_ID,
                  'source_identity': identity, 'index_namespace': namespace}:
        raise ValueError('索引 source binding 不符')
    with closing(connect(index / 'index.db')) as db:
        integrity(db)
        if [r[0] for r in db.execute('SELECT version_num FROM alembic_version')] != ['vs02']:
            raise ValueError('要求已有 vs02，禁止初始化或迁移 AI 索引')
        row = db.execute('SELECT * FROM visual_source_context WHERE index_key=?', (options['index_key'],)).fetchone()
        if not row or row['source_id'] != SOURCE_ID or row['source_identity'] != namespace:
            raise ValueError('index_key 或来源不符')
        from .visual_search.config import VisualConfig, PREPROCESS_VERSION
        config = VisualConfig(model=row['model'], dimension=row['dimension'], version=row['version'], source_identity=namespace)
        if config.index_key != options['index_key'] or row['preprocessing_version'] != PREPROCESS_VERSION:
            raise ValueError('模型参数不符')
        if db.execute("SELECT count(*) FROM visual_jobs WHERE status='running'").fetchone()[0]:
            raise ValueError('AI 索引任务未排空')
    budget = checked_path(root / 'daily-budget.sqlite3', existing=True)
    with closing(connect(budget)) as db:
        integrity(db)
        if db.execute('PRAGMA user_version').fetchone()[0] != 1:
            raise ValueError('预算库版本不符')
        db.execute('SELECT day,call_count,budgeted_cny,actual_cny,unknown_calls FROM daily_budget LIMIT 0')
        db.execute('SELECT id,day,reserve_cny,actual_cny,state FROM call_reservations LIMIT 0')
    files = {}
    # Includes images, query files and any existing manifests; none are created.
    for path in sorted(root.rglob('*')):
        checked_path(path)
        # Windows range locks deny reading their held byte, even in this process.
        # existing_lock already validates and holds these exact files throughout.
        transaction_files = {catalog / 'catalog.sqlite', Path(str(catalog / 'catalog.sqlite') + '-journal')}
        if path.is_file() and path not in transaction_files and path.name != 'calls.lock':
            no_sidecars(path)
            files[str(path.relative_to(root))] = binding(path)
    return identity, files


def plan(db, options, mapping):
    identity, files = protected(options)
    metadata, products = meta(db), rows(db)
    if CONTROL in metadata or metadata.get('identity') != identity:
        raise ValueError('镜像已换号或来源身份不一致')
    last = metadata['last_report']
    if not isinstance(last, dict) or last.get('scope') not in {'full', 'sample'}:
        raise ValueError('原始同步元数据异常')
    by_id = {r['product_id']: r for r in mapping}
    updates = []
    for row in products:
        pid = row['id']
        if pid not in by_id or row['product_code'] != by_id[pid]['old_code']:
            raise ValueError('未知镜像商品、映射缺失或旧编号漂移')
        updates.append({'product_id': pid, 'old_code': row['product_code'], 'new_code': by_id[pid]['new_code']})
    return {'invariants': snapshot(db), 'protected': files, 'updates': updates,
            'missing_from_mirror': sorted(set(by_id) - {r['id'] for r in products}),
            'fingerprint_before': last['catalog_fingerprint']}


def apply(db, prepared, approval, *, restore=False):
    updates = {r['product_id']: r['old_code' if restore else 'new_code'] for r in prepared['updates']}
    for row in rows(db):
        row['product_code'] = updates[row['id']]
        db.execute('UPDATE ai.mirror_products SET document=? WHERE source_id=? AND id=?',
                   (json.dumps(row, ensure_ascii=False), SOURCE_ID, row['id']))
    record = {'state': 'committed', 'operation': 'restored' if restore else 'renumbered',
              'approved_plan_sha256': approval}
    record['commit_sha256'] = fingerprint(record)
    db.execute('INSERT OR REPLACE INTO ai.mirror_meta VALUES (?,?)', (CONTROL, json.dumps(record)))


def verify(db, prepared, approval, *, restore=False):
    if snapshot(db) != prepared['invariants']:
        raise ValueError('镜像非编号字段、原始同步指纹或结构变化')
    field = 'old_code' if restore else 'new_code'
    if [[r['id'], r['product_code']] for r in rows(db)] != [[r['product_id'], r[field]] for r in prepared['updates']]:
        raise ValueError('镜像编号/集合不符')
    record = meta(db)[CONTROL]
    if (record.get('state') != 'committed' or record.get('operation') != ('restored' if restore else 'renumbered') or
            record.get('approved_plan_sha256') != approval or
            record.get('commit_sha256') != fingerprint({k:v for k,v in record.items() if k != 'commit_sha256'})):
        raise ValueError('镜像完成登记或独立 catalog_revision 不符')
