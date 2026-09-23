import { apiRequest } from "@/lib/api/client";
import type {
  InventoryMovementListResponse,
  InventoryMovementApiRecord,
  StockMovementValues,
} from "@/types/inventory";

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

export function listInventoryMovements(
  params: {
    productId: number;
    page: number;
    pageSize: number;
  },
  signal?: AbortSignal,
): Promise<InventoryMovementListResponse> {
  const query = new URLSearchParams({
    product_id: String(params.productId),
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  return apiRequest<InventoryMovementListResponse>(
    `/api/inventory-movements?${query.toString()}`,
    {
      method: "GET",
      signal,
    },
  );
}
