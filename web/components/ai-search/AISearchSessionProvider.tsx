"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { VisualResult } from "@/lib/api/visual-search";
import { AISearchPreviewSessionProvider } from "./AISearchPreviewSessionProvider";

interface SearchSession {
  file: File | null;
  image: string;
  result: VisualResult | null;
  confirmedId: number | null;
}

interface SessionContext {
  session: SearchSession;
  replacePhoto: (file: File | null) => void;
  clearResult: () => void;
  completeSearch: (file: File, result: VisualResult) => void;
  confirmSelection: (queryId: string, productId: number) => void;
}

const SearchSessionContext = createContext<SessionContext | null>(null);

// The root layout survives client navigation. Nothing is written to browser storage.
export function AISearchSessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SearchSession>({ file: null, image: "", result: null, confirmedId: null });
  const previewUrl = useRef<string | null>(null);

  useEffect(() => () => {
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);

  const replacePhoto = useCallback((file: File | null) => {
    const image = file ? URL.createObjectURL(file) : "";
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = image || null;
    setSession({ file, image, result: null, confirmedId: null });
  }, []);

  const clearResult = useCallback(() => {
    setSession((previous) => ({ ...previous, result: null, confirmedId: null }));
  }, []);

  const completeSearch = useCallback((file: File, result: VisualResult) => {
    setSession((previous) => previous.file === file ? { ...previous, result, confirmedId: null } : previous);
  }, []);

  const confirmSelection = useCallback((queryId: string, productId: number) => {
    setSession((previous) => previous.result?.query_id === queryId
      && previous.result.candidates.some((candidate) => candidate.product.id === productId)
      ? { ...previous, confirmedId: productId } : previous);
  }, []);

  const value = useMemo(() => ({ session, replacePhoto, clearResult, completeSearch, confirmSelection }),
    [session, replacePhoto, clearResult, completeSearch, confirmSelection]);

  return <SearchSessionContext.Provider value={value}>
    {process.env.NODE_ENV === "development" ? <AISearchPreviewSessionProvider>{children}</AISearchPreviewSessionProvider> : children}
  </SearchSessionContext.Provider>;
}

export function useAISearchSession() {
  const context = useContext(SearchSessionContext);
  if (!context) throw new Error("AI search session provider is missing");
  return context;
}
