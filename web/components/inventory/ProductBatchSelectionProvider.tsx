"use client";

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { useProductBatchSelection } from "@/hooks/useProductBatchSelection";

type Selection = ReturnType<typeof useProductBatchSelection>;
interface ProductBatchSelectionContext extends Selection {
  batchMode: boolean;
  setBatchMode: (enabled: boolean) => void;
  captureCompletion: () => (exitBatchMode?: boolean) => void;
}

const BatchSelectionContext = createContext<ProductBatchSelectionContext | null>(null);

// Owned by the root layout; client navigation preserves this in-memory session.
export function ProductBatchSelectionProvider({ children }: { children: ReactNode }) {
  const selection = useProductBatchSelection();
  const [batchMode, setBatchModeState] = useState(false);
  const revision = useRef(0);
  const { setProductSelected, addProducts, removeProducts, clear } = selection;

  const changeBatchMode = useCallback((enabled: boolean) => {
    revision.current += 1;
    setBatchModeState(enabled);
  }, []);

  const selectProduct = useCallback<Selection["setProductSelected"]>((product, selected) => {
    const accepted = setProductSelected(product, selected);
    if (accepted) revision.current += 1;
    return accepted;
  }, [setProductSelected]);

  const selectProducts = useCallback<Selection["addProducts"]>((products) => {
    const accepted = addProducts(products);
    if (accepted) revision.current += 1;
    return accepted;
  }, [addProducts]);

  const deselectProducts = useCallback<Selection["removeProducts"]>((ids) => {
    revision.current += 1;
    removeProducts(ids);
  }, [removeProducts]);

  const clearSelection = useCallback(() => {
    revision.current += 1;
    clear();
  }, [clear]);

  // Capture in an event handler, before starting an operation. A late success
  // must not clear a selection the user changed while that operation was pending.
  const captureCompletion = useCallback(() => {
    const startedAt = revision.current;
    return (exitBatchMode = false) => {
      if (revision.current !== startedAt) return;
      revision.current += 1;
      clear();
      if (exitBatchMode) setBatchModeState(false);
    };
  }, [clear]);

  return <BatchSelectionContext.Provider value={{
    ...selection,
    batchMode,
    setBatchMode: changeBatchMode,
    setProductSelected: selectProduct,
    addProducts: selectProducts,
    removeProducts: deselectProducts,
    clear: clearSelection,
    captureCompletion,
  }}>{children}</BatchSelectionContext.Provider>;
}

export function useProductBatchSelectionContext() {
  const context = useContext(BatchSelectionContext);
  if (!context) throw new Error("Product batch selection provider is missing");
  return context;
}
