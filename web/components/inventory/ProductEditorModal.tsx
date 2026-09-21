"use client";

import { PictureOutlined, UploadOutlined } from "@ant-design/icons";
import {
  Alert,
  Cascader,
  Button,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Select,
  Upload,
} from "antd";
import type { UploadProps } from "antd";
import { useState } from "react";
import type {
  CategoryTreeNode,
  InventoryCategoryOption,
  InventoryProduct,
  ProductEditorFormValues,
  WarehouseRead,
} from "@/types/inventory";
import {
  getCategoryPath,
  hasSecondLevelCategories,
  toCategoryOptions,
} from "@/lib/categories";
import {
  MAX_PRODUCT_IMAGE_BYTES,
  uploadProductImage,
} from "@/lib/api/product-images";
import ProductImage from "@/components/inventory/ProductImage";

interface ProductEditorModalProps {
  product?: InventoryProduct;
  categories: CategoryTreeNode[];
  warehouses: WarehouseRead[];
  defaultWarehouseId?: number;
  onCancel: () => void;
  onSave: (values: ProductEditorFormValues) => Promise<void>;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "保存商品失败，请重试。";
}

export default function ProductEditorModal({
  product,
  categories,
  warehouses,
  defaultWarehouseId,
  onCancel,
  onSave,
}: ProductEditorModalProps) {
  const [form] = Form.useForm<ProductEditorFormValues>();
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [imagePath, setImagePath] = useState(product?.imagePath ?? null);
  const [messageApi, messageContextHolder] = message.useMessage();
  const title = product ? "编辑商品" : "新增商品";
  const categoryOptions: InventoryCategoryOption[] = toCategoryOptions(categories);
  const categoryPath = getCategoryPath(categories, product?.categoryId ?? null);
  const hasCategories = hasSecondLevelCategories(categories);

  const beforeUpload: UploadProps["beforeUpload"] = (file) => {
    const extension = file.name.split(".").pop()?.toLowerCase();
    const allowedExtensions = new Set(["jpg", "jpeg", "png", "webp"]);
    const allowedMimeTypes = new Set([
      "image/jpeg",
      "image/png",
      "image/webp",
    ]);

    if (
      !extension ||
      !allowedExtensions.has(extension) ||
      (file.type !== "" && !allowedMimeTypes.has(file.type))
    ) {
      messageApi.error("仅支持 JPG、PNG、WEBP 格式的图片。");
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_PRODUCT_IMAGE_BYTES) {
      messageApi.error("图片不能超过 10MB。");
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  const customRequest: UploadProps["customRequest"] = async (options) => {
    setUploading(true);
    try {
      const result = await uploadProductImage(options.file as File);
      setImagePath(result.image_path);
      form.setFieldsValue({
        imagePath: result.image_path,
        thumbnailPath: result.thumbnail_path,
      });
      options.onSuccess?.(result);
      messageApi.success("商品图片已上传并完成处理");
    } catch (error) {
      const uploadError =
        error instanceof Error ? error : new Error("图片上传失败，请重试。");
      options.onError?.(uploadError);
      messageApi.error(uploadError.message || "图片上传失败，请重试。");
    } finally {
      setUploading(false);
    }
  };

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
        okButtonProps={{ loading: saving || uploading, disabled: uploading }}
        cancelButtonProps={{ disabled: uploading }}
        closable={!uploading}
        mask={{ closable: !uploading }}
        onCancel={onCancel}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        {product && product.categoryId === null && (
          <Alert
            className="product-category-note"
            type="warning"
            showIcon
            title="该商品目前未分类，请选择一个二级分类后保存。"
          />
        )}
        {product && product.warehouseId === null && (
          <Alert
            className="product-category-note"
            type="warning"
            showIcon
            title="该商品目前未选择仓库，请选择所属仓库后保存。"
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
                  warehouseId: product.warehouseId ?? undefined,
                  imagePath: product.imagePath,
                  thumbnailPath: product.thumbnailPath,
                  size: product.size,
                  packingQty: product.packingQty,
                  unit: product.unit,
                  price: product.price,
                  cartonCount: product.cartonCount,
                  remark: product.remark,
                }
              : {
                  categoryPath: [],
                  warehouseId: defaultWarehouseId,
                  imagePath: null,
                  thumbnailPath: null,
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
          <Form.Item name="imagePath" hidden>
            <Input />
          </Form.Item>
          <Form.Item name="thumbnailPath" hidden>
            <Input />
          </Form.Item>
          <Form.Item label="商品图片" className="form-item-full">
            <div className="product-image-editor">
              <div className="product-image-editor-preview">
                {imagePath ? (
                  <ProductImage
                    key={imagePath}
                    imagePath={imagePath}
                    alt="商品图片预览"
                    width={90}
                    height={90}
                    loading="eager"
                  />
                ) : (
                  <span className="product-image-empty" aria-label="暂无图片">
                    <PictureOutlined aria-hidden="true" />
                    <span>暂无图片</span>
                  </span>
                )}
              </div>
              <div className="product-image-editor-controls">
                <Upload
                  accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
                  maxCount={1}
                  showUploadList={false}
                  disabled={uploading}
                  beforeUpload={beforeUpload}
                  customRequest={customRequest}
                >
                  <Button
                    icon={<UploadOutlined />}
                    loading={uploading}
                    disabled={uploading}
                  >
                    {imagePath ? "更换图片" : "上传图片"}
                  </Button>
                </Upload>
                <span className="product-image-help">
                  支持 JPG、PNG、WEBP，单张不超过 10MB。上传后自动生成主图和缩略图。
                </span>
              </div>
            </div>
          </Form.Item>
          {product && (
            <Form.Item label="商品编号" className="form-item-full">
              <Input
                value={product.productCode ?? "未分配"}
                readOnly
                aria-label="只读商品编号"
              />
            </Form.Item>
          )}
          <div className="form-grid">
            <Form.Item
              className="form-item-full"
              name="warehouseId"
              label="所属仓库"
              rules={[{ required: true, message: "请选择所属仓库" }]}
            >
              <Select
                aria-label="商品所属仓库"
                placeholder={
                  product?.warehouseId === null ? "未选择仓库" : "请选择仓库"
                }
                options={warehouses.map((warehouse) => ({
                  label: warehouse.name,
                  value: warehouse.id,
                }))}
              />
            </Form.Item>
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
