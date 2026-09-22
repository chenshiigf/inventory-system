"""Create the workbook used by browser acceptance of product import Preview."""

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.etree import ElementTree

from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, TwoCellAnchor
from PIL import Image

from app.services.product_import.schemas import IMPORT_HEADERS


OUTPUT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "tmp"
    / "product-import-compatibility-acceptance.xlsx"
)


def make_image(color: tuple[int, int, int], *, height: int = 55) -> bytes:
    output = BytesIO()
    Image.new("RGB", (160, height), color).save(output, format="PNG")
    return output.getvalue()


def add_cached_formula(data: bytes, *, cell_ref: str, formula: str, value: int) -> bytes:
    files: dict[str, bytes] = {}
    with ZipFile(BytesIO(data)) as source:
        for name in source.namelist():
            files[name] = source.read(name)
    worksheet_name = next(
        name
        for name in files
        if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
    )
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    root = ElementTree.fromstring(files[worksheet_name])
    cell = next(
        node
        for node in root.iter(f"{{{namespace}}}c")
        if node.attrib.get("r") == cell_ref
    )
    cell.attrib.pop("t", None)
    for child in list(cell):
        if child.tag.rsplit("}", 1)[-1] in {"f", "v"}:
            cell.remove(child)
    formula_node = ElementTree.SubElement(cell, f"{{{namespace}}}f")
    formula_node.text = formula.removeprefix("=")
    value_node = ElementTree.SubElement(cell, f"{{{namespace}}}v")
    value_node.text = str(value)
    files[worksheet_name] = ElementTree.tostring(
        root,
        encoding="utf-8",
        xml_declaration=True,
    )

    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name, content in files.items():
            target.writestr(name, content)
    return output.getvalue()


def build_fixture() -> Path:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "库存明细"
    worksheet.append(
        [
            "商品组",
            "仓库",
            "一级 分类",
            "二级分类",
            "产品\n图片",
            "产品尺寸",
            "装箱数",
            "单位",
            "单价",
            "结余\n箱数",
            "备 注",
        ]
    )

    rows = [
        ["", "主仓", "厨房用品", "盘子", None, "26cm", None, None, None, 4, "普通单包装商品"],
        ["A001", "主仓", "厨房用品", "盘子", None, "20cm", 240, "pcs", 3.50, 21, "三种包装规格"],
        ["A001", "主仓", "厨房用品", "盘子", None, "20cm", 144, "pcs", 3.50, 1, "三种包装规格"],
        ["A001", "主仓", "厨房用品", "盘子", None, "20cm", 72, "pcs", 3.50, 0, "三种包装规格"],
        ["", "主仓", "厨房用品", "盘子", None, "18cm", 12, "set", 5.80, 6, "共享图片商品一"],
        ["", "主仓", "厨房用品", "盘子", None, "22cm", 24, "pcs", 4.20, 8, "共享图片商品二"],
        ["", "主仓", "厨房用品", "盘子", None, "15cm", 18, "pcs", 2.60, "=3+2", "公式有缓存"],
    ]
    for excel_row, row in enumerate(rows, start=2):
        for column_index, value in enumerate(row, start=1):
            worksheet.cell(row=excel_row, column=column_index).value = value

    worksheet.add_image(ExcelImage(BytesIO(make_image((35, 95, 150), height=25))), "E2")

    # A001 has a single image anchored to its first row; the other two rows inherit
    # it through the product group rather than through duplicate drawings.
    worksheet.add_image(ExcelImage(BytesIO(make_image((150, 85, 45), height=25))), "E3")

    # One image explicitly covers rows 6 and 7. These rows have blank groups and
    # therefore remain two products while sharing one Preview image.
    shared_image = ExcelImage(BytesIO(make_image((80, 140, 95))))
    shared_image.anchor = TwoCellAnchor(
        _from=AnchorMarker(col=4, row=5),
        to=AnchorMarker(col=4, row=6),
    )
    worksheet.add_image(shared_image)
    worksheet.add_image(ExcelImage(BytesIO(make_image((150, 55, 120), height=25))), "E8")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(OUTPUT_PATH)
    workbook.close()
    OUTPUT_PATH.write_bytes(
        add_cached_formula(
            OUTPUT_PATH.read_bytes(),
            cell_ref="J8",
            formula="=3+2",
            value=5,
        )
    )
    return OUTPUT_PATH


if __name__ == "__main__":
    print(build_fixture())
