import time
from threading import Event

import pytest
from sqlalchemy import func, select

from app.models import Product, ProductImportBatch
from app.services.product_import import preview_service, preview_tasks
from tests.test_product_import import (
    IMPORT_HEADERS, image_bytes, import_context, preview, seed_reference_data,
    valid_row, workbook_bytes,
)


def start(client, data, name="预检.xlsx"):
    return client.post("/api/product-import/preview/start", files={"file": (name, data)})


def wait_terminal(client, task_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/product-import/preview/tasks/{task_id}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] in {"succeeded", "failed", "cancelled"}:
            return result
        time.sleep(0.01)
    pytest.fail("Preview task did not finish")


def normal_workbook():
    return workbook_bytes([valid_row()], images={2: [image_bytes((90, 120, 160))]})


@pytest.mark.parametrize(("name", "data", "status"), [
    ("test.csv", b"csv", 415), ("empty.xlsx", b"", 400),
    ("invalid.xlsx", b"invalid", 400),
])
def test_invalid_uploads_clean_up(import_context, name, data, status):
    client, _, app, _, directory = import_context
    assert start(client, data, name).status_code == status
    assert not list(directory.iterdir())
    assert not app.state.product_import_preview_tasks._tasks


def test_header_precheck_does_not_touch_images(import_context, monkeypatch):
    client, factory, _, _, directory = import_context
    seed_reference_data(factory)
    data = workbook_bytes([valid_row()], headers=[h for h in IMPORT_HEADERS if h != "仓库"], images={2: [image_bytes((1, 2, 3))]})
    def forbidden(*args, **kwargs):
        pytest.fail("Precheck must not decode images")
    monkeypatch.setattr(preview_service.Image, "open", forbidden)
    assert start(client, data).status_code == 400
    assert not list(directory.iterdir())


def test_data_precheck_keeps_sorted_original_rows_and_skips_images(import_context, monkeypatch):
    client, factory, app, _, directory = import_context
    seed_reference_data(factory)
    data = workbook_bytes([
        None, valid_row(warehouse="义库", carton_count="bad"), None,
        valid_row(packing_qty="bad", category_level_2="不存在"),
    ], images={3: [image_bytes((1, 2, 3))]})
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid rows must fail before image work")
    monkeypatch.setattr(preview_service.Image, "open", forbidden)
    result = start(client, data)
    assert result.status_code == 422
    issues = result.json()["detail"]["errors"]
    assert [issue["excel_row"] for issue in issues] == [3, 5]
    assert "仓库不存在：义库" in issues[0]["messages"]
    assert "结余箱数必须是整数" in issues[0]["messages"]
    assert "装箱数必须是整数" in issues[1]["messages"]
    assert not list(directory.iterdir())
    assert not app.state.product_import_preview_tasks._tasks


@pytest.mark.parametrize("second", [valid_row(product_group="A", warehouse="虎跳仓", packing_qty=12), valid_row(product_group="A", packing_qty=24)])
def test_precheck_reuses_group_and_packaging_rules(import_context, second):
    client, factory, _, _, directory = import_context
    seed_reference_data(factory)
    result = start(client, workbook_bytes([valid_row(product_group="A"), second]))
    assert result.status_code == 422
    assert result.json()["detail"]["errors"][0]["excel_row"] == 2
    assert result.json()["detail"]["errors"][0]["excel_rows"] == [2, 3]
    assert not list(directory.iterdir())


def test_task_result_matches_legacy_preview_and_commit(import_context):
    client, factory, _, _, directory = import_context
    seed_reference_data(factory)
    data = workbook_bytes([
        valid_row(product_group="A"), valid_row(product_group="A", packing_qty=12),
    ], images={2: [image_bytes((1, 2, 3))]})
    legacy = preview(client, data).json()
    started = start(client, data)
    assert started.status_code == 202
    result = wait_terminal(client, started.json()["task_id"])
    assert result["status"] == "succeeded"
    current = result["result"]
    for key in ["source_row_count", "product_count", "valid_count", "warning_count", "error_count"]:
        assert current[key] == legacy[key]
    actual, expected = current["products"][0].copy(), legacy["products"][0].copy()
    for key in ["preview_id", "image_preview_url"]:
        actual.pop(key)
        expected.pop(key)
    assert actual == expected
    committed = client.post("/api/product-import/commit", json={"preview_session_id": current["preview_session_id"]})
    assert committed.status_code == 200, committed.text
    assert committed.json()["packaging_count"] == 2
    assert not (directory / current["preview_session_id"]).exists()


def test_cancel_during_image_processing_cleans_files_and_releases_slot(import_context, monkeypatch):
    client, factory, _, uploads, directory = import_context
    seed_reference_data(factory)
    entered, resume = Event(), Event()
    real_save = preview_service._save_preview_image
    def blocked(image, session_directory, checkpoint):
        real_save(image, session_directory, checkpoint)
        entered.set()
        assert resume.wait(5)
        checkpoint()
    monkeypatch.setattr(preview_service, "_save_preview_image", blocked)
    task_id = start(client, normal_workbook()).json()["task_id"]
    try:
        assert entered.wait(5)
        assert list((directory / task_id).glob("*.webp"))
        second = start(client, normal_workbook())
        assert second.status_code == 409
        assert len(list(directory.iterdir())) == 1
        cancel = client.post(f"/api/product-import/preview/tasks/{task_id}/cancel")
        assert cancel.json()["status"] == "processing"
    finally:
        resume.set()
    assert wait_terminal(client, task_id)["status"] == "cancelled"
    assert not list(directory.iterdir())
    assert not list(uploads.rglob("*.webp"))
    with factory() as db:
        assert db.scalar(select(func.count(Product.id))) == 0
        assert db.scalar(select(func.count(ProductImportBatch.id))) == 0
    monkeypatch.setattr(preview_service, "_save_preview_image", real_save)
    next_id = start(client, normal_workbook()).json()["task_id"]
    assert wait_terminal(client, next_id)["status"] == "succeeded"


def test_failure_releases_slot_and_cleans_temp_files(import_context, monkeypatch):
    client, factory, _, _, directory = import_context
    seed_reference_data(factory)
    real_read = preview_tasks.read_import_workbook
    def failed(*args, **kwargs):
        raise RuntimeError("simulated decoder failure")
    monkeypatch.setattr(preview_tasks, "read_import_workbook", failed)
    task_id = start(client, normal_workbook()).json()["task_id"]
    assert wait_terminal(client, task_id)["status"] == "failed"
    assert not list(directory.iterdir())
    monkeypatch.setattr(preview_tasks, "read_import_workbook", real_read)
    task_id = start(client, normal_workbook()).json()["task_id"]
    assert wait_terminal(client, task_id)["status"] == "succeeded"


def test_completed_task_cancel_race_and_missing_task(import_context):
    client, factory, app, _, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    assert wait_terminal(client, task_id)["status"] == "succeeded"
    assert client.post(f"/api/product-import/preview/tasks/{task_id}/cancel").json()["status"] == "cancelled"
    assert not (directory / task_id).exists()
    app.state.product_import_preview_tasks.ttl_seconds = 0
    assert client.get(f"/api/product-import/preview/tasks/{task_id}").status_code == 404
    assert client.get("/api/product-import/preview/tasks/server-restarted").status_code == 404
    assert client.post("/api/product-import/preview/tasks/server-restarted/cancel").status_code == 404


def test_registry_ttl_keeps_successful_commit_files(import_context):
    client, factory, app, _, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    assert wait_terminal(client, task_id)["status"] == "succeeded"
    app.state.product_import_preview_tasks.ttl_seconds = 0
    assert client.get(f"/api/product-import/preview/tasks/{task_id}").status_code == 404
    assert (directory / task_id / "preview.json").is_file()


def test_source_row_limit_fails_before_image_stage(import_context, monkeypatch):
    client, factory, _, _, directory = import_context
    seed_reference_data(factory)
    data = workbook_bytes([valid_row() for _ in range(301)])
    def forbidden(*args, **kwargs):
        pytest.fail("Row limit must fail before heavy job")
    monkeypatch.setattr(preview_tasks, "read_import_workbook", forbidden)
    result = start(client, data)
    assert result.status_code == 400
    assert "300" in result.json()["detail"]
    assert not list(directory.iterdir())


def test_image_errors_remain_in_final_preview(import_context):
    client, factory, _, _, _ = import_context
    seed_reference_data(factory)
    task_id = start(client, workbook_bytes([valid_row()])).json()["task_id"]
    result = wait_terminal(client, task_id)
    assert result["status"] == "succeeded"
    assert result["result"]["error_count"] == 1
    assert "无商品图片" in result["result"]["products"][0]["messages"]


def test_cancel_before_workbook_loading(import_context, monkeypatch):
    client, factory, _, _, directory = import_context
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
        client.post(f"/api/product-import/preview/tasks/{task_id}/cancel")
    finally:
        resume.set()
    assert wait_terminal(client, task_id)["status"] == "cancelled"
    assert not list(directory.iterdir())


def test_cleanup_failure_does_not_report_cancelled(import_context, monkeypatch):
    client, factory, _, _, directory = import_context
    seed_reference_data(factory)
    task_id = start(client, normal_workbook()).json()["task_id"]
    assert wait_terminal(client, task_id)["status"] == "succeeded"
    real_delete = preview_tasks.delete_preview_session
    def failed(*args, **kwargs):
        raise PermissionError("simulated file lock")
    monkeypatch.setattr(preview_tasks, "delete_preview_session", failed)
    result = client.post(f"/api/product-import/preview/tasks/{task_id}/cancel").json()
    assert result["status"] == "failed"
    assert "清理失败" in result["error"]
    assert (directory / task_id).exists()
    monkeypatch.setattr(preview_tasks, "delete_preview_session", real_delete)
    assert client.post(f"/api/product-import/preview/tasks/{task_id}/cancel").json()["status"] == "cancelled"
    assert not (directory / task_id).exists()
