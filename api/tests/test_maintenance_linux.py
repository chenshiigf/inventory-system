"""Root-only Unix control tests on private synthetic data; never invokes systemd."""
from contextlib import closing, nullcontext
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import socket
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.visual_search.config import VisualConfig
from app.services.visual_search.images import image_hash
from app.services.visual_search.lock import CallLock
from app.services.visual_search.runtime_control import DailyBudgetLedger

pytestmark = pytest.mark.skipif(sys.platform != 'linux' or getattr(os, 'geteuid', lambda: -1)() != 0,
                                reason='requires isolated Linux root runner')
API = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def no_external_connections(monkeypatch):
    original = socket.socket.connect
    def connect(sock, address):
        if sock.family == socket.AF_UNIX or isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return original(sock, address)
        raise AssertionError('external connection forbidden')
    monkeypatch.setattr(socket.socket, 'connect', connect)


@pytest.fixture
def environment(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    visual = root / 'data' / 'visual-search'
    catalog = visual / 'production-catalog'
    catalog.mkdir(parents=True)
    for directory in (visual, catalog):
        lock = CallLock(directory)
        assert lock.acquire()
        lock.release()
    config = VisualConfig(api_key='synthetic-not-a-credential', dimension=256, interval=.1,
                          daily_budget_cny=Decimal('10'), call_reserve_cny=Decimal('.01'))
    ledger = DailyBudgetLedger(visual / 'daily-budget.sqlite3', config)
    with closing(ledger._connect()):
        pass
    business = root / 'data' / 'synthetic-business.db'
    with closing(sqlite3.connect(business)) as db, db:
        db.executescript('''CREATE TABLE products(id INTEGER PRIMARY KEY,code TEXT);
          CREATE TABLE stock(product_id INTEGER,packaging INTEGER,warehouse INTEGER,boxes INTEGER);
          CREATE TABLE movements(id INTEGER PRIMARY KEY,product_id INTEGER,boxes INTEGER);
          INSERT INTO products VALUES(1,'original'); INSERT INTO stock VALUES(1,24,2,100);
          INSERT INTO movements VALUES(1,1,100);''')
    before = hashlib.sha256(business.read_bytes()).hexdigest()
    application = create_app(data_directory=root / 'data', visual_config=config,
                             visual_provider=SimpleNamespace(paid_calls=False),
                             maintenance_socket=root / 'control.sock')
    with TestClient(application) as client:
        yield application, client, root, ledger
    assert hashlib.sha256(business.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(business)) as db:
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert db.execute('SELECT * FROM products').fetchall() == [(1, 'original')]
        assert db.execute('SELECT * FROM stock').fetchall() == [(1, 24, 2, 100)]
        assert db.execute('SELECT * FROM movements').fetchall() == [(1, 1, 100)]
    assert not (root / 'control.sock').exists()


def control(root, command, *, pid=None, seconds=90):
    result = subprocess.run([sys.executable, '-B', str(API / 'scripts/maintenance_control.py'),
                             command, '--socket', str(root / 'control.sock'),
                             '--pid', str(pid or os.getpid()), '--timeout-seconds', str(seconds)],
                            capture_output=True, text=True, timeout=6,
                            env={'PATH': os.environ['PATH'], 'LD_LIBRARY_PATH': os.environ.get('LD_LIBRARY_PATH', '')})
    reply = json.loads(result.stdout)
    assert result.returncode == (0 if reply['state'] in {'SAFE_TO_STOP', 'RUNNING'} else 2), result.stderr
    return reply


def eventually_safe(root):
    end = time.monotonic() + 5
    while time.monotonic() < end:
        reply = control(root, 'status')
        if reply['state'] == 'SAFE_TO_STOP':
            return reply
        time.sleep(.02)
    raise AssertionError(reply)


def guarded_stop(root):
    # Execute the exact documented wrapper, replacing only systemd with a recorder.
    text = (API.parent / 'docs/maintenance-api-release-candidate.md').read_text(encoding='utf8')
    body = 'c2_stop_api() {' + text.split('c2_stop_api() {', 1)[1].split('\n}', 1)[0] + '\n}'
    env = {'PATH': os.environ['PATH'], 'LD_LIBRARY_PATH': os.environ.get('LD_LIBRARY_PATH', ''),
           'PY': sys.executable, 'CURRENT_API': str(API), 'MAINTENANCE_SOCKET': str(root / 'control.sock'),
           'EVIDENCE': str(root), 'EXPECTED_PID': str(os.getpid())}
    script = '''c2_require_stop_policy() { return 0; }
systemctl() {
  if [ "$1" = show ]; then printf '%s\\n' "$EXPECTED_PID";
  else printf '%s\\n' "$*" >> "$EVIDENCE/stop-called"; fi
}
''' + body + '\nc2_stop_api\n'
    return subprocess.run(['bash', '-c', script], env=env, timeout=8, capture_output=True, text=True)


def test_linux_idle_private_control_and_resume(environment):
    app, client, root, ledger = environment
    ledger.settle(ledger.reserve(), {}, known=False)
    with closing(sqlite3.connect(ledger.path)) as db:
        assert db.execute('SELECT state FROM call_reservations').fetchall() == [('unknown',)]
    assert client.get('/docs').status_code == 200
    assert client.get('/maintenance/status').status_code == 404
    assert stat.S_IMODE((root / 'control.sock').stat().st_mode) == 0o600
    assert control(root, 'status', pid=os.getpid() + 100000)['state'] == 'BLOCKED'
    before = ledger.path.read_bytes()
    assert control(root, 'begin')['state'] == 'SAFE_TO_STOP'
    assert client.post('/api/products', json={}).status_code == 503
    assert client.post('/api/visual-search/search', content=b'not parsed').status_code == 503
    assert ledger.path.read_bytes() == before
    assert guarded_stop(root).returncode == 0
    assert (root / 'stop-called').read_text().strip() == 'stop inventory-system-api.service'
    assert control(root, 'resume')['state'] == 'RUNNING'
    assert client.get('/docs').status_code == 200
    print('C2_DRAIN_RESULT idle=SAFE_TO_STOP private_socket=0600 new_http=503 resume=200 business_sha=unchanged')


def test_linux_preview_cancellation_waits_for_finally(environment, monkeypatch):
    from app.services.product_import import preview_tasks
    from app.services.product_import.session_store import SOURCE_FILE_NAME
    from sqlalchemy import create_engine
    app, client, root, _ = environment
    registry = app.state.product_import_preview_tasks
    entered, release = threading.Event(), threading.Event()
    def reader(_data, *, checkpoint):
        entered.set()
        release.wait()
        checkpoint()
    monkeypatch.setattr(preview_tasks, 'read_import_workbook', reader)
    directory = registry.sessions.root / ('b' * 32)
    directory.mkdir()
    (directory / SOURCE_FILE_NAME).write_bytes(b'SYNTHETIC')
    engine = create_engine('sqlite://')
    try:
        registry.acquire()
        registry.start(task_id='b' * 32, directory=directory, engine=engine,
                       file_name='synthetic.xlsx', already_imported=False)
        assert entered.wait(3)
        assert control(root, 'begin')['state'] == 'BLOCKED'
        assert client.post('/api/products', json={}).status_code == 503
        registry.cancel('b' * 32)
        assert control(root, 'status')['state'] == 'BLOCKED'
        release.set()
        result = eventually_safe(root)
        assert registry.get('b' * 32).status == 'cancelled'
        assert not any(result['counts'].values()) and not any(result['checks'].values())
        assert control(root, 'resume')['state'] == 'RUNNING'
    finally:
        release.set()
        registry._executor.shutdown(wait=True)
        engine.dispose()
    print('C2_DRAIN_RESULT preview=BLOCKED_until_cooperative_completion then=SAFE_TO_STOP business_sha=unchanged')


@pytest.mark.parametrize('hold_seconds', [.1, 90.2], ids=['busy_index', 'over_90_seconds'])
def test_linux_real_index_settlement_and_expired_drain_never_stop(environment, hold_seconds):
    app, client, root, ledger = environment
    gate, service = app.state.maintenance, app.state.visual_search
    entered, release = threading.Event(), threading.Event()
    raw_image = BytesIO()
    Image.new('RGB', (8, 8), 'white').save(raw_image, format='PNG')
    raw = raw_image.getvalue()
    class Source:
        production = False
        def context(self): return {}
        def bind(self, _db): return None
        def session(self, _bind): return nullcontext(None)
        def get(self, _db, _pid): return SimpleNamespace(id=1, is_active=True, image_path='synthetic.png')
        def raw(self, _product): return raw
    class Provider:
        paid_calls = False
        def embed(self, *_args):
            reservation = ledger.reserve()
            entered.set()
            release.wait()
            ledger.settle(reservation, {'input_tokens': 0, 'image_tokens': 0, 'total_tokens': 0}, known=True)
            return SimpleNamespace(vector=np.ones(256, dtype=np.float32))
    service.source, service.provider = Source(), Provider()
    with closing(sqlite3.connect(service.store.path)) as db, db:
        db.executescript('''CREATE TABLE alembic_version(version_num TEXT); INSERT INTO alembic_version VALUES('vs01');
          CREATE TABLE visual_jobs(id TEXT PRIMARY KEY,index_key TEXT,owner TEXT,status TEXT,max_items INTEGER,
          processed INTEGER DEFAULT 0,succeeded INTEGER DEFAULT 0,failed INTEGER DEFAULT 0,started_at TEXT,finished_at TEXT,error TEXT);
          CREATE TABLE visual_embeddings(product_id INTEGER,index_key TEXT,image_identifier TEXT,image_hash TEXT,
          embedding_model TEXT,embedding_dimension INTEGER,embedding_version TEXT,preprocessing_version TEXT,
          embedding_vector BLOB,indexed_at TEXT,indexing_status TEXT,error TEXT);''')
    plan = {'obsolete': [], 'skipped': [], 'invalid_images': [], 'unchanged': [],
            'pending': [{'product_id': 1, 'identifier': 'synthetic.png', 'digest': image_hash(raw)}]}
    service.plan = lambda _db: plan
    try:
        service.rebuild(None, 'synthetic', max_items=1, dry_run=False, confirm_paid=True)
        assert entered.wait(3)
        result = control(root, 'begin')
        assert result['state'] == 'BLOCKED' and result['counts']['futures'] == 1
        assert client.post('/api/products', json={}).status_code == 503
        time.sleep(hold_seconds)
        result = control(root, 'status')
        assert guarded_stop(root).returncode == 62
        assert not (root / 'stop-called').exists()
        assert result['state'] == 'BLOCKED'
        if hold_seconds > 90:
            assert result['reason'] == 'drain_deadline_expired'
        release.set()
        service.executor.shutdown(wait=True)
        with closing(sqlite3.connect(service.store.path)) as db:
            assert db.execute('SELECT status,succeeded FROM visual_jobs').fetchall() == [('completed', 1)]
            assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        with closing(sqlite3.connect(ledger.path)) as db:
            assert db.execute('SELECT state FROM call_reservations').fetchall() == [('settled',)]
            assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        if hold_seconds > 90:
            assert control(root, 'status')['state'] == 'BLOCKED'
        else:
            eventually_safe(root)
        assert control(root, 'resume')['state'] == 'RUNNING'
        assert client.get('/docs').status_code == 200
    finally:
        release.set()
        service.executor.shutdown(wait=True)
    print(f'C2_DRAIN_RESULT index_hold={hold_seconds}s stop_commands=0 settlement=settled business_sha=unchanged API=running')


def test_linux_inventory_readonly_catalog_lock_permissions():
    import asyncio
    import multiprocessing
    import tempfile
    from app.maintenance import DrainGate
    # Dedicated synthetic UID/GID; no system accounts or production permissions changed.
    uid, gid = 23455, 23456
    with tempfile.TemporaryDirectory(prefix='c2-lock-permissions-') as directory:
        root = Path(directory)
        catalog = root / 'production-catalog'
        catalog.mkdir()
        for path in (root, catalog):
            os.chown(path, 0, gid)
            path.chmod(0o750)
            (path / 'calls.lock').write_bytes(b'0')
            os.chown(path / 'calls.lock', 0, gid)
            (path / 'calls.lock').chmod(0o640)
        ledger = DailyBudgetLedger(root / 'daily-budget.sqlite3', VisualConfig())
        with closing(ledger._connect()):
            pass
        os.chown(ledger.path, 0, gid)
        ledger.path.chmod(0o640)
        before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
        parent, child = multiprocessing.get_context('fork').Pipe()
        def inspect():
            contender.file.close()  # close inherited FD without unlocking the parent's lease
            contender.file = None
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            async def run():
                for _ in range(2):
                    child.recv()
                    gate = DrainGate(root, catalog)
                    gate.observers.append(lambda: {'preview_tasks': 0})
                    child.send(await gate.command({'command': 'begin'}))
                    child.recv()
                    await gate.close()
                    child.send('released')
            asyncio.run(run())
        contender = CallLock(catalog)
        assert contender.acquire()
        process = multiprocessing.get_context('fork').Process(target=inspect)
        process.start()
        try:
            parent.send('check')
            assert parent.poll(5)
            assert parent.recv()['state'] == 'BLOCKED'
            parent.send('close')
            assert parent.poll(5) and parent.recv() == 'released'
            contender.release()
            parent.send('check')
            assert parent.poll(5)
            assert parent.recv()['state'] == 'SAFE_TO_STOP'
            assert not contender.acquire()
            parent.send('close')
            assert parent.poll(5) and parent.recv() == 'released'
            process.join(5)
            assert process.exitcode == 0
            assert contender.acquire()
            contender.release()
            assert before == {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
        finally:
            contender.release()
            parent.close()
            child.close()
        print('C2_DRAIN_RESULT uid=23455 gid=23456 root_owned_640_lock=PASS bidirectional_flock=PASS bytes=unchanged')
