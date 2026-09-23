"use client";

import { Form, Input, InputNumber, Modal, Select, Typography } from "antd";
import { useState } from "react";
import type {
  InventoryPackaging,
  InventoryProduct,
  StockAdjustmentValues,
} from "@/types/inventory";

interface InventoryAdjustmentModalProps {
  product: InventoryProduct;
  onCancel: () => void;
  onConfirm: (values: StockAdjustmentValues) => Promise<void>;
}

interface AdjustmentFormValues {
  packagingId?: number;
  actualCartonCount?: number;
  remark?: string;
}

function packagingLabel(
  packaging: InventoryPackaging,
  unit: InventoryProduct["unit"],
): string {
  return `${packaging.packingQty ?? "装箱数未填写"} ${unit ?? "—"}/箱 · 当前 ${packaging.cartonCount} 箱`;
}

function formatDelta(delta: number | null): string {
  if (delta === null) {
    return "—";
  }
  if (delta > 0) {
    return `+${delta}箱`;
  }
  if (delta < 0) {
    return `-${Math.abs(delta)}箱`;
  }
  return "0箱";
}

export default function InventoryAdjustmentModal({
  product,
  onCancel,
  onConfirm,
}: InventoryAdjustmentModalProps) {
  const [form] = Form.useForm<AdjustmentFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const selectedPackagingId = Form.useWatch("packagingId", form);
  const actualCartonCount = Form.useWatch("actualCartonCount", form);
  const selectedPackaging = product.packagings.find(
    (packaging) => packaging.id === selectedPackagingId,
  );
  const beforeCartonCount = selectedPackaging?.cartonCount ?? 0;
  const validActualCartonCount =
    typeof actualCartonCount === "number" &&
    Number.isInteger(actualCartonCount) &&
    actualCartonCount >= 0
      ? actualCartonCount
      : null;
  const delta =
    validActualCartonCount === null
      ? null
      : validActualCartonCount - beforeCartonCount;
  const canSubmit =
    !submitting &&
    selectedPackaging !== undefined &&
    validActualCartonCount !== null &&
    delta !== 0;
  const productLabel = product.productCode || product.size || "商品";

  async function handleConfirm() {
    try {
      const values = await form.validateFields();
      if (
        values.packagingId === undefined ||
        values.actualCartonCount === undefined ||
        values.remark === undefined ||
        values.actualCartonCount === beforeCartonCount
      ) {
        return;
      }

      setSubmitting(true);
      try {
        await onConfirm({
          packagingId: values.packagingId,
          actualCartonCount: values.actualCartonCount,
          remark: values.remark.trim(),
        });
      } catch {
        setSubmitting(false);
      }
    } catch {
      // Keep the modal open so Form can show the invalid field.
    }
  }

  return (
    <Modal
      title="库存调整"
      open
      width={540}
      okText="确认调整"
      cancelText="取消"
      confirmLoading={submitting}
      okButtonProps={{ disabled: !canSubmit }}
      onCancel={onCancel}
      onOk={handleConfirm}
      destroyOnHidden
    >
      <div className="inventory-adjustment-modal">
        <div className="inventory-adjustment-summary">
          <div>
            <span>商品编号</span>
            <strong>{productLabel}</strong>
          </div>
          <div>
            <span>当前库存</span>
            <strong>{beforeCartonCount} 箱</strong>
          </div>
        </div>

        <Typography.Text type="secondary" className="inventory-adjustment-note">
          请输入盘点后的实际库存箱数，系统会自动计算差异。
        </Typography.Text>

        <Form
          form={form}
          layout="vertical"
          initialValues={{
            packagingId:
              product.packagings.length === 1
                ? product.packagings[0].id
                : undefined,
            actualCartonCount: undefined,
            remark: "",
          }}
          requiredMark={false}
        >
          {product.packagings.length > 1 ? (
            <Form.Item
              name="packagingId"
              label="包装规格"
              rules={[{ required: true, message: "请选择包装规格" }]}
            >
              <Select
                placeholder="请选择包装规格"
                options={product.packagings.map((packaging) => ({
                  label: packagingLabel(packaging, product.unit),
                  value: packaging.id,
                }))}
                aria-label="选择调整包装规格"
                onChange={() => form.setFieldValue("actualCartonCount", undefined)}
              />
            </Form.Item>
          ) : (
            <>
              <Form.Item name="packagingId" hidden>
                <InputNumber />
              </Form.Item>
              <div className="inventory-adjustment-packaging-fixed">
                <span>包装规格</span>
                <strong>
                  {selectedPackaging
                    ? packagingLabel(selectedPackaging, product.unit)
                    : "—"}
                </strong>
              </div>
            </>
          )}

          <Form.Item
            name="actualCartonCount"
            label="实际库存"
            rules={[
              { required: true, message: "请输入实际库存箱数" },
              {
                validator: async (_rule, value: number | undefined) => {
                  if (
                    value !== undefined &&
                    (!Number.isInteger(value) || value < 0)
                  ) {
                    throw new Error("实际库存必须是大于等于 0 的整数");
                  }
                },
              },
            ]}
          >
            <InputNumber
              min={0}
              precision={0}
              controls
              style={{ width: "100%" }}
              aria-label="实际库存箱数"
            />
          </Form.Item>

          <div
            className={`inventory-adjustment-difference ${
              delta === null
                ? ""
                : delta > 0
                  ? "inventory-adjustment-difference-increase"
                  : delta < 0
                    ? "inventory-adjustment-difference-decrease"
                    : "inventory-adjustment-difference-zero"
            }`}
            aria-live="polite"
          >
            <span>差异</span>
            <strong>{formatDelta(delta)}</strong>
          </div>

          {delta !== null && delta !== 0 && (
            <div className="inventory-adjustment-preview">
              库存将从 {beforeCartonCount} 箱调整为 {validActualCartonCount} 箱，差异{" "}
              {formatDelta(delta)}。
            </div>
          )}
          {delta === 0 && (
            <div className="inventory-adjustment-same-note">
              实际库存与当前库存一致，无需调整。
            </div>
          )}

          <Form.Item
            name="remark"
            label="调整原因"
            rules={[
              { required: true, message: "请填写调整原因" },
              {
                validator: async (_rule, value: string | undefined) => {
                  if (!value?.trim()) {
                    throw new Error("调整原因不能为空");
                  }
                },
              },
            ]}
          >
            <Input.TextArea
              rows={3}
              maxLength={2000}
              showCount
              placeholder="例如：盘点纠正、破损、历史库存核对"
              aria-label="调整原因"
            />
          </Form.Item>
        </Form>
      </div>
    </Modal>
  );
}
