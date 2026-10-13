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
const cropTools = load("lib/image-crop.ts");
const sessionTools = load("lib/ai-search-session.ts");
const api = load("lib/api/visual-search.ts");
const endpoint = load("lib/api/visual-endpoint.ts");
const nextConfig = load("next.config.ts").default;
const { PHASE_DEVELOPMENT_SERVER, PHASE_PRODUCTION_BUILD } = requirePackage("next/constants");
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
  assert.equal(view.productName, p.product_code);
  assert.equal(view.image, p.image_path);
  assert.equal(adapter.formatAISearchPackaging(view), "48件/箱、192件/箱");
  assert.equal("carton_count" in view, false);
});

test("cropped request retains the original File and adds only the selected crop", async () => {
  const original = globalThis.fetch;
  const file = new File(["original-image-bytes"], "scene.png", { type: "image/png" });
  const crop = [0.1, 0.2, 0.3, 0.4];
  let count = 0;
  try {
    globalThis.fetch = async (url, init) => {
      count += 1;
      assert.equal(url, "/api/visual-search/search?source=production-catalog");
      assert.equal(await init.body.get("file").text(), await file.text());
      assert.equal(init.body.get("file").name, file.name);
      assert.equal(init.body.get("crop"), JSON.stringify(crop));
      assert.equal(init.body.get("top_k"), "5");
      return Response.json({ ...result(), crop });
    };
    await api.searchVisual(file, crop, "production-catalog");
    assert.equal(count, 1);
  } finally { globalThis.fetch = original; }
});

test("scaled and reverse mouse drags use source ratios, clamp at image bounds", () => {
  const bounds = { left: 100, top: 50, width: 400, height: 200 };
  const start = cropTools.relativeImagePoint(420, 210, bounds);
  const end = cropTools.relativeImagePoint(180, 90, bounds);
  const crop = cropTools.cropFromPoints(start, end);
  crop.forEach((v, i) => assert.ok(Math.abs(v - [0.2, 0.2, 0.6, 0.6][i]) < 1e-9));
  assert.deepEqual(cropTools.cropPixelBounds(crop, 2000, 1000), { left: 400, top: 200, width: 1200, height: 600 });
  assert.deepEqual(cropTools.relativeImagePoint(-1, 400, bounds), [0, 1]);
  assert.equal(cropTools.validSingleCrop([0, 0, 0.01, 0.01], 100, 100), false);
  assert.equal(cropTools.validSingleCrop([0.9, 0, 0.2, 1], 100, 100), false);
  assert.equal(cropTools.validSingleCrop([0, 0, 0.0025, 0.003333334], 4000, 3000), true, "small regions follow the backend pixel minimum, not a fixed display ratio");
});

test("half-pixel preview bounds match Python ties-to-even crop coordinates", () => {
  assert.deepEqual(cropTools.cropPixelBounds([0.005, 0.015, 0.3, 0.3], 100, 100), { left: 0, top: 2, width: 30, height: 30 });
});

function photoSession() {
  return sessionTools.searchSessionReducer(sessionTools.initialSearchSession(), { type: "photo", file: new File(["original"], "original.png"), image: "blob:original", width: 1000, height: 800 });
}
function requestFor(state, requestId = "one") { return { ...sessionTools.currentSearchInput(state), requestId }; }
function complete(state, request) {
  state = sessionTools.searchSessionReducer(state, { type: "start", request });
  return sessionTools.searchSessionReducer(state, { type: "complete", request, result: { ...result(), crop: request.crop } });
}

test("single crop replaces previous crop and invalidates result, confirmation and stale input", () => {
  let state = photoSession();
  const file = state.file;
  const initial = requestFor(state);
  state = complete(state, initial);
  state = sessionTools.searchSessionReducer(state, { type: "confirm", input: initial, queryId: state.result.query_id, productId: 318 });
  assert.equal(state.confirmedId, 318);
  state = sessionTools.searchSessionReducer(state, { type: "crop", crop: [0, 0, 0.5, 1] });
  assert.equal(state.result, null); assert.equal(state.confirmedId, null);
  assert.equal(state.file, file); assert.equal(state.image, "blob:original");
  const firstCrop = requestFor(state);
  state = complete(state, firstCrop);
  state = sessionTools.searchSessionReducer(state, { type: "crop", crop: [0.5, 0, 0.5, 1] });
  assert.deepEqual(state.crop, [0.5, 0, 0.5, 1]);
  assert.equal(state.cropVersion, 2);
  assert.equal("regions" in state, false);
  assert.equal(sessionTools.searchSessionReducer(state, { type: "complete", request: firstCrop, result: { ...result(), crop: firstCrop.crop } }), state);
  assert.equal(sessionTools.searchSessionReducer(state, { type: "confirm", input: firstCrop, queryId: result().query_id, productId: 318 }), state);
  state = sessionTools.searchSessionReducer(state, { type: "crop", crop: null });
  assert.equal(state.crop, null); assert.equal(state.result, null); assert.equal(state.file, file);
});

test("new photo or newer request cannot be overwritten by late responses or old cleanup", () => {
  let state = photoSession();
  const old = requestFor(state);
  state = sessionTools.searchSessionReducer(state, { type: "start", request: old });
  state = sessionTools.searchSessionReducer(state, { type: "finish", request: old });
  const next = requestFor(state, "next");
  state = sessionTools.searchSessionReducer(state, { type: "start", request: next });
  assert.equal(sessionTools.searchSessionReducer(state, { type: "complete", request: old, result: result() }), state);
  assert.equal(sessionTools.searchSessionReducer(state, { type: "finish", request: old }), state);
  assert.equal(sessionTools.searchSessionReducer(state, { type: "complete", request: next, result: { ...result(), crop: [0, 0, 0.5, 1] } }), state, "wrong backend crop rejected");
  state = sessionTools.searchSessionReducer(state, { type: "photo", file: new File(["new"], "new.png"), image: "blob:new", width: 100, height: 100 });
  assert.equal(state.imageVersion, 2);
  assert.equal(sessionTools.searchSessionReducer(state, { type: "complete", request: next, result: result() }), state);
});

test("copy uses the finally confirmed candidate, excludes empty codes and preserves stable ID", () => {
  const r = result();
  r.candidates.push({ product: { ...product(), id: 319, product_key: "aliyun-inventory-production:319", product_code: "NEW-CODE-319" }, similarity: 0.6 });
  assert.equal(adapter.confirmedProductCode(r, null), null);
  assert.equal(adapter.confirmedProductCode(r, 319), "NEW-CODE-319");
  assert.equal(adapter.confirmedProductCode(r, 999), null);
  r.candidates[1].product.product_code = " ";
  assert.equal(adapter.confirmedProductCode(r, 319), null);
  assert.equal(adapter.toAISearchProduct({ ...product(), product_code: null }).productName, "商品资料");
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

test("development visuals use same-origin requests and images without embedding credentials", async () => {
  const originalFetch = globalThis.fetch;
  const oldMode = process.env.NODE_ENV;
  const oldBase = process.env.NEXT_PUBLIC_API_BASE_URL;
  try {
    process.env.NODE_ENV = "development";
    process.env.NEXT_PUBLIC_API_BASE_URL = "http://127.0.0.1:8002";
    const requests = [];
    globalThis.fetch = async (url, init) => { requests.push({ url, init }); return Response.json(result()); };
    assert.equal(endpoint.getVisualApiBaseUrl(), "");
    assert.equal(adapter.toAISearchProduct(product()).image, product().image_path);
    await api.searchVisual(new File(["test"], "field.jpg"), undefined, "production-catalog");
    await api.getVisualProduct(318, "production-catalog");
    await api.saveVisualFeedback("d".repeat(32), 318, "production-catalog");
    await api.getVisualStatus("production-catalog");
    assert.equal(requests.length, 4);
    for (const req of requests) {
      assert.ok(req.url.startsWith("/api/visual-search/"));
      assert.ok(req.url.endsWith("?source=production-catalog"));
      assert.equal(req.init.credentials, "include");
      assert.equal(req.init.headers.Authorization, undefined);
    }
    await load("lib/api/client.ts").apiRequest("/api/products");
    assert.equal(requests[4].url, "http://127.0.0.1:8002/api/products");
    process.env.NODE_ENV = "production";
    assert.equal(endpoint.getVisualApiBaseUrl(), "http://127.0.0.1:8002");
  } finally {
    globalThis.fetch = originalFetch;
    if (oldMode === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = oldMode;
    if (oldBase === undefined) delete process.env.NEXT_PUBLIC_API_BASE_URL; else process.env.NEXT_PUBLIC_API_BASE_URL = oldBase;
  }
});

test("proxy is local and development-only, production preserves the static export", async () => {
  const previous = process.env.VISUAL_DEV_API_ORIGIN;
  try {
    delete process.env.VISUAL_DEV_API_ORIGIN;
    const dev = nextConfig(PHASE_DEVELOPMENT_SERVER);
    assert.equal(dev.output, undefined);
    assert.equal(dev.skipTrailingSlashRedirect, true);
    assert.deepEqual(await dev.rewrites(), [{ source: "/api/visual-search/:path*", destination: "http://127.0.0.1:8002/api/visual-search/:path*" }]);
    process.env.VISUAL_DEV_API_ORIGIN = "http://127.0.0.1:8128";
    assert.match((await nextConfig(PHASE_DEVELOPMENT_SERVER).rewrites())[0].destination, /^http:\/\/127\.0\.0\.1:8128\//);
    for (const target of ["https://external.invalid", "http://external.invalid", "http://placeholder-user:placeholder-value@127.0.0.1:8002",
      "http://127.0.0.1:8002/api", "http://127.0.0.1:8002/?key=placeholder", "http://127.0.0.1:8002/#fragment",
      "http://127.0.0.1.attacker.invalid:8002", "http://127.0.0.1@attacker.invalid:8002", "ftp://127.0.0.1:8002", "invalid"]) {
      process.env.VISUAL_DEV_API_ORIGIN = target;
      assert.throws(() => nextConfig(PHASE_DEVELOPMENT_SERVER), /local HTTP origin/);
    }
    assert.deepEqual(nextConfig(PHASE_PRODUCTION_BUILD), { output: "export", trailingSlash: true });
  } finally {
    if (previous === undefined) delete process.env.VISUAL_DEV_API_ORIGIN; else process.env.VISUAL_DEV_API_ORIGIN = previous;
  }
});
