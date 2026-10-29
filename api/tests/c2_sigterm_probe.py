"""Child process for synthetic SIGTERM checks; no external network or credentials.

Uses the real app lifespan/close methods. Only the slow workload body and model
are synthetic. It never runs migrations, numbering, or systemd commands.
"""
from contextlib import asynccontextmanager, closing, nullcontext
from dataclasses import replace
from io import BytesIO
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

API = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API))


def main():
    root, scenario = Path(sys.argv[1]).resolve(), sys.argv[2]
    assert root.is_dir() and (root / 'synthetic-only').read_text() == 'C2 TEST ONLY'
    assert Path(os.environ['INVENTORY_DATA_DIR']).resolve() == root / 'data'
    assert os.environ['DATABASE_URL'] == 'sqlite:///' + (root / 'data/inventory.db').as_posix()
    assert not any(k in os.environ for k in ('DASHSCOPE_API_KEY', 'SSH_AUTH_SOCK', 'GITHUB_TOKEN'))

    # No network calls outside this process's loopback listener, including DNS.
    original_connect, original_resolve = socket.socket.connect, socket.getaddrinfo
    def connect(sock, address):
        if sock.family == socket.AF_UNIX or isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
            return original_connect(sock, address)
        raise AssertionError('external network forbidden')
    def resolve(host, *args, **kwargs):
        if host not in {None, '127.0.0.1', '::1', 'localhost'}:
            raise AssertionError('external DNS forbidden')
        return original_resolve(host, *args, **kwargs)
    socket.socket.connect, socket.getaddrinfo = connect, resolve

    guard = threading.Lock()
    def event(name, **values):
        with guard, (root / 'events.jsonl').open('a', encoding='utf8') as stream:
            stream.write(json.dumps({'event': name, 'time': time.monotonic(), **values}) + '\n')
            stream.flush()

    data = root / 'data'
    visual = data / 'visual-search'
    visual.mkdir(parents=True)
    databases = [data / 'inventory.db', visual / 'catalog.sqlite', visual / 'daily-budget.sqlite3']
    for path in databases:
        with closing(sqlite3.connect(path)) as db, db:
            db.executescript('CREATE TABLE c2_sentinel(id INTEGER PRIMARY KEY,value INTEGER); INSERT INTO c2_sentinel VALUES(1,123);')

    from app import main as app_main
    from app.database import engine
    from app.services.product_import import preview_tasks
    from app.services.product_import.session_store import SOURCE_FILE_NAME
    from app.services.visual_search.service import VisualService
    from app.services.visual_search.images import image_hash
    from PIL import Image
    import numpy as np
    import uvicorn

    application = app_main.app
    registry = application.state.product_import_preview_tasks
    development = application.state.visual_search
    sources = application.state.visual_sources
    assert isinstance(development, VisualService)
    release = root / 'release-work'
    business_hash = hashlib.sha256(databases[0].read_bytes()).hexdigest()
    def wait_for_release(checkpoint=None):
        while not release.exists():
            if checkpoint:
                checkpoint()
            time.sleep(.01)
        if checkpoint:
            checkpoint()

    if scenario in {'busy_import', 'stalled_import'}:
        def synthetic_reader(_data, *, checkpoint):
            # Simulates interruptible work or non-interruptible I/O while a
            # synthetic transaction is open. Actual preview cancellation runs.
            with closing(sqlite3.connect(databases[0])) as db, db:
                db.execute('INSERT INTO c2_sentinel VALUES(2,456)')
                event('work_started')
                wait_for_release(checkpoint if scenario == 'busy_import' else None)
                checkpoint()
            return []
        preview_tasks.read_import_workbook = synthetic_reader
        directory = registry.sessions.root / ('a' * 32)
        directory.mkdir()
        (directory / SOURCE_FILE_NAME).write_bytes(b'SYNTHETIC WORKBOOK PLACEHOLDER')
        registry.acquire()
        registry.start(task_id='a' * 32, directory=directory, engine=engine,
                       file_name='synthetic.xlsx', already_imported=False)

    if scenario in {'busy_index', 'busy_mirror_index', 'stalled_index'}:
        service = development
        config = replace(service.config, dimension=256, interval=.1)
        if scenario == 'busy_mirror_index':
            # Same VisualService close path used by VisualSources.production;
            # a synthetic source avoids production mirror identity/data.
            service = VisualService(visual / 'synthetic-mirror-index', data / 'uploads', config,
                                    provider=SimpleNamespace(), call_lock_root=visual)
            service.slot = development.slot
            sources.production = service
        service.config = service.store.config = config
        service.store.root.mkdir(parents=True, exist_ok=True)
        databases.append(service.store.path)
        with closing(sqlite3.connect(service.store.path)) as db, db:
            db.executescript('''
            CREATE TABLE alembic_version(version_num TEXT); INSERT INTO alembic_version VALUES('vs01');
            CREATE TABLE visual_jobs(id TEXT PRIMARY KEY,index_key TEXT,owner TEXT,status TEXT,max_items INTEGER,
              processed INTEGER DEFAULT 0,succeeded INTEGER DEFAULT 0,failed INTEGER DEFAULT 0,started_at TEXT,finished_at TEXT,error TEXT);
            CREATE TABLE visual_embeddings(product_id INTEGER,index_key TEXT,image_identifier TEXT,image_hash TEXT,
              embedding_model TEXT,embedding_dimension INTEGER,embedding_version TEXT,preprocessing_version TEXT,
              embedding_vector BLOB,indexed_at TEXT,indexing_status TEXT,error TEXT);
            ''')
            db.execute("INSERT INTO visual_jobs(id,index_key,owner,status,max_items,started_at) VALUES(?,?,?,'running',1,'synthetic')",
                       ('c2-job', config.index_key, 'synthetic'))
        image = BytesIO()
        Image.new('RGB', (8, 8), 'white').save(image, format='PNG')
        raw = image.getvalue()
        class SyntheticSource:
            production = False
            def session(self, _bind): return nullcontext(None)
            def get(self, _db, _pid): return SimpleNamespace(id=1, is_active=True, image_path='synthetic.png')
            def raw(self, _product): return raw
        class FakeProvider:
            paid_calls = False
            def embed(self, *_args):
                event('work_started')
                wait_for_release()
                return SimpleNamespace(vector=np.ones(config.dimension, dtype=np.float32))
        service.source, service.provider = SyntheticSource(), FakeProvider()
        assert service._acquire()
        plan = {'obsolete': [], 'skipped': [], 'invalid_images': [],
                'pending': [{'product_id': 1, 'identifier': 'synthetic.png', 'digest': image_hash(raw)}]}
        service.executor.submit(service._run_job, None, 'c2-job', plan, 1)

    def observe_close(obj, name):
        original = obj.close
        def close():
            event(name + '_begin')
            original()
            event(name + '_end')
        obj.close = close
    observe_close(registry, 'preview_close')
    observe_close(development, 'visual_close')
    if sources.production:
        observe_close(sources.production, 'mirror_close')
    observe_close(sources, 'sources_close')
    original_lifespan = application.router.lifespan_context
    @asynccontextmanager
    async def observed_lifespan(app):
        async with original_lifespan(app):
            event('startup_complete')
            yield
        event('shutdown_complete')
    application.router.lifespan_context = observed_lifespan
    (root / 'fixtures.json').write_text(json.dumps({
        'databases': [str(p.relative_to(root)) for p in databases],
        'business_sha256': business_hash,
        'python': sys.version.split()[0], 'sqlite': sqlite3.sqlite_version,
    }), encoding='utf8')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    server = uvicorn.Server(uvicorn.Config(application, log_level='warning', lifespan='on', loop='asyncio'))
    async def run():
        task = asyncio.create_task(server.serve(sockets=[sock]))
        while not server.started:
            if task.done():
                await task
                raise AssertionError('server stopped before ready')
            await asyncio.sleep(.01)
        (root / 'ready.json').write_text(json.dumps({'pid': os.getpid(), 'pgid': os.getpgrp(), 'synthetic': True}), encoding='utf8')
        await task
    asyncio.run(run())


if __name__ == '__main__':
    main()
