"use client";

import { CheckOutlined } from "@ant-design/icons";
import { Button, Card, Tag } from "antd";
import ProductImage from "@/components/inventory/ProductImage";
import { formatAISearchPackaging, type AISearchProduct } from "@/lib/mock/ai-search";

interface SearchResultCardProps {
  product: AISearchProduct;
  confirmed: boolean;
  onConfirm: () => void;
  onDetails: () => void;
}

export default function SearchResultCard({ product, confirmed, onConfirm, onDetails }: SearchResultCardProps) {
  return (
    <Card title="最匹配商品" extra={<Tag color="blue">AI推荐</Tag>} className="ai-search-primary-card">
      <div className="ai-search-primary-content">
        <div className="ai-search-product-image">
          <ProductImage imagePath={null} imageUrl={product.image} alt={product.productName} width={220} height={220} loading="eager" />
        </div>
        <div className="ai-search-product-info">
          <h2>{product.productName}</h2>
          <dl className="ai-search-product-fields">
            <div><dt>货号</dt><dd className="ai-search-product-code">{product.productCode}</dd></div>
            <div><dt>尺寸</dt><dd>{product.size}</dd></div>
            <div><dt>包装规格</dt><dd>{formatAISearchPackaging(product)}</dd></div>
            <div><dt>所属仓库</dt><dd>{product.warehouse}</dd></div>
          </dl>
          <div className="ai-search-result-actions">
            <Button type="primary" size="large" icon={<CheckOutlined aria-hidden />} disabled={confirmed} onClick={onConfirm}>
              {confirmed ? "已确认（演示）" : "确认商品"}
            </Button>
            <Button size="large" onClick={onDetails}>查看详情</Button>
          </div>
          {confirmed && <p className="ai-search-confirmed" role="status">已确认此示例商品；确认仅保留在当前页面。</p>}
        </div>
      </div>
    </Card>
  );
}
