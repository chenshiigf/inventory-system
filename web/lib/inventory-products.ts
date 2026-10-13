import type { InventoryProduct, ProductApiRecord, ProductEditorFormValues, ProductUpdatePayload } from "@/types/inventory";

// PATCH omission means leave packaging rows untouched. Never infer identity by
// position or packing quantity: rows can be removed, added or reordered.
export function packagingUpdateFields(
  original: InventoryProduct["packagings"],
  submitted: ProductEditorFormValues["packagings"],
): Pick<ProductUpdatePayload, "packagings"> {
  const unchanged = original.length === submitted.length && original.every((row, i) =>
    row.id === submitted[i].id && row.packingQty === submitted[i].packingQty);
  return unchanged ? {} : { packagings: submitted.map((row) => ({
    ...(row.id !== undefined ? { id: row.id } : {}),
    packing_qty: row.packingQty,
  })) };
}

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
