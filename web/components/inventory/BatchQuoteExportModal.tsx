"use client";

import { Input, Modal } from "antd";
import { useState } from "react";

export interface BatchQuoteExportValues {
  customerName: string;
  quoteDate: string;
}

interface BatchQuoteExportModalProps {
  open: boolean;
  selectedCount: number;
  confirmLoading: boolean;
  onCancel: () => void;
  onConfirm: (values: BatchQuoteExportValues) => Promise<void>;
}

function getToday(): string {
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return `${today.getFullYear()}-${month}-${day}`;
}

export default function BatchQuoteExportModal({
  open,
  selectedCount,
  confirmLoading,
  onCancel,
  onConfirm,
}: BatchQuoteExportModalProps) {
  const [customerName, setCustomerName] = useState("");
  const [quoteDate, setQuoteDate] = useState(getToday);

  return (
    <Modal
      title="导出报价单"
      open={open}
      okText="导出 Excel"
      cancelText="取消"
      confirmLoading={confirmLoading}
      onCancel={onCancel}
      onOk={() =>
        onConfirm({ customerName: customerName.trim(), quoteDate })
      }
    >
      <div className="batch-quote-export-modal-content">
        <p>将导出 {selectedCount} 个商品的报价资料。</p>
        <label className="batch-quote-export-field">
          <span>客户名称（可选）</span>
          <Input
            value={customerName}
            maxLength={200}
            placeholder="例如：义乌客户"
            onChange={(event) => setCustomerName(event.target.value)}
          />
        </label>
        <label className="batch-quote-export-field">
          <span>报价日期</span>
          <Input
            type="date"
            value={quoteDate}
            onChange={(event) => setQuoteDate(event.target.value || getToday())}
          />
        </label>
      </div>
    </Modal>
  );
}
