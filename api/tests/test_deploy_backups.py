"""Deployment templates only: temporary SQLite and mocked OSS, no cloud calls."""

from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
import re
import runpy
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
NOW = datetime(2026, 10, 1, 19, 8, 39)


@pytest.fixture
def daily():
    return runpy.run_path(str(DEPLOY / "inventory-db-backup.py.example"))


def source_db(tmp_path: Path) -> Path:
    source = tmp_path / "source" / "inventory.db"
    source.parent.mkdir()
    connection = sqlite3.connect(source)
    try:
        connection.executescript("CREATE TABLE products (id INTEGER PRIMARY KEY); INSERT INTO products VALUES (1);")
        connection.commit()
    finally:
        connection.close()
    return source


def test_daily_snapshot_matches_production_manifest_and_preserves_source(tmp_path, daily):
    source = source_db(tmp_path)
    original = source.read_bytes()
    result = daily["backup_database"](source, tmp_path / "db", now=NOW)
    manifest = json.loads((result / "manifest.json").read_text())
    assert result.name == "2026-10-01_190839"
    assert set(manifest) == {"created_at", "source", "database", "size", "sha256", "integrity_check"}
    assert manifest["created_at"] == "2026-10-01T19:08:39"
    assert manifest["source"] == str(source.resolve())
    assert manifest["database"] == "inventory.db"
    assert manifest["size"] == (result / "inventory.db").stat().st_size
    assert manifest["sha256"] == hashlib.sha256((result / "inventory.db").read_bytes()).hexdigest()
    assert manifest["integrity_check"] == "ok"
    assert set(p.name for p in result.iterdir()) == {"inventory.db", "manifest.json"}
    assert source.read_bytes() == original
    assert list((tmp_path / "db").iterdir()) == [result]


def test_daily_backup_includes_committed_wal_rows(tmp_path, daily):
    source = source_db(tmp_path)
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("INSERT INTO products VALUES (2)")
        writer.commit()
        result = daily["backup_database"](source, tmp_path / "db", now=NOW)
        reader = sqlite3.connect((result / "inventory.db").as_uri() + "?mode=ro", uri=True)
        try:
            assert reader.execute("SELECT id FROM products ORDER BY id").fetchall() == [(1,), (2,)]
        finally:
            reader.close()
    finally:
        writer.close()


@pytest.mark.parametrize("failure", ["missing", "invalid", "hash", "rename"])
def test_failed_daily_backup_does_not_publish_or_change_source(tmp_path, daily, monkeypatch, failure):
    source = source_db(tmp_path)
    if failure == "missing":
        source = tmp_path / "missing.db"
    elif failure == "invalid":
        source.write_bytes(b"not a database")
    original = source.read_bytes() if source.exists() else None
    globals_ = daily["backup_database"].__globals__

    def fail(*args, **kwargs):
        raise OSError("injected failure")

    if failure == "hash":
        monkeypatch.setitem(globals_, "sha256", fail)
    elif failure == "rename":
        monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises((ValueError, OSError, sqlite3.Error)):
        daily["backup_database"](source, tmp_path / "db", now=NOW)
    assert not (tmp_path / "db").exists() or not list((tmp_path / "db").iterdir())
    assert (source.read_bytes() if source.exists() else None) == original


def test_existing_timestamp_is_never_overwritten(tmp_path, daily):
    source = source_db(tmp_path)
    first = daily["backup_database"](source, tmp_path / "db", now=NOW)
    before = {p.name: p.read_bytes() for p in first.iterdir()}
    with pytest.raises(FileExistsError):
        daily["backup_database"](source, tmp_path / "db", now=NOW)
    assert {p.name: p.read_bytes() for p in first.iterdir()} == before


def old_set(root: Path, name: str, *, days: int, full: bool = False) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "inventory.db").write_bytes(b"old snapshot")
    manifest = {"database": "inventory.db", "integrity_check": "ok"}
    if full:
        manifest = {"backup_version": 1, "database_filename": "inventory.db"}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    age = (NOW - timedelta(days=days)).timestamp()
    os.utime(directory, (age, age))
    return directory


def test_retention_only_removes_old_completed_daily_sets(tmp_path, daily):
    destination = (tmp_path / "db").resolve()
    old = old_set(destination, "2026-09-01_000000", days=30)
    recent = old_set(destination, "2026-09-25_000000", days=6)
    full = old_set(destination, "2026-08-01_000000", days=61, full=True)
    incomplete = old_set(destination, ".2026-08-01_000000.tmp", days=61)
    unrelated = old_set(destination, "unrelated", days=61)
    daily["prune_backups"](destination, NOW, 14)
    assert not old.exists()
    assert all(p.is_dir() for p in (recent, full, incomplete, unrelated))


def test_retention_failure_logs_and_keeps_new_backup(tmp_path, daily, monkeypatch, caplog):
    source = source_db(tmp_path)
    old = old_set(tmp_path / "db", "2026-09-01_000000", days=30)

    def fail(path):
        raise PermissionError("injected cleanup failure")

    monkeypatch.setattr(shutil, "rmtree", fail)
    with caplog.at_level(logging.WARNING):
        result = daily["backup_database"](source, tmp_path / "db", now=NOW)
    assert old.exists() and (result / "manifest.json").is_file()
    assert "retention cleanup" in caplog.text


def test_daily_cli_reports_failure_for_missing_source(tmp_path, daily):
    assert daily["main"](["--source", str(tmp_path / "missing.db"), "--destination", str(tmp_path / "db")]) == 1
    assert not (tmp_path / "db").exists()


def test_daily_cli_returns_success_when_retention_scan_fails(tmp_path, daily, monkeypatch, caplog):
    source = source_db(tmp_path)
    destination = tmp_path / "db"

    def fail(*args):
        raise PermissionError("injected retention directory scan failure")

    monkeypatch.setitem(daily["backup_database"].__globals__, "prune_backups", fail)
    with caplog.at_level(logging.WARNING):
        status = daily["main"](["--source", str(source), "--destination", str(destination)])
    assert status == 0
    assert len(list(destination.glob("*/manifest.json"))) == 1
    assert "DB backup succeeded; retention cleanup failed" in caplog.text


def bash_executable() -> str:
    candidates = [shutil.which("bash"), r"D:\biancheng\Git\bin\bash.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("Bash is not installed; run template checks on Ubuntu")


def shell_path(path: Path) -> str:
    value = path.as_posix()
    if os.name == "nt":
        cygpath = Path(bash_executable()).parents[1] / "usr" / "bin" / "cygpath.exe"
        return subprocess.check_output([str(cygpath), "-u", value], encoding="utf-8").strip()
    return value


@pytest.mark.parametrize("backup_type", ["db", "full"])
@pytest.mark.parametrize("oss_exit", [0, 9])
@pytest.mark.parametrize("complete", [True, False])
def test_uploader_uses_latest_correct_role_flags_and_never_changes_backups(tmp_path, backup_type, oss_exit, complete):
    backups, work = tmp_path / "backups", tmp_path / "work"
    root = backups / backup_type
    for name in ["2026-09-30_040000", "2026-10-01_040000", ".2026-10-02_040000.tmp"]:
        directory = root / name
        directory.mkdir(parents=True)
        for filename in ["inventory.db", "manifest.json", "uploads.tar.gz"]:
            (directory / filename).write_text(filename)
    if not complete:
        missing = "uploads.tar.gz" if backup_type == "full" else "manifest.json"
        (root / "2026-10-01_040000" / missing).unlink()
    before = {p.relative_to(backups): p.read_bytes() for p in backups.rglob("*") if p.is_file()}
    script = tmp_path / "upload.sh"
    text = (DEPLOY / "inventory-oss-upload.sh.example").read_text()
    text = text.replace('/var/backups/inventory-system/${backup_type}', shell_path(backups) + '/${backup_type}')
    text = text.replace("/var/tmp/inventory-ossutil", shell_path(work))
    script.write_text(text, newline="\n")
    capture = tmp_path / "args.txt"
    bash_env = tmp_path / "mock.bash"
    bash_env.write_text('ossutil() { printf "%s\\n" "$@" > "$CAPTURE_ARGS"; return "$OSS_EXIT"; }\n', newline="\n")
    env = {**os.environ, "BASH_ENV": shell_path(bash_env), "CAPTURE_ARGS": shell_path(capture), "OSS_EXIT": str(oss_exit)}
    bash = bash_executable()
    if os.name == "nt":
        # Prefer GNU find/sort over Windows' unrelated find.exe.
        env["PATH"] = str(Path(bash).parents[1] / "usr" / "bin") + os.pathsep + env["PATH"]
    result = subprocess.run([bash, script.as_posix(), backup_type], env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == (oss_exit if complete else 1), result.stderr
    assert {p.relative_to(backups): p.read_bytes() for p in backups.rglob("*") if p.is_file()} == before
    if not complete:
        assert not capture.exists()
        return
    args = capture.read_text().splitlines()
    assert args[:2] == ["cp", "-r"]
    assert args[2] == shell_path(root / "2026-10-01_040000") + "/"
    assert args[3] == f"oss://inventory-system-backup/{backup_type}/2026-10-01_040000/"
    assert args[args.index("--mode") + 1] == "EcsRamRole"
    assert args[args.index("--ecs-role-name") + 1] == "InventorySystemBackupRole"
    assert args[args.index("-e") + 1] == "oss-cn-hangzhou-internal.aliyuncs.com"
    assert args[args.index("--output-dir") + 1] == shell_path(work / "output")
    assert args[args.index("--checkpoint-dir") + 1] == shell_path(work / "checkpoint")
    assert args[-1] == "-f"
    assert {p.relative_to(backups): p.read_bytes() for p in backups.rglob("*") if p.is_file()} == before


def documentation_python(marker: str) -> str:
    doc = (ROOT / "docs/disaster-recovery.md").read_text(encoding="utf-8")
    for block in re.findall(r"<<'PY'\n(.*?)\nPY", doc, re.S):
        if marker in block:
            return block
    raise AssertionError(f"Missing documentation validation snippet: {marker}")


@pytest.mark.parametrize("corrupt", [False, True])
def test_documented_full_manifest_check_uses_real_backup_fields(tmp_path, monkeypatch, corrupt):
    from app.inventory_backup import create_backup

    source = source_db(tmp_path)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "sample.webp").write_bytes(b"sample image")
    result = create_backup(tmp_path / "full", database_url=f"sqlite:///{source.as_posix()}", uploads_directory=uploads)
    if corrupt:
        (result.backup_directory / "uploads.tar.gz").write_bytes(b"corrupted archive")
    monkeypatch.setattr(sys, "argv", ["check", str(result.backup_directory)])
    code = compile(documentation_python("Unexpected full manifest format"), "documented full check", "exec")
    if corrupt:
        with pytest.raises(SystemExit, match="Size/hash mismatch: uploads.tar.gz"):
            exec(code, {})
    else:
        exec(code, {})


@pytest.mark.parametrize("missing", [False, True])
def test_documented_daily_check_validates_manifest_and_missing_shared_images(tmp_path, daily, monkeypatch, missing):
    source = source_db(tmp_path)
    db = sqlite3.connect(source)
    try:
        db.executescript("ALTER TABLE products ADD image_path TEXT; ALTER TABLE products ADD thumbnail_path TEXT; INSERT INTO products (id) VALUES (2);")
        db.execute("UPDATE products SET image_path='products/main/shared.webp', thumbnail_path='products/thumbs/shared.webp'")
        db.commit()
    finally:
        db.close()
    uploads = tmp_path / "uploads"
    for subdir in ["main", "thumbs"]:
        folder = uploads / "products" / subdir
        folder.mkdir(parents=True)
        (folder / "shared.webp").write_bytes(b"shared image")
    if missing:
        (uploads / "products/main/shared.webp").unlink()
    result = daily["backup_database"](source, tmp_path / "daily", now=NOW)
    monkeypatch.setattr(sys, "argv", ["check", str(result), str(uploads)])
    code = compile(documentation_python("Daily DB manifest mismatch"), "documented daily check", "exec")
    if missing:
        with pytest.raises(SystemExit, match="Cannot combine this DB"):
            exec(code, {})
    else:
        exec(code, {})


@pytest.mark.parametrize("arguments", [[], ["other"], ["db", "full"]])
def test_uploader_rejects_invalid_arguments_before_cloud_calls(arguments):
    result = subprocess.run([bash_executable(), (DEPLOY / "inventory-oss-upload.sh.example").as_posix(), *arguments], capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 2


def unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read(DEPLOY / name, encoding="utf-8")
    return parser


def test_units_and_documented_paths_match_confirmed_production():
    for kind, calendar in [("db", "*-*-* 03:30:00 Asia/Shanghai"), ("full", "Sun *-*-* 04:00:00 Asia/Shanghai")]:
        timer = unit(f"inventory-{kind}-backup.timer.example")
        assert timer["Timer"]["OnCalendar"] == calendar
        assert timer["Timer"]["Persistent"] == "true"
        assert timer["Timer"]["Unit"] == f"inventory-{kind}-backup.service"
        service = unit(f"inventory-{kind}-backup.service.example")
        assert service["Service"]["User"] == service["Service"]["Group"] == "inventory"
        assert service["Service"]["UMask"] == "0027"
        dropin = unit(f"inventory-{kind}-backup.service.d/oss-upload.conf.example")
        assert dropin["Unit"]["OnSuccess"] == f"inventory-oss-upload@{kind}.service"
    full = unit("inventory-full-backup.service.example")
    assert full["Service"]["ExecStartPost"] == "-/usr/bin/find /var/backups/inventory-system/full -mindepth 1 -maxdepth 1 -type d -mtime +56 -exec /usr/bin/rm -rf {} +"
    assert full["Service"]["ExecStart"] == "/srv/inventory-system/api/.venv/bin/python /srv/inventory-system/api/scripts/backup_inventory.py --destination /var/backups/inventory-system/full"
    assert full["Service"]["EnvironmentFile"] == "/etc/inventory-system.env"
    uploader = unit("inventory-oss-upload@.service.example")
    assert uploader["Service"]["ExecStart"] == "/usr/local/sbin/inventory-oss-upload.sh %i"
    assert uploader["Service"]["WorkingDirectory"] == "/var/tmp/inventory-ossutil"
    doc = (ROOT / "docs/disaster-recovery.md").read_text(encoding="utf-8")
    for path in ["/var/backups/inventory-system/full", "/var/lib/inventory-system", "/var/www/inventory-system/out", "/var/tmp/inventory-ossutil", "/etc/inventory-system.env"]:
        assert path in doc
    for template in DEPLOY.rglob("*.example"):
        assert template.name in (DEPLOY / "README.md").read_text(encoding="utf-8")
