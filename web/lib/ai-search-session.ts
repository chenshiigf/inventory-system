import type { CropRegion, VisualResult } from "@/lib/api/visual-search";
import { sameCrop, validSingleCrop } from "@/lib/image-crop";

let searchRequestSequence = 0;

// Only identifies in-flight work in this page session, never an API query or token.
export function createSearchRequestId(): string {
  return `ai-search-${++searchRequestSequence}`;
}

export interface SearchInput {
  file: File;
  imageVersion: number;
  cropVersion: number;
  crop: CropRegion | null;
}
export interface SearchRequest extends SearchInput { requestId: string; }
export interface SearchSession {
  file: File | null;
  image: string;
  width: number;
  height: number;
  imageVersion: number;
  cropVersion: number;
  crop: CropRegion | null;
  activeRequestId: string | null;
  result: VisualResult | null;
  confirmedId: number | null;
}
export type SearchSessionAction =
  | { type: "photo"; file: File | null; image: string; width: number; height: number }
  | { type: "crop"; crop: CropRegion | null }
  | { type: "start"; request: SearchRequest }
  | { type: "complete"; request: SearchRequest; result: VisualResult; submittedCrop?: CropRegion | null }
  | { type: "finish"; request: SearchRequest }
  | { type: "confirm"; input: SearchInput; queryId: string; productId: number };

export function initialSearchSession(): SearchSession {
  return { file: null, image: "", width: 0, height: 0, imageVersion: 0, cropVersion: 0, crop: null, activeRequestId: null, result: null, confirmedId: null };
}
export function currentSearchInput(session: SearchSession): SearchInput | null {
  return session.file ? { file: session.file, imageVersion: session.imageVersion, cropVersion: session.cropVersion, crop: session.crop ? [...session.crop] : null } : null;
}
export function matchesSearchInput(session: SearchSession, input: SearchInput): boolean {
  return session.file === input.file && session.imageVersion === input.imageVersion && session.cropVersion === input.cropVersion && sameCrop(session.crop, input.crop);
}
export function searchSessionReducer(session: SearchSession, action: SearchSessionAction): SearchSession {
  switch (action.type) {
    case "photo": return { ...initialSearchSession(), file: action.file, image: action.image, width: action.width, height: action.height, imageVersion: session.imageVersion + 1 };
    case "crop": {
      if (!session.file || (action.crop && !validSingleCrop(action.crop, session.width, session.height))) return session;
      return { ...session, crop: action.crop ? [...action.crop] : null, cropVersion: session.cropVersion + 1, activeRequestId: null, result: null, confirmedId: null };
    }
    case "start": {
      if (!matchesSearchInput(session, action.request) || session.activeRequestId) return session;
      return { ...session, activeRequestId: action.request.requestId, result: null, confirmedId: null };
    }
    case "complete": {
      const submittedCrop = action.submittedCrop === undefined ? action.request.crop : action.submittedCrop;
      if (!matchesSearchInput(session, action.request) || session.activeRequestId !== action.request.requestId || !sameCrop(action.result.crop, submittedCrop)) return session;
      return { ...session, activeRequestId: null, result: action.result, confirmedId: null };
    }
    case "finish": return matchesSearchInput(session, action.request) && session.activeRequestId === action.request.requestId ? { ...session, activeRequestId: null } : session;
    case "confirm": return matchesSearchInput(session, action.input) && session.result?.query_id === action.queryId && session.result.candidates.some((c) => c.product.id === action.productId)
      ? { ...session, confirmedId: action.productId } : session;
  }
}
