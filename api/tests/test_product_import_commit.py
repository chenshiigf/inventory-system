from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from PIL import Image as PillowImage
from sqlalchemy import func, select

from app.models import Category, Product, ProductImportBatch, ProductPackaging
from app.services.products import ProductCreationError
from app.services.product_import import commit_service
from app.services.product_import.preview_service import PreparedProductImport
from tests.test_product_import import (
    ImageSpec,
    image_bytes,
    import_context,
    preview,
    seed_reference_data,
    valid_row,
    workbook_bytes,
)


def commit(client: TestClient, session_id: str):
    return client.post(
        "/api/product-import/commit",
        json={"preview_session_id": session_id},
    )


def preview_payload(client: TestClient, data: bytes) -> dict:
    response = preview(client, data, file_name="正式导入验收.xlsx")
    assert response.status_code == 200, response.text
    return response.json()


def formal_image_files(uploads_directory: Path) -> tuple[list[Path], list[Path]]:
    return (
        sorted((uploads_directory / "products" / "main").glob("*.webp")),
        sorted((uploads_directory / "products" / "thumbs").glob("*.webp")),
    )


def test_preview_session_saves_source_hash_and_server_snapshot(import_context) -> None:
    client, session_factory, _application, _uploads, preview_directory = import_context
    seed_reference_data(session_factory)
    data = workbook_bytes([valid_row()])

    payload = preview_payload(client, data)

    session_directory = preview_directory / payload["preview_session_id"]
    assert (session_directory / "source.xlsx").read_bytes() == data
    snapshot = (session_directory / "preview.json").read_text(encoding="utf-8")
    assert '"file_hash"' in snapshot
    assert '"preview_session_id"' in snapshot
    assert payload["already_imported"] is False


def test_commit_creates_single_packaging_product_and_batch(import_context) -> None:
    client, session_factory, _application, _uploads, preview_directory = import_context
    ids = seed_reference_data(session_factory)
    data = workbook_bytes([valid_row(carton_count=8)])
    preview_data = preview_payload(client, data)

    response = commit(client, preview_data["preview_session_id"])

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["product_count"] == 1
    assert result["packaging_count"] == 1
    assert result["created_products"][0]["product_code"] == "01-01-007"
    assert result["created_products"][0]["excel_rows"] == [2]
    assert not (preview_directory / preview_data["preview_session_id"]).exists()
    with session_factory() as db:
        product = db.scalar(select(Product))
        assert product is not None
        assert product.warehouse_id == ids["main_warehouse"]
        assert product.category_id == ids["plate"]
        assert product.total_carton_count == 8
        assert len(product.packagings) == 1
        assert db.scalar(select(func.count(ProductImportBatch.id))) == 1


def test_commit_merges_group_into_one_product_with_multiple_packagings(
    import_context,
) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    data = workbook_bytes(
        [
            valid_row(product_group="A001", packing_qty=240, carton_count=21),
            {
                "product_group": "A001",
                "packing_qty": 144,
                "carton_count": 1,
            },
        ]
    )
    preview_data = preview_payload(client, data)

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 200, result.text
    assert result.json()["product_count"] == 1
    assert result.json()["packaging_count"] == 2
    assert len(result.json()["created_products"]) == 1
    with session_factory() as db:
        assert db.scalar(select(func.count(Product.id))) == 1
        product = db.scalar(select(Product))
        assert product is not None
        assert [(row.packing_qty, row.carton_count) for row in product.packagings] == [
            (240, 21),
            (144, 1),
        ]
        assert product.total_carton_count == 22


def test_blank_groups_create_independent_products_and_codes(import_context) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    data = workbook_bytes(
        [
            valid_row(remark="商品一"),
            valid_row(remark="商品二", packing_qty=48),
        ]
    )
    preview_data = preview_payload(client, data)

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 200
    codes = [row["product_code"] for row in result.json()["created_products"]]
    assert codes == ["01-01-007", "01-01-008"]
    with session_factory() as db:
        assert db.scalar(select(func.count(Product.id))) == 2


def test_warning_without_image_can_be_committed(import_context) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    preview_data = preview_payload(client, workbook_bytes([valid_row()]))
    assert preview_data["warning_count"] == 1
    assert preview_data["error_count"] == 0

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 200
    with session_factory() as db:
        product = db.scalar(select(Product))
        assert product is not None
        assert product.image_path is None
        assert product.thumbnail_path is None


def test_commit_generates_main_and_thumbnail_webp(import_context) -> None:
    client, session_factory, _application, uploads_directory, _previews = import_context
    seed_reference_data(session_factory)
    data = workbook_bytes(
        [valid_row()],
        images={2: [image_bytes((70, 110, 160), width=180, height=25)]},
    )
    preview_data = preview_payload(client, data)

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 200, result.text
    main_files, thumb_files = formal_image_files(uploads_directory)
    assert len(main_files) == 1
    assert len(thumb_files) == 1
    with PillowImage.open(main_files[0]) as main:
        assert main.format == "WEBP"
        assert max(main.size) <= 1600
    with PillowImage.open(thumb_files[0]) as thumb:
        assert thumb.format == "WEBP"
        assert max(thumb.size) <= 320


def test_two_products_share_one_processed_image_pair(import_context) -> None:
    client, session_factory, _application, uploads_directory, _previews = import_context
    seed_reference_data(session_factory)
    shared_anchor = TwoCellAnchor(
        _from=AnchorMarker(col=4, row=1),
        to=AnchorMarker(col=4, row=2),
    )
    data = workbook_bytes(
        [valid_row(remark="商品一"), valid_row(remark="商品二", packing_qty=48)],
        images={
            2: [
                ImageSpec(
                    image_bytes((40, 120, 90), width=180, height=60),
                    anchor=shared_anchor,
                )
            ]
        },
    )
    preview_data = preview_payload(client, data)
    assert preview_data["product_count"] == 2
    assert all(product["shared_image"] for product in preview_data["products"])

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 200, result.text
    with session_factory() as db:
        products = db.scalars(select(Product).order_by(Product.id)).all()
        assert len(products) == 2
        assert products[0].product_code != products[1].product_code
        assert products[0].image_path == products[1].image_path
        assert products[0].thumbnail_path == products[1].thumbnail_path
    main_files, thumb_files = formal_image_files(uploads_directory)
    assert len(main_files) == 1
    assert len(thumb_files) == 1


def test_grouped_product_image_is_processed_once(import_context) -> None:
    client, session_factory, _application, uploads_directory, _previews = import_context
    seed_reference_data(session_factory)
    data = workbook_bytes(
        [
            valid_row(product_group="A001", packing_qty=240),
            {"product_group": "A001", "packing_qty": 144, "carton_count": 1},
        ],
        images={2: [image_bytes((100, 80, 150), width=180, height=25)]},
    )
    preview_data = preview_payload(client, data)

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 200
    assert result.json()["product_count"] == 1
    assert result.json()["packaging_count"] == 2
    main_files, thumb_files = formal_image_files(uploads_directory)
    assert len(main_files) == 1
    assert len(thumb_files) == 1


def test_commit_rejects_preview_with_errors(import_context) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    ids = seed_reference_data(session_factory)
    preview_data = preview_payload(
        client,
        workbook_bytes([valid_row(warehouse="不存在仓库")]),
    )
    assert preview_data["error_count"] == 1

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 422
    assert result.json()["detail"] == "当前 Preview 存在错误，请修正 Excel 后重新上传。"
    with session_factory() as db:
        assert db.scalar(select(func.count(Product.id))) == 0
        assert db.get(Category, ids["plate"]).next_product_sequence == 7


def test_commit_rejects_more_than_twenty_products(import_context, monkeypatch) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    preview_data = preview_payload(client, workbook_bytes([valid_row()]))
    real_prepare = commit_service.prepare_product_import_preview

    def prepare_with_too_many(*args, **kwargs):
        prepared = real_prepare(*args, **kwargs)
        response = prepared.response.model_copy(update={"product_count": 21})
        return PreparedProductImport(
            response=response,
            images_by_id=prepared.images_by_id,
            product_image_ids=prepared.product_image_ids,
        )

    monkeypatch.setattr(
        commit_service,
        "prepare_product_import_preview",
        prepare_with_too_many,
    )

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 422
    assert result.json()["detail"] == commit_service.TOO_MANY_PRODUCTS_MESSAGE


def test_same_file_second_commit_is_rejected_and_preview_marks_duplicate(
    import_context,
) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    data = workbook_bytes([valid_row()])
    first_preview = preview_payload(client, data)
    assert commit(client, first_preview["preview_session_id"]).status_code == 200

    second_preview = preview_payload(client, data)
    assert second_preview["already_imported"] is True
    second_commit = commit(client, second_preview["preview_session_id"])

    assert second_commit.status_code == 409
    assert second_commit.json()["detail"] == commit_service.DUPLICATE_FILE_MESSAGE
    with session_factory() as db:
        assert db.scalar(select(func.count(Product.id))) == 1
        assert db.scalar(select(func.count(ProductImportBatch.id))) == 1


def test_modified_file_creates_new_batch_and_group_name_is_not_global_identity(
    import_context,
) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    first_data = workbook_bytes([valid_row(product_group="A001", remark="第一批")])
    second_data = workbook_bytes([valid_row(product_group="A001", remark="第二批")])

    first = preview_payload(client, first_data)
    second = preview_payload(client, second_data)
    assert commit(client, first["preview_session_id"]).status_code == 200
    second_result = commit(client, second["preview_session_id"])

    assert second_result.status_code == 200
    with session_factory() as db:
        assert db.scalar(select(func.count(Product.id))) == 2
        assert db.scalar(select(func.count(ProductImportBatch.id))) == 2


def test_commit_failure_rolls_back_database_sequence_and_new_images(
    import_context,
    monkeypatch,
) -> None:
    client, session_factory, _application, uploads_directory, _previews = import_context
    ids = seed_reference_data(session_factory)
    data = workbook_bytes(
        [valid_row(remark="第一件"), valid_row(remark="第二件", packing_qty=48)],
        images={
            2: [image_bytes((20, 80, 140), width=180, height=25)],
            3: [image_bytes((140, 80, 20), width=180, height=25)],
        },
    )
    preview_data = preview_payload(client, data)
    real_create = commit_service.create_product_record
    calls = 0

    def fail_after_second_create(db, payload):
        nonlocal calls
        calls += 1
        product = real_create(db, payload)
        if calls == 2:
            raise ProductCreationError("模拟整批失败", status_code=500)
        return product

    monkeypatch.setattr(commit_service, "create_product_record", fail_after_second_create)
    before_files = formal_image_files(uploads_directory)

    result = commit(client, preview_data["preview_session_id"])

    assert result.status_code == 500
    assert result.json()["detail"] == "模拟整批失败"
    assert formal_image_files(uploads_directory) == before_files
    with session_factory() as db:
        assert db.scalar(select(func.count(Product.id))) == 0
        assert db.scalar(select(func.count(ProductPackaging.id))) == 0
        assert db.scalar(select(func.count(ProductImportBatch.id))) == 0
        assert db.get(Category, ids["plate"]).next_product_sequence == 7


def test_committed_product_is_visible_in_inventory_api(import_context) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    preview_data = preview_payload(client, workbook_bytes([valid_row(carton_count=9)]))
    result = commit(client, preview_data["preview_session_id"])
    assert result.status_code == 200

    inventory = client.get("/api/products?page=1&page_size=20")

    assert inventory.status_code == 200
    assert inventory.json()["total"] == 1
    assert inventory.json()["items"][0]["product_code"] == "01-01-007"
    assert inventory.json()["items"][0]["total_carton_count"] == 9


def test_consumed_or_unknown_session_cannot_commit_again(import_context) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    preview_data = preview_payload(client, workbook_bytes([valid_row()]))
    session_id = preview_data["preview_session_id"]
    assert commit(client, session_id).status_code == 200

    consumed = commit(client, session_id)
    unknown = commit(client, "f" * 32)

    assert consumed.status_code == 404
    assert unknown.status_code == 404


def test_preview_without_optional_source_code_header_is_valid(import_context) -> None:
    client, session_factory, _application, _uploads, _previews = import_context
    seed_reference_data(session_factory)
    core_headers = [
        "商品组",
        "仓库",
        "一级分类",
        "二级分类",
        "产品图片",
        "产品尺寸",
        "装箱数",
        "单位",
        "单价",
        "当前箱数",
        "备注",
    ]

    payload = preview_payload(
        client,
        workbook_bytes([valid_row()], headers=core_headers),
    )

    assert payload["error_count"] == 0
    assert payload["products"][0]["source_codes"] == []
