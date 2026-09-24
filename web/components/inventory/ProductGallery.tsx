"use client";

import { Button, Checkbox, Empty, Pagination, Skeleton, Tag } from "antd";
import { useRouter } from "next/navigation";
import type { SyntheticEvent } from "react";
import ProductImage from "@/components/inventory/ProductImage";
import type { InventoryProduct } from "@/types/inventory";

interface ProductGalleryProps {
  products: InventoryProduct[];
  total: number;
  loading: boolean;
  batchMode: boolean;
  selectedIds: Set<number>;
  onProductSelectionChange: (product: InventoryProduct, selected: boolean) => void;
  currentPage: number;
  pageSize: number;
  getProductDetailHref: (productId: number) => string;
  onBeforeProductDetail: () => void;
  onPageChange: (currentPage: number, pageSize: number) => void;
  onStockIn: (product: InventoryProduct) => void;
  onStockOut: (product: InventoryProduct) => void;
}

function getProductLabel(product: InventoryProduct): string {
  return product.productCode ?? (product.size.trim() || "商品详情");
}

function stopCardInteraction(event: SyntheticEvent) {
  event.stopPropagation();
}

function GalleryLoading() {
  return (
    <div className="product-gallery-grid product-gallery-loading" aria-label="商品加载中">
      {Array.from({ length: 6 }, (_, index) => (
        <div className="product-gallery-skeleton-card" key={index}>
          <div className="product-gallery-skeleton-image" />
          <Skeleton active title={{ width: "62%" }} paragraph={{ rows: 1 }} />
        </div>
      ))}
    </div>
  );
}

export default function ProductGallery({
  products,
  total,
  loading,
  batchMode,
  selectedIds,
  onProductSelectionChange,
  currentPage,
  pageSize,
  getProductDetailHref,
  onBeforeProductDetail,
  onPageChange,
  onStockIn,
  onStockOut,
}: ProductGalleryProps) {
  const router = useRouter();

  function toggleProductSelection(product: InventoryProduct) {
    onProductSelectionChange(product, !selectedIds.has(product.id));
  }

  if (loading && products.length === 0) {
    return <GalleryLoading />;
  }

  if (!loading && products.length === 0) {
    return (
      <div className="product-gallery-empty">
        <Empty description="没有符合条件的商品" />
      </div>
    );
  }

  return (
    <div className="product-gallery">
      <div className="product-gallery-grid" aria-label="商品画廊">
        {products.map((product) => {
          const label = getProductLabel(product);
          const selected = selectedIds.has(product.id);
          return (
            <article
              key={product.id}
              className={`product-gallery-card${product.isActive ? "" : " product-gallery-card-inactive"}${batchMode && selected ? " product-gallery-card-selected" : ""}`}
              role={batchMode ? "checkbox" : "link"}
              tabIndex={0}
              aria-checked={batchMode ? selected : undefined}
              aria-label={`${label}，当前库存 ${product.totalCartonCount} 箱`}
              onClick={() => {
                if (batchMode) {
                  toggleProductSelection(product);
                  return;
                }
                onBeforeProductDetail();
                router.push(getProductDetailHref(product.id));
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  if (batchMode) {
                    toggleProductSelection(product);
                    return;
                  }
                  onBeforeProductDetail();
                  router.push(getProductDetailHref(product.id));
                }
              }}
            >
              <div className="product-gallery-image">
                {batchMode && (
                  <Checkbox
                    className="product-gallery-checkbox"
                    checked={selected}
                    aria-label={`选择商品 ${label}`}
                    onClick={stopCardInteraction}
                    onChange={() => toggleProductSelection(product)}
                  />
                )}
                <ProductImage
                  imagePath={product.imagePath}
                  thumbnailPath={product.thumbnailPath}
                  alt={`${label}商品图片`}
                  width={320}
                  height={220}
                  loading="lazy"
                  enablePreview={false}
                />
                {!product.isActive && (
                  <Tag className="product-gallery-inactive-tag" color="default">
                    已停用
                  </Tag>
                )}
              </div>
              <div className="product-gallery-content">
                <div className="product-gallery-meta">
                  <span className="product-gallery-code">{product.productCode ?? "—"}</span>
                  <span className="product-gallery-stock">
                    <strong>{product.totalCartonCount}</strong>
                    <span>箱</span>
                  </span>
                </div>
                {product.isActive && (
                  <div className="product-gallery-actions">
                    <Button
                      size="small"
                      type="link"
                      onClick={(event) => {
                        stopCardInteraction(event);
                        onStockIn(product);
                      }}
                      onKeyDown={stopCardInteraction}
                    >
                      入库
                    </Button>
                    <Button
                      size="small"
                      type="link"
                      onClick={(event) => {
                        stopCardInteraction(event);
                        onStockOut(product);
                      }}
                      onKeyDown={stopCardInteraction}
                    >
                      出库
                    </Button>
                  </div>
                )}
              </div>
            </article>
          );
        })}
      </div>
      <Pagination
        className="product-gallery-pagination"
        current={currentPage}
        pageSize={pageSize}
        total={total}
        showSizeChanger
        pageSizeOptions={[20, 50, 100]}
        showTotal={(itemCount, range) =>
          range[0] === 0
            ? "暂无商品"
            : `${range[0]}-${range[1]} 条，共 ${itemCount} 条`
        }
        onChange={onPageChange}
      />
    </div>
  );
}
