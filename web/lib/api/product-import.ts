import { apiUpload, getApiBaseUrl } from "@/lib/api/client";

export type ProductImportRowStatus = "valid" | "warning" | "error";

export interface ProductImportPreviewPackaging {
  excel_row: number;
  packing_qty: number | null;
  carton_count: number | null;
  source_code: string;
}

export interface ProductImportPreviewProduct {
  preview_id: string;
  product_group: string | null;
  excel_rows: number[];
  warehouse: string;
  category_level_1: string;
  category_level_2: string;
  image_preview_url: string | null;
  shared_image: boolean;
  size: string;
  unit: string;
  price: string | null;
  remark: string;
  source_codes: string[];
  packagings: ProductImportPreviewPackaging[];
  total_carton_count: number;
  status: ProductImportRowStatus;
  messages: string[];
}

export interface ProductImportPreviewResponse {
  file_name: string;
  source_row_count: number;
  product_count: number;
  valid_count: number;
  warning_count: number;
  error_count: number;
  products: ProductImportPreviewProduct[];
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
