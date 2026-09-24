"use client";

import { Alert, Cascader, Modal } from "antd";
import { useMemo, useState } from "react";
import { toCategoryOptions } from "@/lib/categories";
import type {
  CategorySelection,
  CategoryTreeNode,
} from "@/types/inventory";

interface BatchCategoryModalProps {
  open: boolean;
  selectedCount: number;
  categories: CategoryTreeNode[];
  categoriesLoading: boolean;
  categoriesError: string | null;
  confirmLoading: boolean;
  onCancel: () => void;
  onConfirm: (categoryId: number) => Promise<void>;
}

export default function BatchCategoryModal({
  open,
  selectedCount,
  categories,
  categoriesLoading,
  categoriesError,
  confirmLoading,
  onCancel,
  onConfirm,
}: BatchCategoryModalProps) {
  const [categoryPath, setCategoryPath] = useState<CategorySelection>([]);
  const [validationError, setValidationError] = useState<string | null>(null);
  const categoryOptions = useMemo(
    () => toCategoryOptions(categories),
    [categories],
  );

  async function handleConfirm() {
    const categoryId = categoryPath[categoryPath.length - 1];
    if (categoryPath.length !== 2 || typeof categoryId !== "number") {
      setValidationError("请选择二级分类");
      return;
    }

    setValidationError(null);
    await onConfirm(categoryId);
  }

  return (
    <Modal
      title="修改分类"
      open={open}
      okText="确认修改"
      cancelText="取消"
      confirmLoading={confirmLoading}
      okButtonProps={{
        disabled: categoriesLoading || Boolean(categoriesError),
      }}
      onCancel={onCancel}
      onOk={handleConfirm}
    >
      <div className="batch-category-modal-content">
        <p>将 {selectedCount} 个商品修改为：</p>
        <Cascader
          className="batch-category-picker"
          options={categoryOptions}
          value={categoryPath}
          loading={categoriesLoading}
          disabled={categoriesLoading || Boolean(categoriesError)}
          changeOnSelect
          expandTrigger="click"
          showSearch
          placeholder="请选择二级分类"
          aria-label="批量修改商品分类"
          displayRender={(labels) => labels.join(" / ")}
          onChange={(value) => {
            setCategoryPath((value ?? []) as CategorySelection);
            setValidationError(null);
          }}
        />
        {validationError && (
          <Alert
            className="batch-category-validation"
            type="error"
            showIcon
            title={validationError}
          />
        )}
        {categoriesError && (
          <Alert
            className="batch-category-validation"
            type="error"
            showIcon
            title="分类服务暂不可用"
            description={categoriesError}
          />
        )}
      </div>
    </Modal>
  );
}
