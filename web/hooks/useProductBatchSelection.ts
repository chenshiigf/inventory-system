import { useCallback, useMemo, useState } from "react";
import type { InventoryProduct } from "@/types/inventory";

export const MAX_PRODUCT_BATCH_SELECTION = 100;

export interface SelectedProductSnapshot {
  id: number;
  productCode: string | null;
  isActive: boolean;
  totalCartonCount: number;
}

function toSnapshot(product: InventoryProduct): SelectedProductSnapshot {
  return {
    id: product.id,
    productCode: product.productCode,
    isActive: product.isActive,
    totalCartonCount: product.totalCartonCount,
  };
}

export function useProductBatchSelection() {
  const [registry, setRegistry] = useState<Map<number, SelectedProductSnapshot>>(
    () => new Map(),
  );

  const selectedProducts = useMemo(() => Array.from(registry.values()), [registry]);
  const selectedProductIds = useMemo(() => new Set(registry.keys()), [registry]);

  const setProductSelected = useCallback(
    (product: InventoryProduct, selected: boolean): boolean => {
      const isAlreadySelected = registry.has(product.id);
      if (isAlreadySelected === selected) {
        return true;
      }

      if (selected && registry.size >= MAX_PRODUCT_BATCH_SELECTION) {
        return false;
      }

      setRegistry((current) => {
        const next = new Map(current);
        if (selected) {
          next.set(product.id, toSnapshot(product));
        } else {
          next.delete(product.id);
        }
        return next;
      });
      return true;
    },
    [registry],
  );

  const addProducts = useCallback(
    (products: InventoryProduct[]): boolean => {
      const uniqueProducts = Array.from(
        new Map(products.map((product) => [product.id, product])).values(),
      );
      const productsToAdd = uniqueProducts.filter((product) => !registry.has(product.id));
      if (
        registry.size + productsToAdd.length >
        MAX_PRODUCT_BATCH_SELECTION
      ) {
        return false;
      }

      if (productsToAdd.length === 0) {
        return true;
      }

      setRegistry((current) => {
        const next = new Map(current);
        for (const product of productsToAdd) {
          next.set(product.id, toSnapshot(product));
        }
        return next;
      });
      return true;
    },
    [registry],
  );

  const removeProducts = useCallback((productIds: number[]) => {
    if (productIds.length === 0) {
      return;
    }

    setRegistry((current) => {
      const next = new Map(current);
      for (const productId of productIds) {
        next.delete(productId);
      }
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    setRegistry(new Map());
  }, []);

  return {
    selectedProducts,
    selectedProductIds,
    selectedCount: registry.size,
    setProductSelected,
    addProducts,
    removeProducts,
    clear,
  };
}
