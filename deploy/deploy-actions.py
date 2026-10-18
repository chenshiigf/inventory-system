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
NUMBERING_LIBRARY = Path('/usr/local/lib/inventory-numbering')
SERVICE = 'inventory-system-api.service'
PROXY = 'nginx.service'
HEALTH_URL = 'http://127.0.0.1:8102/api/dashboard/summary'
TIMERS = ('inventory-db-backup.timer', 'inventory-full-backup.timer')
API_ITEMS = ('app', 'alembic', 'scripts', 'alembic.ini', 'requirements.txt', 'requirements.lock.txt')
SERVICE_GUARD = Path('/usr/local/sbin/inventory-numbering-service-guard')
ABORT_CLI = Path('/usr/local/sbin/inventory-numbering-abort')
API_GUARD_DROPIN = Path('/etc/systemd/system/inventory-system-api.service.d/numbering-guard.conf')
PROXY_GUARD_DROPIN = Path('/etc/systemd/system/nginx.service.d/numbering-guard.conf')


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
    last_error = 'Expected HTTP 200, a JSON object and an active API service'
    for _ in range(30):
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=3) as response:
                if response.status == 200 and isinstance(json.load(response), dict) and active(SERVICE):
                    return
        except (OSError, ValueError) as error:
            last_error = str(error)
        time.sleep(2)
    raise RuntimeError(f'API health check failed at {HEALTH_URL}: {last_error}')


def numbering_state(database: Path) -> bool:
    """Data scheme, not Alembic revision, decides rollback compatibility."""
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        if not db.execute("SELECT count(*) FROM sqlite_master WHERE name='production_numbering_cutovers'").fetchone()[0]:
            return False
        rows = db.execute('SELECT id,state FROM production_numbering_cutovers').fetchall()
        if rows and rows != [(1, 'committed')]:
            raise RuntimeError('Invalid numbering completion registry')
        return bool(rows)


def numbering_capable(payload: Path) -> bool:
    try:
        return json.loads((payload / 'api/app/numbering-v2.json').read_text()) == {
            'scheme': 'product-numbering-v2', 'maintenance_protocol': 1}
    except (OSError, ValueError):
        return False


def _root_owned_file(path: Path, *, executable: bool = False) -> bool:
    try:
        info = path.lstat()
        return (not path.is_symlink() and path.is_file() and info.st_uid == 0 and
                not info.st_mode & 0o022 and (not executable or bool(info.st_mode & 0o111)))
    except OSError:
        return False


def _loaded_guard_dropin(unit: str, path: Path, directive: str) -> bool:
    if not _root_owned_file(path):
        return False
    try:
        loaded = command('systemctl', 'show', unit, '--property=DropInPaths', '--value', capture=True).split()
        lines = path.read_text(encoding='utf8').splitlines()
    except (OSError, RuntimeError):
        return False
    return str(path) in loaded and directive in lines


def enter_numbering_hold(payload: Path, database: Path, operation_id: str, commit: str) -> None:
    # The caller already owns /run/inventory-actions.lock. Stdlib-only helpers;
    # no application startup, AI state, model client or production secrets.
    # Never import root code from the service-user-writable release staging tree.
    for path in (NUMBERING_LIBRARY, *NUMBERING_LIBRARY.parents, *NUMBERING_LIBRARY.rglob('*')):
        info = path.lstat()
        if path.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Install reviewed root-owned numbering helpers separately before deployment')
    if not (NUMBERING_LIBRARY / 'app/services/product_code_cutover.py').is_file():
        raise RuntimeError('Root-owned numbering helpers are not installed')
    abort_cli = ABORT_CLI
    abort_module = NUMBERING_LIBRARY / 'numbering_abort.py'
    for path in (abort_cli, abort_module):
        if not _root_owned_file(path, executable=(path == abort_cli)):
            raise RuntimeError('Reviewed root-owned pre-commit abort recovery must be installed before numbering hold')
    if not _root_owned_file(SERVICE_GUARD, executable=True):
        raise RuntimeError('Root-owned persistent service guard must be installed first')
    if not _loaded_guard_dropin(
            SERVICE, API_GUARD_DROPIN,
            'ExecStartPre=/usr/local/sbin/inventory-numbering-service-guard'):
        raise RuntimeError('Persistent API guard is not loaded; do not enter the release window')
    if not _loaded_guard_dropin(
            PROXY, PROXY_GUARD_DROPIN,
            'ExecStartPre=/usr/local/sbin/inventory-numbering-service-guard --proxy'):
        raise RuntimeError('Persistent Nginx guard is not loaded; do not enter the release window')
    sys.path.insert(0, str(NUMBERING_LIBRARY))
    try:
        from app.services.numbering_environment import ProductionEnvironment
        from app.services.product_code_cutover import enter
        enter(ProductionEnvironment(database), operation_id, commit)
    finally:
        sys.path.pop(0)


def numbering_abort_installed() -> bool:
    try:
        cli = ABORT_CLI
        module = NUMBERING_LIBRARY / 'numbering_abort.py'
        if not _root_owned_file(cli, executable=True) or not _root_owned_file(module):
            return False
        return (_root_owned_file(SERVICE_GUARD, executable=True) and
                _loaded_guard_dropin(
                    SERVICE, API_GUARD_DROPIN,
                    'ExecStartPre=/usr/local/sbin/inventory-numbering-service-guard') and
                _loaded_guard_dropin(
                    PROXY, PROXY_GUARD_DROPIN,
                    'ExecStartPre=/usr/local/sbin/inventory-numbering-service-guard --proxy'))
    except OSError:
        return False


def _abort_helpers():
    library = NUMBERING_LIBRARY if (NUMBERING_LIBRARY / 'numbering_abort.py').is_file() else Path(__file__).resolve().parent
    sys.path.insert(0, str(library))
    try:
        import numbering_abort
        return numbering_abort
    except Exception:
        sys.path.pop(0)
        raise


def write_numbering_abort_baseline(rollback: Path, release: str, operation_id: str,
                                   release_sha: str, database: Path, revision: str,
                                   previous: dict, previous_sha256: str,
                                   old_api_sha256: str, old_venv_sha256: str,
                                   old_static_sha256: str, timers: list[str],
                                   proxy_was_active: bool) -> dict:
    helpers = _abort_helpers()
    try:
        sys.path.insert(0, str(NUMBERING_LIBRARY))
        try:
            from app.numbering_maintenance import atomic_json, database_identity
            from app.services.numbering_environment import file_binding
            previous_path = rollback / 'previous-deployed.json'
            if file_binding(previous_path)['sha256'] != previous_sha256:
                raise RuntimeError('Previous release marker copy changed during baseline capture')
            baseline = {
                'format': helpers.ABORT_BASELINE,
                'state': 'captured',
                'operation_id': operation_id,
                'release': release,
                'release_sha': release_sha,
                'database_identity_before_deploy': database_identity(database),
                'database_revision_before_deploy': revision,
                'database_schema_sha256': helpers.database_schema_digest(database),
                'previous_metadata': str(previous_path),
                'previous_metadata_sha256': previous_sha256,
                'previous_commit': previous['commit'],
                'old_api': str(rollback / 'api'),
                'old_api_tree_sha256': old_api_sha256,
                'old_venv': str(rollback / 'old-venv'),
                'old_venv_tree_sha256': old_venv_sha256,
                'old_static': str(rollback / 'old-static'),
                'old_static_tree_sha256': old_static_sha256,
                'timers_were_active': timers,
                'proxy_was_active': proxy_was_active,
                'captured_at_ns': time.time_ns(),
            }
            target = rollback / 'numbering-abort-baseline.json'
            if target.exists() or target.is_symlink():
                raise RuntimeError('Pre-commit abort baseline already exists')
            atomic_json(target, baseline, group=database.stat().st_gid)
            return baseline
        finally:
            sys.path.pop(0)
    finally:
        sys.path.pop(0)


def write_numbering_abort_bundle(rollback: Path, release: str, operation_id: str,
                                 release_sha: str, database: Path, baseline: dict,
                                 backup_directory: Path, *, ensure_venv_copy: bool = False) -> None:
    helpers = _abort_helpers()
    try:
        sys.path.insert(0, str(NUMBERING_LIBRARY))
        try:
            helpers.write_abort_bundle(
                helpers.ProductionAbortContext(database), rollback, release,
                operation_id, release_sha, baseline, backup_directory,
                ensure_venv_copy=ensure_venv_copy,
            )
        finally:
            sys.path.pop(0)
    finally:
        sys.path.pop(0)


def maintenance_held() -> bool:
    directory = DATA_DIR / 'numbering-maintenance'
    if not os.path.lexists(directory):
        return False
    try:
        if directory.is_symlink() or (directory / 'state.json').is_symlink():
            return True
        state = json.loads((directory / 'state.json').read_text())
        return state.get('format') != 'numbering-maintenance-v1' or state.get('state') != 'released'
    except (OSError, ValueError):
        return True


def deploy(release: str, digest: str, migrate: bool, operation_id: str | None = None) -> None:
    import fcntl
    if os.geteuid() != 0:
        raise RuntimeError('Run through the installed sudo command')
    if not re.fullmatch(r'[0-9]+-[0-9]+-[0-9a-f]{40}', release) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise RuntimeError('Invalid release ID or digest')
    if operation_id is not None and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{7,95}', operation_id):
        raise RuntimeError('Invalid numbering operation ID')
    lock = open('/run/inventory-actions.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    env = environment()
    if maintenance_held():
        raise RuntimeError('Numbering maintenance remains held; inspect and recover that operation, never redeploy automatically')
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
    abort_baseline = None
    if operation_id:
        if not numbering_abort_installed():
            raise RuntimeError('Pre-commit abort recovery is not installed; numbering hold was not started')
        helper = _abort_helpers()
        try:
            marker = PROJECT / '.actions-deployed.json'
            if marker.is_symlink() or not marker.is_file():
                raise RuntimeError('Previous deployment marker is required for numbering abort recovery')
            previous = json.loads(marker.read_text(encoding='utf8'))
            if not re.fullmatch(r'[0-9a-f]{40}', previous.get('commit', '')):
                raise RuntimeError('Previous deployment marker has no valid commit')
            if (api / '.venv').is_symlink() or not (api / '.venv').is_dir():
                raise RuntimeError('Previous virtual environment is not an independent directory')
            if STATIC.is_symlink() or not STATIC.is_dir():
                raise RuntimeError('Previous static output is not an independent directory')
            from app.services.numbering_environment import file_binding
            abort_baseline = {
                'previous': previous,
                'previous_sha256': file_binding(marker)['sha256'],
                'old_api_sha256': helper.api_items_digest(api),
                'old_venv_sha256': helper.tree_digest(api / '.venv'),
                'old_static_sha256': helper.tree_digest(STATIC),
                'proxy_was_active': active('nginx.service'),
            }
            if not abort_baseline['proxy_was_active']:
                raise RuntimeError('Nginx must be active before numbering hold to bind its recovery state')
        finally:
            sys.path.pop(0)
    work = PROJECT / '.actions-releases' / release
    if work.exists():
        raise RuntimeError('Release already attempted; rerun Actions for a new attempt ID')
    work.mkdir(parents=True)
    work.chmod(0o755)
    metadata = extract_release(INCOMING / (release + '.tar.gz'), work / 'payload', digest)
    if metadata['commit'] != release.split('-', 2)[2]:
        raise RuntimeError('Release commit does not match ID')
    payload = work / 'payload'
    cutover_committed = numbering_state(db)
    capable = numbering_capable(payload)
    if cutover_committed and not capable:
        raise RuntimeError('Numbering already committed: incompatible old code is forbidden')
    if operation_id and (not capable or cutover_committed):
        raise RuntimeError('Numbering hold requires reviewed compatible code and an uncommitted database')
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
    if not cutover_committed and (capable or revision['head'] == 'e3f4a5b6c7d8') and not operation_id:
        raise RuntimeError('First numbering release requires numbering-hold mode; schema migration cannot open writes')
    if needs_migration and not migrate:
        raise RuntimeError('Database migration required; use Run workflow with migrate=true')
    rollback = BACKUPS / release
    rollback.mkdir(parents=True, mode=0o750)
    (rollback / 'api').mkdir()
    if operation_id:
        command('chown', 'root:inventory', str(rollback))
        rollback.chmod(0o750)
    for item in API_ITEMS:
        source = api / item
        target = rollback / 'api' / item
        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target)
    if operation_id:
        shutil.copy2(PROJECT / '.actions-deployed.json', rollback / 'previous-deployed.json')
        shutil.copytree(STATIC, rollback / 'old-static', symlinks=True)
        if helper.api_items_digest(rollback / 'api') != abort_baseline['old_api_sha256']:
            raise RuntimeError('Previous API recovery copy failed verification')
        if helper.tree_digest(rollback / 'old-static') != abort_baseline['old_static_sha256']:
            raise RuntimeError('Previous static recovery copy failed verification')
    staged_static = STATIC.parent / ('.out-' + release)
    old_static = STATIC.parent / ('.old-out-' + release)
    old_venv = rollback / 'old-venv' if operation_id else work / 'old-venv'
    shutil.copytree(payload / 'out', staged_static)
    for path in (staged_static, *staged_static.rglob('*')):
        path.chmod(0o755 if path.is_dir() else 0o644)
    timers = [timer for timer in TIMERS if active(timer)]
    if operation_id:
        abort_baseline = write_numbering_abort_baseline(
            rollback, release, operation_id, metadata['commit'], db, revision['current'],
            abort_baseline['previous'], abort_baseline['previous_sha256'],
            abort_baseline['old_api_sha256'], abort_baseline['old_venv_sha256'],
            abort_baseline['old_static_sha256'], timers, abort_baseline['proxy_was_active'],
        )
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
        if operation_id:
            enter_numbering_hold(payload, db, operation_id, metadata['commit'])
        (rollback / 'full').mkdir()
        command('chown', 'inventory:inventory', str(rollback / 'full'))
        as_inventory(env, str(old_python), str(api / 'scripts/backup_inventory.py'), '--destination', str(rollback / 'full'))
        # Recheck the revision after stopping business writes.
        with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as connection:
            current = [r[0] for r in connection.execute('SELECT version_num FROM alembic_version')]
        if current != [revision['current']]:
            raise RuntimeError('Database revision changed during staging')
        if operation_id:
            backup_manifests = list((rollback / 'full').glob('*/manifest.json'))
            if len(backup_manifests) != 1:
                raise RuntimeError('Expected exactly one timestamped full backup set')
            write_numbering_abort_bundle(
                rollback, release, operation_id, metadata['commit'], db, abort_baseline,
                backup_manifests[0].parent,
            )
            (api / '.venv').rename(old_venv)
            command('chown', '-R', 'root:inventory', str(old_venv))
        changed = True
        for item in API_ITEMS:
            remove(api / item)
            source = payload / 'api' / item
            if source.is_dir():
                shutil.copytree(source, api / item)
            else:
                shutil.copy2(source, api / item)
        command('chown', '-R', 'inventory:inventory', *(str(api / item) for item in API_ITEMS))
        if operation_id:
            (api / '.venv').symlink_to(venv, target_is_directory=True)
        else:
            (api / '.venv').rename(old_venv)
            (api / '.venv').symlink_to(venv, target_is_directory=True)
        if needs_migration:
            migration_started = True
            # Alembic resolves relative script_location against this cwd.
            os.chdir(api)
            as_inventory(env, str(venv / 'bin/python'), '-m', 'alembic', 'upgrade', 'head')
        STATIC.rename(old_static)
        staged_static.rename(STATIC)
        if operation_id:
            (PROJECT / '.actions-numbering-staged.json').write_text(json.dumps({
                **metadata, 'release': release, 'operation_id': operation_id,
                'state': 'schema_staged_maintenance_held', 'backup': str(rollback / 'full')}, indent=2) + '\n')
            print('Schema and code staged; API and timers remain stopped. Historical numbering has NOT run.')
            succeeded = True
            return
        command('systemctl', 'start', SERVICE)
        health()
        (PROJECT / '.actions-deployed.json').write_text(json.dumps({**metadata, 'release': release}, indent=2) + '\n')
        succeeded = True
        print('Deployment succeeded: ' + metadata['commit'])
    except BaseException:
        if changed:
            command('systemctl', 'stop', SERVICE)
            if migration_started or operation_id or cutover_committed:
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
        elif stopped and not operation_id:
            command('systemctl', 'start', SERVICE)
        raise
    finally:
        # A failed migration needs inspection before backups resume.
        if not operation_id and (not migration_started or succeeded):
            for timer in timers:
                command('systemctl', 'start', timer)


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--capabilities']:
            print(json.dumps({'numbering_maintenance_protocol': 1,
                              'numbering_abort_protocol': int(numbering_abort_installed())}))
        else:
            if len(sys.argv) not in (4, 6) or sys.argv[3] not in ('true', 'false') or (len(sys.argv) == 6 and sys.argv[4] != '--numbering-hold'):
                raise RuntimeError('Usage: deploy-actions.py RELEASE_ID SHA256 true|false [--numbering-hold OPERATION_ID]')
            deploy(sys.argv[1], sys.argv[2], sys.argv[3] == 'true', sys.argv[5] if len(sys.argv) == 6 else None)
    except Exception as error:
        print('Deployment failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
