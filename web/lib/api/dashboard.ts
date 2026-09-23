import { apiRequest } from "@/lib/api/client";
import type { DashboardSummary } from "@/types/inventory";

export function getDashboardSummary(
  signal?: AbortSignal,
): Promise<DashboardSummary> {
  return apiRequest<DashboardSummary>("/api/dashboard/summary", {
    method: "GET",
    signal,
  });
}
