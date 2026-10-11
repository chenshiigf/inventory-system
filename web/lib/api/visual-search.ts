import { apiRequest, apiUpload } from "@/lib/api/client";
import type { ProductApiRecord } from "@/types/inventory";

export type VisualSource = "development" | "production-catalog";
export type VisualProduct = Pick<ProductApiRecord, "id" | "product_code" | "image_path" | "thumbnail_path" | "category_id" | "warehouse_id" | "category_name" | "warehouse_name" | "size" | "unit" | "is_active"> & {
  source_id?: string;
  product_key?: string;
  source_present?: boolean;
  image_sha256?: string | null;
  packagings: { id: number; packing_qty: number | null; sort_order: number }[];
};

export type CropRegion = [number, number, number, number];
export interface VisualResult {
  query_id: string;
  source: VisualSource;
  source_id: string;
  model: string;
  version: string;
  elapsed_ms: number;
  usage: Record<string, unknown>;
  call_summary: { api_calls: number; estimated_cost_cny?: number | null };
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
  return apiRequest<VisualStatus>(`/api/visual-search/index/status?source=${source}`, { credentials: "include" });
}
export function searchVisual(file: File, crop?: CropRegion, source: VisualSource = "development") {
  const body = new FormData();
  body.append("file", file);
  if (crop) body.append("crop", JSON.stringify(crop));
  return apiUpload<VisualResult>(`/api/visual-search/search?source=${source}`, body, { credentials: "include", signal: AbortSignal.timeout(240_000) });
}
export function saveVisualFeedback(query_id: string, productId: number | null, source: VisualSource = "development") {
  return apiRequest<{ saved: boolean }>(`/api/visual-search/feedback?source=${source}`, {
    method: "POST", credentials: "include",
    body: JSON.stringify({ query_id, selected_product_id: productId, no_match: productId === null }),
  });
}
export function rebuildVisual(dry_run: boolean, max_items: number, source: VisualSource = "development") {
  return apiRequest<{ job_id?: string; would_process: number; pending_total: number; unchanged: number; would_clean: number; unique_new_images: number; max_http_attempts: number }>(`/api/visual-search/index/rebuild?source=${source}`, {
    method: "POST", credentials: "include",
    body: JSON.stringify({ dry_run, max_items, confirm_paid: !dry_run, confirm_large_batch: !dry_run && max_items > 10 }),
  });
}

export function getVisualProduct(id: number, source: VisualSource) {
  return apiRequest<{ source: VisualSource; source_id: string; product: VisualProduct }>(`/api/visual-search/catalog/products/${id}?source=${source}`, { credentials: "include" });
}
