from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class ImportColumn:
    key: str
    header: str


# The template generator and Excel reader must use this same ordered definition.
IMPORT_COLUMNS: Final[tuple[ImportColumn, ...]] = (
    ImportColumn("warehouse", "仓库"),
    ImportColumn("category_level_1", "一级分类"),
    ImportColumn("category_level_2", "二级分类"),
    ImportColumn("image", "产品图片"),
    ImportColumn("size", "产品尺寸"),
    ImportColumn("packing_qty", "装箱数"),
    ImportColumn("unit", "单位"),
    ImportColumn("price", "单价"),
    ImportColumn("carton_count", "当前箱数"),
    ImportColumn("remark", "备注"),
    ImportColumn("source_code", "原系统编号"),
)
IMPORT_HEADERS: Final[tuple[str, ...]] = tuple(
    column.header for column in IMPORT_COLUMNS
)

ProductImportRowStatus = Literal["valid", "warning", "error"]


class ProductImportPreviewRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    excel_row: int = Field(ge=2)
    warehouse: str
    category_level_1: str
    category_level_2: str
    image_preview_url: str | None
    has_image: bool
    size: str
    packing_qty: int | None
    unit: str
    price: str | None
    carton_count: int | None
    remark: str
    source_code: str
    status: ProductImportRowStatus
    messages: list[str]


class ProductImportPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_name: str
    total_rows: int
    valid_count: int
    warning_count: int
    error_count: int
    rows: list[ProductImportPreviewRow]
