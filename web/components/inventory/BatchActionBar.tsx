"use client";

import { DownOutlined } from "@ant-design/icons";
import { Button, Dropdown, Tooltip } from "antd";
import type { MenuProps } from "antd";
import type { ReactNode } from "react";

export type BatchStatusSelection = "none" | "active" | "inactive" | "mixed";

interface BatchActionBarProps {
  selectedCount: number;
  currentPageCount: number;
  allCurrentPageSelected: boolean;
  statusSelection: BatchStatusSelection;
  onSelectCurrentPage: () => void;
  onRemoveCurrentPage: () => void;
  onClearAll: () => void;
  onBatchOutbound: () => void;
  onChangeCategory: () => void;
  onExportQuote: () => void;
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
  currentPageCount,
  allCurrentPageSelected,
  statusSelection,
  onSelectCurrentPage,
  onRemoveCurrentPage,
  onClearAll,
  onBatchOutbound,
  onChangeCategory,
  onExportQuote,
  onDeactivate,
  onActivate,
  onExit,
}: BatchActionBarProps) {
  const hasSelection = selectedCount > 0;
  const isMixed = statusSelection === "mixed";
  const statusHint = isMixed
    ? "所选商品包含不同状态，请只选择同一状态商品"
    : undefined;
  const statusMenuItems: MenuProps["items"] = [
    {
      key: "deactivate",
      label: "停用",
      danger: true,
      disabled: !hasSelection || statusSelection !== "active",
    },
    {
      key: "activate",
      label: "启用",
      disabled: !hasSelection || statusSelection !== "inactive",
    },
  ];

  function handleMenuClick(key: string) {
    if (key === "deactivate" && statusSelection === "active") {
      onDeactivate();
    } else if (key === "activate" && statusSelection === "inactive") {
      onActivate();
    }
  }

  return (
    <div className="batch-action-bar" aria-label="商品批量操作">
      <div className="batch-action-row batch-action-row-selection">
        <div className="batch-action-selection-controls">
          <strong>已选择 {selectedCount} 个 · 最多 100 个</strong>
          <Button
            size="small"
            disabled={currentPageCount === 0}
            onClick={allCurrentPageSelected ? onRemoveCurrentPage : onSelectCurrentPage}
          >
            {allCurrentPageSelected ? "取消当前页" : "全选当前页"}
          </Button>
          <Button size="small" disabled={!hasSelection} onClick={onClearAll}>
            清空选择
          </Button>
        </div>
        <Button
          className="batch-action-exit"
          type="text"
          size="small"
          onClick={onExit}
        >
          退出批量模式
        </Button>
      </div>

      <div className="batch-action-row batch-action-row-business">
        <div className="batch-action-business-buttons">
          <Button
            size="small"
            disabled={!hasSelection}
            onClick={onBatchOutbound}
          >
            批量出库
          </Button>
          <Button size="small" disabled={!hasSelection} onClick={onChangeCategory}>
            修改分类
          </Button>
          <Button size="small" disabled={!hasSelection} onClick={onExportQuote}>
            导出报价单
          </Button>
          <ActionTooltip title={statusHint}>
            <Dropdown
              trigger={["click"]}
              disabled={!hasSelection}
              menu={{
                className: "batch-action-status-menu",
                items: statusMenuItems,
                onClick: ({ key }) => handleMenuClick(key),
              }}
            >
              <Button size="small" disabled={!hasSelection}>
                更多 <DownOutlined />
              </Button>
            </Dropdown>
          </ActionTooltip>
        </div>
      </div>
    </div>
  );
}
