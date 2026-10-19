#!/usr/bin/python3
"""Root-installed ExecStartPre guard; independent of the replaceable API code."""
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time

DATA = Path('/var/lib/inventory-system')
CAPABILITY = Path('/srv/inventory-system/api/app/numbering-v2.json')
PROJECT = Path('/srv/inventory-system')
SERVICE = 'inventory-system-api.service'
PROXY = 'nginx.service'


def _database_identity(database):
    info = database.stat()
    return {'path': str(database.resolve()), 'device': info.st_dev, 'inode': info.st_ino}


def _deployment_commit(project):
    marker = project / '.actions-deployed.json'
    if marker.is_symlink() or not marker.is_file():
        raise RuntimeError('Previous release marker is missing or aliased')
    value = json.loads(marker.read_text())
    if not re.fullmatch(r'[0-9a-f]{40}', value.get('commit', '')):
        raise RuntimeError('Previous release marker has no valid commit')
    return value['commit']


def _systemd_proxy_active():
    if not shutil.which('systemctl'):
        raise RuntimeError('Cannot verify that the web proxy is stopped')
    try:
        result = subprocess.run(
            ['systemctl', 'show', PROXY, '--property=LoadState,ActiveState', '--no-pager'],
            capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('Cannot query Nginx state; API/probe startup is forbidden') from error
    if result.returncode != 0:
        raise RuntimeError('Cannot query Nginx state; API/probe startup is forbidden')
    properties = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition('=')
        if not separator or key in properties or key not in {'LoadState', 'ActiveState'}:
            raise RuntimeError('Nginx state output is invalid; startup is forbidden')
        properties[key] = value
    if properties.get('LoadState') != 'loaded':
        raise RuntimeError('Nginx unit is not loaded; startup is forbidden')
    active = properties.get('ActiveState')
    if active in {'inactive', 'failed'}:
        return False
    if active in {'active', 'activating', 'deactivating', 'reloading'}:
        return True
    raise RuntimeError('Nginx state is unknown; startup is forbidden')


def _verify_abort_probe(state, database, project, *, proxy_active, now_ns):
    probe = state.get('abort_probe')
    if not isinstance(probe, dict) or probe.get('format') != 'numbering-abort-probe-v1' or probe.get('state') != 'authorized':
        raise RuntimeError('Numbering maintenance remains held; normal API startup is forbidden')
    if (probe.get('operation_id') != state.get('operation_id') or
            probe.get('release_sha') != state.get('release_sha') or
            probe.get('database_identity') != _database_identity(database) or
            not re.fullmatch(r'[0-9a-f]{40}', probe.get('previous_commit', '')) or
            not re.fullmatch(r'[0-9a-f]{64}', probe.get('plan_sha256', '')) or
            type(probe.get('expires_at_ns')) is not int or probe['expires_at_ns'] <= now_ns):
        raise RuntimeError('Abort probe authorization expired or does not match this operation')
    if _deployment_commit(project) != probe['previous_commit']:
        raise RuntimeError('Abort probe does not match the restored old release')
    if proxy_active():
        raise RuntimeError('Nginx must remain stopped during the private abort probe')
    return True


def verify_readonly_probe(data=DATA, project=PROJECT, *, proxy_active=None, now_ns=None):
    """Authorize only the no-listener child probe, never a systemd API start."""
    database = data / 'inventory.db'
    directory = data / 'numbering-maintenance'
    state_file = directory / 'state.json'
    if (database.is_symlink() or not database.is_file() or directory.is_symlink() or
            state_file.is_symlink() or not state_file.is_file()):
        raise RuntimeError('Read probe database or maintenance marker is missing or aliased')
    state = json.loads(state_file.read_text())
    if (state.get('format') != 'numbering-maintenance-v1' or
            state.get('state') not in {'held', 'abort_resuming'} or
            not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{7,95}', state.get('operation_id', '')) or
            not re.fullmatch(r'[0-9a-f]{40}', state.get('release_sha', '')) or
            state.get('database_identity') != _database_identity(database)):
        raise RuntimeError('Read probe maintenance binding is invalid')
    _verify_abort_probe(state, database, project,
                        proxy_active=proxy_active or _systemd_proxy_active,
                        now_ns=time.time_ns() if now_ns is None else now_ns)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        registered = db.execute("SELECT count(*) FROM sqlite_master WHERE name='production_numbering_cutovers'").fetchone()[0]
        if registered and db.execute('SELECT count(*) FROM production_numbering_cutovers').fetchone()[0]:
            raise RuntimeError('Numbering cutover registry is committed; abort probe is forbidden')


def _verify_abort_release(state, database, data, project):
    receipt = state.get('abort_receipt')
    checks = {'cutover_not_committed': True, 'database_restored': True,
              'old_code_restored': True, 'api_readonly_probe': True,
              'inventory_readonly_checks': True, 'isolated_api_health': True,
              'normal_api_stopped': True, 'ai_unchanged': True, 'timers_stopped': True,
              'proxy_stopped_until_release': True}
    if (state.get('release_reason') != 'precommit_abort' or not isinstance(receipt, dict) or
            receipt.get('format') != 'numbering-abort-receipt-v1' or receipt.get('state') != 'complete' or
            receipt.get('operation_id') != state.get('operation_id') or
            receipt.get('release_sha') != state.get('release_sha') or
            receipt.get('database_identity') != _database_identity(database) or
            not re.fullmatch(r'[0-9a-f]{64}', receipt.get('plan_sha256', '')) or
            not re.fullmatch(r'[0-9a-f]{64}', receipt.get('backup_manifest_sha256', '')) or
            not re.fullmatch(r'[0-9a-f]{40}', receipt.get('previous_commit', '')) or
            receipt.get('checks') != checks):
        raise RuntimeError('Aborted numbering release receipt is incomplete or mismatched')
    if _deployment_commit(project) != receipt.get('previous_commit'):
        raise RuntimeError('Restored source does not match the approved previous release')
    validation_path = data / 'numbering-maintenance' / 'abort-validation.json'
    if validation_path.is_symlink() or not validation_path.is_file():
        raise RuntimeError('Aborted numbering release has no durable pre-release validation')
    try:
        validation = json.loads(validation_path.read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError('Aborted numbering release validation is unreadable') from error
    if (validation.get('format') != 'numbering-abort-validation-v1' or
            validation.get('state') != 'ready_to_release' or
            validation.get('operation_id') != receipt.get('operation_id') or
            validation.get('release_sha') != receipt.get('release_sha') or
            validation.get('database_identity') != receipt.get('database_identity') or
            validation.get('plan_sha256') != receipt.get('plan_sha256') or
            validation.get('previous_commit') != receipt.get('previous_commit') or
            validation.get('checks') != checks):
        raise RuntimeError('Aborted numbering release validation is incomplete or mismatched')
    return True


def verify(data=DATA, capability=CAPABILITY, project=PROJECT):
    database = data / 'inventory.db'
    if not database.is_file() or database.is_symlink():
        raise RuntimeError('Business database missing or aliased')
    directory = data / 'numbering-maintenance'
    abort_release = False
    if os.path.lexists(directory):
        # Hold all normal service starts, including reboot. Even compatible API
        # lifespan can initialize AI state; controlled read-only acceptance is a
        # separate 3B-2 prerequisite, never an implicit restart here.
        state_file = directory / 'state.json'
        if directory.is_symlink() or state_file.is_symlink():
            raise RuntimeError('Maintenance marker is aliased')
        state = json.loads(state_file.read_text())
        if state.get('format') != 'numbering-maintenance-v1':
            raise RuntimeError('Maintenance marker format is invalid')
        if state.get('state') in {'held', 'abort_resuming'}:
            # Even a valid legacy probe/resume marker cannot authorize this
            # ordinary service. A reboot loses no necessary runtime override.
            raise RuntimeError('Numbering maintenance remains held; normal API startup is forbidden')
        elif state.get('state') == 'released' and state.get('release_reason') == 'precommit_abort':
            _verify_abort_release(state, database, data, project)
            abort_release = True
        elif state.get('state') != 'released':
            raise RuntimeError('Numbering maintenance remains held; normal API startup is forbidden')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        registered = db.execute("SELECT count(*) FROM sqlite_master WHERE name='production_numbering_cutovers'").fetchone()[0]
        committed = registered and db.execute('SELECT count(*) FROM production_numbering_cutovers').fetchone()[0]
    if committed and abort_release:
        raise RuntimeError('Numbering cutover registry is committed; abort startup is forbidden')
    if (os.path.lexists(directory) or committed) and not abort_release:
        if capability.is_symlink() or json.loads(capability.read_text()) != {
                'scheme': 'product-numbering-v2', 'maintenance_protocol': 1}:
            raise RuntimeError('Maintenance or committed numbering forbids starting incompatible old code')


def verify_proxy(data=DATA, capability=CAPABILITY, project=PROJECT):
    """Keep Nginx down until a complete, operation-bound release is durable."""
    directory = data / 'numbering-maintenance'
    if os.path.lexists(directory):
        state_file = directory / 'state.json'
        if directory.is_symlink() or state_file.is_symlink() or not state_file.is_file():
            raise RuntimeError('Maintenance marker is missing or aliased; Nginx startup is forbidden')
        try:
            state = json.loads(state_file.read_text())
        except (OSError, ValueError) as error:
            raise RuntimeError('Maintenance marker is unreadable; Nginx startup is forbidden') from error
        if state.get('format') != 'numbering-maintenance-v1' or state.get('state') in {'held', 'abort_resuming'}:
            raise RuntimeError('Numbering maintenance remains held; Nginx startup is forbidden')
    verify(data, capability, project)


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--proxy']:
            verify_proxy()
        elif sys.argv[1:] == ['--abort-probe']:
            # Verification only; this command cannot start the normal service.
            verify_readonly_probe()
        elif not sys.argv[1:]:
            verify()
        else:
            raise RuntimeError('Usage: numbering-service-guard [--proxy|--abort-probe]')
    except Exception as error:
        print('Service start refused: ' + str(error), file=sys.stderr)
        sys.exit(1)
