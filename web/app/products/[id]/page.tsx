import AppLayout from "@/components/app-layout/AppLayout";
import ProductDetailWorkspace from "@/components/inventory/ProductDetailWorkspace";

export default async function ProductDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <AppLayout>
      <ProductDetailWorkspace productId={id} />
    </AppLayout>
  );
}
