"""Offline source separation and content reuse; every image/product is synthetic."""
from dataclasses import replace
from decimal import Decimal
import hashlib
import json
import socket
import sqlite3
import importlib.util
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import numpy as np
from passlib.hash import apr_md5_crypt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.main import create_app
from app.models import Product, ProductPackaging
from app.services.visual_search.config import VisualConfig
from app.services.visual_search.factory import production_service
from app.services.visual_search.images import VisualError
from app.services.visual_search.production_catalog import sync_catalog
from app.services.visual_search.runtime_control import RuntimeSwitch
from app.services.visual_search.sources import DevelopmentSource, ProductionSource, PRODUCTION_ID
from .test_production_catalog import Backend, photo
from .test_visual_search import MockProvider, image, migrate


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    original = socket.socket.connect
    def connect(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original(sock, address)
        raise AssertionError("Production-source tests must not call real APIs")
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr("app.services.visual_search.production_catalog.time.sleep", lambda _: None)
    monkeypatch.setattr("app.services.visual_search.service.time.sleep", lambda _: None)


@pytest.fixture
def production(tmp_path):
    backend = Backend()
    catalog = tmp_path / "visual-search/production-catalog"
    def sync():
        client = backend.client()
        try:
            return sync_catalog(client, catalog, PRODUCTION_ID, "MOCK fixture only", execute=True)
        finally:
            client.close()
    sync()
    auth = tmp_path / ".htpasswd"
    auth.write_text("admin:" + apr_md5_crypt.hash("mock-password"), encoding="utf8")
    config = VisualConfig(dimension=256, api_key="mock-only", retries=0, interval=.1,
                          daily_max_calls=100, daily_budget_cny=Decimal("10"), call_reserve_cny=Decimal("0.05"),
                          htpasswd_file=str(auth), admin_users=("admin",))
    provider = MockProvider()
    visual_root = tmp_path / "visual-search"
    RuntimeSwitch(visual_root).set_enabled(True)
    service = production_service(visual_root, config, provider=provider, initialize=True)
    provider.record = service.store.record_call
    yield backend, service, provider, sync, tmp_path, config
    service.close()


class ForbiddenBusinessDB:
    def __getattr__(self, name):
        raise AssertionError("Production source accessed a development DB: " + name)


def build(service, limit=10):
    return service.rebuild(ForbiddenBusinessDB(), "admin", limit, False, True, background=False)


def test_namespace_shared_vectors_and_idempotent_index(production):
    _, service, provider, _, _, config = production
    assert service.config.index_key != config.index_key
    assert service.source.identity_key in str(service.store.root)
    result = build(service)
    assert result["source_id"] == PRODUCTION_ID and result["succeeded"] == 3
    assert result["usage"]["api_calls"] == provider.calls == 2
    rows = service.store.rows()
    assert set(rows) == {1, 2, 3} and rows[1]["embedding_vector"] == rows[2]["embedding_vector"]
    assert service.status(ForbiddenBusinessDB())["indexed_unique_images"] == 2
    repeated = build(service)
    assert repeated["processed"] == 0 and provider.calls == 2
    with service.store.connection() as db:
        context = dict(db.execute("SELECT * FROM visual_source_context").fetchone())
        assert context["source_id"] == PRODUCTION_ID and context["catalog_fingerprint"]
        assert context["dimension"] == 256 and context["model"] == service.config.model
    manifest = json.loads((service.store.root / "jobs" / f"{result['id']}.json").read_text(encoding="utf8"))
    assert manifest["source_id"] == PRODUCTION_ID and len(manifest["selected"]) == 3


def test_search_details_image_and_feedback_never_read_colliding_development_id(production):
    backend, service, provider, sync, root, config = production
    backend.products[0]["original_product_code"] = "2677-35"
    sync()
    build(service)
    # The development ID 1 deliberately refers to a different blue product.
    engine = create_engine(f"sqlite:///{(root / 'mock-business.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        p = Product(id=1, product_code="DEVELOPMENT-ONLY", size="different", unit="pcs", is_active=True)
        p.packagings = [ProductPackaging(packing_qty=12, carton_count=7)]
        db.add(p)
        db.commit()
    before = (root / "mock-business.db").read_bytes()
    dev_provider = MockProvider()
    app = create_app(data_directory=root, visual_config=config, visual_provider=dev_provider,
                     visual_production_provider=provider)
    app.state.visual_sources.production = service
    service.slot = app.state.visual_search.slot
    def forbidden_db():
        raise AssertionError("Production API created a business session")
        yield
    app.dependency_overrides[get_db] = forbidden_db
    with TestClient(app) as client:
        client.auth = ("admin", "mock-password")
        prefix = "?source=production-catalog"
        result = client.post("/api/visual-search/search" + prefix, files={"file": ("mock.png", image("red"), "image/png")})
        assert result.status_code == 200
        body = result.json()
        assert [c["product"]["id"] for c in body["candidates"]] == [1, 2, 3]
        product = body["candidates"][0]["product"]
        assert product["product_code"] == "MOCK-1" and product["product_key"] == PRODUCTION_ID + ":1"
        assert product["original_product_code"] == "2677-35"
        assert product["packagings"][0]["packing_qty"] == 24
        assert "price" not in product and "carton_count" not in product["packagings"][0]
        assert client.get(product["image_path"]).content == service.source.raw(service.source.get(None, 1))
        detail = client.get("/api/visual-search/catalog/products/1" + prefix).json()
        assert detail["product"]["product_key"] == product["product_key"]
        assert detail["product"]["original_product_code"] == "2677-35"
        query = body["query_id"]
        assert client.get(f"/api/visual-search/queries/{query}/image" + prefix).status_code == 200
        feedback = {"query_id": query, "selected_product_id": 2}
        assert client.post("/api/visual-search/feedback" + prefix, json=feedback).status_code == 200
        # Use an isolated dev DB for the wrong-source request; query IDs cannot cross stores.
        def isolated_db():
            with Session(engine) as db:
                yield db
        app.dependency_overrides[get_db] = isolated_db
        migrate(app.state.visual_search.store.path)
        dev_provider.record = app.state.visual_search.store.record_call
        assert client.post("/api/visual-search/feedback?source=development", json=feedback).status_code == 404
        assert client.get(f"/api/visual-search/queries/{query}/image?source=development").status_code == 404
        assert client.get("/api/visual-search/index/status?source=unknown").status_code == 422
    assert (root / "mock-business.db").read_bytes() == before
    engine.dispose()


def test_original_code_metadata_updates_preserve_vectors_and_ranking(production):
    backend, service, provider, sync, _, _ = production
    # Simulate legacy JSON documents in this temporary SQLite mirror.
    rows = service.source.mirror.products()
    with sqlite3.connect(service.source.mirror.path) as db:
        for row in rows:
            row.pop("original_product_code")
            db.execute("UPDATE mirror_products SET document=? WHERE id=?",
                       (json.dumps(row), row["id"]))
    assert service.source.get(None, 1).detail["original_product_code"] is None
    build(service)
    baseline = service.search(ForbiddenBusinessDB(), "admin", image("red"))
    assert all(c["product"]["original_product_code"] is None for c in baseline["candidates"])
    before_rows = service.store.rows()
    before_file = service.store.path.read_bytes()
    before_context = service.source.context()
    before_calls = provider.calls
    before_key = service.config.index_key
    backend.products[0]["original_product_code"] = "SHARED-2677"
    backend.products[1]["original_product_code"] = "SHARED-2677"
    sync()
    # Reopen the source as a later API process would, retaining the same index key.
    service.source = ProductionSource(service.source.mirror.root)
    context = service.source.context()
    assert context["catalog_fingerprint"] != before_context["catalog_fingerprint"]
    assert context["index_namespace"] == before_context["index_namespace"]
    assert replace(service.config, source_identity=service.source.identity_key).index_key == before_key
    assert service.store.path.read_bytes() == before_file
    plan = service.plan(ForbiddenBusinessDB())
    assert not plan["pending"] and len(plan["unchanged"]) == 3
    assert build(service)["processed"] == 0
    assert provider.calls == before_calls
    assert service.store.rows() == before_rows
    result = service.search(ForbiddenBusinessDB(), "admin", image("red"))
    assert [(c["product"]["id"], c["similarity"]) for c in result["candidates"]] == [
        (c["product"]["id"], c["similarity"]) for c in baseline["candidates"]]
    assert [c["product"]["original_product_code"] for c in result["candidates"]] == [
        "SHARED-2677", "SHARED-2677", None]
    assert service.store.rows() == before_rows


@pytest.mark.parametrize("original_code", [None, "2677-35"])
def test_development_source_exposes_original_code(original_code, tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            product = Product(product_code="DEV-1", original_product_code=original_code,
                              size="10cm", unit="pcs", is_active=True)
            db.add(product)
            db.commit()
            source = DevelopmentSource(tmp_path)
            assert source.get(db, product.id).detail["original_product_code"] == original_code
            assert source.products(db)[0].detail["original_product_code"] == original_code
    finally:
        engine.dispose()


def test_failed_shared_content_attempted_once_then_resume(production):
    _, service, provider, _, _, _ = production
    provider.fail = True
    result = build(service)
    assert result["failed"] == 3 and result["usage"]["api_calls"] == 2
    provider.fail = False
    resumed = build(service)
    assert resumed["succeeded"] == 3 and resumed["usage"]["api_calls"] == 2


def test_changed_reference_reuses_hash_changed_content_invalidates_and_inactive_filtered(production):
    backend, service, provider, sync, _, _ = production
    build(service)
    backend.products[0]["image_path"] = "products/main/moved.webp"
    backend.images["/uploads/products/main/moved.webp"] = backend.images["/uploads/products/main/shared.webp"]
    backend.etags["/uploads/products/main/moved.webp"] = "moved"
    sync()
    assert build(service)["usage"]["api_calls"] == 0
    assert provider.calls == 2
    backend.images["/uploads/products/main/moved.webp"] = photo("green")
    backend.etags["/uploads/products/main/moved.webp"] = "changed"
    backend.products[1]["is_active"] = False
    sync()
    plan = service.plan(ForbiddenBusinessDB())
    assert [r["product_id"] for r in plan["pending"]] == [1]
    assert build(service)["usage"]["api_calls"] == 1
    result = service.search(ForbiddenBusinessDB(), "admin", image("green"))
    assert 2 not in [c["product"]["id"] for c in result["candidates"]]


def test_model_version_dimension_and_mirror_source_cannot_reuse_cache(production):
    _, service, provider, _, root, config = production
    build(service)
    version = production_service(root / "visual-search", replace(config, version="different"), provider=provider)
    try:
        assert version.config.index_key != service.config.index_key
        assert version.rebuild(None, "admin")["unique_new_images"] == 2
        assert version.store.content_vector(service.store.rows()[1]["image_hash"]) is None
    finally:
        version.close()
    dimension = production_service(root / "visual-search", replace(config, dimension=512), provider=provider)
    try:
        assert dimension.config.index_key != service.config.index_key
        assert dimension.rebuild(None, "admin")["unique_new_images"] == 2
    finally:
        dimension.close()
    marker = service.store.root / "source-binding.json"
    binding = json.loads(marker.read_text(encoding="utf8"))
    binding["source_id"] = "wrong-source"
    marker.write_text(json.dumps(binding), encoding="utf8")
    with pytest.raises(VisualError, match="来源不一致"):
        production_service(root / "visual-search", config)


def test_paid_batch_gate_and_dryrun_no_calls_or_store_writes(production):
    _, service, provider, _, _, _ = production
    before = service.store.path.read_bytes()
    summary = service.rebuild(ForbiddenBusinessDB(), "admin", 10)
    assert summary["unique_new_images"] == summary["max_http_attempts"] == 2
    assert provider.calls == 0 and before == service.store.path.read_bytes()
    with pytest.raises(VisualError, match="单独确认"):
        build(service, 11)
    with pytest.raises(VisualError, match="1～100"):
        build(service, 2414)


def test_missing_or_tampered_mirror_never_falls_back_to_development(tmp_path):
    with pytest.raises(VisualError, match="生产图库不可用"):
        production_service(tmp_path, VisualConfig())


def test_legacy_vs01_is_readable_but_never_migrated_implicitly(tmp_path):
    from app.services.visual_search.store import VisualStore
    root = tmp_path / "phase15"
    root.mkdir()
    migrate(root / "index.db", "vs01")
    before = hashlib.sha256((root / "index.db").read_bytes()).hexdigest()
    store = VisualStore(root, VisualConfig())
    assert store.ready and not store.content_cache_ready
    assert before == hashlib.sha256((root / "index.db").read_bytes()).hexdigest()


def test_corrupt_content_cache_recomputed_and_tampered_image_excluded(production):
    _, service, provider, _, _, _ = production
    build(service)
    with service.store.connection() as db:
        db.execute("UPDATE visual_image_vectors SET embedding_vector=?", (b"broken",))
        db.execute("DELETE FROM visual_embeddings")
    assert service.rebuild(None, "admin")["unique_new_images"] == 2
    assert build(service)["usage"]["api_calls"] == 2
    path = service.source.image_path(1)
    path.write_bytes(photo("green"))
    plan = service.plan(ForbiddenBusinessDB())
    assert {r["product_id"] for r in plan["invalid_images"]} == {1, 2}
    result = service.search(ForbiddenBusinessDB(), "admin", image("red"))
    assert [c["product"]["id"] for c in result["candidates"]] == [3]


def test_cli_source_and_payment_gates_without_business_database(production, monkeypatch, capsys, tmp_path):
    _, service, provider, _, _, _ = production
    path = Path(__file__).resolve().parents[1] / "scripts/visual_search.py"
    spec = importlib.util.spec_from_file_location("production_visual_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr("app.services.visual_search.factory.production_service", lambda *a, **kw: service)
    monkeypatch.setattr(service, "close", lambda: None)
    monkeypatch.setattr("app.services.visual_search.config.VisualConfig.from_env", lambda: service.config)
    def run(*args):
        monkeypatch.setattr(sys, "argv", [str(path), *args])
        return module.main()
    assert run("index", "--source", "production-catalog") == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] and provider.calls == 0
    for args in [("index", "--source", "unknown"),
                 ("index", "--source", "production-catalog", "--execute-paid", "--max-items", "11"),
                 ("index", "--source", "production-catalog", "--max-items", "2414"),
                 ("init", "--source", "development")]:
        with pytest.raises(SystemExit):
            run(*args)
    manifest = tmp_path / "labels.json"
    manifest.write_text(json.dumps([{"image": "mock.jpg", "product_id": 1}]), encoding="utf8")
    with pytest.raises(SystemExit):
        run("evaluate", "--source", "production-catalog", "--manifest", str(manifest), "--execute-paid")
    assert provider.calls == 0
