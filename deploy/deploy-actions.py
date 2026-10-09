#!/usr/bin/python3
"""Update the confirmed Ubuntu deployment; never build on the production host."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
import urllib.request

PROJECT = Path('/srv/inventory-system')
STATIC = Path('/var/www/inventory-system/out')
INCOMING = Path('/var/tmp/inventory-actions')
BACKUPS = Path('/var/backups/inventory-system/actions')
ENV_FILE = Path('/etc/inventory-system.env')
DATA_DIR = Path('/var/lib/inventory-system')
SERVICE = 'inventory-system-api.service'
TIMERS = ('inventory-db-backup.timer', 'inventory-full-backup.timer')
API_ITEMS = ('app', 'alembic', 'scripts', 'alembic.ini', 'requirements.txt', 'requirements.lock.txt')


def command(*args: str, capture: bool = False) -> str:
    result = subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE if capture else None)
    return result.stdout.strip() if capture else ''


def active(unit: str) -> bool:
    return subprocess.run(('systemctl', 'is-active', '--quiet', unit), check=False).returncode == 0


def environment() -> dict[str, str]:
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        tokens = shlex.split(line, comments=True)
        if not tokens:
            continue
        if len(tokens) != 1 or '=' not in tokens[0]:
            raise RuntimeError('Unsupported environment file syntax')
        key, value = tokens[0].split('=', 1)
        values[key] = value
    # This installer intentionally targets the documented production paths.
    if values.get('INVENTORY_DATA_DIR') != '/var/lib/inventory-system' or values.get('DATABASE_URL') != 'sqlite:////var/lib/inventory-system/inventory.db':
        raise RuntimeError('Production data paths differ from deploy/README.md; adapt installer first')
    return values


def as_inventory(env: dict[str, str], *args: str, capture: bool = False) -> str:
    return command('runuser', '-u', 'inventory', '--', 'env', *(f'{k}={v}' for k, v in env.items()), *args, capture=capture)


def extract_release(archive: Path, destination: Path, digest: str) -> dict:
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
            raise RuntimeError('Release SHA-256 mismatch')
    with tarfile.open(archive, 'r:gz') as bundle:
        seen = set()
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts:
                raise RuntimeError('Unsafe archive path')
            if not (member.isfile() or member.isdir()) or str(path) in seen:
                raise RuntimeError('Archive links, special files and duplicates are forbidden')
            seen.add(str(path))
            parts = path.parts
            allowed = parts == ('release.json',) or parts[0] == 'out'
            allowed |= parts[0] == 'wheels' and (len(parts) == 1 or len(parts) == 2 and parts[1].endswith('.whl'))
            allowed |= parts[0] == 'api' and (len(parts) == 1 or parts[1] in API_ITEMS[:3] or len(parts) == 2 and parts[1] in API_ITEMS[3:])
            if not allowed or any(p in {'.env', '.venv', '__pycache__', 'data', 'uploads'} for p in parts[1:]):
                raise RuntimeError('Unexpected release content: ' + str(path))
        bundle.extractall(destination, filter='data')
    metadata = json.loads((destination / 'release.json').read_text())
    if not re.fullmatch(r'[0-9a-f]{40}', metadata.get('commit', '')):
        raise RuntimeError('Missing release commit')
    for item in API_ITEMS:
        if not (destination / 'api' / item).exists():
            raise RuntimeError('Incomplete API payload')
    if not (destination / 'out/index.html').is_file() or not list((destination / 'wheels').glob('*.whl')):
        raise RuntimeError('Missing static export or wheels')
    return metadata


def remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def health() -> None:
    for _ in range(30):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8102/api/dashboard', timeout=3) as response:
                if response.status == 200 and isinstance(json.load(response), dict) and active(SERVICE):
                    return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError('API health check failed')


def deploy(release: str, digest: str, migrate: bool) -> None:
    import fcntl
    if os.geteuid() != 0:
        raise RuntimeError('Run through the installed sudo command')
    if not re.fullmatch(r'[0-9]+-[0-9]+-[0-9a-f]{40}', release) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise RuntimeError('Invalid release ID or digest')
    lock = open('/run/inventory-actions.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    env = environment()
    api = PROJECT / 'api'
    old_python = api / '.venv/bin/python'
    if not old_python.is_file() or not STATIC.is_dir() or not active(SERVICE):
        raise RuntimeError('Existing deployment must be installed and running')
    db = DATA_DIR / 'inventory.db'
    if not db.is_file() or db.stat().st_size == 0:
        raise RuntimeError('Production database missing; refusing to create an empty one')
    for tool in ('runuser', 'python3.12', 'systemctl'):
        if not shutil.which(tool):
            raise RuntimeError('Missing server tool: ' + tool)
    if command('uname', '-m', capture=True) != 'x86_64':
        raise RuntimeError('Release wheels require x86_64 Ubuntu')
    for item in API_ITEMS:
        if not (api / item).exists():
            raise RuntimeError('Existing API layout differs from the deployment baseline')
    work = PROJECT / '.actions-releases' / release
    if work.exists():
        raise RuntimeError('Release already attempted; rerun Actions for a new attempt ID')
    work.mkdir(parents=True)
    work.chmod(0o755)
    metadata = extract_release(INCOMING / (release + '.tar.gz'), work / 'payload', digest)
    if metadata['commit'] != release.split('-', 2)[2]:
        raise RuntimeError('Release commit does not match ID')
    payload = work / 'payload'
    venv = work / 'venv'
    # Dependencies are installed as the service user, from prebuilt wheels only.
    command('chown', '-R', 'inventory:inventory', str(work))
    as_inventory(env, 'python3.12', '-m', 'venv', str(venv))
    as_inventory(env, str(venv / 'bin/python'), '-m', 'pip', 'install', '--no-index', '--only-binary=:all:', '--find-links', str(payload / 'wheels'), '-r', str(payload / 'api/requirements.lock.txt'))
    as_inventory(env, str(venv / 'bin/python'), '-m', 'pip', 'check')
    check = """import json, sqlite3, sys
from alembic.config import Config
from alembic.script import ScriptDirectory
config = Config(sys.argv[1])
config.set_main_option('script_location', sys.argv[2])
url = config.get_main_option('sqlalchemy.url')
if url and not url.startswith('driver://'):
    raise SystemExit('Release alembic.ini must use the environment URL placeholder')
script = ScriptDirectory.from_config(config)
heads = script.get_heads()
if len(heads) != 1: raise SystemExit('Expected exactly one Alembic head')
with sqlite3.connect(sys.argv[3] + '?mode=ro', uri=True) as db:
    current = [r[0] for r in db.execute('SELECT version_num FROM alembic_version')]
if len(current) != 1: raise SystemExit('Expected exactly one production revision')
list(script.iterate_revisions(heads[0], current[0]))
print(json.dumps({'current': current[0], 'head': heads[0]}))
"""
    revision = json.loads(as_inventory(env, str(venv / 'bin/python'), '-c', check, str(payload / 'api/alembic.ini'), str(payload / 'api/alembic'), db.as_uri(), capture=True))
    needs_migration = revision['current'] != revision['head']
    if needs_migration and not migrate:
        raise RuntimeError('Database migration required; use Run workflow with migrate=true')
    rollback = BACKUPS / release
    rollback.mkdir(parents=True, mode=0o750)
    (rollback / 'api').mkdir()
    for item in API_ITEMS:
        source = api / item
        target = rollback / 'api' / item
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target)
    staged_static = STATIC.parent / ('.out-' + release)
    old_static = STATIC.parent / ('.old-out-' + release)
    old_venv = work / 'old-venv'
    shutil.copytree(payload / 'out', staged_static)
    for path in (staged_static, *staged_static.rglob('*')):
        path.chmod(0o755 if path.is_dir() else 0o644)
    timers = [timer for timer in TIMERS if active(timer)]
    stopped = changed = migration_started = succeeded = False
    try:
        for timer in timers:
            command('systemctl', 'stop', timer)
        # Allow running backup jobs to finish before replacing their code.
        for _ in range(150):
            if not any(command('systemctl', 'show', '--property=ActiveState', '--value', s, capture=True) in {'active', 'activating', 'deactivating', 'reloading'} for s in ('inventory-db-backup.service', 'inventory-full-backup.service')):
                break
            time.sleep(2)
        else:
            raise RuntimeError('Backup job still running; deploy later')
        command('systemctl', 'stop', SERVICE)
        stopped = True
        (rollback / 'full').mkdir()
        command('chown', 'inventory:inventory', str(rollback), str(rollback / 'full'))
        as_inventory(env, str(old_python), str(api / 'scripts/backup_inventory.py'), '--destination', str(rollback / 'full'))
        # Recheck the revision after stopping business writes.
        with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
            current = [r[0] for r in connection.execute('SELECT version_num FROM alembic_version')]
        if current != [revision['current']]:
            raise RuntimeError('Database revision changed during staging')
        changed = True
        for item in API_ITEMS:
            remove(api / item)
            source = payload / 'api' / item
            if source.is_dir():
                shutil.copytree(source, api / item)
            else:
                shutil.copy2(source, api / item)
        command('chown', '-R', 'inventory:inventory', *(str(api / item) for item in API_ITEMS))
        (api / '.venv').rename(old_venv)
        (api / '.venv').symlink_to(venv, target_is_directory=True)
        if needs_migration:
            migration_started = True
            # Alembic resolves relative script_location against this cwd.
            os.chdir(api)
            as_inventory(env, str(venv / 'bin/python'), '-m', 'alembic', 'upgrade', 'head')
        STATIC.rename(old_static)
        staged_static.rename(STATIC)
        command('systemctl', 'start', SERVICE)
        health()
        (PROJECT / '.actions-deployed.json').write_text(json.dumps({**metadata, 'release': release}, indent=2) + '\n')
        succeeded = True
        print('Deployment succeeded: ' + metadata['commit'])
    except BaseException:
        if changed:
            command('systemctl', 'stop', SERVICE)
            if migration_started:
                print('Migration attempted: API remains stopped. Inspect logs and restore from ' + str(rollback / 'full') + ' if needed.', file=sys.stderr)
            else:
                for item in API_ITEMS:
                    remove(api / item)
                    source = rollback / 'api' / item
                    if source.is_dir():
                        shutil.copytree(source, api / item, symlinks=True)
                    else:
                        shutil.copy2(source, api / item)
                command('chown', '-R', 'inventory:inventory', *(str(api / item) for item in API_ITEMS))
                if old_venv.exists():
                    remove(api / '.venv')
                    old_venv.rename(api / '.venv')
                if old_static.exists():
                    remove(STATIC)
                    old_static.rename(STATIC)
                command('systemctl', 'start', SERVICE)
                health()
                print('Previous code, virtualenv and frontend restored.', file=sys.stderr)
        elif stopped:
            command('systemctl', 'start', SERVICE)
        raise
    finally:
        # A failed migration needs inspection before backups resume.
        if not migration_started or succeeded:
            for timer in timers:
                command('systemctl', 'start', timer)


if __name__ == '__main__':
    try:
        if len(sys.argv) != 4 or sys.argv[3] not in ('true', 'false'):
            raise RuntimeError('Usage: deploy-actions.py RELEASE_ID SHA256 true|false')
        deploy(sys.argv[1], sys.argv[2], sys.argv[3] == 'true')
    except Exception as error:
        print('Deployment failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
