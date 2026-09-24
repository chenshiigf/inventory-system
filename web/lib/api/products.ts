import { apiRequest } from "@/lib/api/client";
import type {
  ProductApiRecord,
  ProductBatchResult,
  ProductCreatePayload,
  ProductListResponse,
  ProductStatus,
  ProductUpdatePayload,
  StockStatus,
} from "@/types/inventory";

interface ProductListParams {
  page: number;
  pageSize: number;
  search: string;
  categoryId?: number;
  warehouseId?: number;
  status?: ProductStatus;
  stockStatus?: StockStatus;
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
  if (params.warehouseId !== undefined) {
    query.set("warehouse_id", String(params.warehouseId));
  }
  if (params.status !== undefined) {
    query.set("status", params.status);
  }
  if (params.stockStatus !== undefined) {
    query.set("stock_status", params.stockStatus);
  }
  return apiRequest<ProductListResponse>(`/api/products?${query.toString()}`, {
    method: "GET",
    signal,
  });
}

export function getProduct(
  id: number,
  signal?: AbortSignal,
): Promise<ProductApiRecord> {
  return apiRequest<ProductApiRecord>(`/api/products/${id}`, {
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

export function deactivateProduct(id: number): Promise<ProductApiRecord> {
  return apiRequest<ProductApiRecord>(`/api/products/${id}/deactivate`, {
    method: "POST",
  });
}

export function activateProduct(id: number): Promise<ProductApiRecord> {
  return apiRequest<ProductApiRecord>(`/api/products/${id}/activate`, {
    method: "POST",
  });
}

export function batchUpdateProductCategory(
  productIds: number[],
  categoryId: number,
): Promise<ProductBatchResult> {
  return apiRequest<ProductBatchResult>("/api/products/batch/category", {
    method: "POST",
    body: JSON.stringify({ product_ids: productIds, category_id: categoryId }),
  });
}

export function batchDeactivateProducts(
  productIds: number[],
): Promise<ProductBatchResult> {
  return apiRequest<ProductBatchResult>("/api/products/batch/deactivate", {
    method: "POST",
    body: JSON.stringify({ product_ids: productIds }),
  });
}

export function batchActivateProducts(
  productIds: number[],
): Promise<ProductBatchResult> {
  return apiRequest<ProductBatchResult>("/api/products/batch/activate", {
    method: "POST",
    body: JSON.stringify({ product_ids: productIds }),
  });
}
