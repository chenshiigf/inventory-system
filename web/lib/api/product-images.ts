import { apiUpload, getApiBaseUrl } from "@/lib/api/client";

export interface ProductImageUploadResult {
  image_path: string;
  thumbnail_path: string;
  image_url: string;
  thumbnail_url: string;
}

export const MAX_PRODUCT_IMAGE_BYTES = 10 * 1024 * 1024;

export function getProductImageUrl(
  imagePath: string | null | undefined,
): string | null {
  if (!imagePath?.trim()) {
    return null;
  }

  const relativePath = imagePath
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\/+/, "");
  const encodedPath = relativePath
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `${getApiBaseUrl()}/uploads/${encodedPath}`;
}

export function uploadProductImage(file: File): Promise<ProductImageUploadResult> {
  const formData = new FormData();
  formData.append("file", file, file.name);
  return apiUpload<ProductImageUploadResult>("/api/product-images", formData);
}
