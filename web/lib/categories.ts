import type {
  CategorySelection,
  CategoryTreeNode,
  InventoryCategoryOption,
} from "@/types/inventory";

function toCategoryOption(category: CategoryTreeNode): InventoryCategoryOption {
  return {
    value: category.id,
    label: category.name,
    children: category.children.map(toCategoryOption),
  };
}

export function toCategoryOptions(
  categories: CategoryTreeNode[],
  includeAll = false,
): InventoryCategoryOption[] {
  return [
    ...(includeAll ? [{ value: "all", label: "全部商品" }] : []),
    ...categories.map(toCategoryOption),
  ];
}

export function getCategoryPath(
  categories: CategoryTreeNode[],
  categoryId: number | null,
): number[] {
  if (categoryId === null) {
    return [];
  }

  for (const parent of categories) {
    if (parent.id === categoryId) {
      return [parent.id];
    }
    const child = parent.children.find((item) => item.id === categoryId);
    if (child) {
      return [parent.id, child.id];
    }
  }

  return [];
}

export function getCategoryLabel(
  categories: CategoryTreeNode[],
  selection: CategorySelection,
): string {
  const id = selection[selection.length - 1];
  if (typeof id !== "number") {
    return "全部商品";
  }

  for (const parent of categories) {
    if (parent.id === id) {
      return parent.name;
    }
    const child = parent.children.find((item) => item.id === id);
    if (child) {
      return `${parent.name} / ${child.name}`;
    }
  }

  return "全部商品";
}

export function hasSecondLevelCategories(categories: CategoryTreeNode[]): boolean {
  return categories.some((category) => category.children.length > 0);
}
