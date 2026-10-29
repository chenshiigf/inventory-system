"""Real SIGTERM on isolated synthetic app processes; no manager or model calls.

An expected >90s observation passes the risk-detection test, never the production
safety criterion. A forcibly cleaned process is always a failed experiment.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='real POSIX SIGTERM requires isolated Linux')
PROBE = Path(__file__).with_name('c2_sigterm_probe.py')


def events(root):
    path = root / 'events.jsonl'
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding='utf8').splitlines():
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # the writer can be completing the final line while observed
    return result


@pytest.mark.parametrize('scenario', [
    'idle', 'busy_import', 'busy_index', 'busy_mirror_index', 'stalled_import', 'stalled_index',
])
def test_real_sigterm_lifespan_and_synthetic_integrity(tmp_path, scenario):
    (tmp_path / 'synthetic-only').write_text('C2 TEST ONLY')
    data = tmp_path / 'data'
    env = {'PATH': os.defpath, 'HOME': str(tmp_path), 'PYTHONDONTWRITEBYTECODE': '1',
           'LD_LIBRARY_PATH': str(Path(sys.base_prefix) / 'lib'),
           'INVENTORY_DATA_DIR': str(data), 'DATABASE_URL': 'sqlite:///' + (data / 'inventory.db').as_posix()}
    forced_cleanup = False
    with (tmp_path / 'child.log').open('w', encoding='utf8') as log:
        child = subprocess.Popen([sys.executable, '-B', str(PROBE), str(tmp_path), scenario],
                                 cwd=tmp_path, env=env, start_new_session=True, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while True:
                if child.poll() is not None:
                    pytest.fail('probe exited before ready: ' + (tmp_path / 'child.log').read_text(encoding='utf8'))
                names = [e['event'] for e in events(tmp_path)]
                if (tmp_path / 'ready.json').exists() and (scenario == 'idle' or 'work_started' in names):
                    break
                if time.monotonic() > deadline:
                    pytest.fail('synthetic probe startup timed out')
                time.sleep(.02)
            ready = json.loads((tmp_path / 'ready.json').read_text(encoding='utf8'))
            assert ready == {'pid': child.pid, 'pgid': child.pid, 'synthetic': True}
            assert os.getpgid(child.pid) == child.pid
            sent = time.monotonic()
            child.send_signal(signal.SIGTERM)
            risky = scenario.startswith('stalled_')
            release_at = sent + (90.5 if risky else .5)
            if scenario != 'idle':
                while time.monotonic() < release_at:
                    if child.poll() is not None:
                        if scenario == 'busy_import':
                            break  # cancellation checkpoints can finish immediately
                        pytest.fail('worker unexpectedly exited before its synthetic release')
                    time.sleep(.05)
                if risky:
                    names = [e['event'] for e in events(tmp_path)]
                    assert child.poll() is None and 'shutdown_complete' not in names
                    assert ('preview_close_begin' if scenario == 'stalled_import' else 'visual_close_begin') in names
                (tmp_path / 'release-work').write_text('release synthetic work; not a forced kill')
            code = child.wait(timeout=15)
            elapsed = time.monotonic() - sent
            assert code in {0, -signal.SIGTERM}, (tmp_path / 'child.log').read_text(encoding='utf8')
        finally:
            if child.poll() is None:
                # This process group was created above, contains only this probe,
                # and is cleaned solely on a failed/aborted isolated experiment.
                assert os.getpgid(child.pid) == child.pid
                forced_cleanup = True
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
    assert not forced_cleanup, 'forced cleanup must never count as safe shutdown'
    names = [e['event'] for e in events(tmp_path)]
    required = ['preview_close_begin', 'preview_close_end', 'visual_close_begin', 'visual_close_end',
                'sources_close_begin', 'sources_close_end', 'shutdown_complete']
    assert all(name in names for name in required)
    assert [names.index(n) for n in required] == sorted(names.index(n) for n in required)
    assert (elapsed > 90) == risky
    if not risky:
        assert elapsed < 15
    fixture = json.loads((tmp_path / 'fixtures.json').read_text(encoding='utf8'))
    for relative in fixture['databases']:
        path = tmp_path / relative
        assert not any(Path(str(path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal'))
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            assert db.execute('PRAGMA foreign_key_check').fetchall() == []
            if path.name != 'index.db':
                assert db.execute('SELECT * FROM c2_sentinel').fetchall() == [(1, 123)]
            else:
                assert db.execute('SELECT status,processed,succeeded FROM visual_jobs').fetchall() == [('completed', 1, 1)]
    assert hashlib.sha256((data / 'inventory.db').read_bytes()).hexdigest() == fixture['business_sha256']
    result = {'scenario': scenario, 'seconds_after_sigterm': round(elapsed, 3),
              'within_90_seconds': not risky, 'risk_observed': risky,
              'forced_cleanup': forced_cleanup, 'sqlite_integrity': 'ok',
              'business_sha256_unchanged': True, 'python': fixture['python'], 'sqlite': fixture['sqlite']}
    print('C2_SIGTERM_RESULT ' + json.dumps(result, sort_keys=True))
