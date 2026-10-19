"""All data is generated here; no production backup, process or AI access."""
from contextlib import contextmanager, closing
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from app.numbering_maintenance import Maintenance, MaintenanceError, checked_path, file_gate
from app.services import product_code_cutover as tool
from app.services.numbering_environment import ProductionEnvironment, file_binding
from tests.test_product_code_offline import synthetic_backup, database


OPERATION = 'synthetic-cutover-001'
RELEASE = 'a' * 40


class SyntheticEnvironment:
    """Only imported by tests. There is deliberately no CLI switch for this."""
    def __init__(self, root, db):
        self.database = db
        self.data_root = root
        self.ready = True
        self.report_root = root / 'reports'

    @contextmanager
    def locked(self):
        with file_gate(self.data_root / 'deploy.lock'):
            yield

    def drained(self):
        if not self.ready:
            raise MaintenanceError('synthetic service not drained')

    def backup_paths(self, source, manifest):
        for path in (source, manifest):
            checked_path(path, existing=True)

    def reports(self):
        self.report_root.mkdir(exist_ok=True)
        return self.report_root


class CutoverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='inventory-cutover-synthetic-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target, _ = synthetic_backup(self.root / 'business')
        with database(self.target) as db:
            db.executescript("""CREATE TABLE production_numbering_cutovers (
                id INTEGER PRIMARY KEY CHECK(id=1), operation_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL CHECK(state='committed'), document TEXT NOT NULL);
                UPDATE alembic_version SET version_num='e3f4a5b6c7d8';""")
        self.context = SyntheticEnvironment(self.target.parent, self.target)
        self.maintenance = Maintenance(self.context.data_root)
        tool.enter(self.context, OPERATION, RELEASE)
        self.source = self.root / 'backup/inventory.db'
        self.source.parent.mkdir()
        shutil.copyfile(self.target, self.source)
        archive = self.source.parent / 'uploads.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            info = tarfile.TarInfo('uploads/synthetic.jpg')
            info.size = 5
            bundle.addfile(info, io.BytesIO(b'image'))
        self.manifest = self.source.parent / 'manifest.json'
        self.manifest.write_text(json.dumps({
            'backup_version': 1, 'created_at': 'synthetic', 'database_filename': self.source.name,
            'database_sha256': file_binding(self.source)['sha256'], 'database_size': self.source.stat().st_size,
            'uploads_archive': archive.name, 'uploads_sha256': file_binding(archive)['sha256'],
            'uploads_archive_size': archive.stat().st_size, 'uploads_file_count': 1}), encoding='utf8')

    def plan(self, **kwargs):
        return tool.plan(self.context, OPERATION, self.source, self.manifest, **kwargs)

    def execute(self, plan=None):
        plan = plan or self.plan()
        return self.plan(execute=True, confirm_production_numbering=True, expected_plan_sha256=plan['plan_sha256'])

    def test_readonly_plan_mapping_and_isolation(self):
        original, before = file_binding(self.source), self.target.read_bytes()
        result = self.plan(export_reports=True)
        self.assertEqual(result['state'], 'planned')
        self.assertEqual([r['product_id'] for r in result['mapping']], [2, 4, 6, 8, 10, 12, 100])
        self.assertEqual([r['new_code'] for r in result['mapping']], [str(250000 + n) for n in range(1, 8)])
        self.assertEqual(result['evidence']['total_cartons'], 28)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(file_binding(self.source), original)
        self.assertFalse(os.path.samefile(self.source, self.target))
        self.assertEqual(self.plan()['plan_sha256'], result['plan_sha256'])
        report = Path(result['reports_directory'])
        checks = json.loads((report / 'checksums.json').read_text())
        for name, value in checks.items():
            self.assertEqual(file_binding(report / name)['sha256'], value)

    def test_atomic_success_field_invariants_swaps_counters_and_once_only(self):
        original = file_binding(self.source)
        plan = self.plan()
        result = self.execute(plan)
        self.assertEqual(result['state'], 'committed')
        self.assertEqual(tool.status(self.context, OPERATION)['evidence'], plan['evidence'])
        with closing(tool.connect(self.target)) as db:
            self.assertEqual(tool.evidence(db), plan['evidence'])
            self.assertEqual(dict(db.execute('SELECT period_ym,next_sequence FROM product_code_months')), plan['expected_counters'])
            self.assertEqual(db.execute('SELECT count(*) FROM production_numbering_cutovers').fetchone()[0], 1)
        with self.assertRaisesRegex(MaintenanceError, '禁止重复'):
            self.execute(plan)
        self.assertEqual(file_binding(self.source), original)
        self.assertTrue(Maintenance(self.context.data_root).held())

    def test_confirmation_missing_or_stale(self):
        before = self.target.read_bytes()
        for kwargs in ({'execute': True}, {'execute': True, 'confirm_production_numbering': True, 'expected_plan_sha256': '0' * 64}):
            with self.assertRaisesRegex(MaintenanceError, '独立确认'):
                self.plan(**kwargs)
        self.assertEqual(self.target.read_bytes(), before)

    def test_bad_maintenance_operation_or_identity(self):
        state = self.maintenance.read()
        for key, value in [('operation_id', 'different-operation'), ('database_identity', {})]:
            bad = {**state, key: value}
            self.maintenance.path.write_text(json.dumps(bad), encoding='utf8')
            with self.assertRaises(MaintenanceError):
                self.plan()
        self.maintenance.path.write_text(json.dumps(state), encoding='utf8')

    def test_missing_or_corrupt_marker_fails_closed_on_restart(self):
        self.maintenance.path.unlink()
        with self.assertRaises(MaintenanceError):
            Maintenance(self.context.data_root).held()
        with self.assertRaises(MaintenanceError):
            self.plan()

    def test_services_must_be_drained(self):
        self.context.ready = False
        with self.assertRaisesRegex(MaintenanceError, 'not drained'):
            self.plan()

    def test_revision_is_not_completion(self):
        self.assertEqual(tool.status(self.context, OPERATION)['state'], 'not_committed')
        with database(self.target) as db:
            db.execute("UPDATE alembic_version SET version_num='d1e2f3a4b5c6'")
        with self.assertRaisesRegex(MaintenanceError, 'revision'):
            self.plan()

    def test_frozen_collection_rejects_new_rows(self):
        with database(self.target) as db:
            db.execute("INSERT INTO products(id,product_code,is_active) VALUES(999,'unexpected',1)")
        with self.assertRaisesRegex(MaintenanceError, '冻结后'):
            self.plan()

    def test_frozen_noncode_fields_and_counters_reject_changes(self):
        for sql in ("UPDATE products SET remark='changed' WHERE id=2",
                    "UPDATE product_code_months SET next_sequence=501"):
            with database(self.target) as db:
                db.execute('BEGIN')
                db.execute(sql)
                # Plan uses a separate connection, so commit then restore this synthetic DB.
            with self.assertRaisesRegex(MaintenanceError, '冻结后'):
                self.plan()
            shutil.copyfile(self.source, self.target)

    def test_invariant_failure_rolls_back_everything(self):
        plan, before = self.plan(), self.target.read_bytes()
        with patch.object(tool, '_completion', side_effect=MaintenanceError('injected invariant failure')):
            with self.assertRaisesRegex(MaintenanceError, 'injected'):
                self.execute(plan)
        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(tool.status(self.context, OPERATION)['state'], 'not_committed')
        self.assertTrue(self.maintenance.held())

    def test_keyboard_interrupt_rolls_back_and_resumes_original_operation(self):
        plan = self.plan()
        with patch.object(tool, '_completion', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.execute(plan)
        self.assertTrue(Maintenance(self.context.data_root).held())
        self.assertEqual(self.plan()['plan_sha256'], plan['plan_sha256'])
        self.assertEqual(self.execute(plan)['state'], 'committed')

    def test_process_death_hot_journal_recovers_without_renumbering(self):
        original = self.plan()
        code = '''import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1]); db.execute('PRAGMA cache_size=1'); db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE products SET product_code='partial-' || id,remark=?", ('x'*200000,))
os._exit(19)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.target)], check=False)
        self.assertEqual(result.returncode, 19)
        self.assertTrue(Path(str(self.target) + '-journal').exists())
        with self.assertRaisesRegex(MaintenanceError, 'sidecar'):
            self.plan()
        restored = tool.recover_transaction(self.context, OPERATION)
        self.assertEqual(restored['state'], 'rolled_back_maintenance_held')
        self.assertTrue(Maintenance(self.context.data_root).held())
        self.assertEqual(self.plan()['plan_sha256'], original['plan_sha256'])

    def test_after_commit_process_interruption_status_recovery_is_readonly(self):
        original = tool.export
        calls = 0
        def interrupt_after_commit(context, report):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return original(context, report)
        with patch.object(tool, 'export', side_effect=interrupt_after_commit):
            with self.assertRaises(KeyboardInterrupt):
                self.execute()
        self.assertEqual(tool.status(self.context, OPERATION)['state'], 'committed')
        self.assertTrue(self.maintenance.held())

    def test_committed_report_failure_is_not_retryable_and_recovers_readonly(self):
        plan, original = self.plan(), tool.export
        calls = 0
        def fail_after_commit(context, report):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('synthetic disk full')
            return original(context, report)
        with patch.object(tool, 'export', side_effect=fail_after_commit):
            result = self.execute(plan)
        self.assertEqual(result['state'], 'committed_report_failed')
        before = self.target.read_bytes()
        restored = tool.status(self.context, OPERATION, recover_report=True)
        self.assertEqual(restored['state'], 'committed')
        self.assertTrue(Path(restored['reports_directory']).is_dir())
        self.assertEqual(before, self.target.read_bytes())
        with self.assertRaisesRegex(MaintenanceError, '禁止重复'):
            self.execute(plan)

    def test_precommit_report_failure_never_writes_codes(self):
        before, plan = self.target.read_bytes(), self.plan()
        with patch.object(tool, 'export', side_effect=OSError('precommit disk full')):
            with self.assertRaises(OSError):
                self.execute(plan)
        self.assertEqual(before, self.target.read_bytes())

    def test_backup_tamper_same_file_and_old_backup_rejected(self):
        with self.assertRaises(MaintenanceError):
            tool.plan(self.context, OPERATION, self.target, self.manifest)
        os.utime(self.source, ns=(1, 1))
        with self.assertRaisesRegex(MaintenanceError, '维护窗口'):
            self.plan()
        self.source.write_bytes(self.source.read_bytes() + b'tampered')
        with self.assertRaisesRegex(MaintenanceError, '摘要'):
            self.plan()

    def test_hardlink_and_sidecar_rejected(self):
        link = self.root / 'linked.db'
        os.link(self.source, link)
        with self.assertRaisesRegex(MaintenanceError, '硬链接'):
            self.plan()
        link.unlink()
        Path(str(self.target) + '-journal').write_bytes(b'not a real journal')
        with self.assertRaisesRegex(MaintenanceError, 'sidecar'):
            self.plan()

    def test_invalid_acceptance_cannot_release_success(self):
        result = self.execute()
        with self.assertRaisesRegex(MaintenanceError, '验收记录'):
            tool.status(self.context, OPERATION, acceptance={})
        self.assertTrue(self.maintenance.held())
        receipt = {key: result[key] for key in ('operation_id', 'release_sha', 'database_identity', 'completion_sha256')}
        receipt['checks'] = {'inventory': True, 'ai_metadata': True, 'old_sessions': True, 'web': True}
        tool.status(self.context, OPERATION, acceptance=receipt)
        self.assertFalse(Maintenance(self.context.data_root).held())

    def test_exclusive_gate_refuses_inflight_write(self):
        with file_gate(self.maintenance.gate, exclusive=False):
            with self.assertRaises(MaintenanceError):
                self.plan()

    def test_public_production_boundary_cannot_target_synthetic_database(self):
        with self.assertRaises(MaintenanceError):
            ProductionEnvironment(self.target)

    def test_incomplete_maintenance_entry_stays_held_and_same_operation_can_finish(self):
        root = self.root / 'entry-failure'
        root.mkdir()
        target = root / 'inventory.db'
        shutil.copyfile(self.source, target)
        context = SyntheticEnvironment(root, target)
        context.ready = False
        with self.assertRaises(MaintenanceError):
            tool.enter(context, OPERATION, RELEASE)
        state = Maintenance(root).read()
        self.assertEqual(state['state'], 'held')
        self.assertIsNone(state['frozen_business_sha256'])
        with self.assertRaises(MaintenanceError):
            tool.enter(context, 'different-operation', RELEASE)
        context.ready = True
        tool.enter(context, OPERATION, RELEASE)
        self.assertIsNotNone(Maintenance(root).read()['frozen_business_sha256'])


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inventory-drain-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.context = ProductionEnvironment.__new__(ProductionEnvironment)
        self.context.data_root = self.root / 'data'
        self.context.data_root.mkdir()
        self.context.proc_root = self.root / 'proc'
        self.context.proc_root.mkdir()
        self.context.policy = {'stopped_units': ['synthetic-api.service'],
                               'ai_enable_markers': [str(self.root / 'disabled-ai-marker')]}
        self.context._policy = lambda: self.context.policy

    def response(self, args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout='LoadState=loaded\nActiveState=inactive\n' if 'show' in args else '')

    def test_inactive_services_and_disabled_marker_pass(self):
        with patch('app.services.numbering_environment.subprocess.run', side_effect=self.response):
            self.context.drained()

    def test_active_service_and_unknown_job_fail(self):
        for output in ('LoadState=loaded\nActiveState=active\n', 'LoadState=not-found\nActiveState=inactive\n'):
            with patch('app.services.numbering_environment.subprocess.run', return_value=subprocess.CompletedProcess([], 0, stdout=output)):
                with self.assertRaises(MaintenanceError):
                    self.context.drained()
        def unknown(args, **kwargs):
            response = self.response(args)
            if 'list-units' in args:
                response.stdout = 'inventory-unreviewed-job.service loaded active running\n'
            return response
        with patch('app.services.numbering_environment.subprocess.run', side_effect=unknown):
            with self.assertRaisesRegex(MaintenanceError, '仍在运行'):
                self.context.drained()

    def test_ai_marker_blocks_without_reading_its_contents(self):
        Path(self.context.policy['ai_enable_markers'][0]).write_bytes(b'synthetic')
        with patch('app.services.numbering_environment.subprocess.run', side_effect=self.response):
            with self.assertRaisesRegex(MaintenanceError, 'AI 仍启用'):
                self.context.drained()

    def test_remaining_file_descriptor_and_uninspectable_process_fail(self):
        fdroot = self.context.proc_root / '123456/fd'
        fdroot.mkdir(parents=True)
        (fdroot / '4').write_bytes(b'fd placeholder, not actual image or AI data')
        with patch('app.services.numbering_environment.subprocess.run', side_effect=self.response):
            with patch('app.services.numbering_environment.os.readlink', return_value=str(self.context.data_root / 'inventory.db')):
                with self.assertRaisesRegex(MaintenanceError, '123456'):
                    self.context.drained()
            with patch('app.services.numbering_environment.os.readlink', side_effect=PermissionError):
                with self.assertRaisesRegex(MaintenanceError, '无法完整检查'):
                    self.context.drained()


if __name__ == '__main__':
    unittest.main()
