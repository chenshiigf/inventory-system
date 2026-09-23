"use client";

import {
  ClearOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Card,
  DatePicker,
  Empty,
  Input,
  Select,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { listInventoryMovements } from "@/lib/api/inventory-movements";
import { listWarehouses } from "@/lib/api/warehouses";
import ProductImage from "@/components/inventory/ProductImage";
import {
  formatMovementQuantity,
  formatMovementTime,
  formatPackaging,
  getMovementDelta,
  getMovementLabel,
} from "@/components/inventory/inventory-movement-utils";
import type {
  InventoryMovementApiRecord,
  InventoryMovementType,
  WarehouseRead,
} from "@/types/inventory";

type MovementFilter = "all" | InventoryMovementType;
type WarehouseFilter = "all" | number;

interface InventoryMovementsWorkspaceProps {
  initialSearch?: string;
}

const PAGE_SIZE_OPTIONS = [20, 50, 100];

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "库存流水加载失败，请重试。";
}

function getMovementTagColor(type: InventoryMovementType): string {
  if (type === "IN") {
    return "green";
  }
  if (type === "OUT") {
    return "red";
  }
  return "blue";
}

export default function InventoryMovementsWorkspace({
  initialSearch = "",
}: InventoryMovementsWorkspaceProps) {
  const [items, setItems] = useState<InventoryMovementApiRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [search, setSearch] = useState(initialSearch.trim());
  const [movementType, setMovementType] = useState<InventoryMovementType>();
  const [warehouseId, setWarehouseId] = useState<number>();
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [datePickerKey, setDatePickerKey] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [completedRequestKey, setCompletedRequestKey] = useState<string | null>(
    null,
  );
  const [loadError, setLoadError] = useState<{
    requestKey: string;
    message: string;
  } | null>(null);
  const [warehouses, setWarehouses] = useState<WarehouseRead[]>([]);
  const [warehousesLoading, setWarehousesLoading] = useState(true);
  const { message: messageApi } = App.useApp();
  const requestKey = `${page}:${pageSize}:${reloadKey}:${search}:${movementType ?? "all"}:${warehouseId ?? "all"}:${startDate}:${endDate}`;
  const loading = completedRequestKey !== requestKey;
  const error = loadError?.requestKey === requestKey ? loadError.message : null;

  useEffect(() => {
    const controller = new AbortController();
    void listWarehouses(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setWarehouses(result);
        }
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) {
          messageApi.error(getErrorMessage(requestError));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setWarehousesLoading(false);
        }
      });

    return () => controller.abort();
  }, [messageApi]);

  useEffect(() => {
    const controller = new AbortController();

    void listInventoryMovements(
      {
        page,
        pageSize,
        search,
        movementType,
        warehouseId,
        startDate: startDate || undefined,
        endDate: endDate || undefined,
      },
      controller.signal,
    )
      .then((response) => {
        if (!controller.signal.aborted) {
          setItems(response.items);
          setTotal(response.total);
          setLoadError(null);
        }
      })
      .catch((requestError: unknown) => {
        if (!controller.signal.aborted) {
          const message = getErrorMessage(requestError);
          setLoadError({ requestKey, message });
          messageApi.error(message);
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setCompletedRequestKey(requestKey);
        }
      });

    return () => controller.abort();
  }, [
    endDate,
    messageApi,
    movementType,
    page,
    pageSize,
    reloadKey,
    search,
    startDate,
    warehouseId,
    requestKey,
  ]);

  const hasFilters = Boolean(
    search.trim() || movementType || warehouseId || startDate || endDate,
  );

  const columns = useMemo(
    () => [
      {
        title: "时间",
        dataIndex: "created_at",
        key: "created_at",
        width: 150,
        render: (value: string) => (
          <span className="inventory-movement-time">
            {formatMovementTime(value)}
          </span>
        ),
      },
      {
        title: "商品图片",
        key: "product-image",
        width: 76,
        render: (_value: unknown, movement: InventoryMovementApiRecord) => (
          <div className="inventory-movement-image">
            <ProductImage
              imagePath={movement.image_path}
              thumbnailPath={movement.thumbnail_path}
              alt={`${movement.product_code ?? "商品"}图片`}
              width={52}
              height={52}
            />
          </div>
        ),
      },
      {
        title: "商品编号",
        dataIndex: "product_code",
        key: "product_code",
        width: 132,
        render: (value: string | null) => (
          <span className={value ? "product-code-value" : "product-code-empty"}>
            {value || "—"}
          </span>
        ),
      },
      {
        title: "仓库",
        dataIndex: "warehouse_name",
        key: "warehouse_name",
        width: 100,
        render: (value: string | null) => value || "未指定",
      },
      {
        title: "类型",
        dataIndex: "movement_type",
        key: "movement_type",
        width: 94,
        render: (value: InventoryMovementType) => (
          <Tag
            className={`inventory-movement-tag inventory-movement-tag-${value.toLowerCase()}`}
            color={getMovementTagColor(value)}
          >
            {getMovementLabel(value)}
          </Tag>
        ),
      },
      {
        title: "包装规格",
        key: "packaging",
        width: 154,
        render: (_value: unknown, movement: InventoryMovementApiRecord) =>
          formatPackaging(movement),
      },
      {
        title: "变化箱数",
        key: "quantity",
        width: 94,
        render: (_value: unknown, movement: InventoryMovementApiRecord) => {
          const delta = getMovementDelta(movement);
          const tone =
            delta > 0
              ? "increase"
              : delta < 0
                ? "decrease"
                : "zero";
          return (
            <span className={`inventory-movement-delta inventory-movement-delta-${tone}`}>
              {formatMovementQuantity(movement)}
            </span>
          );
        },
      },
      {
        title: "库存变化",
        key: "stock",
        width: 112,
        render: (_value: unknown, movement: InventoryMovementApiRecord) =>
          `${movement.before_carton_count} → ${movement.after_carton_count}箱`,
      },
      {
        title: "备注",
        dataIndex: "remark",
        key: "remark",
        width: 220,
        render: (value: string | null) =>
          value ? (
            <Tooltip title={value} placement="topLeft">
              <span className="inventory-movement-remark">{value}</span>
            </Tooltip>
          ) : (
            <span className="table-note-empty">—</span>
          ),
      },
    ],
    [],
  );

  function resetFilters() {
    setSearch("");
    setMovementType(undefined);
    setWarehouseId(undefined);
    setStartDate("");
    setEndDate("");
    setPage(1);
    setDatePickerKey((value) => value + 1);
  }

  return (
    <div className="inventory-movements-page">
      <div className="page-heading inventory-movements-heading">
        <div>
          <Typography.Title level={1}>库存流水</Typography.Title>
          <p className="inventory-movements-subtitle">
            查看商品的入库、出库和库存调整记录
          </p>
        </div>
      </div>

      <Card
        className="inventory-movements-filter-card"
        size="small"
        title="筛选条件"
      >
        <div className="inventory-movements-filter-row">
          <Input
            className="inventory-movements-search"
            allowClear
            maxLength={100}
            prefix={<SearchOutlined aria-hidden="true" />}
            placeholder="搜索商品编号"
            aria-label="搜索商品编号"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
          <Select<MovementFilter>
            className="inventory-movements-type-filter"
            aria-label="筛选流水类型"
            value={movementType ?? "all"}
            options={[
              { label: "全部类型", value: "all" },
              { label: "入库", value: "IN" },
              { label: "出库", value: "OUT" },
              { label: "库存调整", value: "ADJUST" },
            ]}
            onChange={(value) => {
              setMovementType(value === "all" ? undefined : value);
              setPage(1);
            }}
          />
          <Select<WarehouseFilter>
            className="inventory-movements-warehouse-filter"
            aria-label="筛选仓库"
            loading={warehousesLoading}
            value={warehouseId ?? "all"}
            options={[
              { label: "全部仓库", value: "all" },
              ...warehouses.map((warehouse) => ({
                label: warehouse.name,
                value: warehouse.id,
              })),
            ]}
            onChange={(value) => {
              setWarehouseId(value === "all" ? undefined : value);
              setPage(1);
            }}
          />
          <DatePicker.RangePicker
            key={datePickerKey}
            className="inventory-movements-date-filter"
            aria-label="筛选日期范围"
            format="YYYY-MM-DD"
            placeholder={["开始日期", "结束日期"]}
            onChange={(_dates, dateStrings) => {
              setStartDate(dateStrings[0] ?? "");
              setEndDate(dateStrings[1] ?? "");
              setPage(1);
            }}
          />
          <Button icon={<ClearOutlined />} onClick={resetFilters}>
            重置
          </Button>
        </div>
      </Card>

      {error && (
        <Alert
          className="inventory-movements-load-error"
          type="error"
          showIcon
          title="库存流水加载失败"
          description={error}
          action={
            <Button
              size="small"
              onClick={() => {
                setReloadKey((value) => value + 1);
              }}
            >
              重试
            </Button>
          }
        />
      )}

      <Card
        className="inventory-movements-table-card"
        size="small"
        title="流水记录"
        extra={<span className="result-count">共 {total} 条</span>}
      >
        <Table<InventoryMovementApiRecord>
          className="inventory-movements-page-table"
          rowKey="id"
          columns={columns}
          dataSource={items}
          loading={loading}
          size="middle"
          tableLayout="fixed"
          scroll={{ x: 1132 }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={
                  hasFilters ? "没有符合条件的库存流水" : "暂无库存流水"
                }
              />
            ),
          }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: PAGE_SIZE_OPTIONS.map(String),
            showTotal: (value, range) =>
              `第 ${range[0]}-${range[1]} 条，共 ${value} 条`,
            onChange: (nextPage, nextPageSize) => {
              setPage(nextPageSize !== pageSize ? 1 : nextPage);
              if (nextPageSize !== pageSize) {
                setPageSize(nextPageSize);
              }
            },
          }}
        />
      </Card>
    </div>
  );
}
