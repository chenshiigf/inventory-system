from datetime import datetime
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
    created_at: datetime
    updated_at: datetime


class CategoryTreeNode(BaseModel):
    id: int
    name: str
    parent_id: int | None
    sort_order: int
    children: list[CategoryTreeNode] = Field(default_factory=list)


class WarehouseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    sort_order: int


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
    size: ProductSize = ""
    packing_qty: PositiveInt
    unit: ProductUnit
    price: ProductPrice
    carton_count: NonNegativeInt = 0
    remark: str | None = Field(default=None, max_length=2000)

    @field_validator("image_path")
    @classmethod
    def image_path_is_relative(cls, value: str | None) -> str | None:
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
    size: ProductSize | None = None
    packing_qty: PositiveInt | None = None
    unit: ProductUnit | None = None
    price: ProductPrice | None = None
    carton_count: NonNegativeInt | None = None
    remark: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_patch(self) -> "ProductUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one product field must be provided")

        nullable_fields = {"category_id", "warehouse_id", "image_path", "remark"}
        for field_name in self.model_fields_set - nullable_fields:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")

        return self

    @field_validator("image_path")
    @classmethod
    def image_path_is_relative(cls, value: str | None) -> str | None:
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
    image_path: str | None
    size: str
    packing_qty: int
    unit: ProductUnit
    price: Decimal
    carton_count: int
    remark: str | None
    created_at: datetime
    updated_at: datetime


class ProductListRead(BaseModel):
    items: list[ProductRead]
    total: int
    page: int
    page_size: int
