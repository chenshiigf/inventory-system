"use client";

import { Button, Modal, Tag } from "antd";
import ProductImage from "@/components/inventory/ProductImage";
import { formatAISearchPackaging, type AISearchProduct } from "@/lib/mock/ai-search";

interface ProductReadonlyModalProps {
  product: AISearchProduct | null;
  onClose: () => void;
}

export default function ProductReadonlyModal({ product, onClose }: ProductReadonlyModalProps) {
  return (
    <Modal title="商品详情" open={product !== null} onCancel={onClose} width={620}
      footer={<Button size="large" onClick={onClose}>关闭</Button>} className="ai-search-detail-modal">
      {product && <>
        <Tag color="blue">Mock 示例 · 只读</Tag>
        <div className="ai-search-detail-image">
          <ProductImage key={product.productKey} imagePath={null} imageUrl={product.image} alt={product.productName} width={250} height={250} />
        </div>
        <h2 className="ai-search-detail-name">{product.productName}</h2>
        <dl className="ai-search-product-fields">
          <div><dt>货号</dt><dd className="ai-search-product-code">{product.productCode}</dd></div>
          <div><dt>尺寸</dt><dd>{product.size}</dd></div>
          <div><dt>包装规格</dt><dd>{formatAISearchPackaging(product)}</dd></div>
          <div><dt>所属仓库</dt><dd>{product.warehouse}</dd></div>
          <div><dt>图片同步时间</dt><dd>{product.imageSyncedAt ?? "演示数据，尚未同步"}</dd></div>
        </dl>
      </>}
    </Modal>
  );
}
