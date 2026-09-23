"use client";

import { Alert, Button, Empty, Modal, Pagination, Spin, Table, Typography } from "antd";
import { useEffect, useState } from "react";
import { listInventoryMovements } from "@/lib/api/inventory-movements";
import type {
  InventoryMovementApiRecord,
  InventoryMovementListResponse,
  InventoryProduct,
} from "@/types/inventory";

interface InventoryMovementsModalProps {
  product: InventoryProduct;
  onCancel: () => void;
}

const PAGE_SIZE = 20;

function formatMovementTime(value: string): string {
  const date = new Date(value.endsWith("Z") ? value : `${value}Z`);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatPackaging(movement: InventoryMovementApiRecord): string {
  if (movement.packing_qty_snapshot === null) {
    return "装箱数未填写";
  }
  return `${movement.packing_qty_snapshot}${movement.unit_snapshot ? ` ${movement.unit_snapshot}` : ""}/箱`;
}

function getMovementLabel(type: InventoryMovementApiRecord["movement_type"]): string {
  return type === "IN" ? "入库" : "出库";
}

export default function InventoryMovementsModal({
  product,
  onCancel,
}: InventoryMovementsModalProps) {
  const [page, setPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  const [result, setResult] = useState<InventoryMovementListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void listInventoryMovements(
      { productId: product.id, page, pageSize: PAGE_SIZE },
      controller.signal,
    )
      .then((response) => {
        if (!controller.signal.aborted) {
          setResult(response);
        }
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) {
          setResult(null);
          setError(
            requestError instanceof Error
              ? requestError.message
              : "库存流水加载失败，请重试。",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
        }
      });

    return () => controller.abort();
  }, [page, product.id, reloadKey]);

  const columns = [
    {
      title: "时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 150,
      render: (value: string) => formatMovementTime(value),
    },
    {
      title: "类型",
      dataIndex: "movement_type",
      key: "movement_type",
      width: 64,
      render: (value: InventoryMovementApiRecord["movement_type"]) => (
        <span className={`movement-type movement-type-${value.toLowerCase()}`}>
          {getMovementLabel(value)}
        </span>
      ),
    },
    {
      title: "包装规格",
      key: "packaging",
      width: 130,
      render: (_value: unknown, movement: InventoryMovementApiRecord) =>
        formatPackaging(movement),
    },
    {
      title: "变化箱数",
      key: "quantity",
      width: 86,
      render: (_value: unknown, movement: InventoryMovementApiRecord) => (
        <span className={`movement-quantity movement-quantity-${movement.movement_type.toLowerCase()}`}>
          {movement.movement_type === "IN" ? "+" : "−"}
          {movement.quantity}箱
        </span>
      ),
    },
    {
      title: "库存变化",
      key: "stock",
      width: 92,
      render: (_value: unknown, movement: InventoryMovementApiRecord) =>
        `${movement.before_carton_count} → ${movement.after_carton_count}`,
    },
    {
      title: "备注",
      dataIndex: "remark",
      key: "remark",
      render: (value: string | null) =>
        value ? (
          <span className="movement-remark">{value}</span>
        ) : (
          <span className="table-note-empty">—</span>
        ),
    },
  ];

  const hasInitialStockWithoutHistory =
    !loading && result?.total === 0 && product.totalCartonCount > 0;

  return (
    <Modal
      title={`库存流水 · ${(product.productCode ?? product.size) || "商品"}`}
      open
      width={820}
      footer={null}
      onCancel={onCancel}
      destroyOnHidden
    >
      <div className="inventory-movements-modal">
        {hasInitialStockWithoutHistory && (
          <Alert
            className="inventory-movements-initial-note"
            type="info"
            showIcon
            title="现有初始库存不会补造历史流水，流水从系统正式库存操作开始记录。"
          />
        )}
        {loading ? (
          <div className="inventory-movements-loading">
            <Spin />
          </div>
        ) : error ? (
          <Alert
            type="error"
            showIcon
            title="库存流水加载失败"
            description={error}
            action={
              <Button
                size="small"
                onClick={() => {
                  setLoading(true);
                  setError(null);
                  setReloadKey((value) => value + 1);
                }}
              >
                重试
              </Button>
            }
          />
        ) : result?.items.length ? (
          <>
            <Table<InventoryMovementApiRecord>
              className="inventory-movements-table"
              rowKey="id"
              columns={columns}
              dataSource={result.items}
              pagination={false}
              size="small"
              tableLayout="fixed"
            />
            {result.total > PAGE_SIZE && (
              <Pagination
                className="inventory-movements-pagination"
                current={result.page}
                pageSize={result.page_size}
                total={result.total}
                showSizeChanger={false}
                onChange={(nextPage) => {
                  setLoading(true);
                  setError(null);
                  setPage(nextPage);
                }}
              />
            )}
          </>
        ) : (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              hasInitialStockWithoutHistory
                ? "当前暂无正式库存操作流水"
                : "暂无库存流水"
            }
          />
        )}
        <Typography.Text type="secondary" className="inventory-movements-footnote">
          仅显示最近的库存操作记录，按时间从新到旧排列。
        </Typography.Text>
      </div>
    </Modal>
  );
}
