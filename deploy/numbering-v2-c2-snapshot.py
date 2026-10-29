"""Frozen-file snapshot only. Never stops/starts services or opens source SQLite.

Preparation artifact: not installed or authorized to run on production.
"""
from contextlib import ExitStack, closing
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY / 'api'))
from app.services.numbering_io import binding, checked_path, connect, digest, existing_lock, integrity, no_sidecars
from scripts.numbering_v2_preflight import audit

UNITS = ('inventory-system-api.service', 'inventory-db-backup.timer',
         'inventory-full-backup.timer', 'inventory-db-backup.service', 'inventory-full-backup.service')
CONFIRMATIONS = ('maintenance_authorized', 'writers_reviewed', 'deploy_frozen',
                 'inflight_settled', 'original_state_recorded')


def frozen_units(extra):
    """Read explicit non-secret properties; a stopped timer alone is insufficient."""
    units = list(dict.fromkeys((*UNITS, *extra)))
    if any(not re.fullmatch(r'[A-Za-z0-9_@.-]+\.(?:service|timer)', u) or u.startswith('-') for u in units):
        raise ValueError('invalid unit name')
    states = {}
    for unit in units:
        text = subprocess.run(['systemctl', 'show', unit, '-p', 'LoadState', '-p', 'ActiveState',
                               '-p', 'MainPID', '-p', 'ControlGroup', '-p', 'Result',
                               '-p', 'ExecMainCode', '-p', 'ExecMainStatus'],
                              check=True, capture_output=True, text=True, timeout=10).stdout
        info = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        if (info.get('LoadState') != 'loaded' or info.get('ActiveState') != 'inactive' or
                info.get('MainPID', '0') != '0' or info.get('Result', 'success') != 'success'):
            raise ValueError('unit not cleanly stopped: ' + unit)
        if info.get('ExecMainCode') == '2' and info.get('ExecMainStatus') == '9':
            raise ValueError('unit was killed rather than drained: ' + unit)
        group = info.get('ControlGroup', '')
        if group:
            if not group.startswith('/') or '..' in Path(group).parts:
                raise ValueError('invalid cgroup')
            root = Path('/sys/fs/cgroup') / group.lstrip('/')
            for procs in root.rglob('cgroup.procs'):
                if procs.read_text().strip():
                    raise ValueError('cgroup still populated: ' + unit)
        states[unit] = info
    jobs = subprocess.run(['systemctl', 'list-jobs', '--no-legend', '--no-pager'],
                          check=True, capture_output=True, text=True, timeout=10).stdout
    if any(len(line.split()) > 1 and line.split()[1] in units for line in jobs.splitlines()):
        raise ValueError('reviewed unit has a queued job')
    return states


def deployment_guard(fd):
    """Use the same open description held by the operator before stopping writers."""
    import fcntl
    path = checked_path('/run/inventory-actions.lock', existing=True)
    actual, expected = os.fstat(fd), path.stat()
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError('wrong inherited deployment lock')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def inventory(root):
    """Closed scope, no links/special files; do not hash obvious credential files."""
    roots = ['inventory.db', 'visual-search', 'uploads']
    if any(root.glob('inventory.db-mj*')):
        raise ValueError('business super-journal exists')
    if os.path.lexists(root / 'import-previews'):
        roots.append('import-previews')
    if not (root / 'inventory.db').is_file() or any(not (root / name).is_dir() for name in roots[1:]):
        raise ValueError('required database/directory type mismatch')
    result = {}

    def visit(path):
        checked_path(path)
        info = path.lstat()
        if path.name == '.env' or path.name.startswith('.env.') or path.name in {
                '.ssh', 'credentials', 'id_rsa', 'id_ed25519'} or path.suffix.lower() in {'.pem', '.key'}:
            raise ValueError('credential-like path requires separate review')
        entry = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid,
                 'mtime_ns': info.st_mtime_ns, 'inode': info.st_ino, 'device': info.st_dev}
        if stat.S_ISDIR(info.st_mode):
            entry['kind'] = 'directory'
            result[path.relative_to(root).as_posix()] = entry
            for child in sorted(path.iterdir()):
                visit(child)
        elif stat.S_ISREG(info.st_mode):
            if path.name.endswith(('-wal', '-shm', '-journal')) or re.search(r'-mj[0-9a-fA-F]+$', path.name):
                raise ValueError('SQLite sidecar exists')
            no_sidecars(path)
            entry.update(kind='file', **{k: v for k, v in binding(path).items() if k in {'size', 'sha256'}})
            result[path.relative_to(root).as_posix()] = entry
        else:
            raise ValueError('special file')

    for name in roots:
        visit(root / name)
    return result


def payload(items):
    return {name: {k: v for k, v in entry.items() if k in {'kind', 'size', 'sha256'}}
            for name, entry in items.items()}


def save(path, value):
    with path.open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    if os.name == 'posix':
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def copy_file(source, target):
    with source.open('rb') as reader, os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())


def create_snapshot(source, output, approval, *, deploy_fd=9, reserve_bytes=1024**3, reserve_inodes=1024):
    source, output = checked_path(source), checked_path(output)
    if (source.is_relative_to(output) or output.is_relative_to(source) or
            output.is_relative_to(REPOSITORY) or output.exists()):
        raise ValueError('destination must be new, outside source and repository')
    if any(approval.get(k) is not True for k in CONFIRMATIONS) or not approval.get('operator') or not approval.get('reference'):
        raise ValueError('missing reviewed maintenance confirmations')
    if not isinstance(approval.get('extra_units'), list):
        raise ValueError('reviewed extra unit list required, including uploaders if installed')
    deadline = datetime.fromisoformat(approval['window_end_utc'])
    if deadline.utcoffset() is None or deadline <= datetime.now(timezone.utc):
        raise ValueError('maintenance deadline expired or missing timezone')
    if reserve_bytes < 0 or reserve_inodes < 0:
        raise ValueError('negative reserve')
    deployment_guard(deploy_fd)
    before_units = frozen_units(approval['extra_units'])
    visual = source / 'visual-search'
    with ExitStack() as stack:
        for lock in (visual / 'calls.lock', visual / 'production-catalog/calls.lock'):
            stack.enter_context(existing_lock(lock))
        if os.path.lexists(visual / 'runtime/ai-search.enabled'):
            raise ValueError('AI is still enabled')
        started = datetime.now(timezone.utc).isoformat()
        before = inventory(source)
        required = sum(v.get('size', 0) for v in before.values())
        if shutil.disk_usage(output.parent).free < required + reserve_bytes:
            raise OSError('insufficient disk space')
        if hasattr(os, 'statvfs'):
            if os.statvfs(output.parent).f_favail < len(before) + reserve_inodes:
                raise OSError('insufficient inodes')
        output.mkdir(mode=0o700)
        save(output / 'approval.json', approval)
        save(output / 'source-before.json', before)
        target = output / 'data'
        target.mkdir(mode=0o700)
        for name, entry in before.items():
            if datetime.now(timezone.utc) >= deadline:
                raise TimeoutError('maintenance deadline reached')
            path = target / name
            if entry['kind'] == 'directory':
                path.mkdir(mode=0o700)
            else:
                copy_file(source / name, path)
        copied = inventory(target)
        if payload(copied) != payload(before):
            raise ValueError('copy hash or file-set mismatch')
        report = audit(target / 'inventory.db', target / 'visual-search')
        save(output / 'preflight.json', report)
        if report['blockers']:
            raise ValueError('offline preflight blocked; see private report')
        identity = json.loads((target / 'visual-search/production-catalog/source.json').read_text(encoding='utf8'))
        databases = {'business': target / 'inventory.db',
                     'catalog': target / 'visual-search/production-catalog/catalog.sqlite',
                     'budget': target / 'visual-search/daily-budget.sqlite3',
                     'index': target / 'visual-search/production-indices' / ('production-' + digest(identity)) / 'index.db'}
        for path in databases.values():
            with closing(connect(path)) as db:
                integrity(db)
        with closing(connect(databases['budget'])) as db:
            if db.execute("SELECT count(*) FROM call_reservations WHERE state='reserved'").fetchone()[0]:
                raise ValueError('unsettled AI reservations in offline copy')
        with closing(connect(databases['index'])) as db:
            if db.execute("SELECT count(*) FROM visual_jobs WHERE status='running'").fetchone()[0]:
                raise ValueError('running AI jobs in offline copy')
        after_copy = inventory(target)
        after = inventory(source)
        if before != after or copied != after_copy:
            raise ValueError('source or snapshot changed during verification')
        after_units = frozen_units(approval['extra_units'])
        if os.path.lexists(visual / 'runtime/ai-search.enabled'):
            raise ValueError('AI re-enabled during snapshot')
        if datetime.now(timezone.utc) >= deadline:
            raise TimeoutError('maintenance deadline reached')
        save(output / 'source-after.json', after)
        save(output / 'copied.json', after_copy)
        for directory in reversed([target, *(target / n for n, v in before.items() if v['kind'] == 'directory')]):
            sync_directory(directory)
        result = {'state': 'snapshot_verified', 'consistency': 'REQUIRES_HUMAN_FREEZE_REVIEW',
                  'freeze_observed_start_utc': started, 'freeze_observed_end_utc': datetime.now(timezone.utc).isoformat(),
                  'units_before': before_units, 'units_after': after_units,
                  'sha256': {key: binding(path)['sha256'] for key, path in databases.items()},
                  'files': sum(v['kind'] == 'file' for v in before.values()), 'bytes': required,
                  'evidence_sha256': {name: binding(output / name)['sha256'] for name in
                                      ('approval.json', 'source-before.json', 'source-after.json', 'copied.json', 'preflight.json')},
                  'source_metadata_preserved_in': 'source-before.json',
                  'snapshot_permissions': 'private new directories/files; source permissions unchanged'}
        save(output / 'COMPLETE.json', result)
        sync_directory(output)
        sync_directory(output.parent)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source', 'output', 'approval'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--deploy-lock-fd', type=int, default=9)
    args = parser.parse_args()
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('CLI requires the separately approved existing root maintenance session on Linux')
    approval = json.loads(checked_path(args.approval, existing=True).read_text(encoding='utf8'))
    result = create_snapshot(args.source, args.output, approval, deploy_fd=args.deploy_lock_fd)
    print(json.dumps({'state': result['state'], 'consistency': result['consistency']}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Details belong in private evidence, never dump business records to stdout.
        print(json.dumps({'state': 'stopped', 'error_type': type(error).__name__, 'reason': str(error)}), file=sys.stderr)
        raise SystemExit(1)
