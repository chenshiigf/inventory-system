"use client";

import { Alert, Button, Modal, Spin, Tag } from "antd";
import ProductImage from "@/components/inventory/ProductImage";
import { formatAISearchPackaging, type AISearchProduct } from "@/lib/ai-search";

interface ProductReadonlyModalProps {
  product: AISearchProduct | null;
  open: boolean;
  loading: boolean;
  error: string | null;
  confirming: boolean;
  confirmed: boolean;
  disabled: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export default function ProductReadonlyModal({ product, open, loading, error, confirming, confirmed, disabled, onClose, onConfirm }: ProductReadonlyModalProps) {
  return (
    <Modal title="商品资料" open={open} onCancel={onClose} width={620}
      footer={<div className="ai-search-result-actions"><Button size="large" onClick={onClose}>关闭</Button>{product && <Button type="primary" size="large" loading={confirming} disabled={disabled || confirmed || !product.active} onClick={onConfirm}>{confirmed ? "已确认商品" : "就是这个商品"}</Button>}</div>} className="ai-search-detail-modal">
      {loading && <div className="ai-search-detail-loading" role="status"><Spin /><p>正在加载商品资料...</p></div>}
      {error && <Alert type="error" showIcon title={error} />}
      {product && <>
        <Tag color="blue">商品资料 · 只读</Tag>
        <div className="ai-search-detail-image">
          <ProductImage key={product.productKey} imagePath={null} imageUrl={product.image} alt={product.productName} width={250} height={250} />
        </div>
        <h2 className="ai-search-detail-name">{product.productName}</h2>
        <dl className="ai-search-product-fields">
          <div><dt>自家货号</dt><dd className="ai-search-product-code">{product.productCode}</dd></div>
          {product.originalProductCode && <div><dt>原货号</dt><dd>{product.originalProductCode}</dd></div>}
          <div><dt>尺寸</dt><dd>{product.size}</dd></div>
          <div><dt>包装规格</dt><dd>{formatAISearchPackaging(product)}</dd></div>
          <div><dt>所属仓库</dt><dd>{product.warehouse}</dd></div>
          <div><dt>资料同步时间</dt><dd>{product.imageSyncedAt ? new Date(product.imageSyncedAt).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "未提供同步时间"}</dd></div>
        </dl>
      </>}
    </Modal>
  );
}
