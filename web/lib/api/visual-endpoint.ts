import { getApiBaseUrl } from "@/lib/api/client";

// Dev uses the frontend's own Basic Auth protection space through the Next proxy.
// Production keeps the existing static frontend / Nginx deployment contract.
export function getVisualApiBaseUrl(): string {
  return process.env.NODE_ENV === "development" ? "" : getApiBaseUrl();
}
