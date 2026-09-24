import type { ProductStatus, StockStatus } from "@/types/inventory";

export type ProductListView = "table" | "gallery";

export interface ProductListState {
  view: ProductListView;
  warehouseId: number | null;
  categoryId: number | null;
  status: ProductStatus;
  stockStatus: StockStatus;
  search: string;
  page: number;
  pageSize: number;
}

export const DEFAULT_PRODUCT_LIST_STATE: ProductListState = {
  view: "table",
  warehouseId: null,
  categoryId: null,
  status: "active",
  stockStatus: "all",
  search: "",
  page: 1,
  pageSize: 20,
};

type ProductListSearchParams = Record<string, string | string[] | undefined>;
type ProductListSearchSource = URLSearchParams | ProductListSearchParams;

function getSearchParam(
  source: ProductListSearchSource,
  name: string,
): string | undefined {
  if (source instanceof URLSearchParams) {
    return source.get(name) ?? undefined;
  }

  const value = source[name];
  return Array.isArray(value) ? value[0] : value;
}

function parsePositiveInteger(value: string | undefined): number | null {
  if (!value || !/^\d+$/.test(value)) {
    return null;
  }

  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function parseAllowedInteger(
  value: string | undefined,
  allowed: readonly number[],
): number {
  const parsed = parsePositiveInteger(value);
  return parsed !== null && allowed.includes(parsed) ? parsed : allowed[0];
}

export function parseProductListState(
  source?: ProductListSearchSource,
): ProductListState {
  const view = getSearchParam(source ?? {}, "view");
  const status = getSearchParam(source ?? {}, "status");
  const stockStatus = getSearchParam(source ?? {}, "stock_status");
  const warehouseId = parsePositiveInteger(
    getSearchParam(source ?? {}, "warehouse_id"),
  );
  const categoryId = parsePositiveInteger(
    getSearchParam(source ?? {}, "category_id"),
  );

  return {
    view: view === "gallery" ? "gallery" : "table",
    warehouseId,
    categoryId,
    status:
      status === "inactive" || status === "all" ? status : "active",
    stockStatus:
      stockStatus === "in_stock" || stockStatus === "zero"
        ? stockStatus
        : "all",
    search: getSearchParam(source ?? {}, "search") ?? "",
    page: parsePositiveInteger(getSearchParam(source ?? {}, "page")) ?? 1,
    pageSize: parseAllowedInteger(
      getSearchParam(source ?? {}, "page_size"),
      [20, 50, 100],
    ),
  };
}

export function buildProductListQuery(state: ProductListState): string {
  const query = new URLSearchParams();
  if (state.view !== "table") {
    query.set("view", state.view);
  }
  if (state.warehouseId !== null) {
    query.set("warehouse_id", String(state.warehouseId));
  }
  if (state.categoryId !== null) {
    query.set("category_id", String(state.categoryId));
  }
  if (state.status !== "active") {
    query.set("status", state.status);
  }
  if (state.stockStatus !== "all") {
    query.set("stock_status", state.stockStatus);
  }
  if (state.search.trim()) {
    query.set("search", state.search);
  }
  if (state.page !== 1) {
    query.set("page", String(state.page));
  }
  if (state.pageSize !== 20) {
    query.set("page_size", String(state.pageSize));
  }
  return query.toString();
}

export function buildProductListHref(state: ProductListState): string {
  const query = buildProductListQuery(state);
  return query ? `/products?${query}` : "/products";
}

export function buildProductDetailHref(
  productId: number,
  returnTo: string,
): string {
  const query = new URLSearchParams({ returnTo });
  return `/products/${productId}?${query.toString()}`;
}

export function getSafeProductListReturnTo(
  returnTo: string | null | undefined,
): string | null {
  if (!returnTo || !returnTo.startsWith("/products")) {
    return null;
  }

  try {
    const parsed = new URL(returnTo, "http://inventory-system.local");
    if (parsed.pathname !== "/products") {
      return null;
    }
    return `${parsed.pathname}${parsed.search}`;
  } catch {
    return null;
  }
}
