"""Pure metadata checks reused from the reviewed cutover, without provider/index jobs."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path

from .numbering_io import binding, checked_path, connect, digest, integrity, no_sidecars
from .visual_search.catalog_metadata import MAGIC, SOURCE_ID, fingerprint

CONTROL = 'numbering_cutover'
PROJECTION = ('id', 'product_code', 'original_product_code', 'is_active', 'category_id',
              'warehouse_id', 'size', 'unit', 'image_identifier', 'thumbnail_identifier',
              'source_updated_at', 'packagings')


def meta(db):
    return {k: json.loads(v) for k, v in db.execute('SELECT key,document FROM ai.mirror_meta')}


def rows(db):
    result = []
    for source, pid, document in db.execute('SELECT source_id,id,document FROM ai.mirror_products ORDER BY source_id,id'):
        row = json.loads(document)
        if (source != SOURCE_ID or row.get('source_id') != source or row.get('id') != pid or
                row.get('product_key') != f'{source}:{pid}'):
            raise ValueError('镜像包含未知商品来源或身份异常')
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
            for key, value in values:
                if key == 'last_report':
                    value.pop('catalog_fingerprint', None)
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
    source_binding = json.loads(checked_path(index / 'source-binding.json', existing=True).read_text(encoding='utf8'))
    if source_binding != {'format': 'production-visual-index-v1', 'source_id': SOURCE_ID,
                          'source_identity': identity, 'index_namespace': namespace}:
        raise ValueError('索引 source binding 不符')
    with closing(connect(index / 'index.db')) as db:
        integrity(db)
        if db.execute('SELECT version_num FROM alembic_version').fetchone()[0] != 'vs02':
            raise ValueError('要求已有 vs02，禁止初始化或迁移 AI 索引')
        row = db.execute('SELECT * FROM visual_source_context WHERE index_key=?', (options['index_key'],)).fetchone()
        if not row or row['source_id'] != SOURCE_ID or row['source_identity'] != namespace:
            raise ValueError('index_key 或模型来源不符')
        from .visual_search.config import VisualConfig, PREPROCESS_VERSION
        config = VisualConfig(model=row['model'], dimension=row['dimension'], version=row['version'], source_identity=namespace)
        if config.index_key != options['index_key'] or row['preprocessing_version'] != PREPROCESS_VERSION:
            raise ValueError('模型参数不符')
        if db.execute("SELECT count(*) FROM visual_jobs WHERE status='running'").fetchone()[0]:
            raise ValueError('AI 索引任务未排空')
    paths = {'source': catalog / 'source.json', 'binding': index / 'source-binding.json',
             'index': index / 'index.db', 'budget': root / 'daily-budget.sqlite3',
             'manifest': Path(options['manifest'])}
    for path in (paths['index'], paths['budget']):
        no_sidecars(path)
        with closing(connect(path)) as db:
            integrity(db)
    return identity, {k: binding(v) for k, v in paths.items()}


def plan(db, options, mapping):
    identity, files = protected(options)
    metadata, products = meta(db), rows(db)
    if CONTROL in metadata or metadata.get('identity') != identity:
        raise ValueError('镜像已换号或来源身份不一致')
    manifest = json.loads(Path(options['manifest']).read_text(encoding='utf8'))
    original = manifest['snapshot']
    if (manifest.get('identity') != identity or fingerprint(original) != metadata['last_report']['catalog_fingerprint'] or
            metadata.get('categories') != original.get('categories') or metadata.get('warehouses') != original.get('warehouses')):
        raise ValueError('当前目录与同步 manifest 不符')
    by_id = {r['product_id']: r for r in mapping}
    records = {r['id']: r for r in manifest['records']}
    if len(records) != len(manifest['records']):
        raise ValueError('同步 manifest 商品重复')
    ids = {r['id'] for r in products}
    scope = metadata['last_report']['scope']
    if scope not in {'full', 'sample'} or not set(records).issubset(ids):
        raise ValueError('镜像同步范围或商品集合异常')
    updates = []
    for row in products:
        pid = row['id']
        if pid not in by_id or row['product_code'] != by_id[pid]['old_code']:
            raise ValueError('未知镜像商品、映射缺失或旧货号漂移')
        if ((pid in records and records[pid] != row) or
                (scope == 'full' and row.get('source_present') and pid not in records)):
            raise ValueError('镜像与同步 manifest 记录不符')
        updates.append({'product_id': pid, 'old_code': row['product_code'], 'new_code': by_id[pid]['new_code']})
    changed = deepcopy(original)
    seen = []
    for row in changed['products']:
        pid = row['id']
        if (pid not in by_id or pid not in records or row['product_code'] != by_id[pid]['old_code'] or
                any(row.get(k) != records[pid].get(k) for k in PROJECTION)):
            raise ValueError('目录指纹的原始投影不符')
        seen.append(pid)
        row['product_code'] = by_id[pid]['new_code']
    if len(seen) != len(set(seen)) or set(seen) != set(records):
        raise ValueError('目录快照商品集合异常')
    return {'invariants': snapshot(db), 'protected': files, 'updates': updates,
            'missing_from_mirror': sorted(set(by_id) - ids),
            'fingerprint_before': fingerprint(original), 'fingerprint_after': fingerprint(changed),
            'snapshot_after': changed}


def apply(db, prepared, approval):
    updates = {r['product_id']: r['new_code'] for r in prepared['updates']}
    for row in rows(db):
        row['product_code'] = updates[row['id']]
        db.execute('UPDATE ai.mirror_products SET document=? WHERE source_id=? AND id=?',
                   (json.dumps(row, ensure_ascii=False), SOURCE_ID, row['id']))
    last_report = meta(db)['last_report']
    last_report['catalog_fingerprint'] = prepared['fingerprint_after']
    db.execute("UPDATE ai.mirror_meta SET document=? WHERE key='last_report'", (json.dumps(last_report, ensure_ascii=False),))
    record = {'state': 'committed', 'approved_plan_sha256': approval,
              'fingerprint_after': prepared['fingerprint_after']}
    record['commit_sha256'] = fingerprint(record)
    db.execute('INSERT INTO ai.mirror_meta VALUES (?,?)', (CONTROL, json.dumps(record)))


def verify(db, prepared, approval):
    if snapshot(db) != prepared['invariants']:
        raise ValueError('镜像非编号字段或结构变化')
    if [[r['id'], r['product_code']] for r in rows(db)] != [[r['product_id'], r['new_code']] for r in prepared['updates']]:
        raise ValueError('镜像编号/集合不符')
    metadata = meta(db)
    record = metadata[CONTROL]
    if (record.get('state') != 'committed' or record.get('approved_plan_sha256') != approval or
            record.get('commit_sha256') != fingerprint({k:v for k,v in record.items() if k != 'commit_sha256'}) or
            metadata['last_report']['catalog_fingerprint'] != prepared['fingerprint_after'] or
            fingerprint(prepared['snapshot_after']) != prepared['fingerprint_after']):
        raise ValueError('镜像完成登记或指纹不符')
