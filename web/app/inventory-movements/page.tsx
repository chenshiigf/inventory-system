import AppLayout from "@/components/app-layout/AppLayout";
import InventoryMovementsWorkspace from "@/components/inventory/InventoryMovementsWorkspace";

export default async function InventoryMovementsPage({
  searchParams,
}: {
  searchParams: Promise<{ search?: string | string[] }>;
}) {
  const params = await searchParams;
  const initialSearch = Array.isArray(params.search)
    ? params.search[0] ?? ""
    : params.search ?? "";

  return (
    <AppLayout>
      <InventoryMovementsWorkspace initialSearch={initialSearch} />
    </AppLayout>
  );
}
