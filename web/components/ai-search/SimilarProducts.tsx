"use client";

import { Button, Collapse } from "antd";
import ProductImage from "@/components/inventory/ProductImage";
import type { AISearchProduct } from "@/lib/mock/ai-search";

interface SimilarProductsProps {
  products: AISearchProduct[];
  onDetails: (product: AISearchProduct) => void;
}

export default function SimilarProducts({ products, onDetails }: SimilarProductsProps) {
  return (
    <Collapse
      className="ai-search-similar"
      defaultActiveKey={[]}
      items={[{
        key: "similar",
        label: `其他相似商品（${products.length}）`,
        children: <div className="ai-search-similar-list">{products.map((product) => (
          <article key={product.productKey} className="ai-search-similar-product">
            <ProductImage imagePath={null} imageUrl={product.image} alt={product.productName} width={72} height={72} />
            <div><h3>{product.productName}</h3><p>货号：{product.productCode}</p></div>
            <Button onClick={() => onDetails(product)}>查看详情</Button>
          </article>
        ))}</div>,
      }]}
    />
  );
}
