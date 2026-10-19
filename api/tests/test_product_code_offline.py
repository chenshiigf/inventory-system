"""Stdlib tests: all databases are generated SQL fixtures in fresh OS-temp dirs."""
import csv
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.services import product_code_offline as tool
from app.services.product_code_rehearsal import RehearsalError, rehearse


@contextmanager
def database(path):
    db = sqlite3.connect(path)
    try:
        with db:
            yield db
    finally:
        db.close()


def refresh_manifest(source, manifest):
    manifest.write_text(json.dumps({'database': source.name, 'size': source.stat().st_size,
                                   'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                                   'integrity_check': 'ok'}), encoding='utf8')


def synthetic_backup(root):
    root.mkdir()
    source = root / 'inventory.db'
    with database(source) as db:
        db.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE alembic_version(version_num TEXT PRIMARY KEY);
        INSERT INTO alembic_version VALUES ('d1e2f3a4b5c6');
        CREATE TABLE categories(id INTEGER PRIMARY KEY,name TEXT,parent_id INTEGER REFERENCES categories(id));
        INSERT INTO categories VALUES(1,'合成大类',NULL),(2,'合成小类',1);
        CREATE TABLE warehouses(id INTEGER PRIMARY KEY,name TEXT);
        INSERT INTO warehouses VALUES(1,'合成仓库一'),(2,'合成仓库二');
        CREATE TABLE products(id INTEGER PRIMARY KEY,product_code TEXT,original_product_code TEXT,
          category_id INTEGER REFERENCES categories(id),warehouse_id INTEGER REFERENCES warehouses(id),
          is_active INTEGER NOT NULL,image_path TEXT,thumbnail_path TEXT,size TEXT,unit TEXT,
          price NUMERIC,remark TEXT,created_at TEXT,updated_at TEXT);
        CREATE UNIQUE INDEX uq_products_product_code ON products(product_code) WHERE product_code IS NOT NULL;
        CREATE TABLE product_packagings(id INTEGER PRIMARY KEY AUTOINCREMENT,
          product_id INTEGER NOT NULL REFERENCES products(id),packing_qty INTEGER,
          carton_count INTEGER NOT NULL CHECK(carton_count>=0),sort_order INTEGER,created_at TEXT,updated_at TEXT);
        CREATE TABLE inventory_movements(id INTEGER PRIMARY KEY,product_id INTEGER REFERENCES products(id),
          product_packaging_id INTEGER REFERENCES product_packagings(id),warehouse_id INTEGER REFERENCES warehouses(id),
          movement_type TEXT,quantity INTEGER,before_carton_count INTEGER,after_carton_count INTEGER,
          packing_qty_snapshot INTEGER,unit_snapshot TEXT,remark TEXT,created_at TEXT);
        CREATE TABLE product_import_batches(id INTEGER PRIMARY KEY,file_name TEXT,file_hash TEXT UNIQUE,
          source_row_count INTEGER,product_count INTEGER,created_at TEXT);
        INSERT INTO product_import_batches VALUES(1,'synthetic.xlsx','abc123',7,7,'2026-10-01');
        CREATE TABLE product_code_months(period_ym TEXT PRIMARY KEY,next_sequence INTEGER CHECK(next_sequence>=1));
        INSERT INTO product_code_months VALUES('202611',500);
        """)
        for index, (pid, code) in enumerate(zip([2, 4, 6, 8, 10, 12, 100],
                                               ['250002', '250001', None, '', '异常旧货号', '261099', '2610100']), 1):
            db.execute('INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                       (pid, code, '000' + str(index), None if index == 3 else 2,
                        1 if index < 5 else 2, index != 2, 'uploads/synthetic.jpg', None,
                        str(index), 'pcs', 1.25, '合成商品', '2026-10-01', '2026-10-01'))
            db.execute("INSERT INTO product_packagings VALUES(?,?,24,?,0,'2026-10-01','2026-10-01')",
                       (index * 10, pid, index))
        db.execute("INSERT INTO inventory_movements VALUES(1,2,10,1,'IN',1,0,1,24,'pcs','合成流水','2026-10-01')")
    manifest = root / 'manifest.json'
    refresh_manifest(source, manifest)
    return source, manifest


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='inventory-offline-synthetic-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.manifest = synthetic_backup(self.root / 'source')
        self.work = self.root / 'work'

    def prepare(self):
        return tool.prepare_copy(self.source, self.manifest, self.work)

    def execute(self, plan):
        return tool.offline_plan(self.work, execute=True, confirm_offline_copy=True,
                                 expected_plan_sha256=plan['plan_sha256'])

    def change_source(self, sql):
        with database(self.source) as db:
            db.executescript(sql)
        refresh_manifest(self.source, self.manifest)

    def test_readonly_source_plan_evidence_and_csv(self):
        before = self.source.read_bytes()
        files = set(self.source.parent.iterdir())
        self.assertEqual(tool.inspect_backup(self.source, self.manifest)['source_revision'], tool.HEAD_REVISION)
        plan = self.prepare()
        copy = self.work / 'work.sqlite3'
        self.assertFalse(os.path.samefile(self.source, copy))
        self.assertEqual(self.source.stat().st_nlink, 1)
        self.assertEqual(copy.stat().st_nlink, 1)
        copy_before, work_files = copy.read_bytes(), set(self.work.iterdir())
        self.assertEqual(tool.offline_plan(self.work), plan)
        self.assertEqual(copy.read_bytes(), copy_before)
        self.assertEqual(set(self.work.iterdir()), work_files)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(set(self.source.parent.iterdir()), files)
        self.assertEqual(plan['product_ids'], [2, 4, 6, 8, 10, 12, 100])
        self.assertEqual(plan['total_cartons'], 28)
        self.assertEqual(plan['stock_by_warehouse'], {'1': 10, '2': 18})
        self.assertEqual([r['new_code'] for r in plan['mapping']], [f'25{n:04}' for n in range(1, 8)])
        self.assertFalse(plan['mapping'][1]['is_active'])
        self.assertEqual(plan['mapping'][2]['category'], '')
        report = tool.offline_plan(self.work, export_reports=True)
        with (Path(report['reports_directory']) / 'mapping.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]['original_product_code'], '0001')
        self.assertIn('图片不可用', rows[0]['image_availability'])
        self.assertEqual(rows[2]['old_code_anomaly'], '空货号')
        self.assertEqual(rows[4]['old_code_anomaly'], '格式异常')
        self.assertEqual(copy.read_bytes(), copy_before)

    def test_swaps_all_products_counters_and_duplicate_execution(self):
        before, plan = self.source.read_bytes(), self.prepare()
        self.assertEqual(self.execute(plan)['state'], 'committed')
        after = tool.offline_plan(self.work)
        for key in ['invariants_sha256', 'table_sha256', 'stock_by_product', 'stock_by_warehouse']:
            self.assertEqual(after[key], plan[key])
        self.assertEqual(after['expected_counters']['202610'], 101)
        self.assertEqual(after['expected_counters']['202611'], 500)
        self.assertEqual(self.source.read_bytes(), before)
        with self.assertRaisesRegex(RehearsalError, '已提交'):
            self.execute(after)

    def test_confirmation_and_digest_are_copy_bound(self):
        plan = self.prepare()
        for kwargs in ({'execute': True}, {'execute': True, 'confirm_offline_copy': True}):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(RehearsalError, '显式'):
                tool.offline_plan(self.work, **kwargs)
        other = self.root / 'other'
        other_plan = tool.prepare_copy(self.source, self.manifest, other)
        self.assertNotEqual(plan['plan_sha256'], other_plan['plan_sha256'])
        with self.assertRaisesRegex(RehearsalError, '摘要'):
            tool.offline_plan(other, execute=True, confirm_offline_copy=True,
                              expected_plan_sha256=plan['plan_sha256'])

    def test_changed_manifest_rejected(self):
        self.prepare()
        self.manifest.write_text(self.manifest.read_text() + '\n')
        with self.assertRaisesRegex(RehearsalError, '摘要已变化'):
            tool.offline_plan(self.work)

    def test_bad_checksum_revision_integrity_fk_and_wal(self):
        for kind in ['checksum', 'revision', 'integrity', 'foreign_key', 'wal']:
            with self.subTest(kind=kind):
                source, manifest = synthetic_backup(self.root / kind)
                if kind == 'checksum':
                    source.write_bytes(source.read_bytes() + b'changed')
                elif kind == 'integrity':
                    source.write_bytes(b'not sqlite')
                    refresh_manifest(source, manifest)
                elif kind == 'wal':
                    Path(str(source) + '-wal').touch()
                else:
                    with database(source) as db:
                        db.execute("UPDATE alembic_version SET version_num='unknown'" if kind == 'revision'
                                   else 'UPDATE product_packagings SET product_id=999')
                    refresh_manifest(source, manifest)
                with self.assertRaises((RehearsalError, sqlite3.DatabaseError)):
                    tool.inspect_backup(source, manifest)

    def test_full_manifest_supported(self):
        self.manifest.write_text(json.dumps({'database_filename': self.source.name,
                                           'database_size': self.source.stat().st_size,
                                           'database_sha256': hashlib.sha256(self.source.read_bytes()).hexdigest()}))
        self.assertEqual(tool.inspect_backup(self.source, self.manifest)['source_revision'], tool.HEAD_REVISION)

    def test_unsafe_paths_hardlinks_and_samefile(self):
        for value in [tool.REPO_ROOT / 'api/data/inventory.db', Path('/var/lib/inventory-system/inventory.db'),
                      Path('D:/inventory-local-data/inventory.db'), tool.REPO_ROOT / 'real-business-backup.db']:
            with self.subTest(value=value), self.assertRaises(RehearsalError):
                tool.safe_path(value)
        for work in [self.source.parent / 'work', self.source.parent]:
            with self.assertRaises(RehearsalError):
                tool.prepare_copy(self.source, self.manifest, work)
        hard = self.source.parent / 'hard.db'
        os.link(self.source, hard)
        with self.assertRaisesRegex(RehearsalError, '硬链接'):
            tool.inspect_backup(self.source, self.manifest)
        hard.unlink()
        self.prepare()
        copy = self.work / 'work.sqlite3'
        copy.unlink()
        os.link(self.source, copy)
        with self.assertRaisesRegex(RehearsalError, '硬链接'):
            tool.offline_plan(self.work)

    def test_symlink_parent_rejected(self):
        alias = self.root / 'alias'
        try:
            alias.symlink_to(self.source.parent, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not permit symlink creation')
        with self.assertRaisesRegex(RehearsalError, '链接'):
            tool.inspect_backup(alias / self.source.name, alias / self.manifest.name)

    @unittest.skipUnless(os.name == 'nt', 'Windows junction check')
    def test_windows_junction_parent_rejected(self):
        alias = self.root / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(self.source.parent)], capture_output=True)
        self.assertEqual(result.returncode, 0)
        try:
            with self.assertRaisesRegex(RehearsalError, 'reparse'):
                tool.inspect_backup(alias / self.source.name, alias / self.manifest.name)
        finally:
            alias.rmdir()

    def test_any_reparse_attribute_rejected(self):
        real = Path.lstat
        class Reparse:
            st_mode = 0
            st_file_attributes = 0x400
        with patch.object(Path, 'lstat', lambda p: Reparse() if p == self.source else real(p)):
            with self.assertRaisesRegex(RehearsalError, 'reparse'):
                tool.safe_path(self.source)

    def test_every_business_dimension_checked(self):
        changes = ["UPDATE products SET price=99 WHERE id=2",
                   "UPDATE products SET original_product_code='changed' WHERE id=2",
                   "UPDATE products SET image_path='changed' WHERE id=2",
                   "UPDATE products SET warehouse_id=2 WHERE id=2",
                   "UPDATE products SET id=200 WHERE id=100",
                   "UPDATE product_packagings SET carton_count=carton_count+1 WHERE id=10",
                   "UPDATE product_packagings SET packing_qty=48 WHERE id=10",
                   "UPDATE inventory_movements SET remark='changed'",
                   "UPDATE inventory_movements SET product_packaging_id=NULL",
                   "UPDATE product_import_batches SET file_hash='changed'",
                   "UPDATE product_code_months SET next_sequence=999",
                   "UPDATE products SET product_code='changed' WHERE id=2",
                   "CREATE INDEX unexpected ON products(size)"]
        for index, sql in enumerate(changes):
            with self.subTest(sql=sql):
                work = self.root / ('changed-' + str(index))
                plan = tool.prepare_copy(self.source, self.manifest, work)
                with database(work / 'work.sqlite3') as db:
                    db.execute(sql)
                with self.assertRaises(RehearsalError):
                    tool.offline_plan(work, execute=True, confirm_offline_copy=True,
                                      expected_plan_sha256=plan['plan_sha256'])

    def test_synthetic_identity_forgery_rejected(self):
        self.prepare()
        path = self.work / 'work.sqlite3'
        (self.work / 'numbering-rehearsal.json').write_text(json.dumps({
            'format': 'synthetic-numbering-rehearsal-v1', 'database': str(path),
            'token': 'forged', 'through_product_id': 100}))
        with database(path) as db:
            db.execute('CREATE TABLE numbering_rehearsal_guard(token TEXT,executed INTEGER)')
            db.execute("INSERT INTO numbering_rehearsal_guard VALUES('forged',0)")
        with self.assertRaisesRegex(RehearsalError, '合成'):
            rehearse(path)
        (self.work / tool.MARKER).unlink()
        with self.assertRaisesRegex(RehearsalError, '伪造合成'):
            rehearse(path)

    def test_transaction_rollback_on_final_code_failure(self):
        self.change_source("CREATE TRIGGER fail BEFORE UPDATE OF product_code ON products "
                           "WHEN new.product_code='250004' BEGIN SELECT RAISE(ABORT,'test failure'); END;")
        plan = self.prepare()
        with self.assertRaises(sqlite3.IntegrityError):
            self.execute(plan)
        self.assertEqual(tool.offline_plan(self.work), plan)

    def test_trigger_noncode_or_counter_change_rolls_back(self):
        for index, sql in enumerate(["UPDATE products SET remark='changed' WHERE id=2;",
                                      "UPDATE product_code_months SET next_sequence=999;"]):
            with self.subTest(sql=sql):
                source, manifest = synthetic_backup(self.root / ('trigger-' + str(index)))
                with database(source) as db:
                    db.executescript("CREATE TRIGGER alter_data AFTER UPDATE OF product_code ON products "
                                     "WHEN new.product_code='250001' BEGIN " + sql + " END;")
                refresh_manifest(source, manifest)
                work = self.root / ('trigger-work-' + str(index))
                plan = tool.prepare_copy(source, manifest, work)
                with self.assertRaises(RehearsalError):
                    tool.offline_plan(work, execute=True, confirm_offline_copy=True,
                                      expected_plan_sha256=plan['plan_sha256'])
                self.assertEqual(tool.offline_plan(work), plan)

    def test_process_interruption_recovers_and_retries(self):
        plan = self.prepare()
        code = """
import os,sys
from pathlib import Path
from app.services import product_code_offline as t
real=t._open
def open_db(*a,**kw):
    db=real(*a,**kw)
    if kw.get('write'):
        def trace(sql):
            if sql.startswith('UPDATE products') and "'250004'" in sql:
                os._exit(91)
        db.set_trace_callback(trace)
    return db
t._open=open_db
t.offline_plan(Path(sys.argv[1]),execute=True,confirm_offline_copy=True,expected_plan_sha256=sys.argv[2])
"""
        process = subprocess.run([sys.executable, '-c', code, str(self.work), plan['plan_sha256']],
                                 cwd=tool.API_ROOT, capture_output=True)
        self.assertEqual(process.returncode, 91, process.stderr)
        with self.assertRaisesRegex(RehearsalError, 'journal'):
            tool.offline_plan(self.work)
        # SQLite verifies the rolled-back rows on this synthetic copy. A leftover
        # non-hot journal is still refused by the tool: restart from the backup.
        with database(self.work / 'work.sqlite3') as db:
            self.assertEqual(dict(db.execute('SELECT id,product_code FROM products')),
                             {r['product_id']: r['old_code'] for r in plan['mapping']})
            self.assertEqual(db.execute('SELECT state FROM offline_numbering_run').fetchone()[0], 'prepared')
        self.work = self.root / 'restart-work'
        restarted = self.prepare()
        self.assertEqual(restarted['mapping'], plan['mapping'])
        self.assertEqual(self.execute(restarted)['state'], 'committed')

    def test_postcommit_report_failure_detected_and_reexported(self):
        plan, real, calls = self.prepare(), tool._export, []
        def export(*args):
            calls.append(1)
            if len(calls) == 2:
                raise OSError('synthetic disk full after commit')
            return real(*args)
        with patch.object(tool, '_export', export):
            result = self.execute(plan)
        self.assertEqual(result['state'], 'committed_report_failed')
        status = tool.offline_plan(self.work, export_reports=True)
        self.assertEqual(status['state'], 'committed')
        self.assertTrue(Path(status['reports_directory'], 'mapping.csv').exists())
        self.assertEqual(status['committed_result']['approved_plan_sha256'], plan['plan_sha256'])
        with self.assertRaisesRegex(RehearsalError, '已提交'):
            self.execute(plan)

    def test_precommit_report_failure_writes_no_codes(self):
        plan = self.prepare()
        before = (self.work / 'work.sqlite3').read_bytes()
        with patch.object(tool, '_export', side_effect=OSError('synthetic report failure')):
            with self.assertRaises(OSError):
                self.execute(plan)
        self.assertEqual((self.work / 'work.sqlite3').read_bytes(), before)

    def test_duplicate_old_codes_and_invalid_counter_stop_before_copy_identity(self):
        for index, sql in enumerate([
                "DROP INDEX uq_products_product_code; UPDATE products SET product_code='duplicate';",
                "UPDATE product_code_months SET period_ym='202613';",
                "UPDATE products SET product_code='26109999999999999999999' WHERE id=2;"]):
            with self.subTest(sql=sql):
                source, manifest = synthetic_backup(self.root / ('bad-' + str(index)))
                with database(source) as db:
                    db.executescript(sql)
                refresh_manifest(source, manifest)
                before = source.read_bytes()
                with self.assertRaises(RehearsalError):
                    tool.prepare_copy(source, manifest, self.root / ('bad-work-' + str(index)))
                self.assertEqual(source.read_bytes(), before)

    def test_blob_and_text_noncode_fields_are_not_equal(self):
        self.change_source("UPDATE products SET remark=x'6162' WHERE id=2;")
        self.prepare()
        with database(self.work / 'work.sqlite3') as db:
            db.execute("UPDATE products SET remark='6162' WHERE id=2")
        with self.assertRaisesRegex(RehearsalError, '非编号'):
            tool.offline_plan(self.work)

    def test_empty_warehouse_and_unassigned_stock_are_explicit(self):
        self.change_source("INSERT INTO warehouses VALUES(3,'空的合成仓库'); "
                           "UPDATE products SET warehouse_id=NULL WHERE id=100;")
        self.assertEqual(self.prepare()['stock_by_warehouse'], {'1': 10, '2': 11, '3': 0, 'None': 7})

    def test_movement_packaging_must_belong_to_product(self):
        self.change_source('UPDATE inventory_movements SET product_packaging_id=20;')
        with self.assertRaisesRegex(RehearsalError, '不属于'):
            self.prepare()

    def test_marker_hardlink_and_existing_directory_rejected(self):
        self.prepare()
        marker = self.work / tool.MARKER
        os.link(marker, self.root / 'marker-alias.json')
        with self.assertRaisesRegex(RehearsalError, '硬链接'):
            tool.offline_plan(self.work)
        with self.assertRaisesRegex(RehearsalError, '全新'):
            self.prepare()

    def test_synthetic_2523_products_2573_packagings_187_movements(self):
        with database(self.source) as db:
            for pid in range(101, 2617):
                db.execute("INSERT INTO products SELECT ?,?,original_product_code,category_id,warehouse_id,"
                           "is_active,image_path,thumbnail_path,size,unit,price,remark,created_at,updated_at "
                           "FROM products WHERE id=2", (pid, f'OLD-{pid}'))
                db.execute("INSERT INTO product_packagings(product_id,packing_qty,carton_count,sort_order) "
                           "VALUES(?,24,3,0)", (pid,))
            for pid in range(101, 151):
                db.execute("INSERT INTO product_packagings(product_id,packing_qty,carton_count,sort_order) "
                           "VALUES(?,48,2,1)", (pid,))
            for mid in range(2, 188):
                db.execute("INSERT INTO inventory_movements SELECT ?,product_id,product_packaging_id,warehouse_id,"
                           "movement_type,quantity,before_carton_count,after_carton_count,packing_qty_snapshot,"
                           "unit_snapshot,remark,created_at FROM inventory_movements WHERE id=1", (mid,))
        refresh_manifest(self.source, self.manifest)
        plan = self.prepare()
        self.assertEqual(plan['counts']['products'], 2523)
        self.assertEqual(plan['counts']['product_packagings'], 2573)
        self.assertEqual(plan['counts']['inventory_movements'], 187)
        self.assertEqual(plan['total_cartons'], 7676)
        self.assertEqual(plan['mapping'][-1]['new_code'], '252523')
        self.execute(plan)
        after = tool.offline_plan(self.work)
        self.assertEqual(after['table_sha256'], plan['table_sha256'])
        self.assertEqual(after['product_ids'], plan['product_ids'])
        self.assertEqual(after['stock_by_product'], plan['stock_by_product'])
        self.assertEqual(after['stock_by_warehouse'], plan['stock_by_warehouse'])

    def test_cli_requires_source_and_prepare_does_not_renumber(self):
        script = tool.API_ROOT / 'scripts/rehearse_product_codes.py'
        def call(*args):
            return subprocess.run([sys.executable, str(script), *map(str, args)], cwd=tool.API_ROOT,
                                  capture_output=True, encoding='utf8')
        self.assertNotEqual(call('offline-prepare').returncode, 0)
        prepare = call('offline-prepare', '--source-backup', self.source, '--source-manifest', self.manifest,
                       '--work-directory', self.work)
        self.assertEqual(prepare.returncode, 0, prepare.stderr)
        self.assertEqual(json.loads(prepare.stdout)['state'], 'prepared')
        readonly = call('offline-plan', '--work-directory', self.work)
        self.assertEqual(readonly.returncode, 0, readonly.stderr)
        with database(self.work / 'work.sqlite3') as db:
            self.assertEqual(db.execute('SELECT product_code FROM products WHERE id=2').fetchone()[0], '250002')

    @unittest.skipUnless(importlib.util.find_spec('sqlalchemy') and importlib.util.find_spec('alembic.config'),
                         'Alembic/SQLAlchemy unavailable; no installation')
    def test_normal_alembic_old_revision_upgrade_copy_only(self):
        for revision in ['b2c3d4e5f6a7', 'c8d9e0f1a2b3']:
            with self.subTest(revision=revision):
                root = self.root / revision
                root.mkdir()
                source = root / 'inventory.db'
                env = os.environ.copy()
                env.update(INVENTORY_DATA_DIR=str(root / 'runtime'), DATABASE_URL=f'sqlite:///{source.as_posix()}',
                           DASHSCOPE_API_KEY='', PYTHONUTF8='1')
                code = ("import sys; from alembic.config import Config; from alembic import command; "
                        "c=Config(); c.set_main_option('script_location',sys.argv[1]); "
                        "c.set_main_option('sqlalchemy.url',sys.argv[2]); command.upgrade(c,sys.argv[3])")
                result = subprocess.run([sys.executable, '-c', code, str(tool.API_ROOT / 'alembic'),
                                         env['DATABASE_URL'], revision], cwd=tool.API_ROOT, env=env,
                                        capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                with database(source) as db:
                    db.execute("INSERT INTO products(id,product_code,is_active,size,created_at,updated_at) "
                               "VALUES(20,'01-01-001',1,'synthetic','2026-10-01','2026-10-01')")
                manifest = root / 'manifest.json'
                refresh_manifest(source, manifest)
                before = source.read_bytes()
                with self.assertRaisesRegex(RehearsalError, '--upgrade-copy'):
                    tool.prepare_copy(source, manifest, self.root / (revision + '-refuse'))
                plan = tool.prepare_copy(source, manifest, self.root / (revision + '-work'), upgrade_copy=True)
                self.assertEqual(plan['revision'], tool.HEAD_REVISION)
                self.assertEqual(plan['mapping'][0]['old_code'], '01-01-001')
                self.assertEqual(source.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
