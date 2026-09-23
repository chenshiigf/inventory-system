"use client";

import {
  DeleteOutlined,
  PlusOutlined,
  PictureOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App,
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
  const { modal } = App.useApp();
  const selectedUnit = Form.useWatch("unit", form) ?? product?.unit ?? "—";
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
        className="product-editor-modal"
        title={title}
        open
        width={980}
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
                  unit: product.unit,
                  price: product.price,
                  packagings: product.packagings.map((packaging) => ({
                    id: packaging.id,
                    packingQty: packaging.packingQty,
                    cartonCount: packaging.cartonCount,
                  })),
                  remark: product.remark,
                }
              : {
                  categoryPath: [],
                  warehouseId: defaultWarehouseId,
                  imagePath: null,
                  thumbnailPath: null,
                  size: "",
                  unit: "pcs",
                  price: "0.00",
                  packagings: [{ packingQty: 1, cartonCount: 0 }],
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
          <div className="product-editor-overview">
            <div className="product-editor-image-column">
              <div className="product-editor-field-label">商品图片</div>
              <div className="product-image-editor">
                <div className="product-image-editor-preview">
                  {imagePath ? (
                    <ProductImage
                      key={imagePath}
                      imagePath={imagePath}
                      alt="商品图片预览"
                      width={126}
                      height={126}
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
                    JPG、PNG、WEBP，单张不超过 10MB。
                  </span>
                </div>
              </div>
            </div>
            <div className="product-editor-info-column">
              <div className="product-editor-grid product-editor-code-grid">
                <div className="product-editor-readonly-field">
                  <span className="product-editor-field-label">商品编号</span>
                  <Input
                    value={product ? product.productCode ?? "未分配" : "保存后生成"}
                    disabled
                    aria-label="只读商品编号"
                  />
                </div>
                <Form.Item
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
              </div>
              <Form.Item
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
              <div className="product-editor-compact-grid">
                <Form.Item name="size" label="尺寸">
                  <Input placeholder="例如 18 × 18 cm" maxLength={200} />
                </Form.Item>
                <Form.Item name="unit" label="单位">
                  <Select
                    allowClear
                    placeholder="可不填写"
                    options={[
                      { label: "pcs", value: "pcs" },
                      { label: "set", value: "set" },
                    ]}
                  />
                </Form.Item>
                <Form.Item name="price" label="单价">
                  <InputNumber
                    min={0}
                    precision={2}
                    stringMode
                    style={{ width: "100%" }}
                    placeholder="可不填写"
                  />
                </Form.Item>
              </div>
            </div>
          </div>
          <Form.Item
            label="包装规格"
            required
            className="product-editor-section packaging-form-item"
          >
            <Form.List name="packagings">
              {(fields, { add, remove }) => (
                <div className="packaging-editor-list">
                  {fields.map((field, index) => {
                    const packaging = form.getFieldValue([
                      "packagings",
                      field.name,
                    ]) as
                      | { cartonCount?: number }
                      | undefined;
                    const cartonCount = packaging?.cartonCount ?? 0;

                    function removePackaging() {
                      if (fields.length === 1) {
                        return;
                      }
                      if (cartonCount > 0) {
                        modal.confirm({
                          title: "确认删除包装规格？",
                          content: `该包装规格当前还有 ${cartonCount} 箱库存，删除后这部分库存记录将被移除，是否继续？`,
                          okText: "继续删除",
                          cancelText: "取消",
                          onOk: () => remove(field.name),
                        });
                        return;
                      }
                      remove(field.name);
                    }

                    return (
                      <div className="packaging-editor-row" key={field.key}>
                        <span className="packaging-editor-index">
                          {index + 1}
                        </span>
                        <Form.Item
                          name={[field.name, "packingQty"]}
                          rules={[
                            ...(fields.length > 1
                              ? [{ required: true, message: "请输入装箱数" }]
                              : []),
                            {
                              type: "number",
                              min: 1,
                              transform: (value) => value ?? undefined,
                              message: "装箱数必须是正整数",
                            },
                          ]}
                          className="packaging-editor-quantity"
                        >
                          <InputNumber
                            min={1}
                            precision={0}
                            placeholder="每箱数量"
                            aria-label={`第 ${index + 1} 个包装规格的装箱数`}
                          />
                        </Form.Item>
                        <span className="packaging-editor-unit">
                          {selectedUnit}/箱
                        </span>
                        <Form.Item
                          name={[field.name, "cartonCount"]}
                          rules={[
                            { required: true, message: "请输入当前箱数" },
                            {
                              type: "number",
                              min: 0,
                              transform: (value) => value ?? undefined,
                              message: "当前箱数不能小于 0",
                            },
                          ]}
                          className="packaging-editor-cartons"
                        >
                          <InputNumber
                            min={0}
                            precision={0}
                            placeholder="当前箱数"
                            aria-label={`第 ${index + 1} 个包装规格的当前箱数`}
                          />
                        </Form.Item>
                        <span className="packaging-editor-unit">箱</span>
                        <Button
                          type="link"
                          danger
                          icon={<DeleteOutlined />}
                          disabled={fields.length === 1}
                          onClick={removePackaging}
                          aria-label={`删除第 ${index + 1} 个包装规格`}
                        >
                          删除
                        </Button>
                      </div>
                    );
                  })}
                  <Button
                    type="dashed"
                    icon={<PlusOutlined />}
                    onClick={() => add({ packingQty: 1, cartonCount: 0 })}
                  >
                    添加包装规格
                  </Button>
                </div>
              )}
            </Form.List>
          </Form.Item>

          <Form.Item name="remark" label="备注" className="product-editor-remark">
            <Input.TextArea
              autoSize={{ minRows: 2, maxRows: 5 }}
              maxLength={2000}
              showCount
            />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
}
