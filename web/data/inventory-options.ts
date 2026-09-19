import type { InventoryCategoryOption } from "@/types/inventory";

// Category persistence is outside this phase, so the selector stays on all products.
export const categoryOptions: InventoryCategoryOption[] = [
  { value: "all", label: "全部商品" },
];
