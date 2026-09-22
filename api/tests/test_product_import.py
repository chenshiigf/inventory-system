from collections.abc import Generator
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image as PillowImage
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import create_app
from app.models import Category, Product, Warehouse
from app.services.product_import.schemas import IMPORT_HEADERS


XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture
def import_context(
    tmp_path: Path,
) -> Generator[tuple[TestClient, sessionmaker, FastAPI, Path], None, None]:
    database_path = tmp_path / "product-import-test.db"
    uploads_directory = tmp_path / "uploads"
    preview_directory = tmp_path / "previews"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)
    application = create_app(
        uploads_directory=uploads_directory,
        product_import_preview_directory=preview_directory,
    )

    def override_get_db():
        with TestingSessionLocal() as db:
            yield db

    application.dependency_overrides[get_db] = override_get_db
    with TestClient(application) as client:
        yield client, TestingSessionLocal, application, uploads_directory

    application.dependency_overrides.clear()
    engine.dispose()


def seed_reference_data(session_factory: sessionmaker) -> dict[str, int]:
    with session_factory.begin() as db:
        main_warehouse = Warehouse(name="主仓", sort_order=0)
        tiger_warehouse = Warehouse(name="虎跳仓", sort_order=1)
        kitchen = Category(
            name="厨房用品",
            code="01",
            sort_order=0,
            next_product_sequence=None,
        )
        christmas = Category(
            name="圣诞系列",
            code="02",
            sort_order=1,
            next_product_sequence=None,
        )
        db.add_all([main_warehouse, tiger_warehouse, kitchen, christmas])
        db.flush()
        plate = Category(
            name="盘子",
            parent_id=kitchen.id,
            code="01",
            next_product_sequence=7,
        )
        bowl = Category(
            name="碗",
            parent_id=kitchen.id,
            code="02",
            next_product_sequence=1,
        )
        db.add_all([plate, bowl])
        db.flush()
        return {
            "main_warehouse": main_warehouse.id,
            "tiger_warehouse": tiger_warehouse.id,
            "kitchen": kitchen.id,
            "christmas": christmas.id,
            "plate": plate.id,
            "bowl": bowl.id,
        }


def image_bytes(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    PillowImage.new("RGB", (64, 40), color).save(output, format="PNG")
    return output.getvalue()


def workbook_bytes(
    rows: list[list[object] | None],
    *,
    images: dict[int, list[bytes]] | None = None,
    sheet_name: str = "商品导入",
    headers: list[str] | None = None,
) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    worksheet.append(headers or list(IMPORT_HEADERS))
    for excel_row, row in enumerate(rows, start=2):
        if row is not None:
            for column_index, value in enumerate(row, start=1):
                worksheet.cell(row=excel_row, column=column_index).value = value
        for data in (images or {}).get(excel_row, []):
            worksheet.add_image(ExcelImage(BytesIO(data)), f"D{excel_row}")
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def valid_row(
    *,
    warehouse: str = "主仓",
    category_level_1: str = "厨房用品",
    category_level_2: str = "盘子",
    size: object = "20cm",
    packing_qty: object = 24,
    unit: object = "pcs",
    price: object = 3.50,
    carton_count: object = 10,
    remark: object = "蓝边款",
    source_code: object = "10235",
) -> list[object]:
    return [
        warehouse,
        category_level_1,
        category_level_2,
        None,
        size,
        packing_qty,
        unit,
        price,
        carton_count,
        remark,
        source_code,
    ]


def preview(
    client: TestClient,
    data: bytes,
    *,
    file_name: str = "厨房用品-导入.xlsx",
):
    return client.post(
        "/api/product-import/preview",
        files={"file": (file_name, data, XLSX_CONTENT_TYPE)},
    )


def test_template_download_is_a_fixed_valid_xlsx(import_context) -> None:
    client, _session_factory, _application, _uploads = import_context

    response = client.get("/api/product-import/template")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(XLSX_CONTENT_TYPE)
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    workbook = load_workbook(BytesIO(response.content), read_only=False)
    assert workbook.sheetnames == ["商品导入"]
    worksheet = workbook["商品导入"]
    assert worksheet.max_row == 1
    assert [cell.value for cell in worksheet[1]] == list(IMPORT_HEADERS)
    assert all(worksheet.cell(row=1, column=index).comment for index in range(1, 12))
    workbook.close()


def test_valid_excel_preview_reads_fixed_fields_and_embedded_image(import_context) -> None:
    client, session_factory, _application, _uploads = import_context
    seed_reference_data(session_factory)
    data = workbook_bytes([valid_row()], images={2: [image_bytes((220, 80, 40))]})

    response = preview(client, data)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["file_name"] == "厨房用品-导入.xlsx"
    assert payload["total_rows"] == 1
    row = payload["rows"][0]
    assert row["excel_row"] == 2
    assert row["warehouse"] == "主仓"
    assert row["category_level_1"] == "厨房用品"
    assert row["category_level_2"] == "盘子"
    assert row["packing_qty"] == 24
    assert row["unit"] == "pcs"
    assert row["price"] == "3.50"
    assert row["carton_count"] == 10
    assert row["source_code"] == "10235"
    assert row["status"] == "valid"
    assert row["has_image"] is True
    assert row["image_preview_url"].startswith("/import-previews/")
    image_response = client.get(row["image_preview_url"])
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/webp"


def test_preview_skips_empty_rows_and_limits_to_first_20_candidates(import_context) -> None:
    client, session_factory, _application, _uploads = import_context
    seed_reference_data(session_factory)
    rows: list[list[object] | None] = [valid_row(source_code="first")]
    rows.append([" ", None, None, None, None, None, None, None, None, None, None])
    rows.extend(valid_row(source_code=str(index)) for index in range(2, 23))

    response = preview(client, workbook_bytes(rows))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 20
    assert [row["excel_row"] for row in payload["rows"]] == [
        2,
        *range(4, 23),
    ]


def test_completely_empty_workbook_rows_are_skipped(import_context) -> None:
    client, session_factory, _application, _uploads = import_context
    seed_reference_data(session_factory)

    response = preview(client, workbook_bytes([None, None, valid_row()]))

    assert response.status_code == 200
    assert [row["excel_row"] for row in response.json()["rows"]] == [4]


@pytest.mark.parametrize(
    ("row", "expected_message"),
    [
        (valid_row(warehouse="不存在仓"), "仓库不存在：不存在仓"),
        (valid_row(category_level_1="不存在分类"), "一级分类不存在：不存在分类"),
        (valid_row(category_level_2="不存在小类"), "二级分类不存在：不存在小类"),
        (
            valid_row(category_level_1="圣诞系列", category_level_2="盘子"),
            "二级分类‘盘子’不属于一级分类‘圣诞系列’",
        ),
        (valid_row(unit="box"), "单位必须为 pcs 或 set"),
        (valid_row(carton_count=-2), "当前箱数不能小于 0"),
        (valid_row(packing_qty=0), "装箱数必须是正整数"),
        (valid_row(packing_qty="24pcs"), "装箱数必须是整数"),
        (valid_row(price="¥3.50"), "单价必须是 >= 0 且最多两位小数的合法数字"),
    ],
)
def test_field_and_reference_validation_returns_error(
    import_context,
    row: list[object],
    expected_message: str,
) -> None:
    client, session_factory, _application, _uploads = import_context
    seed_reference_data(session_factory)

    response = preview(client, workbook_bytes([row]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["error_count"] == 1
    assert payload["rows"][0]["status"] == "error"
    assert expected_message in payload["rows"][0]["messages"]


@pytest.mark.parametrize(
    ("unit", "expected"),
    [("pcs", "pcs"), ("set", "set"), ("PCS", "pcs"), ("SET", "set")],
)
def test_allowed_units_are_normalized_to_lowercase(
    import_context,
    unit: str,
    expected: str,
) -> None:
    client, session_factory, _application, _uploads = import_context
    seed_reference_data(session_factory)

    response = preview(client, workbook_bytes([valid_row(unit=unit)]))

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["unit"] == expected
    assert row["status"] == "warning"
    assert "无商品图片" in row["messages"]


def test_zero_cartons_is_valid_and_missing_size_is_warning(import_context) -> None:
    client, session_factory, _application, _uploads = import_context
    seed_reference_data(session_factory)

    response = preview(
        client,
        workbook_bytes([valid_row(size="", carton_count=0)]),
    )

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["carton_count"] == 0
    assert row["status"] == "warning"
    assert "产品尺寸为空" in row["messages"]
    assert "无商品图片" in row["messages"]


def test_single_row_multiple_images_is_an_error(import_context) -> None:
    client, session_factory, _application, _uploads = import_context
    seed_reference_data(session_factory)

    response = preview(
        client,
        workbook_bytes(
            [valid_row()],
            images={2: [image_bytes((10, 20, 30)), image_bytes((30, 20, 10))]},
        ),
    )

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["has_image"] is True
    assert row["status"] == "error"
    assert "一行只能有一张商品图片" in row["messages"]


@pytest.mark.parametrize(
    ("file_name", "expected_status"),
    [("legacy.xls", 415), ("legacy.csv", 415)],
)
def test_non_xlsx_files_are_rejected(import_context, file_name: str, expected_status: int) -> None:
    client, _session_factory, _application, _uploads = import_context

    response = preview(client, b"not an xlsx", file_name=file_name)

    assert response.status_code == expected_status


def test_invalid_xlsx_bytes_are_rejected(import_context) -> None:
    client, _session_factory, _application, _uploads = import_context

    response = preview(client, b"not an xlsx")

    assert response.status_code == 400
    assert "无法读取 Excel" in response.json()["detail"]


def test_missing_sheet_is_rejected(import_context) -> None:
    client, _session_factory, _application, _uploads = import_context

    response = preview(
        client,
        workbook_bytes([valid_row()], sheet_name="Sheet1"),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "未找到工作表：商品导入"


def test_header_mismatch_identifies_column_and_actual_value(import_context) -> None:
    client, _session_factory, _application, _uploads = import_context
    headers = list(IMPORT_HEADERS)
    headers[5] = "箱数"

    response = preview(
        client,
        workbook_bytes([valid_row()], headers=headers),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "F1 应为 装箱数，实际为 箱数"


def test_preview_does_not_create_products_or_change_sequences_or_formal_uploads(
    import_context,
) -> None:
    client, session_factory, _application, uploads_directory = import_context
    ids = seed_reference_data(session_factory)
    data = workbook_bytes([valid_row()], images={2: [image_bytes((90, 120, 160))]})

    before_formal_files = list((uploads_directory / "products").rglob("*"))
    response = preview(client, data)

    assert response.status_code == 200
    with session_factory() as db:
        assert db.scalar(select(func.count(Product.id))) == 0
        plate = db.get(Category, ids["plate"])
        assert plate is not None
        assert plate.next_product_sequence == 7
        assert db.scalar(select(Product.product_code).where(Product.product_code.is_not(None))) is None

    after_formal_files = list((uploads_directory / "products").rglob("*"))
    assert after_formal_files == before_formal_files
