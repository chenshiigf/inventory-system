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

export interface ProductApiRecord {
  id: number;
  category_id: number | null;
  warehouse_id: number | null;
  product_code: string | null;
  image_path: string | null;
  thumbnail_path: string | null;
  size: string;
  packing_qty: number;
  unit: ProductUnit;
  price: string;
  carton_count: number;
  remark: string | null;
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
  packing_qty: number;
  unit: ProductUnit;
  price: string;
  carton_count: number;
  remark: string | null;
}

export type ProductUpdatePayload = Partial<ProductCreatePayload>;

export interface InventoryProduct {
  id: number;
  productCode: string | null;
  categoryId: number | null;
  warehouseId: number | null;
  imagePath: string | null;
  thumbnailPath: string | null;
  size: string;
  packingQty: number;
  unit: ProductUnit;
  price: string;
  cartonCount: number;
  remark: string;
}

export type StockMovementDirection = "in" | "out";

export interface StockMovementValues {
  quantity: number;
  note?: string;
}

export interface ProductEditorFormValues {
  imagePath: string | null;
  thumbnailPath: string | null;
  categoryPath: number[];
  warehouseId: number;
  size: string;
  packingQty: number;
  unit: ProductUnit;
  price: string;
  cartonCount: number;
  remark: string;
}
