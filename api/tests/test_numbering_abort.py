"""Synthetic-only tests for the explicit pre-commit release abort path."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from app.inventory_backup import create_backup, restore_backup
from app.numbering_maintenance import Maintenance, MaintenanceError, file_gate
from app.services import product_code_cutover as cutover
from app.services.numbering_environment import file_binding
from tests.test_product_code_offline import database, synthetic_backup


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("numbering_abort_under_test", ROOT / "deploy/numbering_abort.py")
abort = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(abort)

OPERATION = "synthetic-abort-001"
OLD_COMMIT = "a" * 40
NEW_COMMIT = "b" * 40
RELEASE = f"173-1-{NEW_COMMIT}"


class SyntheticAbortContext:
    def __init__(self, root):
        self.root = root
        self.data_root = root / "data"
        self.database = self.data_root / "inventory.db"
        self.project = root / "project"
        self.api = self.project / "api"
        self.static = root / "www/out"
        self.backups = root / "backups"
        self.api.mkdir(parents=True)
        self.data_root.mkdir(parents=True)
        self.static.mkdir(parents=True)
        (self.data_root / "uploads/products").mkdir(parents=True)
        (self.data_root / "uploads/products/sample.jpg").write_bytes(b"synthetic image")
        self.service_running = False
        self.proxy_running = True
        self.timer_running = False
        self.probe_mode = False
        self.failure = None
        self.restore_calls = 0

    @contextmanager
    def locked(self):
        with file_gate(self.root / "deploy.lock"):
            yield

    def drained(self):
        if self.failure == "freeze":
            raise abort.AbortError("synthetic freeze/drain failure")
        if self.service_running or self.timer_running:
            raise abort.AbortError("synthetic business service is not drained")

    def backup_paths(self, source, manifest):
        for path in (Path(source), Path(manifest)):
            if not path.resolve().is_relative_to(self.backups.resolve()):
                raise abort.AbortError("synthetic backup outside isolated backup root")

    def api_active(self):
        return self.service_running

    def proxy_active(self):
        return self.proxy_running

    def stop_proxy(self):
        if self.failure == "proxy_stop":
            raise abort.AbortError("synthetic nginx stop failure")
        self.proxy_running = False

    def start_proxy(self):
        if self.failure == "proxy_start":
            raise abort.AbortError("synthetic nginx start failure")
        self.proxy_running = True

    def stop_api(self):
        self.service_running = False

    def start_api(self):
        if (self.probe_mode and self.failure == "probe_start") or (not self.probe_mode and self.failure == "normal_start"):
            raise abort.AbortError("synthetic API start failure")
        self.service_running = True

    def stop_timers(self):
        self.timer_running = False

    def start_timers(self, timers):
        if self.failure == "timer_start":
            raise abort.AbortError("synthetic timer start failure")
        self.timer_running = bool(timers)

    def configure_readonly_probe(self, _operation_id, _database):
        if self.proxy_running:
            raise abort.AbortError("proxy must be stopped for synthetic probe")
        self.probe_mode = True

    def clear_readonly_probe(self, _operation_id):
        self.service_running = False
        self.probe_mode = False

    def health_readonly(self):
        if not self.service_running:
            raise abort.AbortError("synthetic API is not active")
        if (self.probe_mode and self.failure == "probe_health") or (not self.probe_mode and self.failure == "normal_health"):
            raise abort.AbortError("synthetic read-only API health failure")
        uri = self.database.as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            assert db.execute("SELECT count(*) FROM products").fetchone()[0] == 7
            if self.probe_mode:
                with pytest.raises(sqlite3.OperationalError, match="readonly"):
                    db.execute("UPDATE products SET remark='should not write' WHERE id=2")

    def restore_database(self, bundle):
        self.restore_calls += 1
        if self.proxy_running:
            raise abort.AbortError("synthetic Nginx must be stopped before database restore")
        if self.failure == "database_restore":
            raise abort.AbortError("synthetic database restore failure")
        restore_backup(Path(bundle["backup_directory"]), self.data_root, force=True)

    def chown_inventory(self, path):
        if self.failure == "code_restore" and Path(path).name == "app":
            raise abort.AbortError("synthetic old code restore failure")

    def chown_recovery(self, _path):
        return None

    def prepare_backup_root(self, path):
        path.mkdir(parents=True, exist_ok=True)

    def create_abort_backup(self, destination):
        if self.failure == "backup":
            raise abort.AbortError("synthetic full backup failure")
        create_backup(
            destination,
            database_url=f"sqlite:///{self.database.resolve().as_posix()}",
            uploads_directory=self.data_root / "uploads",
            created_at=datetime.now(timezone.utc),
        )

    def api_startup_safe(self):
        if self.failure == "unsafe_api_startup":
            raise abort.AbortError("synthetic stale AI job or preview state")

    def ai_fingerprint(self):
        root = self.data_root / "visual-search"
        return abort.tree_digest(root)


def _write_old_api(context):
    for item in abort.API_ITEMS:
        path = context.api / item
        if item in {"app", "alembic", "scripts"}:
            path.mkdir(parents=True)
            (path / "old.txt").write_text(f"old {item}", encoding="utf8")
        else:
            path.write_text(f"old {item}", encoding="utf8")
    (context.api / "scripts/restore_inventory.py").write_text("synthetic restore entry\n", encoding="utf8")
    venv = context.api / ".venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_bytes(b"old interpreter")
    (venv / "python").chmod(0o755)
    (context.static / "index.html").write_text("old static", encoding="utf8")
    context.project.mkdir(parents=True, exist_ok=True)
    (context.project / ".actions-deployed.json").write_text(
        json.dumps({"commit": OLD_COMMIT, "release": "old-release"}), encoding="utf8"
    )


def _fixture(tmp_path, *, finish_migration=True, fail_freeze=False, with_backup=True):
    context = SyntheticAbortContext(tmp_path)
    source_root = tmp_path / "seed"
    source, _ = synthetic_backup(source_root)
    shutil.copyfile(source, context.database)
    ai = context.data_root / "visual-search"
    ai.mkdir()
    (ai / "synthetic-vector-index.db").write_bytes(b"vector rows unchanged")
    (ai / "daily-budget.sqlite3").write_bytes(b"budget rows unchanged")
    _write_old_api(context)
    context.backups.mkdir()
    release_dir = context.backups / RELEASE
    release_dir.mkdir()

    helper_api = release_dir / "api"
    shutil.copytree(context.api, helper_api, ignore=shutil.ignore_patterns(".venv"), symlinks=True)
    old_venv = release_dir / "old-venv"
    shutil.copytree(context.api / ".venv", old_venv, symlinks=True)
    old_static = release_dir / "old-static"
    shutil.copytree(context.static, old_static, symlinks=True)
    previous_metadata = release_dir / "previous-deployed.json"
    shutil.copy2(context.project / ".actions-deployed.json", previous_metadata)
    marker = json.loads(previous_metadata.read_text(encoding="utf8"))
    with closing(sqlite3.connect(context.database.as_uri() + "?mode=ro", uri=True)) as db:
        baseline_revision = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    baseline = {
        "format": abort.ABORT_BASELINE,
        "state": "captured",
        "operation_id": OPERATION,
        "release": RELEASE,
        "release_sha": NEW_COMMIT,
        "database_identity_before_deploy": abort.database_identity(context.database),
        "database_revision_before_deploy": baseline_revision,
        "database_schema_sha256": abort.database_schema_digest(context.database),
        "previous_metadata": str(previous_metadata),
        "previous_metadata_sha256": file_binding(previous_metadata)["sha256"],
        "previous_commit": marker["commit"],
        "old_api": str(helper_api),
        "old_api_tree_sha256": abort.api_items_digest(helper_api),
        "old_venv": str(old_venv),
        "old_venv_tree_sha256": abort.tree_digest(old_venv),
        "old_static": str(old_static),
        "old_static_tree_sha256": abort.tree_digest(old_static),
        "timers_were_active": ["inventory-db-backup.timer"],
        "proxy_was_active": True,
    }
    (release_dir / "numbering-abort-baseline.json").write_text(json.dumps(baseline), encoding="utf8")

    if fail_freeze:
        context.failure = "freeze"
        with pytest.raises(abort.AbortError, match="freeze/drain"):
            cutover.enter(context, OPERATION, NEW_COMMIT)
        context.failure = None
        return context, release_dir, None

    maintenance_state = cutover.enter(context, OPERATION, NEW_COMMIT)
    backup_parent = release_dir / "full"
    if not with_backup:
        return context, release_dir, maintenance_state
    backup = create_backup(
        backup_parent,
        database_url=f"sqlite:///{context.database.resolve().as_posix()}",
        uploads_directory=context.data_root / "uploads",
        created_at=datetime.now(timezone.utc),
    ).backup_directory

    abort.write_abort_bundle(context, release_dir, RELEASE, OPERATION, NEW_COMMIT,
                             baseline, backup, ensure_venv_copy=False)

    # Simulate a failed release after schema upgrade and partial source install.
    if finish_migration:
        with closing(sqlite3.connect(context.database)) as db:
            with db:
                db.execute("CREATE TABLE production_numbering_cutovers(id INTEGER PRIMARY KEY CHECK(id=1), operation_id TEXT, state TEXT, document TEXT)")
                db.execute("UPDATE alembic_version SET version_num='e3f4a5b6c7d8'")
    for item in abort.API_ITEMS:
        path = context.api / item
        if path.is_dir():
            (path / "old.txt").unlink(missing_ok=True)
            (path / "new.txt").write_text("partial new code", encoding="utf8")
        else:
            path.write_text("partial new code", encoding="utf8")
    shutil.rmtree(context.api / ".venv")
    (context.api / ".venv/bin").mkdir(parents=True)
    (context.api / ".venv/bin/python").write_bytes(b"new interpreter")
    (context.static / "index.html").write_text("new static", encoding="utf8")
    return context, release_dir, maintenance_state


def _plan(context):
    result = abort.plan(context, OPERATION, RELEASE)
    assert result["dry_run"] is True
    assert result["state"] == "precommit_abort_ready"
    return result


def _execute(context, plan):
    return abort.execute(context, OPERATION, RELEASE, confirm=True,
                         expected_plan_sha256=plan["plan_sha256"])


def test_abort_plan_is_readonly_and_requires_matching_confirmation(tmp_path):
    context, _release_dir, _ = _fixture(tmp_path)
    before = context.database.read_bytes()
    ai_before = context.ai_fingerprint()
    result = _plan(context)
    assert result["database"]["revision"] == "e3f4a5b6c7d8"
    assert result["database"]["product_count"] == 7
    assert context.database.read_bytes() == before
    assert context.ai_fingerprint() == ai_before
    with pytest.raises(abort.AbortError, match="显式确认"):
        abort.execute(context, OPERATION, RELEASE, expected_plan_sha256=result["plan_sha256"])
    with pytest.raises(abort.AbortError, match="摘要"):
        abort.execute(context, OPERATION, RELEASE, confirm=True, expected_plan_sha256="0" * 64)
    assert context.proxy_running is True
    assert Maintenance(context.data_root).held()


def test_abort_restores_full_database_old_code_and_reopens_old_service(tmp_path):
    context, release_dir, _ = _fixture(tmp_path)
    backup = next((release_dir / "full").glob("*/inventory.db"))
    backup_bytes = backup.read_bytes()
    ai_before = context.ai_fingerprint()
    plan = _plan(context)
    result = _execute(context, plan)
    assert result["phase"] == "complete"
    assert result["state"] == "complete"
    assert context.database.read_bytes() == backup_bytes
    assert (context.api / "app/old.txt").read_text(encoding="utf8") == "old app"
    assert (context.api / ".venv/bin/python").read_bytes() == b"old interpreter"
    assert (context.static / "index.html").read_text(encoding="utf8") == "old static"
    assert context.service_running and context.proxy_running and context.timer_running
    assert not Maintenance(context.data_root).held()
    state = Maintenance(context.data_root).read()
    assert state["state"] == "released"
    assert state["release_reason"] == "precommit_abort"
    assert context.ai_fingerprint() == ai_before
    assert abort.status(context, OPERATION, RELEASE)["state"] == "complete"
    with pytest.raises(abort.AbortError, match="已完成"):
        _execute(context, plan)


def test_committed_numbering_registry_forbids_abort(tmp_path):
    context, _release_dir, _ = _fixture(tmp_path)
    with sqlite3.connect(context.database) as db:
        db.execute("INSERT INTO production_numbering_cutovers VALUES(1,'other-operation','committed','{}')")
    with pytest.raises(abort.AbortError, match="已提交"):
        _plan(context)
    with pytest.raises(abort.AbortError, match="已提交"):
        _execute(context, {"plan_sha256": "0" * 64})
    assert Maintenance(context.data_root).held()
    assert context.proxy_running


@pytest.mark.parametrize("kind", ["freeze", "backup"])
def test_abort_refuses_when_freeze_or_full_backup_is_incomplete(tmp_path, kind):
    if kind == "freeze":
        context, _release_dir, _ = _fixture(tmp_path, fail_freeze=True)
    else:
        context, _release_dir, _ = _fixture(tmp_path, with_backup=False)
    with pytest.raises((abort.AbortError, MaintenanceError)):
        _plan(context)
    assert context.proxy_running


def test_explicit_prepare_recovers_after_incomplete_maintenance_freeze(tmp_path):
    context, release_dir, _ = _fixture(tmp_path, fail_freeze=True)
    assert Maintenance(context.data_root).held()
    assert not (release_dir / "numbering-abort.json").exists()
    prepared = abort.prepare(context, OPERATION, RELEASE, confirm=True)
    assert prepared["state"] == "recovery_bundle_ready"
    assert Maintenance(context.data_root).held()
    assert (release_dir / "numbering-abort.json").is_file()
    plan = _plan(context)
    assert _execute(context, plan)["state"] == "complete"


def test_explicit_prepare_retries_full_backup_failure_without_releasing_maintenance(tmp_path):
    context, release_dir, _ = _fixture(tmp_path, with_backup=False)
    context.failure = "backup"
    with pytest.raises(abort.AbortError, match="full backup failure"):
        abort.prepare(context, OPERATION, RELEASE, confirm=True)
    assert Maintenance(context.data_root).held()
    assert not (release_dir / "numbering-abort.json").exists()
    assert not context.service_running
    context.failure = None
    prepared = abort.prepare(context, OPERATION, RELEASE, confirm=True)
    assert prepared["state"] == "recovery_bundle_ready"
    plan = _plan(context)
    assert _execute(context, plan)["state"] == "complete"


def test_failed_database_restore_keeps_maintenance_and_does_not_start_old_code(tmp_path):
    context, _release_dir, _ = _fixture(tmp_path)
    before_code = (context.api / "app/new.txt").read_text(encoding="utf8")
    plan = _plan(context)
    context.failure = "database_restore"
    with pytest.raises(abort.AbortError, match="database restore failure"):
        _execute(context, plan)
    assert Maintenance(context.data_root).held()
    assert context.api_active() is False
    assert context.proxy_active() is False
    assert (context.api / "app/new.txt").read_text(encoding="utf8") == before_code
    assert abort.status(context, OPERATION, RELEASE)["phase"] == "database_restore_started"


def test_failed_old_code_restore_stays_held_and_can_resume_with_same_digest(tmp_path):
    context, _release_dir, _ = _fixture(tmp_path)
    plan = _plan(context)
    context.failure = "code_restore"
    with pytest.raises(abort.AbortError, match="old code restore failure"):
        _execute(context, plan)
    assert Maintenance(context.data_root).held()
    assert not context.service_running and not context.proxy_running
    context.failure = None
    result = _execute(context, plan)
    assert result["state"] == "complete"
    assert context.service_running and context.proxy_running


def test_interrupted_abort_is_readable_and_resumes_without_replanning(tmp_path, monkeypatch):
    context, _release_dir, _ = _fixture(tmp_path)
    plan = _plan(context)
    original = abort._restore_old_artifacts
    calls = {"count": 0}

    def interrupt_once(*args):
        calls["count"] += 1
        if calls["count"] == 1:
            raise KeyboardInterrupt("synthetic process interruption")
        return original(*args)

    monkeypatch.setattr(abort, "_restore_old_artifacts", interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        _execute(context, plan)
    status = abort.status(context, OPERATION, RELEASE)
    assert status["phase"] == "code_restore_started"
    assert status["maintenance"] == "held"
    assert status["plan_sha256"] == plan["plan_sha256"]
    context.proxy_running = True  # Simulate Nginx being reactivated after reboot.
    result = _execute(context, plan)
    assert result["state"] == "complete"
    assert context.restore_calls == 1  # database restore completed before the interruption checkpoint


def test_interrupted_abort_resumes_from_the_same_verified_backup_after_an_hour(tmp_path, monkeypatch):
    context, _release_dir, _ = _fixture(tmp_path)
    plan = _plan(context)
    original_check = abort._verify_not_committed

    def interrupt_after_plan(_context):
        raise KeyboardInterrupt("synthetic interruption after durable abort authorization")

    monkeypatch.setattr(abort, "_verify_not_committed", interrupt_after_plan)
    with pytest.raises(KeyboardInterrupt):
        _execute(context, plan)
    monkeypatch.setattr(abort, "_verify_not_committed", original_check)
    original_now = abort.time.time_ns
    monkeypatch.setattr(abort.time, "time_ns", lambda: original_now() + 2 * 60 * 60 * 10**9)
    result = _execute(context, plan)
    assert result["state"] == "complete"
    assert context.restore_calls == 1


def test_readonly_probe_cleanup_removes_only_operation_scoped_temporary_data(tmp_path, monkeypatch):
    context = abort.ProductionAbortContext.__new__(abort.ProductionAbortContext)
    runtime_root = tmp_path / "runtime"
    data_root = tmp_path / "probe-data"
    dropin_root = tmp_path / "dropins"
    for path in (runtime_root / OPERATION, data_root / OPERATION / "data", dropin_root):
        path.mkdir(parents=True)
    (runtime_root / OPERATION / "probe.env").write_text("synthetic")
    (data_root / OPERATION / "data/visual-index.db").write_bytes(b"synthetic probe-only data")
    (dropin_root / "numbering-abort-probe.conf").write_text("synthetic")
    monkeypatch.setattr(abort, "RUNTIME_PROBE_ROOT", runtime_root)
    monkeypatch.setattr(abort, "PROBE_DATA_ROOT", data_root)
    monkeypatch.setattr(abort, "RUNTIME_DROPIN_ROOT", dropin_root)
    context.stop_api = lambda: None
    context._systemctl = lambda *args, **kwargs: None
    context.clear_readonly_probe(OPERATION)
    assert not (runtime_root / OPERATION).exists()
    assert not (data_root / OPERATION).exists()
    assert not (dropin_root / "numbering-abort-probe.conf").exists()


@pytest.mark.parametrize("failure", ["probe_start", "probe_health", "unsafe_api_startup"])
def test_probe_failure_does_not_release_maintenance_or_open_proxy(tmp_path, failure):
    context, _release_dir, _ = _fixture(tmp_path)
    plan = _plan(context)
    context.failure = failure
    with pytest.raises(abort.AbortError):
        _execute(context, plan)
    assert Maintenance(context.data_root).held()
    # The unsafe-startup check is a no-op preflight and runs before the
    # recovery plan stops Nginx. Probe-time failures happen after Nginx stops.
    assert context.proxy_running is (failure == "unsafe_api_startup")
    assert not context.service_running
    expected_state = "held_abort_not_started" if failure == "unsafe_api_startup" else "in_progress"
    assert abort.status(context, OPERATION, RELEASE)["state"] == expected_state


def test_normal_old_service_failure_after_release_reholds_before_proxy_opens(tmp_path):
    context, _release_dir, _ = _fixture(tmp_path)
    plan = _plan(context)
    context.failure = "normal_health"
    with pytest.raises(abort.AbortError, match="synthetic read-only API health failure"):
        _execute(context, plan)
    assert Maintenance(context.data_root).held()
    assert not context.proxy_running
    assert not context.service_running
    assert abort.status(context, OPERATION, RELEASE)["state"] == "failed_maintenance_held"


def test_abort_release_method_requires_durable_probe_validation(tmp_path):
    context, _release_dir, state = _fixture(tmp_path)
    maintenance = Maintenance(context.data_root)
    receipt = {
        "format": abort.ABORT_RECEIPT,
        "state": "complete",
        "operation_id": OPERATION,
        "release_sha": NEW_COMMIT,
        "database_identity": state["database_identity"],
        "previous_commit": OLD_COMMIT,
        "plan_sha256": "1" * 64,
        "checks": {
            "cutover_not_committed": True,
            "database_restored": True,
            "old_code_restored": True,
            "api_readonly_probe": True,
            "inventory_readonly_checks": True,
        },
    }
    with pytest.raises(MaintenanceError):
        maintenance.abort_release(OPERATION, context.database, receipt)
    assert maintenance.held()
