"""Real isolated SQLite/child-ASGI tests; systemd activation stays mocked."""
import asyncio
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

import pytest

from app.numbering_maintenance import Maintenance, atomic_json
from tests import test_numbering_abort as recovery


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("abort_readonly_probe_test", ROOT / "deploy/numbering_abort_probe.py")
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

# A legacy application with no maintenance middleware. Its ordinary POST route
# really writes SQLite; its startup also tries a write through the supplied URL.
LEGACY = '''
import json
import os
from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
engine = create_engine(os.environ["DATABASE_URL"])
class Legacy:
    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    with engine.connect() as db:
                        db.execute(text("SELECT count(*) FROM products"))
                        try:
                            db.execute(text("UPDATE products SET remark='startup write'"))
                        except OperationalError as error:
                            if "readonly" not in str(error).lower():
                                raise
                        else:
                            raise RuntimeError("SQLite is writable during probe")
                    Path(os.environ["INVENTORY_DATA_DIR"], "startup-in-scratch").write_text("isolated")
                    await send({"type": "lifespan.startup.complete"})
                else:
                    engine.dispose()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        else:
            if scope["method"] != "GET":
                with engine.begin() as db:
                    db.execute(text("UPDATE products SET remark='ordinary legacy write'"))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})
app = Legacy()
'''

FASTAPI_LEGACY = '''
from contextlib import asynccontextmanager
import os
from pathlib import Path
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
engine = create_engine(os.environ["DATABASE_URL"])
@asynccontextmanager
async def lifespan(app):
    with engine.connect() as db:
        try:
            db.execute(text("UPDATE products SET remark='startup write'"))
        except OperationalError as error:
            if "readonly" not in str(error).lower():
                raise
        else:
            raise RuntimeError("Probe database is writable")
    Path(os.environ["INVENTORY_DATA_DIR"], "startup-in-scratch").write_text("isolated")
    yield
    engine.dispose()
app = FastAPI(lifespan=lifespan)
@app.get("/api/dashboard/summary")
@app.get("/api/products")
def read():
    with engine.connect() as db:
        return {"total": db.execute(text("SELECT count(*) FROM products")).scalar_one()}
@app.post("/api/products")
def ordinary_write():
    with engine.begin() as db:
        db.execute(text("UPDATE products SET remark='legacy wrote'"))
    return {"written": True}
'''


def child(api, database, scratch):
    # Same isolated config as the production env -i command, but without Linux
    # runuser. This executes a real child with the installed local interpreter.
    env = {k: v for k, v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
    result = subprocess.run([
        sys.executable, "-B", str(ROOT / "deploy/numbering_abort_probe.py"),
        "--api-root", str(api), "--database", str(database), "--data-root", str(scratch),
    ], env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "format": "numbering-abort-readonly-probe-v1", "state": "passed",
        "transport": "asgi-no-listener", "read_routes": 2, "writes_rejected": True,
    }


def test_loopback_asgi_writes_cannot_reach_legacy_without_maintenance_middleware(tmp_path):
    database = tmp_path / "synthetic.db"
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE products(id INTEGER, remark TEXT); INSERT INTO products VALUES(1,'before')")
    reached = []

    async def legacy(scope, receive, send):
        reached.append(scope["method"])
        with sqlite3.connect(database) as db:
            db.execute("UPDATE products SET remark='legacy wrote'")
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": b"{}"})

    async def attempts():
        guarded = probe.ReadOnlyProbe(legacy)
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
            assert (await probe.request(guarded, method, "/api/products"))[0] == 503
        assert (await probe.request(guarded, "GET", "/api/visual-search/search"))[0] == 503
        assert (await probe.request(guarded, "GET", "/api/products", b"unexpected=query"))[0] == 503
        assert reached == []
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT remark FROM products").fetchone()[0] == "before"
        # Establish that the legacy app itself has a real writable route.
        assert (await probe.request(legacy, "POST", "/api/products"))[0] == 200

    asyncio.run(attempts())
    assert reached == ["POST"]
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT remark FROM products").fetchone()[0] == "legacy wrote"


@pytest.mark.parametrize("legacy_source", [LEGACY, FASTAPI_LEGACY], ids=["bare-asgi", "fastapi-no-maintenance"])
def test_real_child_uses_readonly_database_and_separate_scratch_after_restart(tmp_path, legacy_source):
    api = tmp_path / "legacy"
    (api / "app").mkdir(parents=True)
    (api / "app/__init__.py").write_text("")
    (api / "app/main.py").write_text(legacy_source)
    database = tmp_path / "business/inventory.db"
    database.parent.mkdir()
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE products(id INTEGER, remark TEXT); INSERT INTO products VALUES(1,'preserved')")
    before = database.read_bytes()
    for index in range(2):
        scratch = tmp_path / f"probe-{index}"
        scratch.mkdir()
        child(api, database, scratch)
        assert (scratch / "startup-in-scratch").read_text() == "isolated"
        assert database.read_bytes() == before
    assert not (database.parent / "startup-in-scratch").exists()


def test_full_abort_with_real_legacy_probe_interrupts_and_resumes_without_normal_api(tmp_path, monkeypatch):
    original_old = recovery._write_old_api

    def old_with_legacy(context):
        original_old(context)
        (context.api / "app/__init__.py").write_text("")
        (context.api / "app/main.py").write_text(LEGACY)

    monkeypatch.setattr(recovery, "_write_old_api", old_with_legacy)
    context, release_dir, _ = recovery._fixture(tmp_path)
    plan = recovery._plan(context)
    backup = next((release_dir / "full").glob("*/inventory.db")).read_bytes()

    def real_probe():
        assert Maintenance(context.data_root).read()["state"] == "abort_resuming"
        assert not context.service_running and not context.proxy_running
        context.probe_calls += 1
        with tempfile.TemporaryDirectory(dir=tmp_path, prefix="child-probe-") as scratch:
            child(context.api, context.database, Path(scratch))
        assert context.database.read_bytes() == backup

    context.probe_readonly = real_probe
    original_release = Maintenance.abort_release

    def interrupted(self, *_args, **_kwargs):
        assert not context.service_running
        assert context.normal_start_states == []
        raise KeyboardInterrupt("synthetic process interruption before release")

    monkeypatch.setattr(Maintenance, "abort_release", interrupted)
    with pytest.raises(KeyboardInterrupt):
        recovery._execute(context, plan)
    assert Maintenance(context.data_root).held()
    assert context.database.read_bytes() == backup
    assert not context.service_running
    monkeypatch.setattr(Maintenance, "abort_release", original_release)
    assert recovery._execute(context, plan)["state"] == "complete"
    assert context.probe_calls == 2
    assert context.restore_calls == 1
    assert context.normal_start_states == ["released"]
    assert context.database.read_bytes() == backup


def test_real_child_health_failure_does_not_release_or_start_ordinary_api(tmp_path, monkeypatch):
    original_old = recovery._write_old_api

    def old_with_failed_reads(context):
        original_old(context)
        (context.api / "app/__init__.py").write_text("")
        (context.api / "app/main.py").write_text(LEGACY.replace('"status": 200', '"status": 500'))

    monkeypatch.setattr(recovery, "_write_old_api", old_with_failed_reads)
    context, release_dir, _ = recovery._fixture(tmp_path)
    plan = recovery._plan(context)
    backup = next((release_dir / "full").glob("*/inventory.db")).read_bytes()

    def failed_real_probe():
        with tempfile.TemporaryDirectory(dir=tmp_path, prefix="failed-child-") as scratch:
            child(context.api, context.database, Path(scratch))

    context.probe_readonly = failed_real_probe
    with pytest.raises(AssertionError, match="Read-only legacy probe failed"):
        recovery._execute(context, plan)
    assert Maintenance(context.data_root).read()["state"] == "abort_resuming"
    assert context.normal_start_states == []
    assert not context.service_running and not context.proxy_running and not context.timer_running
    assert context.database.read_bytes() == backup


@pytest.mark.parametrize("phase", ["held", "abort_resuming"])
def test_normal_production_service_adapter_cannot_start_before_atomic_release(tmp_path, phase):
    database = tmp_path / "inventory.db"
    with closing(sqlite3.connect(database)) as db:
        db.execute("CREATE TABLE products(id INTEGER)")
    maintenance = Maintenance(tmp_path)
    state = maintenance.enter(recovery.OPERATION, recovery.NEW_COMMIT, database, "synthetic")
    atomic_json(maintenance.path, {**state, "state": phase})
    context = recovery.abort.ProductionAbortContext.__new__(recovery.abort.ProductionAbortContext)
    context.data_root = tmp_path
    calls = []
    context._systemctl = lambda *args: calls.append(args)
    with pytest.raises(recovery.abort.AbortError, match="禁止启动普通 API"):
        context.start_api()
    assert calls == []


def test_stale_legacy_service_override_is_not_used_or_automatically_removed(tmp_path, monkeypatch):
    override = tmp_path / "numbering-abort-probe.conf"
    override.write_text("old runtime override")
    monkeypatch.setattr(recovery.abort, "LEGACY_PROBE_DROPIN", override)
    context = recovery.abort.ProductionAbortContext.__new__(recovery.abort.ProductionAbortContext)
    context.data_root = tmp_path
    with pytest.raises(recovery.abort.AbortError, match="旧版临时 probe 服务覆盖"):
        context.api_startup_safe()
    assert override.read_text() == "old runtime override"
