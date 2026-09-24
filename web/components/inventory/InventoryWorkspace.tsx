"use client";

import { PlusOutlined } from "@ant-design/icons";
import { Alert, App, Button, message, Segmented, Typography } from "antd";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ProductEditorModal from "@/components/inventory/ProductEditorModal";
import BatchActionBar, {
  type BatchStatusSelection,
} from "@/components/inventory/BatchActionBar";
import BatchCategoryModal from "@/components/inventory/BatchCategoryModal";
import InventoryAdjustmentModal from "@/components/inventory/InventoryAdjustmentModal";
import InventoryMovementsModal from "@/components/inventory/InventoryMovementsModal";
import ProductGallery from "@/components/inventory/ProductGallery";
import ProductTable from "@/components/inventory/ProductTable";
import InventoryToolbar from "@/components/inventory/InventoryToolbar";
import StockMovementModal from "@/components/inventory/StockMovementModal";
import { listCategories } from "@/lib/api/categories";
import {
  batchActivateProducts,
  batchDeactivateProducts,
  batchUpdateProductCategory,
  createProduct as createProductRequest,
  activateProduct,
  deactivateProduct,
  listProducts,
  updateProduct as updateProductRequest,
} from "@/lib/api/products";
import { listWarehouses } from "@/lib/api/warehouses";
import { toInventoryProduct } from "@/lib/inventory-products";
import {
  createStockAdjustment,
  createStockMovement,
} from "@/lib/api/inventory-movements";
import {
  getCategoryLabel,
  getCategoryPath,
  hasSecondLevelCategories,
  toCategoryOptions,
} from "@/lib/categories";
import {
  buildProductDetailHref,
  buildProductListQuery,
  buildProductListHref,
  DEFAULT_PRODUCT_LIST_STATE,
  parseProductListState,
  type ProductListState,
} from "@/lib/product-list-state";
import {
  readProductListScroll,
  saveProductListScroll,
} from "@/lib/product-list-scroll";
import type {
  CategorySelection,
  CategoryTreeNode,
  InventoryProduct,
  ProductCreatePayload,
  ProductEditorFormValues,
  ProductUpdatePayload,
  ProductStatus,
  StockStatus,
  StockMovementDirection,
  StockAdjustmentValues,
  StockMovementValues,
  WarehouseRead,
  WarehouseSelection,
} from "@/types/inventory";

interface ProductEditorState {
  product?: InventoryProduct;
}

interface StockMovementState {
  product: InventoryProduct;
  direction: StockMovementDirection;
}

type InventoryViewMode = "table" | "gallery";

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "服务请求失败，请重试。";
}

interface InventoryWorkspaceProps {
  initialState?: ProductListState;
}

export default function InventoryWorkspace({
  initialState = DEFAULT_PRODUCT_LIST_STATE,
}: InventoryWorkspaceProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const searchParamsString = searchParams.toString();
  const initialStateQuery = useMemo(
    () => buildProductListQuery(initialState),
    [initialState],
  );
  const urlState = useMemo(
    () =>
      searchParamsString === initialStateQuery
        ? initialState
        : parseProductListState(new URLSearchParams(searchParamsString)),
    [initialState, initialStateQuery, searchParamsString],
  );
  const [categories, setCategories] = useState<CategoryTreeNode[]>([]);
  const [filterCategories, setFilterCategories] = useState<CategoryTreeNode[]>([]);
  const [categoriesLoading, setCategoriesLoading] = useState(true);
  const [categoriesError, setCategoriesError] = useState<string | null>(null);
  const [filterCategoriesLoading, setFilterCategoriesLoading] = useState(true);
  const [filterCategoriesError, setFilterCategoriesError] = useState<string | null>(null);
  const [categoriesReloadCounter, setCategoriesReloadCounter] = useState(0);
  const [filterCategoriesReloadCounter, setFilterCategoriesReloadCounter] =
    useState(0);
  const [warehouses, setWarehouses] = useState<WarehouseRead[]>([]);
  const [warehousesLoading, setWarehousesLoading] = useState(true);
  const [warehousesError, setWarehousesError] = useState<string | null>(null);
  const [warehousesReloadCounter, setWarehousesReloadCounter] = useState(0);
  const [products, setProducts] = useState<InventoryProduct[]>([]);
  const [total, setTotal] = useState(0);
  const [completedRequestKey, setCompletedRequestKey] = useState<string | null>(
    null,
  );
  const [loadError, setLoadError] = useState<{
    requestKey: string;
    message: string;
  } | null>(null);
  const [reloadCounter, setReloadCounter] = useState(0);
  const [productEditor, setProductEditor] = useState<ProductEditorState | null>(
    null,
  );
  const [stockMovement, setStockMovement] =
    useState<StockMovementState | null>(null);
  const [movementProduct, setMovementProduct] =
    useState<InventoryProduct | null>(null);
  const [adjustmentProduct, setAdjustmentProduct] =
    useState<InventoryProduct | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [selectedProductIds, setSelectedProductIds] = useState<Set<number>>(
    new Set(),
  );
  const [batchCategoryModalOpen, setBatchCategoryModalOpen] = useState(false);
  const [batchCategorySubmitting, setBatchCategorySubmitting] = useState(false);
  const [messageApi, messageContextHolder] = message.useMessage();
  const { modal } = App.useApp();
  const restoredScrollUrlsRef = useRef(new Set<string>());
  const warehouseValue: WarehouseSelection = urlState.warehouseId ?? "all";
  const warehouseId = urlState.warehouseId ?? undefined;
  const categoryValue = useMemo<CategorySelection>(() => {
    if (urlState.categoryId === null) {
      return ["all"];
    }

    const categoryPath = getCategoryPath(filterCategories, urlState.categoryId);
    return categoryPath.length > 0 ? categoryPath : [urlState.categoryId];
  }, [filterCategories, urlState.categoryId]);
  const categoryId = urlState.categoryId ?? undefined;
  const searchValue = urlState.search;
  const statusValue = urlState.status;
  const stockStatusValue = urlState.stockStatus;
  const viewMode: InventoryViewMode = urlState.view;
  const currentPage = urlState.page;
  const pageSize = urlState.pageSize;
  const categoryOptions = useMemo(
    () => toCategoryOptions(filterCategories, true),
    [filterCategories],
  );
  const hasCategories = hasSecondLevelCategories(categories);
  const categoryServiceError = categoriesError ?? filterCategoriesError;
  const currentCategoryLabel = getCategoryLabel(filterCategories, categoryValue);
  const currentListState = urlState;
  const selectedProducts = useMemo(
    () => products.filter((product) => selectedProductIds.has(product.id)),
    [products, selectedProductIds],
  );
  const selectedStatus: BatchStatusSelection = useMemo(() => {
    if (selectedProducts.length === 0) {
      return "none";
    }
    const activeCount = selectedProducts.filter((product) => product.isActive).length;
    if (activeCount === selectedProducts.length) {
      return "active";
    }
    if (activeCount === 0) {
      return "inactive";
    }
    return "mixed";
  }, [selectedProducts]);
  const allCurrentPageSelected =
    products.length > 0 && products.every((product) => selectedProductIds.has(product.id));
  const productListHref = useMemo(
    () => buildProductListHref(currentListState),
    [currentListState],
  );
  const getProductDetailHref = useCallback(
    (productId: number) => buildProductDetailHref(productId, productListHref),
    [productListHref],
  );
  const handleBeforeProductDetail = useCallback(() => {
    saveProductListScroll(productListHref);
  }, [productListHref]);
  const selectedWarehouseName =
    warehouseValue === "all"
      ? "全部仓库"
      : warehouses.find((warehouse) => warehouse.id === warehouseValue)?.name ??
        "仓库";
  const currentRangeLabel = `${selectedWarehouseName} / ${currentCategoryLabel}`;
  const requestKey = `${currentPage}:${pageSize}:${reloadCounter}:${searchValue}:${statusValue}:${stockStatusValue}:${warehouseId ?? "all"}:${categoryId ?? "all"}`;
  const loading = completedRequestKey !== requestKey;
  const visibleLoadError =
    loadError?.requestKey === requestKey ? loadError.message : null;

  useEffect(() => {
    if (loading || restoredScrollUrlsRef.current.has(productListHref)) {
      return;
    }

    const savedScrollY = readProductListScroll(productListHref);
    if (savedScrollY === null) {
      restoredScrollUrlsRef.current.add(productListHref);
      return;
    }

    let animationFrame = 0;
    let cancelled = false;
    let frameCount = 0;
    const restoreScroll = () => {
      if (cancelled) {
        return;
      }

      const maxScrollY = Math.max(
        0,
        document.documentElement.scrollHeight - window.innerHeight,
      );
      const listRendered = Boolean(
        document.querySelector(
          viewMode === "gallery" ? ".product-gallery" : ".inventory-table",
        ),
      );

      if (
        (listRendered && maxScrollY >= savedScrollY) ||
        frameCount >= 120
      ) {
        window.scrollTo({
          top: Math.min(savedScrollY, maxScrollY),
          left: 0,
          behavior: "auto",
        });
        restoredScrollUrlsRef.current.add(productListHref);
        return;
      }

      frameCount += 1;
      animationFrame = window.requestAnimationFrame(restoreScroll);
    };

    animationFrame = window.requestAnimationFrame(restoreScroll);
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(animationFrame);
    };
  }, [loading, productListHref, viewMode]);

  useEffect(() => {
    const controller = new AbortController();

    void listCategories(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setCategories(result);
          setCategoriesError(null);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setCategories([]);
          setCategoriesError(getErrorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setCategoriesLoading(false);
        }
      });

    return () => controller.abort();
  }, [categoriesReloadCounter]);

  useEffect(() => {
    const controller = new AbortController();

    void listCategories(controller.signal, warehouseId)
      .then((result) => {
        if (!controller.signal.aborted) {
          setFilterCategories(result);
          setFilterCategoriesError(null);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setFilterCategories([]);
          setFilterCategoriesError(getErrorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setFilterCategoriesLoading(false);
        }
      });

    return () => controller.abort();
  }, [filterCategoriesReloadCounter, warehouseId]);

  useEffect(() => {
    const controller = new AbortController();

    void listWarehouses(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) {
          setWarehouses(result);
          setWarehousesError(null);
        }
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setWarehouses([]);
          setWarehousesError(getErrorMessage(error));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setWarehousesLoading(false);
        }
      });

    return () => controller.abort();
  }, [warehousesReloadCounter]);

  useEffect(() => {
    const controller = new AbortController();

    void listProducts(
      {
        page: currentPage,
        pageSize,
        search: searchValue,
        categoryId,
        warehouseId,
        status: statusValue,
        stockStatus: stockStatusValue,
      },
      controller.signal,
    )
      .then((response) => {
        if (controller.signal.aborted) {
          return;
        }
        setProducts(response.items.map(toInventoryProduct));
        setTotal(response.total);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setProducts([]);
        setTotal(0);
        setLoadError({ requestKey, message: getErrorMessage(error) });
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setCompletedRequestKey(requestKey);
        }
      });

    return () => controller.abort();
  }, [
    categoryId,
    currentPage,
    pageSize,
    reloadCounter,
    requestKey,
    searchValue,
    stockStatusValue,
    statusValue,
    warehouseId,
  ]);

  const activeMovementProduct = stockMovement
    ? products.find((product) => product.id === stockMovement.product.id)
    : undefined;

  function replaceProductListUrl(overrides: Partial<ProductListState>) {
    router.replace(
      buildProductListHref({ ...currentListState, ...overrides }),
      { scroll: false },
    );
  }

  function handleCategoryChange(value: CategorySelection) {
    clearSelection();
    const nextCategoryValue = value.length > 0 ? value : ["all"];
    const nextCategory = nextCategoryValue[nextCategoryValue.length - 1];
    replaceProductListUrl({
      categoryId: typeof nextCategory === "number" ? nextCategory : null,
      page: 1,
    });
  }

  function handleWarehouseChange(value: WarehouseSelection) {
    clearSelection();
    setFilterCategoriesLoading(true);
    setFilterCategoriesError(null);
    replaceProductListUrl({
      warehouseId: value === "all" ? null : value,
      categoryId: null,
      page: 1,
    });
  }

  function handleSearchChange(value: string) {
    clearSelection();
    replaceProductListUrl({ search: value, page: 1 });
  }

  function handleStatusChange(value: ProductStatus) {
    clearSelection();
    replaceProductListUrl({ status: value, page: 1 });
  }

  function handleStockStatusChange(value: StockStatus) {
    clearSelection();
    replaceProductListUrl({ stockStatus: value, page: 1 });
  }

  function handlePaginationChange(nextPage: number, nextPageSize: number) {
    clearSelection();
    const resolvedPage = nextPageSize === pageSize ? nextPage : 1;
    replaceProductListUrl({ page: resolvedPage, pageSize: nextPageSize });
  }

  function handleViewModeChange(value: InventoryViewMode) {
    replaceProductListUrl({ view: value });
  }

  function enterBatchMode() {
    setSelectedProductIds(new Set());
    setBatchMode(true);
  }

  function exitBatchMode() {
    setSelectedProductIds(new Set());
    setBatchMode(false);
    setBatchCategoryModalOpen(false);
  }

  function selectCurrentPage() {
    setSelectedProductIds(new Set(products.map((product) => product.id)));
  }

  function clearSelection() {
    setSelectedProductIds(new Set());
  }

  async function confirmBatchCategory(categoryId: number) {
    setBatchCategorySubmitting(true);
    try {
      const result = await batchUpdateProductCategory(
        Array.from(selectedProductIds),
        categoryId,
      );
      setBatchCategoryModalOpen(false);
      clearSelection();
      setReloadCounter((value) => value + 1);
      messageApi.success(`已修改 ${result.updated_count} 个商品的分类`);
    } catch (error) {
      messageApi.error(getErrorMessage(error));
    } finally {
      setBatchCategorySubmitting(false);
    }
  }

  function adjustPageAfterStatusBatch(updatedCount: number) {
    if (statusValue === "all") {
      return;
    }

    const remainingTotal = Math.max(0, total - updatedCount);
    const lastPage = Math.max(1, Math.ceil(remainingTotal / pageSize));
    if (currentPage > lastPage) {
      replaceProductListUrl({ page: lastPage });
    }
  }

  async function confirmBatchStatus(isActive: boolean) {
    const productIds = Array.from(selectedProductIds);
    if (productIds.length === 0) {
      return;
    }

    try {
      const result = isActive
        ? await batchActivateProducts(productIds)
        : await batchDeactivateProducts(productIds);
      adjustPageAfterStatusBatch(result.updated_count);
      clearSelection();
      setReloadCounter((value) => value + 1);
      messageApi.success(
        isActive
          ? `已启用 ${result.updated_count} 个商品`
          : `已停用 ${result.updated_count} 个商品`,
      );
    } catch (error) {
      messageApi.error(getErrorMessage(error));
      throw error;
    }
  }

  function confirmBatchDeactivate() {
    if (selectedStatus !== "active") {
      return;
    }

    const inStockCount = selectedProducts.filter(
      (product) => product.totalCartonCount > 0,
    ).length;
    const zeroStockCount = selectedProducts.length - inStockCount;
    modal.confirm({
      title: `确认停用 ${selectedProducts.length} 个商品？`,
      content: (
        <div>
          <p>有库存商品：{inStockCount} 个</p>
          <p>零库存商品：{zeroStockCount} 个</p>
          <p>停用不会删除商品、库存、包装规格或历史流水。</p>
        </div>
      ),
      okText: "确认停用",
      cancelText: "取消",
      okButtonProps: { danger: true },
      onOk: () => confirmBatchStatus(false),
    });
  }

  function confirmBatchActivate() {
    if (selectedStatus !== "inactive") {
      return;
    }

    modal.confirm({
      title: `确认启用 ${selectedProducts.length} 个商品？`,
      content: "启用后商品会重新出现在正常商品列表中，库存及历史流水不会受到影响。",
      okText: "确认启用",
      cancelText: "取消",
      onOk: () => confirmBatchStatus(true),
    });
  }

  async function saveProduct(values: ProductEditorFormValues) {
    const categoryId = values.categoryPath[1];
    if (!categoryId) {
      throw new Error("请选择一个二级分类后保存商品。");
    }
    if (!values.warehouseId) {
      throw new Error("请选择所属仓库后保存商品。");
    }

    const editableFields = {
      category_id: categoryId,
      warehouse_id: values.warehouseId,
      image_path: values.imagePath,
      thumbnail_path: values.thumbnailPath,
      size: values.size.trim(),
      unit: values.unit,
      price: values.price,
      remark: values.remark.trim() || null,
    };

    if (productEditor?.product) {
      const updatePayload: ProductUpdatePayload = {
        ...editableFields,
        packagings: values.packagings.map((packaging) => ({
          ...(packaging.id ? { id: packaging.id } : {}),
          packing_qty: packaging.packingQty,
        })),
      };
      await updateProductRequest(productEditor.product.id, updatePayload);
    } else {
      const createPayload: ProductCreatePayload = {
        ...editableFields,
        packagings: values.packagings.map((packaging) => ({
          packing_qty: packaging.packingQty,
        })),
      };
      await createProductRequest(createPayload);
    }

    setProductEditor(null);
    setFilterCategoriesLoading(true);
    setFilterCategoriesError(null);
    replaceProductListUrl({ search: "", categoryId: null, page: 1 });
    setReloadCounter((value) => value + 1);
    setFilterCategoriesReloadCounter((value) => value + 1);
    messageApi.success(
      productEditor?.product ? "商品信息已保存到数据库" : "商品已保存到数据库",
    );
  }

  async function setProductActive(product: InventoryProduct, isActive: boolean) {
    try {
      if (isActive) {
        await activateProduct(product.id);
      } else {
        await deactivateProduct(product.id);
      }
      setReloadCounter((value) => value + 1);
      messageApi.success(isActive ? "商品已重新启用" : "商品已停用");
    } catch (error) {
      messageApi.error(getErrorMessage(error));
      throw error;
    }
  }

  function confirmProductStatusChange(product: InventoryProduct, isActive: boolean) {
    if (isActive) {
      modal.confirm({
        title: "重新启用商品？",
        content: "商品将回到默认在用商品列表，商品编号、图片、包装规格和库存保持不变。",
        okText: "确认启用",
        cancelText: "取消",
        onOk: () => setProductActive(product, true),
      });
      return;
    }

    const hasStock = product.totalCartonCount > 0;
    modal.confirm({
      title: "停用商品？",
      content: (
        <div>
          <p>停用后，该商品将从默认库存列表中隐藏，历史数据仍会保留。</p>
          {hasStock && (
            <>
              <p>该商品当前还有 {product.totalCartonCount} 箱库存。</p>
              <p>停用不会清空库存。</p>
            </>
          )}
        </div>
      ),
      okText: "确认停用",
      cancelText: "取消",
      onOk: () => setProductActive(product, false),
    });
  }

  async function confirmStockMovement(values: StockMovementValues) {
    if (!stockMovement) {
      return;
    }

    const { direction, product } = stockMovement;
    try {
      await createStockMovement(product.id, direction, values);
      setStockMovement(null);
      setReloadCounter((value) => value + 1);
      messageApi.success(direction === "in" ? "入库成功，库存已更新" : "出库成功，库存已更新");
    } catch (error) {
      messageApi.error(getErrorMessage(error));
      throw error;
    }
  }

  async function confirmStockAdjustment(values: StockAdjustmentValues) {
    if (!adjustmentProduct) {
      return;
    }

    try {
      await createStockAdjustment(adjustmentProduct.id, values);
      setAdjustmentProduct(null);
      setReloadCounter((value) => value + 1);
      messageApi.success("库存调整成功，库存已更新");
    } catch (error) {
      messageApi.error(getErrorMessage(error));
      throw error;
    }
  }

  return (
    <>
      {messageContextHolder}
      <div className="inventory-page">
        <div className="page-heading">
          <Typography.Title level={1}>商品库存</Typography.Title>
          <div className="page-heading-actions">
            {!batchMode && (
              <Button onClick={enterBatchMode}>批量操作</Button>
            )}
            <Button
              className="add-product-button"
              type="primary"
              icon={<PlusOutlined />}
              disabled={
                warehousesLoading || Boolean(warehousesError) || warehouses.length === 0
              }
              onClick={() => setProductEditor({})}
            >
              新增商品
            </Button>
          </div>
        </div>

        {warehousesError && (
          <Alert
            className="inventory-load-error"
            type="error"
            showIcon
            title="仓库服务暂不可用"
            description={warehousesError}
            action={
              <Button
                size="small"
                onClick={() => {
                  setWarehousesLoading(true);
                  setWarehousesReloadCounter((value) => value + 1);
                }}
              >
                重试
              </Button>
            }
          />
        )}
        {!warehousesLoading && !warehousesError && warehouses.length === 0 && (
          <Alert
            className="inventory-load-error"
            type="warning"
            showIcon
            title="仓库列表为空，请先运行开发仓库初始化命令。"
          />
        )}

        {categoryServiceError && (
          <Alert
            className="inventory-load-error"
            type="error"
            showIcon
            title="分类服务暂不可用"
            description={categoryServiceError}
            action={
              <Button
                size="small"
                onClick={() => {
                  setCategoriesLoading(true);
                  setFilterCategoriesLoading(true);
                  setCategoriesError(null);
                  setFilterCategoriesError(null);
                  setCategoriesReloadCounter((value) => value + 1);
                  setFilterCategoriesReloadCounter((value) => value + 1);
                }}
              >
                重试
              </Button>
            }
          />
        )}
        {!categoriesLoading &&
          !categoriesError &&
          !filterCategoriesError &&
          !hasCategories && (
          <Alert
            className="inventory-load-error"
            type="info"
            showIcon
            title="请先到分类管理新增一级和二级分类，再为商品选择分类。"
          />
        )}

        <InventoryToolbar
          warehouses={warehouses}
          warehouseValue={warehouseValue}
          onWarehouseChange={handleWarehouseChange}
          warehouseDisabled={
            warehousesLoading || Boolean(warehousesError) || warehouses.length === 0
          }
          categoryOptions={categoryOptions}
          categoryValue={categoryValue}
          onCategoryChange={handleCategoryChange}
          searchValue={searchValue}
          onSearchChange={handleSearchChange}
          statusValue={statusValue}
          onStatusChange={handleStatusChange}
          stockStatusValue={stockStatusValue}
          onStockStatusChange={handleStockStatusChange}
          resultCount={total}
          categoryDisabled={
            categoriesLoading ||
            filterCategoriesLoading ||
            Boolean(categoriesError) ||
            Boolean(filterCategoriesError)
          }
        />

        {batchMode && (
          <BatchActionBar
            selectedCount={selectedProductIds.size}
            currentPageCount={products.length}
            allCurrentPageSelected={allCurrentPageSelected}
            statusSelection={selectedStatus}
            onSelectCurrentPage={selectCurrentPage}
            onClearSelection={clearSelection}
            onChangeCategory={() => setBatchCategoryModalOpen(true)}
            onDeactivate={confirmBatchDeactivate}
            onActivate={confirmBatchActivate}
            onExit={exitBatchMode}
          />
        )}

        {visibleLoadError && (
          <Alert
            className="inventory-load-error"
            type="error"
            showIcon
            title="商品服务暂不可用"
            description={visibleLoadError}
            action={
              <Button
                size="small"
                onClick={() => setReloadCounter((value) => value + 1)}
              >
                重试
              </Button>
            }
          />
        )}

        <section className="inventory-panel" aria-label="商品库存列表">
          <div className="inventory-panel-heading">
            <div className="category-context">
              <span className="category-context-label">当前范围</span>
              <strong className="category-path">{currentRangeLabel}</strong>
            </div>
            <Segmented<InventoryViewMode>
              className="inventory-view-switcher"
              aria-label="切换库存视图"
              value={viewMode}
              options={[
                { label: "表格视图", value: "table" },
                { label: "画廊视图", value: "gallery" },
              ]}
              onChange={handleViewModeChange}
            />
          </div>
          {viewMode === "table" ? (
            <ProductTable
              products={products}
              total={total}
              loading={loading}
              batchMode={batchMode}
              selectedIds={selectedProductIds}
              onSelectionChange={setSelectedProductIds}
              currentPage={currentPage}
              pageSize={pageSize}
              getProductDetailHref={getProductDetailHref}
              onBeforeProductDetail={handleBeforeProductDetail}
              onPageChange={handlePaginationChange}
              onStockIn={(product) =>
                setStockMovement({ product, direction: "in" })
              }
              onStockOut={(product) =>
                setStockMovement({ product, direction: "out" })
              }
              onViewMovements={(product) => setMovementProduct(product)}
              onAdjust={(product) => setAdjustmentProduct(product)}
              onEdit={(product) => setProductEditor({ product })}
              onDeactivate={(product) => confirmProductStatusChange(product, false)}
              onActivate={(product) => confirmProductStatusChange(product, true)}
            />
          ) : (
            <ProductGallery
              products={products}
              total={total}
              loading={loading}
              batchMode={batchMode}
              selectedIds={selectedProductIds}
              onSelectionChange={setSelectedProductIds}
              currentPage={currentPage}
              pageSize={pageSize}
              getProductDetailHref={getProductDetailHref}
              onBeforeProductDetail={handleBeforeProductDetail}
              onPageChange={handlePaginationChange}
              onStockIn={(product) =>
                setStockMovement({ product, direction: "in" })
              }
              onStockOut={(product) =>
                setStockMovement({ product, direction: "out" })
              }
            />
          )}
        </section>
      </div>

      {productEditor && (
        <ProductEditorModal
          product={productEditor.product}
          categories={categories}
          warehouses={warehouses}
          defaultWarehouseId={productEditor.product ? undefined : warehouseId}
          onCancel={() => setProductEditor(null)}
          onSave={saveProduct}
        />
      )}

      <BatchCategoryModal
        key={batchCategoryModalOpen ? "batch-category-open" : "batch-category-closed"}
        open={batchCategoryModalOpen}
        selectedCount={selectedProductIds.size}
        categories={categories}
        categoriesLoading={categoriesLoading}
        categoriesError={categoriesError}
        confirmLoading={batchCategorySubmitting}
        onCancel={() => setBatchCategoryModalOpen(false)}
        onConfirm={confirmBatchCategory}
      />

      {stockMovement && activeMovementProduct && (
        <StockMovementModal
          key={stockMovement.product.id + "-" + stockMovement.direction}
          product={activeMovementProduct}
          direction={stockMovement.direction}
          onCancel={() => setStockMovement(null)}
          onConfirm={confirmStockMovement}
        />
      )}

      {movementProduct && (
        <InventoryMovementsModal
          product={movementProduct}
          onCancel={() => setMovementProduct(null)}
        />
      )}

      {adjustmentProduct && (
        <InventoryAdjustmentModal
          product={adjustmentProduct}
          onCancel={() => setAdjustmentProduct(null)}
          onConfirm={confirmStockAdjustment}
        />
      )}
    </>
  );
}
