import type { DefaultOptionType } from "antd/es/cascader";

export type InventoryCategoryOption = DefaultOptionType;
export type CategorySelection = (string | number)[];

export type ProductUnit = "pcs" | "set";

export interface CategoryTreeNode {
  id: number;
  name: string;
  parent_id: number | null;
  sort_order: number;
  code: string;
  children: CategoryTreeNode[];
}

export interface CategoryRead {
  id: number;
  name: string;
  parent_id: number | null;
  sort_order: number;
  code: string;
  created_at: string;
  updated_at: string;
}

export interface CategoryCreatePayload {
  name: string;
  parent_id: number | null;
  sort_order?: number;
}

export interface CategoryUpdatePayload {
  name: string;
}

export interface WarehouseRead {
  id: number;
  name: string;
  sort_order: number;
}

export type WarehouseSelection = "all" | number;
export type ProductStatus = "active" | "inactive" | "all";

export interface ProductPackagingApiRecord {
  id: number;
  packing_qty: number | null;
  carton_count: number;
  sort_order: number;
}

export interface ProductPackagingCreatePayload {
  packing_qty: number | null;
}

export interface ProductPackagingUpdatePayload extends ProductPackagingCreatePayload {
  id?: number;
}

export interface ProductApiRecord {
  id: number;
  category_id: number | null;
  warehouse_id: number | null;
  product_code: string | null;
  is_active: boolean;
  image_path: string | null;
  thumbnail_path: string | null;
  size: string;
  unit: ProductUnit | null;
  price: string | null;
  remark: string | null;
  packagings: ProductPackagingApiRecord[];
  total_carton_count: number;
  created_at: string;
  updated_at: string;
}

export interface ProductListResponse {
  items: ProductApiRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProductCreatePayload {
  category_id: number;
  warehouse_id: number;
  image_path: string | null;
  thumbnail_path: string | null;
  size: string;
  unit: ProductUnit | null;
  price: string | null;
  remark: string | null;
  packagings: ProductPackagingCreatePayload[];
}

export type ProductUpdatePayload =
  Partial<Omit<ProductCreatePayload, "packagings">> & {
    packagings?: ProductPackagingUpdatePayload[];
  };

export interface InventoryPackaging {
  id: number;
  packingQty: number | null;
  cartonCount: number;
  sortOrder: number;
}

export interface InventoryProduct {
  id: number;
  productCode: string | null;
  isActive: boolean;
  categoryId: number | null;
  warehouseId: number | null;
  imagePath: string | null;
  thumbnailPath: string | null;
  size: string;
  unit: ProductUnit | null;
  price: string | null;
  packagings: InventoryPackaging[];
  totalCartonCount: number;
  remark: string;
}

export type StockMovementDirection = "in" | "out";

export interface StockMovementValues {
  packagingId?: number;
  packingQty?: number | null;
  quantity: number;
  note?: string;
  isNewPackaging?: boolean;
}

export type InventoryMovementType = "IN" | "OUT";

export interface InventoryMovementApiRecord {
  id: number;
  product_id: number;
  product_code: string | null;
  product_packaging_id: number | null;
  warehouse_id: number | null;
  movement_type: InventoryMovementType;
  quantity: number;
  before_carton_count: number;
  after_carton_count: number;
  packing_qty_snapshot: number | null;
  unit_snapshot: string | null;
  remark: string | null;
  created_at: string;
}

export interface InventoryMovementListResponse {
  items: InventoryMovementApiRecord[];
  total: number;
  page: number;
  page_size: number;
}

export interface ProductPackagingFormValue {
  id?: number;
  packingQty: number | null;
  cartonCount: number;
}

export interface ProductEditorFormValues {
  imagePath: string | null;
  thumbnailPath: string | null;
  categoryPath: number[];
  warehouseId: number;
  size: string;
  unit: ProductUnit | null;
  price: string | null;
  packagings: ProductPackagingFormValue[];
  remark: string;
}
