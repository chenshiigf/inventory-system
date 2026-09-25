"use client";

import { useSearchParams } from "next/navigation";
import ProductDetailWorkspace from "@/components/inventory/ProductDetailWorkspace";

export default function ProductDetailRoute() {
  const searchParams = useSearchParams();
  const productId = searchParams.get("id") ?? "";
  const returnTo = searchParams.get("returnTo") ?? undefined;

  return <ProductDetailWorkspace productId={productId} returnTo={returnTo} />;
}
