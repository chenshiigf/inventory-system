"""Harness guardrails, with no private catalog, real credentials or network calls."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest


@pytest.fixture
def benchmark():
    path = Path(__file__).resolve().parents[1] / "scripts/benchmark_visual_resources.py"
    spec = importlib.util.spec_from_file_location("benchmark_visual_resources", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_outputs_cannot_target_source_or_reuse_root(benchmark, tmp_path):
    root = tmp_path / "ignored"
    root.mkdir()
    assert benchmark.private_destination(root / "new", root) == root / "new"
    for path in (root, tmp_path / "original-index", root / "../outside"):
        with pytest.raises(ValueError):
            benchmark.private_destination(path, root)


def test_environment_never_inherits_real_credentials(benchmark, monkeypatch, tmp_path):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "do-not-inherit")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///original-business.db")
    monkeypatch.setenv("INVENTORY_HTPASSWD_FILE", "original.htpasswd")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-inherit")
    env = benchmark.clean_environment(tmp_path)
    assert not env["DASHSCOPE_API_KEY"] and not env["INVENTORY_HTPASSWD_FILE"]
    assert "UNRELATED_SECRET" not in env
    assert str(tmp_path.as_posix()) in env["DATABASE_URL"]
    assert env["OPENBLAS_NUM_THREADS"] == "2"


def test_protection_manifest_skips_junctions_and_credentials(benchmark, monkeypatch, tmp_path):
    root = tmp_path / "api/data/visual-search"
    root.mkdir(parents=True)
    (root / "labels.json").write_text("preserve")
    (root / ".htpasswd").write_text("do not read")
    linked = root / "linked-photos"
    linked.mkdir()
    (linked / "outside.txt").write_text("do not traverse")
    monkeypatch.setattr(benchmark, "ROOT", tmp_path)
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda p: p == linked or original(p))
    result = benchmark.protection_manifest()
    expected = str(Path("api/data/visual-search/labels.json"))
    assert list(result) == [expected]


def test_sqlite_snapshot_is_consistent_and_does_not_overwrite(benchmark, tmp_path):
    source, target = tmp_path / "source.db", tmp_path / "copy.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE example(id INTEGER)")
        db.execute("INSERT INTO example VALUES (42)")
        db.commit()
        benchmark.sqlite_snapshot(source, target)
        assert db.execute("SELECT * FROM example").fetchall() == [(42,)]
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT * FROM example").fetchall() == [(42,)]
        db.execute("UPDATE example SET id=99")
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT * FROM example").fetchall() == [(42,)]
    with pytest.raises(ValueError, match="overwrite"):
        benchmark.sqlite_snapshot(source, target)


def test_external_sockets_dns_and_udp_are_blocked_before_connection(benchmark):
    # A separate interpreter keeps the permanent audit hook out of pytest.
    code = '''
import importlib.util, socket, sys
s=importlib.util.spec_from_file_location('b',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
m.block_external_network()
tests=[lambda: socket.getaddrinfo('dashscope.aliyuncs.com',443),
lambda: socket.socket().connect(('203.0.113.1',443)),
lambda: socket.socket(type=socket.SOCK_DGRAM).sendto(b'x',('203.0.113.1',443))]
for test in tests:
    try: test()
    except PermissionError: pass
    else: raise AssertionError('network escaped offline guard')
assert socket.getaddrinfo('127.0.0.1',1234)
print('blocked')
'''
    result = subprocess.run([sys.executable, "-B", "-c", code, benchmark.__file__], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "blocked" in result.stdout


def test_percentiles_are_observed_values(benchmark):
    assert benchmark.stats([]) is None
    result = benchmark.stats(list(range(1, 31)))
    assert result["n"] == 30 and result["p50_ms"] == 15.5 and result["p95_ms"] == 29


def test_retry_uses_fresh_output_and_preserves_completed_case(benchmark, tmp_path):
    original = tmp_path / "case"
    original.mkdir()
    (original / "result.json").write_text("preserve")
    candidate = benchmark.unused_path(original)
    assert candidate == tmp_path / "case-attempt-2" and not candidate.exists()
    assert (original / "result.json").read_text() == "preserve"


@pytest.mark.skipif(os.name != "nt", reason="Windows-only OS cap")
def test_os_limit_precedes_image_decode_and_parent_kills_on_close(benchmark, monkeypatch, tmp_path):
    from PIL import Image
    root = tmp_path / "ignored"
    root.mkdir()
    image = tmp_path / "tiny.png"
    Image.new("RGB", (100, 50), "red").save(image)
    monkeypatch.setattr(benchmark, "OUTPUT_ROOT", root)
    # private_destination's default is captured at definition time.
    original = benchmark.private_destination
    monkeypatch.setattr(benchmark, "private_destination", lambda path: original(path, root))
    result = benchmark.run_child({"mode": "image", "api_path": str(Path(__file__).resolve().parents[1]), "image": str(image)},
                                 root / "child", cap_mib=128, timeout=20, floor_mib=512)
    assert result["exit_code"] == 0 and result["worker"]["status"] == 200
    assert 0 < result["job_peak_commit_mib"] < 128
    assert result["worker"]["size"] == [100, 50]
    first = result["worker"]["events"][0]
    assert first["verified_cap_mib"] == 128 and first["logical_cpus"] == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows-only OS cap")
def test_job_rejects_allocation_above_limit(benchmark):
    # Do not allocate until assigned to the job. An unsafe/no-op cap would fail.
    child = subprocess.Popen([sys.executable, "-B", "-c",
        "import sys;sys.stdin.readline();\ntry: x=bytearray(160*1024*1024)\nexcept MemoryError: print('limited',flush=True)\nelse: print('escaped',flush=True)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    job = None
    try:
        job = benchmark.WindowsJob(child, 96)
        stdout, stderr = child.communicate("go\n", timeout=10)
        assert child.returncode == 0 and "limited" in stdout, stderr
        assert job.peak() <= 96
    finally:
        if job:
            job.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only OS cap")
def test_closing_job_terminates_its_test_process(benchmark):
    child = subprocess.Popen([sys.executable, "-B", "-c", "import time;time.sleep(30)"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    job = None
    try:
        job = benchmark.WindowsJob(child, 96)
        assert child.poll() is None
        started = time.perf_counter()
        job.close()
        # Windows kill-on-close may report exit code 0. Timely termination of a
        # live 30-second sleeper proves the behavior without assuming a code.
        assert child.wait(timeout=5) is not None
        assert time.perf_counter() - started < 5
    finally:
        if job:
            job.close()
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_worker_refuses_to_run_without_os_limit_handshake(benchmark, tmp_path):
    result = subprocess.run([sys.executable, "-B", benchmark.__file__, "--worker", str(tmp_path / "nonexistent.json")],
                            input="", text=True, capture_output=True, timeout=10)
    assert result.returncode != 0 and "resource-limit handshake" in result.stderr
