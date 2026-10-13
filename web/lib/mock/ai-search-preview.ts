import type { AISearchProduct } from "@/lib/ai-search";
import type { PreviewResult } from "@/lib/ai-search-preview-session";

function svgImage(body: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(body)}`;
}
function bottle(color: string, x = 0, y = 0): string {
  return `<g transform="translate(${x} ${y})"><path d="M62 28h76v20h16v146q-54 30-108 0V48h16z" fill="${color}"/><rect x="46" y="103" width="108" height="48" rx="4" fill="#fffdf5"/><path d="M60 59v35" stroke="#fff" stroke-opacity=".45" stroke-width="8"/><ellipse cx="100" cy="48" rx="54" ry="11" fill="${color}"/><circle cx="88" cy="36" r="3" fill="#233047"/><circle cx="100" cy="33" r="3" fill="#233047"/><circle cx="112" cy="36" r="3" fill="#233047"/></g>`;
}
const colors = ["#bd373e", "#286a95", "#537653", "#c09a46", "#80668a"];
export const previewProducts: AISearchProduct[] = colors.map((color, index) => ({
  productId: index + 1, productKey: `mock:${index + 1}`, productCode: `DEMO-00${index + 1}`, productName: "",
  image: svgImage(`<svg xmlns="http://www.w3.org/2000/svg" width="260" height="260" viewBox="0 0 260 260"><rect width="260" height="260" fill="#f7f7f8"/>${bottle(color, 30, 14)}<text x="130" y="244" text-anchor="middle" font-size="12" fill="#717b89">Mock 示例商品</text></svg>`),
  size: "3.5×2×5.5cm", packagings: [{ id: index + 1, packingQty: index % 2 ? 144 : 192, unit: "件" }],
  warehouse: "示例仓库", imageSyncedAt: null, active: true,
}));
export const previewScene = svgImage(`<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="1000" viewBox="0 0 1500 1000"><rect width="1500" height="1000" fill="#eeeef1"/><path d="M0 740h1500M350 0v1000M1150 0v1000" stroke="#e2e2e7" stroke-width="2"/><g transform="translate(240 210) scale(1.8)">${bottle(colors[0])}</g><g transform="translate(610 210) scale(1.8)">${bottle(colors[1])}</g><g transform="translate(980 210) scale(1.8)">${bottle(colors[2])}</g><text x="750" y="870" text-anchor="middle" font-size="26" fill="#737d8b">Mock 示例照片 · 可分别框选三个商品</text></svg>`);

function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(new DOMException("Aborted", "AbortError")); return; }
    const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, ms);
    signal?.addEventListener("abort", abort, { once: true });
  });
}
// No fetch, real IDs, provider, API credentials or production image references.
export async function searchPreviewMock(queryId: string, regionNumber: number, fail: boolean, signal?: AbortSignal): Promise<PreviewResult> {
  await pause(700, signal);
  if (fail) throw new Error("模拟识别失败，请手动重试；没有调用真实 API。");
  const offset = Math.max(0, regionNumber - 1) % previewProducts.length;
  return { queryId, source: "mock", candidates: previewProducts.map((_, i) => ({ product: previewProducts[(offset + i) % previewProducts.length], score: 0.9 - i * 0.08 })) };
}
export async function confirmPreviewMock(queryId: string, signal?: AbortSignal): Promise<{ saved: true; queryId: string }> {
  await pause(220, signal);
  return { saved: true, queryId };
}
