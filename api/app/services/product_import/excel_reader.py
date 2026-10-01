from dataclasses import dataclass
from collections import Counter
from io import BytesIO
from itertools import zip_longest
import posixpath
from typing import Final
from xml.etree.ElementTree import ParseError, iterparse
from zipfile import BadZipFile, ZipFile, _ECD_ENTRIES_TOTAL, _ECD_SIZE, _EndRecData
from zlib import error as ZlibError

from openpyxl import load_workbook
from openpyxl.utils import coordinate_to_tuple, get_column_letter, range_boundaries
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet._reader import WorkSheetParser

from .schemas import (
    FIELD_ALIASES,
    REQUIRED_IMPORT_FIELDS,
    normalize_header,
)
from .cancellation import Checkpoint, no_checkpoint


MAX_IMPORT_SOURCE_ROWS: Final[int] = 300
TOO_MANY_SOURCE_ROWS_MESSAGE = "单个 Excel 最多支持 300 条数据行，请拆分后再导入。"
IMPORT_SHEET_NAME: Final[str] = "商品导入"
DEFAULT_ROW_HEIGHT_POINTS: Final[float] = 15.0
DEFAULT_COLUMN_WIDTH: Final[float] = 8.43
EMU_PER_POINT: Final[float] = 12_700.0
EMU_PER_PIXEL: Final[float] = 9_525.0
MIN_COVERAGE_RATIO: Final[float] = 0.60
# Budgets count actual serialized content, never the advertised dimension or
# the blank rectangle between sparse coordinates. The 100 MB upload limit
# remains independent; image-heavy normal uploads can still reach that limit.
MAX_ZIP_ENTRIES: Final[int] = 4096
MAX_ZIP_DIRECTORY_BYTES: Final[int] = 2 * 1024 * 1024
MAX_EXPANDED_BYTES: Final[int] = 512 * 1024 * 1024
MAX_XML_PART_BYTES: Final[int] = 16 * 1024 * 1024
MAX_XML_BYTES: Final[int] = 64 * 1024 * 1024
MAX_XML_ELEMENTS: Final[int] = 300_000
MAX_SERIALIZED_ROWS: Final[int] = 20_000
MAX_WORKBOOK_CELLS: Final[int] = 100_000
MAX_WORKBOOK_IMAGES: Final[int] = 1000
MAX_IMAGE_ROW_SPAN: Final[int] = 1000
MAX_IMAGE_ROW_ASSOCIATIONS: Final[int] = 20_000
EXCEL_MAX_ROW: Final[int] = 1_048_576
EXCEL_MAX_COLUMN: Final[int] = 16_384
SHEET_NAMESPACE: Final[str] = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
DRAWING_NAMESPACE: Final[str] = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}"
DOCUMENT_RELATIONSHIP_NAMESPACE: Final[str] = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
ANCHOR_TAGS: Final[frozenset[str]] = frozenset(
    f"{DRAWING_NAMESPACE}{kind}"
    for kind in ("oneCellAnchor", "twoCellAnchor", "absoluteAnchor")
)
ANCHOR_NAMES: Final[frozenset[str]] = frozenset(tag.rsplit("}", 1)[-1] for tag in ANCHOR_TAGS)
RESOURCE_ERROR_MESSAGE: Final[str] = "Excel 实际内容或图片覆盖区域异常过大，请清理多余空白行/列和图片后重新上传。"
INVALID_WORKBOOK_MESSAGE: Final[str] = "无法读取 Excel 文件，请确认文件是有效的 .xlsx 工作簿。"


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


def _check_anchor_budget(anchor) -> int:
    def child(parent, name):
        return next((node for node in parent if node.tag.rsplit("}", 1)[-1] == name), None)

    markers = []
    for name in ("from", "to"):
        marker = child(anchor, name)
        if marker is not None:
            row_node, column_node = child(marker, "row"), child(marker, "col")
            row = int(row_node.text if row_node is not None else "-1")
            column = int(column_node.text if column_node is not None else "-1")
            if not 0 <= row < EXCEL_MAX_ROW or not 0 <= column < EXCEL_MAX_COLUMN:
                raise ProductImportWorkbookError("Excel 图片锚点超出有效行列范围，请调整图片位置后重新上传。")
            markers.append(row)
    if len(markers) == 2 and abs(markers[1] - markers[0]) + 1 > MAX_IMAGE_ROW_SPAN:
        raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
    extension = child(anchor, "ext")
    if extension is not None:
        # OneCellAnchor may cover more rows when row heights are very small.
        # Actual coverage is checked again before building any row association.
        height = int(extension.get("cy", "0"))
        if height > MAX_IMAGE_ROW_SPAN * DEFAULT_ROW_HEIGHT_POINTS * EMU_PER_POINT:
            raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
    return abs(markers[1] - markers[0]) + 1 if len(markers) == 2 else 1


def _relationship_owner(name: str) -> str:
    folder, basename = posixpath.split(name)
    return posixpath.join(posixpath.dirname(folder), basename[:-5])


def _check_drawing_reference_costs(
    entries, part_costs, relationships, sheet_ids, image_ids, checkpoint: Checkpoint,
) -> None:
    """Count the fixed worksheet -> drawing -> image load path, including reuse.

    No graph recursion: these are precisely the parts normal-mode openpyxl
    opens while binding worksheets and drawings. ZIP names remain case-sensitive;
    only XML/rels suffix classification is case-insensitive.
    """
    by_name = {entry.filename: entry for entry in entries}
    relationship_parts = {}
    for name in relationships:
        owner = _relationship_owner(name)
        # Prefer the exact lowercase sidecar that openpyxl itself looks up.
        if owner not in relationship_parts or name.endswith(".rels"):
            relationship_parts[owner] = name

    def dependencies(owner):
        return relationships.get(relationship_parts.get(owner), [])

    def by_id(owner):
        result = {}
        for relation_id, relation_type, target in dependencies(owner):
            result.setdefault(relation_id, (relation_type, target))
        return result

    xml_loads = Counter()
    image_loads = Counter()
    costs = Counter()
    for cost in part_costs.values():
        costs.update(cost)
    expanded_bytes = sum(entry.file_size for entry in entries)
    image_count = 0

    def enforce():
        if (
            costs["elements"] > MAX_XML_ELEMENTS or costs["rows"] > MAX_SERIALIZED_ROWS
            or costs["cells"] > MAX_WORKBOOK_CELLS or costs["anchors"] > MAX_WORKBOOK_IMAGES
            or costs["associations"] > MAX_IMAGE_ROW_ASSOCIATIONS
            or costs["bytes"] > MAX_XML_BYTES or expanded_bytes > MAX_EXPANDED_BYTES
            or image_count > MAX_WORKBOOK_IMAGES
        ):
            raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)

    def load_xml(name, count):
        nonlocal expanded_bytes
        if name not in by_name:
            return
        # A relationship must not smuggle an unscanned XML part under a
        # non-XML suffix. Normal OOXML XML parts (in any case) are scanned.
        if name not in part_costs:
            raise ProductImportWorkbookError(INVALID_WORKBOOK_MESSAGE)
        previous = xml_loads[name]
        xml_loads[name] += count
        extra = max(0, xml_loads[name] - 1) - max(0, previous - 1)
        costs.update({key: value * extra for key, value in part_costs[name].items()})
        expanded_bytes += by_name[name].file_size * extra
        enforce()

    sheets = Counter()
    for workbook, ids in sheet_ids.items():
        deps = by_id(workbook)
        for relation_id in ids:
            relation_type, target = deps.get(relation_id, ("", ""))
            if target in by_name and not relation_type.endswith("/chartsheet"):
                sheets[target] += 1

    for sheet, count in sheets.items():
        checkpoint()
        load_xml(sheet, count)
        if sheet in relationship_parts:
            load_xml(relationship_parts[sheet], count)
        for _relation_id, relation_type, drawing in dependencies(sheet):
            if not relation_type.endswith("/drawing") or drawing not in by_name:
                continue
            load_xml(drawing, count)
            if drawing in relationship_parts:
                load_xml(relationship_parts[drawing], count)
            deps = by_id(drawing)
            for image_id in image_ids.get(drawing, []):
                image_type, media = deps.get(image_id, ("", ""))
                if image_type.endswith("/image") and media in by_name:
                    previous = image_loads[media]
                    image_loads[media] += count
                    extra = max(0, image_loads[media] - 1) - max(0, previous - 1)
                    expanded_bytes += by_name[media].file_size * extra
                    image_count += count
                    enforce()


def _check_workbook_resources(data: bytes, checkpoint: Checkpoint) -> None:
    """Bound ZIP/XML work before openpyxl loads cells, drawings or Pillow.

    This inspects resource metadata only; openpyxl still interprets all cell
    values, formulas, styles and drawings. Empty distant formatting is allowed.
    Merged/hyperlink areas count too: normal-mode openpyxl materializes them.
    """
    checkpoint()
    # Inspect only the end record first, using zipfile's own ZIP64-aware reader.
    # ZipFile would otherwise allocate every central-directory entry before we
    # could reject an archive with millions of tiny entries.
    end_record = _EndRecData(BytesIO(data))
    if end_record is None:
        raise ProductImportWorkbookError(INVALID_WORKBOOK_MESSAGE)
    if end_record[_ECD_ENTRIES_TOTAL] > MAX_ZIP_ENTRIES or end_record[_ECD_SIZE] > MAX_ZIP_DIRECTORY_BYTES:
        raise ProductImportWorkbookError("Excel 压缩包目录异常过大，请精简工作簿后重新上传。")
    with ZipFile(BytesIO(data)) as archive:
        entries = archive.infolist()
        xml_entries = [entry for entry in entries if entry.filename.lower().endswith((".xml", ".rels"))]
        if (
            len(entries) > MAX_ZIP_ENTRIES
            or sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES
            or sum(entry.file_size for entry in xml_entries) > MAX_XML_BYTES
            or any(entry.file_size > MAX_XML_PART_BYTES for entry in xml_entries)
            or sum(entry.filename.lower().startswith("xl/media/") for entry in entries) > MAX_WORKBOOK_IMAGES
        ):
            raise ProductImportWorkbookError("Excel 解压后的内容异常过大，请精简工作簿后重新上传。")
        # Content types identify XML even when a relationship uses an unusual
        # part suffix. This is only package metadata, not a second XLSX parser.
        by_name = {entry.filename: entry for entry in entries}
        xml_names = {entry.filename for entry in xml_entries}
        types_part = by_name.get("[Content_Types].xml")
        if types_part is not None:
            xml_extensions = set()
            type_nodes = 0
            for _event, node in iterparse(BytesIO(archive.read(types_part))):
                type_nodes += 1
                if type_nodes > MAX_XML_ELEMENTS:
                    raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
                if type_nodes % 128 == 0:
                    checkpoint()
                content_type = node.get("ContentType", "").lower()
                if content_type.endswith(("+xml", "/xml")):
                    if node.tag.endswith("}Override"):
                        xml_names.add(node.get("PartName", "").lstrip("/"))
                    elif node.tag.endswith("}Default"):
                        xml_extensions.add(node.get("Extension", "").lower())
                node.clear()
            xml_entries = [entry for entry in entries if entry.filename in xml_names or entry.filename.rsplit(".", 1)[-1].lower() in xml_extensions]
            if any(entry.file_size > MAX_XML_PART_BYTES for entry in xml_entries) or sum(entry.file_size for entry in xml_entries) > MAX_XML_BYTES:
                raise ProductImportWorkbookError("Excel 解压后的内容异常过大，请精简工作簿后重新上传。")
        elements = rows = cells = anchors = image_associations = 0
        part_costs, relationships, sheet_ids, image_ids = {}, {}, {}, {}
        for entry in xml_entries:
            checkpoint()
            content = archive.read(entry)
            if b"<!DOCTYPE" in content or b"<!ENTITY" in content:
                raise ProductImportWorkbookError(INVALID_WORKBOOK_MESSAGE)
            anchor_depth = 0
            current_row = previous_row = current_column = 0
            in_data_row = False
            before = (elements, rows, cells, anchors, image_associations)
            if entry.filename.lower().endswith(".rels"):
                relationships[entry.filename] = []
            for event, node in iterparse(BytesIO(content), events=("start", "end")):
                local_name = node.tag.rsplit("}", 1)[-1]
                if event == "start":
                    elements += 1
                    if elements > MAX_XML_ELEMENTS:
                        raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
                    if elements % 128 == 0:
                        checkpoint()
                    # openpyxl's from_tree strips element namespaces in these
                    # collections; count the same names it will actually load.
                    if local_name == "Relationship" and node.get("TargetMode") != "External":
                        owner = _relationship_owner(entry.filename)
                        target = node.get("Target", "")
                        target = target[1:] if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(owner), target))
                        relationships.setdefault(entry.filename, []).append((node.get("Id", ""), node.get("Type", ""), target))
                    elif local_name == "sheet":
                        relation_id = node.get(f"{DOCUMENT_RELATIONSHIP_NAMESPACE}id")
                        if relation_id:
                            sheet_ids.setdefault(entry.filename, []).append(relation_id)
                    if node.tag == f"{SHEET_NAMESPACE}row":
                        in_data_row = True
                        rows += 1
                        current_row = int(node.get("r", previous_row + 1))
                        current_column = 0
                        if not previous_row < current_row <= EXCEL_MAX_ROW:
                            raise ProductImportWorkbookError("Excel 数据行号异常，请重新保存工作簿后上传。")
                        previous_row = current_row
                    elif local_name == "c" and in_data_row:
                        cells += 1
                        row, column = coordinate_to_tuple(node.get("r")) if node.get("r") else (current_row, current_column + 1)
                        if row != current_row or not 1 <= column <= EXCEL_MAX_COLUMN:
                            raise ProductImportWorkbookError("Excel 单元格坐标异常，请重新保存工作簿后上传。")
                        current_column = column
                    elif local_name == "comment":
                        # openpyxl indexes comment references with ws[ref];
                        # an invalid range here could materialize a rectangle.
                        row, column = coordinate_to_tuple(node.get("ref", ""))
                        if not (1 <= row <= EXCEL_MAX_ROW and 1 <= column <= EXCEL_MAX_COLUMN):
                            raise ProductImportWorkbookError(INVALID_WORKBOOK_MESSAGE)
                        cells += 1
                    elif local_name in {"mergeCell", "hyperlink"}:
                        left, top, right, bottom = range_boundaries(node.get("ref", ""))
                        if None in (left, top, right, bottom) or not (1 <= top <= bottom <= EXCEL_MAX_ROW and 1 <= left <= right <= EXCEL_MAX_COLUMN):
                            raise ProductImportWorkbookError(INVALID_WORKBOOK_MESSAGE)
                        cells += (right - left + 1) * (bottom - top + 1)
                    elif local_name in ANCHOR_NAMES:
                        anchors += 1
                        anchor_depth += 1
                    if rows > MAX_SERIALIZED_ROWS or cells > MAX_WORKBOOK_CELLS or anchors > MAX_WORKBOOK_IMAGES:
                        raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
                else:
                    if node.tag == f"{SHEET_NAMESPACE}row":
                        in_data_row = False
                    if local_name in ANCHOR_NAMES:
                        image_ids.setdefault(entry.filename, []).extend(
                            blip.get(f"{DOCUMENT_RELATIONSHIP_NAMESPACE}embed")
                            for blip in node.iter()
                            if blip.tag.rsplit("}", 1)[-1] == "blip" and blip.get(f"{DOCUMENT_RELATIONSHIP_NAMESPACE}embed")
                        )
                        image_associations += _check_anchor_budget(node)
                        if image_associations > MAX_IMAGE_ROW_ASSOCIATIONS:
                            raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
                        anchor_depth -= 1
                    if not anchor_depth:
                        node.clear()
            after = (elements, rows, cells, anchors, image_associations)
            part_costs[entry.filename] = dict(zip(
                ("elements", "rows", "cells", "anchors", "associations"),
                (new - old for new, old in zip(after, before)),
            ), bytes=entry.file_size)
        _check_drawing_reference_costs(entries, part_costs, relationships, sheet_ids, image_ids, checkpoint)


def _read_sparse_sheet(worksheet, checkpoint: Checkpoint) -> dict[int, dict[int, object]]:
    """Use openpyxl's value/formula parser without iter_rows' blank padding.

    The small adapter is specific to openpyxl 3.1 (our existing dependency).
    Keeping its cell decoding preserves cached formulas and date semantics.
    """
    rows: dict[int, dict[int, object]] = {}
    if not worksheet.parent.read_only:
        for index, ((row, column), cell) in enumerate(worksheet._cells.items()):
            if index % 128 == 0:
                checkpoint()
            if cell.value is not None:
                rows.setdefault(row, {})[column] = cell.value
        checkpoint()
        return rows
    with worksheet._get_source() as source:
        parser = WorkSheetParser(
            source, worksheet._shared_strings, data_only=worksheet.parent.data_only,
            epoch=worksheet.parent.epoch, date_formats=worksheet.parent._date_formats,
            timedelta_formats=worksheet.parent._timedelta_formats,
        )
        for row, cells in parser.parse():
            checkpoint()
            values = {cell["column"]: cell["value"] for cell in cells if cell["value"] is not None}
            if values:
                rows[row] = values
    return rows


def build_header_map(header_values: dict[int, object]) -> dict[str, int]:
    """Map normalized known headers to canonical field names.

    Unknown columns are deliberately absent from the result.  This is the
    boundary that prevents historical columns from affecting Preview parsing.
    """

    alias_to_field = _canonical_header_map()
    stock_matches: list[tuple[int, str]] = []
    for column_index, cell_value in sorted(header_values.items()):
        header = normalize_header(cell_value)
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
    for column_index, cell_value in sorted(header_values.items()):
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
        if end_row - start_row + 1 > MAX_IMAGE_ROW_SPAN:
            raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
        return tuple(range(max(2, start_row), end_row + 1))

    extension = getattr(anchor, "ext", None)
    height_emu = getattr(extension, "cy", None)
    if not isinstance(height_emu, int) or height_emu <= 0:
        height_emu = int(max(getattr(image, "height", 1), 1) * EMU_PER_PIXEL)

    row_offset = getattr(from_cell, "rowOff", 0) or 0
    # Relative row heights give the same overlap without summing all preceding
    # rows (which was quadratic, and particularly costly for distant anchors).
    image_top = float(row_offset)
    image_bottom = image_top + float(height_emu)
    covered: list[int] = []
    row_top = 0.0
    for row in range(start_row, min(EXCEL_MAX_ROW, start_row + MAX_IMAGE_ROW_SPAN - 1) + 1):
        row_height = _row_height_emu(worksheet, row)
        row_bottom = row_top + row_height
        overlap = min(row_bottom, image_bottom) - max(row_top, image_top)
        if row >= 2 and overlap > 0 and (row == start_row or overlap / row_height >= MIN_COVERAGE_RATIO):
            covered.append(row)
        if row_bottom >= image_bottom:
            break
        row_top = row_bottom
    else:
        raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
    return tuple(covered)


def _read_embedded_images(
    worksheet,
    *,
    image_column_index: int,
    last_row: int,
    checkpoint: Checkpoint = no_checkpoint,
) -> dict[int, list[EmbeddedImage]]:
    images_by_row: dict[int, list[EmbeddedImage]] = {}
    associations = 0
    for image_index, image in enumerate(getattr(worksheet, "_images", []), start=1):
        checkpoint()
        covered_rows = get_image_covered_rows(
            image,
            worksheet,
            image_column_index,
            last_row,
        )
        if not covered_rows:
            continue
        associations += len(covered_rows)
        if associations > MAX_IMAGE_ROW_ASSOCIATIONS:
            raise ProductImportWorkbookError(RESOURCE_ERROR_MESSAGE)
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
        checkpoint()
    return images_by_row


def _select_import_sheet(formula_workbook, value_workbook, checkpoint: Checkpoint):
    sparse_sheets = {
        name: _read_sparse_sheet(formula_workbook[name], checkpoint)
        for name in formula_workbook.sheetnames
    }
    nonempty_sheet_names = [
        sheet_name
        for sheet_name in formula_workbook.sheetnames
        if any(not _is_blank(value) for row in sparse_sheets[sheet_name].values() for value in row.values())
        or getattr(formula_workbook[sheet_name], "_images", None)
    ]
    candidates: list[tuple[str, dict[str, int]]] = []
    errors: dict[str, ProductImportWorkbookError] = {}
    for sheet_name in nonempty_sheet_names:
        try:
            candidates.append((sheet_name, build_header_map(sparse_sheets[sheet_name].get(1, {}))))
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
            sparse_sheets[sheet_name],
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


def read_import_workbook(
    data: bytes,
    *,
    include_images: bool = True,
    checkpoint: Checkpoint = no_checkpoint,
) -> list[RawImportRow]:
    class CheckedBuffer(BytesIO):
        def read(self, size: int = -1) -> bytes:
            checkpoint()
            result = super().read(size)
            checkpoint()
            return result

    # Read-only mode deliberately skips Drawing/Pillow work during precheck.
    formula_workbook = None
    value_workbook = None
    try:
        _check_workbook_resources(data, checkpoint)
        formula_workbook = load_workbook(CheckedBuffer(data), read_only=not include_images, data_only=False)
        checkpoint()
        # The value copy never needs drawings, even in the image phase.
        value_workbook = load_workbook(CheckedBuffer(data), read_only=True, data_only=True)
    except (
        BadZipFile, InvalidFileException, OSError, ValueError, KeyError, TypeError,
        IndexError, OverflowError, ParseError, RuntimeError, NotImplementedError, ZlibError,
    ) as error:
        if formula_workbook is not None:
            formula_workbook.close()
        if isinstance(error, ProductImportWorkbookError):
            raise
        raise ProductImportWorkbookError(INVALID_WORKBOOK_MESSAGE) from error
    except BaseException:
        if formula_workbook is not None:
            formula_workbook.close()
        if value_workbook is not None:
            value_workbook.close()
        raise

    try:
        formula_sheet, value_sheet, header_map, formula_data = _select_import_sheet(
            formula_workbook,
            value_workbook,
            checkpoint,
        )
        value_data = _read_sparse_sheet(value_sheet, checkpoint)
        checkpoint()
        image_rows = _read_embedded_images(
            formula_sheet,
            image_column_index=header_map["image"],
            last_row=max(formula_data, default=1),
            checkpoint=checkpoint,
        ) if include_images else {}
        rows: list[RawImportRow] = []
        # Align by original row identity, including image-only tails. Neither
        # workbook dimension nor iterator length decides which rows survive.
        excel_rows = sorted((formula_data.keys() | value_data.keys() | image_rows.keys()) - {1})
        formula_rows = (formula_data.get(row, {}) for row in excel_rows)
        value_rows = (value_data.get(row, {}) for row in excel_rows)
        for position, (formula_values, cell_values) in enumerate(
            zip_longest(formula_rows, value_rows, fillvalue={}),
        ):
            excel_row = excel_rows[position]
            checkpoint()
            values = {
                field: cell_values.get(column_index)
                for field, column_index in header_map.items()
            }
            formulas: dict[str, str] = {}
            for field, column_index in header_map.items():
                formula = formula_values.get(column_index)
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
            if len(rows) > MAX_IMPORT_SOURCE_ROWS:
                raise ProductImportWorkbookError(TOO_MANY_SOURCE_ROWS_MESSAGE)

        return rows
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, ParseError) as error:
        if isinstance(error, ProductImportWorkbookError):
            raise
        raise ProductImportWorkbookError(INVALID_WORKBOOK_MESSAGE) from error
    finally:
        formula_workbook.close()
        value_workbook.close()
