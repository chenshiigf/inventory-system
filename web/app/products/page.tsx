import { Suspense } from "react";
import AppLayout from "@/components/app-layout/AppLayout";
import InventoryWorkspace from "@/components/inventory/InventoryWorkspace";
import { parseProductListState } from "@/lib/product-list-state";

export default async function ProductsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const initialState = parseProductListState(await searchParams);

  return (
    <AppLayout>
      <Suspense fallback={null}>
        <InventoryWorkspace initialState={initialState} />
      </Suspense>
    </AppLayout>
  );
}
