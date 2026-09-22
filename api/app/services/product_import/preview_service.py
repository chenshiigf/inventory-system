from __future__ import annotations

import re
import uuid
import warnings
from collections import Counter, OrderedDict
from dataclasses import dataclass
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
from .schemas import (
    ProductImportPreviewPackaging,
    ProductImportPreviewProduct,
    ProductImportPreviewResponse,
)


PREVIEW_IMAGE_MAX_SIDE: Final[int] = 300
PREVIEW_IMAGE_QUALITY: Final[int] = 80
INTEGER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[+-]?\d+$")
PRICE_ADAPTER = TypeAdapter(ProductPrice)
GROUP_FIELDS: Final[tuple[str, ...]] = (
    "warehouse",
    "category_level_1",
    "category_level_2",
    "size",
    "unit",
    "price",
    "remark",
)
FIELD_LABELS: Final[dict[str, str]] = {
    "warehouse": "仓库",
    "category_level_1": "一级分类",
    "category_level_2": "二级分类",
    "size": "产品尺寸",
    "unit": "单位",
    "price": "单价",
    "remark": "备注",
}
FORMULA_CACHE_ERROR: Final[str] = (
    "结余箱数为公式，但没有可读取的计算结果。请使用 Microsoft Excel 打开文件、确认公式结果正常后保存，再重新上传。"
)


@dataclass(frozen=True)
class PreparedProductImport:
    response: ProductImportPreviewResponse
    images_by_id: dict[str, EmbeddedImage]
    product_image_ids: dict[str, str | None]


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _unique_messages(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(message for message in messages if message))


def _parse_integer(
    value: object,
    *,
    label: str,
    positive: bool,
    allow_blank: bool = False,
) -> tuple[int | None, str | None]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return (None, None) if allow_blank else (None, f"{label}不能为空")
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
        return None, None
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


def _parse_carton_count(raw_row: RawImportRow) -> tuple[int | None, str | None]:
    if "carton_count" in raw_row.formulas:
        if raw_row.values.get("carton_count") is None or not _text(
            raw_row.values.get("carton_count")
        ):
            return None, FORMULA_CACHE_ERROR
    return _parse_integer(
        raw_row.values.get("carton_count"),
        label="结余箱数",
        positive=False,
    )


def _save_preview_image(
    image: EmbeddedImage,
    session_directory: Path,
) -> None:
    output_path = session_directory / f"{image.image_id}.webp"
    if output_path.exists():
        return
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


def _normalized_group_value(field: str, value: object) -> str:
    normalized = _text(value)
    if field == "unit":
        return normalized.lower()
    if field == "price":
        price, _error = _parse_price(value)
        return price or normalized
    return normalized


def _resolve_group_fields(
    rows: list[RawImportRow],
    product_group: str | None,
) -> tuple[dict[str, str], list[str]]:
    primary = rows[0]
    resolved = {
        field: _normalized_group_value(field, primary.values.get(field))
        for field in GROUP_FIELDS
    }
    conflicts: list[str] = []
    if product_group is None:
        return resolved, conflicts

    for row in rows[1:]:
        for field in GROUP_FIELDS:
            current = _normalized_group_value(field, row.values.get(field))
            if not current:
                continue
            if not resolved[field]:
                conflicts.append(
                    f"商品组 {product_group} 的{FIELD_LABELS[field]}主行不能为空"
                )
                continue
            if current == resolved[field]:
                continue
            if field == "remark":
                conflicts.append(f"商品组 {product_group} 的备注不一致")
            else:
                conflicts.append(
                    f"商品组 {product_group} 的{FIELD_LABELS[field]}不一致："
                    f"{resolved[field]} / {current}"
                )
    return resolved, _unique_messages(conflicts)


def _validate_reference_fields(
    *,
    values: dict[str, str],
    warehouse_by_name: dict[str, Warehouse],
    root_by_name: dict[str, Category],
    child_by_parent_and_name: dict[tuple[int, str], Category],
    children_by_name: dict[str, list[Category]],
) -> list[str]:
    errors: list[str] = []
    warehouse = values["warehouse"]
    if not warehouse:
        errors.append("仓库不能为空")
    elif warehouse not in warehouse_by_name:
        errors.append(f"仓库不存在：{warehouse}")

    category_level_1 = values["category_level_1"]
    category_level_2 = values["category_level_2"]
    parent = None
    if not category_level_1:
        errors.append("一级分类不能为空")
    else:
        parent = root_by_name.get(category_level_1)
        if parent is None:
            errors.append(f"一级分类不存在：{category_level_1}")

    if not category_level_2:
        errors.append("二级分类不能为空")
    elif parent is not None:
        child = child_by_parent_and_name.get((parent.id, category_level_2))
        if child is None:
            if category_level_2 in children_by_name:
                errors.append(
                    f"二级分类‘{category_level_2}’不属于一级分类‘{category_level_1}’"
                )
            else:
                errors.append(f"二级分类不存在：{category_level_2}")
    return errors


def _validate_product_fields(values: dict[str, str]) -> tuple[str | None, list[str], list[str]]:
    errors: list[str] = []
    warnings_found: list[str] = []
    unit = values["unit"]
    if unit and unit not in {"pcs", "set"}:
        errors.append("单位必须为 pcs 或 set")

    price, price_error = _parse_price(values["price"])
    if price_error:
        errors.append(price_error)

    size = values["size"]
    if len(size) > 200:
        errors.append("产品尺寸不能超过 200 个字符")
    elif not size:
        warnings_found.append("产品尺寸为空")

    if len(values["remark"]) > 2000:
        errors.append("备注不能超过 2000 个字符")
    return price, errors, warnings_found


def _append_secondary_stock(
    remark: str,
    rows: list[RawImportRow],
) -> tuple[str, list[str]]:
    total = 0
    warnings_found: list[str] = []
    for row in rows:
        value = row.values.get("secondary_stock")
        if value is None or not _text(value):
            continue
        parsed, error = _parse_integer(value, label="义库", positive=False)
        if error:
            warnings_found.append("义库值无法识别，未合并到备注。")
        elif parsed:
            total += parsed
    if total <= 0:
        return remark, _unique_messages(warnings_found)
    suffix = f"义库：{total}箱"
    return (f"{remark}；{suffix}" if remark else suffix), _unique_messages(warnings_found)


def prepare_product_import_preview(
    *,
    rows: list[RawImportRow],
    db: Session,
    preview_directory: Path,
    preview_url_prefix: str = "/import-previews",
    file_name: str,
    session_id: str | None = None,
    already_imported: bool = False,
    write_preview_images: bool = True,
) -> PreparedProductImport:
    warehouses = db.scalars(select(Warehouse)).all()
    warehouse_by_name = {warehouse.name.strip(): warehouse for warehouse in warehouses}
    root_by_name, child_by_parent_and_name, children_by_name = _category_indexes(db)

    session_id = session_id or uuid.uuid4().hex
    session_directory = preview_directory / session_id
    if write_preview_images:
        session_directory.mkdir(parents=True, exist_ok=True)

    grouped_rows: OrderedDict[str, list[RawImportRow]] = OrderedDict()
    group_names: dict[str, str | None] = {}
    for raw_row in rows:
        product_group = _text(raw_row.values.get("product_group")) or None
        group_key = f"group:{product_group}" if product_group else f"row:{raw_row.excel_row}"
        grouped_rows.setdefault(group_key, []).append(raw_row)
        group_names[group_key] = product_group

    records: list[dict[str, object]] = []
    image_usage: Counter[str] = Counter()
    image_by_id: dict[str, EmbeddedImage] = {}
    product_image_ids: dict[str, str | None] = {}

    for product_index, (group_key, group_rows) in enumerate(grouped_rows.items(), start=1):
        product_group = group_names[group_key]
        resolved, conflict_errors = _resolve_group_fields(group_rows, product_group)
        errors = list(conflict_errors)
        warnings_found: list[str] = []

        errors.extend(
            _validate_reference_fields(
                values=resolved,
                warehouse_by_name=warehouse_by_name,
                root_by_name=root_by_name,
                child_by_parent_and_name=child_by_parent_and_name,
                children_by_name=children_by_name,
            )
        )
        price, field_errors, field_warnings = _validate_product_fields(resolved)
        errors.extend(field_errors)
        warnings_found.extend(field_warnings)

        resolved_remark, secondary_warnings = _append_secondary_stock(
            resolved["remark"],
            group_rows,
        )
        resolved["remark"] = resolved_remark
        warnings_found.extend(secondary_warnings)
        if len(resolved_remark) > 2000:
            errors.append("备注不能超过 2000 个字符")

        unique_images: OrderedDict[str, EmbeddedImage] = OrderedDict()
        for raw_row in group_rows:
            if len(raw_row.images) > 1:
                errors.append(
                    f"Excel第 {raw_row.excel_row} 行检测到多张商品图片，请整理为一张主图。"
                )
            for image in raw_row.images:
                unique_images.setdefault(image.image_id, image)
                image_by_id[image.image_id] = image

        if len(unique_images) > 1:
            group_label = product_group or f"Excel第 {group_rows[0].excel_row}行"
            if product_group:
                errors.append(
                    f"商品组 {group_label} 检测到多张商品图片，请只保留一张主图。"
                )
        elif not unique_images:
            errors.append("无商品图片")

        preview_image_id: str | None = None
        image_preview_url: str | None = None
        if len(unique_images) == 1:
            preview_image_id, image = next(iter(unique_images.items()))
            try:
                if write_preview_images:
                    _save_preview_image(image, session_directory)
                image_preview_url = (
                    f"{preview_url_prefix}/{session_id}/{preview_image_id}.webp"
                )
            except ValueError as error:
                errors.append(str(error))

        packagings: list[ProductImportPreviewPackaging] = []
        seen_packing_quantities: set[int] = set()
        for raw_row in group_rows:
            packing_qty, packing_error = _parse_integer(
                raw_row.values.get("packing_qty"),
                label="装箱数",
                positive=True,
                allow_blank=len(group_rows) == 1,
            )
            if packing_error:
                errors.append(f"Excel第 {raw_row.excel_row} 行：{packing_error}")

            carton_count, carton_error = _parse_carton_count(raw_row)
            if carton_error:
                errors.append(f"Excel第 {raw_row.excel_row} 行：{carton_error}")

            if packing_qty is not None and packing_qty in seen_packing_quantities:
                group_label = product_group or f"Excel第 {group_rows[0].excel_row}行"
                errors.append(
                    f"商品组 {group_label} 存在重复装箱数：{packing_qty}"
                )
                continue
            if packing_qty is not None:
                seen_packing_quantities.add(packing_qty)
            packagings.append(
                ProductImportPreviewPackaging(
                    excel_row=raw_row.excel_row,
                    packing_qty=packing_qty,
                    carton_count=carton_count,
                    source_code=_text(raw_row.values.get("source_code")),
                )
            )

        if not packagings:
            # A group can only be empty when the source row itself was present,
            # so retain one audit slot even if duplicate detection removed it.
            raw_row = group_rows[0]
            packagings.append(
                ProductImportPreviewPackaging(
                    excel_row=raw_row.excel_row,
                    packing_qty=None,
                    carton_count=None,
                    source_code=_text(raw_row.values.get("source_code")),
                )
            )

        source_codes = list(
            dict.fromkeys(
                source_code
                for source_code in (
                    _text(row.values.get("source_code")) for row in group_rows
                )
                if source_code
            )
        )
        total_carton_count = sum(
            packaging.carton_count or 0 for packaging in packagings
        )
        for image_id in unique_images:
            image_usage[image_id] += 1

        preview_id = f"preview-{session_id}-{product_index}"
        product_image_ids[preview_id] = preview_image_id
        records.append(
            {
                "preview_id": preview_id,
                "product_group": product_group,
                "excel_rows": [row.excel_row for row in group_rows],
                "warehouse": resolved["warehouse"],
                "category_level_1": resolved["category_level_1"],
                "category_level_2": resolved["category_level_2"],
                "image_preview_url": image_preview_url,
                "shared_image": False,
                "size": resolved["size"],
                "unit": resolved["unit"] or None,
                "price": price,
                "remark": resolved["remark"],
                "source_codes": source_codes,
                "packagings": packagings,
                "total_carton_count": total_carton_count,
                "status": "error" if errors else "warning" if warnings_found else "valid",
                "messages": _unique_messages([*errors, *warnings_found]),
                "_image_id": preview_image_id,
            }
        )

    for record in records:
        image_id = record.pop("_image_id")
        record["shared_image"] = bool(
            isinstance(image_id, str) and image_usage[image_id] > 1
        )

    products = [ProductImportPreviewProduct.model_validate(record) for record in records]
    valid_count = sum(product.status == "valid" for product in products)
    warning_count = sum(product.status == "warning" for product in products)
    error_count = sum(product.status == "error" for product in products)
    return PreparedProductImport(
        response=ProductImportPreviewResponse(
            preview_session_id=session_id,
            file_name=file_name,
            already_imported=already_imported,
            source_row_count=len(rows),
            product_count=len(products),
            valid_count=valid_count,
            warning_count=warning_count,
            error_count=error_count,
            products=products,
        ),
        images_by_id=image_by_id,
        product_image_ids=product_image_ids,
    )


def build_product_import_preview(
    *,
    rows: list[RawImportRow],
    db: Session,
    preview_directory: Path,
    preview_url_prefix: str = "/import-previews",
    file_name: str,
    session_id: str | None = None,
    already_imported: bool = False,
    write_preview_images: bool = True,
) -> ProductImportPreviewResponse:
    return prepare_product_import_preview(
        rows=rows,
        db=db,
        preview_directory=preview_directory,
        preview_url_prefix=preview_url_prefix,
        file_name=file_name,
        session_id=session_id,
        already_imported=already_imported,
        write_preview_images=write_preview_images,
    ).response
