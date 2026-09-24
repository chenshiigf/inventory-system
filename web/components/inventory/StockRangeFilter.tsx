"use client";

import { DownOutlined, FilterOutlined } from "@ant-design/icons";
import { Button, Input, Popover } from "antd";
import { useState } from "react";

interface StockRangeFilterProps {
  stockMin: number | null;
  stockMax: number | null;
  onChange: (stockMin: number | null, stockMax: number | null) => void;
}

interface StockRangePreset {
  key: string;
  label: string;
  stockMin: number | null;
  stockMax: number | null;
}

const STOCK_RANGE_PRESETS: StockRangePreset[] = [
  { key: "all", label: "全部库存", stockMin: null, stockMax: null },
  { key: "zero", label: "0箱", stockMin: 0, stockMax: 0 },
  { key: "one-to-ten", label: "1–10箱", stockMin: 1, stockMax: 10 },
  { key: "over-ten", label: ">10箱", stockMin: 11, stockMax: null },
  { key: "over-fifty", label: ">50箱", stockMin: 51, stockMax: null },
  { key: "over-hundred", label: ">100箱", stockMin: 101, stockMax: null },
];

function formatStockRange(
  stockMin: number | null,
  stockMax: number | null,
): string {
  if (stockMin === null && stockMax === null) {
    return "全部";
  }
  if (stockMin === 0 && stockMax === 0) {
    return "0箱";
  }
  if (stockMin !== null && stockMax !== null) {
    return `${stockMin}–${stockMax}箱`;
  }
  if (stockMin !== null) {
    if (stockMin === 11) return ">10箱";
    if (stockMin === 51) return ">50箱";
    if (stockMin === 101) return ">100箱";
    return `≥${stockMin}箱`;
  }
  return `≤${stockMax}箱`;
}

function matchesPreset(
  preset: StockRangePreset,
  stockMin: number | null,
  stockMax: number | null,
): boolean {
  return preset.stockMin === stockMin && preset.stockMax === stockMax;
}

function parseBound(value: string): number | null | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    return null;
  }
  if (!/^\d+$/.test(trimmed)) {
    return undefined;
  }

  const parsed = Number(trimmed);
  return Number.isSafeInteger(parsed) ? parsed : undefined;
}

export default function StockRangeFilter({
  stockMin,
  stockMax,
  onChange,
}: StockRangeFilterProps) {
  const [open, setOpen] = useState(false);
  const [minimumText, setMinimumText] = useState("");
  const [maximumText, setMaximumText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const label = formatStockRange(stockMin, stockMax);

  function openPopover(nextOpen: boolean) {
    if (nextOpen) {
      setMinimumText(stockMin === null ? "" : String(stockMin));
      setMaximumText(stockMax === null ? "" : String(stockMax));
      setError(null);
    }
    setOpen(nextOpen);
  }

  function applyCustomRange() {
    const nextStockMin = parseBound(minimumText);
    const nextStockMax = parseBound(maximumText);
    if (nextStockMin === undefined || nextStockMax === undefined) {
      setError("请输入非负整数");
      return;
    }
    if (
      nextStockMin !== null &&
      nextStockMax !== null &&
      nextStockMin > nextStockMax
    ) {
      setError("最少箱数不能大于最多箱数");
      return;
    }

    onChange(nextStockMin, nextStockMax);
    setOpen(false);
  }

  function resetRange() {
    setMinimumText("");
    setMaximumText("");
    setError(null);
    onChange(null, null);
    setOpen(false);
  }

  const content = (
    <div className="stock-range-filter-content">
      <div className="stock-range-section-label">常用筛选</div>
      <div className="stock-range-quick-grid" aria-label="常用库存范围">
        {STOCK_RANGE_PRESETS.map((preset) => (
          <Button
            key={preset.key}
            size="small"
            type={
              matchesPreset(preset, stockMin, stockMax) ? "primary" : "default"
            }
            aria-pressed={matchesPreset(preset, stockMin, stockMax)}
            onClick={() => {
              onChange(preset.stockMin, preset.stockMax);
              setOpen(false);
            }}
          >
            {preset.label}
          </Button>
        ))}
      </div>
      <div className="stock-range-input-row">
        <Input
          aria-label="最少箱数"
          inputMode="numeric"
          placeholder="最少"
          value={minimumText}
          onChange={(event) => {
            setMinimumText(event.target.value);
            setError(null);
          }}
          onPressEnter={applyCustomRange}
        />
        <span aria-hidden="true">—</span>
        <Input
          aria-label="最多箱数"
          inputMode="numeric"
          placeholder="最多"
          value={maximumText}
          onChange={(event) => {
            setMaximumText(event.target.value);
            setError(null);
          }}
          onPressEnter={applyCustomRange}
        />
      </div>
      {error && (
        <div className="stock-range-validation-error" role="alert">
          {error}
        </div>
      )}
      <div className="stock-range-footer">
        <Button size="small" onClick={resetRange}>
          重置
        </Button>
        <Button size="small" type="primary" onClick={applyCustomRange}>
          确定
        </Button>
      </div>
    </div>
  );

  return (
    <Popover
      open={open}
      onOpenChange={openPopover}
      trigger="click"
      placement="bottomLeft"
      destroyOnHidden
      classNames={{ root: "stock-range-popover" }}
      content={content}
    >
      <Button
        className="inventory-stock-range-trigger"
        aria-label={`库存：${label}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        icon={<FilterOutlined aria-hidden="true" />}
      >
        <span>库存：{label}</span>
        <DownOutlined aria-hidden="true" className="stock-range-trigger-caret" />
      </Button>
    </Popover>
  );
}
