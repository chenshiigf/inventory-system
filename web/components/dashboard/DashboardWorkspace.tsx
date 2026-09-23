"use client";

import {
  AppstoreAddOutlined,
  DashboardOutlined,
  FileExcelOutlined,
  HistoryOutlined,
  PlusOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App,
  Card,
  Empty,
  Progress,
  Skeleton,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import ProductImage from "@/components/inventory/ProductImage";
import {
  formatMovementQuantity,
  formatMovementTime,
  getMovementDelta,
  getMovementLabel,
} from "@/components/inventory/inventory-movement-utils";
import { getDashboardSummary } from "@/lib/api/dashboard";
import type {
  DashboardCategoryDistribution,
  DashboardSummary,
  DashboardWarehouseDistribution,
  DashboardZeroStockProduct,
  InventoryMovementApiRecord,
} from "@/types/inventory";

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "概览数据加载失败，请重试。";
}

function getMovementTagColor(type: InventoryMovementApiRecord["movement_type"]): string {
  if (type === "IN") {
    return "green";
  }
  if (type === "OUT") {
    return "red";
  }
  return "blue";
}

function DashboardCardSkeleton({ rows = 2 }: { rows?: number }) {
  return <Skeleton active title={{ width: "38%" }} paragraph={{ rows }} />;
}

function MetricCard({
  label,
  value,
  suffix,
  loading,
}: {
  label: string;
  value: number | null;
  suffix?: string;
  loading: boolean;
}) {
  return (
    <Card className="dashboard-metric-card" size="small">
      {loading ? (
        <Skeleton active title={{ width: "44%" }} paragraph={{ rows: 1 }} />
      ) : (
        <div className="dashboard-metric-content">
          <span className="dashboard-metric-label">{label}</span>
          <span className="dashboard-metric-value">
            {value === null ? "—" : value.toLocaleString("zh-CN")}
            {suffix && <small>{suffix}</small>}
          </span>
        </div>
      )}
    </Card>
  );
}

function MovementProduct({ movement }: { movement: InventoryMovementApiRecord }) {
  return (
    <div className="dashboard-movement-product">
      <div className="dashboard-movement-image">
        <ProductImage
          imagePath={movement.image_path}
          thumbnailPath={movement.thumbnail_path}
          alt={`${movement.product_code ?? "商品"}图片`}
          width={38}
          height={38}
        />
      </div>
      <span
        className={movement.product_code ? "product-code-value" : "product-code-empty"}
        title={movement.product_code ?? undefined}
      >
        {movement.product_code ?? "未编号商品"}
      </span>
    </div>
  );
}

function ZeroStockProductRow({ product }: { product: DashboardZeroStockProduct }) {
  return (
    <div className="dashboard-zero-stock-row">
      <div className="dashboard-zero-stock-image">
        <ProductImage
          imagePath={product.image_path}
          thumbnailPath={product.thumbnail_path}
          alt={`${product.product_code ?? "商品"}图片`}
          width={42}
          height={42}
        />
      </div>
      <div className="dashboard-zero-stock-info">
        <strong className={product.product_code ? "product-code-value" : "product-code-empty"}>
          {product.product_code ?? "未编号商品"}
        </strong>
        <span>{product.category_name}</span>
      </div>
      <span className="dashboard-zero-stock-warehouse">
        {product.warehouse_name ?? "未指定"}
      </span>
    </div>
  );
}

type DistributionItem =
  | DashboardCategoryDistribution
  | DashboardWarehouseDistribution;

function DistributionRows<T extends DistributionItem>({
  items,
  getName,
  getValue,
  formatValue,
  progressTotal,
  emptyDescription,
}: {
  items: T[];
  getName: (item: T) => string;
  getValue: (item: T) => number;
  formatValue: (value: number, percent: number) => string;
  progressTotal?: number;
  emptyDescription: string;
}) {
  if (items.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyDescription} />;
  }

  const maxValue = Math.max(...items.map(getValue), 1);
  const total = progressTotal ?? maxValue;
  return (
    <div className="dashboard-distribution-list">
      {items.map((item) => {
        const value = getValue(item);
        const percent = total > 0 ? Math.round((value / total) * 100) : 0;
        return (
          <div
            className="dashboard-distribution-row"
            key={`${getName(item)}-${value}`}
          >
            <div className="dashboard-distribution-heading">
              <span className="dashboard-distribution-name" title={getName(item)}>
                {getName(item)}
              </span>
              <span className="dashboard-distribution-value">
                {formatValue(value, percent)}
              </span>
            </div>
            <Progress
              percent={progressTotal === undefined ? Math.round((value / maxValue) * 100) : percent}
              showInfo={false}
              size="small"
              strokeColor="#002fa7"
              railColor="#edf0f5"
            />
          </div>
        );
      })}
    </div>
  );
}

export default function DashboardWorkspace() {
  const [data, setData] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const { message: messageApi } = App.useApp();

  useEffect(() => {
    let isCurrentRequest = true;

    void getDashboardSummary()
      .then((summary) => {
        if (isCurrentRequest) {
          setData(summary);
          setError(null);
        }
      })
      .catch((requestError: unknown) => {
        if (isCurrentRequest) {
          const message = getErrorMessage(requestError);
          setData(null);
          setError(message);
          messageApi.error(message);
        }
      })
      .finally(() => {
        if (isCurrentRequest) {
          setLoading(false);
        }
      });

    return () => {
      isCurrentRequest = false;
    };
  }, [messageApi, reloadKey]);

  const movementColumns = useMemo<ColumnsType<InventoryMovementApiRecord>>(
    () => [
      {
        title: "时间",
        dataIndex: "created_at",
        key: "created_at",
        width: 130,
        render: (value: string) => (
          <span className="dashboard-movement-time">{formatMovementTime(value)}</span>
        ),
      },
      {
        title: "商品",
        key: "product",
        width: 156,
        render: (_value: unknown, movement) => <MovementProduct movement={movement} />,
      },
      {
        title: "类型",
        dataIndex: "movement_type",
        key: "movement_type",
        width: 86,
        render: (value: InventoryMovementApiRecord["movement_type"]) => (
          <Tag color={getMovementTagColor(value)}>{getMovementLabel(value)}</Tag>
        ),
      },
      {
        title: "变化箱数",
        key: "quantity",
        width: 92,
        render: (_value: unknown, movement) => {
          const delta = getMovementDelta(movement);
          return (
            <span
              className={`dashboard-movement-delta ${
                delta > 0
                  ? "dashboard-movement-delta-increase"
                  : delta < 0
                    ? "dashboard-movement-delta-decrease"
                    : "dashboard-movement-delta-zero"
              }`}
            >
              {formatMovementQuantity(movement)}
            </span>
          );
        },
      },
      {
        title: "库存变化",
        key: "stock",
        width: 104,
        render: (_value: unknown, movement) =>
          `${movement.before_carton_count} → ${movement.after_carton_count}箱`,
      },
      {
        title: "备注",
        dataIndex: "remark",
        key: "remark",
        ellipsis: true,
        render: (value: string | null) =>
          value ? (
            <Tooltip title={value} placement="topLeft">
              <span className="dashboard-movement-remark">{value}</span>
            </Tooltip>
          ) : (
            <span className="table-note-empty">—</span>
          ),
      },
    ],
    [],
  );

  const warehouseTotal =
    data?.warehouse_distribution.reduce((total, item) => total + item.carton_count, 0) ?? 0;

  return (
    <div className="dashboard-page">
      <div className="page-heading dashboard-heading">
        <div>
          <Typography.Title level={1}>概览</Typography.Title>
          <p className="dashboard-subtitle">快速了解当前库存情况和最近的库存动态</p>
        </div>
        <DashboardOutlined className="dashboard-heading-icon" aria-hidden="true" />
      </div>

      {error && (
        <Alert
          className="dashboard-load-error"
          type="error"
          showIcon
          title="概览数据加载失败"
          description={error}
          action={
            <button
              className="dashboard-retry-button"
              type="button"
              onClick={() => {
                setLoading(true);
                setReloadKey((value) => value + 1);
              }}
            >
              重试
            </button>
          }
        />
      )}

      <div className="dashboard-stat-grid">
        <MetricCard
          label="在用商品"
          value={data?.active_product_count ?? (error ? null : 0)}
          loading={loading}
        />
        <MetricCard
          label="当前总库存"
          value={data?.total_carton_count ?? (error ? null : 0)}
          suffix="箱"
          loading={loading}
        />
        <MetricCard
          label="零库存商品"
          value={data?.zero_stock_product_count ?? (error ? null : 0)}
          loading={loading}
        />
        <MetricCard
          label="已停用商品"
          value={data?.inactive_product_count ?? (error ? null : 0)}
          loading={loading}
        />
      </div>

      <div className="dashboard-main-grid">
        <Card
          className="dashboard-section-card dashboard-movement-card"
          title="最近库存动态"
          extra={<Link href="/inventory-movements">查看全部</Link>}
        >
          <Table<InventoryMovementApiRecord>
            className="dashboard-movement-table"
            rowKey="id"
            columns={movementColumns}
            dataSource={data?.recent_movements ?? []}
            loading={loading}
            size="small"
            tableLayout="fixed"
            pagination={false}
            scroll={{ x: 650 }}
            locale={{
              emptyText: (
                <Empty
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                  description="暂无库存动态"
                />
              ),
            }}
          />
        </Card>

        <Card
          className="dashboard-section-card dashboard-zero-stock-card"
          title="零库存商品"
          extra={<Link href="/products">查看全部</Link>}
        >
          {loading ? (
            <DashboardCardSkeleton rows={5} />
          ) : data?.zero_stock_products.length ? (
            <div className="dashboard-zero-stock-list">
              {data.zero_stock_products.map((product) => (
                <ZeroStockProductRow key={product.id} product={product} />
              ))}
            </div>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="当前没有零库存商品"
            />
          )}
        </Card>
      </div>

      <div className="dashboard-bottom-grid">
        <Card className="dashboard-section-card" title="商品分类分布">
          {loading ? (
            <DashboardCardSkeleton rows={5} />
          ) : (
            <DistributionRows
              items={data?.category_distribution ?? []}
              getName={(item) => item.category_name}
              getValue={(item) => item.product_count}
              formatValue={(value) => `${value} 个商品`}
              emptyDescription="暂无分类数据"
            />
          )}
        </Card>

        <Card className="dashboard-section-card" title="仓库库存分布">
          {loading ? (
            <DashboardCardSkeleton rows={5} />
          ) : (
            <DistributionRows
              items={data?.warehouse_distribution ?? []}
              getName={(item) => item.warehouse_name}
              getValue={(item) => item.carton_count}
              formatValue={(value, percent) => `${value}箱 ${percent}%`}
              progressTotal={warehouseTotal}
              emptyDescription="暂无仓库数据"
            />
          )}
          {!loading && data?.warehouse_distribution.length && warehouseTotal === 0 ? (
            <p className="dashboard-distribution-note">当前所有仓库均为零库存</p>
          ) : null}
        </Card>

        <Card className="dashboard-section-card dashboard-actions-card" title="快速操作">
          <div className="dashboard-action-list">
            <Link className="dashboard-action-link" href="/products">
              <PlusOutlined aria-hidden="true" />
              <span>新增商品</span>
            </Link>
            <Link className="dashboard-action-link" href="/products/import">
              <FileExcelOutlined aria-hidden="true" />
              <span>批量导入</span>
            </Link>
            <Link className="dashboard-action-link" href="/inventory-movements">
              <HistoryOutlined aria-hidden="true" />
              <span>查看库存流水</span>
            </Link>
            <Link className="dashboard-action-link" href="/categories">
              <TagsOutlined aria-hidden="true" />
              <span>管理分类</span>
            </Link>
          </div>
          <div className="dashboard-actions-footnote">
            <AppstoreAddOutlined aria-hidden="true" />
            <span>常用库存操作集中在这里</span>
          </div>
        </Card>
      </div>
    </div>
  );
}
