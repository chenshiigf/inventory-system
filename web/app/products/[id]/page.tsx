import AppLayout from "@/components/app-layout/AppLayout";
import ProductDetailWorkspace from "@/components/inventory/ProductDetailWorkspace";

export default async function ProductDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { id } = await params;
  const resolvedSearchParams = await searchParams;
  const rawReturnTo = resolvedSearchParams.returnTo;
  const returnTo = Array.isArray(rawReturnTo) ? rawReturnTo[0] : rawReturnTo;

  return (
    <AppLayout>
      <ProductDetailWorkspace productId={id} returnTo={returnTo} />
    </AppLayout>
  );
}
