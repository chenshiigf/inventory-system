from contextlib import closing
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import re

from app.database import Base, get_db
from app.main import create_app
from app.numbering_maintenance import Maintenance, MaintenanceAdmission, file_gate


@pytest.fixture
def maintenance_api(tmp_path):
    path = tmp_path / 'isolated.db'
    engine = create_engine(f'sqlite:///{path.as_posix()}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    root = tmp_path / 'data'
    app = create_app(data_directory=root)
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    yield app, Maintenance(root), path
    engine.dispose()


def test_writes_work_before_maintenance_and_are_blocked_after_restart(maintenance_api):
    app, maintenance, path = maintenance_api
    with TestClient(app) as client:
        assert client.post('/api/categories', json={'name': '合成分类'}).status_code == 201
    maintenance.enter('synthetic-maintenance', 'a' * 40, path, 'synthetic')
    # New app/middleware instance reads the persistent state, no cached boolean.
    restarted = create_app(data_directory=maintenance.root)
    restarted.dependency_overrides.update(app.dependency_overrides)
    with TestClient(restarted) as client:
        assert client.post('/api/categories', json={'name': '禁止新增'}).status_code == 503
        assert client.get('/api/products').status_code == 200
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT count(*) FROM categories').fetchone()[0] == 1


def test_abort_resume_state_keeps_api_writes_blocked_after_restart(maintenance_api):
    _app, maintenance, path = maintenance_api
    maintenance.enter('synthetic-maintenance', 'a' * 40, path, 'synthetic')
    state = maintenance.read()
    state.update({
        'state': 'abort_resuming',
        'abort_resume': {
            'format': 'numbering-abort-resume-v1',
            'state': 'authorized',
            'operation_id': 'synthetic-maintenance',
            'release_sha': 'a' * 40,
            'database_identity': state['database_identity'],
            'plan_sha256': 'b' * 64,
            'previous_commit': 'c' * 40,
            'expires_at_ns': 9_999_999_999_999_999_999,
        },
    })
    maintenance.path.write_text(json.dumps(state), encoding='utf8')
    assert maintenance.held()

    # Simulate middleware reloaded by a new process after interruption.
    reached = {'write': False}
    async def downstream(_scope, _receive, _send):
        reached['write'] = True
    middleware = MaintenanceAdmission(downstream, maintenance.root)
    sent = []
    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}
    async def send(message):
        sent.append(message)
    scope = {
        'type': 'http', 'path': '/api/categories', 'method': 'POST',
        'headers': [], 'query_string': b'', 'server': ('127.0.0.1', 8102),
        'client': ('127.0.0.1', 12345), 'scheme': 'http', 'http_version': '1.1',
        'asgi': {'version': '3.0'},
    }
    request = middleware(scope, receive, send)
    try:
        request.send(None)
    except StopIteration:
        pass
    else:
        pytest.fail('maintenance response unexpectedly yielded to an event loop')
    response_start = next(message for message in sent if message['type'] == 'http.response.start')
    assert response_start['status'] == 503
    assert not reached['write']
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT count(*) FROM categories').fetchone()[0] == 0


@pytest.mark.parametrize('method,path', [
    ('post', '/api/products'), ('patch', '/api/products/1'), ('delete', '/api/products/1'),
    ('post', '/api/categories'), ('patch', '/api/warehouses/1'),
    ('post', '/api/products/import/preview'), ('post', '/api/products/import/execute'),
    ('post', '/api/inventory/in'), ('post', '/api/inventory/out'),
    ('post', '/api/uploads'), ('delete', '/api/uploads/test.jpg'),
    ('post', '/api/visual-search/search'), ('get', '/api/visual-search/status'),
    ('put', '/api/unknown-future-write')])
def test_all_api_mutations_and_ai_routes_refused_before_validation(maintenance_api, method, path):
    app, maintenance, database = maintenance_api
    maintenance.enter('synthetic-maintenance', 'a' * 40, database, 'synthetic')
    before = database.read_bytes()
    files = sorted(str(p.relative_to(maintenance.root)) for p in maintenance.root.rglob('*'))
    with TestClient(app) as client:
        response = getattr(client, method)(path)
        assert response.status_code == 503
        assert response.headers['Retry-After'] == '30'
    assert database.read_bytes() == before
    assert sorted(str(p.relative_to(maintenance.root)) for p in maintenance.root.rglob('*')) == files


def test_broken_state_and_busy_gate_fail_closed(maintenance_api):
    app, maintenance, database = maintenance_api
    maintenance.enter('synthetic-maintenance', 'a' * 40, database, 'synthetic')
    maintenance.path.write_text('{}', encoding='utf8')
    with TestClient(app) as client:
        assert client.post('/api/products').status_code == 503
        with file_gate(maintenance.gate):
            assert client.post('/api/products').status_code == 503


def test_every_registered_api_write_route_is_guarded(maintenance_api):
    app, maintenance, database = maintenance_api
    maintenance.enter('synthetic-maintenance', 'a' * 40, database, 'synthetic')
    checked = []
    with TestClient(app) as client:
        for route_path, operations in app.openapi()['paths'].items():
            if not route_path.startswith('/api/'):
                continue
            for method in set(operations) & {'post', 'put', 'patch', 'delete'}:
                path = re.sub(r'\{[^}]+\}', '1', route_path)
                assert client.request(method, path).status_code == 503, (method, path)
                checked.append((method, path))
    assert len(checked) >= 20
