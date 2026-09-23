import type { InventoryProduct, ProductApiRecord } from "@/types/inventory";

export function toInventoryProduct(product: ProductApiRecord): InventoryProduct {
  return {
    id: product.id,
    productCode: product.product_code,
    isActive: product.is_active,
    categoryId: product.category_id,
    warehouseId: product.warehouse_id,
    categoryName: product.category_name,
    warehouseName: product.warehouse_name,
    imagePath: product.image_path,
    thumbnailPath: product.thumbnail_path,
    size: product.size,
    unit: product.unit,
    price: product.price,
    packagings: product.packagings.map((packaging) => ({
      id: packaging.id,
      packingQty: packaging.packing_qty,
      cartonCount: packaging.carton_count,
      sortOrder: packaging.sort_order,
    })),
    totalCartonCount: product.total_carton_count,
    remark: product.remark ?? "",
  };
}
