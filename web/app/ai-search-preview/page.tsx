import { notFound } from "next/navigation";

export default async function AISearchPreviewPage() {
  // A production/static export must never render the Mock workspace.
  if (process.env.NODE_ENV === "development") {
    const { default: AISearchPreviewWorkspace } = await import("@/components/ai-search/AISearchPreviewWorkspace");
    return <AISearchPreviewWorkspace />;
  }
  notFound();
}
