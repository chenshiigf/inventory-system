import { Suspense } from "react";
import AppLayout from "@/components/app-layout/AppLayout";
import InventoryWorkspace from "@/components/inventory/InventoryWorkspace";

export default function ProductsPage() {
  return (
    <AppLayout>
      <Suspense fallback={null}>
        <InventoryWorkspace />
      </Suspense>
    </AppLayout>
  );
}
