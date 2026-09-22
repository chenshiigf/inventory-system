from __future__ import annotations

import re
import uuid
import warnings
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path
from typing import Final

from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Warehouse
from app.schemas import ProductPrice

from .excel_reader import EmbeddedImage, RawImportRow
from .schemas import ProductImportPreviewResponse, ProductImportPreviewRow


PREVIEW_IMAGE_MAX_SIDE: Final[int] = 300
PREVIEW_IMAGE_QUALITY: Final[int] = 80
INTEGER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[+-]?\d+$")
PRICE_ADAPTER = TypeAdapter(ProductPrice)


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_integer(
    value: object,
    *,
    label: str,
    positive: bool,
) -> tuple[int | None, str | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, f"{label}不能为空"
    if isinstance(value, bool):
        return None, f"{label}必须是整数"

    parsed: int | None = None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, Decimal) and value == value.to_integral_value():
        parsed = int(value)
    elif isinstance(value, float) and value.is_integer():
        parsed = int(value)
    elif isinstance(value, str) and INTEGER_PATTERN.fullmatch(value.strip()):
        parsed = int(value.strip())

    if parsed is None:
        return None, f"{label}必须是整数"
    if positive and parsed <= 0:
        return None, f"{label}必须是正整数"
    if not positive and parsed < 0:
        return None, f"{label}不能小于 0"
    return parsed, None


def _parse_price(value: object) -> tuple[str | None, str | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, "单价不能为空"
    if isinstance(value, bool):
        return None, "单价必须是合法数字"

    raw_value = str(value).strip()
    try:
        parsed = Decimal(raw_value)
        if not parsed.is_finite():
            raise InvalidOperation
        normalized = PRICE_ADAPTER.validate_python(parsed)
    except (InvalidOperation, ValueError, TypeError, ValidationError):
        return None, "单价必须是 >= 0 且最多两位小数的合法数字"

    return format(normalized.quantize(Decimal("0.01")), "f"), None


def _save_preview_image(
    image: EmbeddedImage,
    session_directory: Path,
    excel_row: int,
) -> None:
    output_path = session_directory / f"row-{excel_row}.webp"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image.data)) as source:
                if getattr(source, "is_animated", False) or getattr(
                    source, "n_frames", 1
                ) > 1:
                    raise ValueError("animated image")
                oriented = ImageOps.exif_transpose(source)
                oriented.thumbnail(
                    (PREVIEW_IMAGE_MAX_SIDE, PREVIEW_IMAGE_MAX_SIDE),
                    Image.Resampling.LANCZOS,
                )
                has_transparency = (
                    "A" in oriented.getbands()
                    or "transparency" in oriented.info
                )
                normalized = oriented.convert("RGBA" if has_transparency else "RGB")
                normalized.save(
                    output_path,
                    format="WEBP",
                    quality=PREVIEW_IMAGE_QUALITY,
                    method=5,
                )
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        OSError,
        UnidentifiedImageError,
        ValueError,
        SyntaxError,
    ) as error:
        raise ValueError("商品图片无法解析") from error


def _category_indexes(
    db: Session,
) -> tuple[
    dict[str, Category],
    dict[tuple[int, str], Category],
    dict[str, list[Category]],
]:
    categories = db.scalars(select(Category)).all()
    root_by_name = {
        category.name.strip(): category
        for category in categories
        if category.parent_id is None
    }
    child_by_parent_and_name = {
        (category.parent_id, category.name.strip()): category
        for category in categories
        if category.parent_id is not None
    }
    children_by_name: dict[str, list[Category]] = {}
    for category in categories:
        if category.parent_id is not None:
            children_by_name.setdefault(category.name.strip(), []).append(category)
    return root_by_name, child_by_parent_and_name, children_by_name


def build_product_import_preview(
    *,
    rows: list[RawImportRow],
    db: Session,
    preview_directory: Path,
    preview_url_prefix: str = "/import-previews",
    file_name: str,
) -> ProductImportPreviewResponse:
    warehouses = db.scalars(select(Warehouse)).all()
    warehouse_by_name = {warehouse.name.strip(): warehouse for warehouse in warehouses}
    root_by_name, child_by_parent_and_name, children_by_name = _category_indexes(db)

    session_id = uuid.uuid4().hex
    session_directory = preview_directory / session_id
    session_directory.mkdir(parents=True, exist_ok=False)
    preview_rows: list[ProductImportPreviewRow] = []

    for raw_row in rows:
        values = raw_row.values
        warehouse_name = _text(values.get("warehouse"))
        category_level_1 = _text(values.get("category_level_1"))
        category_level_2 = _text(values.get("category_level_2"))
        size = _text(values.get("size"))
        unit = _text(values.get("unit")).lower()
        remark = _text(values.get("remark"))
        source_code = _text(values.get("source_code"))
        messages: list[str] = []
        warnings_found: list[str] = []

        if not warehouse_name:
            messages.append("仓库不能为空")
        elif warehouse_name not in warehouse_by_name:
            messages.append(f"仓库不存在：{warehouse_name}")

        parent = None
        if not category_level_1:
            messages.append("一级分类不能为空")
        else:
            parent = root_by_name.get(category_level_1)
            if parent is None:
                messages.append(f"一级分类不存在：{category_level_1}")

        if not category_level_2:
            messages.append("二级分类不能为空")
        elif parent is not None:
            child = child_by_parent_and_name.get((parent.id, category_level_2))
            if child is None:
                if category_level_2 in children_by_name:
                    messages.append(
                        f"二级分类‘{category_level_2}’不属于一级分类‘{category_level_1}’"
                    )
                else:
                    messages.append(f"二级分类不存在：{category_level_2}")

        packing_qty, packing_error = _parse_integer(
            values.get("packing_qty"),
            label="装箱数",
            positive=True,
        )
        if packing_error:
            messages.append(packing_error)

        if not unit:
            messages.append("单位不能为空")
        elif unit not in {"pcs", "set"}:
            messages.append("单位必须为 pcs 或 set")

        price, price_error = _parse_price(values.get("price"))
        if price_error:
            messages.append(price_error)

        carton_count, carton_error = _parse_integer(
            values.get("carton_count"),
            label="当前箱数",
            positive=False,
        )
        if carton_error:
            messages.append(carton_error)

        if len(size) > 200:
            messages.append("产品尺寸不能超过 200 个字符")
        elif not size:
            warnings_found.append("产品尺寸为空")

        if len(remark) > 2000:
            messages.append("备注不能超过 2000 个字符")

        has_image = len(raw_row.images) > 0
        image_preview_url: str | None = None
        if not has_image:
            warnings_found.append("无商品图片")
        else:
            if len(raw_row.images) > 1:
                messages.append("一行只能有一张商品图片")
            try:
                _save_preview_image(raw_row.images[0], session_directory, raw_row.excel_row)
                image_preview_url = (
                    f"{preview_url_prefix}/{session_id}/row-{raw_row.excel_row}.webp"
                )
            except ValueError as error:
                messages.append(str(error))

        all_messages = [*messages, *warnings_found]
        row_status = "error" if messages else "warning" if warnings_found else "valid"
        preview_rows.append(
            ProductImportPreviewRow(
                excel_row=raw_row.excel_row,
                warehouse=warehouse_name,
                category_level_1=category_level_1,
                category_level_2=category_level_2,
                image_preview_url=image_preview_url,
                has_image=has_image,
                size=size,
                packing_qty=packing_qty,
                unit=unit,
                price=price,
                carton_count=carton_count,
                remark=remark,
                source_code=source_code,
                status=row_status,
                messages=all_messages,
            )
        )

    valid_count = sum(row.status == "valid" for row in preview_rows)
    warning_count = sum(row.status == "warning" for row in preview_rows)
    error_count = sum(row.status == "error" for row in preview_rows)
    return ProductImportPreviewResponse(
        file_name=file_name,
        total_rows=len(preview_rows),
        valid_count=valid_count,
        warning_count=warning_count,
        error_count=error_count,
        rows=preview_rows,
    )
