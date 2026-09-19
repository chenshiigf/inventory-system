"use client";

import { PictureOutlined } from "@ant-design/icons";
import { Button, Space, Table, Tooltip } from "antd";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import type { InventoryProduct } from "@/types/inventory";

interface ProductTableProps {
  products: InventoryProduct[];
  total: number;
  loading: boolean;
  currentPage: number;
  pageSize: number;
  onPageChange: (currentPage: number, pageSize: number) => void;
  onStockIn: (product: InventoryProduct) => void;
  onStockOut: (product: InventoryProduct) => void;
  onEdit: (product: InventoryProduct) => void;
}

function getPaginationConfig(
  currentPage: number,
  pageSize: number,
  total: number,
  onPageChange: (currentPage: number, pageSize: number) => void,
): TablePaginationConfig {
  return {
    current: currentPage,
    pageSize,
    total,
    showSizeChanger: true,
    pageSizeOptions: [20, 50, 100],
    showTotal: (itemCount, range) =>
      range[0] === 0
        ? "暂无商品"
        : range[0] + "-" + range[1] + " 条，共 " + itemCount + " 条",
    onChange: onPageChange,
  };
}

export default function ProductTable({
  products,
  total,
  loading,
  currentPage,
  pageSize,
  onPageChange,
  onStockIn,
  onStockOut,
  onEdit,
}: ProductTableProps) {
  const columns: ColumnsType<InventoryProduct> = [
    {
      title: "商品图片",
      dataIndex: "imagePath",
      key: "image",
      width: 120,
      render: (_imagePath, product) => (
        <div className="product-image-frame">
          <span
            className="product-image-empty"
            title={product.imagePath ?? "本阶段暂未接入图片上传"}
            aria-label="商品图片暂未接入"
          >
            <PictureOutlined aria-hidden="true" />
            <span>图片待接入</span>
          </span>
        </div>
      ),
    },
    {
      title: "尺寸",
      dataIndex: "size",
      key: "size",
      width: 130,
      render: (value: string) => (
        <span className="table-secondary-value">{value || "—"}</span>
      ),
    },
    {
      title: "装箱数",
      dataIndex: "packingQty",
      key: "packingQty",
      width: 90,
      render: (value: number) => (
        <span className="table-secondary-value">{value}</span>
      ),
    },
    {
      title: "单位",
      dataIndex: "unit",
      key: "unit",
      width: 80,
      render: (value: string) => (
        <span className="table-secondary-value">{value || "—"}</span>
      ),
    },
    {
      title: "单价",
      dataIndex: "price",
      key: "price",
      width: 100,
      render: (value: string) => (
        <span className="table-price">¥ {value}</span>
      ),
    },
    {
      title: "当前箱数",
      dataIndex: "cartonCount",
      key: "cartonCount",
      width: 120,
      render: (value: number) => (
        <span className="stock-quantity">
          <strong>{value}</strong>
          <span>箱</span>
        </span>
      ),
    },
    {
      title: "备注",
      dataIndex: "remark",
      key: "remark",
      width: 210,
      render: (value: string | null) => {
        const note = value?.trim() ?? "";
        return note ? (
          <Tooltip title={note} mouseEnterDelay={0.2}>
            <span className="table-note-value">{note}</span>
          </Tooltip>
        ) : (
          <span className="table-note-empty">—</span>
        );
      },
    },
    {
      title: "操作",
      key: "actions",
      width: 180,
      render: (_value, product) => (
        <Space className="product-row-actions" size={4}>
          <Button
            type="link"
            size="small"
            aria-label={"为尺寸 " + product.size + " 的商品入库"}
            onClick={() => onStockIn(product)}
          >
            入库
          </Button>
          <Button
            type="link"
            size="small"
            aria-label={"为尺寸 " + product.size + " 的商品出库"}
            onClick={() => onStockOut(product)}
          >
            出库
          </Button>
          <Button
            type="link"
            size="small"
            aria-label={"编辑尺寸 " + product.size + " 的商品"}
            onClick={() => onEdit(product)}
          >
            编辑
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <Table<InventoryProduct>
      className="inventory-table"
      rowKey="id"
      columns={columns}
      dataSource={products}
      loading={loading}
      tableLayout="fixed"
      scroll={{ x: 1030 }}
      pagination={getPaginationConfig(
        currentPage,
        pageSize,
        total,
        onPageChange,
      )}
      locale={{ emptyText: "没有符合条件的商品" }}
    />
  );
}
