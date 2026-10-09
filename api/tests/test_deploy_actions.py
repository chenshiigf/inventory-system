"""Exercise deployment failures on temporary paths without touching a real server."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile

import pytest


@pytest.fixture
def installer():
    path = Path(__file__).resolve().parents[2] / 'deploy/deploy-actions.py'
    spec = importlib.util.spec_from_file_location('deploy_actions', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
def server(installer, tmp_path, monkeypatch):
    if os.name != 'posix':
        pytest.skip('Deployment integration tests require a Unix file lock')
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
    commit = 'a' * 40
    release = '100-1-' + commit
    digest = archive(m.INCOMING / (release + '.tar.gz'), entries(m, commit))
    calls = []
    state = {'head': 'old', 'failure': None, 'health_calls': 0}
    monkeypatch.setattr(m, 'environment', lambda: {})
    monkeypatch.setattr(m.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(m.shutil, 'which', lambda tool: '/usr/bin/' + tool)
    monkeypatch.setattr(m, 'active', lambda unit: unit == m.SERVICE or unit in m.TIMERS)
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
        if 'backup_inventory.py' in ' '.join(args):
            if state['failure'] == 'backup':
                raise subprocess.CalledProcessError(1, args)
            (Path(args[-1]) / 'verified-full').mkdir()
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
