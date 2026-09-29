"use client";

import { ReloadOutlined } from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  InputNumber,
  Modal,
  Table,
  Input,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useMemo, useState } from "react";
import OperationSuccessModal from "@/components/common/OperationSuccessModal";
import ProductImage from "@/components/inventory/ProductImage";
import { ApiResponseError } from "@/lib/api/client";
import {
  commitBatchStockOut,
  previewBatchStockOut,
  type BatchStockOutCommitLine,
  type BatchStockOutProductPreview,
} from "@/lib/api/inventory-movements";

interface BatchStockOutModalProps {
  open: boolean;
  productIds: number[];
  onClose: () => void;
  onReturnToInventory: () => void;
  onViewMovements: () => void;
}

interface BatchStockOutRow {
  key: string;
  product: BatchStockOutProductPreview;
  packaging: BatchStockOutProductPreview["packagings"][number] | null;
  isFirstPackagingRow: boolean;
  packagingRowCount: number;
}

interface BatchStockOutSuccess {
  productCount: number;
  packagingCount: number;
  cartonCount: number;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "服务请求失败，请重试。";
}

export default function BatchStockOutModal({
  open,
  productIds,
  onClose,
  onReturnToInventory,
  onViewMovements,
}: BatchStockOutModalProps) {
  const { message } = App.useApp();
  const [products, setProducts] = useState<BatchStockOutProductPreview[]>([]);
  const [previewLoading, setPreviewLoading] = useState(true);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewReloadCounter, setPreviewReloadCounter] = useState(0);
  const [quantities, setQuantities] = useState<Record<number, number>>({});
  const [remark, setRemark] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [operationError, setOperationError] = useState<string | null>(null);
  const [stockConflict, setStockConflict] = useState(false);
  const [success, setSuccess] = useState<BatchStockOutSuccess | null>(null);
  const productIdKey = productIds.join(",");
  const stableProductIds = useMemo(
    () => productIdKey.split(",").filter(Boolean).map(Number),
    [productIdKey],
  );

  useEffect(() => {
    if (!open || stableProductIds.length === 0) {
      return;
    }

    const controller = new AbortController();
    void previewBatchStockOut(stableProductIds, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) {
          return;
        }
        setProducts(result.products);
        setQuantities((current) => {
          const next = { ...current };
          for (const product of result.products) {
            for (const packaging of product.packagings) {
              next[packaging.id] ??= 0;
            }
          }
          return next;
        });
        setPreviewError(null);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setPreviewError(getErrorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setPreviewLoading(false);
        }
      });

    return () => controller.abort();
  }, [open, stableProductIds, previewReloadCounter]);

  const rows = useMemo<BatchStockOutRow[]>(() => {
    const result: BatchStockOutRow[] = [];
    for (const product of products) {
      const packagingRows = product.packagings.length > 0
        ? product.packagings
        : [null];
      packagingRows.forEach((packaging, index) => {
        result.push({
          key: packaging ? String(packaging.id) : `product-${product.product_id}-empty`,
          product,
          packaging,
          isFirstPackagingRow: index === 0,
          packagingRowCount: packagingRows.length,
        });
      });
    }
    return result;
  }, [products]);

  const positiveLines = useMemo(() => {
    const lines: Array<{
      product: BatchStockOutProductPreview;
      packaging: BatchStockOutRow["packaging"];
      quantity: number;
    }> = [];
    for (const row of rows) {
      if (!row.packaging) {
        continue;
      }
      const quantity = quantities[row.packaging.id] ?? 0;
      if (quantity > 0) {
        lines.push({ product: row.product, packaging: row.packaging, quantity });
      }
    }
    return lines;
  }, [quantities, rows]);

  const totalCartons = positiveLines.reduce((sum, line) => sum + line.quantity, 0);
  const affectedProductCount = new Set(
    positiveLines.map((line) => line.product.product_id),
  ).size;
  const exceedsCurrentStock = rows.some(
    (row) =>
      row.packaging !== null &&
      (quantities[row.packaging.id] ?? 0) > row.packaging.carton_count,
  );
  const includesInactiveProduct = positiveLines.some(
    (line) => !line.product.is_active,
  );
  const canSubmit =
    positiveLines.length > 0 &&
    !exceedsCurrentStock &&
    !includesInactiveProduct &&
    !previewLoading &&
    !previewError &&
    !submitting;
  const packagingCount = products.reduce(
    (count, product) => count + product.packagings.length,
    0,
  );

  function refreshPreview() {
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewReloadCounter((value) => value + 1);
  }

  const columns: ColumnsType<BatchStockOutRow> = [
    {
      title: "商品信息",
      width: 260,
      onCell: (row) => ({
        rowSpan: row.isFirstPackagingRow ? row.packagingRowCount : 0,
      }),
      render: (_value, row) =>
        row.isFirstPackagingRow ? (
          <div className="batch-stock-out-product">
            <ProductImage
              imagePath={row.product.image_path}
              thumbnailPath={row.product.thumbnail_path}
              alt={row.product.product_code ?? "库存商品"}
              width={48}
              height={48}
              enablePreview={false}
            />
            <div className="batch-stock-out-product-copy">
              <strong>{row.product.product_code ?? "暂无编号"}</strong>
              {row.product.size && (
                <span className="batch-stock-out-product-size">
                  {row.product.size}
                </span>
              )}
              {!row.product.is_active && (
                <span className="batch-stock-out-inactive">已停用</span>
              )}
            </div>
          </div>
        ) : null,
    },
    {
      title: "仓库",
      width: 116,
      onCell: (row) => ({
        rowSpan: row.isFirstPackagingRow ? row.packagingRowCount : 0,
      }),
      render: (_value, row) =>
        row.isFirstPackagingRow
          ? row.product.warehouse_name ?? "未指定仓库"
          : null,
    },
    {
      title: "包装规格",
      width: 150,
      render: (_value, row) =>
        row.packaging
          ? `${row.packaging.packing_qty ?? "未填写"} ${row.product.unit ?? ""}/箱`
          : "暂无包装规格",
    },
    {
      title: "当前库存",
      width: 112,
      render: (_value, row) =>
        row.packaging ? `${row.packaging.carton_count} 箱` : "—",
    },
    {
      title: "出库箱数",
      width: 150,
      render: (_value, row) => {
        if (!row.packaging) {
          return "—";
        }
        const quantity = quantities[row.packaging.id] ?? 0;
        const invalid = quantity > row.packaging.carton_count;
        return (
          <div className="batch-stock-out-quantity-cell">
            <InputNumber
              aria-label={`${row.product.product_code ?? "商品"} ${row.packaging.packing_qty ?? "未填写"} 包装出库箱数`}
              min={0}
              precision={0}
              step={1}
              value={quantity}
              status={invalid ? "error" : undefined}
              disabled={previewLoading || Boolean(previewError) || submitting || Boolean(success)}
              onChange={(value) => {
                const nextValue = typeof value === "number"
                  ? Math.max(0, Math.floor(value))
                  : 0;
                setQuantities((current) => ({
                  ...current,
                  [row.packaging!.id]: nextValue,
                }));
                setOperationError(null);
              }}
            />
            {invalid && (
              <span className="batch-stock-out-validation">超过当前库存</span>
            )}
          </div>
        );
      },
    },
    {
      title: "出库后库存",
      width: 124,
      render: (_value, row) => {
        if (!row.packaging) {
          return "—";
        }
        const quantity = quantities[row.packaging.id] ?? 0;
        return `${Math.max(0, row.packaging.carton_count - quantity)} 箱`;
      },
    },
  ];

  async function handleCommit() {
    if (!canSubmit) {
      return;
    }

    const submittedLines: BatchStockOutCommitLine[] = positiveLines.map((line) => ({
      product_id: line.product.product_id,
      product_packaging_id: line.packaging!.id,
      quantity: line.quantity,
    }));
    const successSummary = {
      productCount: affectedProductCount,
      packagingCount: positiveLines.length,
      cartonCount: totalCartons,
    };
    setConfirmOpen(false);
    setSubmitting(true);
    setOperationError(null);
    setStockConflict(false);
    try {
      await commitBatchStockOut(submittedLines, remark);
      setSuccess(successSummary);
    } catch (error) {
      if (error instanceof ApiResponseError && error.statusCode === 409) {
        setStockConflict(true);
        refreshPreview();
        message.warning("库存已发生变化，工作台已刷新最新库存。请核对后重新确认。");
      } else {
        setOperationError(getErrorMessage(error));
        message.error(getErrorMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  }

  const modalTitle = (
    <div className="batch-stock-out-modal-title">
      <Typography.Title level={4}>批量出库</Typography.Title>
      <Typography.Text type="secondary">
        已选择 {productIds.length} 个商品 · 共 {packagingCount} 个包装规格
      </Typography.Text>
    </div>
  );

  return (
    <>
      <Modal
        className="batch-stock-out-modal"
        classNames={{ container: "batch-stock-out-modal-container" }}
        open={open && !success}
        width={1180}
        centered
        title={modalTitle}
        closable={!success}
        mask={{ closable: !submitting && !success }}
        onCancel={success ? undefined : onClose}
        footer={
          <>
            <div className="batch-stock-out-remark">
              <label htmlFor="batch-stock-out-remark">出库备注（可选）</label>
              <Input.TextArea
                id="batch-stock-out-remark"
                value={remark}
                maxLength={200}
                showCount
                autoSize={{ minRows: 2, maxRows: 4 }}
                placeholder="例如：客户名称、柜号、其他说明..."
                disabled={previewLoading || Boolean(previewError) || submitting}
                onChange={(event) => setRemark(event.target.value)}
              />
            </div>
            <div className="batch-stock-out-footer">
              <div className="batch-stock-out-total">
                本次出库：{affectedProductCount} 个商品 · {positiveLines.length} 个包装规格 · 共 {totalCartons} 箱
              </div>
              <div className="batch-stock-out-footer-actions">
                <Button disabled={submitting} onClick={onClose}>取消</Button>
                <Button
                  type="primary"
                  disabled={!canSubmit}
                  loading={submitting}
                  onClick={() => setConfirmOpen(true)}
                >
                  确认出库（{totalCartons} 箱）
                </Button>
              </div>
            </div>
          </>
        }
      >
        <div className="batch-stock-out-table-region">
          {stockConflict && (
            <Alert
              className="batch-stock-out-alert"
              type="warning"
              showIcon
              message="库存已发生变化，已刷新最新库存。您填写的出库数量和备注已保留，请核对后重新确认。"
            />
          )}
          {includesInactiveProduct && (
            <Alert
              className="batch-stock-out-alert"
              type="warning"
              showIcon
              message="已停用商品不能出库，请将这些商品的出库箱数调整为 0。"
            />
          )}
          {previewError && (
            <Alert
              className="batch-stock-out-alert"
              type="error"
              showIcon
              message="无法读取最新库存"
              description={previewError}
              action={
                <Button
                  size="small"
                  icon={<ReloadOutlined />}
                  onClick={refreshPreview}
                >
                  重试
                </Button>
              }
            />
          )}
          {operationError && (
            <Alert
              className="batch-stock-out-alert"
              type="error"
              showIcon
              message={operationError}
            />
          )}
          <Table<BatchStockOutRow>
            className="batch-stock-out-table"
            columns={columns}
            dataSource={rows}
            loading={previewLoading}
            pagination={false}
            rowKey="key"
            size="middle"
            scroll={{ x: 900 }}
          />
        </div>
      </Modal>

      <OperationSuccessModal
        open={open && Boolean(success)}
        title="批量出库成功"
        description={success
          ? `已对 ${success.productCount} 个商品（${success.packagingCount} 个包装规格）完成出库，共 ${success.cartonCount} 箱。`
          : null}
        secondaryAction={{ label: "返回商品库存", onClick: onReturnToInventory }}
        primaryAction={{ label: "查看库存流水", onClick: onViewMovements }}
        onClose={onReturnToInventory}
      />

      <Modal
        className="batch-stock-out-confirm-modal"
        open={confirmOpen && !success}
        title="确认出库"
        centered
        closable={!submitting}
        mask={{ closable: !submitting }}
        confirmLoading={submitting}
        okText="确认出库"
        cancelText="取消"
        onCancel={() => setConfirmOpen(false)}
        onOk={() => void handleCommit()}
      >
        <div className="batch-stock-out-confirm-content">
          <p>本次将对：</p>
          <ul>
            <li>{affectedProductCount} 个商品</li>
            <li>{positiveLines.length} 个包装规格</li>
          </ul>
          <p>执行出库，共 {totalCartons} 箱。</p>
          <Typography.Text type="secondary">
            确认后将生成库存流水，此操作不可撤销。
          </Typography.Text>
        </div>
      </Modal>
    </>
  );
}
