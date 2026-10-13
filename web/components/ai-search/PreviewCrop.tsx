"use client";

import { useEffect, useRef } from "react";
import type { CropRegion } from "@/lib/api/visual-search";
import { cropPixelBounds } from "@/lib/image-crop";

export default function PreviewCrop({ src, crop, label, className = "ai-preview-crop" }: { src: string; crop: CropRegion | null; label: string; className?: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let active = true;
    canvas.current?.getContext("2d")?.clearRect(0, 0, canvas.current.width, canvas.current.height);
    const image = new window.Image();
    image.onload = () => {
      if (!active || !canvas.current) return;
      const { left, top, width, height } = cropPixelBounds(crop ?? [0, 0, 1, 1], image.naturalWidth, image.naturalHeight);
      const scale = Math.min(1, 520 / Math.max(width, height));
      canvas.current.width = Math.max(1, Math.round(width * scale));
      canvas.current.height = Math.max(1, Math.round(height * scale));
      canvas.current.getContext("2d")?.drawImage(image, left, top, width, height, 0, 0, canvas.current.width, canvas.current.height);
    };
    image.src = src;
    return () => { active = false; image.onload = null; };
  }, [src, crop]);
  return <canvas ref={canvas} className={className} role="img" aria-label={label} />;
}
