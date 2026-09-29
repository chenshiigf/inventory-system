import { apiRequest } from "@/lib/api/client";
import type {
  InventoryMovementListResponse,
  InventoryMovementApiRecord,
  InventoryMovementType,
  StockAdjustmentValues,
  StockMovementValues,
} from "@/types/inventory";

export interface BatchStockOutPackagingPreview {
  id: number;
  packing_qty: number | null;
  carton_count: number;
  sort_order: number;
}

export interface BatchStockOutProductPreview {
  product_id: number;
  product_code: string | null;
  is_active: boolean;
  image_path: string | null;
  thumbnail_path: string | null;
  size: string;
  unit: "pcs" | "set" | null;
  warehouse_id: number | null;
  warehouse_name: string | null;
  packagings: BatchStockOutPackagingPreview[];
}

export interface BatchStockOutCommitLine {
  product_id: number;
  product_packaging_id: number;
  quantity: number;
}

export interface BatchStockOutCommitResult {
  movement_count: number;
  total_cartons: number;
}

interface StockMovementPayload {
  product_packaging_id?: number;
  packing_qty?: number;
  quantity: number;
  remark?: string;
}

export function createStockMovement(
  productId: number,
  direction: "in" | "out",
  values: StockMovementValues,
): Promise<InventoryMovementApiRecord> {
  const payload: StockMovementPayload = {
    quantity: values.quantity,
  };
  if (values.packagingId !== undefined) {
    payload.product_packaging_id = values.packagingId;
  }
  if (values.isNewPackaging && values.packingQty !== undefined && values.packingQty !== null) {
    payload.packing_qty = values.packingQty;
  }
  if (values.note !== undefined) {
    payload.remark = values.note;
  }
  return apiRequest<InventoryMovementApiRecord>(
    `/api/products/${productId}/stock/${direction}`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
  );
}

export function createStockAdjustment(
  productId: number,
  values: StockAdjustmentValues,
): Promise<InventoryMovementApiRecord> {
  return apiRequest<InventoryMovementApiRecord>(
    `/api/products/${productId}/stock/adjust`,
    {
      method: "POST",
      body: JSON.stringify({
        product_packaging_id: values.packagingId,
        actual_carton_count: values.actualCartonCount,
        remark: values.remark,
      }),
    },
  );
}

export function previewBatchStockOut(
  productIds: number[],
  signal?: AbortSignal,
): Promise<{ products: BatchStockOutProductPreview[] }> {
  return apiRequest<{ products: BatchStockOutProductPreview[] }>(
    "/api/inventory/batch-outbound/preview",
    {
      method: "POST",
      body: JSON.stringify({ product_ids: productIds }),
      signal,
    },
  );
}

export function commitBatchStockOut(
  items: BatchStockOutCommitLine[],
  remark: string,
): Promise<BatchStockOutCommitResult> {
  return apiRequest<BatchStockOutCommitResult>(
    "/api/inventory/batch-outbound/commit",
    {
      method: "POST",
      body: JSON.stringify({ items, remark: remark.trim() || null }),
    },
  );
}

export function listInventoryMovements(
  params: {
    productId?: number;
    productPackagingId?: number;
    movementType?: InventoryMovementType;
    search?: string;
    warehouseId?: number;
    startDate?: string;
    endDate?: string;
    page: number;
    pageSize: number;
  },
  signal?: AbortSignal,
): Promise<InventoryMovementListResponse> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  if (params.productId !== undefined) {
    query.set("product_id", String(params.productId));
  }
  if (params.productPackagingId !== undefined) {
    query.set("product_packaging_id", String(params.productPackagingId));
  }
  if (params.movementType !== undefined) {
    query.set("movement_type", params.movementType);
  }
  if (params.search?.trim()) {
    query.set("search", params.search.trim());
  }
  if (params.warehouseId !== undefined) {
    query.set("warehouse_id", String(params.warehouseId));
  }
  if (params.startDate) {
    query.set("start_date", params.startDate);
  }
  if (params.endDate) {
    query.set("end_date", params.endDate);
  }
  return apiRequest<InventoryMovementListResponse>(
    `/api/inventory-movements?${query.toString()}`,
    {
      method: "GET",
      signal,
    },
  );
}
