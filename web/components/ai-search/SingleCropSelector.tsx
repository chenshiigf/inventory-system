"use client";

import RegionSelector from "@/components/visual-search/RegionSelector";
import type { CropRegion } from "@/lib/api/visual-search";

export default function SingleCropSelector({ src, crop, drawing, disabled, onCrop }: {
  src: string; crop: CropRegion | null; drawing: boolean; disabled: boolean; onCrop: (crop: CropRegion) => void;
}) {
  return <div className="ai-search-single-crop"><RegionSelector src={src} regions={!drawing && crop ? [crop] : []} selected={crop ? 0 : -1} drawing={drawing} disabled={disabled} onSelect={() => undefined} onAdd={onCrop} showManual={false} minRatio={0} /></div>;
}
