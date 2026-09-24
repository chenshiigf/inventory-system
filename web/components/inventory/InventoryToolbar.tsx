"use client";

import { SearchOutlined } from "@ant-design/icons";
import { Cascader, Input, Select } from "antd";
import StockRangeFilter from "@/components/inventory/StockRangeFilter";
import type {
  CategorySelection,
  InventoryCategoryOption,
  ProductStatus,
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
  statusValue: ProductStatus;
  onStatusChange: (value: ProductStatus) => void;
  stockMin: number | null;
  stockMax: number | null;
  onStockRangeChange: (stockMin: number | null, stockMax: number | null) => void;
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
  statusValue,
  onStatusChange,
  stockMin,
  stockMax,
  onStockRangeChange,
  resultCount,
  categoryDisabled = false,
}: InventoryToolbarProps) {
  return (
    <div className="inventory-toolbar" aria-label="仓库、商品分类、状态、库存与搜索">
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
      <Select<ProductStatus>
        className="inventory-status-picker"
        aria-label="选择商品状态"
        value={statusValue}
        options={[
          { label: "在用商品", value: "active" },
          { label: "已停用", value: "inactive" },
          { label: "全部商品", value: "all" },
        ]}
        onChange={onStatusChange}
      />
      <StockRangeFilter
        stockMin={stockMin}
        stockMax={stockMax}
        onChange={onStockRangeChange}
      />
      <Input
        className="inventory-search"
        allowClear
        maxLength={100}
        prefix={<SearchOutlined aria-hidden="true" />}
        placeholder="搜索编号、尺寸或备注"
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
