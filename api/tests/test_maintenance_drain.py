"""Synthetic data only; no signals, migrations, or service commands."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
import hashlib
import sqlite3
import threading
import time

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.maintenance import DrainGate, MaintenanceMiddleware, MaintenanceRejected
from app.services.visual_search.config import VisualConfig
from app.services.visual_search.lock import CallLock
from app.services.visual_search.runtime_control import DailyBudgetLedger


@pytest.fixture
def setup(tmp_path):
    visual = tmp_path / 'visual-search'
    catalog = visual / 'production-catalog'
    catalog.mkdir(parents=True)
    for root in (visual, catalog):
        lock = CallLock(root)
        assert lock.acquire()
        lock.release()
    budget = DailyBudgetLedger(visual / 'daily-budget.sqlite3', VisualConfig(
        daily_budget_cny=Decimal('10'), call_reserve_cny=Decimal('.01')))
    with closing(budget._connect()):
        pass  # creation is confined to this synthetic fixture
    business = tmp_path / 'business.sqlite'
    with closing(sqlite3.connect(business)) as db, db:
        db.executescript('CREATE TABLE stock(id INTEGER PRIMARY KEY,boxes INTEGER); INSERT INTO stock VALUES(1,100);')
    gate = DrainGate(visual, catalog)
    gate.observers.append(lambda: {'preview_tasks': 0})
    yield gate, budget, business
    asyncio.run(gate.close())


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_idle_safe_holds_existing_locks_and_reads_without_changes(setup):
    gate, budget, business = setup
    before = checksum(business), checksum(budget.path)
    async def run():
        assert (await gate.command({'command': 'status'}))['state'] == 'BLOCKED'
        assert (await gate.command({'command': 'begin'}))['state'] == 'SAFE_TO_STOP'
        contender = CallLock(gate.visual_root)
        assert not contender.acquire()
        assert (await gate.command({'command': 'resume'}))['state'] == 'RUNNING'
        assert contender.acquire()
        contender.release()
    asyncio.run(run())
    assert before == (checksum(business), checksum(budget.path))


def test_all_new_http_rejected_before_body_or_business_write(setup):
    gate, budget, business = setup
    writes = []
    async def write(request):
        writes.append(await request.body())
        return JSONResponse({'ok': True})
    app = MaintenanceMiddleware(Starlette(routes=[Route('/write', write, methods=['POST'])]), gate)
    before = checksum(business), checksum(budget.path)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://synthetic') as client:
            assert (await client.post('/write', content=b'normal')).status_code == 200
            assert (await gate.command({'command': 'begin'}))['state'] == 'SAFE_TO_STOP'
            assert (await client.post('/write', content=b'must not enter')).status_code == 503
            assert (await client.get('/maintenance/status')).status_code == 503
    asyncio.run(run())
    assert writes == [b'normal']
    assert before == (checksum(business), checksum(budget.path))


def test_admitted_http_can_finish_and_enqueue_after_begin(setup):
    gate, _, business = setup
    started, proceed = asyncio.Event(), asyncio.Event()
    release = threading.Event()
    pool = ThreadPoolExecutor(max_workers=1)
    async def write(request):
        started.set()
        await proceed.wait()
        gate.submit(pool, release.wait)
        with closing(sqlite3.connect(business)) as db, db:
            db.execute('UPDATE stock SET boxes=99')
        return JSONResponse({'ok': True})
    app = MaintenanceMiddleware(Starlette(routes=[Route('/write', write, methods=['POST'])]), gate)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://synthetic') as client:
            request = asyncio.create_task(client.post('/write'))
            await started.wait()
            assert (await gate.command({'command': 'begin'}))['reason'] == 'work_in_flight'
            assert (await client.post('/write')).status_code == 503
            proceed.set()
            assert (await request).status_code == 200
            assert (await gate.command({'command': 'status'}))['state'] == 'BLOCKED'
            release.set()
            pool.shutdown(wait=True)
            assert (await gate.command({'command': 'status'}))['state'] == 'SAFE_TO_STOP'
    try:
        asyncio.run(run())
    finally:
        release.set()
        pool.shutdown(wait=True)
    with closing(sqlite3.connect(business)) as db:
        assert db.execute('SELECT boxes FROM stock').fetchall() == [(99,)]
        assert db.execute('PRAGMA integrity_check').fetchone() == ('ok',)


def test_real_future_queue_and_completion_are_tracked(setup):
    gate, _, business = setup
    before = checksum(business)
    release, entered = threading.Event(), threading.Event()
    def job():
        entered.set()
        release.wait()
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        gate.submit(pool, job)
        assert entered.wait(2)
        gate.submit(pool, lambda: None)
        counts = gate.counts()
        assert counts['queued'] == 1 and counts['running'] == 1 and counts['futures'] == 2
        assert asyncio.run(gate.command({'command': 'begin'}))['state'] == 'BLOCKED'
        with pytest.raises(MaintenanceRejected):
            gate.submit(pool, lambda: None)
        release.set()
        pool.shutdown(wait=True)
        assert asyncio.run(gate.command({'command': 'status'}))['state'] == 'SAFE_TO_STOP'
    finally:
        release.set()
        pool.shutdown(wait=True)
    assert checksum(business) == before


def test_reservation_must_settle_and_status_never_changes_ledger(setup):
    gate, budget, business = setup
    before = checksum(business)
    reservation = budget.reserve()
    ledger = checksum(budget.path)
    result = asyncio.run(gate.command({'command': 'begin'}))
    assert result['state'] == 'BLOCKED' and result['checks']['reserved_calls'] == 1
    assert checksum(budget.path) == ledger
    budget.settle(reservation, {'input_tokens': 0, 'image_tokens': 0, 'total_tokens': 0}, known=True)
    ledger = checksum(budget.path)
    assert asyncio.run(gate.command({'command': 'status'}))['state'] == 'SAFE_TO_STOP'
    assert checksum(budget.path) == ledger and checksum(business) == before


def test_http_abort_is_unknown_even_after_count_reaches_zero(setup):
    gate, _, _ = setup
    entered = asyncio.Event()
    async def wait(request):
        entered.set()
        await asyncio.Event().wait()
    app = MaintenanceMiddleware(Starlette(routes=[Route('/wait', wait)]), gate)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://synthetic') as client:
            task = asyncio.create_task(client.get('/wait'))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert gate.counts()['http'] == 0
            assert (await gate.command({'command': 'begin'}))['reason'] == 'http_aborted_or_failed'
            await gate.command({'command': 'resume'})
            assert (await gate.command({'command': 'begin'}))['state'] == 'BLOCKED'
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['missing', 'corrupt', 'journal', 'unknown_cost', 'running_index'])
def test_unknown_or_unfinished_state_is_blocked_without_repair(setup, failure):
    gate, budget, business = setup
    if failure == 'missing':
        budget.path.unlink()
    elif failure == 'corrupt':
        budget.path.write_bytes(b'SYNTHETIC CORRUPTION')
    elif failure == 'journal':
        budget.path.with_name(budget.path.name + '-journal').write_bytes(b'SYNTHETIC LOG')
    elif failure == 'unknown_cost':
        budget.settle(budget.reserve(), {}, known=False)
    else:
        with closing(sqlite3.connect(gate.visual_root / 'index.db')) as db, db:
            db.executescript("CREATE TABLE visual_jobs(status TEXT); INSERT INTO visual_jobs VALUES('running');")
    before = {p: checksum(p) for p in gate.visual_root.rglob('*') if p.is_file() and p.name != 'calls.lock'}
    result = asyncio.run(gate.command({'command': 'begin'}))
    assert result['state'] == 'BLOCKED'
    assert before == {p: checksum(p) for p in gate.visual_root.rglob('*') if p.is_file() and p.name != 'calls.lock'}


def test_deadline_cannot_extend_and_does_not_stop_or_finish_work(setup):
    gate, _, _ = setup
    pool, release = ThreadPoolExecutor(max_workers=1), threading.Event()
    try:
        future = gate.submit(pool, release.wait)
        asyncio.run(gate.command({'command': 'begin', 'timeout_seconds': .02}))
        deadline = gate.deadline
        time.sleep(.04)
        result = asyncio.run(gate.command({'command': 'begin', 'timeout_seconds': 90}))
        assert result['reason'] == 'drain_deadline_expired' and gate.deadline == deadline
        assert not future.done()
        release.set()
        pool.shutdown(wait=True)
        assert asyncio.run(gate.command({'command': 'status'}))['state'] == 'BLOCKED'
        assert (asyncio.run(gate.command({'command': 'resume'})))['state'] == 'RUNNING'
    finally:
        release.set()
        pool.shutdown(wait=True)


def test_preview_terminal_response_does_not_hide_finally_work(setup, tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from app.services.product_import import preview_tasks
    from app.services.product_import.session_store import PreviewSessionLifecycle, SOURCE_FILE_NAME
    gate, _, business = setup
    sessions = PreviewSessionLifecycle(tmp_path / 'previews')
    sessions.root.mkdir()
    registry = preview_tasks.PreviewTaskRegistry(sessions, maintenance=gate)
    gate.observers = [registry.drain_state]
    entered, finish_read, in_finally, finish_finally = (threading.Event() for _ in range(4))
    original_release = sessions.release
    def release(path):
        in_finally.set()
        finish_finally.wait()
        original_release(path)
    def read(_data, *, checkpoint):
        entered.set()
        finish_read.wait()
        checkpoint()
    monkeypatch.setattr(sessions, 'release', release)
    monkeypatch.setattr(preview_tasks, 'read_import_workbook', read)
    directory = sessions.root / ('a' * 32)
    directory.mkdir()
    (directory / SOURCE_FILE_NAME).write_bytes(b'SYNTHETIC')
    engine = create_engine('sqlite://')
    before = checksum(business)
    try:
        registry.acquire()
        registry.start(task_id='a' * 32, directory=directory, engine=engine, file_name='synthetic.xlsx', already_imported=False)
        assert entered.wait(2)
        assert asyncio.run(gate.command({'command': 'begin'}))['state'] == 'BLOCKED'
        registry.cancel('a' * 32)
        finish_read.set()
        assert in_finally.wait(2)
        assert registry.get('a' * 32).status == 'cancelled'
        assert gate.counts()['futures'] == 1
        assert asyncio.run(gate.command({'command': 'status'}))['state'] == 'BLOCKED'
        finish_finally.set()
        registry.close()
        assert asyncio.run(gate.command({'command': 'status'}))['state'] == 'SAFE_TO_STOP'
    finally:
        finish_read.set()
        finish_finally.set()
        registry.close()
        engine.dispose()
    assert checksum(business) == before


def test_audit_timeout_is_blocked_and_keeps_event_loop_running(setup, monkeypatch):
    gate, _, _ = setup
    release = threading.Event()
    monkeypatch.setattr(gate, '_audit', lambda: release.wait())
    async def run():
        try:
            result = await gate.command({'command': 'begin'})
            assert result['state'] == 'BLOCKED' and result['reason'] == 'audit_TimeoutError'
            assert (await gate.command({'command': 'resume'}))['reason'] == 'audit_in_progress'
            await asyncio.sleep(.01)
        finally:
            release.set()
    asyncio.run(run())


def test_finished_request_context_cannot_admit_detached_work(setup):
    from contextvars import copy_context
    gate, _, _ = setup
    contexts = []
    async def endpoint(request):
        contexts.append(copy_context())
        return JSONResponse({'ok': True})
    app = MaintenanceMiddleware(Starlette(routes=[Route('/', endpoint)]), gate)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://synthetic') as client:
            await client.get('/')
            assert (await gate.command({'command': 'begin'}))['state'] == 'SAFE_TO_STOP'
            with pytest.raises(MaintenanceRejected):
                contexts[0].run(gate.check_entry)
    asyncio.run(run())


def test_default_app_has_no_maintenance_gate(tmp_path, monkeypatch):
    from app.main import create_app
    from fastapi.testclient import TestClient
    monkeypatch.delenv('INVENTORY_MAINTENANCE_SOCKET', raising=False)
    application = create_app(data_directory=tmp_path / 'daily')
    assert application.state.maintenance is None
    with TestClient(application) as client:
        assert client.get('/docs').status_code == 200
        assert client.get('/maintenance/status').status_code == 404


def test_drain_workflow_has_only_read_only_pr_tests():
    from pathlib import Path
    import yaml
    text = (Path(__file__).resolve().parents[2] / '.github/workflows/c2-drain-tests.yml').read_text()
    data = yaml.load(text, Loader=yaml.BaseLoader)
    assert set(data['on']) == {'pull_request'}
    assert data['on']['pull_request']['branches'] == ['codex/numbering-v2-c2-linux-validation']
    assert data['permissions'] == {'contents': 'read'}
    assert data['concurrency']['group'].startswith('c2-drain-pr-')
    assert set(data['jobs']) == {'c2-drain'}
    assert data['jobs']['c2-drain']['runs-on'] == 'ubuntu-24.04'
    assert not any(word in text.lower() for word in ('secrets.', 'ssh', 'environment:', 'workflow_dispatch', 'push:'))
