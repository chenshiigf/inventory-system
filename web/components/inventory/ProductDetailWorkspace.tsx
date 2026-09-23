"use client";

import {
  ArrowLeftOutlined,
  CheckOutlined,
  EditOutlined,
  InboxOutlined,
  SettingOutlined,
  StopOutlined,
  SwapOutlined,
} from "@ant-design/icons";
import {
  Alert,
  App,
  Button,
  Empty,
  Image,
  message,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import ProductEditorModal from "@/components/inventory/ProductEditorModal";
import InventoryAdjustmentModal from "@/components/inventory/InventoryAdjustmentModal";
import StockMovementModal from "@/components/inventory/StockMovementModal";
import {
  formatMovementQuantity,
  formatMovementTime,
  formatPackaging,
  getMovementDelta,
  getMovementLabel,
} from "@/components/inventory/inventory-movement-utils";
import { getProductImageUrl } from "@/lib/api/product-images";
import {
  activateProduct,
  deactivateProduct,
  getProduct,
  updateProduct,
} from "@/lib/api/products";
import {
  createStockAdjustment,
  createStockMovement,
  listInventoryMovements,
} from "@/lib/api/inventory-movements";
import { listCategories } from "@/lib/api/categories";
import { listWarehouses } from "@/lib/api/warehouses";
import { toInventoryProduct } from "@/lib/inventory-products";
import type {
  CategoryTreeNode,
  InventoryMovementApiRecord,
  InventoryProduct,
  ProductEditorFormValues,
  ProductUpdatePayload,
  StockAdjustmentValues,
  StockMovementDirection,
  StockMovementValues,
  WarehouseRead,
} from "@/types/inventory";

interface ProductDetailWorkspaceProps {
  productId: string;
}

interface StockMovementState {
  direction: StockMovementDirection;
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "服务请求失败，请重试。";
}

function isProductNotFound(error: unknown): boolean {
  return error instanceof Error && error.message === "Product not found";
}

function displayValue(value: string | null | undefined): string {
  return value?.trim() ? value : "—";
}

function displayPrice(value: string | null | undefined): string {
  return value?.trim() ? `¥ ${value}` : "—";
}

function formatPackagingLabel(
  packingQty: number | null,
  unit: InventoryProduct["unit"],
): string {
  return packingQty === null
    ? "装箱数未填写"
    : `${packingQty} ${unit ?? "—"}/箱`;
}

function formatPackagingSummary(
  packagings: InventoryProduct["packagings"],
  unit: InventoryProduct["unit"],
): string {
  const quantities = packagings
    .map((packaging) => packaging.packingQty)
    .filter((packingQty): packingQty is number => packingQty !== null && packingQty > 0)
    .filter((packingQty, index, values) => values.indexOf(packingQty) === index);

  if (quantities.length === 0) {
    return "—";
  }

  return `${quantities.join(" / ")} ${unit ?? "—"}/箱`;
}

function getMovementTone(movement: InventoryMovementApiRecord): string {
  const delta = getMovementDelta(movement);
  if (delta > 0) {
    return "product-detail-movement-increase";
  }
  if (delta < 0) {
    return "product-detail-movement-decrease";
  }
  return "product-detail-movement-zero";
}

export default function ProductDetailWorkspace({
  productId,
}: ProductDetailWorkspaceProps) {
  const parsedProductId = Number(productId);
  const validProductId =
    Number.isInteger(parsedProductId) && parsedProductId > 0
      ? parsedProductId
      : null;
  const [product, setProduct] = useState<InventoryProduct | null>(null);
  const [movements, setMovements] = useState<InventoryMovementApiRecord[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const requestKey = `${validProductId ?? "invalid"}:${reloadKey}`;
  const [completedRequestKey, setCompletedRequestKey] = useState<string | null>(
    null,
  );
  const loading = validProductId !== null && completedRequestKey !== requestKey;
  const movementLoading = loading;
  const [stockMovement, setStockMovement] =
    useState<StockMovementState | null>(null);
  const [adjustmentOpen, setAdjustmentOpen] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [metadataLoading, setMetadataLoading] = useState(false);
  const [categories, setCategories] = useState<CategoryTreeNode[]>([]);
  const [warehouses, setWarehouses] = useState<WarehouseRead[]>([]);
  const [messageApi, messageContextHolder] = message.useMessage();
  const { modal } = App.useApp();

  useEffect(() => {
    if (validProductId === null) {
      return;
    }

    const controller = new AbortController();

    void Promise.all([
      getProduct(validProductId, controller.signal),
      listInventoryMovements(
        { productId: validProductId, page: 1, pageSize: 5 },
        controller.signal,
      ),
    ])
      .then(([productResponse, movementResponse]) => {
        if (controller.signal.aborted) {
          return;
        }
        setProduct(toInventoryProduct(productResponse));
        setMovements(movementResponse.items);
        setLoadError(null);
        setNotFound(false);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setProduct(null);
        setMovements([]);
        setNotFound(isProductNotFound(error));
        setLoadError(isProductNotFound(error) ? null : getErrorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setCompletedRequestKey(requestKey);
        }
      });

    return () => controller.abort();
  }, [requestKey, validProductId]);

  const packagingRows = useMemo(
    () =>
      product
        ? [...product.packagings].sort(
            (left, right) => left.sortOrder - right.sortOrder,
          )
        : [],
    [product],
  );
  const packagingSummary = formatPackagingSummary(
    packagingRows,
    product?.unit ?? null,
  );

  const movementColumns: ColumnsType<InventoryMovementApiRecord> = [
    {
      title: "时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 148,
      render: (value: string) => (
        <span className="product-detail-movement-time">
          {formatMovementTime(value)}
        </span>
      ),
    },
    {
      title: "类型",
      dataIndex: "movement_type",
      key: "movement_type",
      width: 82,
      render: (value: InventoryMovementApiRecord["movement_type"]) => (
        <span className={`product-detail-movement-type product-detail-movement-type-${value.toLowerCase()}`}>
          {getMovementLabel(value)}
        </span>
      ),
    },
    {
      title: "包装规格",
      key: "packaging",
      width: 150,
      render: (_value: unknown, movement: InventoryMovementApiRecord) =>
        formatPackaging(movement),
    },
    {
      title: "变化箱数",
      key: "quantity",
      width: 96,
      render: (_value: unknown, movement: InventoryMovementApiRecord) => (
        <span className={`product-detail-movement-quantity ${getMovementTone(movement)}`}>
          {formatMovementQuantity(movement)}
        </span>
      ),
    },
    {
      title: "库存变化",
      key: "stock",
      width: 112,
      render: (_value: unknown, movement: InventoryMovementApiRecord) =>
        `${movement.before_carton_count} → ${movement.after_carton_count}`,
    },
    {
      title: "备注",
      dataIndex: "remark",
      key: "remark",
      render: (value: string | null) =>
        value ? (
          <Tooltip title={value} placement="topLeft">
            <span className="product-detail-movement-remark">{value}</span>
          </Tooltip>
        ) : (
          <span className="table-note-empty">—</span>
        ),
    },
  ];

  async function openEditor() {
    if (!product) {
      return;
    }

    setMetadataLoading(true);
    try {
      const [categoryResponse, warehouseResponse] = await Promise.all([
        listCategories(),
        listWarehouses(),
      ]);
      setCategories(categoryResponse);
      setWarehouses(warehouseResponse);
      setEditorOpen(true);
    } catch (error) {
      messageApi.error(getErrorMessage(error));
    } finally {
      setMetadataLoading(false);
    }
  }

  async function saveProduct(values: ProductEditorFormValues) {
    if (!product) {
      return;
    }

    const categoryId = values.categoryPath[1];
    if (!categoryId) {
      throw new Error("请选择一个二级分类后保存商品。");
    }
    if (!values.warehouseId) {
      throw new Error("请选择所属仓库后保存商品。");
    }

    const updatePayload: ProductUpdatePayload = {
      category_id: categoryId,
      warehouse_id: values.warehouseId,
      image_path: values.imagePath,
      thumbnail_path: values.thumbnailPath,
      size: values.size.trim(),
      unit: values.unit,
      price: values.price,
      remark: values.remark.trim() || null,
      packagings: values.packagings.map((packaging) => ({
        ...(packaging.id ? { id: packaging.id } : {}),
        packing_qty: packaging.packingQty,
      })),
    };

    await updateProduct(product.id, updatePayload);
    setEditorOpen(false);
    messageApi.success("商品信息已保存到数据库");
    setReloadKey((value) => value + 1);
  }

  async function changeProductStatus(isActive: boolean) {
    if (!product) {
      return;
    }

    try {
      if (isActive) {
        await activateProduct(product.id);
      } else {
        await deactivateProduct(product.id);
      }
      messageApi.success(isActive ? "商品已重新启用" : "商品已停用");
      setReloadKey((value) => value + 1);
    } catch (error) {
      messageApi.error(getErrorMessage(error));
    }
  }

  function confirmProductStatusChange(isActive: boolean) {
    if (!product) {
      return;
    }

    if (isActive) {
      modal.confirm({
        title: "重新启用商品？",
        content: "商品将回到默认在用商品列表，商品编号、图片、包装规格和库存保持不变。",
        okText: "确认启用",
        cancelText: "取消",
        onOk: () => changeProductStatus(true),
      });
      return;
    }

    modal.confirm({
      title: "停用商品？",
      content: (
        <div>
          <p>停用后，该商品将从默认库存列表中隐藏，历史数据仍会保留。</p>
          {product.totalCartonCount > 0 && (
            <>
              <p>该商品当前还有 {product.totalCartonCount} 箱库存。</p>
              <p>停用不会清空库存。</p>
            </>
          )}
        </div>
      ),
      okText: "确认停用",
      cancelText: "取消",
      onOk: () => changeProductStatus(false),
    });
  }

  async function confirmStockMovement(values: StockMovementValues) {
    if (!product || !stockMovement) {
      return;
    }

    try {
      await createStockMovement(product.id, stockMovement.direction, values);
      setStockMovement(null);
      messageApi.success(
        stockMovement.direction === "in"
          ? "入库成功，库存已更新"
          : "出库成功，库存已更新",
      );
      setReloadKey((value) => value + 1);
    } catch (error) {
      messageApi.error(getErrorMessage(error));
      throw error;
    }
  }

  async function confirmStockAdjustment(values: StockAdjustmentValues) {
    if (!product) {
      return;
    }

    try {
      await createStockAdjustment(product.id, values);
      setAdjustmentOpen(false);
      messageApi.success("库存调整成功，库存已更新");
      setReloadKey((value) => value + 1);
    } catch (error) {
      messageApi.error(getErrorMessage(error));
      throw error;
    }
  }

  function renderPageState() {
    if (loading) {
      return (
        <div className="product-detail-page-state">
          <Spin description="正在加载商品详情" />
        </div>
      );
    }

    if (notFound || validProductId === null) {
      return (
        <div className="product-detail-page-state">
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="商品不存在或已被移除"
          >
            <Link href="/products">
              <Button type="primary">返回商品库存</Button>
            </Link>
          </Empty>
        </div>
      );
    }

    if (loadError || !product) {
      return (
        <div className="product-detail-page-state">
          <Alert
            type="error"
            showIcon
            title="商品详情加载失败"
            description={loadError ?? "暂时没有可展示的商品详情。"}
            action={
              <Button size="small" onClick={() => setReloadKey((value) => value + 1)}>
                重试
              </Button>
            }
          />
        </div>
      );
    }

    return null;
  }

  return (
    <>
      {messageContextHolder}
      <div className="product-detail-page">
        <Link href="/products" className="product-detail-back-link">
          <ArrowLeftOutlined aria-hidden="true" />
          <span>返回商品库存</span>
        </Link>

        {renderPageState()}

        {product && !loading && !notFound && !loadError && (
          <>
            <div className="product-detail-heading">
              <div>
                <Typography.Title level={1}>商品详情</Typography.Title>
                <p className="product-detail-subtitle">
                  {displayValue(product.productCode)}
                  {product.size ? ` · ${product.size}` : ""}
                </p>
              </div>
              <Tag
                className="product-detail-status-tag"
                color={product.isActive ? "green" : "default"}
              >
                {product.isActive ? "在用" : "已停用"}
              </Tag>
            </div>

            <section className="product-detail-overview" aria-label="商品概览">
              <div className="product-detail-image-column">
                <div className="product-detail-image-frame">
                  {getProductImageUrl(product.imagePath) ? (
                    <Image
                      src={getProductImageUrl(product.imagePath) ?? undefined}
                      alt={`${(product.productCode ?? product.size) || "商品"}主图`}
                      preview={false}
                    />
                  ) : (
                    <div className="product-detail-image-empty">
                      <InboxOutlined aria-hidden="true" />
                      <span>暂无商品图片</span>
                    </div>
                  )}
                </div>
                <span className="product-detail-image-note">商品主图</span>
              </div>

              <div className="product-detail-core-column">
                <div className="product-detail-code-block">
                  <span className="product-detail-eyebrow">商品编号</span>
                  <strong>{displayValue(product.productCode)}</strong>
                </div>
                <div className="product-detail-stock-block">
                  <span className="product-detail-eyebrow">当前库存</span>
                  <div>
                    <strong>{product.totalCartonCount}</strong>
                    <span>箱</span>
                  </div>
                </div>

                <div className="product-detail-info-grid">
                  <div>
                    <span>分类</span>
                    <strong>{displayValue(product.categoryName)}</strong>
                  </div>
                  <div>
                    <span>仓库</span>
                    <strong>{displayValue(product.warehouseName)}</strong>
                  </div>
                  <div>
                    <span>尺寸</span>
                    <strong>{displayValue(product.size)}</strong>
                  </div>
                  <div>
                    <span>单位</span>
                    <strong>{displayValue(product.unit)}</strong>
                  </div>
                  <div>
                    <span>装箱数</span>
                    <strong>{packagingSummary}</strong>
                  </div>
                  <div>
                    <span>单价</span>
                    <strong>{displayPrice(product.price)}</strong>
                  </div>
                  <div className="product-detail-info-wide">
                    <span>备注</span>
                    <strong>{displayValue(product.remark)}</strong>
                  </div>
                </div>

                <div className="product-detail-actions" aria-label="商品操作">
                  {product.isActive && (
                    <>
                      <Button
                        type="primary"
                        icon={<InboxOutlined />}
                        onClick={() => setStockMovement({ direction: "in" })}
                      >
                        入库
                      </Button>
                      <Button
                        icon={<SwapOutlined />}
                        onClick={() => setStockMovement({ direction: "out" })}
                      >
                        出库
                      </Button>
                    </>
                  )}
                  <Button
                    icon={<SettingOutlined />}
                    onClick={() => setAdjustmentOpen(true)}
                  >
                    库存调整
                  </Button>
                  <Button
                    icon={<EditOutlined />}
                    loading={metadataLoading}
                    onClick={() => void openEditor()}
                  >
                    编辑
                  </Button>
                  {product.isActive ? (
                    <Button
                      danger
                      icon={<StopOutlined />}
                      onClick={() => confirmProductStatusChange(false)}
                    >
                      停用
                    </Button>
                  ) : (
                    <Button
                      type="primary"
                      icon={<CheckOutlined />}
                      onClick={() => confirmProductStatusChange(true)}
                    >
                      重新启用
                    </Button>
                  )}
                </div>
              </div>

              <div className="product-detail-packaging-column">
                <div className="product-detail-section-heading">
                  <div>
                    <span className="product-detail-eyebrow">库存构成</span>
                    <h2>包装与库存</h2>
                  </div>
                  <span className="product-detail-packaging-count">
                    {packagingRows.length} 种
                  </span>
                </div>
                <Table
                  className="product-detail-packaging-table"
                  rowKey="id"
                  size="small"
                  pagination={false}
                  tableLayout="fixed"
                  dataSource={packagingRows}
                  columns={[
                    {
                      title: "包装规格",
                      key: "packing_qty",
                      render: (_value: unknown, packaging) =>
                        formatPackagingLabel(packaging.packingQty, product.unit),
                    },
                    {
                      title: "当前箱数",
                      dataIndex: "cartonCount",
                      key: "cartonCount",
                      width: 92,
                      render: (value: number) => (
                        <span className="product-detail-carton-count">{value} 箱</span>
                      ),
                    },
                  ]}
                  locale={{ emptyText: "暂无包装规格" }}
                />
                <div className="product-detail-packaging-total">
                  <span>合计当前库存</span>
                  <strong>{product.totalCartonCount} 箱</strong>
                </div>
              </div>
            </section>

            <section className="product-detail-movements" aria-label="最近库存流水">
              <div className="product-detail-section-heading">
                <div>
                  <span className="product-detail-eyebrow">操作记录</span>
                  <h2>最近库存流水</h2>
                </div>
                <Link
                  href={
                    product.productCode
                      ? `/inventory-movements?search=${encodeURIComponent(product.productCode)}`
                      : "/inventory-movements"
                  }
                  className="product-detail-all-movements"
                >
                  查看全部流水
                </Link>
              </div>

              {movementLoading ? (
                <div className="product-detail-movement-state">
                  <Spin size="small" description="正在加载流水" />
                </div>
              ) : movements.length > 0 ? (
                <Table<InventoryMovementApiRecord>
                  className="product-detail-movement-table"
                  rowKey="id"
                  size="small"
                  pagination={false}
                  tableLayout="fixed"
                  dataSource={movements}
                  columns={movementColumns}
                />
              ) : (
                <div className="product-detail-movement-empty">
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description="暂无库存流水"
                  />
                  <span>初始库存不会补造历史流水。</span>
                </div>
              )}
            </section>
          </>
        )}
      </div>

      {product && stockMovement && (
        <StockMovementModal
          key={`${product.id}-${stockMovement.direction}`}
          product={product}
          direction={stockMovement.direction}
          onCancel={() => setStockMovement(null)}
          onConfirm={confirmStockMovement}
        />
      )}

      {product && adjustmentOpen && (
        <InventoryAdjustmentModal
          product={product}
          onCancel={() => setAdjustmentOpen(false)}
          onConfirm={confirmStockAdjustment}
        />
      )}

      {product && editorOpen && (
        <ProductEditorModal
          product={product}
          categories={categories}
          warehouses={warehouses}
          onCancel={() => setEditorOpen(false)}
          onSave={saveProduct}
        />
      )}
    </>
  );
}
