"use client";

import { PlusOutlined } from "@ant-design/icons";
import { Alert, Button, message, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import ProductEditorModal from "@/components/inventory/ProductEditorModal";
import ProductTable from "@/components/inventory/ProductTable";
import InventoryToolbar from "@/components/inventory/InventoryToolbar";
import StockMovementModal from "@/components/inventory/StockMovementModal";
import { listCategories } from "@/lib/api/categories";
import {
  createProduct as createProductRequest,
  listProducts,
  updateProduct as updateProductRequest,
} from "@/lib/api/products";
import {
  getCategoryLabel,
  hasSecondLevelCategories,
  toCategoryOptions,
} from "@/lib/categories";
import type {
  CategorySelection,
  CategoryTreeNode,
  InventoryProduct,
  ProductApiRecord,
  ProductCreatePayload,
  ProductEditorFormValues,
  ProductUpdatePayload,
  StockMovementDirection,
  StockMovementValues,
} from "@/types/inventory";

interface ProductEditorState {
  product?: InventoryProduct;
}

interface StockMovementState {
  product: InventoryProduct;
  direction: StockMovementDirection;
}

function toInventoryProduct(product: ProductApiRecord): InventoryProduct {
  return {
    id: product.id,
    categoryId: product.category_id,
    imagePath: product.image_path,
    size: product.size,
    packingQty: product.packing_qty,
    unit: product.unit,
    price: product.price,
    cartonCount: product.carton_count,
    remark: product.remark ?? "",
  };
}

function getErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "服务请求失败，请重试。";
}

export default function InventoryWorkspace() {
  const [categories, setCategories] = useState<CategoryTreeNode[]>([]);
  const [categoriesLoading, setCategoriesLoading] = useState(true);
  const [categoriesError, setCategoriesError] = useState<string | null>(null);
  const [categoriesReloadCounter, setCategoriesReloadCounter] = useState(0);
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
  const [categoryValue, setCategoryValue] = useState<CategorySelection>(["all"]);
  const [searchValue, setSearchValue] = useState("");
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [productEditor, setProductEditor] = useState<ProductEditorState | null>(
    null,
  );
  const [stockMovement, setStockMovement] =
    useState<StockMovementState | null>(null);
  const [messageApi, messageContextHolder] = message.useMessage();
  const selectedCategory = categoryValue[categoryValue.length - 1];
  const categoryId =
    typeof selectedCategory === "number" ? selectedCategory : undefined;
  const categoryOptions = useMemo(
    () => toCategoryOptions(categories, true),
    [categories],
  );
  const hasCategories = hasSecondLevelCategories(categories);
  const currentCategoryLabel = getCategoryLabel(categories, categoryValue);
  const requestKey = `${currentPage}:${pageSize}:${reloadCounter}:${searchValue}:${categoryId ?? "all"}`;
  const loading = completedRequestKey !== requestKey;
  const visibleLoadError =
    loadError?.requestKey === requestKey ? loadError.message : null;

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

    void listProducts(
      { page: currentPage, pageSize, search: searchValue, categoryId },
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
  }, [categoryId, currentPage, pageSize, reloadCounter, requestKey, searchValue]);

  const activeMovementProduct = stockMovement
    ? products.find((product) => product.id === stockMovement.product.id)
    : undefined;

  function handleCategoryChange(value: CategorySelection) {
    setCategoryValue(value.length > 0 ? value : ["all"]);
    setCurrentPage(1);
  }

  function handleSearchChange(value: string) {
    setSearchValue(value);
    setCurrentPage(1);
  }

  function handlePaginationChange(nextPage: number, nextPageSize: number) {
    setCurrentPage(nextPage);
    setPageSize(nextPageSize);
  }

  async function saveProduct(values: ProductEditorFormValues) {
    const categoryId = values.categoryPath[1];
    if (!categoryId) {
      throw new Error("请选择一个二级分类后保存商品。");
    }

    const editableFields: Omit<ProductCreatePayload, "image_path"> = {
      category_id: categoryId,
      size: values.size.trim(),
      packing_qty: values.packingQty,
      unit: values.unit,
      price: values.price,
      carton_count: values.cartonCount,
      remark: values.remark.trim() || null,
    };

    if (productEditor?.product) {
      const updatePayload: ProductUpdatePayload = editableFields;
      await updateProductRequest(productEditor.product.id, updatePayload);
    } else {
      const createPayload: ProductCreatePayload = {
        image_path: null,
        ...editableFields,
      };
      await createProductRequest(createPayload);
    }

    setProductEditor(null);
    setSearchValue("");
    setCurrentPage(1);
    setReloadCounter((value) => value + 1);
    messageApi.success(
      productEditor?.product ? "商品信息已保存到数据库" : "商品已保存到数据库",
    );
  }

  function confirmStockMovement(values: StockMovementValues) {
    if (!stockMovement) {
      return;
    }

    const { direction, product } = stockMovement;
    setProducts((previousProducts) =>
      previousProducts.map((item) =>
        item.id === product.id
          ? {
              ...item,
              cartonCount:
                item.cartonCount +
                (direction === "in" ? values.quantity : -values.quantity),
            }
          : item,
      ),
    );
    setStockMovement(null);
    messageApi.warning("入库/出库原型只临时改变本页数字，刷新后会恢复数据库中的箱数。");
  }

  return (
    <>
      {messageContextHolder}
      <div className="inventory-page">
        <div className="page-heading">
          <Typography.Title level={1}>商品库存</Typography.Title>
          <Button
            className="add-product-button"
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => setProductEditor({})}
          >
            新增商品
          </Button>
        </div>

        {categoriesError && (
          <Alert
            className="inventory-load-error"
            type="error"
            showIcon
            title="分类服务暂不可用"
            description={categoriesError}
            action={
              <Button
                size="small"
                onClick={() => {
                  setCategoriesLoading(true);
                  setCategoriesReloadCounter((value) => value + 1);
                }}
              >
                重试
              </Button>
            }
          />
        )}
        {!categoriesLoading && !categoriesError && !hasCategories && (
          <Alert
            className="inventory-load-error"
            type="info"
            showIcon
            title="请先到分类管理新增一级和二级分类，再为商品选择分类。"
          />
        )}

        <InventoryToolbar
          categoryOptions={categoryOptions}
          categoryValue={categoryValue}
          onCategoryChange={handleCategoryChange}
          searchValue={searchValue}
          onSearchChange={handleSearchChange}
          resultCount={total}
          categoryDisabled={categoriesLoading || Boolean(categoriesError)}
        />

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
              <span className="category-context-label">当前分类</span>
              <strong className="category-path">{currentCategoryLabel}</strong>
            </div>
          </div>
          <ProductTable
            products={products}
            total={total}
            loading={loading}
            currentPage={currentPage}
            pageSize={pageSize}
            onPageChange={handlePaginationChange}
            onStockIn={(product) =>
              setStockMovement({ product, direction: "in" })
            }
            onStockOut={(product) =>
              setStockMovement({ product, direction: "out" })
            }
            onEdit={(product) => setProductEditor({ product })}
          />
        </section>
      </div>

      {productEditor && (
        <ProductEditorModal
          product={productEditor.product}
          categories={categories}
          onCancel={() => setProductEditor(null)}
          onSave={saveProduct}
        />
      )}

      {stockMovement && activeMovementProduct && (
        <StockMovementModal
          key={stockMovement.product.id + "-" + stockMovement.direction}
          product={activeMovementProduct}
          direction={stockMovement.direction}
          onCancel={() => setStockMovement(null)}
          onConfirm={confirmStockMovement}
        />
      )}
    </>
  );
}
