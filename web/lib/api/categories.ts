import { apiRequest } from "@/lib/api/client";
import type {
  CategoryCreatePayload,
  CategoryRead,
  CategoryTreeNode,
  CategoryUpdatePayload,
} from "@/types/inventory";

export function listCategories(
  signal?: AbortSignal,
  warehouseId?: number,
): Promise<CategoryTreeNode[]> {
  const query = new URLSearchParams();
  if (warehouseId !== undefined) {
    query.set("warehouse_id", String(warehouseId));
  }
  const search = query.toString();
  return apiRequest<CategoryTreeNode[]>(`/api/categories${search ? `?${search}` : ""}`, {
    method: "GET",
    signal,
  });
}

export function createCategory(
  payload: CategoryCreatePayload,
): Promise<CategoryRead> {
  return apiRequest<CategoryRead>("/api/categories", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateCategory(
  id: number,
  payload: CategoryUpdatePayload,
): Promise<CategoryRead> {
  return apiRequest<CategoryRead>(`/api/categories/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}
