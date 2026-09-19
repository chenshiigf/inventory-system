import { apiRequest } from "@/lib/api/client";
import type {
  ProductApiRecord,
  ProductCreatePayload,
  ProductListResponse,
  ProductUpdatePayload,
} from "@/types/inventory";

interface ProductListParams {
  page: number;
  pageSize: number;
  search: string;
  categoryId?: number;
}

export function listProducts(
  params: ProductListParams,
  signal?: AbortSignal,
): Promise<ProductListResponse> {
  const query = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  if (params.search.trim()) {
    query.set("search", params.search.trim());
  }
  if (params.categoryId !== undefined) {
    query.set("category_id", String(params.categoryId));
  }
  return apiRequest<ProductListResponse>(`/api/products?${query.toString()}`, {
    method: "GET",
    signal,
  });
}

export function createProduct(
  payload: ProductCreatePayload,
): Promise<ProductApiRecord> {
  return apiRequest<ProductApiRecord>("/api/products", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateProduct(
  id: number,
  payload: ProductUpdatePayload,
): Promise<ProductApiRecord> {
  return apiRequest<ProductApiRecord>(`/api/products/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}
