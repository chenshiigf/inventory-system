"""One production cutover, isolated from the offline tool's path permissions.

No application startup, AI imports, migration stamping or network calls. Public
CLI supplies ProductionEnvironment; tests supply synthetic paths and drain checks.
"""
from contextlib import closing
import csv
import json
import os
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from app.numbering_maintenance import Maintenance, MaintenanceError, atomic_json, checked_path, database_identity, file_gate
from app.services import product_code_offline as offline
from app.services.numbering_environment import file_binding
from app.services.product_code_rehearsal import digest

REVISION = 'e3f4a5b6c7d8'
CONTROL = 'production_numbering_cutovers'


def connect(path, *, write=False, recover_journal=False):
    path = checked_path(path, existing=True)
    for suffix in ('-wal', '-shm', '-journal'):
        sidecar = Path(str(path) + suffix)
        if os.path.lexists(sidecar):
            checked_path(sidecar, existing=True)
            # Stopped services must checkpoint before the maintenance operation.
            if not (write and recover_journal and suffix == '-journal'):
                raise MaintenanceError('存在数据库 sidecar；保持维护，使用受控事务恢复或人工排查，禁止手工删除')
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro&immutable=1'), uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    if not write:
        db.execute('PRAGMA query_only=ON')
    return db


def check(db, *, require_head=True):
    if [tuple(row) for row in db.execute('PRAGMA integrity_check')] != [('ok',)] or db.execute('PRAGMA foreign_key_check').fetchall():
        raise MaintenanceError('数据库完整性或外键校验失败')
    revisions = [r[0] for r in db.execute('SELECT version_num FROM alembic_version')]
    if len(revisions) != 1 or revisions[0] not in ({REVISION} if require_head else {'c8d9e0f1a2b3', 'd1e2f3a4b5c6', REVISION}):
        raise MaintenanceError('数据库 revision 不满足要求；只允许正常 Alembic 升级，不可 stamp')
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if names & {offline.CONTROL, 'numbering_rehearsal_guard'}:
        raise MaintenanceError('拒绝预演数据库身份')
    if require_head and CONTROL not in names:
        raise MaintenanceError('缺少正式切换登记表')


def snapshot(db, *, invariant=False):
    data = offline._business_snapshot(db, invariant=invariant)
    data.pop(CONTROL, None)
    return data


def evidence(db):
    result = offline._evidence(db)
    data = snapshot(db, invariant=True)
    result.update(invariants_sha256=digest(data), table_sha256={k: digest(v) for k, v in data.items()},
                  counts={k: len(v) for k, v in data.items()})
    return result


def verify_backup(context, source, manifest, state):
    context.backup_paths(source, manifest)
    source, manifest = checked_path(source, existing=True), checked_path(manifest, existing=True)
    if os.path.samefile(source, context.database):
        raise MaintenanceError('原件与目标不能为同一文件')
    binding = {'database': file_binding(source), 'manifest': file_binding(manifest)}
    metadata = json.loads(manifest.read_text(encoding='utf8'))
    if (metadata.get('backup_version') != 1 or metadata.get('database_filename') != source.name or
            metadata.get('database_sha256') != binding['database']['sha256'] or
            metadata.get('database_size') != binding['database']['size'] or
            metadata.get('uploads_archive') != 'uploads.tar.gz'):
        raise MaintenanceError('正式切换要求正常完整业务备份（数据库和 uploads），摘要必须匹配')
    archive = checked_path(manifest.parent / 'uploads.tar.gz', existing=True)
    binding['uploads'] = file_binding(archive)
    if (metadata.get('uploads_sha256') != binding['uploads']['sha256'] or
            metadata.get('uploads_archive_size') != binding['uploads']['size']):
        raise MaintenanceError('图片归档摘要不匹配')
    # Reuse the existing archive member validation without extraction/app startup.
    import tarfile
    from pathlib import PurePosixPath
    count, seen = 0, set()
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'uploads' or
                    not (member.isdir() or member.isfile()) or str(path) in seen):
                raise MaintenanceError('图片归档包含不安全或重复成员')
            seen.add(str(path))
            if member.isfile():
                count += 1
                with bundle.extractfile(member) as stream:
                    while stream.read(1024 * 1024):
                        pass  # Read gzip fully, detecting truncated archives; never extract.
    if count != metadata.get('uploads_file_count'):
        raise MaintenanceError('图片归档数量不匹配')
    now = time.time_ns()
    for value in binding.values():
        if not state['entered_at_ns'] <= value['mtime_ns'] <= now or now - value['mtime_ns'] > 3600 * 10**9:
            raise MaintenanceError('备份必须在当前维护窗口内生成且不超过一小时')
    with closing(connect(source)) as backup:
        check(backup, require_head=False)
        if digest(snapshot(backup)) != state['frozen_business_sha256']:
            raise MaintenanceError('备份业务数据与维护冻结集合不符')
        names = {r[0] for r in backup.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counters = dict(backup.execute('SELECT period_ym,next_sequence FROM product_code_months')) if 'product_code_months' in names else {}
        if counters != state['frozen_counters']:
            raise MaintenanceError('备份月份计数器与冻结状态不符')
    if binding != {k: file_binding(v['path']) for k, v in binding.items()}:
        raise MaintenanceError('备份核验期间发生变化')
    return binding


def enter(context, operation_id, release_sha):
    maintenance = Maintenance(context.data_root)
    state = maintenance.read()
    if state:
        maintenance.require(operation_id, context.database)
        if state['release_sha'] != release_sha or state['frozen_business_sha256'] is not None:
            raise MaintenanceError('维护已冻结或发布 SHA 不符，不允许覆盖原操作')
    else:
        # Persist the fail-closed intent BEFORE checks which may fail or be
        # interrupted. Only this same operation can finish an incomplete freeze.
        maintenance.enter(operation_id, release_sha, context.database, None)
    with file_gate(maintenance.gate):
        state = maintenance.require(operation_id, context.database)
        context.drained()
        with closing(connect(context.database)) as db:
            check(db, require_head=False)
            if db.execute("SELECT count(*) FROM sqlite_master WHERE name=?", (CONTROL,)).fetchone()[0] and db.execute(f'SELECT count(*) FROM {CONTROL}').fetchone()[0]:
                raise MaintenanceError('正式换号已完成，不允许另建维护操作')
            frozen = digest(snapshot(db))
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            counters = dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months')) if 'product_code_months' in names else {}
        state = {**state, 'frozen_business_sha256': frozen, 'frozen_counters': counters,
                 'drain_policy': getattr(context, 'policy', None)}
        atomic_json(maintenance.path, state, group=Path(context.database).stat().st_gid)
        return state


def _completion(db, record):
    if evidence(db) != record['evidence'] or offline._schema(db) != record['schema']:
        raise MaintenanceError('提交后非编号字段、关联或结构发生变化')
    actual = [list(r) for r in db.execute('SELECT id,product_code FROM products ORDER BY id')]
    if actual != [[r['product_id'], r['new_code']] for r in record['mapping']]:
        raise MaintenanceError('已提交编号与原映射不符')
    if dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months')) != record['expected_counters']:
        raise MaintenanceError('编号计数器变化')
    body = {k: v for k, v in record.items() if k != 'completion_sha256'}
    if digest(body) != record['completion_sha256']:
        raise MaintenanceError('切换登记摘要损坏')


def _plan(context, db, state, backup):
    check(db)
    if state.get('drain_policy') != getattr(context, 'policy', None):
        raise MaintenanceError('维护冻结后排空清单变化')
    if db.execute(f'SELECT count(*) FROM {CONTROL}').fetchone()[0]:
        raise MaintenanceError('正式换号已提交；禁止重复执行，请使用 status 或 recover-report')
    if digest(snapshot(db)) != state['frozen_business_sha256']:
        raise MaintenanceError('冻结后业务数据变化，拒绝新增或变更的历史集合')
    if dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months')) != state['frozen_counters']:
        raise MaintenanceError('冻结后编号计数器变化')
    mapping = offline._mapping(db)
    body = {'format': 'production-numbering-plan-v1', 'operation_id': state['operation_id'],
            'release_sha': state['release_sha'], 'database_identity': database_identity(context.database),
            'maintenance': state, 'backup': backup, 'mapping': mapping, 'schema': offline._schema(db),
            'evidence': evidence(db), 'expected_counters': offline._counters(db, mapping),
            'counters_before': dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months'))}
    return {**body, 'plan_sha256': digest(body), 'state': 'planned', 'dry_run': True}


def export(context, report):
    root = context.reports()
    directory = root / ('report-' + uuid4().hex)
    directory.mkdir(mode=0o700)
    mapping = report['mapping']
    with (directory / 'mapping.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(mapping[0]), quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(mapping)
        stream.flush()
        os.fsync(stream.fileno())
    for name, value in [('report.json', report), ('checksums.json', {
            'mapping.csv': file_binding(directory / 'mapping.csv')['sha256']})]:
        if name == 'checksums.json':
            value['report.json'] = file_binding(directory / 'report.json')['sha256']
        with (directory / name).open('x', encoding='utf8') as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
    if os.name != 'nt':
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return str(directory)


def plan(context, operation_id, source, manifest, *, execute=False,
         confirm_production_numbering=False, expected_plan_sha256=None, export_reports=False):
    with context.locked(), file_gate(Maintenance(context.data_root).gate):
        context.drained()
        state = Maintenance(context.data_root).require(operation_id, context.database)
        backup = verify_backup(context, source, manifest, state)
        committed = False
        with closing(connect(context.database, write=execute)) as db:
            try:
                if execute:
                    db.execute('BEGIN IMMEDIATE')
                report = _plan(context, db, state, backup)
                if execute:
                    if not confirm_production_numbering or expected_plan_sha256 != report['plan_sha256']:
                        raise MaintenanceError('必须独立确认生产换号且摘要与当前预检完全匹配')
                    precommit_reports = export(context, report)
                    for period, sequence in report['expected_counters'].items():
                        db.execute('INSERT INTO product_code_months VALUES (?,?) ON CONFLICT(period_ym) '
                                   'DO UPDATE SET next_sequence=excluded.next_sequence', (period, sequence))
                    token = uuid4().hex
                    for row in report['mapping']:
                        db.execute('UPDATE products SET product_code=? WHERE id=?',
                                   (f"__cutover_{token}_{row['product_id']}", row['product_id']))
                    for row in report['mapping']:
                        db.execute('UPDATE products SET product_code=? WHERE id=?', (row['new_code'], row['product_id']))
                    record = {**report, 'state': 'committed', 'dry_run': False,
                              'approved_plan_sha256': report['plan_sha256'], 'precommit_reports': precommit_reports}
                    record['completion_sha256'] = digest(record)
                    db.execute(f'INSERT INTO {CONTROL}(id,operation_id,state,document) VALUES(1,?,\'committed\',?)',
                               (operation_id, json.dumps(record, ensure_ascii=False, sort_keys=True)))
                    _completion(db, record)
                    check(db)
                    context.drained()
                    if backup != {k: file_binding(v['path']) for k, v in backup.items()}:
                        raise MaintenanceError('提交前备份发生变化')
                    Maintenance(context.data_root).require(operation_id, context.database)
                    db.commit()
                    committed = True
                    report = record
                if execute or export_reports:
                    try:
                        report = {**report, 'reports_directory': export(context, report)}
                    except Exception as error:
                        if not committed:
                            raise
                        report = {**report, 'state': 'committed_report_failed', 'report_error': str(error)}
                return report
            finally:
                db.rollback()


def recover_transaction(context, operation_id):
    """Explicit SQLite hot-journal recovery only; never assigns a product code."""
    with context.locked(), file_gate(Maintenance(context.data_root).gate):
        context.drained()
        state = Maintenance(context.data_root).require(operation_id, context.database)
        with closing(connect(context.database, write=True, recover_journal=True)) as db:
            # The first read lets SQLite, not our code, recover a hot journal.
            check(db)
            row = db.execute(f'SELECT operation_id,document FROM {CONTROL} WHERE id=1').fetchone()
            if row:
                record = json.loads(row['document'])
                if row['operation_id'] != operation_id or record['maintenance'] != state:
                    raise MaintenanceError('恢复后的已提交记录与维护操作不符')
                _completion(db, record)
                return {'state': 'committed', 'completion_sha256': record['completion_sha256']}
            if (digest(snapshot(db)) != state['frozen_business_sha256'] or
                    dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months')) != state['frozen_counters']):
                raise MaintenanceError('事务恢复后未还原冻结数据，禁止继续')
            return {'state': 'rolled_back_maintenance_held'}


def status(context, operation_id, *, recover_report=False, acceptance=None):
    with context.locked(), file_gate(Maintenance(context.data_root).gate):
        context.drained()
        maintenance = Maintenance(context.data_root)
        state = maintenance.require(operation_id, context.database)
        if state.get('drain_policy') != getattr(context, 'policy', None):
            raise MaintenanceError('维护冻结后排空清单变化')
        with closing(connect(context.database)) as db:
            check(db)
            row = db.execute(f'SELECT operation_id,state,document FROM {CONTROL} WHERE id=1').fetchone()
            if not row:
                if (digest(snapshot(db)) != state['frozen_business_sha256'] or
                        dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months')) != state['frozen_counters']):
                    raise MaintenanceError('未提交但数据不再匹配冻结状态，拒绝自动继续')
                return {'state': 'not_committed', 'maintenance': state, 'evidence': evidence(db)}
            record = json.loads(row['document'])
            if (row['operation_id'] != operation_id or row['state'] != 'committed' or
                    record['database_identity'] != database_identity(context.database) or record['maintenance'] != state):
                raise MaintenanceError('切换登记与当前维护操作或数据库身份不符')
            _completion(db, record)
        if recover_report:
            record = {**record, 'reports_directory': export(context, record)}
        if acceptance is not None:
            maintenance.release(operation_id, context.database, acceptance, record['completion_sha256'])
        return record
