import { apiRequest } from "@/lib/api/client";
import type { WarehouseRead } from "@/types/inventory";

export function listWarehouses(signal?: AbortSignal): Promise<WarehouseRead[]> {
  return apiRequest<WarehouseRead[]>("/api/warehouses", {
    method: "GET",
    signal,
  });
}
