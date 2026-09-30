"""Reader and disk-lifecycle regressions for preview closeout."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import Request, UploadFile
from fastapi.testclient import TestClient

from app.main import create_app
from app.routers.product_import import preview_product_import, start_preview_task
from app.services.product_import import commit_service, preview_tasks, session_store
from app.services.product_import.excel_reader import read_import_workbook
from tests.test_product_import import image_bytes, import_context, preview, seed_reference_data, valid_row, workbook_bytes
from tests.test_product_import_tasks import normal_workbook, start, wait_terminal


def expire(directory):
    old = time.time() - session_store.PREVIEW_SESSION_TTL_SECONDS - 10
    for name in [session_store.SOURCE_FILE_NAME, session_store.PREVIEW_FILE_NAME]:
        path = directory / name
        if path.exists():
            os.utime(path, (old, old))
    os.utime(directory, (old, old))


def without_dimensions(data):
    output = BytesIO()
    with ZipFile(BytesIO(data)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name in source.namelist():
            content = source.read(name)
            if name.startswith("xl/worksheets/") and name.endswith(".xml"):
                root = ElementTree.fromstring(content)
                for node in list(root):
                    if node.tag.endswith("}dimension"):
                        root.remove(node)
                content = ElementTree.tostring(root)
            target.writestr(name, content)
    return output.getvalue()


def test_trailing_image_row_survives_shorter_value_iterator(import_context):
    client, factory, _, _, _ = import_context
    seed_reference_data(factory)
    data = workbook_bytes([valid_row(), None], images={
        2: [image_bytes((1, 2, 3))], 3: [image_bytes((4, 5, 6))],
    })
    rows = read_import_workbook(data)
    assert [row.excel_row for row in rows] == [2, 3]
    assert rows[1].images
    assert all(value is None for value in rows[1].values.values())
    result = preview(client, data).json()
    assert result["source_row_count"] == 2
    assert next(product for product in result["products"] if product["excel_rows"] == [3])["status"] == "error"
    task_id = start(client, data).json()["task_id"]
    result = wait_terminal(client, task_id)["result"]
    assert result["source_row_count"] == 2
    assert result["error_count"] > 0
    assert client.post("/api/product-import/commit", json={"preview_session_id": task_id}).status_code == 422


@pytest.mark.parametrize("include_images", [False, True])
def test_missing_dimensions_preserve_original_rows(include_images):
    data = without_dimensions(workbook_bytes([None, valid_row()], images={3: [image_bytes((1, 2, 3))]}))
    rows = read_import_workbook(data, include_images=include_images)
    assert [row.excel_row for row in rows] == [3]
    assert rows[0].values["packing_qty"] == 24
    assert bool(rows[0].images) == include_images


def test_missing_dimensions_preview_and_commit(import_context):
    client, factory, _, _, _ = import_context
    seed_reference_data(factory)
    task_id = start(client, without_dimensions(normal_workbook())).json()["task_id"]
    assert wait_terminal(client, task_id)["status"] == "succeeded"
    assert client.post("/api/product-import/commit", json={"preview_session_id": task_id}).status_code == 200


def test_session_expiry_is_independent_of_task_ttl(import_context):
    client, factory, app, _, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    wait_terminal(client, task_id)
    app.state.product_import_preview_tasks.ttl_seconds = 0
    assert client.get(f"/api/product-import/preview/tasks/{task_id}").status_code == 404
    app.state.product_import_preview_sessions.cleanup()
    assert (directory / task_id).exists()
    assert client.post("/api/product-import/commit", json={"preview_session_id": task_id}).status_code == 200


def test_expired_session_rejected_then_reclaimed_without_touching_uploads(import_context):
    client, factory, app, uploads, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    wait_terminal(client, task_id)
    expire(directory / task_id)
    formal = uploads / "products" / "main" / "keep.webp"
    formal.write_bytes(b"formal image")
    unrelated = directory / "keep"
    unrelated.mkdir()
    assert client.post("/api/product-import/commit", json={"preview_session_id": task_id}).status_code == 404
    app.state.product_import_preview_sessions.cleanup()
    assert not (directory / task_id).exists()
    assert formal.read_bytes() == b"formal image"
    assert unrelated.exists()


def test_cleanup_failure_logged_and_retried_after_task_record_expires(import_context, monkeypatch, caplog):
    client, factory, app, _, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    wait_terminal(client, task_id)
    real_delete = session_store.shutil.rmtree
    def failed(*args, **kwargs):
        raise PermissionError("locked preview")
    monkeypatch.setattr(session_store.shutil, "rmtree", failed)
    assert client.post(f"/api/product-import/preview/tasks/{task_id}/cancel").json()["status"] == "failed"
    app.state.product_import_preview_tasks.ttl_seconds = 0
    assert client.get(f"/api/product-import/preview/tasks/{task_id}").status_code == 404
    expire(directory / task_id)
    app.state.product_import_preview_sessions.cleanup()
    assert (directory / task_id).exists()
    assert "will retry" in caplog.text
    monkeypatch.setattr(session_store.shutil, "rmtree", real_delete)
    app.state.product_import_preview_sessions.cleanup()
    assert not (directory / task_id).exists()


def test_running_task_protected_from_orphan_cleanup(import_context, monkeypatch):
    client, factory, app, _, directory = import_context
    seed_reference_data(factory)
    entered, resume = Event(), Event()
    real_read = preview_tasks.read_import_workbook
    def blocked(data, *, checkpoint):
        entered.set()
        assert resume.wait(5)
        return real_read(data, checkpoint=checkpoint)
    monkeypatch.setattr(preview_tasks, "read_import_workbook", blocked)
    task_id = start(client, normal_workbook()).json()["task_id"]
    try:
        assert entered.wait(5)
        expire(directory / task_id)
        app.state.product_import_preview_sessions.cleanup()
        assert (directory / task_id / "source.xlsx").exists()
    finally:
        resume.set()
    assert wait_terminal(client, task_id)["status"] == "succeeded"


def test_commit_protected_from_orphan_cleanup(import_context, monkeypatch):
    client, factory, app, _, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    wait_terminal(client, task_id)
    entered, resume = Event(), Event()
    real_save = commit_service.process_and_save_product_image
    def blocked(*args, **kwargs):
        entered.set()
        assert resume.wait(5)
        return real_save(*args, **kwargs)
    monkeypatch.setattr(commit_service, "process_and_save_product_image", blocked)
    with ThreadPoolExecutor(max_workers=1) as executor:
        commit = executor.submit(client.post, "/api/product-import/commit", json={"preview_session_id": task_id})
        try:
            assert entered.wait(5)
            expire(directory / task_id)
            app.state.product_import_preview_sessions.cleanup()
            assert (directory / task_id).exists()
        finally:
            resume.set()
        assert commit.result().status_code == 200
    assert not (directory / task_id).exists()


def test_startup_reclaims_crash_orphans_and_keeps_valid_sessions(import_context):
    client, factory, _, uploads, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    wait_terminal(client, task_id)
    orphan = directory / ("a" * 32)
    orphan.mkdir()
    (orphan / "source.xlsx").write_bytes(b"interrupted upload")
    expire(orphan)
    restarted = create_app(uploads_directory=uploads, product_import_preview_directory=directory)
    with TestClient(restarted):
        assert not orphan.exists()
        assert (directory / task_id / "preview.json").exists()


def test_new_preview_reclaims_expired_sessions(import_context):
    client, factory, _, _, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    wait_terminal(client, task_id)
    expire(directory / task_id)
    next_task = start(client, normal_workbook()).json()["task_id"]
    assert not (directory / task_id).exists()
    assert wait_terminal(client, next_task)["status"] == "succeeded"


def test_cancellation_overlaps_success_publication(import_context, monkeypatch):
    client, factory, _, _, directory = import_context
    seed_reference_data(factory)
    entered, resume = Event(), Event()
    real_create = preview_tasks.create_preview_session
    def blocked(*args, **kwargs):
        result = real_create(*args, **kwargs)
        entered.set()
        assert resume.wait(5)
        return result
    monkeypatch.setattr(preview_tasks, "create_preview_session", blocked)
    task_id = start(client, normal_workbook()).json()["task_id"]
    try:
        assert entered.wait(5)
        assert (directory / task_id / "preview.json").exists()
        assert client.post(f"/api/product-import/preview/tasks/{task_id}/cancel").json()["status"] == "processing"
    finally:
        resume.set()
    assert wait_terminal(client, task_id)["status"] == "cancelled"
    assert not (directory / task_id).exists()


@pytest.mark.parametrize("endpoint", [preview_product_import, start_preview_task])
def test_upload_close_failure_still_releases_reservation(import_context, endpoint):
    _, factory, app, _, directory = import_context
    class BrokenClose(BytesIO):
        def close(self):
            raise OSError("close failed")
    buffer = BrokenClose(b"invalid workbook")
    request = Request({"type": "http", "app": app})
    try:
        with factory() as db, pytest.raises(OSError, match="close failed"):
            endpoint(request=request, db=db, file=UploadFile(buffer, filename="bad.xlsx"))
        registry = app.state.product_import_preview_tasks
        registry.acquire()
        registry.release()
        assert not list(directory.iterdir())
    finally:
        BytesIO.close(buffer)
