"use client";

import { Button, Empty, Pagination, Skeleton, Tag } from "antd";
import { useRouter } from "next/navigation";
import type { SyntheticEvent } from "react";
import ProductImage from "@/components/inventory/ProductImage";
import type { InventoryProduct } from "@/types/inventory";

interface ProductGalleryProps {
  products: InventoryProduct[];
  total: number;
  loading: boolean;
  currentPage: number;
  pageSize: number;
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
  currentPage,
  pageSize,
  onPageChange,
  onStockIn,
  onStockOut,
}: ProductGalleryProps) {
  const router = useRouter();

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
          return (
            <article
              key={product.id}
              className={`product-gallery-card${product.isActive ? "" : " product-gallery-card-inactive"}`}
              role="link"
              tabIndex={0}
              aria-label={`${label}，当前库存 ${product.totalCartonCount} 箱`}
              onClick={() => router.push(`/products/${product.id}`)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  router.push(`/products/${product.id}`);
                }
              }}
            >
              <div className="product-gallery-image">
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
