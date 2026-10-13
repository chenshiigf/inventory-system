"use client";

import { EditOutlined } from "@ant-design/icons";
import { Alert, Button, Input, Popover, Tooltip } from "antd";
import type { TextAreaRef } from "antd/es/input/TextArea";
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
  const textArea = useRef<TextAreaRef>(null);
  const editButton = useRef<HTMLButtonElement>(null);
  const editorPanel = useRef<HTMLDivElement>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    // Saving can blur a disabled control to body. Restore the editable field on
    // failure, unless the user has moved focus to an outside control.
    if (state.error && state.open && (document.activeElement === document.body || editorPanel.current?.contains(document.activeElement))) {
      textArea.current?.focus({ preventScroll: true });
    }
  }, [state.error, state.open]);
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
      afterOpenChange={(open) => {
        // The portal is initially offscreen while Ant Design measures it.
        // Focus only after positioning, and never scroll the document to focus.
        if (open) textArea.current?.focus({ preventScroll: true });
        else if (document.activeElement === document.body || editorPanel.current?.contains(document.activeElement)) {
          editButton.current?.focus({ preventScroll: true });
        }
      }}
      onOpenChange={(open) => {
        if (lock.current) return;
        if (open && !disabled) dispatch({ type: "open", remark: product.remark });
        else if (!open) dispatch({ type: "cancel" });
      }} content={<div ref={editorPanel} className="product-remark-popover">
        <Input.TextArea ref={textArea} aria-label="商品备注" value={state.draft} maxLength={MAX_REMARK_LENGTH} showCount
          autoSize={{ minRows: 3, maxRows: 7 }} disabled={state.saving || disabled}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => dispatch({ type: "edit", draft: event.target.value })}
          placeholder="填写备注；清空后保存可删除备注" />
        {state.error && <Alert type="error" showIcon title={state.error} />}
        <div className="product-remark-actions">
          <Button size="small" disabled={state.saving} onClick={() => dispatch({ type: "cancel" })}>取消</Button>
          <Button type="primary" size="small" loading={state.saving} disabled={disabled || state.saving} onClick={save}>保存</Button>
        </div>
      </div>}>
      <Button ref={editButton} type="text" size="small" icon={<EditOutlined />} className="product-remark-edit" disabled={disabled || state.saving}
        aria-label={`编辑商品 ${product.productCode ?? product.id} 的备注`} title="编辑备注" />
    </Popover>
  </div>;
}
