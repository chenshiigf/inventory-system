import { apiRequest, apiUpload } from "@/lib/api/client";
import type { ProductApiRecord } from "@/types/inventory";
import { getVisualApiBaseUrl } from "@/lib/api/visual-endpoint";

export type VisualSource = "development" | "production-catalog";
export type VisualProduct = Pick<ProductApiRecord, "id" | "product_code" | "image_path" | "thumbnail_path" | "category_id" | "warehouse_id" | "category_name" | "warehouse_name" | "size" | "unit" | "is_active"> & {
  original_product_code?: ProductApiRecord["original_product_code"];
  source_id?: string;
  product_key?: string;
  source_present?: boolean;
  image_sha256?: string | null;
  product_name?: string | null;
  synced_at?: string;
  source_updated_at?: string | null;
  packagings: { id: number; packing_qty: number | null; sort_order: number }[];
};

export type CropRegion = [number, number, number, number];
export interface VisualUsage {
  input_tokens?: number;
  image_tokens?: number;
  total_tokens?: number;
  [key: string]: unknown;
}
export interface VisualCallSummary {
  api_calls: number;
  failed_calls: number;
  input_tokens: number;
  image_tokens: number;
  total_tokens: number;
  estimated_cost_cny: number | null;
  cost_complete: boolean;
}
export interface VisualTimings {
  image_preprocessing: number;
  sqlite_vectors: number;
  catalog_bulk_read: number;
  gallery_hash_and_vector_validation: number;
  query_embedding?: number;
  catalog_revalidation?: number;
  numpy_similarity?: number;
  query_persistence: number;
  response_details: number;
  total: number;
}
export interface VisualProductResponse {
  source: VisualSource;
  source_id: string;
  index_namespace?: string;
  catalog_fingerprint?: string;
  product: VisualProduct;
}
export interface VisualResult {
  query_id: string;
  source: VisualSource;
  source_id: string;
  index_namespace?: string;
  catalog_fingerprint?: string;
  model: string;
  version: string;
  elapsed_ms: number;
  usage: VisualUsage;
  call_summary: VisualCallSummary;
  crop: CropRegion | null;
  timings_ms: VisualTimings;
  message: string;
  candidates: { product: VisualProduct; similarity: number }[];
}
export interface VisualStatus {
  source: VisualSource;
  source_id: string;
  schema_ready: boolean;
  api_key_configured: boolean;
  indexed: number;
  unindexed: number;
  failed: number;
  skipped: number;
  model: string;
  dimension: number;
  version: string;
  indexed_unique_images: number;
  jobs: { id: string; status: string; processed: number; succeeded: number; failed: number }[];
}

export function getVisualStatus(source: VisualSource = "development") {
  return apiRequest<VisualStatus>(`/api/visual-search/index/status?source=${source}`, { credentials: "include" }, getVisualApiBaseUrl());
}
export function searchVisual(file: File, crop?: CropRegion, source: VisualSource = "development", signal?: AbortSignal) {
  const body = new FormData();
  body.append("file", file);
  body.append("top_k", "5");
  if (crop) body.append("crop", JSON.stringify(crop));
  const timeout = AbortSignal.timeout(240_000);
  return apiUpload<VisualResult>(`/api/visual-search/search?source=${source}`, body, { credentials: "include", signal: signal ? AbortSignal.any([signal, timeout]) : timeout }, getVisualApiBaseUrl());
}
export function saveVisualFeedback(query_id: string, productId: number | null, source: VisualSource = "development") {
  return apiRequest<{ saved: boolean; query_id: string }>(`/api/visual-search/feedback?source=${source}`, {
    method: "POST", credentials: "include",
    body: JSON.stringify({ query_id, selected_product_id: productId, no_match: productId === null }),
  }, getVisualApiBaseUrl());
}
export function rebuildVisual(dry_run: boolean, max_items: number, source: VisualSource = "development") {
  return apiRequest<{ job_id?: string; would_process: number; pending_total: number; unchanged: number; would_clean: number; unique_new_images: number; max_http_attempts: number }>(`/api/visual-search/index/rebuild?source=${source}`, {
    method: "POST", credentials: "include",
    body: JSON.stringify({ dry_run, max_items, confirm_paid: !dry_run, confirm_large_batch: !dry_run && max_items > 10 }),
  }, getVisualApiBaseUrl());
}

export function getVisualProduct(id: number, source: VisualSource, signal?: AbortSignal) {
  return apiRequest<VisualProductResponse>(`/api/visual-search/catalog/products/${id}?source=${source}`, { credentials: "include", signal }, getVisualApiBaseUrl());
}
