"""Exercise deployment failures on temporary paths without touching a real server."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import create_app


@pytest.fixture
def installer():
    path = Path(__file__).resolve().parents[2] / 'deploy/deploy-actions.py'
    spec = importlib.util.spec_from_file_location('deploy_actions', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_check_reaches_actual_dashboard_api(installer, tmp_path, monkeypatch):
    """Exercise the installer's URL against real routes and an isolated database."""
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'health.db').as_posix()}",
        connect_args={'check_same_thread': False},
    )
    session_local = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    application = create_app(data_directory=tmp_path / 'data')

    def database():
        with session_local() as db:
            yield db

    application.dependency_overrides[get_db] = database
    monkeypatch.setattr(installer, 'active', lambda unit: unit == installer.SERVICE)
    try:
        with TestClient(application) as client:
            def open_api(url, timeout):
                target = urlsplit(url)
                assert target.netloc == '127.0.0.1:8102'
                response = client.get(target.path)
                response.raise_for_status()
                assert response.json()['active_product_count'] == 0
                stream = io.BytesIO(response.content)
                stream.status = response.status_code
                return stream

            monkeypatch.setattr(installer.urllib.request, 'urlopen', open_api)
            installer.health()
    finally:
        engine.dispose()


def archive(path, entries):
    with tarfile.open(path, 'w:gz') as bundle:
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            if content is None:
                member.type = tarfile.DIRTYPE
                bundle.addfile(member)
            else:
                data = content.encode()
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entries(module, commit):
    return {
        'release.json': json.dumps({'commit': commit}),
        'out/index.html': 'new frontend',
        'wheels/package.whl': 'fixture',
        **{f'api/{item}': None if item in module.API_ITEMS[:3] else 'new' for item in module.API_ITEMS},
        'api/app/version.py': 'new',
    }


@pytest.mark.parametrize('bad_path', ['../outside', '/absolute', 'api/data/inventory.db', 'api/app/.env', 'api/app/__pycache__/cached.pyc', 'api/uploads/a', 'wheels/installer.sh'])
def test_rejects_unsafe_or_business_data_payload(installer, tmp_path, bad_path):
    bundle = tmp_path / 'release.tar.gz'
    digest = archive(bundle, {bad_path: 'unsafe'})
    with pytest.raises(RuntimeError):
        installer.extract_release(bundle, tmp_path / 'extracted', digest)
    assert not (tmp_path / 'outside').exists()


def test_rejects_links_and_wrong_digest(installer, tmp_path):
    bundle = tmp_path / 'release.tar.gz'
    with tarfile.open(bundle, 'w:gz') as stream:
        member = tarfile.TarInfo('out/link')
        member.type = tarfile.SYMTYPE
        member.linkname = '/etc/passwd'
        stream.addfile(member)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match='links'):
        installer.extract_release(bundle, tmp_path / 'extracted', digest)
    with pytest.raises(RuntimeError, match='SHA-256'):
        installer.extract_release(bundle, tmp_path / 'extracted', '0' * 64)


@pytest.fixture
def server(installer, tmp_path, monkeypatch, request):
    if os.name != 'posix':
        if request.node.name not in {
            'test_hold_release_install_failure_keeps_business_services_stopped',
            'test_hold_alembic_upgrade_failure_keeps_business_services_stopped',
        }:
            pytest.skip('Deployment integration tests require a Unix file lock')
        # These two fault-injection tests exercise deployer state transitions,
        # not OS lock semantics. Linux CI tests the actual flock behavior.
        monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=lambda *args: None))
        monkeypatch.setattr(os, 'chown', lambda *args: None, raising=False)
    m = installer
    for name, relative in [('PROJECT', 'project'), ('STATIC', 'www/out'), ('INCOMING', 'incoming'), ('BACKUPS', 'backups'), ('DATA_DIR', 'data')]:
        monkeypatch.setattr(m, name, tmp_path / relative)
    m.STATIC.mkdir(parents=True)
    (m.STATIC / 'index.html').write_text('old frontend')
    m.INCOMING.mkdir()
    m.DATA_DIR.mkdir()
    with sqlite3.connect(m.DATA_DIR / 'inventory.db') as db:
        db.executescript("CREATE TABLE alembic_version(version_num TEXT); INSERT INTO alembic_version VALUES('old'); CREATE TABLE business(value TEXT); INSERT INTO business VALUES('preserve');")
    (m.DATA_DIR / 'uploads').mkdir()
    (m.DATA_DIR / 'uploads/photo').write_bytes(b'production photo')
    api = m.PROJECT / 'api'
    for item in m.API_ITEMS:
        path = api / item
        if item in m.API_ITEMS[:3]:
            path.mkdir(parents=True)
            (path / 'old.txt').write_text('old code')
        else:
            path.write_text('old file')
    (api / '.venv/bin').mkdir(parents=True)
    (api / '.venv/bin/python').write_text('old interpreter')
    (m.PROJECT / '.actions-deployed.json').write_text(json.dumps({'commit': 'c' * 40, 'release': 'old-release'}))
    commit = 'a' * 40
    release = '100-1-' + commit
    digest = archive(m.INCOMING / (release + '.tar.gz'), entries(m, commit))
    calls = []
    state = {'head': 'old', 'failure': None, 'health_calls': 0}
    monkeypatch.setattr(m, 'environment', lambda: {})
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0, raising=False)
    monkeypatch.setattr(m.shutil, 'which', lambda tool: '/usr/bin/' + tool)
    monkeypatch.setattr(m, 'active', lambda unit: unit in {m.SERVICE, 'nginx.service', *m.TIMERS})
    monkeypatch.setattr(m, 'numbering_abort_installed', lambda: True)
    monkeypatch.setattr(m, 'enter_numbering_hold', lambda *args: None)
    # Bundle assembly is covered with the synthetic abort context tests. The
    # deployer contract tests only need to exercise staging boundaries.
    monkeypatch.setattr(m, 'write_numbering_abort_bundle', lambda *args, **kwargs: None)
    # Replace the lock path with a temporary file; production calls remain mocked.
    original_open = open
    monkeypatch.setattr(m, 'open', lambda path, *args: original_open(tmp_path / 'lock' if path == '/run/inventory-actions.lock' else path, *args), raising=False)
    def command(*args, capture=False):
        calls.append(args)
        return 'x86_64' if args == ('uname', '-m') else ''
    monkeypatch.setattr(m, 'command', command)
    def service_user(env, *args, capture=False):
        calls.append(args)
        if '-c' in args:
            return json.dumps({'current': 'old', 'head': state['head']})
        if '-m' in args and args[args.index('-m') + 1] == 'venv':
            fake_venv = Path(args[-1])
            (fake_venv / 'bin').mkdir(parents=True, exist_ok=True)
            (fake_venv / 'bin/python').write_text('synthetic interpreter')
        if 'backup_inventory.py' in ' '.join(args):
            if state['failure'] == 'backup':
                raise subprocess.CalledProcessError(1, args)
            backup_root = Path(args[-1])
            if state.get('numbering_hold'):
                backup_set = backup_root / '2026-10-18_120000'
                backup_set.mkdir()
                (backup_set / 'manifest.json').write_text('{}')
                (backup_set / 'inventory.db').write_bytes(b'synthetic database backup')
                (backup_set / 'uploads.tar.gz').write_bytes(b'synthetic uploads backup')
            else:
                (backup_root / 'verified-full').mkdir()
        if 'upgrade' in args and state['failure'] == 'migration':
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(m, 'as_inventory', service_user)
    def health():
        state['health_calls'] += 1
        if state['failure'] == 'health' and state['health_calls'] == 1:
            raise RuntimeError('injected health failure')
    monkeypatch.setattr(m, 'health', health)
    monkeypatch.setattr(m.os, 'chdir', lambda path: None)
    return m, release, digest, calls, state


def test_deploy_success_preserves_business_data(server):
    m, release, digest, calls, state = server
    before = (m.DATA_DIR / 'inventory.db').read_bytes()
    m.deploy(release, digest, False)
    assert (m.PROJECT / 'api/app/version.py').read_text() == 'new'
    assert (m.PROJECT / 'api/.venv').is_symlink()
    assert (m.STATIC / 'index.html').read_text() == 'new frontend'
    assert (m.DATA_DIR / 'inventory.db').read_bytes() == before
    assert (m.DATA_DIR / 'uploads/photo').read_bytes() == b'production photo'
    assert not any('upgrade' in c for c in calls)
    assert json.loads((m.PROJECT / '.actions-deployed.json').read_text())['release'] == release


def test_pending_migration_fails_before_stopping_api(server):
    m, release, digest, calls, state = server
    state['head'] = 'new'
    with pytest.raises(RuntimeError, match='migration required'):
        m.deploy(release, digest, False)
    assert ('systemctl', 'stop', m.SERVICE) not in calls
    assert (m.STATIC / 'index.html').read_text() == 'old frontend'


def test_health_failure_restores_old_code_venv_and_static(server):
    m, release, digest, calls, state = server
    state['failure'] = 'health'
    with pytest.raises(RuntimeError, match='injected health'):
        m.deploy(release, digest, False)
    assert (m.PROJECT / 'api/app/old.txt').read_text() == 'old code'
    assert not (m.PROJECT / 'api/app/version.py').exists()
    assert not (m.PROJECT / 'api/.venv').is_symlink()
    assert (m.PROJECT / 'api/.venv/bin/python').read_text() == 'old interpreter'
    assert (m.STATIC / 'index.html').read_text() == 'old frontend'
    assert state['health_calls'] == 2


def test_backup_failure_restarts_unchanged_service(server):
    m, release, digest, calls, state = server
    state['failure'] = 'backup'
    with pytest.raises(subprocess.CalledProcessError):
        m.deploy(release, digest, False)
    assert (m.PROJECT / 'api/app/old.txt').is_file()
    assert ('systemctl', 'start', m.SERVICE) in calls


def test_failed_migration_keeps_api_and_timers_stopped(server):
    m, release, digest, calls, state = server
    state.update(head='new', failure='migration')
    with pytest.raises(subprocess.CalledProcessError):
        m.deploy(release, digest, True)
    assert ('systemctl', 'start', m.SERVICE) not in calls
    assert all(('systemctl', 'start', timer) not in calls for timer in m.TIMERS)
    assert (m.BACKUPS / release / 'full/verified-full').is_dir()


def test_missing_production_database_never_creates_one(server):
    m, release, digest, calls, state = server
    (m.DATA_DIR / 'inventory.db').unlink()
    with pytest.raises(RuntimeError, match='database missing'):
        m.deploy(release, digest, False)
    assert not (m.DATA_DIR / 'inventory.db').exists()
    assert ('systemctl', 'stop', m.SERVICE) not in calls


def numbering_payload(server):
    m, release, _, calls, state = server
    content = entries(m, release.split('-', 2)[2])
    content['api/app/numbering-v2.json'] = json.dumps({'scheme': 'product-numbering-v2', 'maintenance_protocol': 1})
    digest = archive(m.INCOMING / (release + '.tar.gz'), content)
    state['numbering_hold'] = True
    return m, release, digest, calls, state


def test_first_numbering_release_cannot_use_standard_deploy(server):
    m, release, digest, calls, _ = numbering_payload(server)
    with pytest.raises(RuntimeError, match='numbering-hold'):
        m.deploy(release, digest, False)
    assert ('systemctl', 'stop', m.SERVICE) not in calls


def test_hold_stages_schema_and_keeps_api_timers_stopped(server, monkeypatch):
    m, release, digest, calls, state = numbering_payload(server)
    held = []
    monkeypatch.setattr(m, 'enter_numbering_hold', lambda *args: held.append(args))
    state['head'] = 'new'
    m.deploy(release, digest, True, 'synthetic-cutover')
    assert len(held) == 1
    assert ('systemctl', 'start', m.SERVICE) not in calls
    assert all(('systemctl', 'start', timer) not in calls for timer in m.TIMERS)
    assert not (m.PROJECT / '.actions-deployed.json').exists()
    assert json.loads((m.PROJECT / '.actions-numbering-staged.json').read_text())['state'] == 'schema_staged_maintenance_held'
    baseline = json.loads((m.BACKUPS / release / 'numbering-abort-baseline.json').read_text())
    assert baseline['format'] == 'numbering-abort-baseline-v1'
    assert baseline['previous_commit'] == 'c' * 40


def test_hold_backup_failure_does_not_restart_business(server, monkeypatch):
    m, release, digest, calls, state = numbering_payload(server)
    monkeypatch.setattr(m, 'enter_numbering_hold', lambda *args: None)
    state['failure'] = 'backup'
    with pytest.raises(subprocess.CalledProcessError):
        m.deploy(release, digest, False, 'synthetic-cutover')
    assert ('systemctl', 'start', m.SERVICE) not in calls
    assert all(('systemctl', 'start', timer) not in calls for timer in m.TIMERS)


def test_hold_release_install_failure_keeps_business_services_stopped(server, monkeypatch):
    m, release, digest, calls, _ = numbering_payload(server)
    entered = []
    monkeypatch.setattr(m, 'enter_numbering_hold', lambda *args: entered.append(args))
    original_copytree = m.shutil.copytree
    release_app = m.PROJECT / '.actions-releases' / release / 'payload/api/app'

    def fail_release_app_copy(source, destination, *args, **kwargs):
        if Path(source) == release_app:
            raise OSError('injected release package installation failure')
        return original_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(m.shutil, 'copytree', fail_release_app_copy)
    with pytest.raises(OSError, match='release package installation'):
        m.deploy(release, digest, True, 'synthetic-cutover')

    rollback = m.BACKUPS / release
    assert len(entered) == 1
    assert not (m.PROJECT / 'api/app').exists()
    assert (rollback / 'api/app/old.txt').read_text() == 'old code'
    assert (rollback / 'old-venv/bin/python').read_text() == 'old interpreter'
    assert not (m.PROJECT / '.actions-numbering-staged.json').exists()
    assert ('systemctl', 'start', m.SERVICE) not in calls
    assert all(('systemctl', 'start', timer) not in calls for timer in m.TIMERS)


def test_hold_alembic_upgrade_failure_keeps_business_services_stopped(server, monkeypatch):
    m, release, digest, calls, state = numbering_payload(server)
    state.update(head='new', failure='migration')
    entered = []
    monkeypatch.setattr(m, 'enter_numbering_hold', lambda *args: entered.append(args))
    if os.name != 'posix':
        original_symlink_to = Path.symlink_to

        def simulated_venv_link(path, target, target_is_directory=False):
            if path == m.PROJECT / 'api/.venv':
                m.shutil.copytree(target, path)
            else:
                original_symlink_to(path, target, target_is_directory=target_is_directory)

        monkeypatch.setattr(Path, 'symlink_to', simulated_venv_link)

    with pytest.raises(subprocess.CalledProcessError):
        m.deploy(release, digest, True, 'synthetic-cutover')

    rollback = m.BACKUPS / release
    assert len(entered) == 1
    assert (rollback / 'api/app/old.txt').read_text() == 'old code'
    assert (rollback / 'old-venv/bin/python').read_text() == 'old interpreter'
    assert json.loads((m.PROJECT / '.actions-deployed.json').read_text())['commit'] == 'c' * 40
    assert ('systemctl', 'start', m.SERVICE) not in calls
    assert all(('systemctl', 'start', timer) not in calls for timer in m.TIMERS)


def commit_numbering_registry(module):
    with sqlite3.connect(module.DATA_DIR / 'inventory.db') as db:
        db.executescript("CREATE TABLE production_numbering_cutovers(id INTEGER PRIMARY KEY,state TEXT); INSERT INTO production_numbering_cutovers VALUES(1,'committed');")


def test_committed_numbering_rejects_old_payload(server):
    m, release, digest, calls, _ = server
    commit_numbering_registry(m)
    with pytest.raises(RuntimeError, match='incompatible old code'):
        m.deploy(release, digest, False)
    assert ('systemctl', 'stop', m.SERVICE) not in calls


def test_committed_numbering_failure_never_restores_previous_code(server):
    m, release, digest, calls, state = numbering_payload(server)
    commit_numbering_registry(m)
    state['failure'] = 'health'
    with pytest.raises(RuntimeError, match='injected health'):
        m.deploy(release, digest, False)
    assert (m.PROJECT / 'api/app/version.py').read_text() == 'new'
    assert not (m.PROJECT / 'api/app/old.txt').exists()
    # Exactly one start was attempted for health; no rollback restart.
    assert calls.count(('systemctl', 'start', m.SERVICE)) == 1


def test_existing_maintenance_refuses_automatic_deploy(server):
    m, release, digest, calls, _ = server
    directory = m.DATA_DIR / 'numbering-maintenance'
    directory.mkdir()
    (directory / 'state.json').write_text('{broken')
    with pytest.raises(RuntimeError, match='maintenance remains held'):
        m.deploy(release, digest, False)
    assert ('systemctl', 'stop', m.SERVICE) not in calls


def test_capability_and_dispatch_gate_are_explicit(installer, tmp_path):
    assert not installer.numbering_capable(tmp_path)
    marker = tmp_path / 'api/app/numbering-v2.json'
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({'scheme': 'product-numbering-v2', 'maintenance_protocol': 1}))
    assert installer.numbering_capable(tmp_path)
    workflow = (Path(__file__).resolve().parents[2] / '.github/workflows/deploy.yml').read_text(encoding='utf8')
    assert "github.event_name == 'workflow_dispatch' && inputs.deploy == true" in workflow
    assert "vars.DEPLOY_ENABLED == 'true'" in workflow
    assert '--capabilities' in workflow and '--numbering-hold' in workflow
    assert 'python -m pytest tests' in workflow
    assert 'node --test tests/*.test.mjs' in workflow


def test_persistent_service_guard_refuses_old_code_during_maintenance_and_after_commit(tmp_path):
    source = Path(__file__).resolve().parents[2] / 'deploy/numbering-service-guard.py'
    spec = importlib.util.spec_from_file_location('numbering_guard_test', source)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    data = tmp_path / 'data'
    data.mkdir()
    with sqlite3.connect(data / 'inventory.db') as db:
        db.execute('CREATE TABLE business(value TEXT)')
    capability = tmp_path / 'app/numbering-v2.json'
    guard.verify(data, capability)
    (data / 'numbering-maintenance').mkdir()
    with pytest.raises(OSError):
        guard.verify(data, capability)
    capability.parent.mkdir()
    capability.write_text(json.dumps({'scheme': 'product-numbering-v2', 'maintenance_protocol': 1}))
    (data / 'numbering-maintenance/state.json').write_text(json.dumps({'format': 'numbering-maintenance-v1', 'state': 'held'}))
    with pytest.raises(RuntimeError, match='maintenance remains held'):
        guard.verify(data, capability)
    (data / 'numbering-maintenance/state.json').write_text(json.dumps({'format': 'numbering-maintenance-v1', 'state': 'released'}))
    guard.verify(data, capability)
    (data / 'numbering-maintenance/state.json').unlink()
    (data / 'numbering-maintenance').rmdir()
    with sqlite3.connect(data / 'inventory.db') as db:
        db.executescript("CREATE TABLE production_numbering_cutovers(id INTEGER,state TEXT); INSERT INTO production_numbering_cutovers VALUES(1,'committed');")
    capability.write_text('{}')
    with pytest.raises(RuntimeError, match='incompatible old code'):
        guard.verify(data, capability)


def _abort_guard_fixture(tmp_path):
    source = Path(__file__).resolve().parents[2] / 'deploy/numbering-service-guard.py'
    spec = importlib.util.spec_from_file_location('numbering_abort_guard_test', source)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    data = tmp_path / 'data'
    maintenance = data / 'numbering-maintenance'
    project = tmp_path / 'project'
    capability = tmp_path / 'api/app/numbering-v2.json'
    data.mkdir()
    maintenance.mkdir(parents=True)
    project.mkdir()
    capability.parent.mkdir(parents=True)
    database = data / 'inventory.db'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE products(id INTEGER PRIMARY KEY)')
    old_commit = 'a' * 40
    project.joinpath('.actions-deployed.json').write_text(json.dumps({'commit': old_commit}))
    return guard, data, maintenance, database, project, capability, old_commit


def test_service_guard_allows_only_operation_bound_private_abort_probe(tmp_path):
    guard, data, maintenance, database, project, capability, old_commit = _abort_guard_fixture(tmp_path)
    operation_id = 'abort-probe-001'
    release_sha = 'b' * 40
    now = 1_000_000
    state = {
        'format': 'numbering-maintenance-v1', 'state': 'held',
        'operation_id': operation_id, 'release_sha': release_sha,
        'database_identity': guard._database_identity(database),
        'abort_probe': {
            'format': 'numbering-abort-probe-v1', 'state': 'authorized',
            'operation_id': operation_id, 'release_sha': release_sha,
            'database_identity': guard._database_identity(database),
            'previous_commit': old_commit, 'plan_sha256': 'c' * 64,
            'expires_at_ns': now + 1,
        },
    }
    (maintenance / 'state.json').write_text(json.dumps(state))
    guard.verify(data, capability, project, proxy_active=lambda: False,
                 service_loopback=lambda: True, now_ns=now)
    with pytest.raises(RuntimeError, match='Nginx must remain stopped'):
        guard.verify(data, capability, project, proxy_active=lambda: True,
                     service_loopback=lambda: True, now_ns=now)
    with pytest.raises(RuntimeError, match='loopback'):
        guard.verify(data, capability, project, proxy_active=lambda: False,
                     service_loopback=lambda: False, now_ns=now)
    state['abort_probe']['expires_at_ns'] = now
    (maintenance / 'state.json').write_text(json.dumps(state))
    with pytest.raises(RuntimeError, match='expired'):
        guard.verify(data, capability, project, proxy_active=lambda: False,
                     service_loopback=lambda: True, now_ns=now)


def test_service_guard_allows_private_abort_resume_only_with_proxy_down(tmp_path):
    guard, data, maintenance, database, project, capability, old_commit = _abort_guard_fixture(tmp_path)
    operation_id = 'abort-resume-001'
    release_sha = 'b' * 40
    now = 2_000_000
    state = {
        'format': 'numbering-maintenance-v1', 'state': 'abort_resuming',
        'operation_id': operation_id, 'release_sha': release_sha,
        'database_identity': guard._database_identity(database),
        'abort_resume': {
            'format': 'numbering-abort-resume-v1', 'state': 'authorized',
            'operation_id': operation_id, 'release_sha': release_sha,
            'database_identity': guard._database_identity(database),
            'previous_commit': old_commit, 'plan_sha256': 'c' * 64,
            'expires_at_ns': now + 1,
        },
    }
    (maintenance / 'state.json').write_text(json.dumps(state))
    guard.verify(data, capability, project, proxy_active=lambda: False,
                 service_loopback=lambda: True, now_ns=now)
    with pytest.raises(RuntimeError, match='Nginx must remain stopped'):
        guard.verify(data, capability, project, proxy_active=lambda: True,
                     service_loopback=lambda: True, now_ns=now)
    state['abort_resume']['expires_at_ns'] = now
    (maintenance / 'state.json').write_text(json.dumps(state))
    with pytest.raises(RuntimeError, match='expired'):
        guard.verify(data, capability, project, proxy_active=lambda: False,
                     service_loopback=lambda: True, now_ns=now)


@pytest.mark.parametrize('state_name', ['held', 'abort_resuming'])
def test_nginx_guard_refuses_start_while_numbering_maintenance_is_held(tmp_path, state_name):
    guard, data, maintenance, _database, project, capability, _old_commit = _abort_guard_fixture(tmp_path)
    (maintenance / 'state.json').write_text(json.dumps({
        'format': 'numbering-maintenance-v1', 'state': state_name,
    }))
    with pytest.raises(RuntimeError, match='Nginx startup is forbidden'):
        guard.verify_proxy(data, capability, project)


def test_numbering_abort_installed_requires_loaded_api_and_nginx_guards(installer, tmp_path, monkeypatch):
    library = tmp_path / 'numbering-library'
    library.mkdir()
    cli = tmp_path / 'numbering-abort'
    cli.write_text('synthetic')
    module = library / 'numbering_abort.py'
    module.write_text('synthetic')
    service_guard = tmp_path / 'numbering-service-guard'
    service_guard.write_text('synthetic')
    api_dropin = tmp_path / 'api-guard.conf'
    api_dropin.write_text('ExecStartPre=/usr/local/sbin/inventory-numbering-service-guard\n')
    proxy_dropin = tmp_path / 'nginx-guard.conf'
    proxy_dropin.write_text('ExecStartPre=/usr/local/sbin/inventory-numbering-service-guard --proxy\n')
    monkeypatch.setattr(installer, 'NUMBERING_LIBRARY', library)
    monkeypatch.setattr(installer, 'ABORT_CLI', cli)
    monkeypatch.setattr(installer, 'SERVICE_GUARD', service_guard)
    monkeypatch.setattr(installer, 'API_GUARD_DROPIN', api_dropin)
    monkeypatch.setattr(installer, 'PROXY_GUARD_DROPIN', proxy_dropin)
    monkeypatch.setattr(installer, '_root_owned_file', lambda path, executable=False: path.is_file())
    monkeypatch.setattr(installer, 'command', lambda *args, **kwargs: str(
        api_dropin if args[2] == installer.SERVICE else proxy_dropin
    ))
    assert installer.numbering_abort_installed()

    monkeypatch.setattr(installer, 'command', lambda *args, **kwargs: str(api_dropin))
    assert not installer.numbering_abort_installed()


def test_service_guard_allows_verified_precommit_abort_release_only_for_old_commit(tmp_path):
    guard, data, maintenance, database, project, capability, old_commit = _abort_guard_fixture(tmp_path)
    checks = {'cutover_not_committed': True, 'database_restored': True,
              'old_code_restored': True, 'api_readonly_probe': True,
              'inventory_readonly_checks': True, 'normal_api_health': True,
              'ai_unchanged': True, 'timers_restored': True,
              'proxy_stopped_until_release': True}
    state = {
        'format': 'numbering-maintenance-v1', 'state': 'released',
        'release_reason': 'precommit_abort', 'operation_id': 'abort-release-001',
        'release_sha': 'b' * 40, 'database_identity': guard._database_identity(database),
        'abort_receipt': {
            'format': 'numbering-abort-receipt-v1', 'state': 'complete',
            'operation_id': 'abort-release-001', 'release_sha': 'b' * 40,
            'database_identity': guard._database_identity(database),
            'plan_sha256': 'c' * 64, 'backup_manifest_sha256': 'd' * 64,
            'previous_commit': old_commit, 'checks': checks,
        },
    }
    (maintenance / 'state.json').write_text(json.dumps(state))
    with pytest.raises(RuntimeError, match='durable pre-release validation'):
        guard.verify(data, capability, project)
    with pytest.raises(RuntimeError, match='durable pre-release validation'):
        guard.verify_proxy(data, capability, project)
    (maintenance / 'abort-validation.json').write_text(json.dumps({
        'format': 'numbering-abort-validation-v1', 'state': 'ready_to_release',
        'operation_id': 'abort-release-001', 'release_sha': 'b' * 40,
        'database_identity': guard._database_identity(database), 'plan_sha256': 'c' * 64,
        'previous_commit': old_commit, 'checks': checks,
    }))
    guard.verify(data, capability, project)
    guard.verify_proxy(data, capability, project)
    project.joinpath('.actions-deployed.json').write_text(json.dumps({'commit': 'e' * 40}))
    with pytest.raises(RuntimeError, match='Restored source'):
        guard.verify(data, capability, project)
    project.joinpath('.actions-deployed.json').write_text(json.dumps({'commit': old_commit}))
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE production_numbering_cutovers(id INTEGER PRIMARY KEY, state TEXT)')
        db.execute("INSERT INTO production_numbering_cutovers VALUES(1,'committed')")
    with pytest.raises(RuntimeError, match='cutover registry is committed'):
        guard.verify(data, capability, project)
