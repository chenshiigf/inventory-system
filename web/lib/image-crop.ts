import type { CropRegion } from "@/lib/api/visual-search";

export function relativeImagePoint(clientX: number, clientY: number, bounds: { left: number; top: number; width: number; height: number }): [number, number] {
  if (bounds.width <= 0 || bounds.height <= 0) return [0, 0];
  return [Math.max(0, Math.min(1, (clientX - bounds.left) / bounds.width)), Math.max(0, Math.min(1, (clientY - bounds.top) / bounds.height))];
}
export function cropFromPoints(start: [number, number], end: [number, number]): CropRegion {
  return [Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.abs(end[0] - start[0]), Math.abs(end[1] - start[1])];
}
// Match Python/Pillow's round() on half-pixel boundaries, including ties to even.
function roundPixel(value: number): number {
  const floor = Math.floor(value);
  return value - floor === 0.5 ? floor + (floor % 2 === 0 ? 0 : 1) : Math.round(value);
}
export function cropPixelBounds(crop: CropRegion, width: number, height: number) {
  const [x, y, w, h] = crop;
  const left = roundPixel(x * width), top = roundPixel(y * height);
  return { left, top, width: roundPixel((x + w) * width) - left, height: roundPixel((y + h) * height) - top };
}
export function validSingleCrop(crop: CropRegion, width: number, height: number): boolean {
  const [x, y, w, h] = crop;
  if (!crop.every(Number.isFinite) || x < 0 || y < 0 || w <= 0 || h <= 0 || x + w > 1.000001 || y + h > 1.000001) return false;
  const pixels = cropPixelBounds(crop, width, height);
  return pixels.width >= 10 && pixels.height >= 10;
}
export function sameCrop(a: CropRegion | null, b: CropRegion | null): boolean {
  return a === null || b === null ? a === b : a.every((value, i) => Math.abs(value - b[i]) < 1e-9);
}
