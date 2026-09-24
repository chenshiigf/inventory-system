"use client";

import { Button, Tooltip } from "antd";
import type { ReactNode } from "react";

export type BatchStatusSelection = "none" | "active" | "inactive" | "mixed";

interface BatchActionBarProps {
  selectedCount: number;
  currentPageSelectedCount: number;
  currentPageCount: number;
  allCurrentPageSelected: boolean;
  statusSelection: BatchStatusSelection;
  onSelectCurrentPage: () => void;
  onRemoveCurrentPage: () => void;
  onClearAll: () => void;
  onChangeCategory: () => void;
  onDeactivate: () => void;
  onActivate: () => void;
  onExit: () => void;
}

function ActionTooltip({
  title,
  children,
}: {
  title?: string;
  children: ReactNode;
}) {
  if (!title) {
    return children;
  }

  return (
    <Tooltip title={title}>
      <span className="batch-action-tooltip-target">{children}</span>
    </Tooltip>
  );
}

export default function BatchActionBar({
  selectedCount,
  currentPageSelectedCount,
  currentPageCount,
  allCurrentPageSelected,
  statusSelection,
  onSelectCurrentPage,
  onRemoveCurrentPage,
  onClearAll,
  onChangeCategory,
  onDeactivate,
  onActivate,
  onExit,
}: BatchActionBarProps) {
  const hasSelection = selectedCount > 0;
  const isMixed = statusSelection === "mixed";
  const statusHint = isMixed
    ? "所选商品包含不同状态，请只选择同一状态商品"
    : undefined;

  return (
    <div className="batch-action-bar" aria-label="商品批量操作">
      <div className="batch-action-summary">
        <strong>已选择 {selectedCount} / 100 个</strong>
        <span>当前页已选择 {currentPageSelectedCount} 个</span>
      </div>
      <div className="batch-action-buttons">
        <Button
          size="small"
          disabled={currentPageCount === 0 || allCurrentPageSelected}
          onClick={onSelectCurrentPage}
        >
          全选当前页
        </Button>
        <Button
          size="small"
          disabled={currentPageSelectedCount === 0}
          onClick={onRemoveCurrentPage}
        >
          取消当前页
        </Button>
        <Button size="small" disabled={!hasSelection} onClick={onClearAll}>
          清空全部
        </Button>
        <Button size="small" disabled={!hasSelection} onClick={onChangeCategory}>
          修改分类
        </Button>
        <ActionTooltip title={statusHint}>
          <Button
            size="small"
            danger
            disabled={!hasSelection || statusSelection !== "active"}
            onClick={onDeactivate}
          >
            停用
          </Button>
        </ActionTooltip>
        <ActionTooltip title={statusHint}>
          <Button
            size="small"
            disabled={!hasSelection || statusSelection !== "inactive"}
            onClick={onActivate}
          >
            启用
          </Button>
        </ActionTooltip>
      </div>
      <Button className="batch-action-exit" type="text" size="small" onClick={onExit}>
        退出批量模式
      </Button>
    </div>
  );
}
