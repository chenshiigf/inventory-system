"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from "react";
import type { CropRegion, VisualResult } from "@/lib/api/visual-search";
import { initialSearchSession, searchSessionReducer, type SearchInput, type SearchRequest, type SearchSession } from "@/lib/ai-search-session";
import { AISearchPreviewSessionProvider } from "./AISearchPreviewSessionProvider";

interface SessionContext {
  session: SearchSession;
  replacePhoto: (file: File | null, width?: number, height?: number) => void;
  setCrop: (crop: CropRegion | null) => void;
  startSearch: (request: SearchRequest) => void;
  completeSearch: (request: SearchRequest, result: VisualResult) => void;
  finishSearch: (request: SearchRequest) => void;
  confirmSelection: (input: SearchInput, queryId: string, productId: number) => void;
}

const SearchSessionContext = createContext<SessionContext | null>(null);

// The root layout survives client navigation. Nothing is written to browser storage.
export function AISearchSessionProvider({ children }: { children: ReactNode }) {
  const [session, dispatch] = useReducer(searchSessionReducer, undefined, initialSearchSession);
  const previewUrl = useRef<string | null>(null);

  useEffect(() => () => {
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);

  const replacePhoto = useCallback((file: File | null, width = 0, height = 0) => {
    const image = file ? URL.createObjectURL(file) : "";
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = image || null;
    dispatch({ type: "photo", file, image, width, height });
  }, []);

  const setCrop = useCallback((crop: CropRegion | null) => dispatch({ type: "crop", crop }), []);
  const startSearch = useCallback((request: SearchRequest) => dispatch({ type: "start", request }), []);
  const completeSearch = useCallback((request: SearchRequest, result: VisualResult) => dispatch({ type: "complete", request, result }), []);
  const finishSearch = useCallback((request: SearchRequest) => dispatch({ type: "finish", request }), []);
  const confirmSelection = useCallback((input: SearchInput, queryId: string, productId: number) => dispatch({ type: "confirm", input, queryId, productId }), []);
  const value = useMemo(() => ({ session, replacePhoto, setCrop, startSearch, completeSearch, finishSearch, confirmSelection }),
    [session, replacePhoto, setCrop, startSearch, completeSearch, finishSearch, confirmSelection]);

  return <SearchSessionContext.Provider value={value}>
    {process.env.NODE_ENV === "development" ? <AISearchPreviewSessionProvider>{children}</AISearchPreviewSessionProvider> : children}
  </SearchSessionContext.Provider>;
}

export function useAISearchSession() {
  const context = useContext(SearchSessionContext);
  if (!context) throw new Error("AI search session provider is missing");
  return context;
}
