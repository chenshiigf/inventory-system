"""Linux-only integration: private synthetic files, real flock/fsync, fake systemctl.

No /run files or real units are created. The deployment-lock name is mapped to a
private test file, retaining the real descriptor/inode/flock validation.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from tests.test_numbering_v2_preflight import _setup, _snapshot

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    sys.platform != 'linux' or not hasattr(os, 'geteuid') or os.geteuid() != 0,
    reason='requires a separately available isolated Linux root test environment; never production',
)


@pytest.mark.parametrize('scenario', ['normal', 'journal', 'busy_ai_lock', 'queued_job', 'no_fd'])
def test_linux_snapshot_entry_with_inherited_lock_and_real_fsync(tmp_path, scenario):
    import fcntl

    business, visual, _ = _setup(tmp_path)
    (business.parent / 'uploads').mkdir()
    (business.parent / 'uploads/synthetic.jpg').write_bytes(b'SYNTHETIC ONLY')
    if scenario == 'journal':
        Path(str(business) + '-journal').write_bytes(b'SYNTHETIC JOURNAL; DO NOT REMOVE')
    before = _snapshot(business.parent)
    deployment_lock = tmp_path / 'deployment.lock'
    deployment_lock.write_bytes(b'0')
    approval = tmp_path / 'approval.json'
    approval.write_text(json.dumps({
        'operator': 'synthetic Linux operator', 'reference': 'OFFLINE ONLY',
        'maintenance_authorized': True, 'writers_reviewed': True, 'deploy_frozen': True,
        'inflight_settled': True, 'original_state_recorded': True, 'extra_units': [],
        'window_end_utc': (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }))
    commands = tmp_path / 'fake-bin'
    commands.mkdir()
    fake = commands / 'systemctl_mock.py'
    fake.write_text('''import os, sys
if sys.argv[1] == 'show':
    print('LoadState=loaded\\nActiveState=inactive\\nMainPID=0\\nResult=success\\nControlGroup=\\nExecMainCode=1\\nExecMainStatus=0')
elif sys.argv[1] == 'list-jobs':
    if os.environ['SCENARIO'] == 'queued_job':
        print('1 inventory-system-api.service start waiting')
else:
    raise SystemExit('real service actions forbidden in test')
''')
    wrapper = commands / 'systemctl'
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(fake)) + ' "$@"\n')
    wrapper.chmod(0o700)  # only this synthetic test command
    driver = tmp_path / 'driver.py'
    driver.write_text('''import fcntl, importlib.util, os, sqlite3, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('snapshot', os.environ['SNAPSHOT_SCRIPT'])
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
checked = tool.checked_path
def private_deploy_path(path, **kwargs):
    if str(path) == '/run/inventory-actions.lock':
        path = os.environ['SYNTHETIC_DEPLOY_LOCK']
    return checked(path, **kwargs)
tool.checked_path = private_deploy_path
real_sync = tool.sync_directory
def sync(path):
    real_sync(path)
    with open(os.environ['FSYNC_LOG'], 'a') as stream:
        stream.write(str(path) + '\\n')
tool.sync_directory = sync
real_connect = sqlite3.connect
def copy_sql_only(database, *args, **kwargs):
    assert str(database).startswith(Path(os.environ['OUTPUT']).as_uri() + '/')
    return real_connect(database, *args, **kwargs)
sqlite3.connect = copy_sql_only
if os.environ['SCENARIO'] != 'no_fd':
    os.fstat(9)  # descriptor was inherited from Bash, not opened by the driver
    with open(os.environ['SYNTHETIC_DEPLOY_LOCK'], 'rb') as probe:
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise AssertionError('Bash did not already hold the deployment lock')
try:
    tool.main()
except (OSError, ValueError, KeyError, TypeError):
    raise SystemExit(1)
''')
    output = tmp_path / 'snapshot'
    fsync_log = tmp_path / 'fsync-paths.txt'
    env = {'PATH': str(commands) + os.pathsep + os.defpath, 'PYTHONDONTWRITEBYTECODE': '1',
           'SCENARIO': scenario, 'SNAPSHOT_SCRIPT': str(ROOT / 'deploy/numbering-v2-c2-snapshot.py'),
           'SYNTHETIC_DEPLOY_LOCK': str(deployment_lock), 'OUTPUT': str(output), 'FSYNC_LOG': str(fsync_log)}
    argv = [sys.executable, '-B', str(driver), '--source', str(business.parent),
            '--output', str(output), '--approval', str(approval), '--deploy-lock-fd', '9']
    shell = '' if scenario == 'no_fd' else 'exec 9<' + shlex.quote(str(deployment_lock)) + '\nflock -n -x 9 || exit 90\n'
    shell += 'exec ' + shlex.join(argv)
    with (visual / 'calls.lock').open('rb') as busy:
        if scenario == 'busy_ai_lock':
            fcntl.flock(busy, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run([shutil.which('bash') or '/bin/bash', '-c', shell], env=env,
                                cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert _snapshot(business.parent) == before
    assert result.returncode == (0 if scenario == 'normal' else 1), result.stderr
    if scenario == 'normal':
        assert _snapshot(output / 'data') == before
        assert json.loads((output / 'COMPLETE.json').read_text())['state'] == 'snapshot_verified'
        assert str(output / 'data') in fsync_log.read_text().splitlines()
        for name in before:
            assert not os.path.samefile(business.parent / name, output / 'data' / name)
    else:
        assert not (output / 'COMPLETE.json').exists()
