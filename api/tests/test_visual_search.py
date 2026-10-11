from dataclasses import replace
from io import BytesIO
import json
from pathlib import Path
import socket

from alembic import command
from alembic.config import Config
import bcrypt
from fastapi.testclient import TestClient
import httpx
import numpy as np
from PIL import Image
from passlib.hash import apr_md5_crypt
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base, get_db
from app.main import create_app
from app.models import Product, ProductPackaging
from app.services.visual_search.config import VisualConfig
from app.services.visual_search.images import VisualError, gallery_image, image_hash, prepare_image
from app.services.visual_search.provider import DashScopeProvider, Embedding, normalize
from app.services.visual_search.service import VisualService, rank_candidates

API_ROOT = Path(__file__).resolve().parents[1]


def migrate(path, target="head"):
    config = Config(str(API_ROOT / "visual-alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path.as_posix()}")
    if target == "base":
        command.downgrade(config, target)
    else:
        command.upgrade(config, target)


def image(color="red", size=(100, 80), image_format="PNG", exif=None):
    output = BytesIO()
    Image.new("RGB", size, color).save(output, image_format, **({"exif": exif} if exif else {}))
    return output.getvalue()


def color_vector(data):
    color = np.asarray(Image.open(BytesIO(data)).resize((1, 1)).convert("RGB"), dtype=np.float32).reshape(3)
    vector = np.zeros(256, dtype=np.float32)
    vector[:3] = color
    return normalize(vector, 256)


class MockProvider:
    """Deterministic colour fixture, not a production visual model."""
    def __init__(self):
        self.calls = 0
        self.fail = False
        self.record = None

    def embed(self, data, purpose, task_id):
        self.calls += 1
        if self.fail:
            self.record(purpose, task_id, {}, None, 1, "测试服务异常")
            raise VisualError("测试服务异常", 502)
        usage = {"input_tokens": 4, "image_tokens": 100, "total_tokens": 104}
        self.record(purpose, task_id, usage, "mock-test-only", 1, None)
        return Embedding(color_vector(data), usage)


@pytest.fixture(autouse=True)
def forbid_external_network(monkeypatch):
    original = socket.socket.connect
    def forbidden(sock, address):
        if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
            return original(sock, address)
        raise AssertionError("Mock tests must not make external API calls")
    monkeypatch.setattr(socket.socket, "connect", forbidden)


@pytest.fixture
def visual(tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    root = tmp_path / "visual-search"
    root.mkdir()
    migrate(root / "index.db")
    engine = create_engine(f"sqlite:///{(tmp_path / 'business.db').as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    credentials = tmp_path / ".htpasswd"
    credentials.write_text("admin:" + apr_md5_crypt.hash("test-password") + "\nviewer:" + apr_md5_crypt.hash("viewer-password"), encoding="utf8")
    config = VisualConfig(dimension=256, api_key="mock-test-only", interval=0.1, htpasswd_file=str(credentials), admin_users=("admin",))
    provider = MockProvider()
    application = create_app(data_directory=tmp_path, visual_config=config, visual_provider=provider)
    service = application.state.visual_search
    provider.record = service.store.record_call
    def override_db():
        with Session(engine) as db:
            yield db
    application.dependency_overrides[get_db] = override_db
    with TestClient(application) as client:
        client.auth = ("admin", "test-password")
        yield client, service, engine, uploads, provider
    engine.dispose()


def add_product(visual, color="red", active=True, no_image=False):
    _, _, engine, uploads, _ = visual
    with Session(engine) as db:
        product = Product(is_active=active, image_path=None, size="10x20", unit="pcs")
        product.packagings = [ProductPackaging(packing_qty=12, carton_count=7)]
        db.add(product)
        db.flush()
        pid = product.id
        if not no_image:
            identifier = f"product-{pid}.png"
            (uploads / identifier).write_bytes(image(color))
            product.image_path = identifier
        db.commit()
        return pid


def index(visual, max_items=10):
    _, service, engine, _, _ = visual
    with Session(engine) as db:
        return service.rebuild(db, "admin", max_items, False, True, background=False)


def search(client, data=None, crop=None):
    return client.post("/api/visual-search/search", files={"file": ("query.png", data or image(), "image/png")}, data={"crop": json.dumps(crop)} if crop is not None else {})


def test_mock_same_image_ranking_feedback_and_business_unchanged(visual):
    client, service, engine, _, provider = visual
    red = add_product(visual, "red")
    blue = add_product(visual, "blue")
    red_tie = add_product(visual, "red")
    assert index(visual)["succeeded"] == 3
    result = search(client).json()
    ids = [c["product"]["id"] for c in result["candidates"]]
    assert ids == [red, red_tie, blue]
    assert result["candidates"][0]["similarity"] > .99
    assert result["call_summary"]["api_calls"] == 1
    assert provider.calls == 4
    assert client.post("/api/visual-search/feedback", json={"query_id": result["query_id"], "selected_product_id": red}).json()["saved"]
    assert client.post("/api/visual-search/feedback", json={"query_id": result["query_id"], "no_match": True}).status_code == 200
    assert client.get(f"/api/visual-search/queries/{result['query_id']}/image").status_code == 200
    with Session(engine) as db:
        assert all(p.packagings[0].carton_count == 7 for p in db.scalars(select(Product)).all())
    assert service.store.usage_summary()["image_tokens"] == 400


def test_empty_gallery_and_no_image_make_no_calls(visual):
    client, _, _, _, provider = visual
    add_product(visual, no_image=True)
    result = search(client)
    assert result.status_code == 200 and result.json()["candidates"] == []
    assert provider.calls == 0
    assert index(visual)["processed"] == 0
    status = client.get("/api/visual-search/index/status").json()
    assert status["skipped"] == 1 and "无主图" in status["issues"][0]["reason"]


def test_dry_run_limit_incremental_changes_deactivation_deletion(visual):
    client, service, engine, uploads, provider = visual
    first = add_product(visual, "red")
    second = add_product(visual, "blue")
    before = service.store.path.read_bytes()
    result = client.post("/api/visual-search/index/rebuild", json={"max_items": 1}).json()
    assert result["would_process"] == 1 and result["pending_total"] == 2
    assert before == service.store.path.read_bytes() and provider.calls == 0
    assert index(visual, 1)["succeeded"] == 1
    assert index(visual)["succeeded"] == 1
    assert index(visual)["succeeded"] == 0
    (uploads / f"product-{first}.png").write_bytes(image("green"))
    # Same filename but changed bytes cannot return its old vector.
    assert first not in [c["product"]["id"] for c in search(client).json()["candidates"]]
    assert index(visual)["succeeded"] == 1
    with Session(engine) as db:
        db.get(Product, first).is_active = False
        db.delete(db.get(Product, second))
        db.commit()
    assert search(client).json()["candidates"] == []
    index(visual)
    assert all(row["indexing_status"] != "ready" for row in service.store.rows().values())


def test_failure_then_resume_and_no_paid_confirmation(visual):
    client, service, _, _, provider = visual
    add_product(visual)
    assert client.post("/api/visual-search/index/rebuild", json={"dry_run": False}).status_code == 422
    provider.fail = True
    assert index(visual)["failed"] == 1
    assert client.get("/api/visual-search/index/status").json()["failed"] == 1
    provider.fail = False
    assert index(visual)["succeeded"] == 1
    provider.fail = True
    assert search(client).status_code == 502
    assert service.slot.acquire(blocking=False)
    service.slot.release()


def test_interrupted_job_and_new_model_isolation(visual):
    _, service, _, uploads, _ = visual
    add_product(visual)
    index(visual)
    with service.store.connection() as db:
        db.execute("INSERT INTO visual_jobs (id,index_key,owner,status,max_items,started_at) VALUES ('crashed',?,'admin','running',10,'2026-01-01')", (service.config.index_key,))
    restarted = VisualService(service.store.root, uploads, service.config, MockProvider())
    assert restarted.store.job("crashed")["status"] == "interrupted"
    alternate = VisualService(service.store.root, uploads, replace(service.config, version="v2"), MockProvider())
    assert alternate.store.rows() == {}
    assert alternate.config.index_key != service.config.index_key
    alternate.close()
    restarted.close()


@pytest.mark.parametrize("bad", [b"not image", image(image_format="GIF"), image() + b"x" * (10 * 1024 * 1024)], ids=["invalid", "gif", "oversize"])
def test_invalid_images_no_model_calls(visual, bad):
    client, _, _, _, provider = visual
    assert search(client, bad).status_code in {400, 413, 415}
    assert provider.calls == 0


@pytest.mark.parametrize("crop", [[0, 0, 2, 1], [0, 0, 0, .5], [0, 0, float("nan"), 1], [True, 0, 1, 1], [0, 0, .01, .01], [1, 2]])
def test_invalid_crop(visual, crop):
    assert search(visual[0], crop=crop).status_code in {400, 422}


def test_multi_product_crop_and_query_reference(visual):
    client, _, _, _, _ = visual
    red = add_product(visual, "red")
    blue = add_product(visual, "blue")
    index(visual)
    source = Image.new("RGB", (200, 100), "red")
    source.paste(Image.new("RGB", (100, 100), "blue"), (100, 0))
    output = BytesIO()
    source.save(output, "PNG")
    one = search(client, output.getvalue(), [0, 0, .5, 1]).json()
    two = search(client, output.getvalue(), [.5, 0, .5, 1]).json()
    assert one["query_id"] != two["query_id"]
    assert one["candidates"][0]["product"]["id"] == red
    assert two["candidates"][0]["product"]["id"] == blue
    normalized = client.get(f"/api/visual-search/queries/{two['query_id']}/image").content
    assert Image.open(BytesIO(normalized)).size == (100, 100)


def test_dimensions_and_stable_sort():
    assert rank_candidates([1, 0], {3: [1, 0], 1: [1, 0], 2: [0, 1]}, 2) == [(1, 1), (3, 1), (2, 0)]
    assert rank_candidates([1, 0], {}, 2) == []
    assert rank_candidates([1, 0], {1: [0, 1]}, 2, min_score=.1) == []
    for bad in [[1], [0, 0], [float("nan"), 1]]:
        with pytest.raises(VisualError):
            normalize(bad, 2)


def test_auth_all_endpoints_no_paid_calls(visual):
    client, _, _, _, provider = visual
    paths = [("GET", "/index/status"), ("POST", "/index/rebuild"), ("POST", "/feedback"), ("GET", "/queries/unknown/image"), ("GET", "/index/jobs/unknown")]
    for auth in [None, ("admin", "wrong"), ("viewer", "viewer-password")]:
        client.auth = auth
        for method, suffix in paths:
            assert client.request(method, "/api/visual-search" + suffix, json={} if method == "POST" else None).status_code == 401
        assert search(client).status_code == 401
    assert provider.calls == 0


def test_bcrypt_htpasswd_and_unconfigured_auth(visual):
    client, service, _, _, _ = visual
    Path(service.config.htpasswd_file).write_text("admin:" + bcrypt.hashpw(b"test-password", bcrypt.gensalt()).decode(), encoding="utf8")
    assert client.get("/api/visual-search/index/status").status_code == 200
    client.app.state.visual_config = replace(service.config, htpasswd_file="")
    assert client.get("/api/visual-search/index/status").status_code == 503


def test_feedback_rejects_wrong_candidates_and_inactive(visual):
    client, _, engine, _, _ = visual
    pid = add_product(visual)
    index(visual)
    query_id = search(client).json()["query_id"]
    assert client.post("/api/visual-search/feedback", json={"query_id": query_id, "selected_product_id": 999}).status_code == 422
    assert client.post("/api/visual-search/feedback", json={"query_id": query_id, "selected_product_id": pid, "no_match": True}).status_code == 422
    with Session(engine) as db:
        db.get(Product, pid).is_active = False
        db.commit()
    assert client.post("/api/visual-search/feedback", json={"query_id": query_id, "selected_product_id": pid}).status_code == 422


def test_missing_key_status_dryrun_and_real_request_blocked(visual):
    client, service, _, _, provider = visual
    add_product(visual)
    service.config = replace(service.config, api_key="")
    assert client.get("/api/visual-search/index/status").json()["api_key_configured"] is False
    assert client.post("/api/visual-search/index/rebuild", json={}).status_code == 200
    assert client.post("/api/visual-search/index/rebuild", json={"dry_run": False, "confirm_paid": True}).status_code == 503
    assert provider.calls == 0


def test_concurrency_guard(visual):
    client, service, _, _, provider = visual
    add_product(visual)
    index(visual)
    service.slot.acquire()
    try:
        assert search(client).status_code == 409
        assert client.post("/api/visual-search/index/rebuild", json={"dry_run": False, "confirm_paid": True}).status_code == 409
    finally:
        service.slot.release()
    assert provider.calls == 1


def test_cross_site_paid_requests_rejected(visual):
    client, _, _, _, provider = visual
    assert client.post("/api/visual-search/index/rebuild", json={}, headers={"Origin": "https://attacker.example"}).status_code == 403
    assert client.post("/api/visual-search/index/rebuild", json={}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert provider.calls == 0


def test_cli_and_api_share_lock_without_interrupting_live_job(visual):
    _, service, _, uploads, _ = visual
    assert service._acquire()
    try:
        with service.store.connection() as db:
            db.execute("INSERT INTO visual_jobs (id,index_key,owner,status,max_items,started_at) VALUES ('live',?,'admin','running',10,'2026-01-01')", (service.config.index_key,))
        another = VisualService(service.store.root, uploads, service.config, MockProvider())
        assert another.store.job("live")["status"] == "running"
        assert not another._acquire()
        another.close()
    finally:
        service._release()


def test_paths_and_preprocessing_orientation(tmp_path):
    with pytest.raises(VisualError):
        gallery_image(tmp_path, "../../secret")
    with pytest.raises(VisualError):
        gallery_image(tmp_path, "https://example.com/image.png")
    exif = Image.Exif()
    exif[274] = 6
    assert Image.open(BytesIO(prepare_image(image(size=(120, 80), image_format="JPEG", exif=exif)))).size == (80, 120)


def test_separate_migration_rolls_back_only_visual_tables(tmp_path):
    path = tmp_path / "index.db"
    migrate(path)
    import sqlite3
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='visual_feedback'").fetchone()
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='products'").fetchone()
    migrate(path, "base")
    with sqlite3.connect(path) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'visual_%'").fetchall()
    migrate(path)


@pytest.mark.parametrize("response_kind", ["success", "legacy_image", "wrong_type", "timeout", "server", "auth", "malformed", "dimension"])
def test_dashscope_native_contract_usage_timeout_errors(visual, response_kind, monkeypatch):
    _, service, _, _, _ = visual
    calls = []
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == service.config.endpoint
        assert body["model"] == "qwen3-vl-embedding"
        assert body["parameters"]["dimension"] == 256
        assert body["input"]["contents"][0]["image"].startswith("data:image/jpeg;base64,")
        assert request.headers["Authorization"] == "Bearer mock-test-only"
        calls.append(request)
        if response_kind == "timeout":
            raise httpx.ReadTimeout("private transport detail", request=request)
        if response_kind in {"server", "auth"}:
            return httpx.Response(503 if response_kind == "server" else 401, json={"message": "private detail"})
        if response_kind == "malformed":
            return httpx.Response(200, json={"output": {}})
        response_type = "image" if response_kind == "legacy_image" else "text" if response_kind == "wrong_type" else "vl"
        return httpx.Response(200, json={"output": {"embeddings": [{"embedding": [1] * (4 if response_kind == "dimension" else 256), "type": response_type, "index": 0}]}, "usage": {"input_tokens": 5, "image_tokens": 100, "total_tokens": 105}, "request_id": "test"})
    provider = DashScopeProvider(service.config, service.store.record_call, httpx.MockTransport(handler))
    monkeypatch.setattr("app.services.visual_search.provider.time.sleep", lambda _: None)
    if response_kind in {"success", "legacy_image"}:
        embedded = provider.embed(prepare_image(image()), "search", "contract")
        assert embedded.vector.shape == (256,) and np.linalg.norm(embedded.vector) == pytest.approx(1)
        assert embedded.request_id == "test"
        assert embedded.usage == {"input_tokens": 5, "image_tokens": 100, "total_tokens": 105}
        usage = service.store.usage_summary("contract")
        assert usage["total_tokens"] == 105
        assert usage["estimated_cost_cny"] == pytest.approx((5 * .0007 + 100 * .0018) / 1000)
    else:
        with pytest.raises(VisualError):
            provider.embed(prepare_image(image()), "search", "contract")
        assert len(calls) == (2 if response_kind in {"timeout", "server"} else 1)
        assert service.store.usage_summary("contract")["failed_calls"] == len(calls)


def test_rebuild_background_and_max_limit(visual):
    client, service, _, _, _ = visual
    add_product(visual)
    assert client.post("/api/visual-search/index/rebuild", json={"max_items": 101}).status_code == 422
    response = client.post("/api/visual-search/index/rebuild", json={"dry_run": False, "confirm_paid": True, "max_items": 1})
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    service.executor.submit(lambda: None).result(timeout=5)
    assert client.get(f"/api/visual-search/index/jobs/{job_id}").json()["succeeded"] == 1
