"use client";

import { SearchOutlined } from "@ant-design/icons";
import { Cascader, Input } from "antd";
import type {
  CategorySelection,
  InventoryCategoryOption,
} from "@/types/inventory";

interface InventoryToolbarProps {
  categoryOptions: InventoryCategoryOption[];
  categoryValue: CategorySelection;
  onCategoryChange: (value: CategorySelection) => void;
  searchValue: string;
  onSearchChange: (value: string) => void;
  resultCount: number;
  categoryDisabled?: boolean;
}

export default function InventoryToolbar({
  categoryOptions,
  categoryValue,
  onCategoryChange,
  searchValue,
  onSearchChange,
  resultCount,
  categoryDisabled = false,
}: InventoryToolbarProps) {
  return (
    <div className="inventory-toolbar" aria-label="商品分类与搜索">
      <Cascader
        className="inventory-category-picker"
        options={categoryOptions}
        value={categoryValue}
        disabled={categoryDisabled}
        changeOnSelect
        expandTrigger="click"
        showSearch
        placeholder="全部商品"
        aria-label="选择商品分类"
        displayRender={(labels) => labels.join(" / ")}
        onChange={(value) =>
          onCategoryChange((value ?? []) as CategorySelection)
        }
      />
      <Input
        className="inventory-search"
        allowClear
        maxLength={100}
        prefix={<SearchOutlined aria-hidden="true" />}
        placeholder="搜索尺寸或备注"
        aria-label="搜索商品"
        value={searchValue}
        onChange={(event) => onSearchChange(event.target.value)}
      />
      <span className="result-count toolbar-result-count">
        共 {resultCount} 个商品
      </span>
    </div>
  );
}
