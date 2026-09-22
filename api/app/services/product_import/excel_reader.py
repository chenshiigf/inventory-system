from dataclasses import dataclass
from io import BytesIO
from typing import Final
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from .schemas import (
    FIELD_ALIASES,
    REQUIRED_IMPORT_FIELDS,
    normalize_header,
)


MAX_PREVIEW_ROWS: Final[int] = 20
IMPORT_SHEET_NAME: Final[str] = "商品导入"
DEFAULT_ROW_HEIGHT_POINTS: Final[float] = 15.0
DEFAULT_COLUMN_WIDTH: Final[float] = 8.43
EMU_PER_POINT: Final[float] = 12_700.0
EMU_PER_PIXEL: Final[float] = 9_525.0
MIN_COVERAGE_RATIO: Final[float] = 0.60


class ProductImportWorkbookError(ValueError):
    """An uploaded workbook cannot be read as a controlled Preview."""


@dataclass(frozen=True)
class EmbeddedImage:
    image_id: str
    data: bytes
    covered_rows: tuple[int, ...]


@dataclass(frozen=True)
class RawImportRow:
    excel_row: int
    values: dict[str, object]
    formulas: dict[str, str]
    images: tuple[EmbeddedImage, ...]


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _display_header(value: object) -> str:
    return _text(value) or "空白"


def _canonical_header_map() -> dict[str, str]:
    return {
        alias: field
        for field, aliases in FIELD_ALIASES.items()
        for alias in aliases
    }


def build_header_map(worksheet) -> dict[str, int]:
    """Map normalized known headers to canonical field names.

    Unknown columns are deliberately absent from the result.  This is the
    boundary that prevents historical columns from affecting Preview parsing.
    """

    alias_to_field = _canonical_header_map()
    stock_matches: list[tuple[int, str]] = []
    for column_index in range(1, worksheet.max_column + 1):
        header = normalize_header(worksheet.cell(row=1, column=column_index).value)
        if header in FIELD_ALIASES["carton_count"]:
            stock_matches.append((column_index, header))

    if not stock_matches:
        raise ProductImportWorkbookError(
            "未找到库存列，请提供‘结余箱数’或‘当前箱数’。"
        )
    if len(stock_matches) > 1:
        raise ProductImportWorkbookError(
            "同时发现‘结余箱数’和‘当前箱数’，请保留其中一个。"
        )

    header_map: dict[str, int] = {}
    header_names: dict[str, str] = {}
    for column_index in range(1, worksheet.max_column + 1):
        cell_value = worksheet.cell(row=1, column=column_index).value
        header = normalize_header(cell_value)
        if not header:
            continue
        field = alias_to_field.get(header)
        if field is None:
            continue
        if field in header_map:
            first_header = header_names[field]
            raise ProductImportWorkbookError(
                f"发现重复字段表头：{first_header}、{header}，请保留其中一个。"
            )
        header_map[field] = column_index
        header_names[field] = _display_header(cell_value)

    for field in REQUIRED_IMPORT_FIELDS:
        if field not in header_map:
            raise ProductImportWorkbookError(
                f"未找到必需表头：{FIELD_ALIASES[field][0]}"
            )
    return header_map


def _anchor_from(anchor: object):
    return getattr(anchor, "_from", None)


def _anchor_to(anchor: object):
    return getattr(anchor, "to", None) or getattr(anchor, "_to", None)


def _row_height_emu(worksheet, row: int) -> float:
    dimension = worksheet.row_dimensions[row]
    height_points = dimension.height
    if height_points is None:
        height_points = worksheet.sheet_format.defaultRowHeight
    if height_points is None:
        height_points = DEFAULT_ROW_HEIGHT_POINTS
    return max(float(height_points), 1.0) * EMU_PER_POINT


def _column_width_emu(worksheet, column: int) -> float:
    letter = get_column_letter(column)
    dimension = worksheet.column_dimensions[letter]
    width = dimension.width
    if width is None:
        width = worksheet.sheet_format.defaultColWidth
    if width is None:
        width = DEFAULT_COLUMN_WIDTH
    # Excel column width is character based. Seven pixels is a conservative
    # approximation that is sufficient for deciding whether the image column
    # is touched; drawing row coverage uses the explicit row dimensions above.
    return max(float(width), 1.0) * 7.0 * EMU_PER_PIXEL


def _row_top_emu(worksheet, row: int) -> float:
    return sum(_row_height_emu(worksheet, index) for index in range(1, row))


def _one_cell_horizontal_range(image: object, worksheet) -> tuple[int, int]:
    anchor = getattr(image, "anchor", None)
    from_cell = _anchor_from(anchor)
    if from_cell is None or not isinstance(getattr(from_cell, "col", None), int):
        return (0, -1)

    start_column = from_cell.col + 1
    extension = getattr(anchor, "ext", None)
    width_emu = getattr(extension, "cx", None)
    if not isinstance(width_emu, int) or width_emu <= 0:
        width_emu = int(max(getattr(image, "width", 1), 1) * EMU_PER_PIXEL)

    remaining = float(width_emu)
    column = start_column
    while remaining > _column_width_emu(worksheet, column) and column < start_column + 100:
        remaining -= _column_width_emu(worksheet, column)
        column += 1
    return start_column, column


def get_image_covered_rows(
    image: object,
    worksheet,
    image_column_index: int,
    last_row: int | None = None,
) -> tuple[int, ...]:
    """Return data rows visibly covered by an Excel drawing.

    TwoCellAnchor is authoritative and includes its explicit start/end rows.
    OneCellAnchor uses the drawing extension and worksheet row heights. The
    anchored row is always retained; later rows require at least 60% vertical
    overlap so a one-pixel edge does not merge two unrelated products.
    """

    anchor = getattr(image, "anchor", None)
    from_cell = _anchor_from(anchor)
    if from_cell is None:
        return ()
    start_row_zero = getattr(from_cell, "row", None)
    start_col_zero = getattr(from_cell, "col", None)
    if not isinstance(start_row_zero, int) or not isinstance(start_col_zero, int):
        return ()

    target_column = image_column_index
    to_cell = _anchor_to(anchor)
    if to_cell is not None and isinstance(getattr(to_cell, "col", None), int):
        column_start = start_col_zero + 1
        column_end = max(column_start, to_cell.col + 1)
    else:
        column_start, column_end = _one_cell_horizontal_range(image, worksheet)
    if not column_start <= target_column <= column_end:
        return ()

    start_row = start_row_zero + 1
    if to_cell is not None and isinstance(getattr(to_cell, "row", None), int):
        end_row = max(start_row, to_cell.row + 1)
        return tuple(range(max(2, start_row), end_row + 1))

    extension = getattr(anchor, "ext", None)
    height_emu = getattr(extension, "cy", None)
    if not isinstance(height_emu, int) or height_emu <= 0:
        height_emu = int(max(getattr(image, "height", 1), 1) * EMU_PER_PIXEL)

    row_offset = getattr(from_cell, "rowOff", 0) or 0
    image_top = _row_top_emu(worksheet, start_row) + float(row_offset)
    image_bottom = image_top + float(height_emu)
    scan_last_row = max(last_row or worksheet.max_row, start_row + 100)
    covered: list[int] = []
    for row in range(max(2, start_row), scan_last_row + 1):
        row_top = _row_top_emu(worksheet, row)
        row_bottom = row_top + _row_height_emu(worksheet, row)
        overlap = min(row_bottom, image_bottom) - max(row_top, image_top)
        if overlap <= 0:
            continue
        if row == start_row or overlap / _row_height_emu(worksheet, row) >= MIN_COVERAGE_RATIO:
            covered.append(row)
        if row_top >= image_bottom:
            break
    return tuple(covered)


def _read_embedded_images(
    worksheet,
    *,
    image_column_index: int,
    last_row: int,
) -> dict[int, list[EmbeddedImage]]:
    images_by_row: dict[int, list[EmbeddedImage]] = {}
    for image_index, image in enumerate(getattr(worksheet, "_images", []), start=1):
        covered_rows = get_image_covered_rows(
            image,
            worksheet,
            image_column_index,
            last_row,
        )
        if not covered_rows:
            continue
        try:
            data = image._data()
        except (OSError, ValueError, AttributeError) as error:
            raise ProductImportWorkbookError("无法读取 Excel 中的商品图片。") from error
        if not data:
            raise ProductImportWorkbookError("无法读取 Excel 中的商品图片。")
        embedded = EmbeddedImage(
            image_id=f"image-{image_index}",
            data=data,
            covered_rows=covered_rows,
        )
        for row in covered_rows:
            images_by_row.setdefault(row, []).append(embedded)
    return images_by_row


def _worksheet_has_content(worksheet) -> bool:
    if getattr(worksheet, "_images", None):
        return True
    return any(
        not _is_blank(cell.value)
        for row in worksheet.iter_rows()
        for cell in row
    )


def _select_import_sheet(formula_workbook, value_workbook):
    nonempty_sheet_names = [
        sheet_name
        for sheet_name in formula_workbook.sheetnames
        if _worksheet_has_content(formula_workbook[sheet_name])
    ]
    candidates: list[tuple[str, dict[str, int]]] = []
    errors: dict[str, ProductImportWorkbookError] = {}
    for sheet_name in nonempty_sheet_names:
        worksheet = formula_workbook[sheet_name]
        try:
            candidates.append((sheet_name, build_header_map(worksheet)))
        except ProductImportWorkbookError as error:
            errors[sheet_name] = error

    if len(candidates) > 1:
        names = "、".join(sheet_name for sheet_name, _header_map in candidates)
        raise ProductImportWorkbookError(
            f"发现多个可导入工作表：{names}，请只保留一个。"
        )
    if len(candidates) == 1:
        sheet_name, header_map = candidates[0]
        return (
            formula_workbook[sheet_name],
            value_workbook[sheet_name],
            header_map,
        )

    # Duplicate known headers describe an identifiable sheet with an internal
    # conflict, so keep that actionable diagnostic.  Missing required headers
    # mean the sheet does not satisfy the import contract and use the uniform
    # sheet-level diagnostic below.
    if len(nonempty_sheet_names) == 1:
        sheet_name = nonempty_sheet_names[0]
        error = errors.get(sheet_name)
        if error is not None and (
            str(error).startswith("同时发现")
            or str(error).startswith("发现重复字段表头")
        ):
            raise error

    raise ProductImportWorkbookError("未找到可识别的商品数据工作表。")


def read_import_workbook(data: bytes) -> list[RawImportRow]:
    try:
        formula_workbook = load_workbook(BytesIO(data), read_only=False, data_only=False)
        value_workbook = load_workbook(BytesIO(data), read_only=False, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError, KeyError) as error:
        raise ProductImportWorkbookError(
            "无法读取 Excel 文件，请确认文件是有效的 .xlsx 工作簿。"
        ) from error

    try:
        formula_sheet, value_sheet, header_map = _select_import_sheet(
            formula_workbook,
            value_workbook,
        )
        image_rows = _read_embedded_images(
            formula_sheet,
            image_column_index=header_map["image"],
            last_row=max(formula_sheet.max_row, value_sheet.max_row),
        )
        last_row = max(formula_sheet.max_row, value_sheet.max_row, max(image_rows, default=1))
        rows: list[RawImportRow] = []

        for excel_row in range(2, last_row + 1):
            values = {
                field: value_sheet.cell(row=excel_row, column=column_index).value
                for field, column_index in header_map.items()
            }
            formulas: dict[str, str] = {}
            for field, column_index in header_map.items():
                formula = formula_sheet.cell(row=excel_row, column=column_index).value
                if isinstance(formula, str) and formula.strip().startswith("="):
                    formulas[field] = formula.strip()

            images = tuple(image_rows.get(excel_row, []))
            if all(_is_blank(value) for value in values.values()) and not formulas and not images:
                continue

            rows.append(
                RawImportRow(
                    excel_row=excel_row,
                    values=values,
                    formulas=formulas,
                    images=images,
                )
            )
            if len(rows) >= MAX_PREVIEW_ROWS:
                break

        return rows
    finally:
        formula_workbook.close()
        value_workbook.close()
