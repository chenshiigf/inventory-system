import type { CropRegion } from "@/lib/api/visual-search";
import type { AISearchProduct } from "@/lib/ai-search";

export type PreviewMode = "single" | "multiple";
export type RegionStatus = "pending" | "searching" | "searched" | "confirmed" | "failed";
export interface PreviewResult {
  queryId: string;
  source: "mock";
  candidates: { product: AISearchProduct; score: number }[];
}
export interface QueryTarget { sessionId: string; regionId: string; revision: number; requestId: string; }
export interface PreviewAttempt {
  requestId: string;
  revision: number;
  crop: CropRegion | null;
  result?: PreviewResult;
  error?: string;
}
export interface PreviewRegion {
  id: string;
  number: number;
  crop: CropRegion | null;
  revision: number;
  status: RegionStatus;
  result: PreviewResult | null;
  error: string | null;
  attempts: PreviewAttempt[];
}
export interface ConfirmedPreviewItem {
  id: string;
  sessionId: string;
  regionId: string;
  regionNumber: number;
  revision: number;
  queryId: string;
  product: AISearchProduct;
}
export interface PreviewSession {
  id: string;
  file: File | null;
  image: string;
  width: number;
  height: number;
  mode: PreviewMode;
  whole: PreviewRegion;
  regions: PreviewRegion[];
  selectedId: string | null;
  nextNumber: number;
  confirmed: ConfirmedPreviewItem[];
  searchCount: number;
}
export type PreviewAction =
  | { type: "photo"; id: string; file: File; image: string; width: number; height: number }
  | { type: "mode"; mode: PreviewMode }
  | { type: "add"; id: string; crop: CropRegion }
  | { type: "select"; id: string }
  | { type: "revise"; id: string; crop: CropRegion }
  | { type: "delete"; id: string }
  | ({ type: "start" } & QueryTarget)
  | ({ type: "success"; result: PreviewResult } & QueryTarget)
  | ({ type: "failure"; error: string } & QueryTarget)
  | ({ type: "confirm"; queryId: string; productKey: string } & Omit<QueryTarget, "requestId">)
  | { type: "remove"; id: string };

function newRegion(id: string, number: number, crop: CropRegion | null): PreviewRegion {
  return { id, number, crop, revision: 0, status: "pending", result: null, error: null, attempts: [] };
}
export function initialPreviewSession(): PreviewSession {
  return { id: "", file: null, image: "", width: 0, height: 0, mode: "single", whole: newRegion("whole", 0, null), regions: [], selectedId: null, nextNumber: 1, confirmed: [], searchCount: 0 };
}
export function findPreviewRegion(state: PreviewSession, id: string): PreviewRegion | undefined {
  return id === state.whole.id ? state.whole : state.regions.find((r) => r.id === id);
}
export function selectedPreviewRegion(state: PreviewSession): PreviewRegion | undefined {
  return state.mode === "single" ? state.whole : state.selectedId ? findPreviewRegion(state, state.selectedId) : undefined;
}
export function validPreviewCrop(crop: CropRegion, width: number, height: number): boolean {
  const [x, y, w, h] = crop;
  return crop.every(Number.isFinite) && x >= 0 && y >= 0 && w > 0 && h > 0 && x + w <= 1.000001 && y + h <= 1.000001
    && Math.round((x + w) * width) - Math.round(x * width) >= 10
    && Math.round((y + h) * height) - Math.round(y * height) >= 10;
}
function updateRegion(state: PreviewSession, id: string, update: (region: PreviewRegion) => PreviewRegion): PreviewSession {
  if (id === state.whole.id) return { ...state, whole: update(state.whole) };
  return { ...state, regions: state.regions.map((r) => r.id === id ? update(r) : r) };
}
function targetMatches(state: PreviewSession, target: Omit<QueryTarget, "requestId">): boolean {
  return state.id === target.sessionId && findPreviewRegion(state, target.regionId)?.revision === target.revision;
}
export function previewSessionReducer(state: PreviewSession, action: PreviewAction): PreviewSession {
  switch (action.type) {
    case "photo": return { ...initialPreviewSession(), id: action.id, file: action.file, image: action.image, width: action.width, height: action.height, mode: state.mode };
    case "mode": return { ...state, mode: action.mode, selectedId: state.selectedId ?? state.regions[0]?.id ?? null };
    case "select": return state.regions.some((r) => r.id === action.id) ? { ...state, selectedId: action.id } : state;
    case "add": {
      if (!state.file || state.regions.length >= 10 || findPreviewRegion(state, action.id) || !validPreviewCrop(action.crop, state.width, state.height)) return state;
      return { ...state, regions: [...state.regions, newRegion(action.id, state.nextNumber, action.crop)], selectedId: action.id, nextNumber: state.nextNumber + 1 };
    }
    case "revise": {
      const r = findPreviewRegion(state, action.id);
      if (!r?.crop || r.status === "confirmed" || !validPreviewCrop(action.crop, state.width, state.height)) return state;
      return updateRegion(state, r.id, (region) => ({ ...region, crop: action.crop, revision: region.revision + 1, result: null, error: null, status: "pending" }));
    }
    case "delete": {
      const r = state.regions.find((r) => r.id === action.id);
      if (!r || r.status === "confirmed") return state;
      const regions = state.regions.filter((r) => r.id !== action.id);
      return { ...state, regions, selectedId: state.selectedId === action.id ? regions[0]?.id ?? null : state.selectedId };
    }
    case "start": {
      const r = findPreviewRegion(state, action.regionId);
      if (!state.file || !targetMatches(state, action) || !r || r.result || r.status === "searching" || state.whole.status === "searching" || state.regions.some((r) => r.status === "searching")) return state;
      return updateRegion({ ...state, searchCount: state.searchCount + 1 }, r.id, (region) => ({ ...region, status: "searching", error: null, attempts: [...region.attempts, { requestId: action.requestId, revision: region.revision, crop: region.crop ? [...region.crop] : null }] }));
    }
    case "success":
    case "failure": {
      const r = findPreviewRegion(state, action.regionId);
      if (!targetMatches(state, action) || !r || r.status !== "searching" || r.attempts.at(-1)?.requestId !== action.requestId) return state;
      if (action.type === "success" && (action.result.source !== "mock" || action.result.queryId !== action.requestId || action.result.candidates.length !== 5)) return state;
      return updateRegion(state, r.id, (region) => ({ ...region, status: action.type === "success" ? "searched" : "failed",
        result: action.type === "success" ? action.result : null, error: action.type === "failure" ? action.error : null,
        attempts: region.attempts.map((a) => a.requestId === action.requestId ? { ...a, ...(action.type === "success" ? { result: action.result } : { error: action.error }) } : a) }));
    }
    case "confirm": {
      const r = findPreviewRegion(state, action.regionId);
      if (!targetMatches(state, action) || !r?.result || r.result.queryId !== action.queryId) return state;
      const candidate = r.result.candidates.find((c) => c.product.productKey === action.productKey);
      if (!candidate) return state;
      const item: ConfirmedPreviewItem = { id: `${r.id}:${action.queryId}`, sessionId: state.id, regionId: r.id, regionNumber: r.number, revision: r.revision, queryId: action.queryId, product: candidate.product };
      return updateRegion({ ...state, confirmed: [...state.confirmed.filter((i) => i.regionId !== r.id), item] }, r.id, (region) => ({ ...region, status: "confirmed" }));
    }
    case "remove": {
      const item = state.confirmed.find((i) => i.id === action.id);
      if (!item) return state;
      return updateRegion({ ...state, confirmed: state.confirmed.filter((i) => i.id !== item.id) }, item.regionId, (region) => region.result?.queryId === item.queryId ? { ...region, status: "searched" } : region);
    }
  }
}
