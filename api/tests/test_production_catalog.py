"""Offline compatibility and safety cases. Synthetic products; no real network."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import sqlite3

import httpx
from PIL import Image
import pytest

from app.services.visual_search.production_catalog import (
    CatalogClient, CatalogError, ProductionCatalog, sync_catalog, initialize,
)
from app.services.visual_search.lock import CallLock

SOURCE = "mock-production"
LABEL = "MOCK fixture only"


def photo(color):
    output = BytesIO()
    Image.new("RGB", (60, 80), color).save(output, "WEBP")
    return output.getvalue()


def product(pid, path="products/main/shared.webp"):
    return {"id": pid, "product_code": f"MOCK-{pid}", "is_active": True,
            "category_id": 1, "warehouse_id": 1, "image_path": path,
            "thumbnail_path": "products/thumbs/mock.webp", "size": "10cm", "unit": "pcs",
            "packagings": [{"id": pid * 2, "packing_qty": 24, "sort_order": 0, "carton_count": 555}],
            "price": "991.23", "total_carton_count": 555,
            "remark": "PRIVATE-CUSTOMER-NOTE", "account": "DO-NOT-SAVE",
            "updated_at": "2026-10-10T01:00:00"}


class Backend:
    def __init__(self):
        self.products = [product(1), product(2), product(3, "products/main/other.webp")]
        self.images = {"/uploads/products/main/shared.webp": photo("red"), "/uploads/products/main/other.webp": photo("blue")}
        self.etags = {path: "version-1" for path in self.images}
        self.requests = []
        self.failures = {}
        self.product_pages = 0
        self.mutate_final = False
        self.bad_page = False
        self.broken_length = False

    def handle(self, request):
        assert request.method == "GET"
        assert request.url.host == "127.0.0.1" and request.url.port == 18102
        assert "authorization" not in request.headers
        self.requests.append(request)
        path = request.url.path
        remaining = self.failures.get(path, [])
        if remaining:
            outcome = remaining.pop(0)
            if outcome == "timeout":
                raise httpx.ReadTimeout("PRIVATE transport detail", request=request)
            return httpx.Response(outcome, json={"message": "PRIVATE failure body"})
        if path == "/api/products":
            assert request.url.params["status"] == "all"
            self.product_pages += 1
            rows = deepcopy(self.products)
            if self.mutate_final and self.product_pages % 2 == 0:
                rows[0]["size"] = "changed during download"
            page, size = int(request.url.params["page"]), int(request.url.params["page_size"])
            items = rows[(page - 1) * size:page * size]
            if self.bad_page and page == 2:
                items[0] = deepcopy(rows[0])
            return httpx.Response(200, json={"items": items, "total": len(rows), "page": page, "page_size": size})
        if path == "/api/categories":
            return httpx.Response(200, json=[{"id": 1, "name": "MOCK <script>category</script>", "parent_id": None,
                                            "code": "01", "sort_order": 0, "children": [], "price": "EXCLUDED"}])
        if path == "/api/warehouses":
            return httpx.Response(200, json=[{"id": 1, "name": "MOCK warehouse", "sort_order": 0, "carton_count": 555}])
        if path in self.images:
            if request.headers.get("if-none-match") == self.etags[path]:
                return httpx.Response(304)
            data = self.images[path]
            return httpx.Response(200, content=data, headers={"content-type": "image/webp", "etag": self.etags[path],
                                                          "content-length": str(len(data) + int(self.broken_length))})
        return httpx.Response(404)

    def client(self):
        return CatalogClient("http://127.0.0.1:18102", transport=httpx.MockTransport(self.handle), interval=.2)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Catalog tests must not access any network or AI API")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr("app.services.visual_search.production_catalog.time.sleep", lambda _: None)


@pytest.fixture
def fixture(tmp_path):
    return Backend(), tmp_path / "mirror"


def run(backend, root, *, full=False, execute=True, source=SOURCE, limit=10):
    client = backend.client()
    try:
        return sync_catalog(client, root, source, LABEL, limit=None if full else limit,
                            expected_total=len(backend.products) if full else None, execute=execute)
    finally:
        client.close()


def mirror(root):
    return ProductionCatalog(root, SOURCE)


def test_whitelist_identity_shared_images_offline_browser(fixture):
    backend, root = fixture
    report = run(backend, root)
    assert report["selected_products"] == 3 and report["ready_product_images"] == 3
    assert report["distinct_image_files"] == 2 and report["duplicate_product_links"] == 1
    assert report["packaging_rows"] == 3 and report["source_total"] == 3
    assert report["shared_reference_reused"] == 1 and report["ai_calls"] == 0
    m = mirror(root)
    assert m.get(SOURCE, 1)["product_key"] == SOURCE + ":1"
    assert m.image_path(SOURCE, 1) == m.image_path(SOURCE, 2)
    assert m.get(SOURCE, 1)["packagings"][0]["packing_qty"] == 24
    for path in list(root.rglob("*.json")) + [root / "catalog.sqlite", root / "browse.html"]:
        data = path.read_bytes()
        for secret in (b'PRIVATE-CUSTOMER-NOTE', b'991.23', b'DO-NOT-SAVE', b'carton_count', b'"price"'):
            assert secret not in data
    html = (root / "browse.html").read_text(encoding="utf8")
    assert "&lt;script&gt;" in html and "<script>category</script>" not in html
    assert "http://" not in html and "https://" not in html
    assert not m.audit()["local_image_failures"]
    with pytest.raises(CatalogError):
        m.get("development", 1)
    with pytest.raises(CatalogError):
        ProductionCatalog(root, "another-production")
    with pytest.raises(sqlite3.OperationalError):
        with m._connect() as db:
            db.execute("DELETE FROM mirror_products")


def test_dry_run_does_not_create_directory_or_download(fixture):
    backend, root = fixture
    report = run(backend, root, execute=False)
    assert report["dry_run"] and report["images_downloaded"] == 0
    assert not root.exists()
    assert all(not r.url.path.startswith("/uploads") for r in backend.requests)


def test_sample_limit_and_full_pagination(fixture):
    backend, root = fixture
    backend.products = [product(i) for i in range(1, 102)]
    report = run(backend, root, limit=10)
    assert report["selected_products"] == 10 and report["source_total"] == 101
    assert len(mirror(root).products()) == 10
    before_namespace = mirror(root).index_namespace
    before_path = mirror(root).image_path(SOURCE, 1)
    before_image = (before_path.read_bytes(), before_path.stat().st_mtime_ns)
    (root / "human-labels.json").write_text('{"keep":"sample-label"}', encoding="utf8")
    assert all(int(r.url.params["page_size"]) == 10 for r in backend.requests if r.url.path == "/api/products")
    backend.requests.clear()
    report = run(backend, root, full=True)
    assert report["selected_products"] == 101
    assert len(mirror(root).products()) == 101
    assert report["files_downloaded"] == 0 and report["files_not_modified"] == 1
    assert mirror(root).index_namespace == before_namespace
    assert before_image == (before_path.read_bytes(), before_path.stat().st_mtime_ns)
    assert (root / "human-labels.json").read_text(encoding="utf8") == '{"keep":"sample-label"}'
    assert [int(r.url.params["page"]) for r in backend.requests if r.url.path == "/api/products"] == [1, 2, 1, 2]
    assert (root / "browse-2.html").is_file()


def test_incremental_304_changed_image_inactive_and_labels_preserved(fixture):
    backend, root = fixture
    run(backend, root)
    labels = root / "human-labels.json"
    labels.write_text('{"correct":"keep"}', encoding="utf8")
    old_image = mirror(root).image_path(SOURCE, 1)
    report = run(backend, root)
    assert report.get("files_downloaded", 0) == 0 and report["files_not_modified"] == 2
    backend.images["/uploads/products/main/shared.webp"] = photo("green")
    backend.etags["/uploads/products/main/shared.webp"] = "version-2"
    backend.products[2]["is_active"] = False
    report = run(backend, root)
    assert report["files_downloaded"] == 1 and report["files_not_modified"] == 1
    assert mirror(root).image_path(SOURCE, 1) != old_image and old_image.exists()
    assert not mirror(root).get(SOURCE, 3)["is_active"]
    with pytest.raises(CatalogError):
        mirror(root).image_path(SOURCE, 3)
    assert labels.read_text(encoding="utf8") == '{"correct":"keep"}'


def test_delete_only_full_stable_scan_preserves_history(fixture):
    backend, root = fixture
    run(backend, root, full=True)
    backend.products = backend.products[1:]
    run(backend, root)
    assert mirror(root).get(SOURCE, 1)["source_present"]
    run(backend, root, full=True)
    assert not mirror(root).get(SOURCE, 1)["source_present"]
    assert mirror(root).audit()["invalidated_products"] == 1
    assert len(mirror(root).products()) == 3 and len(list((root / "manifests").glob("*.json"))) == 3


@pytest.mark.parametrize("outcome", [[503, 503], ["timeout"], [404], [200]])
def test_failed_image_retry_and_next_run_resume(fixture, outcome):
    backend, root = fixture
    if outcome == [200]:
        backend.images["/uploads/products/main/other.webp"] = b"broken not an image"
    else:
        backend.failures["/uploads/products/main/other.webp"] = list(outcome)
    report = run(backend, root)
    if outcome in [[503, 503], ["timeout"]]:
        assert report["failed_main_images"] == 0 and report["network"]["retries"] == len(outcome)
    else:
        assert report["failed_main_images"] == 1 and not report["images_complete"]
        backend.images["/uploads/products/main/other.webp"] = photo("blue")
        report = run(backend, root)
        assert report["failed_main_images"] == 0 and report["files_downloaded"] == 1
    assert "PRIVATE" not in json.dumps(report)


def test_network_exhaustion_and_bad_content_length(fixture):
    backend, root = fixture
    backend.failures["/uploads/products/main/other.webp"] = [503, 503, 503]
    report = run(backend, root)
    assert report["failed_main_images"] == 1 and report["network"]["retries"] == 2
    assert not report["images_complete"]
    backend.broken_length = True
    # Clear cache to force a body download rather than 304.
    (root / "download-cache.json").unlink()
    report = run(backend, root)
    assert report["failed_main_images"] == 3
    assert all("长度" in r["reason"] for r in report["image_failures"])


def test_unstable_scan_does_not_replace_published_catalog(fixture):
    backend, root = fixture
    run(backend, root)
    before = (root / "catalog.sqlite").read_bytes()
    backend.product_pages = 0
    backend.mutate_final = True
    with pytest.raises(CatalogError, match="清单不一致"):
        run(backend, root)
    assert (root / "catalog.sqlite").read_bytes() == before
    assert any(not json.loads(p.read_text(encoding="utf8"))["published"] for p in (root / "reports").glob("*.json"))


def test_pagination_conflict_expected_total_and_auth_fail(fixture):
    backend, root = fixture
    client = backend.client()
    with pytest.raises(CatalogError, match="总数"):
        sync_catalog(client, root, SOURCE, LABEL, limit=None, expected_total=2414, execute=True)
    assert not (root / "catalog.sqlite").exists()
    backend.products = [product(i) for i in range(1, 102)]
    backend.bad_page = True
    with pytest.raises(CatalogError, match="冲突"):
        run(backend, root, full=True)
    backend.failures["/api/products"] = [401]
    with pytest.raises(CatalogError, match="HTTP 401") as caught:
        run(backend, root)
    assert "PRIVATE" not in str(caught.value)
    client.close()


@pytest.mark.parametrize("identifier", ["../../secret", "https://other/image", "/absolute", "products/%2e%2e/secret", "products\\main\\x", "products/x?token=secret"])
def test_invalid_image_reference_never_requested(fixture, identifier):
    backend, root = fixture
    backend.products = [product(1, identifier)]
    report = run(backend, root)
    assert report["failed_main_images"] == 1
    assert not any(r.url.path.startswith("/uploads") for r in backend.requests)


@pytest.mark.parametrize("url", ["http://8.136.127.226:8082", "https://example.com", "http://localhost:18102", "http://root:password@127.0.0.1:18102", "http://127.0.0.1:18102/api", "http://127.0.0.1:bad"])
def test_connection_restrictions(url):
    with pytest.raises(CatalogError):
        CatalogClient(url)


def test_existing_data_protected_and_other_source_rejected(fixture, tmp_path):
    backend, root = fixture
    for name in ("index.db", "inventory.db", "unknown.txt"):
        protected = tmp_path / name.replace(".", "-")
        protected.mkdir()
        (protected / name).write_bytes(b"keep-existing")
        with pytest.raises(CatalogError):
            run(backend, protected)
        assert (protected / name).read_bytes() == b"keep-existing"
    run(backend, root)
    with pytest.raises(CatalogError, match="来源"):
        run(backend, root, source="another-production")


def test_corrupt_cache_detected_and_repaired(fixture):
    backend, root = fixture
    run(backend, root)
    path = mirror(root).image_path(SOURCE, 1)
    path.write_bytes(photo("black"))
    assert len(mirror(root).audit()["local_image_failures"]) == 2
    report = run(backend, root)
    assert report["local_cache_invalid"] >= 1
    assert report["failed_main_images"] == 0 and not mirror(root).audit()["local_image_failures"]


def test_null_qty_missing_image_and_packaging_preserved(fixture):
    backend, root = fixture
    backend.products[0]["packagings"][0]["packing_qty"] = None
    backend.products[1]["image_path"] = None
    backend.products[2]["packagings"] = []
    report = run(backend, root)
    assert report["missing_main_images"] == 1 and report["unknown_packing_qty_rows"] == 1
    assert report["missing_packaging_products"] == 1
    assert mirror(root).get(SOURCE, 1)["packagings"][0]["packing_qty"] is None


def test_sync_lock_and_full_gate(fixture):
    backend, root = fixture
    initialize(root, SOURCE, LABEL)
    lock = CallLock(root)
    assert lock.acquire()
    try:
        with pytest.raises(CatalogError, match="运行中"):
            run(backend, root)
        assert not backend.requests
    finally:
        lock.release()
    with pytest.raises(CatalogError, match="预期"):
        sync_catalog(backend.client(), root, SOURCE, LABEL, limit=None, execute=True)


def test_redirect_not_followed(fixture):
    _, root = fixture
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "http://external.invalid"})
    client = CatalogClient("http://127.0.0.1:18102", transport=httpx.MockTransport(handler), interval=.2)
    with pytest.raises(CatalogError, match="HTTP 302"):
        sync_catalog(client, root, SOURCE, LABEL, execute=True)
    assert len(calls) == 1
    client.close()


def test_hardlinked_catalog_cannot_change_another_database(fixture, tmp_path):
    backend, root = fixture
    initialize(root, SOURCE, LABEL)
    protected = tmp_path / "development.db"
    protected.write_bytes(b"keep-business-database")
    os.link(protected, root / "catalog.sqlite")
    with pytest.raises(CatalogError, match="硬链接"):
        run(backend, root)
    assert protected.read_bytes() == b"keep-business-database"
    assert not backend.requests


def test_changed_reference_new_product_and_distinct_paths_same_content(fixture):
    backend, root = fixture
    run(backend, root)
    backend.products[0]["image_path"] = "products/main/replaced.webp"
    backend.images["/uploads/products/main/replaced.webp"] = photo("yellow")
    backend.etags["/uploads/products/main/replaced.webp"] = "new"
    backend.products.append(product(4, "products/main/copy.webp"))
    backend.images["/uploads/products/main/copy.webp"] = backend.images["/uploads/products/main/shared.webp"]
    backend.etags["/uploads/products/main/copy.webp"] = "copy"
    report = run(backend, root)
    assert report["selected_products"] == 4 and report["distinct_image_files"] == 3
    assert mirror(root).get(SOURCE, 1)["image_identifier"] == "products/main/replaced.webp"
    assert mirror(root).image_path(SOURCE, 2) == mirror(root).image_path(SOURCE, 4)
    assert report["files_content_reused"] == 1


def test_missing_required_contract_rejected_before_images(fixture):
    backend, root = fixture
    del backend.products[0]["packagings"][0]["packing_qty"]
    with pytest.raises(CatalogError, match="必要字段"):
        run(backend, root)
    assert not any(r.url.path.startswith("/uploads") for r in backend.requests)


def test_full_2414_dryrun_two_passes_and_sample_unchanged(fixture):
    backend, root = fixture
    backend.products = [product(i) for i in range(1, 2415)]
    run(backend, root, limit=10)
    before = {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}
    backend.requests.clear()
    result = run(backend, root, full=True, execute=False)
    assert result["source_total"] == result["selected_products"] == 2414
    assert result["snapshot_passes"] == 2 and result["pages_per_pass"] == 25
    assert result["pagination_consistent"] and not result["published"]
    assert result["existing_mirror_products"] == result["existing_ids_in_selection"] == 10
    assert result["would_invalidate_products"] == 0
    assert result["unique_image_references"] == result["verified_cached_image_references"] == 1
    assert result["duplicate_reference_links"] == 2413
    assert result["limits"]["concurrency"] == 1 and result["limits"]["interval_seconds"] == .2
    assert result["images_downloaded"] == result["ai_calls"] == 0
    pages = [int(r.url.params["page"]) for r in backend.requests if r.url.path == "/api/products"]
    assert pages == list(range(1, 26)) * 2 and len(backend.requests) == 54
    assert not any(r.url.path.startswith("/uploads") for r in backend.requests)
    assert before == {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_dryrun_detects_content_change_between_passes(fixture):
    backend, root = fixture
    backend.mutate_final = True
    with pytest.raises(CatalogError, match="两遍商品清单不一致"):
        run(backend, root, execute=False)
    assert not root.exists()
    assert not any(r.url.path.startswith("/uploads") for r in backend.requests)


@pytest.mark.parametrize("when", ["second_page", "second_pass"])
def test_total_change_aborts_full_before_publish(fixture, when):
    backend, root = fixture
    run(backend, root)
    before = (root / "catalog.sqlite").read_bytes()
    backend.products = [product(i) for i in range(1, 102)]
    backend.product_pages = 0
    backend.requests.clear()
    original = backend.handle
    def handle(request):
        response = original(request)
        if request.url.path == "/api/products" and backend.product_pages == (2 if when == "second_page" else 3):
            body = response.json()
            body["total"] += 1
            return httpx.Response(200, json=body)
        return response
    client = CatalogClient("http://127.0.0.1:18102", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(CatalogError, match="总数"):
            sync_catalog(client, root, SOURCE, LABEL, limit=None, expected_total=101, execute=True)
    finally:
        client.close()
    assert (root / "catalog.sqlite").read_bytes() == before
    if when == "second_page":
        assert not any(r.url.path.startswith("/uploads") for r in backend.requests)


def test_disk_insufficient_blocks_images_and_keeps_catalog(fixture, monkeypatch):
    from types import SimpleNamespace
    backend, root = fixture
    run(backend, root)
    before = (root / "catalog.sqlite").read_bytes()
    backend.requests.clear()
    monkeypatch.setattr("app.services.visual_search.production_catalog.shutil.disk_usage", lambda _: SimpleNamespace(free=0))
    result = run(backend, root, execute=False)
    assert not result["disk"]["sufficient"] and result["disk"]["required_free_bytes"] >= 1024 ** 3
    with pytest.raises(CatalogError, match="磁盘空间不足"):
        run(backend, root)
    assert (root / "catalog.sqlite").read_bytes() == before
    assert not any(r.url.path.startswith("/uploads") for r in backend.requests)


@pytest.mark.parametrize("interval", [0, .1, float("nan"), float("inf")])
def test_minimum_interval_enforced(interval):
    with pytest.raises(CatalogError, match="间隔"):
        CatalogClient("http://127.0.0.1:18102", interval=interval)


def test_request_spacing_and_retry_backoff(fixture, monkeypatch):
    backend, _ = fixture
    pauses = []
    monkeypatch.setattr("app.services.visual_search.production_catalog.time.sleep", pauses.append)
    backend.failures["/api/warehouses"] = [429, 503]
    client = backend.client()
    try:
        assert client.json("/api/warehouses")
        assert client.stats["http_requests"] == 3 and client.stats["retries"] == 2
    finally:
        client.close()
    assert pauses == [.2, 1, .2, 2, .2]


def test_interrupted_download_resumes_completed_files_and_releases_lock(fixture):
    backend, root = fixture
    original = backend.handle
    interrupted = False
    def handle(request):
        nonlocal interrupted
        if request.url.path == "/uploads/products/main/other.webp" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt()
        return original(request)
    client = CatalogClient("http://127.0.0.1:18102", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(CatalogError, match="已中断"):
            sync_catalog(client, root, SOURCE, LABEL, execute=True)
    finally:
        client.close()
    assert not (root / "catalog.sqlite").exists()
    assert len(list((root / "images").glob("*"))) == 1
    reports = [json.loads(p.read_text(encoding="utf8")) for p in (root / "reports").glob("*.json")]
    assert len(reports) == 1 and not reports[0]["published"] and reports[0]["finished_at"]
    resumed = run(backend, root)
    assert resumed["files_not_modified"] == 1 and resumed["files_downloaded"] == 1
    assert resumed["failed_main_images"] == 0 and resumed["published"]


def test_local_io_exception_recorded_without_private_error_text(fixture, monkeypatch):
    backend, root = fixture
    run(backend, root)
    before = (root / "catalog.sqlite").read_bytes()
    def fail(*args, **kwargs):
        raise OSError("PRIVATE disk error or credential detail")
    monkeypatch.setattr("app.services.visual_search.production_catalog.publish", fail)
    with pytest.raises(CatalogError, match="OSError"):
        run(backend, root)
    assert (root / "catalog.sqlite").read_bytes() == before
    reports = [json.loads(p.read_text(encoding="utf8")) for p in (root / "reports").glob("*.json")]
    failed = [r for r in reports if not r["published"]]
    assert len(failed) == 1 and failed[0]["finished_at"] and "PRIVATE" not in json.dumps(failed)
