import { apiRequest, apiUpload } from "@/lib/api/client";
import type { ProductApiRecord } from "@/types/inventory";

export type CropRegion = [number, number, number, number];
export interface VisualResult {
  query_id: string;
  model: string;
  version: string;
  elapsed_ms: number;
  usage: Record<string, unknown>;
  call_summary: { api_calls: number; estimated_cost_cny?: number | null };
  message: string;
  candidates: { product: ProductApiRecord; similarity: number }[];
}
export interface VisualStatus {
  schema_ready: boolean;
  api_key_configured: boolean;
  indexed: number;
  unindexed: number;
  failed: number;
  skipped: number;
  model: string;
  dimension: number;
  version: string;
  jobs: { id: string; status: string; processed: number; succeeded: number; failed: number }[];
}

export function getVisualStatus() {
  return apiRequest<VisualStatus>("/api/visual-search/index/status", { credentials: "include" });
}
export function searchVisual(file: File, crop?: CropRegion) {
  const body = new FormData();
  body.append("file", file);
  if (crop) body.append("crop", JSON.stringify(crop));
  return apiUpload<VisualResult>("/api/visual-search/search", body, { credentials: "include", signal: AbortSignal.timeout(240_000) });
}
export function saveVisualFeedback(query_id: string, productId: number | null) {
  return apiRequest<{ saved: boolean }>("/api/visual-search/feedback", {
    method: "POST", credentials: "include",
    body: JSON.stringify({ query_id, selected_product_id: productId, no_match: productId === null }),
  });
}
export function rebuildVisual(dry_run: boolean, max_items: number) {
  return apiRequest<{ job_id?: string; would_process: number; pending_total: number; unchanged: number; would_clean: number }>("/api/visual-search/index/rebuild", {
    method: "POST", credentials: "include",
    body: JSON.stringify({ dry_run, max_items, confirm_paid: !dry_run }),
  });
}
