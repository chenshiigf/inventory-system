"use client";

import { useRef, useState, type PointerEvent } from "react";
import { Button, InputNumber, Space } from "antd";
import type { CropRegion } from "@/lib/api/visual-search";
import { cropFromPoints, relativeImagePoint } from "@/lib/image-crop";

export default function RegionSelector({ src, regions, selected, onSelect, onAdd, disabled, drawing = true, numbers, showManual = true, minRatio = 0.015 }: {
  src: string; regions: CropRegion[]; selected: number; onSelect: (index: number) => void;
  onAdd: (region: CropRegion) => void; disabled: boolean;
  drawing?: boolean; numbers?: number[]; showManual?: boolean; minRatio?: number;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const start = useRef<[number, number] | null>(null);
  const [draft, setDraft] = useState<CropRegion | null>(null);
  const [manual, setManual] = useState<CropRegion>([0, 0, 0.5, 0.5]);
  function point(event: PointerEvent): [number, number] {
    return relativeImagePoint(event.clientX, event.clientY, stage.current!.getBoundingClientRect());
  }
  function move(event: PointerEvent): CropRegion | null {
    if (!start.current) return null;
    return cropFromPoints(start.current, point(event));
  }
  return <>
    <div ref={stage} className={`visual-region-stage${disabled ? " is-disabled" : ""}${drawing ? " is-drawing" : ""}`}
      onPointerDown={(event) => { if (disabled || !drawing || regions.length >= 10 || event.button !== 0) return; start.current = point(event); event.currentTarget.setPointerCapture(event.pointerId); }}
      onPointerMove={(event) => { if (start.current) setDraft(move(event)); }}
      onPointerCancel={() => { start.current = null; setDraft(null); }}
      onPointerUp={(event) => { const region = move(event); start.current = null; setDraft(null); if (region && !disabled && drawing && region[2] > minRatio && region[3] > minRatio) onAdd(region); }}>
      {/* Native image preserves exact displayed bounds for source-relative crops. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt="上传原图，在商品周围拖动框选" draggable={false} />
      {regions.map((region, index) => <button key={index} type="button" disabled={disabled} aria-label={`选择区域 ${numbers?.[index] ?? index + 1}`} className={`visual-region-box${selected === index ? " selected" : ""}`}
        style={{ left: `${region[0] * 100}%`, top: `${region[1] * 100}%`, width: `${region[2] * 100}%`, height: `${region[3] * 100}%` }}
        onPointerDown={(event) => event.stopPropagation()} onPointerUp={(event) => event.stopPropagation()} onClick={() => onSelect(index)}><span>{numbers?.[index] ?? index + 1}</span></button>)}
      {draft && <div className="visual-region-box selected draft" style={{ left: `${draft[0] * 100}%`, top: `${draft[1] * 100}%`, width: `${draft[2] * 100}%`, height: `${draft[3] * 100}%` }} />}
    </div>
    {showManual && <details className="visual-manual-region"><summary>用数字框选（支持键盘）</summary>
      <Space wrap>{["左侧 %", "顶部 %", "宽度 %", "高度 %"].map((label, i) => <label key={label}>{label}<InputNumber aria-label={label} min={0} max={100} value={Math.round(manual[i] * 100)} disabled={disabled} onChange={(value: number | null) => setManual((old) => old.map((v, j) => j === i ? (value ?? 0) / 100 : v) as CropRegion)} /></label>)}
        <Button disabled={disabled || !drawing || regions.length >= 10 || manual[2] <= 0 || manual[3] <= 0 || manual[0] + manual[2] > 1 || manual[1] + manual[3] > 1} onClick={() => onAdd(manual)}>添加区域</Button>
      </Space>
    </details>}
  </>;
}
