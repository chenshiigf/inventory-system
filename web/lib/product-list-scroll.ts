const PRODUCT_LIST_SCROLL_PREFIX = "product-list-scroll:";
const PRODUCT_LIST_SCROLL_PENDING_PREFIX = "product-list-scroll-pending:";

function getStorageKey(productListHref: string): string {
  return `${PRODUCT_LIST_SCROLL_PREFIX}${productListHref}`;
}

function getPendingStorageKey(productListHref: string): string {
  return `${PRODUCT_LIST_SCROLL_PENDING_PREFIX}${productListHref}`;
}

export function saveProductListScroll(
  productListHref: string,
  scrollY?: number,
): void {
  if (typeof window === "undefined") {
    return;
  }

  const currentScrollY = scrollY ?? window.scrollY;
  const normalizedScrollY = Number.isFinite(currentScrollY)
    ? Math.max(0, Math.round(currentScrollY))
    : 0;

  try {
    window.sessionStorage.setItem(
      getStorageKey(productListHref),
      String(normalizedScrollY),
    );
  } catch {
    // Storage may be unavailable in private browsing or restricted contexts.
  }
}

export function readProductListScroll(productListHref: string): number | null {
  if (typeof window === "undefined") {
    return null;
  }

  try {
    const value = window.sessionStorage.getItem(getStorageKey(productListHref));
    if (value === null) {
      return null;
    }

    const scrollY = Number(value);
    return Number.isFinite(scrollY) && scrollY >= 0 ? scrollY : null;
  } catch {
    return null;
  }
}

export function markProductListScrollPending(productListHref: string): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.setItem(getPendingStorageKey(productListHref), "1");
  } catch {
    // Storage may be unavailable in private browsing or restricted contexts.
  }
}

export function isProductListScrollPending(productListHref: string): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  try {
    return window.sessionStorage.getItem(getPendingStorageKey(productListHref)) === "1";
  } catch {
    return false;
  }
}

export function consumeProductListScroll(productListHref: string): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.sessionStorage.removeItem(getPendingStorageKey(productListHref));
    window.sessionStorage.removeItem(getStorageKey(productListHref));
  } catch {
    // Storage may be unavailable in private browsing or restricted contexts.
  }
}
