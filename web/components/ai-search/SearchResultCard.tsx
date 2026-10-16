"use client";

import { CheckOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Tag } from "antd";
import ProductImage from "@/components/inventory/ProductImage";
import { formatAISearchPackaging, type AISearchProduct } from "@/lib/ai-search";

interface SearchResultCardProps {
  product: AISearchProduct;
  confirmed: boolean;
  confirming: boolean;
  disabled: boolean;
  onConfirm: () => void;
  onRestart: () => void;
  onDetails: () => void;
}

export default function SearchResultCard({ product, confirmed, confirming, disabled, onConfirm, onRestart, onDetails }: SearchResultCardProps) {
  return (
    <Card title="找到可能商品" extra={<Tag color="blue">AI推荐</Tag>} className="ai-search-primary-card">
      <div className="ai-search-primary-content">
        <div className="ai-search-product-image">
          <ProductImage imagePath={null} imageUrl={product.image} alt={product.productName} width={220} height={220} loading="eager" />
        </div>
        <div className="ai-search-product-info">
          <h2>{product.productName}</h2>
          <dl className="ai-search-product-fields">
            <div><dt>自家货号</dt><dd className="ai-search-product-code">{product.productCode}</dd></div>
            {product.originalProductCode && <div><dt>原货号</dt><dd>{product.originalProductCode}</dd></div>}
            <div><dt>尺寸</dt><dd>{product.size}</dd></div>
            <div><dt>包装规格</dt><dd>{formatAISearchPackaging(product)}</dd></div>
            <div><dt>所属仓库</dt><dd>{product.warehouse}</dd></div>
          </dl>
          <div className="ai-search-result-actions">
            <Button className="ai-search-confirm-button" type="primary" size="large" icon={<CheckOutlined aria-hidden />} loading={confirming} disabled={disabled || confirmed || !product.active} onClick={onConfirm}>
              {confirmed ? "已确认商品" : "就是这个商品"}
            </Button>
            <Button size="large" icon={<ReloadOutlined aria-hidden />} disabled={disabled} onClick={onRestart}>更换图片</Button>
            <Button size="large" disabled={disabled} onClick={onDetails}>查看商品资料</Button>
          </div>
          {confirmed && <p className="ai-search-confirmed" role="status">人工确认已保存。</p>}
        </div>
      </div>
    </Card>
  );
}
