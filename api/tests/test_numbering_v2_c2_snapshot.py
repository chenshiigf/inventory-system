"""Synthetic files + mocked systemd only. No SSH, source SQL or service changes."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from tests.test_numbering_v2_preflight import _setup, _snapshot

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def case(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('c2_snapshot', ROOT / 'deploy/numbering-v2-c2-snapshot.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    business, visual, catalog = _setup(tmp_path)
    source = business.parent
    (source / 'uploads').mkdir()
    (source / 'uploads/sample.jpg').write_bytes(b'SYNTHETIC IMAGE')
    (visual / 'manifests').mkdir()
    events = []

    @contextmanager
    def lock(path):
        tool.checked_path(path, existing=True)
        events.append('lock:' + path.relative_to(source).as_posix())
        try:
            yield
        finally:
            events.append('unlock:' + path.relative_to(source).as_posix())

    def inspect(extra):
        events.append('inspect')
        return {u: {'ActiveState': 'inactive', 'MainPID': '0'} for u in (*tool.UNITS, *extra)}

    monkeypatch.setattr(tool, 'deployment_guard', lambda fd: events.append('deployment_guard'))
    monkeypatch.setattr(tool, 'existing_lock', lock)
    monkeypatch.setattr(tool, 'frozen_units', inspect)
    monkeypatch.setattr(tool.shutil, 'disk_usage', lambda path: SimpleNamespace(free=100 * 1024**3))
    approval = {k: True for k in tool.CONFIRMATIONS}
    approval.update(operator='synthetic operator', reference='OFFLINE ONLY', extra_units=[],
                    window_end_utc=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
    return SimpleNamespace(tool=tool, source=source, business=business, visual=visual, catalog=catalog,
                           out=tmp_path / 'new-set', approval=approval, events=events)


def run(c):
    return c.tool.create_snapshot(c.source, c.out, c.approval)


def test_snapshot_keeps_every_input_and_copies_four_databases(case, monkeypatch):
    c = case
    before = _snapshot(c.source)
    real_connect = sqlite3.connect

    def copy_connections_only(database, *args, **kwargs):
        assert str(database).startswith(c.out.as_uri() + '/'), 'must never open source SQL'
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', copy_connections_only)
    real_copy = c.tool.copy_file

    def copy(source, target):
        assert c.events[:4] == ['deployment_guard', 'inspect', 'lock:visual-search/calls.lock',
                                'lock:visual-search/production-catalog/calls.lock']
        c.events.append('copy')
        real_copy(source, target)

    monkeypatch.setattr(c.tool, 'copy_file', copy)
    report = run(c)
    assert report['state'] == 'snapshot_verified'
    assert report['consistency'] == 'REQUIRES_HUMAN_FREEZE_REVIEW'
    assert set(report['sha256']) == {'business', 'catalog', 'index', 'budget'}
    assert before == _snapshot(c.source) == _snapshot(c.out / 'data')
    assert (c.out / 'data/visual-search/manifests').is_dir()
    assert (c.out / 'COMPLETE.json').is_file()
    assert c.events[-3:] == ['inspect', 'unlock:visual-search/production-catalog/calls.lock',
                             'unlock:visual-search/calls.lock']
    for name in before:
        assert not os.path.samefile(c.source / name, c.out / 'data' / name)


@pytest.mark.parametrize('confirmation', ['writers_reviewed', 'deploy_frozen', 'inflight_settled',
                                        'maintenance_authorized', 'original_state_recorded'])
def test_missing_human_gate_never_starts_copy(case, confirmation):
    case.approval[confirmation] = False
    with pytest.raises(ValueError):
        run(case)
    assert not case.out.exists()
    assert not case.events


@pytest.mark.parametrize('suffix', ['-wal', '-shm', '-journal', '-mj1234abcd'])
def test_logs_are_preserved_and_block_before_output(case, suffix):
    path = Path(str(case.business) + suffix)
    path.write_bytes(b'SYNTHETIC LOG')
    before = _snapshot(case.source)
    with pytest.raises(ValueError):
        run(case)
    assert before == _snapshot(case.source)
    assert not case.out.exists()


@pytest.mark.parametrize('failure', ['enabled', 'lock', 'space', 'expired', 'credential', 'missing'])
def test_pre_copy_failure_never_changes_source(case, monkeypatch, failure):
    c = case
    if failure == 'enabled':
        (c.visual / 'runtime').mkdir()
        (c.visual / 'runtime/ai-search.enabled').write_text('enabled\n')
    elif failure == 'lock':
        def busy(path):
            raise ValueError('busy synthetic lock')
        monkeypatch.setattr(c.tool, 'existing_lock', busy)
    elif failure == 'space':
        monkeypatch.setattr(c.tool.shutil, 'disk_usage', lambda path: SimpleNamespace(free=0))
    elif failure == 'expired':
        c.approval['window_end_utc'] = '2000-01-01T00:00:00+00:00'
    elif failure == 'credential':
        (c.visual / '.env').write_bytes(b'SYNTHETIC PLACEHOLDER')
    else:
        (c.visual / 'calls.lock').unlink()
    before = _snapshot(c.source)
    with pytest.raises((OSError, ValueError)):
        run(c)
    assert before == _snapshot(c.source)
    assert not c.out.exists()


@pytest.mark.parametrize('failure', ['io', 'target_hash', 'source_drift', 'source_new_file', 'binding', 'damaged_db', 'runtime'])
def test_incomplete_set_is_retained_without_success_marker(case, monkeypatch, failure):
    c = case
    before = _snapshot(c.source)
    real_copy = c.tool.copy_file
    changed = False

    def copy(source, target):
        nonlocal changed
        if failure == 'io':
            raise OSError('synthetic disk failure')
        real_copy(source, target)
        if not changed and source.name == 'sample.jpg':
            changed = True
            if failure == 'target_hash':
                target.write_bytes(b'CORRUPT COPY')
            elif failure == 'source_drift':
                source.write_bytes(b'SYNTHETIC ROGUE WRITE')
            elif failure == 'source_new_file':
                (c.source / 'uploads/late.jpg').write_bytes(b'SYNTHETIC LATE FILE')
    monkeypatch.setattr(c.tool, 'copy_file', copy)
    if failure == 'binding':
        binding = next((c.visual / 'production-indices').glob('*/source-binding.json'))
        binding.write_text('{}')
        before = _snapshot(c.source)
    elif failure == 'damaged_db':
        c.business.write_bytes(b'SYNTHETIC INVALID DB')
        before = _snapshot(c.source)
    elif failure == 'runtime':
        count = 0
        def inspect(extra):
            nonlocal count
            count += 1
            if count == 2:
                raise ValueError('synthetic writer restarted')
            return {}
        monkeypatch.setattr(c.tool, 'frozen_units', inspect)
    with pytest.raises((OSError, ValueError)):
        run(c)
    assert c.out.is_dir()
    assert not (c.out / 'COMPLETE.json').exists()
    assert (c.out / 'source-before.json').is_file()
    if failure not in {'source_drift', 'source_new_file'}:
        assert before == _snapshot(c.source)


def test_existing_backup_is_not_overwritten(case):
    case.out.mkdir()
    marker = case.out / 'keep'
    marker.write_bytes(b'OLD SYNTHETIC BACKUP')
    with pytest.raises(ValueError):
        run(case)
    assert marker.read_bytes() == b'OLD SYNTHETIC BACKUP'
    assert list(case.out.iterdir()) == [marker]


def test_hard_link_is_blocked(case):
    os.link(case.source / 'uploads/sample.jpg', case.source / 'uploads/linked.jpg')
    with pytest.raises(ValueError, match='硬链接'):
        run(case)
    assert not case.out.exists()


@pytest.mark.parametrize('state', ['active', 'activating', 'failed'])
def test_actual_state_reader_rejects_unquiet_unit(case, monkeypatch, state):
    # Restore the real reader; subprocess remains mocked, never systemctl on this host.
    spec = importlib.util.spec_from_file_location('c2_reader', ROOT / 'deploy/numbering-v2-c2-snapshot.py')
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    commands = []
    def command(args, **kwargs):
        commands.append(args)
        return SimpleNamespace(stdout=f'LoadState=loaded\nActiveState={state}\nMainPID=10\nResult=success\n')
    monkeypatch.setattr(reader.subprocess, 'run', command)
    with pytest.raises(ValueError):
        reader.frozen_units([])
    assert commands[0][:2] == ['systemctl', 'show']


def test_inode_exhaustion(case, monkeypatch):
    monkeypatch.setattr(case.tool.os, 'statvfs', lambda path: SimpleNamespace(f_favail=0), raising=False)
    with pytest.raises(OSError, match='inodes'):
        run(case)
    assert not case.out.exists()


@pytest.mark.parametrize('failure', ['cgroup', 'queued_job', 'killed'])
def test_state_reader_checks_children_jobs_and_abnormal_exit(tmp_path, monkeypatch, failure):
    spec = importlib.util.spec_from_file_location('c2_guard', ROOT / 'deploy/numbering-v2-c2-snapshot.py')
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    procs = tmp_path / 'synthetic-cgroup.procs'
    procs.write_text('123\n')
    monkeypatch.setattr(tool.Path, 'rglob', lambda path, pattern: [procs] if failure == 'cgroup' else [])
    def command(args, **kwargs):
        if args[1] == 'list-jobs':
            return SimpleNamespace(stdout='1 inventory-system-api.service start waiting\n')
        status = ('LoadState=loaded\nActiveState=inactive\nMainPID=0\nResult=success\n'
                  'ControlGroup=/synthetic-cgroup\n')
        if failure == 'killed':
            status += 'ExecMainCode=2\nExecMainStatus=9\n'
        return SimpleNamespace(stdout=status)
    monkeypatch.setattr(tool.subprocess, 'run', command)
    with pytest.raises(ValueError):
        tool.frozen_units([])


@pytest.mark.parametrize('kind', ['reserved', 'running'])
def test_unfinished_ai_work_blocks_on_copy_without_fixing_ledger(case, kind):
    if kind == 'reserved':
        path = case.visual / 'daily-budget.sqlite3'
        sql = "UPDATE call_reservations SET state='reserved'"
    else:
        path = next((case.visual / 'production-indices').glob('*/index.db'))
        sql = "INSERT INTO visual_jobs VALUES ('synthetic-job','synthetic-key','running')"
    with sqlite3.connect(path) as db:
        db.execute(sql)
    before = _snapshot(case.source)
    with pytest.raises(ValueError, match='offline copy'):
        run(case)
    assert _snapshot(case.source) == before
    assert not (case.out / 'COMPLETE.json').exists()


def test_wal_header_without_sidecar_blocks(case):
    content = bytearray(case.business.read_bytes())
    content[18:20] = b'\x02\x02'
    case.business.write_bytes(content)
    before = _snapshot(case.source)
    with pytest.raises(ValueError, match='WAL'):
        run(case)
    assert before == _snapshot(case.source)
    assert not case.out.exists()


def test_read_only_catalog_permissions_are_recorded_without_changing_source(case):
    path = case.catalog / 'catalog.sqlite'
    path.chmod(0o640)
    mode = path.stat().st_mode
    try:
        run(case)
        assert path.stat().st_mode == mode
        before = json.loads((case.out / 'source-before.json').read_text(encoding='utf8'))
        assert before['visual-search/production-catalog/catalog.sqlite']['mode'] == mode & 0o7777
    finally:
        path.chmod(0o600)  # synthetic fixture only, permits pytest cleanup on Windows


@pytest.mark.parametrize('scenario,expected', [
    ('normal', 0), ('source_unknown', 40), ('start_failed', 42), ('health_failed', 44),
    ('timer_inactive', 0), ('catchup_pending', 50), ('catchup_approved', 0),
    ('ai_not_restored', 51),
])
def test_documented_recovery_guards_with_mock_services(tmp_path, scenario, expected):
    bash = shutil.which('bash')
    if not bash:
        pytest.skip('bash unavailable; no real service commands are substituted')
    doc = (ROOT / 'docs/numbering-v2-c2-maintenance.md').read_text(encoding='utf8')
    functions = doc.split('<!-- recovery-functions -->\n```bash\n', 1)[1].split('\n```', 1)[0]
    script = functions + '''
systemctl() {
  printf '%s\n' "$*" >> "$LOG"
  if [ "$SCENARIO" = start_failed ] && [ "$1" = start ]; then return 1; fi
  return 0
}
curl() { printf 'health\n' >> "$LOG"; [ "$SCENARIO" != health_failed ]; }
SOURCE_SAFE=yes
C2_STATE_CAPTURED=yes
API_WAS_ACTIVE=yes
AI_STATE_RESTORED=yes
DB_TIMER_WAS_ACTIVE=yes
FULL_TIMER_WAS_ACTIVE=no
DB_TIMER_DECISION=not-missed
FULL_TIMER_DECISION=pending
case "$SCENARIO" in
  source_unknown) SOURCE_SAFE=no ;;
  timer_inactive) DB_TIMER_WAS_ACTIVE=no ;;
  catchup_pending) DB_TIMER_DECISION=pending ;;
  catchup_approved) DB_TIMER_DECISION=catchup-approved ;;
  ai_not_restored) AI_STATE_RESTORED=no ;;
esac
c2_resume_api && c2_restore_timer inventory-db-backup.timer
'''
    log = tmp_path / 'mock-commands.txt'
    env = {**os.environ, 'SCENARIO': scenario, 'LOG': str(log).replace('\\', '/')}
    result = subprocess.run([bash, '-c', script], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == expected, result.stderr
    commands = log.read_text().splitlines() if log.exists() else []
    if scenario == 'source_unknown':
        assert commands == []
    if scenario in {'start_failed', 'health_failed', 'catchup_pending', 'timer_inactive', 'ai_not_restored'}:
        assert not any('start inventory-db-backup.timer' in command for command in commands)
    if scenario in {'normal', 'catchup_approved'}:
        assert commands.index('health') < commands.index('start inventory-db-backup.timer')


@pytest.mark.parametrize('scenario,expected', [('enabled', 0), ('disabled', 0), ('conflict', 55), ('bad_evidence', 56)])
def test_documented_ai_marker_restore_preserves_original_bytes(tmp_path, scenario, expected):
    bash = shutil.which('bash')
    if not bash:
        pytest.skip('bash unavailable')
    doc = (ROOT / 'docs/numbering-v2-c2-maintenance.md').read_text(encoding='utf8')
    functions = doc.split('<!-- recovery-functions -->\n```bash\n', 1)[1].split('\n```', 1)[0]
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    saved = evidence / 'ai-search.enabled'
    saved.write_bytes(b'enabled\n' if scenario != 'bad_evidence' else b'INVALID SYNTHETIC MARKER')
    marker = tmp_path / 'runtime/ai-search.enabled'
    marker.parent.mkdir()
    if scenario == 'conflict':
        marker.write_bytes(b'DO NOT OVERWRITE')
    original = saved.read_bytes()
    env = {**os.environ, 'EVIDENCE': evidence.as_posix(), 'FLAG': marker.as_posix(),
           'AI_WAS_ENABLED': 'no' if scenario == 'disabled' else 'yes', 'API_HEALTHY': 'yes'}
    result = subprocess.run([bash, '-c', functions + '\nc2_restore_ai_marker\n'],
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == expected, result.stderr
    assert saved.read_bytes() == original
    if scenario == 'enabled':
        assert marker.read_bytes() == original
        assert (evidence / 'ai-restored-sha256.txt').is_file()
    elif scenario == 'conflict':
        assert marker.read_bytes() == b'DO NOT OVERWRITE'
    else:
        assert not marker.exists()


@pytest.mark.parametrize('api,db,full,expected', [
    ('active', 'active', 'inactive', 0), ('active', 'inactive', 'active', 0),
    ('active', 'active', 'active', 0), ('active', 'inactive', 'inactive', 0),
    ('inactive', 'active', 'active', 10), ('active', 'activating', 'active', 11),
    ('active', 'active', 'failed', 12),
])
def test_original_state_is_captured_per_unit_before_changes(tmp_path, api, db, full, expected):
    bash = shutil.which('bash')
    if not bash:
        pytest.skip('bash unavailable')
    doc = (ROOT / 'docs/numbering-v2-c2-maintenance.md').read_text(encoding='utf8')
    functions = doc.split('<!-- capture-functions -->\n```bash\n', 1)[1].split('\n```', 1)[0]
    script = functions + '''
systemctl() {
  [ "$1" = show ] || return 99
  case "$2" in
    inventory-system-api.service) current=$API_INITIAL ;;
    inventory-db-backup.timer) current=$DB_INITIAL ;;
    inventory-full-backup.timer) current=$FULL_INITIAL ;;
    *) return 99 ;;
  esac
  if [ "$4" = ActiveState ]; then printf '%s\n' "$current"; return; fi
  case "$current:$2" in
    active:inventory-system-api.service) printf 'running\n' ;;
    active:*) printf 'waiting\n' ;;
    inactive:*) printf 'dead\n' ;;
    *) printf 'start\n' ;;
  esac
}
c2_capture_original_state || exit $?
[ "$SOURCE_SAFE:$API_STOP_POLICY:$DB_TIMER_DECISION:$FULL_TIMER_DECISION" = no:BLOCKED:pending:pending ] || exit 98
( DB_TIMER_WAS_ACTIVE=no ) 2>/dev/null
[ "$?" -ne 0 ] || exit 97
( FULL_TIMER_WAS_ACTIVE=yes ) 2>/dev/null
[ "$?" -ne 0 ] || exit 96
'''
    env = {**os.environ, 'C2_STATE_CAPTURED': 'no', 'API_INITIAL': api, 'DB_INITIAL': db, 'FULL_INITIAL': full,
           'EVIDENCE': tmp_path.as_posix()}
    result = subprocess.run([bash, '-c', script], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == expected, result.stderr
    if expected == 0:
        assert (tmp_path / 'original-api-active.txt').read_text().strip() == 'yes'
        assert (tmp_path / 'original-db-timer-active.txt').read_text().strip() == ('yes' if db == 'active' else 'no')
        assert (tmp_path / 'original-full-timer-active.txt').read_text().strip() == ('yes' if full == 'active' else 'no')


@pytest.mark.parametrize('target,db,full,db_decision,full_decision,expected,started', [
    ('db', 'no', 'yes', 'pending', 'not-missed', 0, False),
    ('full', 'yes', 'no', 'not-missed', 'pending', 0, False),
    ('db', 'yes', 'yes', 'pending', 'catchup-approved', 50, False),
    ('full', 'yes', 'yes', 'catchup-approved', 'pending', 50, False),
    ('db', 'yes', 'no', 'catchup-approved', 'pending', 0, True),
    ('full', 'no', 'yes', 'pending', 'not-missed', 0, True),
])
def test_timer_never_inherits_other_timer_state_or_decision(tmp_path, target, db, full, db_decision, full_decision, expected, started):
    bash = shutil.which('bash')
    if not bash:
        pytest.skip('bash unavailable')
    doc = (ROOT / 'docs/numbering-v2-c2-maintenance.md').read_text(encoding='utf8')
    functions = doc.split('<!-- recovery-functions -->\n```bash\n', 1)[1].split('\n```', 1)[0]
    script = functions + '''
systemctl() { printf '%s\n' "$*" >> "$LOG"; }
# Deliberately stale legacy variables must have no effect.
TIMER_WAS_ACTIVE=yes
TIMER_DECISION=catchup-approved
c2_restore_timer "$TARGET"
'''
    unit = 'inventory-db-backup.timer' if target == 'db' else 'inventory-full-backup.timer'
    log = tmp_path / 'commands.txt'
    env = {**os.environ, 'C2_STATE_CAPTURED': 'yes', 'SOURCE_SAFE': 'yes', 'API_HEALTHY': 'yes',
           'AI_STATE_RESTORED': 'yes', 'DB_TIMER_WAS_ACTIVE': db, 'FULL_TIMER_WAS_ACTIVE': full,
           'DB_TIMER_DECISION': db_decision, 'FULL_TIMER_DECISION': full_decision,
           'LOG': log.as_posix(), 'TARGET': unit}
    result = subprocess.run([bash, '-c', script], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == expected, result.stderr
    commands = log.read_text().splitlines() if log.exists() else []
    assert commands == ([f'start {unit}', f'is-active --quiet {unit}'] if started else [])


@pytest.mark.parametrize('scenario,expected', [('blocked', 60), ('empty_evidence', 60), ('not_captured', 60), ('approved_mock', 0)])
def test_stop_cannot_be_used_as_policy_experiment(tmp_path, scenario, expected):
    bash = shutil.which('bash')
    if not bash:
        pytest.skip('bash unavailable')
    doc = (ROOT / 'docs/numbering-v2-c2-maintenance.md').read_text(encoding='utf8')
    functions = doc.split('<!-- capture-functions -->\n```bash\n', 1)[1].split('\n```', 1)[0]
    evidence = tmp_path / 'synthetic-stop-review.txt'
    evidence.write_text('OFFLINE MOCK APPROVAL' if scenario != 'empty_evidence' else '')
    log = tmp_path / 'commands.txt'
    script = functions + '\nsystemctl() { printf "%s\\n" "$*" >> "$LOG"; }\nc2_stop_api\n'
    env = {**os.environ, 'C2_STATE_CAPTURED': 'no' if scenario == 'not_captured' else 'yes',
           'API_STOP_POLICY': 'BLOCKED' if scenario == 'blocked' else 'PASS',
           'API_STOP_EVIDENCE': evidence.as_posix(), 'LOG': log.as_posix()}
    result = subprocess.run([bash, '-c', script], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == expected, result.stderr
    assert (log.read_text().splitlines() if log.exists() else []) == (
        ['stop inventory-system-api.service'] if expected == 0 else [])


@pytest.mark.parametrize('daily_rc,decision,expected', [('1', 'not-missed', 1), ('0', 'pending', 50), ('0', 'not-missed', 0)])
def test_weekly_restore_cannot_continue_after_daily_failure(tmp_path, daily_rc, decision, expected):
    bash = shutil.which('bash')
    if not bash:
        pytest.skip('bash unavailable')
    doc = (ROOT / 'docs/numbering-v2-c2-maintenance.md').read_text(encoding='utf8')
    functions = doc.split('<!-- recovery-functions -->\n```bash\n', 1)[1].split('\n```', 1)[0]
    weekly = doc.split('<!-- weekly-timer-restore -->\n```bash\n', 1)[1].split('\n```', 1)[0]
    log = tmp_path / 'commands.txt'
    env = {**os.environ, 'C2_STATE_CAPTURED': 'yes', 'SOURCE_SAFE': 'yes', 'API_HEALTHY': 'yes',
           'AI_STATE_RESTORED': 'yes', 'DB_TIMER_RESTORE_RC': daily_rc, 'FULL_TIMER_RESTORE_RC': '1',
           'FULL_TIMER_WAS_ACTIVE': 'yes', 'FULL_TIMER_DECISION': 'pending',
           'LOG': log.as_posix(), 'EVIDENCE': tmp_path.as_posix()}
    script = functions + '\nsystemctl() { printf "%s\\n" "$*" >> "$LOG"; }\n' + weekly
    script += '\nexit "$FULL_TIMER_RESTORE_RC"\n'
    # Binary stdin avoids Windows translating the LF into CRLF for Bash read.
    result = subprocess.run([bash, '-c', script], env=env, input=(decision + '\n').encode('utf8'),
                            capture_output=True, timeout=15)
    assert result.returncode == expected, result.stderr
    commands = log.read_text().splitlines() if log.exists() else []
    assert commands == (['start inventory-full-backup.timer', 'is-active --quiet inventory-full-backup.timer'] if expected == 0 else [])
