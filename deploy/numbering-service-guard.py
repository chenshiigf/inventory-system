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
    return subprocess.run(['systemctl', 'is-active', '--quiet', PROXY], check=False).returncode == 0


def _systemd_service_is_loopback():
    if not shutil.which('systemctl'):
        raise RuntimeError('Cannot verify the API bind address')
    result = subprocess.run(['systemctl', 'show', SERVICE, '--property=ExecStart', '--value'],
                            capture_output=True, text=True, check=True)
    command = result.stdout
    return '--host 127.0.0.1 --port 8102' in command


def _verify_abort_probe(state, database, project, *, proxy_active, service_loopback, now_ns):
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
    if not service_loopback():
        raise RuntimeError('Abort probe API must bind only to loopback 127.0.0.1:8102')
    return True


def _verify_abort_resume(state, database, project, *, proxy_active, service_loopback, now_ns):
    resume = state.get('abort_resume')
    if (not isinstance(resume, dict) or
            resume.get('format') != 'numbering-abort-resume-v1' or
            resume.get('state') != 'authorized'):
        raise RuntimeError('Numbering maintenance remains held; normal API startup is forbidden')
    if (resume.get('operation_id') != state.get('operation_id') or
            resume.get('release_sha') != state.get('release_sha') or
            resume.get('database_identity') != _database_identity(database) or
            not re.fullmatch(r'[0-9a-f]{40}', resume.get('previous_commit', '')) or
            not re.fullmatch(r'[0-9a-f]{64}', resume.get('plan_sha256', '')) or
            type(resume.get('expires_at_ns')) is not int or resume['expires_at_ns'] <= now_ns):
        raise RuntimeError('Abort resume authorization expired or does not match this operation')
    if _deployment_commit(project) != resume['previous_commit']:
        raise RuntimeError('Abort resume does not match the restored old release')
    if proxy_active():
        raise RuntimeError('Nginx must remain stopped while the old API is resumed privately')
    if not service_loopback():
        raise RuntimeError('Abort resume API must bind only to loopback 127.0.0.1:8102')
    return True


def _verify_abort_release(state, database, data, project):
    receipt = state.get('abort_receipt')
    checks = {'cutover_not_committed': True, 'database_restored': True,
              'old_code_restored': True, 'api_readonly_probe': True,
              'inventory_readonly_checks': True, 'normal_api_health': True,
              'ai_unchanged': True, 'timers_restored': True,
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


def verify(data=DATA, capability=CAPABILITY, project=PROJECT, *, proxy_active=None,
           service_loopback=None, now_ns=None):
    database = data / 'inventory.db'
    if not database.is_file() or database.is_symlink():
        raise RuntimeError('Business database missing or aliased')
    directory = data / 'numbering-maintenance'
    abort_probe = False
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
        if state.get('state') == 'held':
            proxy_check = proxy_active or _systemd_proxy_active
            loopback_check = service_loopback or _systemd_service_is_loopback
            abort_probe = _verify_abort_probe(state, database, project,
                                              proxy_active=proxy_check,
                                              service_loopback=loopback_check,
                                              now_ns=time.time_ns() if now_ns is None else now_ns)
        elif state.get('state') == 'abort_resuming':
            proxy_check = proxy_active or _systemd_proxy_active
            loopback_check = service_loopback or _systemd_service_is_loopback
            abort_probe = _verify_abort_resume(state, database, project,
                                               proxy_active=proxy_check,
                                               service_loopback=loopback_check,
                                               now_ns=time.time_ns() if now_ns is None else now_ns)
        elif state.get('state') == 'released' and state.get('release_reason') == 'precommit_abort':
            _verify_abort_release(state, database, data, project)
            abort_release = True
        elif state.get('state') != 'released':
            raise RuntimeError('Numbering maintenance remains held; normal API startup is forbidden')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        registered = db.execute("SELECT count(*) FROM sqlite_master WHERE name='production_numbering_cutovers'").fetchone()[0]
        committed = registered and db.execute('SELECT count(*) FROM production_numbering_cutovers').fetchone()[0]
    if committed and (abort_probe or abort_release):
        raise RuntimeError('Numbering cutover registry is committed; abort startup is forbidden')
    if (os.path.lexists(directory) or committed) and not abort_probe and not abort_release:
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
        elif not sys.argv[1:]:
            verify()
        else:
            raise RuntimeError('Usage: numbering-service-guard [--proxy]')
    except Exception as error:
        print('Service start refused: ' + str(error), file=sys.stderr)
        sys.exit(1)
