"""One full synthetic numbering + AI metadata release rehearsal.

This uses only throwaway SQLite databases, generated images and Mock providers.
It never starts systemd, a production endpoint, a cloud AI client or real photos.
"""
from contextlib import closing
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import tarfile

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from passlib.hash import apr_md5_crypt

from app import product_codes
from app.database import get_db
from app.main import create_app
from app.numbering_maintenance import Maintenance, MaintenanceError
from app.schemas import ProductCreate, ProductPackagingCreate
from app.services import product_code_cutover as business
from app.services.numbering_environment import file_binding
from app.services.product_code_cutover import REVISION
from app.services.products import create_product_record
from app.services.visual_search import numbering_cutover as ai_cutover
from app.services.visual_search.catalog_metadata import SOURCE_ID, catalog_context
from app.services.visual_search.config import VisualConfig
from app.services.visual_search.factory import production_service
from app.services.visual_search.images import VisualError
from app.services.visual_search.production_catalog import sync_catalog
from app.services.visual_search.runtime_control import RuntimeSwitch
from .test_product_code_cutover import SyntheticEnvironment
from .test_product_code_offline import database, synthetic_backup
from .test_production_catalog import Backend, photo
from .test_visual_search import MockProvider
from app.numbering_acceptance import create_acceptance_app, issue_capability


OPERATION = "synthetic-full-numbering-001"
RELEASE_SHA = "b" * 40


@pytest.fixture(autouse=True)
def forbid_external_sockets(monkeypatch):
    connect = socket.socket.connect

    def guarded(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return connect(sock, address)  # TestClient's in-process transport.
        raise AssertionError("Full cutover rehearsal must not access a network")

    monkeypatch.setattr(socket.socket, "connect", guarded)


def make_alembic_config(database_path):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def seed_business(path):
    with database(path) as db:
        db.executescript("DELETE FROM inventory_movements; DELETE FROM product_packagings; DELETE FROM products;")
        codes = ["MOCK-1", "MOCK-2", "MOCK-3", "SYNTHETIC-4", "LAST-MINUTE-5"]
        for product_id, code in enumerate(codes, 1):
            db.execute(
                "INSERT INTO products(id,product_code,original_product_code,category_id,warehouse_id,is_active,"
                "image_path,thumbnail_path,size,unit,price,remark,created_at,updated_at) "
                "VALUES(?,?,?,1,1,?,?,?,?,?,'1.25','synthetic',?,?)",
                (product_id, code, f"ORIGINAL-{product_id}", int(product_id != 2),
                 "uploads/synthetic.jpg", None, "test-size", "pcs", "2026-10-01", "2026-10-01"),
            )
            db.execute("INSERT INTO product_packagings VALUES(?,?,24,?,0,'2026-10-01','2026-10-01')",
                       (product_id * 10, product_id, product_id))
        db.execute("INSERT INTO inventory_movements VALUES(1,1,10,1,'IN',1,0,1,24,'pcs','synthetic','2026-10-01')")


def write_backup(source, root):
    root.mkdir()
    database_copy = root / "inventory.db"
    shutil.copyfile(source, database_copy)
    archive = root / "uploads.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        info = tarfile.TarInfo("uploads/synthetic.jpg")
        payload = b"synthetic-image"
        info.size = len(payload)
        bundle.addfile(info, io.BytesIO(payload))
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({
        "backup_version": 1, "database_filename": database_copy.name,
        "database_sha256": file_binding(database_copy)["sha256"],
        "database_size": database_copy.stat().st_size,
        "uploads_archive": archive.name,
        "uploads_sha256": file_binding(archive)["sha256"],
        "uploads_archive_size": archive.stat().st_size,
        "uploads_file_count": 1,
    }), encoding="utf8")
    return database_copy, archive, manifest


def test_complete_synthetic_cutover_and_service_resume(tmp_path, monkeypatch):
    business_path, _ = synthetic_backup(tmp_path / "business")
    seed_business(business_path)
    with closing(sqlite3.connect(business_path)) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "d1e2f3a4b5c6"
        assert db.execute("SELECT count(*) FROM products").fetchone()[0] == 5

    # Build the synthetic AI mirror, index and an old query before the freeze.
    backend = Backend()
    visual_root = tmp_path / "visual"
    catalog_root = visual_root / "production-catalog"
    with closing(backend.client()) as client:
        sync_catalog(client, catalog_root, SOURCE_ID, "SYNTHETIC ONLY", execute=True)
    config = VisualConfig(dimension=256, api_key="mock-only", interval=0.1,
                          daily_budget_cny=1, call_reserve_cny=0.003)
    provider = MockProvider()
    service = production_service(visual_root, config, provider=provider, initialize=True)
    provider.record = service.store.record_call
    service.rebuild(None, "synthetic-full", 10, False, True, background=False)
    reservation = service.budget.reserve()
    service.budget.settle(reservation, {"input_tokens": 4, "image_tokens": 20}, known=True)
    old_query = service.search(None, "synthetic-old-session", photo("red"))
    assert provider.calls > 0  # Mock only; represents a pre-cutover search.
    old_catalog_version = catalog_context(catalog_root)
    RuntimeSwitch(visual_root).set_enabled(False)
    with service.store.connection() as index:
        assert index.execute("SELECT count(*) FROM visual_jobs WHERE status='running'").fetchone()[0] == 0
    service.close()  # Simulated AI service drained and stopped.
    calls_before_cutover = provider.calls

    # Freeze after the last synthetic product is present; maintenance survives restart.
    environment = SyntheticEnvironment(business_path.parent, business_path)
    held = business.enter(environment, OPERATION, RELEASE_SHA)
    assert Maintenance(business_path.parent).held()
    assert held["frozen_business_sha256"]

    # Exercise the real API admission middleware while the app is restarted in maintenance.
    engine = create_engine(f"sqlite:///{business_path.as_posix()}", connect_args={"check_same_thread": False})
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def get_isolated_db():
        with sessions() as db:
            yield db

    held_app = create_app(data_directory=business_path.parent, visual_config=config,
                          production_catalog_directory=catalog_root, visual_provider=MockProvider())
    held_app.dependency_overrides[get_db] = get_isolated_db
    with TestClient(held_app) as client:
        assert client.get("/api/products").status_code == 200
        assert client.post("/api/products", json={}).status_code == 503
        assert client.get("/api/visual-search/catalog/version").status_code == 503
    assert Maintenance(business_path.parent).held()
    engine.dispose()

    # Capture a consistent synthetic business backup, then perform normal Alembic upgrade.
    backup_db, _archive, backup_manifest = write_backup(business_path, tmp_path / "backup")
    command.upgrade(make_alembic_config(business_path), REVISION)
    with closing(sqlite3.connect(business_path)) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == REVISION
    assert Maintenance(business_path.parent).held()
    business_plan = business.plan(environment, OPERATION, backup_db, backup_manifest)
    assert [row["product_id"] for row in business_plan["mapping"]] == [1, 2, 3, 4, 5]
    assert [row["new_code"] for row in business_plan["mapping"]] == [f"25{n:04d}" for n in range(1, 6)]
    business_result = business.plan(environment, OPERATION, backup_db, backup_manifest,
        execute=True, confirm_production_numbering=True,
        expected_plan_sha256=business_plan["plan_sha256"])
    assert business_result["state"] == "committed"
    business_reports = Path(business_result["reports_directory"])

    # Bind the synthetic AI metadata update to the actual committed business result.
    from app.services.visual_search.numbering_cutover import _meta
    with closing(ai_cutover.connect(catalog_root / "catalog.sqlite")) as mirror:
        sync_id = _meta(mirror)["last_report"]["sync_id"]
    ai_inputs = ai_cutover.Inputs(
        visual_root, catalog_root, business_reports / "report.json", business_reports / "mapping.csv",
        catalog_root / "manifests" / f"{sync_id}.json", tmp_path / "private-ai-reports",
        OPERATION, business_result["completion_sha256"],
        file_binding(business_reports / "mapping.csv")["sha256"],
        service.source.identity_key, service.config.index_key,
    )
    before_vectors = ai_cutover.vector_evidence(ai_inputs)
    before_index = service.store.path.read_bytes()
    budget_path = visual_root / "daily-budget.sqlite3"
    before_budget_bytes = budget_path.read_bytes()
    before_budget = service.budget.snapshot()
    before_source = (catalog_root / "source.json").read_bytes()
    before_binding = (visual_root / "production-indices" / ai_inputs.namespace / "source-binding.json").read_bytes()
    image_bytes = {p.relative_to(catalog_root).as_posix(): file_binding(p)["sha256"]
                   for p in (catalog_root / "images").rglob("*") if p.is_file()}
    plan = ai_cutover.plan(ai_inputs)
    assert plan["business_products"] == 5 and plan["mirror_products"] == 3
    assert plan["missing_from_mirror"] == [4, 5]
    ai_result = ai_cutover.plan(ai_inputs, execute=True, confirm_metadata_only=True,
                                expected_plan_sha256=plan["plan_sha256"])
    assert ai_result["state"] == "committed"
    assert provider.calls == calls_before_cutover
    assert ai_cutover.vector_evidence(ai_inputs) == before_vectors
    assert service.store.path.read_bytes() == before_index
    assert budget_path.read_bytes() == before_budget_bytes
    assert service.budget.snapshot() == before_budget
    assert (catalog_root / "source.json").read_bytes() == before_source
    assert (visual_root / "production-indices" / ai_inputs.namespace / "source-binding.json").read_bytes() == before_binding
    assert {p.relative_to(catalog_root).as_posix(): file_binding(p)["sha256"]
            for p in (catalog_root / "images").rglob("*") if p.is_file()} == image_bytes
    assert catalog_context(catalog_root)["index_namespace"] == ai_inputs.namespace
    assert service.config.index_key == ai_inputs.index_key
    with pytest.raises(VisualError, match="目录已更新"):
        service.feedback(None, "synthetic-old-session", old_query["query_id"], 1, False)

    # Read-only acceptance is admin + scoped capability, while public writes remain held.
    auth_file = tmp_path / "synthetic.htpasswd"
    auth_file.write_text("admin:" + apr_md5_crypt.hash("synthetic") + "\n", encoding="utf8")
    token_file = tmp_path / "acceptance-token.json"
    issue_capability(token_file, OPERATION, business_result["completion_sha256"],
                     database=business_path, catalog_root=catalog_root)
    token = json.loads(token_file.read_text(encoding="utf8"))["token"]
    acceptance = create_acceptance_app(token_file, OPERATION, business_result["completion_sha256"],
        htpasswd_file=auth_file, admin_users=("admin",), database=business_path, catalog_root=catalog_root)
    headers = {"X-Numbering-Acceptance": token}
    with TestClient(acceptance, client=("127.0.0.1", 12345)) as client:
        health = client.get("/acceptance/health", auth=("admin", "synthetic"), headers=headers)
        assert health.status_code == 200, health.text
        assert health.json()["state"] == "maintenance_held_readonly"
        products = client.get("/acceptance/products", auth=("admin", "synthetic"), headers=headers).json()["items"]
        assert len(products) == 5 and [row["product_code"] for row in products] == [f"25{n:04d}" for n in range(1, 6)]
        assert client.get("/acceptance/products/1", auth=("admin", "synthetic"), headers=headers).json()["packagings"]
        assert client.get("/acceptance/stock", auth=("admin", "synthetic"), headers=headers).status_code == 200
        mirror = client.get("/acceptance/catalog/products", auth=("admin", "synthetic"), headers=headers).json()["items"]
        assert len(mirror) == 3 and [row["product_code"] for row in mirror] == ["250001", "250002", "250003"]
        assert client.post("/api/products", auth=("admin", "synthetic"), headers=headers).status_code == 503
    assert Maintenance(business_path.parent).held()

    # A complete, operation-bound receipt is required before releasing maintenance.
    receipt = {"operation_id": OPERATION, "release_sha": RELEASE_SHA,
        "database_identity": business_result["database_identity"],
        "completion_sha256": business_result["completion_sha256"],
        "checks": {"inventory": True, "ai_metadata": True, "old_sessions": True, "web": True}}
    business.status(environment, OPERATION, acceptance=receipt)
    assert not Maintenance(business_path.parent).held()
    assert Maintenance(business_path.parent).read()["state"] == "released"

    # Restart business service and verify the first post-release creation uses YYMM.
    monkeypatch.setattr(product_codes, "product_creation_time",
                        lambda: datetime(2026, 10, 18, tzinfo=timezone.utc))
    resumed_engine = create_engine(f"sqlite:///{business_path.as_posix()}", connect_args={"check_same_thread": False})
    resumed_sessions = sessionmaker(bind=resumed_engine, autoflush=False, autocommit=False)
    payload = ProductCreate(size="synthetic-after-cutover", packagings=[ProductPackagingCreate(packing_qty=12, carton_count=1)])
    with resumed_sessions.begin() as db:
        from app.database import begin_write_transaction
        begin_write_transaction(db)
        new_product = create_product_record(db, payload)
        assert new_product.product_code == "261001"
        new_id = new_product.id
    with closing(sqlite3.connect(business_path)) as db:
        assert db.execute("SELECT product_code FROM products WHERE id=?", (new_id,)).fetchone()[0] == "261001"
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    RuntimeSwitch(visual_root).set_enabled(True)  # Explicit synthetic AI resume; no search is issued.
    assert RuntimeSwitch(visual_root).is_enabled()
    assert provider.calls == calls_before_cutover
    service.close()
    resumed_engine.dispose()


def test_freeze_failure_leaves_persistent_maintenance_held(tmp_path):
    path, _ = synthetic_backup(tmp_path / "business")
    environment = SyntheticEnvironment(path.parent, path)
    environment.ready = False
    with pytest.raises(MaintenanceError, match="not drained"):
        business.enter(environment, OPERATION, RELEASE_SHA)
    state = Maintenance(path.parent).require(OPERATION, path)
    assert state["state"] == "held"
    assert state["frozen_business_sha256"] is None
    assert Maintenance(path.parent).held()


def test_alembic_interruption_rolls_back_and_keeps_maintenance(tmp_path, monkeypatch):
    path, _ = synthetic_backup(tmp_path / "business")
    environment = SyntheticEnvironment(path.parent, path)
    business.enter(environment, OPERATION, RELEASE_SHA)
    config = make_alembic_config(path)
    def interrupt_after_ddl(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("UPDATE ALEMBIC_VERSION"):
            raise RuntimeError("injected synthetic Alembic failure")

    event.listen(Engine, "before_cursor_execute", interrupt_after_ddl)
    try:
        with pytest.raises(RuntimeError, match="injected synthetic Alembic failure"):
            command.upgrade(config, REVISION)
    finally:
        event.remove(Engine, "before_cursor_execute", interrupt_after_ddl)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "d1e2f3a4b5c6"
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='production_numbering_cutovers'").fetchone()[0] == 0
    assert Maintenance(path.parent).held()


def test_counter_migration_interruption_rolls_back_and_can_resume(tmp_path):
    path, _ = synthetic_backup(tmp_path / "business")
    with database(path) as db:
        db.execute("DROP TABLE product_code_months")
        db.execute("UPDATE alembic_version SET version_num='c8d9e0f1a2b3'")
    environment = SyntheticEnvironment(path.parent, path)
    business.enter(environment, OPERATION, RELEASE_SHA)
    config = make_alembic_config(path)

    observed = []

    def interrupt_after_ddl(_connection, _cursor, statement, _parameters, _context, _many):
        observed.append(statement)
        normalized = statement.lstrip().upper()
        if normalized.startswith(("UPDATE ALEMBIC_VERSION", "INSERT INTO ALEMBIC_VERSION")):
            raise RuntimeError("injected counter Alembic failure")

    event.listen(Engine, "before_cursor_execute", interrupt_after_ddl)
    try:
        try:
            command.upgrade(config, "d1e2f3a4b5c6")
        except RuntimeError as error:
            assert "injected counter Alembic failure" in str(error)
        else:
            pytest.fail(f"injected failure did not fire; Alembic statements: {observed!r}")
    finally:
        event.remove(Engine, "before_cursor_execute", interrupt_after_ddl)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "c8d9e0f1a2b3"
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='product_code_months'").fetchone()[0] == 0
    assert Maintenance(path.parent).held()
    command.upgrade(config, "d1e2f3a4b5c6")
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "d1e2f3a4b5c6"
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='product_code_months'").fetchone()[0] == 1
    assert Maintenance(path.parent).held()
