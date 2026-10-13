"use client";

import { useEffect, useRef } from "react";
import type { CropRegion } from "@/lib/api/visual-search";

export default function PreviewCrop({ src, crop, label }: { src: string; crop: CropRegion | null; label: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let active = true;
    const image = new window.Image();
    image.onload = () => {
      if (!active || !canvas.current) return;
      const [x, y, w, h] = crop ?? [0, 0, 1, 1];
      const left = Math.round(x * image.naturalWidth), top = Math.round(y * image.naturalHeight);
      const width = Math.round((x + w) * image.naturalWidth) - left, height = Math.round((y + h) * image.naturalHeight) - top;
      const scale = Math.min(1, 520 / Math.max(width, height));
      canvas.current.width = Math.max(1, Math.round(width * scale));
      canvas.current.height = Math.max(1, Math.round(height * scale));
      canvas.current.getContext("2d")?.drawImage(image, left, top, width, height, 0, 0, canvas.current.width, canvas.current.height);
    };
    image.src = src;
    return () => { active = false; image.onload = null; };
  }, [src, crop]);
  return <canvas ref={canvas} className="ai-preview-crop" role="img" aria-label={label} />;
}
