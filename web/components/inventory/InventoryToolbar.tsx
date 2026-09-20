"use client";

import { SearchOutlined } from "@ant-design/icons";
import { Cascader, Input, Select } from "antd";
import type {
  CategorySelection,
  InventoryCategoryOption,
  WarehouseRead,
  WarehouseSelection,
} from "@/types/inventory";

interface InventoryToolbarProps {
  warehouses: WarehouseRead[];
  warehouseValue: WarehouseSelection;
  onWarehouseChange: (value: WarehouseSelection) => void;
  warehouseDisabled?: boolean;
  categoryOptions: InventoryCategoryOption[];
  categoryValue: CategorySelection;
  onCategoryChange: (value: CategorySelection) => void;
  searchValue: string;
  onSearchChange: (value: string) => void;
  resultCount: number;
  categoryDisabled?: boolean;
}

export default function InventoryToolbar({
  warehouses,
  warehouseValue,
  onWarehouseChange,
  warehouseDisabled = false,
  categoryOptions,
  categoryValue,
  onCategoryChange,
  searchValue,
  onSearchChange,
  resultCount,
  categoryDisabled = false,
}: InventoryToolbarProps) {
  return (
    <div className="inventory-toolbar" aria-label="仓库、商品分类与搜索">
      <Select<WarehouseSelection>
        className="inventory-warehouse-picker"
        aria-label="选择仓库"
        value={warehouseValue}
        disabled={warehouseDisabled}
        options={[
          { label: "全部仓库", value: "all" },
          ...warehouses.map((warehouse) => ({
            label: warehouse.name,
            value: warehouse.id,
          })),
        ]}
        onChange={onWarehouseChange}
      />
      <Cascader
        className="inventory-category-picker"
        options={categoryOptions}
        value={categoryValue}
        disabled={categoryDisabled}
        changeOnSelect
        expandTrigger="click"
        showSearch
        placeholder="全部分类"
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
