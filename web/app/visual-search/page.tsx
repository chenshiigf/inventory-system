import { notFound } from "next/navigation";

export default async function VisualSearchPage() {
  // Keep the multi-region/indexing test tool out of the family production UI.
  if (process.env.NODE_ENV === "development") {
    const [{ default: AppLayout }, { default: VisualSearchWorkspace }] = await Promise.all([
      import("@/components/app-layout/AppLayout"),
      import("@/components/visual-search/VisualSearchWorkspace"),
    ]);
    return <AppLayout><VisualSearchWorkspace /></AppLayout>;
  }
  notFound();
}
