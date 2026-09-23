"use client";

import { Button, Dropdown, Popover, Space, Table, Tag, Tooltip } from "antd";
import { MoreOutlined } from "@ant-design/icons";
import type { ColumnsType, TablePaginationConfig } from "antd/es/table";
import Link from "next/link";
import ProductImage from "@/components/inventory/ProductImage";
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
  onViewMovements: (product: InventoryProduct) => void;
  onAdjust: (product: InventoryProduct) => void;
  onEdit: (product: InventoryProduct) => void;
  onDeactivate: (product: InventoryProduct) => void;
  onActivate: (product: InventoryProduct) => void;
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
  onViewMovements,
  onAdjust,
  onEdit,
  onDeactivate,
  onActivate,
}: ProductTableProps) {
  function renderPackagingSummary(product: InventoryProduct) {
    const packagings = [...product.packagings].sort(
      (left, right) => left.sortOrder - right.sortOrder,
    );
    const isMultiple = packagings.length > 1;
    const summary = packagings
      .map((packaging) => packaging.packingQty ?? "—")
      .join(" / ");
    const details = (
      <div className="packaging-popover-content">
        {packagings.map((packaging) => (
          <div key={packaging.id}>
            {packaging.packingQty ?? "未填写"} {product.unit ?? "—"}/箱 × {packaging.cartonCount}箱
          </div>
        ))}
      </div>
    );

    if (!isMultiple) {
      return <span className="table-secondary-value">{summary || "—"}</span>;
    }

    return (
      <Popover title="包装规格" content={details} trigger="hover">
        <span className="packaging-summary" tabIndex={0}>
          <span className="table-secondary-value">{summary}</span>
          <span className="packaging-summary-count">{packagings.length}种包装</span>
        </span>
      </Popover>
    );
  }

  const columns: ColumnsType<InventoryProduct> = [
    {
      title: "商品编号",
      dataIndex: "productCode",
      key: "productCode",
      width: 120,
      render: (value: string | null, product) => (
        <div className="product-code-cell">
          <span
            className={value ? "product-code-value" : "product-code-empty"}
            title={value ?? undefined}
          >
            <Link
              href={`/products/${product.id}`}
              className="product-detail-link"
              aria-label={`查看商品 ${(value ?? product.size) || "详情"}`}
            >
              {value ?? "—"}
            </Link>
          </span>
          {!product.isActive && (
            <Tag className="product-status-tag" color="default">
              已停用
            </Tag>
          )}
        </div>
      ),
    },
    {
      title: "商品图片",
      dataIndex: "imagePath",
      key: "image",
      width: 120,
      render: (_imagePath, product) => (
        <div className="product-image-frame">
          <Link
            href={`/products/${product.id}`}
            className="product-image-detail-link"
            aria-label={`查看商品 ${(product.productCode ?? product.size) || "详情"}`}
          >
            <ProductImage
              key={`${product.id}:${product.imagePath ?? ""}:${product.thumbnailPath ?? ""}`}
              imagePath={product.imagePath}
              thumbnailPath={product.thumbnailPath}
              alt={`${product.productCode ?? (product.size || "商品")}商品图片`}
              width={108}
              height={82}
              loading="lazy"
              hoverPreview
              enablePreview={false}
            />
          </Link>
        </div>
      ),
    },
    {
      title: "尺寸",
      dataIndex: "size",
      key: "size",
      width: 130,
      render: (value: string | null) => (
        <span className="table-secondary-value">{value || "—"}</span>
      ),
    },
    {
      title: "装箱数",
      key: "packagings",
      width: 120,
      render: (_value, product) => renderPackagingSummary(product),
    },
    {
      title: "单位",
      dataIndex: "unit",
      key: "unit",
      width: 80,
      render: (value: string | null) => (
        <span className="table-secondary-value">{value || "—"}</span>
      ),
    },
    {
      title: "单价",
      dataIndex: "price",
      key: "price",
      width: 100,
      render: (value: string | null) => (
        <span className="table-price">{value ? `¥ ${value}` : "—"}</span>
      ),
    },
    {
      title: "当前箱数",
      key: "totalCartonCount",
      width: 120,
      render: (_value, product) => (
        <span className="stock-quantity">
          <strong>{product.totalCartonCount}</strong>
          <span>箱</span>
        </span>
      ),
    },
    {
      title: "备注",
      dataIndex: "remark",
      key: "remark",
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
      width: 150,
      render: (_value, product) => {
        const managementItems = product.isActive
          ? [
              { key: "movements", label: "库存流水" },
              { key: "adjust", label: "库存调整" },
              { key: "edit", label: "编辑" },
              { key: "deactivate", label: "停用", danger: true },
            ]
          : [
              { key: "movements", label: "库存流水" },
              { key: "adjust", label: "库存调整" },
              { key: "edit", label: "编辑" },
            ];

        return (
          <Space className="product-row-actions" size={4}>
            {product.isActive ? (
              <>
                <Button
                  type="link"
                  size="small"
                  aria-label={"入库尺寸 " + product.size + " 的商品"}
                  onClick={() => onStockIn(product)}
                >
                  入库
                </Button>
                <Button
                  type="link"
                  size="small"
                  aria-label={"出库尺寸 " + product.size + " 的商品"}
                  onClick={() => onStockOut(product)}
                >
                  出库
                </Button>
              </>
            ) : (
              <Button
                type="link"
                size="small"
                aria-label={"重新启用尺寸 " + product.size + " 的商品"}
                onClick={() => onActivate(product)}
              >
                重新启用
              </Button>
            )}
            <Dropdown
              trigger={["click"]}
              menu={{
                items: managementItems,
                onClick: ({ key }) => {
                  if (key === "movements") {
                    onViewMovements(product);
                  } else if (key === "adjust") {
                    onAdjust(product);
                  } else if (key === "edit") {
                    onEdit(product);
                  } else if (key === "deactivate") {
                    onDeactivate(product);
                  }
                },
              }}
            >
              <Button
                type="text"
                size="small"
                icon={<MoreOutlined />}
                aria-label={`${product.size || product.productCode || "商品"}的操作菜单`}
              />
            </Dropdown>
          </Space>
        );
      },
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
      pagination={getPaginationConfig(
        currentPage,
        pageSize,
        total,
        onPageChange,
      )}
      rowClassName={(product) => (product.isActive ? "" : "product-row-inactive")}
      locale={{ emptyText: "没有符合条件的商品" }}
    />
  );
}
