"use client";

import { Button, Collapse } from "antd";
import ProductImage from "@/components/inventory/ProductImage";
import type { AISearchCandidate, AISearchProduct } from "@/lib/ai-search";

interface SimilarProductsProps {
  products: AISearchCandidate[];
  disabled: boolean;
  onDetails: (product: AISearchProduct) => void;
}

export default function SimilarProducts({ products, disabled, onDetails }: SimilarProductsProps) {
  return (
    <Collapse
      className="ai-search-similar"
      defaultActiveKey={[]}
      items={[{
        key: "similar",
        label: `其他相似商品（${products.length}）`,
        children: <div className="ai-search-similar-list">{products.map(({ product, score }) => (
          <article key={product.productKey} className="ai-search-similar-product">
            <ProductImage imagePath={null} imageUrl={product.image} alt={product.productName} width={72} height={72} />
            <div><h3>{product.productName}</h3><p>货号：{product.productCode}</p><p>匹配分数 {score.toFixed(3)}</p></div>
            <Button disabled={disabled} onClick={() => onDetails(product)}>查看详情</Button>
          </article>
        ))}</div>,
      }]}
    />
  );
}
