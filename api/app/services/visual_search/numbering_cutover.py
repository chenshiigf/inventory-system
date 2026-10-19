"""Approved metadata-only cutover. Never constructs a visual service/provider.

The business result may come from another host. Its approval digest must be
obtained from the committed business registry, not invented from a local plan.
"""
from contextlib import closing, contextmanager
from dataclasses import dataclass
import csv
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from app.numbering_maintenance import MaintenanceError, checked_path, file_gate
from app.services.numbering_environment import file_binding
from app.services.product_code_rehearsal import digest
from .catalog_metadata import MAGIC, SOURCE_ID, catalog_context, fingerprint

CONTROL = 'numbering_cutover'
PROJECTION = ('id', 'product_code', 'original_product_code', 'is_active', 'category_id',
              'warehouse_id', 'size', 'unit', 'image_identifier', 'thumbnail_identifier',
              'source_updated_at', 'packagings')


@dataclass(frozen=True)
class Inputs:
    visual_root: Path
    catalog_root: Path
    result: Path
    mapping: Path
    manifest: Path
    report_root: Path
    operation_id: str
    completion_sha256: str
    mapping_sha256: str
    namespace: str
    index_key: str


def connect(path, *, write=False, recover=False):
    path = checked_path(path, existing=True)
    for suffix in ('-wal', '-shm', '-journal'):
        if os.path.lexists(str(path) + suffix):
            checked_path(str(path) + suffix, existing=True)
            if not (recover and write and suffix == '-journal'):
                raise MaintenanceError('镜像或索引存在 sidecar，停止并保持 AI 关闭；不得手工删日志')
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro&immutable=1'), uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    if not write:
        db.execute('PRAGMA query_only=ON')
    return db


def integrity(db):
    if [tuple(r) for r in db.execute('PRAGMA integrity_check')] != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
        raise MaintenanceError('AI 数据完整性或外键异常')


def _rows(db):
    result = []
    for row in db.execute('SELECT source_id,id,document FROM mirror_products ORDER BY source_id,id'):
        document = json.loads(row['document'])
        if (row['source_id'] != SOURCE_ID or document.get('source_id') != SOURCE_ID or
                document.get('id') != row['id'] or document.get('product_key') != f"{SOURCE_ID}:{row['id']}"):
            raise MaintenanceError('镜像存在未知来源或商品身份不符')
        result.append(document)
    return result


def _meta(db):
    return {k: json.loads(v) for k, v in db.execute('SELECT key,document FROM mirror_meta')}


def invariants(db):
    integrity(db)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    values = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        rows = [list(row) for row in db.execute(f'SELECT * FROM {quoted} ORDER BY rowid')]
        if table == 'mirror_products':
            rows = [[r[0], r[1], {k: v for k, v in json.loads(r[2]).items() if k != 'product_code'}] for r in rows]
        elif table == 'mirror_meta':
            rows = [[k, json.loads(v)] for k, v in rows if k != CONTROL]
            for key, value in rows:
                if key == 'last_report':
                    value.pop('catalog_fingerprint', None)
        values[table] = rows
    schema = [list(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
    return {'schema_sha256': digest(schema), 'table_sha256': {k: digest(v) for k, v in values.items()}}


def approved_mapping(inputs):
    result = json.loads(checked_path(inputs.result, existing=True).read_text(encoding='utf8'))
    body = {k: v for k, v in result.items() if k != 'completion_sha256'}
    if (result.get('format') != 'production-numbering-plan-v1' or
            result.get('state') != 'committed' or result.get('dry_run') is not False or
            result.get('operation_id') != inputs.operation_id or
            result.get('completion_sha256') != inputs.completion_sha256 or digest(body) != inputs.completion_sha256 or
            result.get('approved_plan_sha256') != result.get('plan_sha256')):
        raise MaintenanceError('必须提供已提交且经过批准的业务换号结果和完成摘要')
    plan_body = {k: v for k, v in result.items() if k not in {
        'plan_sha256', 'state', 'dry_run', 'approved_plan_sha256', 'precommit_reports', 'completion_sha256'}}
    if (digest(plan_body) != result['plan_sha256'] or result['maintenance']['operation_id'] != inputs.operation_id or
            result['database_identity'] != result['maintenance']['database_identity']):
        raise MaintenanceError('业务批准计划、维护身份或完成登记内部不一致')
    mapping = result['mapping']
    ids = [r['product_id'] for r in mapping]
    if not 1 <= len(ids) <= 9999 or any(type(pid) is not int or pid <= 0 for pid in ids) or ids != sorted(set(ids)):
        raise MaintenanceError('完整映射商品 ID 集合异常')
    if ids != result['evidence']['product_ids'] or len(ids) != result['evidence']['counts']['products']:
        raise MaintenanceError('映射未覆盖业务切换时的完整商品集合')
    if any(row['new_code'] != f'25{n:04d}' for n, row in enumerate(mapping, 1)):
        raise MaintenanceError('完整映射编号不符合排名规则')
    if file_binding(inputs.mapping)['sha256'] != inputs.mapping_sha256:
        raise MaintenanceError('批准的 mapping.csv 摘要不符')
    with Path(inputs.mapping).open(encoding='utf-8-sig', newline='') as stream:
        actual = list(csv.DictReader(stream))
    expected = [{k: '' if v is None else str(v) for k, v in row.items()} for row in mapping]
    if actual != expected:
        raise MaintenanceError('mapping.csv 不是已提交结果中的完整映射')
    return {r['product_id']: r for r in mapping}


def _identity(inputs):
    root = checked_path(inputs.catalog_root)
    identity = json.loads(checked_path(root / 'source.json', existing=True).read_text(encoding='utf8'))
    if (identity.get('format') != MAGIC or identity.get('source_id') != SOURCE_ID or
            inputs.namespace != 'production-' + fingerprint(identity)):
        raise MaintenanceError('source identity 或批准的 namespace 不匹配')
    index = checked_path(inputs.visual_root) / 'production-indices' / inputs.namespace
    binding = json.loads(checked_path(index / 'source-binding.json', existing=True).read_text(encoding='utf8'))
    if binding != {'format': 'production-visual-index-v1', 'source_id': SOURCE_ID,
                   'source_identity': identity, 'index_namespace': inputs.namespace}:
        raise MaintenanceError('索引 source binding 不一致')
    with closing(connect(index / 'index.db')) as db:
        integrity(db)
        if db.execute('SELECT version_num FROM alembic_version').fetchone()[0] != 'vs02':
            raise MaintenanceError('生产索引必须是已有 vs02，不允许初始化或迁移索引')
        context = db.execute('SELECT * FROM visual_source_context WHERE index_key=?', (inputs.index_key,)).fetchone()
        if not context or context['source_id'] != SOURCE_ID or context['source_identity'] != inputs.namespace:
            raise MaintenanceError('index_key 或模型来源身份不符')
        if db.execute("SELECT count(*) FROM visual_jobs WHERE status='running'").fetchone()[0]:
            raise MaintenanceError('索引任务未排空')
        from .config import VisualConfig, PREPROCESS_VERSION
        model = VisualConfig(model=context['model'], dimension=context['dimension'], version=context['version'],
                             source_identity=inputs.namespace)
        if model.index_key != inputs.index_key or context['preprocessing_version'] != PREPROCESS_VERSION:
            raise MaintenanceError('模型参数或预处理版本与 index_key 不符')
    return identity, index


def protected_files(inputs):
    identity, index = _identity(inputs)
    paths = {'source.json': Path(inputs.catalog_root) / 'source.json',
             'source-binding.json': index / 'source-binding.json', 'index.db': index / 'index.db',
             'budget': Path(inputs.visual_root) / 'daily-budget.sqlite3', 'business_result': inputs.result,
             'approved_mapping': inputs.mapping, 'sync_manifest': inputs.manifest}
    for path in (index / 'index.db', paths['budget']):
        if any(os.path.lexists(str(path) + suffix) for suffix in ('-wal', '-shm', '-journal')):
            raise MaintenanceError('索引或预算存在 sidecar，无法证明账本完整不变量')
    return identity, {k: file_binding(path) for k, path in paths.items()}


def vector_evidence(inputs):
    index = checked_path(inputs.visual_root) / 'production-indices' / inputs.namespace / 'index.db'
    result = {}
    with closing(connect(index)) as db:
        for table, order in [('visual_embeddings', 'product_id,index_key'), ('visual_image_vectors', 'index_key,image_hash')]:
            rows = [[{'sqlite_blob_sha256': hashlib.sha256(v).hexdigest()} if isinstance(v, bytes) else v for v in row]
                    for row in db.execute(f'SELECT * FROM {table} ORDER BY {order}')]
            result[table] = {'rows': len(rows), 'sha256': digest(rows)}
    return result


@contextmanager
def paused(inputs):
    root, catalog = checked_path(inputs.visual_root), checked_path(inputs.catalog_root)
    # Use the exact byte range/flock shared by the existing CallLock. Require
    # existing locks: dry-run never initializes a catalog or creates lock files.
    for path in (root / 'calls.lock', catalog / 'calls.lock'):
        checked_path(path, existing=True)
    with file_gate(root / 'calls.lock'), file_gate(catalog / 'calls.lock'):
        marker = checked_path(root / 'runtime/ai-search.enabled')
        if os.path.lexists(marker):
            raise MaintenanceError('必须先关闭 AI 并等待在途调用及同步结束')
        yield


def _plan(inputs, db):
    mapping = approved_mapping(inputs)
    identity, protected = protected_files(inputs)
    meta, rows = _meta(db), _rows(db)
    if CONTROL in meta:
        raise MaintenanceError('镜像换号已提交，禁止重复执行；使用 status/recover-report')
    if meta.get('identity') != identity:
        raise MaintenanceError('镜像元数据 identity 不匹配')
    manifest = json.loads(Path(inputs.manifest).read_text(encoding='utf8'))
    snapshot = manifest['snapshot']
    if manifest.get('identity') != identity or fingerprint(snapshot) != meta['last_report']['catalog_fingerprint']:
        raise MaintenanceError('必须指定当前目录 fingerprint 对应的原始同步 manifest')
    if meta.get('categories') != snapshot.get('categories') or meta.get('warehouses') != snapshot.get('warehouses'):
        raise MaintenanceError('分类或仓库元数据不符合目录 fingerprint 的原始快照')
    manifest_rows = {r['id']: r for r in manifest['records']}
    if len(manifest_rows) != len(manifest['records']):
        raise MaintenanceError('同步 manifest 含重复商品')
    updates = []
    scope = meta['last_report']['scope']
    if scope not in {'full', 'sample'} or not set(manifest_rows).issubset({row['id'] for row in rows}):
        raise MaintenanceError('同步范围未知或本次已发布的镜像商品缺失')
    for row in rows:
        pid = row['id']
        if pid not in mapping or row['product_code'] != mapping[pid]['old_code']:
            raise MaintenanceError('镜像存在未知商品、映射缺失或旧货号漂移')
        if ((pid in manifest_rows and manifest_rows[pid] != row) or
                (row.get('source_present') and scope == 'full' and pid not in manifest_rows)):
            raise MaintenanceError('当前镜像与批准的同步 manifest 不一致')
        updates.append({'product_id': pid, 'old_code': row['product_code'], 'new_code': mapping[pid]['new_code']})
    changed = deepcopy(snapshot)
    snapshot_ids = []
    for row in changed['products']:
        pid = row['id']
        if pid not in mapping or row['product_code'] != mapping[pid]['old_code']:
            raise MaintenanceError('同步快照与完整映射不符')
        original = manifest_rows.get(pid)
        if original is None or any(original.get(k) != row.get(k) for k in PROJECTION):
            raise MaintenanceError('同步快照与镜像记录投影不符')
        snapshot_ids.append(pid)
        row['product_code'] = mapping[pid]['new_code']
    if len(snapshot_ids) != len(set(snapshot_ids)) or set(manifest_rows) != set(snapshot_ids):
        raise MaintenanceError('同步 manifest 商品集合不一致')
    body = {'format': 'ai-numbering-cutover-v1', 'operation_id': inputs.operation_id,
            'business_completion_sha256': inputs.completion_sha256, 'mapping_sha256': inputs.mapping_sha256,
            'source_id': SOURCE_ID, 'namespace': inputs.namespace, 'index_key': inputs.index_key,
            'catalog_identity': file_identity(Path(inputs.catalog_root) / 'catalog.sqlite'),
            'protected': protected, 'invariants': invariants(db), 'updates': updates,
            'vector_evidence': vector_evidence(inputs),
            'business_products': len(mapping), 'mirror_products': len(rows),
            'missing_from_mirror': sorted(set(mapping) - {r['id'] for r in rows}),
            'fingerprint_before': fingerprint(snapshot), 'fingerprint_after': fingerprint(changed),
            'snapshot_after': changed}
    return {**body, 'plan_sha256': digest(body), 'state': 'planned', 'dry_run': True}


def file_identity(path):
    from app.numbering_maintenance import database_identity
    return database_identity(path)


def export(inputs, report):
    root = checked_path(inputs.report_root)
    repository = Path(__file__).resolve().parents[4]
    if (root.is_relative_to(repository) or root.is_relative_to(checked_path(inputs.visual_root)) or
            root.is_relative_to(checked_path(inputs.catalog_root))):
        raise MaintenanceError('真实报告必须存放在仓库及 AI 数据目录之外')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = root / ('ai-cutover-' + uuid4().hex)
    target.mkdir(mode=0o700)
    for name, body in [('report.json', report), ('checksums.json', None)]:
        if body is None:
            body = {'report.json': file_binding(target / 'report.json')['sha256']}
        with (target / name).open('x', encoding='utf8') as stream:
            json.dump(body, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    return str(target)


def verify_committed(db, record):
    body = {k: v for k, v in record.items() if k != 'commit_sha256'}
    if record.get('state') != 'committed' or digest(body) != record.get('commit_sha256'):
        raise MaintenanceError('镜像完成登记摘要异常')
    if invariants(db) != record['invariants']:
        raise MaintenanceError('镜像非编号字段发生变化')
    if [[r['id'], r['product_code']] for r in _rows(db)] != [[r['product_id'], r['new_code']] for r in record['updates']]:
        raise MaintenanceError('镜像结果编号或商品集合不符')
    if _meta(db)['last_report']['catalog_fingerprint'] != record['fingerprint_after'] or fingerprint(record['snapshot_after']) != record['fingerprint_after']:
        raise MaintenanceError('镜像目录 fingerprint 不符')


def plan(inputs, *, execute=False, confirm_metadata_only=False, expected_plan_sha256=None, export_reports=False):
    committed = False
    with paused(inputs), closing(connect(Path(inputs.catalog_root) / 'catalog.sqlite', write=execute)) as db:
        try:
            if execute:
                db.execute('BEGIN IMMEDIATE')
            report = _plan(inputs, db)
            if execute:
                if not confirm_metadata_only or expected_plan_sha256 != report['plan_sha256']:
                    raise MaintenanceError('镜像执行需要独立确认和完全匹配的计划摘要')
                pre = export(inputs, report)
                updates = {row['product_id']: row['new_code'] for row in report['updates']}
                for row in _rows(db):
                    row['product_code'] = updates[row['id']]
                    db.execute('UPDATE mirror_products SET document=? WHERE source_id=? AND id=?',
                               (json.dumps(row, ensure_ascii=False), SOURCE_ID, row['id']))
                last_report = _meta(db)['last_report']
                last_report['catalog_fingerprint'] = report['fingerprint_after']
                db.execute("UPDATE mirror_meta SET document=? WHERE key='last_report'", (json.dumps(last_report, ensure_ascii=False),))
                report = {**report, 'state': 'committed', 'dry_run': False, 'precommit_reports': pre}
                report['commit_sha256'] = digest(report)
                db.execute('INSERT INTO mirror_meta(key,document) VALUES(?,?)', (CONTROL, json.dumps(report, ensure_ascii=False)))
                verify_committed(db, report)
                if protected_files(inputs)[1] != report['protected']:
                    raise MaintenanceError('提交前向量、预算或批准来源发生变化')
                if os.path.lexists(Path(inputs.visual_root) / 'runtime/ai-search.enabled'):
                    raise MaintenanceError('提交前 AI 被重新启用')
                db.commit()
                committed = True
            if execute or export_reports:
                try:
                    report = {**report, 'reports_directory': export(inputs, report)}
                except Exception as error:
                    if not committed:
                        raise
                    report = {**report, 'state': 'committed_report_failed', 'report_error': type(error).__name__}
            return report
        finally:
            db.rollback()


def status(inputs, *, recover_report=False, recover_transaction=False):
    with paused(inputs), closing(connect(Path(inputs.catalog_root) / 'catalog.sqlite', write=recover_transaction,
                                        recover=recover_transaction)) as db:
        meta = _meta(db)  # First read lets SQLite recover a genuine hot journal.
        if CONTROL not in meta:
            _plan(inputs, db)
            return {'state': 'not_committed'}
        record = meta[CONTROL]
        if (record['operation_id'] != inputs.operation_id or record['business_completion_sha256'] != inputs.completion_sha256 or
                record['mapping_sha256'] != inputs.mapping_sha256 or record['namespace'] != inputs.namespace or
                record['index_key'] != inputs.index_key or record['catalog_identity'] != file_identity(Path(inputs.catalog_root) / 'catalog.sqlite')):
            raise MaintenanceError('镜像完成登记不属于本次批准操作')
        approved_mapping(inputs)
        verify_committed(db, record)
        if protected_files(inputs)[1] != record['protected']:
            raise MaintenanceError('向量、预算、模型身份或批准来源变化')
        if vector_evidence(inputs) != record['vector_evidence']:
            raise MaintenanceError('向量行集合或内容变化')
        return {**record, 'reports_directory': export(inputs, record)} if recover_report else record
