"use client";

import { EditOutlined } from "@ant-design/icons";
import { Alert, Button, Input, Popover, Tooltip } from "antd";
import { useEffect, useReducer, useRef, type ChangeEvent } from "react";
import { initialRemarkEditor, MAX_REMARK_LENGTH, remarkEditorReducer, remarkErrorMessage } from "@/lib/product-remarks";
import type { InventoryProduct } from "@/types/inventory";

export default function ProductRemarkEditor({ product, disabled, onSave }: {
  product: Pick<InventoryProduct, "id" | "productCode" | "remark">;
  disabled: boolean;
  onSave: (productId: number, draft: string) => Promise<string>;
}) {
  const [state, dispatch] = useReducer(remarkEditorReducer, undefined, initialRemarkEditor);
  const lock = useRef(false);
  const mounted = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const note = product.remark.trim();

  async function save() {
    if (disabled || lock.current || !state.open) return;
    lock.current = true;
    dispatch({ type: "save" });
    try {
      await onSave(product.id, state.draft);
      if (mounted.current) dispatch({ type: "saved" });
    } catch (error) {
      if (mounted.current) dispatch({ type: "failed", error: remarkErrorMessage(error) });
    } finally { lock.current = false; }
  }

  return <div className="product-remark-cell">
    {note ? <Tooltip title={note} mouseEnterDelay={0.2}><span className="table-note-value">{note}</span></Tooltip> : <span className="table-note-empty">—</span>}
    <Popover trigger="click" placement="topRight" title={`编辑备注 · ${product.productCode ?? "商品"}`} open={state.open}
      onOpenChange={(open) => {
        if (lock.current) return;
        if (open && !disabled) dispatch({ type: "open", remark: product.remark });
        else if (!open) dispatch({ type: "cancel" });
      }} content={<div className="product-remark-popover">
        <Input.TextArea aria-label="商品备注" autoFocus value={state.draft} maxLength={MAX_REMARK_LENGTH} showCount
          autoSize={{ minRows: 3, maxRows: 7 }} disabled={state.saving || disabled}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => dispatch({ type: "edit", draft: event.target.value })}
          placeholder="填写备注；清空后保存可删除备注" />
        {state.error && <Alert type="error" showIcon title={state.error} />}
        <div className="product-remark-actions">
          <Button size="small" disabled={state.saving} onClick={() => dispatch({ type: "cancel" })}>取消</Button>
          <Button type="primary" size="small" loading={state.saving} disabled={disabled || state.saving} onClick={save}>保存</Button>
        </div>
      </div>}>
      <Button type="text" size="small" icon={<EditOutlined />} className="product-remark-edit" disabled={disabled || state.saving}
        aria-label={`编辑商品 ${product.productCode ?? product.id} 的备注`} title="编辑备注" />
    </Popover>
  </div>;
}
