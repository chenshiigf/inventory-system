import { ApiResponseError } from "@/lib/api/client";
import { updateProduct } from "@/lib/api/products";
import type { InventoryProduct } from "@/types/inventory";

export const MAX_REMARK_LENGTH = 2000;

export async function saveProductRemark(productId: number, draft: string): Promise<string> {
  const remark = draft.trim();
  if (remark.length > MAX_REMARK_LENGTH) throw new Error("备注最多 2000 个字，请缩短后保存。");
  // Never spread a product/form here: this action can only update remark.
  const response = await updateProduct(productId, { remark: remark || null });
  if (response.id !== productId) throw new Error("商品资料无法核对，请刷新列表后重试。");
  return response.remark ?? "";
}

export function remarkErrorMessage(error: unknown): string {
  if (error instanceof ApiResponseError && error.statusCode === 401) return "请先登录库存系统，输入内容已保留。";
  if (error instanceof ApiResponseError && error.statusCode === 403) return "当前账号无权编辑，输入内容已保留，请联系管理员。";
  return error instanceof Error ? error.message : "备注保存失败，输入内容已保留，请重试。";
}

export interface RemarkEditorState { open: boolean; draft: string; saving: boolean; error: string | null; }
export type RemarkEditorAction =
  | { type: "open"; remark: string }
  | { type: "edit"; draft: string }
  | { type: "save" }
  | { type: "failed"; error: string }
  | { type: "saved" }
  | { type: "cancel" };
export function initialRemarkEditor(): RemarkEditorState { return { open: false, draft: "", saving: false, error: null }; }
export function remarkEditorReducer(state: RemarkEditorState, action: RemarkEditorAction): RemarkEditorState {
  if (state.saving && action.type !== "saved" && action.type !== "failed") return state;
  switch (action.type) {
    case "open": return { open: true, draft: action.remark, saving: false, error: null };
    case "edit": return { ...state, draft: action.draft, error: null };
    case "save": return state.open ? { ...state, saving: true, error: null } : state;
    case "failed": return { ...state, saving: false, error: action.error };
    case "saved":
    case "cancel": return initialRemarkEditor();
  }
}

export interface RemarkUpdate { revision: number; remark: string; }
export function applyRemarkToProducts(products: InventoryProduct[], id: number, remark: string): InventoryProduct[] {
  return products.map(product => product.id === id ? { ...product, remark } : product);
}
// A GET begun before a successful edit must not put its old remark back.
// GETs begun after the edit still use the server's latest authoritative value.
export function applyRemarkUpdatesToResponse(products: InventoryProduct[], updates: ReadonlyMap<number, RemarkUpdate>, requestRevision: number): InventoryProduct[] {
  return products.map(product => {
    const update = updates.get(product.id);
    return update && update.revision > requestRevision ? { ...product, remark: update.remark } : product;
  });
}
