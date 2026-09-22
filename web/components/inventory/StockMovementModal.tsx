"use client";

import { PictureOutlined } from "@ant-design/icons";
import {
  AutoComplete,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Typography,
} from "antd";
import ProductImage from "@/components/inventory/ProductImage";
import type {
  InventoryPackaging,
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

interface StockMovementFormValues {
  packagingId?: number;
  packingQty?: string;
  quantity?: number;
  note?: string;
}

function parsePositiveInteger(value: unknown): number | null {
  const text =
    typeof value === "string"
      ? value
      : typeof value === "number"
        ? String(value)
        : "";
  if (!/^[1-9]\d*$/.test(text)) {
    return null;
  }

  const parsed = Number(text);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export default function StockMovementModal({
  product,
  direction,
  onCancel,
  onConfirm,
}: StockMovementModalProps) {
  const [form] = Form.useForm<StockMovementFormValues>();
  const isStockIn = direction === "in";
  const isMultiplePackaging = product.packagings.length > 1;
  const selectedPackagingId = Form.useWatch("packagingId", form);
  const packingQtyValue = Form.useWatch("packingQty", form);
  const quantity = Form.useWatch("quantity", form);
  const defaultPackaging = product.packagings[0];
  const selectedPackingQty = parsePositiveInteger(packingQtyValue);
  const selectedPackaging = isStockIn
    ? product.packagings.find(
        (packaging) => packaging.packingQty === selectedPackingQty,
      )
    : product.packagings.find(
        (packaging) => packaging.id === selectedPackagingId,
      ) ?? (!isMultiplePackaging ? defaultPackaging : undefined);
  const currentCartonCount = selectedPackaging?.cartonCount ?? 0;
  const validQuantity =
    typeof quantity === "number" && Number.isInteger(quantity) && quantity > 0
      ? quantity
      : 0;
  const expectedCartonCount = isStockIn
    ? currentCartonCount + validQuantity
    : selectedPackaging
      ? selectedPackaging.cartonCount - validQuantity
      : 0;
  const isNewPackaging =
    isStockIn && selectedPackingQty !== null && !selectedPackaging;
  const title = isStockIn ? "入库" : "出库";

  async function handleConfirm() {
    try {
      const values = await form.validateFields();
      const packingQty = isStockIn
        ? parsePositiveInteger(values.packingQty)
        : selectedPackaging?.packingQty;
      const packagingId = selectedPackaging?.id;

      if (!packingQty || (!isStockIn && !packagingId) || !values.quantity) {
        return;
      }

      onConfirm({
        packagingId,
        packingQty,
        quantity: values.quantity,
        note: values.note,
        isNewPackaging,
      });
    } catch {
      // Keep the dialog open so Form can show the invalid field.
    }
  }

  function packagingLabel(packaging: InventoryPackaging) {
    return `${packaging.packingQty} ${product.unit}/箱（当前 ${packaging.cartonCount} 箱）`;
  }

  const packagingOptions = product.packagings.map((packaging) => ({
    label: packagingLabel(packaging),
    value: String(packaging.packingQty),
  }));
  const packagingDescription = isStockIn
    ? selectedPackingQty === null
      ? "请选择或输入装箱数"
      : isNewPackaging
        ? `将新增包装规格：${selectedPackingQty} ${product.unit}/箱`
        : `${selectedPackingQty} ${product.unit}/箱`
    : selectedPackaging
      ? `${selectedPackaging.packingQty} ${product.unit}/箱`
      : "请选择装箱数";

  return (
    <Modal
      title={title}
      open
      width={520}
      okText={isStockIn ? "确认入库" : "确认出库"}
      cancelText="取消"
      okButtonProps={{
        disabled:
          !isStockIn &&
          (!selectedPackaging || selectedPackaging.cartonCount <= 0),
      }}
      onCancel={onCancel}
      onOk={handleConfirm}
      destroyOnHidden
    >
      <div className="stock-product-summary">
        <div className="stock-product-image">
          {product.imagePath ? (
            <ProductImage
              imagePath={product.imagePath}
              thumbnailPath={product.thumbnailPath}
              alt={`${product.size || "商品"}图片`}
              width={90}
              height={90}
              loading="eager"
            />
          ) : (
            <PictureOutlined aria-label="商品图片暂未接入" />
          )}
        </div>
        <div className="stock-product-current">
          <span className="stock-product-current-label">
            {isMultiplePackaging ? "当前包装库存" : "当前库存"}
          </span>
          <strong className="stock-product-current-value">
            {currentCartonCount}
            <span>箱</span>
          </strong>
          <Typography.Text
            type={isNewPackaging ? undefined : "secondary"}
            className={isNewPackaging ? "stock-new-packaging-note" : undefined}
          >
            {packagingDescription}
          </Typography.Text>
          {isMultiplePackaging && (
            <span className="stock-product-total">
              商品合计 {product.totalCartonCount} 箱
            </span>
          )}
        </div>
      </div>

      <Typography.Paragraph className="stock-prototype-note" type="secondary">
        入库和出库目前只是页面原型，确认后仅临时改变本页数字；刷新后会恢复数据库中的箱数。
      </Typography.Paragraph>

      <Form
        form={form}
        layout="vertical"
        initialValues={{
          packagingId:
            !isStockIn && !isMultiplePackaging
              ? defaultPackaging?.id
              : undefined,
          packingQty:
            isStockIn && !isMultiplePackaging
              ? String(defaultPackaging?.packingQty ?? "")
              : undefined,
          quantity: 1,
          note: "",
        }}
        requiredMark={false}
      >
        {isStockIn ? (
          <Form.Item label="装箱数" required>
            <Space.Compact style={{ width: "100%" }}>
              <Form.Item
                name="packingQty"
                noStyle
                rules={[
                  { required: true, message: "请选择或输入装箱数" },
                  {
                    validator: async (_rule, value: string | undefined) => {
                      if (!value) {
                        return;
                      }
                      if (parsePositiveInteger(value) === null) {
                        throw new Error(
                          "装箱数必须是大于 0 的整数，只能输入数字",
                        );
                      }
                    },
                  },
                ]}
              >
                <AutoComplete
                  options={packagingOptions}
                  placeholder="请选择或输入装箱数"
                  filterOption={(inputValue, option) =>
                    String(option?.value ?? "").includes(inputValue)
                  }
                  style={{ flex: 1 }}
                  aria-label="装箱数"
                />
              </Form.Item>
              <Input
                readOnly
                value={`${product.unit}/箱`}
                aria-label="装箱数单位"
                tabIndex={-1}
                style={{ width: 72, flex: "0 0 72px", textAlign: "center" }}
              />
            </Space.Compact>
          </Form.Item>
        ) : isMultiplePackaging ? (
          <Form.Item
            name="packagingId"
            label="装箱数"
            rules={[{ required: true, message: "请选择装箱数" }]}
          >
            <Select
              placeholder="请选择装箱数"
              options={product.packagings.map((packaging) => ({
                label: packagingLabel(packaging),
                value: packaging.id,
              }))}
              aria-label="选择装箱数"
            />
          </Form.Item>
        ) : (
          <div className="stock-packaging-fixed" aria-label="装箱数">
            <span>装箱数</span>
            <strong>
              {defaultPackaging
                ? `${defaultPackaging.packingQty} ${product.unit}/箱`
                : "未设置"}
            </strong>
          </div>
        )}
        <Form.Item label={isStockIn ? "本次入库" : "本次出库"} required>
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
                    if (
                      !isStockIn &&
                      selectedPackaging &&
                      value > selectedPackaging.cartonCount
                    ) {
                      throw new Error("出库数不能超过当前包装库存");
                    }
                  },
                },
              ]}
            >
              <InputNumber
                min={1}
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
        {(isStockIn ? selectedPackingQty !== null : Boolean(selectedPackaging)) && (
          <div className="stock-preview-line">
            当前 {currentCartonCount} 箱 · {isStockIn ? "本次入库" : "本次出库"} {validQuantity} 箱 · 预计 {Math.max(expectedCartonCount, 0)} 箱
          </div>
        )}
        <div className="stock-expected-count">
          操作后预计：<strong>{Math.max(expectedCartonCount, 0)}</strong> 箱
        </div>
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
