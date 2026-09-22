import { apiUpload, getApiBaseUrl } from "@/lib/api/client";

export type ProductImportRowStatus = "valid" | "warning" | "error";

export interface ProductImportPreviewRow {
  excel_row: number;
  warehouse: string;
  category_level_1: string;
  category_level_2: string;
  image_preview_url: string | null;
  has_image: boolean;
  size: string;
  packing_qty: number | null;
  unit: string;
  price: string | null;
  carton_count: number | null;
  remark: string;
  source_code: string;
  status: ProductImportRowStatus;
  messages: string[];
}

export interface ProductImportPreviewResponse {
  file_name: string;
  total_rows: number;
  valid_count: number;
  warning_count: number;
  error_count: number;
  rows: ProductImportPreviewRow[];
}

export function getProductImportTemplateUrl(): string {
  return `${getApiBaseUrl()}/api/product-import/template`;
}

export function getImportPreviewImageUrl(
  previewPath: string | null | undefined,
): string | null {
  if (!previewPath?.trim()) {
    return null;
  }
  const trimmedPath = previewPath.trim();
  if (/^https?:\/\//i.test(trimmedPath)) {
    return trimmedPath;
  }
  return `${getApiBaseUrl()}${trimmedPath.startsWith("/") ? trimmedPath : `/${trimmedPath}`}`;
}

export function previewProductImport(
  file: File,
): Promise<ProductImportPreviewResponse> {
  const formData = new FormData();
  formData.append("file", file, file.name);
  return apiUpload<ProductImportPreviewResponse>(
    "/api/product-import/preview",
    formData,
  );
}
