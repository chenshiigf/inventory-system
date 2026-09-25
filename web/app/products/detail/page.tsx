import { Suspense } from "react";
import AppLayout from "@/components/app-layout/AppLayout";
import ProductDetailRoute from "@/components/inventory/ProductDetailRoute";

export default function ProductDetailPage() {
  return (
    <AppLayout>
      <Suspense fallback={null}>
        <ProductDetailRoute />
      </Suspense>
    </AppLayout>
  );
}
