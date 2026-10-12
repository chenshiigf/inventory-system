/** UI examples only. Production IDs and details must come from a future source adapter. */
export interface AISearchProduct {
  productId: number;
  productKey: string;
  productCode: string;
  productName: string;
  image: string;
  size: string;
  packagings: { id: number; packingQty: number | null; unit: string }[];
  warehouse: string;
  imageSyncedAt: string | null;
}

export interface AISearchQueryResult {
  queryId: string;
  sourceId: string;
  queryImage: { image: string; name: string };
  primaryProduct: AISearchProduct;
  similarProducts: AISearchProduct[];
}

export const mockAISearchResult: AISearchQueryResult = {
  queryId: "mock-ai-search-preview",
  sourceId: "mock-ai-search",
  queryImage: { image: "", name: "尚未选择照片" },
  primaryProduct: {
    productId: 318,
    productKey: "mock-ai-search:318",
    productCode: "01-05-027",
    productName: "房屋调味瓶（红色）",
    image: "/mock-products/ai-house-red.svg",
    size: "8×5.5×10cm",
    packagings: [{ id: 1, packingQty: 48, unit: "个" }],
    warehouse: "舜仓",
    imageSyncedAt: null,
  },
  similarProducts: [
    {
      productId: 317, productKey: "mock-ai-search:317", productCode: "01-05-026",
      productName: "房屋调味瓶（蓝色）", image: "/mock-products/ai-house-blue.svg",
      size: "8×5.5×10cm", packagings: [{ id: 2, packingQty: 48, unit: "个" }],
      warehouse: "舜仓", imageSyncedAt: null,
    },
    {
      productId: 308, productKey: "mock-ai-search:308", productCode: "01-05-017",
      productName: "车形调味瓶", image: "/mock-products/ai-car.svg", size: "7×3×4cm",
      packagings: [{ id: 3, packingQty: 240, unit: "个" }], warehouse: "舜仓", imageSyncedAt: null,
    },
    {
      productId: 9001, productKey: "mock-ai-search:9001", productCode: "示例-003",
      productName: "房屋调味瓶（绿色）", image: "/mock-products/ai-house-green.svg",
      size: "8×5.5×10cm", packagings: [{ id: 4, packingQty: 48, unit: "个" }],
      warehouse: "舜仓", imageSyncedAt: null,
    },
    {
      productId: 9002, productKey: "mock-ai-search:9002", productCode: "示例-004",
      productName: "房屋调味瓶（浅色）", image: "/mock-products/ai-house-cream.svg",
      size: "8×5.5×10cm", packagings: [{ id: 5, packingQty: 48, unit: "个" }],
      warehouse: "舜仓", imageSyncedAt: null,
    },
  ],
};

export function formatAISearchPackaging(product: AISearchProduct): string {
  return product.packagings.map((p) => p.packingQty === null ? "装箱数未填写" : `${p.packingQty}${p.unit}/箱`).join("、") || "包装规格未填写";
}
