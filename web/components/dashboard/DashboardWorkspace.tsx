"use client";

import {
  AppstoreOutlined,
  ExclamationCircleOutlined,
  InboxOutlined,
} from "@ant-design/icons";
import { Alert, App, Card, Empty, Skeleton } from "antd";
import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import PageHeading from "@/components/common/PageHeading";
import { getDashboardSummary } from "@/lib/api/dashboard";
import {
  buildProductListHref,
  DEFAULT_PRODUCT_LIST_STATE,
  type ProductListState,
} from "@/lib/product-list-state";
import type {
  DashboardCategoryDistribution,
  DashboardSummary,
  DashboardWarehouseDistribution,
} from "@/types/inventory";

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "概览数据加载失败，请重试。";
}

function DashboardCardSkeleton({ rows = 2 }: { rows?: number }) {
  return <Skeleton active title={{ width: "38%" }} paragraph={{ rows }} />;
}

function buildDashboardProductHref(
  overrides: Partial<ProductListState> = {},
  includeDefaultStatus = false,
): string {
  return buildProductListHref(
    { ...DEFAULT_PRODUCT_LIST_STATE, ...overrides },
    { includeDefaultStatus },
  );
}

function MetricCard({
  label,
  value,
  suffix,
  icon,
  tone,
  loading,
  href,
}: {
  label: string;
  value: number | null;
  suffix?: string;
  icon: ReactNode;
  tone: "blue" | "green" | "orange";
  loading: boolean;
  href: string;
}) {
  return (
    <Link
      className="dashboard-metric-link"
      href={href}
      aria-label={
        value === null
          ? `查看${label}`
          : `查看${label}：${value.toLocaleString("zh-CN")}${suffix ?? ""}`
      }
    >
      <Card className="dashboard-metric-card" size="small" variant="outlined">
        <div className="dashboard-metric-content">
          <span className={`dashboard-metric-icon dashboard-metric-icon-${tone}`} aria-hidden="true">
            {icon}
          </span>
          <div className="dashboard-metric-copy">
            <span className="dashboard-metric-label">{label}</span>
            {loading ? (
              <Skeleton active title={false} paragraph={{ rows: 1, width: "58%" }} />
            ) : (
              <span className="dashboard-metric-value">
                {value === null ? "—" : value.toLocaleString("zh-CN")}
                {suffix && <small>{suffix}</small>}
              </span>
            )}
          </div>
        </div>
      </Card>
    </Link>
  );
}

function formatShare(value: number, total: number): string {
  return `${(total > 0 ? (value / total) * 100 : 0).toFixed(1)}%`;
}

function CategoryDistribution({
  items,
}: {
  items: DashboardCategoryDistribution[];
}) {
  if (items.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分类数据" />;
  }

  const total = items.reduce((sum, item) => sum + item.product_count, 0);
  const maxValue = Math.max(...items.map((item) => item.product_count), 0);

  return (
    <div className="dashboard-category-content">
      <div className="dashboard-category-columns" aria-hidden="true">
        <span>#</span>
        <span>分类名称</span>
        <span />
        <span>商品数</span>
        <span>占比</span>
      </div>
      <div className="dashboard-category-list" role="list" aria-label="按分类筛选商品">
        {items.map((item, index) => {
          const barWidth = maxValue > 0 ? (item.product_count / maxValue) * 100 : 0;
          const rowContent = (
            <>
              <span className={`dashboard-category-rank${index === 0 ? " is-leading" : ""}`}>
                {index + 1}
              </span>
              <span className="dashboard-category-name" title={item.category_name}>
                {item.category_name}
              </span>
              <span
                className="dashboard-category-bar-track"
                aria-hidden="true"
              >
                <span style={{ width: `${barWidth}%` }} />
              </span>
              <span className="dashboard-category-count">
                {item.product_count.toLocaleString("zh-CN")}
              </span>
              <span className="dashboard-category-share">
                {formatShare(item.product_count, total)}
              </span>
            </>
          );

          return (
            item.category_id === null ? (
              <div
                className="dashboard-category-row"
                key={`uncategorized-${item.category_name}`}
                role="listitem"
              >
                {rowContent}
              </div>
            ) : (
              <Link
                className="dashboard-category-row"
                key={item.category_id}
                href={buildDashboardProductHref({ categoryId: item.category_id })}
                role="listitem"
                aria-label={`筛选分类：${item.category_name}，${item.product_count} 个商品`}
              >
                {rowContent}
              </Link>
            )
          );
        })}
      </div>
    </div>
  );
}

const warehouseColors = [
  "#1677ff",
  "#23a26d",
  "#f59e0b",
  "#8b72d9",
  "#22a7a1",
  "#e56d55",
  "#5969db",
  "#8a9aa9",
];

function getWarehouseColor(index: number): string {
  return warehouseColors[index % warehouseColors.length];
}

function pieSlicePath(startAngle: number, endAngle: number): string {
  const center = 110;
  const radius = 102;
  const startX = center + radius * Math.cos(startAngle);
  const startY = center + radius * Math.sin(startAngle);
  const endX = center + radius * Math.cos(endAngle);
  const endY = center + radius * Math.sin(endAngle);
  const largeArc = endAngle - startAngle > Math.PI ? 1 : 0;

  return `M ${center} ${center} L ${startX} ${startY} A ${radius} ${radius} 0 ${largeArc} 1 ${endX} ${endY} Z`;
}

function WarehouseDistribution({
  items,
}: {
  items: DashboardWarehouseDistribution[];
}) {
  if (items.length === 0) {
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无仓库数据" />;
  }

  const total = items.reduce((sum, item) => sum + item.carton_count, 0);
  const positiveItems = items
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => item.carton_count > 0);
  const slices = positiveItems.reduce<{
    endAngle: number;
    slices: Array<{
      warehouse: DashboardWarehouseDistribution;
      color: string;
      path: string;
    }>;
  }>(
    (result, { item, index }) => {
      const endAngle = result.endAngle + (item.carton_count / total) * Math.PI * 2;
      return {
        endAngle,
        slices: [
          ...result.slices,
          {
            warehouse: item,
            color: getWarehouseColor(index),
            path: pieSlicePath(result.endAngle, endAngle),
          },
        ],
      };
    },
    { endAngle: -Math.PI / 2, slices: [] },
  ).slices;

  return (
    <div className="dashboard-warehouse-content">
      <div
        className={`dashboard-pie-chart${total === 0 ? " is-empty" : ""}`}
        aria-label={`仓库库存分布，共 ${total.toLocaleString("zh-CN")} 箱`}
      >
        <svg
          viewBox="0 0 220 220"
          role={total === 0 ? "img" : "group"}
          aria-label={`仓库库存分布，共 ${total.toLocaleString("zh-CN")} 箱`}
        >
          {total === 0 ? (
            <circle cx="110" cy="110" r="102" fill="#edf0f5" />
          ) : positiveItems.length === 1 ? (
            <a
              href={buildDashboardProductHref({
                warehouseId: slices[0].warehouse.warehouse_id,
              })}
              aria-label={`筛选仓库：${slices[0].warehouse.warehouse_name}`}
            >
              <circle cx="110" cy="110" r="102" fill={slices[0].color} />
            </a>
          ) : (
            slices.map((slice) => (
              <a
                key={slice.warehouse.warehouse_id}
                href={buildDashboardProductHref({
                  warehouseId: slice.warehouse.warehouse_id,
                })}
                aria-label={`筛选仓库：${slice.warehouse.warehouse_name}`}
              >
                <path
                  className="dashboard-pie-slice"
                  d={slice.path}
                  fill={slice.color}
                  stroke="#ffffff"
                  strokeWidth="1.5"
                />
              </a>
            ))
          )}
        </svg>
        {total === 0 && <span>0 箱</span>}
      </div>
      <div className="dashboard-warehouse-legend" role="list" aria-label="按仓库筛选商品">
        {items.map((item, index) => (
          <Link
            className="dashboard-warehouse-legend-row"
            key={item.warehouse_id}
            role="listitem"
            href={buildDashboardProductHref({ warehouseId: item.warehouse_id })}
            aria-label={`筛选仓库：${item.warehouse_name}，${item.carton_count.toLocaleString("zh-CN")} 箱`}
          >
            <span
              className="dashboard-warehouse-legend-dot"
              style={{ backgroundColor: getWarehouseColor(index) }}
              aria-hidden="true"
            />
            <span className="dashboard-warehouse-name" title={item.warehouse_name}>
              {item.warehouse_name}
            </span>
            <span className="dashboard-warehouse-count">
              {item.carton_count.toLocaleString("zh-CN")} 箱
            </span>
            <span className="dashboard-warehouse-share">
              {formatShare(item.carton_count, total)}
            </span>
          </Link>
        ))}
      </div>
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
    void getDashboardSummary()
      .then((summary) => {
        setData(summary);
        setError(null);
      })
      .catch((requestError: unknown) => {
        const message = getErrorMessage(requestError);
        setData(null);
        setError(message);
        messageApi.error(message);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [messageApi, reloadKey]);

  const categoryDistribution = useMemo(
    () =>
      [...(data?.category_distribution ?? [])].sort(
        (a, b) => b.product_count - a.product_count,
      ),
    [data?.category_distribution],
  );
  const warehouseDistribution = useMemo(
    () =>
      [...(data?.warehouse_distribution ?? [])].sort(
        (a, b) => b.carton_count - a.carton_count,
      ),
    [data?.warehouse_distribution],
  );
  const categoryTotal = categoryDistribution.reduce(
    (total, item) => total + item.product_count,
    0,
  );
  const warehouseTotal = warehouseDistribution.reduce(
    (total, item) => total + item.carton_count,
    0,
  );
  const inUseProductsHref = buildDashboardProductHref({}, true);
  const allInUseProductsHref = buildDashboardProductHref();
  const zeroStockProductsHref = buildDashboardProductHref({
    stockMin: 0,
    stockMax: 0,
  });

  return (
    <div className="dashboard-page">
      <PageHeading title="概览" className="dashboard-heading" />

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
          icon={<AppstoreOutlined />}
          tone="blue"
          loading={loading}
          href={inUseProductsHref}
        />
        <MetricCard
          label="总库存"
          value={data?.total_carton_count ?? (error ? null : 0)}
          suffix="箱"
          icon={<InboxOutlined />}
          tone="green"
          loading={loading}
          href={allInUseProductsHref}
        />
        <MetricCard
          label="零库存商品"
          value={data?.zero_stock_product_count ?? (error ? null : 0)}
          icon={<ExclamationCircleOutlined />}
          tone="orange"
          loading={loading}
          href={zeroStockProductsHref}
        />
      </div>

      <div className="dashboard-distribution-grid">
        <Card
          className="dashboard-section-card dashboard-category-card"
          title="商品分类"
          extra={
            !loading && data ? (
              <span className="dashboard-panel-total">
                共 {categoryTotal.toLocaleString("zh-CN")} 个商品
              </span>
            ) : null
          }
        >
          {loading ? (
            <DashboardCardSkeleton rows={7} />
          ) : (
            <CategoryDistribution items={categoryDistribution} />
          )}
        </Card>

        <Card
          className="dashboard-section-card dashboard-warehouse-card"
          title="仓库库存"
          extra={
            !loading && data ? (
              <span className="dashboard-panel-total">
                共 {warehouseTotal.toLocaleString("zh-CN")} 箱
              </span>
            ) : null
          }
        >
          {loading ? (
            <DashboardCardSkeleton rows={4} />
          ) : (
            <WarehouseDistribution items={warehouseDistribution} />
          )}
        </Card>
      </div>
    </div>
  );
}
