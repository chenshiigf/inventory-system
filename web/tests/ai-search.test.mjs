import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const requirePackage = createRequire(import.meta.url);
const modules = new Map();
function load(relative) {
  const filename = path.join(root, relative);
  if (modules.has(filename)) return modules.get(filename).exports;
  const compiledModule = { exports: {} };
  modules.set(filename, compiledModule);
  const code = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = (name) => name.startsWith("@/") ? load(`${name.slice(2)}.ts`) : requirePackage(name);
  vm.runInThisContext(`(function(exports, require, module) {${code}\n})`, { filename })(compiledModule.exports, localRequire, compiledModule);
  return compiledModule.exports;
}
const adapter = load("lib/ai-search.ts");
const api = load("lib/api/visual-search.ts");
const source = { source: "production-catalog", source_id: "aliyun-inventory-production", index_namespace: `production-${"a".repeat(64)}`, catalog_fingerprint: "b".repeat(64) };
const product = () => ({ id: 318, product_key: "aliyun-inventory-production:318", source_id: source.source_id,
  product_code: "test-code", image_path: "/api/visual-search/catalog/products/318/image?source=production-catalog",
  image_sha256: "c".repeat(64), category_name: null, warehouse_name: "test", size: "test-size", unit: "pcs",
  is_active: true, source_present: true, synced_at: "2026-10-12T00:00:00Z",
  packagings: [{ id: 1, packing_qty: 48, sort_order: 0 }] });
const result = () => ({ ...source, query_id: "d".repeat(32), model: "qwen3-vl-embedding", version: "v1",
  elapsed_ms: 10, crop: null, usage: { input_tokens: 22, image_tokens: 270, total_tokens: 292 },
  call_summary: { api_calls: 1, failed_calls: 0, input_tokens: 22, image_tokens: 270, total_tokens: 292, estimated_cost_cny: .0005014, cost_complete: true },
  timings_ms: { image_preprocessing: 1, sqlite_vectors: 1, catalog_bulk_read: 1, gallery_hash_and_vector_validation: 1, query_persistence: 1, response_details: 1, total: 10 },
  candidates: [{ product: product(), similarity: .7 }], message: "test" });

test("production adapter preserves identity, missing names and multiple packaging quantities", () => {
  const p = product();
  p.packagings.push({ id: 2, packing_qty: 192, sort_order: 1 });
  adapter.validateProductionResult(result());
  const view = adapter.toAISearchProduct(p);
  assert.equal(view.productKey, p.product_key);
  assert.equal(view.productName, "商品名称未填写");
  assert.equal(view.image, p.image_path);
  assert.equal(adapter.formatAISearchPackaging(view), "48件/箱、192件/箱");
  assert.equal("carton_count" in view, false);
});
test("reject development IDs, unsafe images, duplicate candidates and reversed scores", () => {
  for (const change of [r => { r.source = "development"; }, r => { r.source_id = "development"; },
    r => { r.candidates[0].product.product_key = "development:318"; },
    r => { r.candidates[0].product.image_path = "/uploads/wrong.jpg"; },
    r => { r.usage.total_tokens = 0; }, r => { r.call_summary.api_calls = -1; },
    r => { r.timings_ms.total = NaN; }, r => { r.crop = [0, 1]; },
    r => { r.candidates.push(r.candidates[0]); }, r => { r.candidates[0].similarity = NaN; },
    r => { r.candidates.push({ product: { ...product(), id: 319, product_key: "aliyun-inventory-production:319", image_path: null }, similarity: .8 }); }]) {
    const r = result(); change(r);
    assert.throws(() => adapter.validateProductionResult(r), adapter.AISearchSourceError);
  }
});
test("empty candidate response is valid and details cannot change source or product ID", () => {
  const r = result(); r.candidates = [];
  adapter.validateProductionResult(r);
  adapter.validateProductionDetail({ ...source, product: product() }, 318, r);
  assert.throws(() => adapter.validateProductionDetail({ ...source, product: product() }, 319, r));
  assert.throws(() => adapter.validateProductionDetail({ ...source, catalog_fingerprint: "f".repeat(64), product: product() }, 318, r));
});
test("search uses one multipart request with explicit production source and top_k=5", async () => {
  const original = globalThis.fetch;
  const requests = [];
  try {
    globalThis.fetch = async (url, init) => { requests.push({ url, init }); return Response.json(result()); };
    await api.searchVisual(new File(["test"], "field.jpg", { type: "image/jpeg" }), undefined, "production-catalog");
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, "/api/visual-search/search?source=production-catalog");
    assert.equal(requests[0].init.body.get("top_k"), "5");
    assert.equal(requests[0].init.body.has("crop"), false);
    assert.deepEqual([...requests[0].init.body.keys()], ["file", "top_k"]);
    assert.equal(requests[0].init.credentials, "include");
    assert.equal(requests[0].init.headers["Content-Type"], undefined);
  } finally { globalThis.fetch = original; }
});
test("search failure is never automatically retried; feedback sends no_match=false", async () => {
  const original = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async () => { calls += 1; return Response.json({ detail: "failed" }, { status: 500 }); };
    await assert.rejects(api.searchVisual(new File(["x"], "x.jpg"), undefined, "production-catalog"));
    assert.equal(calls, 1);
    globalThis.fetch = async (url, init) => {
      assert.equal(url, "/api/visual-search/feedback?source=production-catalog");
      assert.deepEqual(JSON.parse(init.body), { query_id: "d".repeat(32), selected_product_id: 318, no_match: false });
      return Response.json({ saved: true, query_id: "d".repeat(32) });
    };
    await api.saveVisualFeedback("d".repeat(32), 318, "production-catalog");
  } finally { globalThis.fetch = original; }
});
