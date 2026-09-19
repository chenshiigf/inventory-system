import { apiRequest } from "@/lib/api/client";
import type {
  CategoryCreatePayload,
  CategoryRead,
  CategoryTreeNode,
  CategoryUpdatePayload,
} from "@/types/inventory";

export function listCategories(signal?: AbortSignal): Promise<CategoryTreeNode[]> {
  return apiRequest<CategoryTreeNode[]>("/api/categories", {
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
