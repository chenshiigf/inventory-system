import { getVisualApiBaseUrl } from "@/lib/api/visual-endpoint";
import type { VisualProduct, VisualProductResponse, VisualResult } from "@/lib/api/visual-search";

export const AI_SEARCH_SOURCE = "production-catalog" as const;
export const AI_SEARCH_SOURCE_ID = "aliyun-inventory-production";

export interface AISearchProduct {
  productId: number;
  productKey: string;
  productCode: string;
  originalProductCode?: string | null;
  productName: string;
  image: string | null;
  size: string;
  packagings: { id: number; packingQty: number | null; unit: string }[];
  warehouse: string;
  imageSyncedAt: string | null;
  active: boolean;
}
export interface AISearchCandidate { product: AISearchProduct; score: number; }
export class AISearchSourceError extends Error {
  constructor() { super("商品数据来源或响应异常，请联系管理员核对生产图库。"); }
}
function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
function nullableText(value: unknown): boolean { return value === null || typeof value === "string"; }
function positiveId(value: unknown): value is number { return typeof value === "number" && Number.isSafeInteger(value) && value > 0; }
function finite(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function count(value: unknown): value is number { return finite(value) && Number.isSafeInteger(value) && value >= 0; }
function validateContext(value: unknown) {
  if (!record(value) || value.source !== AI_SEARCH_SOURCE || value.source_id !== AI_SEARCH_SOURCE_ID
    || typeof value.index_namespace !== "string" || !/^production-[a-f0-9]{64}$/.test(value.index_namespace)
    || typeof value.catalog_fingerprint !== "string" || !/^[a-f0-9]{64}$/.test(value.catalog_fingerprint)) throw new AISearchSourceError();
}
function validateProduct(value: unknown): asserts value is VisualProduct {
  if (!record(value) || !positiveId(value.id) || value.source_id !== AI_SEARCH_SOURCE_ID
    || value.product_key !== `${AI_SEARCH_SOURCE_ID}:${value.id}`
    || !nullableText(value.product_code) || !nullableText(value.image_path)
    || typeof value.size !== "string" || !nullableText(value.unit)
    || !nullableText(value.category_name) || !nullableText(value.warehouse_name)
    || typeof value.is_active !== "boolean" || typeof value.source_present !== "boolean"
    || typeof value.synced_at !== "string" || !Number.isFinite(Date.parse(value.synced_at))
    || (value.product_name !== undefined && !nullableText(value.product_name))
    || (value.original_product_code !== undefined && !nullableText(value.original_product_code))
    || !Array.isArray(value.packagings)) throw new AISearchSourceError();
  if (value.image_path !== null && value.image_path !== `/api/visual-search/catalog/products/${value.id}/image?source=${AI_SEARCH_SOURCE}`) throw new AISearchSourceError();
  if (value.image_path !== null && (typeof value.image_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(value.image_sha256))) throw new AISearchSourceError();
  const ids = new Set<number>();
  for (const p of value.packagings) {
    if (!record(p) || !positiveId(p.id) || (p.packing_qty !== null && !positiveId(p.packing_qty))
      || !finite(p.sort_order) || !Number.isInteger(p.sort_order) || p.sort_order < 0 || ids.has(p.id)) throw new AISearchSourceError();
    ids.add(p.id);
  }
}
export function validateProductionResult(value: unknown): asserts value is VisualResult {
  validateContext(value);
  if (!record(value) || typeof value.query_id !== "string" || !/^[a-f0-9]{32}$/.test(value.query_id)
    || value.model !== "qwen3-vl-embedding" || typeof value.version !== "string"
    || !finite(value.elapsed_ms) || value.elapsed_ms < 0 || typeof value.message !== "string"
    || !record(value.usage) || !record(value.call_summary) || !record(value.timings_ms)
    || !Array.isArray(value.candidates) || value.candidates.length > 5) throw new AISearchSourceError();
  const summary = value.call_summary;
  const usage = value.usage;
  const timings = value.timings_ms;
  if (!["api_calls", "failed_calls", "input_tokens", "image_tokens", "total_tokens"].every((k) => count(summary[k]))
    || typeof summary.cost_complete !== "boolean"
    || (summary.estimated_cost_cny !== null && (!finite(summary.estimated_cost_cny) || summary.estimated_cost_cny < 0))) throw new AISearchSourceError();
  if (value.candidates.length && (!["input_tokens", "image_tokens", "total_tokens"].every((k) => count(usage[k]))
    || (usage.total_tokens as number) !== (usage.input_tokens as number) + (usage.image_tokens as number))) throw new AISearchSourceError();
  const requiredTimings = ["image_preprocessing", "sqlite_vectors", "catalog_bulk_read", "gallery_hash_and_vector_validation", "query_persistence", "response_details", "total"];
  if (!requiredTimings.every((k) => finite(timings[k]) && (timings[k] as number) >= 0)) throw new AISearchSourceError();
  if (value.crop !== null && (!Array.isArray(value.crop) || value.crop.length !== 4 || !value.crop.every(finite))) throw new AISearchSourceError();
  const ids = new Set<number>();
  let previousScore = 1;
  for (const candidate of value.candidates) {
    if (!record(candidate) || !finite(candidate.similarity) || candidate.similarity < -1 || candidate.similarity > previousScore) throw new AISearchSourceError();
    validateProduct(candidate.product);
    if (ids.has(candidate.product.id) || !candidate.product.is_active || candidate.product.source_present !== true) throw new AISearchSourceError();
    ids.add(candidate.product.id);
    previousScore = candidate.similarity;
  }
}
export function validateProductionDetail(value: unknown, expectedId: number, search: VisualResult): asserts value is VisualProductResponse {
  validateContext(value);
  if (!record(value) || value.index_namespace !== search.index_namespace || value.catalog_fingerprint !== search.catalog_fingerprint) throw new AISearchSourceError();
  validateProduct(value.product);
  if (value.product.id !== expectedId) throw new AISearchSourceError();
}
export function toAISearchProduct(product: VisualProduct): AISearchProduct {
  validateProduct(product);
  const unit = product.unit === "pcs" ? "件" : product.unit === "set" ? "套" : product.unit || "单位未填写";
  return {
    productId: product.id,
    productKey: product.product_key!,
    productCode: product.product_code?.trim() || "货号未填写",
    originalProductCode: product.original_product_code?.trim() || null,
    productName: product.product_name?.trim() || product.product_code?.trim() || "商品资料",
    image: product.image_path ? `${getVisualApiBaseUrl()}${product.image_path}` : null,
    size: product.size || "尺寸未填写",
    packagings: product.packagings.map((p) => ({ id: p.id, packingQty: p.packing_qty, unit })),
    warehouse: product.warehouse_name || "所属仓库未填写",
    imageSyncedAt: product.synced_at || null,
    active: product.is_active && product.source_present === true,
  };
}
export function confirmedProductCode(result: VisualResult | null, confirmedId: number | null): string | null {
  if (!result || confirmedId === null) return null;
  return result.candidates.find((c) => c.product.id === confirmedId)?.product.product_code?.trim() || null;
}
export function formatAISearchPackaging(product: AISearchProduct): string {
  return product.packagings.map((p) => p.packingQty === null ? "装箱数未填写" : `${p.packingQty}${p.unit}/箱`).join("、") || "包装规格未填写";
}
