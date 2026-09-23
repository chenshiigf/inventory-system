import { apiRequest } from "@/lib/api/client";
import type { WarehouseRead, WarehouseSummaryRead } from "@/types/inventory";

export function listWarehouses(signal?: AbortSignal): Promise<WarehouseRead[]> {
  return apiRequest<WarehouseRead[]>("/api/warehouses", {
    method: "GET",
    signal,
  });
}

export function listWarehouseSummaries(
  signal?: AbortSignal,
): Promise<WarehouseSummaryRead[]> {
  return apiRequest<WarehouseSummaryRead[]>("/api/warehouses/summary", {
    method: "GET",
    signal,
  });
}

export function createWarehouse(name: string): Promise<WarehouseRead> {
  return apiRequest<WarehouseRead>("/api/warehouses", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export function updateWarehouse(
  warehouseId: number,
  name: string,
): Promise<WarehouseRead> {
  return apiRequest<WarehouseRead>(`/api/warehouses/${warehouseId}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });
}
