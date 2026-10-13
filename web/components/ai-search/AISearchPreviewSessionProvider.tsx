"use client";

import { createContext, useContext, useEffect, useReducer, useRef, type Dispatch, type ReactNode } from "react";
import { initialPreviewSession, previewSessionReducer, type PreviewAction, type PreviewSession } from "@/lib/ai-search-preview-session";

const PreviewContext = createContext<{ session: PreviewSession; dispatch: Dispatch<PreviewAction> } | null>(null);

export function AISearchPreviewSessionProvider({ children }: { children: ReactNode }) {
  const [session, dispatch] = useReducer(previewSessionReducer, undefined, initialPreviewSession);
  const currentUrl = useRef("");
  useEffect(() => {
    if (currentUrl.current && currentUrl.current !== session.image) URL.revokeObjectURL(currentUrl.current);
    currentUrl.current = session.image;
  }, [session.image]);
  useEffect(() => () => { if (currentUrl.current) URL.revokeObjectURL(currentUrl.current); }, []);
  return <PreviewContext.Provider value={{ session, dispatch }}>{children}</PreviewContext.Provider>;
}
export function useAISearchPreviewSession() {
  const context = useContext(PreviewContext);
  if (!context) throw new Error("AI search preview session provider is missing");
  return context;
}
