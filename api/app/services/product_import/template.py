from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .schemas import IMPORT_COLUMNS


TEMPLATE_SHEET_NAME = "商品导入"
TEMPLATE_HEADER_FILL = "5B6B82"


def build_product_import_template() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = TEMPLATE_SHEET_NAME
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(IMPORT_COLUMNS))}1"
    worksheet.sheet_view.showGridLines = False

    worksheet.append([column.header for column in IMPORT_COLUMNS])
    for column_index, column in enumerate(IMPORT_COLUMNS, start=1):
        cell = worksheet.cell(row=1, column=column_index)
        cell.fill = PatternFill(fill_type="solid", fgColor=TEMPLATE_HEADER_FILL)
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    comments = {
        "商品组": "普通商品留空。同一商品有多个装箱规格时，把相关行填写相同的商品组名称，例如：A001。",
        "仓库": "填写系统中已经存在的仓库名称，例如：主仓。",
        "一级分类": "填写系统中已经存在的一级分类名称。",
        "二级分类": "填写属于前面一级分类的二级分类名称。",
        "产品图片": "每个最终商品至少关联一张有效图片。图片可以覆盖多个商品行；同一张图片也可以被多个商品共用。",
        "产品尺寸": "允许为空，例如：20cm 或 20×20×5cm。只 trim 前后空格。",
        "装箱数": "单包装允许为空；多包装时每行必填、且装箱数不能重复。填写正整数，例如：24。",
        "单位": "允许为空；填写时仅支持 pcs 或 set，大小写会自动转为小写。",
        "单价": "允许为空；填写时使用 >= 0 的数字，例如：3.50，不要带货币符号。",
        "当前箱数": "必填，填写 >= 0 的整数；也接受历史表头‘结余箱数’。公式必须保存有最新计算结果。",
        "备注": "允许为空，填写纯文本。",
    }
    for column_index, column in enumerate(IMPORT_COLUMNS, start=1):
        worksheet.cell(row=1, column=column_index).comment = Comment(
            comments[column.header],
            "商品库存",
        )

    column_widths = [
        16,
        16,
        16,
        16,
        20,
        20,
        12,
        10,
        12,
        14,
        24,
    ]
    for column_index, width in enumerate(column_widths, start=1):
        worksheet.column_dimensions[get_column_letter(column_index)].width = width
    worksheet.row_dimensions[1].height = 26

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()
