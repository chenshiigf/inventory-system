"use client";

import { PictureOutlined } from "@ant-design/icons";
import {
  Form,
  Input,
  InputNumber,
  Modal,
  Space,
  Typography,
} from "antd";
import type {
  InventoryProduct,
  StockMovementDirection,
  StockMovementValues,
} from "@/types/inventory";

interface StockMovementModalProps {
  product: InventoryProduct;
  direction: StockMovementDirection;
  onCancel: () => void;
  onConfirm: (values: StockMovementValues) => void;
}

type StockMovementFormValues = StockMovementValues;

export default function StockMovementModal({
  product,
  direction,
  onCancel,
  onConfirm,
}: StockMovementModalProps) {
  const [form] = Form.useForm<StockMovementFormValues>();
  const isStockIn = direction === "in";
  const title = isStockIn ? "入库" : "出库";

  async function handleConfirm() {
    try {
      const values = await form.validateFields();
      onConfirm(values);
    } catch {
      // Keep the dialog open so Form can show the invalid field.
    }
  }

  return (
    <Modal
      title={title}
      open
      width={520}
      okText={isStockIn ? "确认入库" : "确认出库"}
      cancelText="取消"
      okButtonProps={{
        disabled: !isStockIn && product.cartonCount <= 0,
      }}
      onCancel={onCancel}
      onOk={handleConfirm}
      destroyOnHidden
    >
      <div className="stock-product-summary">
        <div className="stock-product-image">
          <PictureOutlined aria-label="商品图片暂未接入" />
        </div>
        <div className="stock-product-current">
          <span className="stock-product-current-label">当前库存</span>
          <strong className="stock-product-current-value">
            {product.cartonCount}
            <span>箱</span>
          </strong>
          <Typography.Text type="secondary">
            {product.size || "未填写尺寸"}
          </Typography.Text>
        </div>
      </div>

      <Typography.Paragraph className="stock-prototype-note" type="secondary">
        入库和出库目前只是页面原型，确认后仅临时改变本页数字；刷新后会恢复数据库中的箱数。
      </Typography.Paragraph>

      <Form
        form={form}
        layout="vertical"
        initialValues={{ quantity: 1, note: "" }}
        requiredMark={false}
      >
        <Form.Item
          label={isStockIn ? "本次入库" : "本次出库"}
          required
        >
          <Space.Compact style={{ width: "100%" }}>
            <Form.Item
              name="quantity"
              noStyle
              rules={[
                { required: true, message: "请输入箱数" },
                {
                  validator: async (_rule, value: number | null | undefined) => {
                    if (value === null || value === undefined) {
                      return;
                    }
                    if (!Number.isInteger(value) || value <= 0) {
                      throw new Error("箱数必须是大于 0 的整数");
                    }
                    if (!isStockIn && value > product.cartonCount) {
                      throw new Error("出库数不能超过当前库存");
                    }
                  },
                },
              ]}
            >
              <InputNumber
                min={1}
                max={isStockIn ? undefined : product.cartonCount}
                precision={0}
                controls
                style={{ width: "100%" }}
                aria-label={isStockIn ? "本次入库箱数" : "本次出库箱数"}
              />
            </Form.Item>
            <Input
              readOnly
              value="箱"
              aria-label="箱"
              style={{ width: 54, flex: "0 0 54px", textAlign: "center" }}
            />
          </Space.Compact>
        </Form.Item>
        <Form.Item name="note" label="备注">
          <Input.TextArea
            rows={3}
            maxLength={200}
            showCount
            placeholder="可选"
          />
        </Form.Item>
      </Form>
    </Modal>
  );
}
