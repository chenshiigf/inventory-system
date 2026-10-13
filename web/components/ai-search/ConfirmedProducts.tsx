"use client";

import { Button, Card, Empty, Popconfirm } from "antd";
import { CloseOutlined } from "@ant-design/icons";
import ProductImage from "@/components/inventory/ProductImage";
import { formatAISearchPackaging } from "@/lib/ai-search";
import type { ConfirmedPreviewItem } from "@/lib/ai-search-preview-session";

export default function ConfirmedProducts({ items, disabled, onRemove }: { items: ConfirmedPreviewItem[]; disabled: boolean; onRemove: (id: string) => void }) {
  return <Card title={`本次已找到的商品（${items.length}）`} className="ai-preview-confirmed-list" extra={<span className="ai-preview-muted">本次清单 · 不涉及库存操作</span>}>
    {items.length ? <div className="ai-preview-confirmed-grid">{items.map((item) => <article className="ai-preview-confirmed-item" key={item.id} data-region-id={item.regionId}>
      <ProductImage imagePath={null} imageUrl={item.product.image} alt={item.product.productName || item.product.productCode} width={68} height={68} />
      <div><strong>{item.product.productCode}</strong><span>{item.regionNumber ? `区域 ${item.regionNumber}` : "单款整图"}</span><p>{item.product.size} · {formatAISearchPackaging(item.product)}</p><p>{item.product.warehouse}</p></div>
      <Popconfirm title="从本次清单移除？" description="只移除这一项，其他区域保持不变。" okText="移除" cancelText="保留" onConfirm={() => onRemove(item.id)} disabled={disabled}>
        <Button type="text" icon={<CloseOutlined />} disabled={disabled} aria-label={`移除${item.regionNumber ? `区域 ${item.regionNumber}` : "单款整图"}清单项`} />
      </Popconfirm>
    </article>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="确认商品后，会加入这里。" />}
  </Card>;
}
