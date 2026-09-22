"""Create the five-row workbook used by browser acceptance of product import preview."""

from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image

from app.services.product_import.schemas import IMPORT_HEADERS


OUTPUT_PATH = Path(__file__).resolve().parents[1] / "data" / "tmp" / "product-import-browser-acceptance.xlsx"


def make_image(color: tuple[int, int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", (160, 100), color).save(output, format="PNG")
    return output.getvalue()


def build_fixture() -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "商品导入"
    worksheet.append(list(IMPORT_HEADERS))

    rows = [
        ["主仓", "厨房用品", "盘子", None, "20cm", 24, "pcs", 3.50, 10, "蓝边款", "10235"],
        ["主仓", "厨房用品", "盘子", None, "18cm", 12, "set", 5.80, 6, None, "10236"],
        ["虎跳仓", "厨房用品", "盘子", None, "22cm", 24, "pcs", 4.20, 8, "测试无图", "10237"],
        ["主仓", "厨房用品", "盘子", None, "16cm", 18, "个", 2.80, 4, "故意填写非法单位", "10238"],
        ["主仓", "厨房用品", "盘子", None, "15cm", 18, "pcs", 2.60, -2, "故意填写负库存", "10239"],
    ]
    for excel_row, row in enumerate(rows, start=2):
        for column_index, value in enumerate(row, start=1):
            worksheet.cell(row=excel_row, column=column_index).value = value

    worksheet.add_image(ExcelImage(BytesIO(make_image((220, 80, 40)))), "D2")
    worksheet.add_image(ExcelImage(BytesIO(make_image((50, 120, 210)))), "D3")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(OUTPUT_PATH)
    workbook.close()
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build_fixture())
