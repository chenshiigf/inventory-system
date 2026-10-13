// Derive the highlight from the actual URL, including static-export trailing
// slashes and product detail pages. Import must precede the products fallback.
export function selectedNavigationKey(pathname: string, development = false): string | null {
  const path = pathname.replace(/\/+$/, "") || "/";
  if (path === "/") return "dashboard";
  if (development && path === "/ai-search-preview") return "ai-search";
  const routes = [
    ["/products/import", "product-import"],
    ["/inventory-movements", "inventory-movements"],
    ["/categories", "categories"],
    ["/warehouses", "warehouses"],
    ["/visual-search", "visual-search"],
    ["/ai-search", "ai-search"],
    ["/products", "inventory"],
  ];
  return routes.find(([route]) => path === route || path.startsWith(`${route}/`))?.[1] ?? null;
}
