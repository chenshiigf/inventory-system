import type { CropRegion } from "@/lib/api/visual-search";
import { cropPixelBounds, validSingleCrop } from "@/lib/image-crop";

// Initial deployment candidate, not an accuracy-validated model optimum.
export const AI_IMAGE_LONG_EDGE = 1600;
export const AI_IMAGE_MAX_PIXELS = 3_000_000;
export const AI_IMAGE_MAX_BYTES = 2 * 1024 * 1024;
export const AI_ORIGINAL_MAX_PIXELS = 40_000_000;

export class AIImageProcessingError extends Error {
  constructor(message = "照片自动处理失败，请重新选择有效的 JPG、PNG 或 WEBP 图片。") {
    super(message);
    this.name = "AIImageProcessingError";
  }
}

function checkAbort(signal?: AbortSignal) {
  if (signal?.aborted) throw new DOMException("照片处理已取消", "AbortError");
}

export function searchImageSize(width: number, height: number, longEdge = AI_IMAGE_LONG_EDGE) {
  if (![width, height, longEdge].every(Number.isFinite) || width <= 0 || height <= 0 || longEdge < 1 || longEdge > 2048) {
    throw new AIImageProcessingError("照片尺寸无效，请重新选择图片。");
  }
  const scale = Math.min(1, longEdge / Math.max(width, height), Math.sqrt(AI_IMAGE_MAX_PIXELS / (width * height)));
  return { width: Math.max(1, Math.floor(width * scale)), height: Math.max(1, Math.floor(height * scale)) };
}

export async function readAIPhoto(file: File, signal?: AbortSignal): Promise<ImageBitmap> {
  checkAbort(signal);
  if (!file.size || !["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
    throw new AIImageProcessingError("请选择有效的 JPG、PNG 或 WEBP 图片。");
  }
  let bitmap: ImageBitmap;
  try {
    // Same orientation as the native original preview. Never rotate a second time.
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    checkAbort(signal);
    throw new AIImageProcessingError();
  }
  try {
    checkAbort(signal);
    if (bitmap.width < 1 || bitmap.height < 1 || bitmap.width * bitmap.height > AI_ORIGINAL_MAX_PIXELS) {
      throw new AIImageProcessingError("照片分辨率超过 4000 万像素，请选择较小的照片。");
    }
    return bitmap;
  } catch (error) {
    bitmap.close();
    throw error;
  }
}

function encodeJPEG(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob || !blob.size || blob.type !== "image/jpeg") reject(new AIImageProcessingError());
      else resolve(blob);
    }, "image/jpeg", quality);
  });
}

export interface PreparedAISearchImage {
  file: File;
  width: number;
  height: number;
  originalCrop: CropRegion | null;
  // Pixels are already cropped. Sending the original ratios would crop twice.
  serverCrop: null;
}

export async function prepareAISearchImage(file: File, crop: CropRegion | null, options: {
  signal?: AbortSignal;
  sourceSize?: { width: number; height: number };
  longEdge?: number;
} = {}): Promise<PreparedAISearchImage> {
  const { signal, sourceSize } = options;
  const bitmap = await readAIPhoto(file, signal);
  let canvas: HTMLCanvasElement | undefined;
  try {
    if (sourceSize && (bitmap.width !== sourceSize.width || bitmap.height !== sourceSize.height)) {
      throw new AIImageProcessingError("照片方向或尺寸无法核对，请重新选择图片后框选。");
    }
    if (crop && !validSingleCrop(crop, bitmap.width, bitmap.height)) {
      throw new AIImageProcessingError("框选区域无效，请重新框住完整商品。");
    }
    const bounds = cropPixelBounds(crop ?? [0, 0, 1, 1], bitmap.width, bitmap.height);
    let size = searchImageSize(bounds.width, bounds.height, options.longEdge);
    canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) throw new AIImageProcessingError("浏览器无法处理照片，请使用新版 Chrome 或 Edge 后重试。");
    // Each attempt draws DIRECTLY from original pixels, not a downscaled preview
    // or the preceding compressed/cropped search file. No full-size crop canvas.
    for (let attempt = 0; attempt < 4; attempt += 1) {
      checkAbort(signal);
      canvas.width = size.width; canvas.height = size.height;
      context.imageSmoothingEnabled = true; context.imageSmoothingQuality = "high";
      context.fillStyle = "white"; context.fillRect(0, 0, size.width, size.height);
      context.drawImage(bitmap, bounds.left, bounds.top, bounds.width, bounds.height, 0, 0, size.width, size.height);
      for (const quality of [0.9, 0.8, 0.7]) {
        const blob = await encodeJPEG(canvas, quality);
        checkAbort(signal);
        if (blob.size <= AI_IMAGE_MAX_BYTES) {
          return { file: new File([blob], `${file.name.replace(/\.[^.]+$/, "") || "photo"}-ai.jpg`, { type: "image/jpeg", lastModified: file.lastModified }),
            ...size, originalCrop: crop ? [...crop] : null, serverCrop: null };
        }
      }
      size = searchImageSize(Math.max(1, Math.floor(size.width * .8)), Math.max(1, Math.floor(size.height * .8)));
    }
    throw new AIImageProcessingError("照片压缩后仍过大，请更换照片或缩小框选范围。");
  } catch (error) {
    if (error instanceof AIImageProcessingError || (error instanceof DOMException && error.name === "AbortError")) throw error;
    throw new AIImageProcessingError();
  } finally {
    bitmap.close();
    // Release backing pixels, including failure/abort paths. Never retain canvas.
    if (canvas) { canvas.width = 0; canvas.height = 0; }
  }
}
