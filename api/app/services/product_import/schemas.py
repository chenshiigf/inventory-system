from dataclasses import dataclass
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class ImportColumn:
    key: str
    header: str


# Header matching is intentionally exact after trimming. Keeping aliases in one
# place makes it impossible for the reader and template to silently disagree.
FIELD_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "product_group": ("商品组",),
    "warehouse": ("仓库",),
    "category_level_1": ("一级分类",),
    "category_level_2": ("二级分类",),
    "image": ("产品图片",),
    "size": ("产品尺寸",),
    "packing_qty": ("装箱数",),
    "unit": ("单位",),
    "price": ("单价",),
    "carton_count": ("结余箱数", "当前箱数"),
    "remark": ("备注",),
    "source_code": ("系统编号", "原系统编号"),
    "secondary_stock": ("义库",),
}

# The downloaded template uses the least ambiguous stock header, while uploads
# accept both stock aliases above.
IMPORT_COLUMNS: Final[tuple[ImportColumn, ...]] = (
    ImportColumn("product_group", "商品组"),
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
)
IMPORT_HEADERS: Final[tuple[str, ...]] = tuple(
    column.header for column in IMPORT_COLUMNS
)

# These headers describe the import contract. Values may be blank where the
# Preview rules allow it, but the field name must be present so the file cannot
# be interpreted by column position or guesswork.
REQUIRED_IMPORT_FIELDS: Final[tuple[str, ...]] = (
    "warehouse",
    "category_level_1",
    "category_level_2",
    "image",
    "size",
    "packing_qty",
    "unit",
    "price",
    "carton_count",
    "remark",
)

ProductImportRowStatus = Literal["valid", "warning", "error"]


class ProductImportPreviewPackaging(BaseModel):
    model_config = ConfigDict(extra="forbid")

    excel_row: int = Field(ge=2)
    packing_qty: int | None
    carton_count: int | None
    source_code: str


class ProductImportPreviewProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str
    product_group: str | None
    excel_rows: list[int] = Field(min_length=1)
    warehouse: str
    category_level_1: str
    category_level_2: str
    image_preview_url: str | None
    shared_image: bool
    size: str
    unit: str
    price: str | None
    remark: str
    source_codes: list[str]
    packagings: list[ProductImportPreviewPackaging] = Field(min_length=1)
    total_carton_count: int
    status: ProductImportRowStatus
    messages: list[str]


class ProductImportPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_session_id: str
    file_name: str
    already_imported: bool
    source_row_count: int
    product_count: int
    valid_count: int
    warning_count: int
    error_count: int
    products: list[ProductImportPreviewProduct]


class ProductImportCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_session_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class ProductImportCreatedProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: int
    product_code: str
    excel_rows: list[int]
    packaging_count: int


class ProductImportCommitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: int
    file_name: str
    source_row_count: int
    product_count: int
    packaging_count: int
    created_products: list[ProductImportCreatedProduct]
