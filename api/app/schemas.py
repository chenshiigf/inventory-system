from datetime import date, datetime
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


ProductUnit = Literal["pcs", "set"]
ProductSize = Annotated[str, StringConstraints(max_length=200)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
NonNegativeInt = Annotated[int, Field(ge=0, strict=True)]
ProductPrice = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=12, decimal_places=2),
]
CategoryId = Annotated[int, Field(gt=0, strict=True)]
WarehouseId = Annotated[int, Field(gt=0, strict=True)]
CategoryName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100, strip_whitespace=True),
]
CategorySortOrder = Annotated[int, Field(ge=0, strict=True)]
WarehouseName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100, strip_whitespace=True),
]


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CategoryName
    parent_id: CategoryId | None = None
    sort_order: CategorySortOrder = 0


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: CategoryName


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    parent_id: int | None
    sort_order: int
    code: str
    created_at: datetime
    updated_at: datetime


class CategoryTreeNode(BaseModel):
    id: int
    name: str
    parent_id: int | None
    sort_order: int
    code: str
    children: list[CategoryTreeNode] = Field(default_factory=list)


class WarehouseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sort_order: int


class WarehouseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: WarehouseName


class WarehouseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: WarehouseName


class WarehouseSummaryRead(BaseModel):
    id: int
    name: str
    sort_order: int
    product_count: int
    carton_count: int


class ProductPackagingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    packing_qty: PositiveInt | None = None
    carton_count: NonNegativeInt = 0


class ProductPackagingWrite(ProductPackagingCreate):
    id: PositiveInt | None = None


class ProductPackagingUpdate(BaseModel):
    """Public product-edit payload: packaging data, never stock quantity."""

    model_config = ConfigDict(extra="forbid")

    id: PositiveInt | None = None
    packing_qty: PositiveInt | None = None


class ProductPackagingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    packing_qty: int | None
    carton_count: int
    sort_order: int


def _validate_unique_packaging_quantities(
    packagings: list[ProductPackagingCreate | ProductPackagingWrite],
) -> None:
    quantities = [packaging.packing_qty for packaging in packagings]
    if len(quantities) != len(set(quantities)):
        raise ValueError("packagings cannot contain duplicate packing_qty values")


def validate_image_path(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip().replace("\\", "/")
    if not normalized:
        return None
    if normalized.lower().startswith("data:"):
        raise ValueError("image_path must be a relative path or key, not image data")

    path = PurePosixPath(normalized)
    if path.is_absolute() or ":" in normalized or "\x00" in normalized:
        raise ValueError("image_path must be a relative path or key")
    if any(part == ".." for part in path.parts):
        raise ValueError("image_path cannot contain parent directory segments")

    return normalized


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: CategoryId | None = None
    warehouse_id: WarehouseId | None = None
    image_path: str | None = Field(default=None, max_length=500)
    thumbnail_path: str | None = Field(default=None, max_length=500)
    size: ProductSize = ""
    packagings: list[ProductPackagingCreate] = Field(min_length=1)
    unit: ProductUnit | None = None
    price: ProductPrice | None = None
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_unique_packagings(self) -> "ProductCreate":
        _validate_unique_packaging_quantities(self.packagings)
        return self

    @field_validator("image_path", "thumbnail_path")
    @classmethod
    def image_paths_are_relative(cls, value: str | None) -> str | None:
        return validate_image_path(value)

    @field_validator("size")
    @classmethod
    def trim_size(cls, value: str) -> str:
        return value.strip()

    @field_validator("remark")
    @classmethod
    def trim_remark(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: CategoryId | None = None
    warehouse_id: WarehouseId | None = None
    image_path: str | None = Field(default=None, max_length=500)
    thumbnail_path: str | None = Field(default=None, max_length=500)
    size: ProductSize | None = None
    packagings: list[ProductPackagingUpdate] | None = Field(
        default=None, min_length=1
    )
    unit: ProductUnit | None = None
    price: ProductPrice | None = None
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_patch(self) -> "ProductUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one product field must be provided")

        nullable_fields = {
            "category_id",
            "warehouse_id",
            "image_path",
            "thumbnail_path",
            "unit",
            "price",
            "remark",
        }
        for field_name in self.model_fields_set - nullable_fields:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")

        if self.packagings is not None:
            _validate_unique_packaging_quantities(self.packagings)

        return self

    @field_validator("image_path", "thumbnail_path")
    @classmethod
    def image_paths_are_relative(cls, value: str | None) -> str | None:
        return validate_image_path(value)

    @field_validator("size")
    @classmethod
    def trim_size(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("remark")
    @classmethod
    def trim_remark(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int | None
    warehouse_id: int | None
    product_code: str | None
    is_active: bool
    image_path: str | None
    thumbnail_path: str | None
    size: str
    unit: ProductUnit | None
    price: Decimal | None
    remark: str | None
    packagings: list[ProductPackagingRead]
    total_carton_count: int
    created_at: datetime
    updated_at: datetime


class ProductDetailRead(ProductRead):
    category_name: str | None = None
    warehouse_name: str | None = None


class ProductListRead(BaseModel):
    items: list[ProductRead]
    total: int
    page: int
    page_size: int


class ProductBatchIdsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_ids: list[PositiveInt] = Field(min_length=1, max_length=100)


class ProductBatchCategoryRequest(ProductBatchIdsRequest):
    category_id: CategoryId


class ProductQuoteExportRequest(ProductBatchIdsRequest):
    customer_name: str | None = Field(default=None, max_length=200)
    quote_date: date | None = None

    @field_validator("customer_name")
    @classmethod
    def trim_customer_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ProductBatchResult(BaseModel):
    updated_count: int = Field(ge=0)


class ProductImageUploadRead(BaseModel):
    image_path: str
    thumbnail_path: str
    image_url: str
    thumbnail_url: str


MovementType = Literal["IN", "OUT", "ADJUST"]


class StockMovementCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_packaging_id: PositiveInt | None = None
    packing_qty: PositiveInt | None = None
    quantity: PositiveInt
    remark: str | None = Field(default=None, max_length=2000)

    @field_validator("remark")
    @classmethod
    def empty_remark_is_null(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        # Keep internal spaces and line breaks exactly as entered.
        return value


class StockAdjustmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_packaging_id: PositiveInt
    actual_carton_count: NonNegativeInt
    remark: str = Field(max_length=2000)

    @field_validator("remark")
    @classmethod
    def require_adjustment_remark(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("调整原因不能为空")
        return trimmed


class InventoryMovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    product_code: str | None = None
    image_path: str | None = None
    thumbnail_path: str | None = None
    product_packaging_id: int | None
    warehouse_id: int | None
    warehouse_name: str | None = None
    movement_type: MovementType
    quantity: int
    before_carton_count: int
    after_carton_count: int
    packing_qty_snapshot: int | None
    unit_snapshot: str | None
    remark: str | None
    created_at: datetime


class InventoryMovementListRead(BaseModel):
    items: list[InventoryMovementRead]
    total: int
    page: int
    page_size: int


class DashboardCategoryDistributionRead(BaseModel):
    category_id: int | None
    category_name: str
    product_count: int


class DashboardWarehouseDistributionRead(BaseModel):
    warehouse_id: int
    warehouse_name: str
    carton_count: int


class DashboardSummaryRead(BaseModel):
    active_product_count: int
    total_carton_count: int
    zero_stock_product_count: int
    category_distribution: list[DashboardCategoryDistributionRead]
    warehouse_distribution: list[DashboardWarehouseDistributionRead]
