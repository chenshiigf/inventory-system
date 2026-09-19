"use client";

import { PictureOutlined } from "@ant-design/icons";
import {
  Alert,
  Cascader,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
} from "antd";
import { useState } from "react";
import type {
  CategoryTreeNode,
  InventoryCategoryOption,
  InventoryProduct,
  ProductEditorFormValues,
} from "@/types/inventory";
import {
  getCategoryPath,
  hasSecondLevelCategories,
  toCategoryOptions,
} from "@/lib/categories";

interface ProductEditorModalProps {
  product?: InventoryProduct;
  categories: CategoryTreeNode[];
  onCancel: () => void;
  onSave: (values: ProductEditorFormValues) => Promise<void>;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "保存商品失败，请重试。";
}

export default function ProductEditorModal({
  product,
  categories,
  onCancel,
  onSave,
}: ProductEditorModalProps) {
  const [form] = Form.useForm<ProductEditorFormValues>();
  const [saving, setSaving] = useState(false);
  const [messageApi, messageContextHolder] = message.useMessage();
  const title = product ? "编辑商品" : "新增商品";
  const categoryOptions: InventoryCategoryOption[] = toCategoryOptions(categories);
  const categoryPath = getCategoryPath(categories, product?.categoryId ?? null);
  const hasCategories = hasSecondLevelCategories(categories);

  async function handleSave(values: ProductEditorFormValues) {
    setSaving(true);
    try {
      await onSave({
        ...values,
        size: values.size.trim(),
        remark: values.remark.trim(),
      });
    } catch (error) {
      messageApi.error(getErrorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {messageContextHolder}
      <Modal
        title={title}
        open
        width={650}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ loading: saving }}
        onCancel={onCancel}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Alert
          className="product-image-phase-note"
          type="info"
          showIcon
          title={
            product
              ? "本阶段暂不接入图片上传；商品图片路径不会被修改。"
              : "本阶段暂不接入图片上传；新增商品的图片路径为空。"
          }
        />
        <div className="product-image-editor">
          <div className="product-image-editor-preview">
            <PictureOutlined style={{ fontSize: 26 }} />
          </div>
          <span className="product-image-help">图片上传将在后续阶段接入。</span>
        </div>

        {product && product.categoryId === null && (
          <Alert
            className="product-category-note"
            type="warning"
            showIcon
            title="该商品目前未分类，请选择一个二级分类后保存。"
          />
        )}
        {!hasCategories && (
          <Alert
            className="product-category-note"
            type="info"
            showIcon
            title="暂无可选二级分类，请先到左侧分类管理新增一级和二级分类。"
          />
        )}

        <Form<ProductEditorFormValues>
          form={form}
          layout="vertical"
          initialValues={
            product
              ? {
                  categoryPath,
                  size: product.size,
                  packingQty: product.packingQty,
                  unit: product.unit,
                  price: product.price,
                  cartonCount: product.cartonCount,
                  remark: product.remark,
                }
              : {
                  categoryPath: [],
                  size: "",
                  packingQty: 1,
                  unit: "pcs",
                  price: "0.00",
                  cartonCount: 0,
                  remark: "",
                }
          }
          onFinish={handleSave}
          requiredMark={false}
        >
          <div className="form-grid">
            <Form.Item
              className="form-item-full"
              name="categoryPath"
              label="商品分类"
              rules={[
                { required: true, message: "请选择商品分类" },
                {
                  validator: (_rule, value: number[] | undefined) => {
                    if (!value?.length || value.length === 2) {
                      return Promise.resolve();
                    }
                    return Promise.reject(new Error("请选择一个二级分类"));
                  },
                },
              ]}
            >
              <Cascader
                options={categoryOptions}
                placeholder="请选择二级分类"
                displayRender={(labels) => labels.join(" / ")}
                aria-label="商品分类"
              />
            </Form.Item>
            <Form.Item name="size" label="尺寸">
              <Input placeholder="例如 18 × 18 cm" maxLength={200} />
            </Form.Item>
            <Form.Item
              name="packingQty"
              label="装箱数"
              rules={[{ required: true, message: "请输入装箱数" }]}
            >
              <InputNumber
                min={1}
                precision={0}
                style={{ width: "100%" }}
                placeholder="每箱数量"
              />
            </Form.Item>
            <Form.Item
              name="unit"
              label="单位"
              rules={[{ required: true, message: "请选择单位" }]}
            >
              <Select
                options={[
                  { label: "pcs", value: "pcs" },
                  { label: "set", value: "set" },
                ]}
              />
            </Form.Item>
            <Form.Item
              name="price"
              label="单价"
              rules={[{ required: true, message: "请输入单价" }]}
            >
              <InputNumber
                min={0}
                precision={2}
                stringMode
                style={{ width: "100%" }}
                placeholder="0.00"
              />
            </Form.Item>
            <Form.Item label="当前箱数" required>
              <Form.Item
                name="cartonCount"
                noStyle
                rules={[{ required: true, message: "请输入当前箱数" }]}
              >
                <InputNumber
                  min={0}
                  precision={0}
                  style={{ width: "100%" }}
                />
              </Form.Item>
            </Form.Item>
          </div>

          <Form.Item name="remark" label="备注">
            <Input.TextArea rows={3} maxLength={2000} showCount />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
