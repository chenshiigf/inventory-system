import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const response = () => ({
  source: "production-catalog", source_id: "aliyun-inventory-production",
  index_namespace: `production-${"a".repeat(64)}`, catalog_fingerprint: "b".repeat(64),
  query_id: "c".repeat(32), model: "qwen3-vl-embedding", version: "v1", elapsed_ms: 1,
  crop: null, usage: { input_tokens: 0, image_tokens: 1, total_tokens: 1 },
  call_summary: { api_calls: 1, failed_calls: 0, input_tokens: 0, image_tokens: 1, total_tokens: 1,
    estimated_cost_cny: 0.0000018, cost_complete: true },
  timings_ms: { image_preprocessing: 0, sqlite_vectors: 0, catalog_bulk_read: 0,
    gallery_hash_and_vector_validation: 0, query_persistence: 0, response_details: 0, total: 1 },
  candidates: [], message: "Mock only",
});

// Execute the production component's actual button handler with small UI/hook
// mocks. Browser APIs reflect a public HTTP origin; every fetch is in-process.
function workspace({ crypto = {}, failInitialization = false, crop = null, initialResult = null, confirmedId = null, clipboard } = {}) {
  const requests = [], versionRequests = [], states = [], refs = [], modules = new Map(), effects = [];
  const listeners = new Map();
  const events = { addEventListener(name, callback) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(callback); },
    removeEventListener(name, callback) { listeners.get(name)?.delete(callback); } };
  let catalogVersion = response(), versionFailure = false;
  let stateIndex = 0, refIndex = 0, contextValue, resolveSearch;
  let initializationFailures = failInitialization ? 1 : 0;
  const jsx = (type, props) => ({ type, props: props ?? {} });
  const context = vm.createContext({
    crypto, isSecureContext: false, File, FormData, Response, DOMException, AbortSignal,
    navigator: { clipboard },
    AbortController: class {
      constructor() {
        if (initializationFailures-- > 0) throw new Error("Request initialization failed");
        return new AbortController();
      }
    },
    window: { ...events, location: { protocol: "http:", origin: "http://inventory.example:8082" }, matchMedia: () => ({ matches: false }) },
    document: { ...events, visibilityState: "visible" },
    process: { env: { NODE_ENV: "production" } },
    fetch: async (url, init) => {
      if (url.includes("/catalog/version?")) {
        versionRequests.push(url);
        return versionFailure ? Response.json({ detail: "unavailable" }, { status: 503 }) : Response.json(catalogVersion);
      }
      requests.push({ url, init });
      if (url.includes("/feedback?")) return Response.json({ saved: true, query_id: JSON.parse(init.body).query_id });
      return new Promise(resolve => { resolveSearch = () => resolve(Response.json(response())); });
    },
  });
  const ui = Object.fromEntries(["Button", "Card", "Alert", "Empty", "Image", "Input", "Spin"].map(name => [name, name]));
  ui.App = { useApp: () => ({ message: { success() {}, warning() {}, error() {} } }) };
  const react = {
    useEffect(callback) { effects.push(callback); },
    useRef(value) { const index = refIndex++; return refs[index] ??= { current: value }; },
    useState(value) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = value;
      return [states[index], next => { states[index] = typeof next === "function" ? next(states[index]) : next; }];
    },
  };
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative).exports;
    const compiledModule = { exports: {} };
    modules.set(relative, compiledModule);
    const code = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const require = name => {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx };
      if (name === "antd") return ui;
      if (name === "@ant-design/icons") return new Proxy({}, { get: (_, key) => String(key) });
      if (name === "./AISearchSessionProvider") return { useAISearchSession: () => contextValue };
      if (name === "@/lib/ai-search-image") return {
        AIImageProcessingError: class extends Error {},
        prepareAISearchImage: async file => ({ file, serverCrop: null }),
      };
      if (name.startsWith("@/lib/")) return load(`${name.slice(2)}.ts`);
      if (name.startsWith("./") || name.startsWith("@/components/")) return { default: name };
      throw new Error(`Unexpected import: ${name}`);
    };
    vm.runInContext(`(function(exports, require, module) {${code}\n})`, context)(compiledModule.exports, require, compiledModule);
    return compiledModule.exports;
  }
  const sessionTools = load("lib/ai-search-session.ts");
  let session = sessionTools.searchSessionReducer(sessionTools.initialSearchSession(), {
    type: "photo", file: new File(["Mock image"], "field.jpg", { type: "image/jpeg" }),
    image: "blob:mock", width: 1000, height: 800,
  });
  if (crop) session = sessionTools.searchSessionReducer(session, { type: "crop", crop });
  const dispatch = action => { session = sessionTools.searchSessionReducer(session, action); };
  if (initialResult) {
    const request = { ...sessionTools.currentSearchInput(session), requestId: "fixture" };
    dispatch({ type: "start", request });
    dispatch({ type: "complete", request, result: initialResult });
    if (confirmedId !== null) dispatch({ type: "confirm", input: request, queryId: initialResult.query_id, productId: confirmedId });
  }
  const Component = load("components/ai-search/AISearchWorkspace.tsx").default;
  function render() {
    stateIndex = refIndex = 0;
    contextValue = { session, replacePhoto() {}, setCrop() {},
      invalidateCatalog: queryId => dispatch({ type: "catalog-invalid", queryId }),
      confirmSelection: (input, queryId, productId) => dispatch({ type: "confirm", input, queryId, productId }),
      startSearch: request => dispatch({ type: "start", request }),
      completeSearch: (request, result, submittedCrop) => dispatch({ type: "complete", request, result, submittedCrop }),
      finishSearch: request => dispatch({ type: "finish", request }),
    };
    return Component();
  }
  function find(node, predicate) {
    if (!node || typeof node !== "object") return null;
    if (Array.isArray(node)) return node.map(child => find(child, predicate)).find(Boolean);
    return predicate(node) ? node : find(node.props?.children, predicate);
  }
  const button = () => find(render(), node => node.type === "Button" && ["开始找商品", "识别框选商品"].includes(node.props.children));
  return { context, requests, versionRequests, sessionTools, render, button, states, find, getSession: () => session,
    setCatalogVersion: value => { catalogVersion = value; }, failCatalog: () => { versionFailure = true; },
    mountEffects: () => { effects.length = 0; render(); effects.splice(0).forEach(callback => callback()); },
    emit: name => { listeners.get(name)?.forEach(callback => callback()); },
    release: () => resolveSearch(), error: () => find(render(), node => node.type === "Alert" && node.props.type === "error") };
}

test("HTTP click without randomUUID starts exactly one search and releases the busy state", async () => {
  for (const crop of [null, [0.1, 0.2, 0.4, 0.5]]) {
    const app = workspace({ crop });
    assert.equal(app.context.isSecureContext, false);
    assert.equal(typeof app.context.crypto.randomUUID, "undefined");
    const pending = app.button().props.onClick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.requests.length, 1);
    assert.equal(app.states[0], "searching");
    assert.equal(app.button().props.disabled, true);
    await app.button().props.onClick();
    assert.equal(app.requests.length, 1, "no duplicate paid request");
    assert.equal(app.requests[0].url, "/api/visual-search/search?source=production-catalog");
    assert.equal(app.requests[0].init.body.get("top_k"), "5");
    assert.equal(app.requests[0].init.body.has("crop"), false, "preprocessed pixels are never cropped twice");
    app.release(); await pending;
    assert.equal(app.states[0], "success");
    assert.equal(app.getSession().activeRequestId, null);
    assert.equal(app.button().props.disabled, false);
  }
});

test("copy rejects changed catalog and removes old result, confirmation and manual fallback", async () => {
  const r = response();
  r.candidates = [{ product: { id: 1, product_key: "aliyun-inventory-production:1", source_id: r.source_id,
    product_code: "OLD-1", image_path: null, category_name: null, warehouse_name: null, size: "Mock", unit: "pcs",
    is_active: true, source_present: true, synced_at: "2026-10-12T00:00:00Z", packagings: [] }, similarity: .9 }];
  const copied = [];
  const app = workspace({ initialResult: r, confirmedId: 1, clipboard: { writeText: async value => copied.push(value) } });
  app.setCatalogVersion({ ...r, catalog_fingerprint: "e".repeat(64) });
  const alert = app.find(app.render(), node => node.type === "Alert" && node.props.type === "success");
  await app.find(alert.props.description, node => node.type === "Button").props.onClick();
  assert.equal(app.getSession().result, null);
  assert.equal(app.getSession().confirmedId, null);
  assert.deepEqual(copied, []);
  assert.equal(app.requests.length, 0, "no model search or feedback");
  assert.match(app.error().props.title, /目录已更新/);
  assert.ok(!app.find(app.render(), node => node.type === "Input"));
});

test("unavailable version never copies an unverified code", async () => {
  const r = response();
  r.candidates = [{ product: { id: 1, product_key: "aliyun-inventory-production:1", source_id: r.source_id,
    product_code: "OLD-1", image_path: null, category_name: null, warehouse_name: null, size: "Mock", unit: "pcs",
    is_active: true, source_present: true, synced_at: "2026-10-12T00:00:00Z", packagings: [] }, similarity: .9 }];
  const copied = [];
  const app = workspace({ initialResult: r, confirmedId: 1, clipboard: { writeText: async value => copied.push(value) } });
  app.failCatalog();
  const alert = app.find(app.render(), node => node.type === "Alert" && node.props.type === "success");
  await app.find(alert.props.description, node => node.type === "Button").props.onClick();
  assert.equal(app.getSession().result, null);
  assert.equal(copied.length, 0);
  assert.match(app.error().props.title, /商品管理页核对/);
});

test("focus, BFCache and visibility restoration check without a paid search", async () => {
  for (const event of ["focus", "pageshow", "visibilitychange"]) {
    const app = workspace({ initialResult: response() });
    app.mountEffects();
    await new Promise(resolve => setImmediate(resolve));
    app.setCatalogVersion({ ...response(), catalog_fingerprint: "e".repeat(64) });
    app.emit(event);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.getSession().result, null);
    assert.equal(app.getSession().confirmedId, null);
    assert.equal(app.requests.length, 0);
    assert.equal(app.versionRequests.length, 2);
  }
});

test("restoring an already stale root session clears it on mount", async () => {
  const app = workspace({ initialResult: response() });
  app.setCatalogVersion({ ...response(), catalog_fingerprint: "e".repeat(64) });
  app.mountEffects();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.getSession().result, null);
  assert.equal(app.requests.length, 0);
});

test("a cutover epoch clears a session even when the legacy snapshot fingerprint is unchanged", async () => {
  const app = workspace({ initialResult: response() });
  app.setCatalogVersion({ ...response(), catalog_revision: "f".repeat(64) });
  app.mountEffects();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.getSession().result, null);
  assert.equal(app.requests.length, 0);
});

test("malformed version responses are unavailable, never a clipboard fallback", async () => {
  for (const invalid of [null, {}, { ...response(), catalog_fingerprint: null }]) {
    const app = workspace({ initialResult: response() });
    app.setCatalogVersion(invalid);
    app.mountEffects();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(app.getSession().result, null);
    assert.match(app.error().props.title, /无法核验/);
    assert.equal(app.requests.length, 0);
  }
});

test("request initialization failures show an error, unlock the button and allow manual retry", async () => {
  const app = workspace({ failInitialization: true });
  await app.button().props.onClick();
  assert.equal(app.requests.length, 0);
  assert.equal(app.states[0], "error");
  assert.match(app.error().props.title, /识别失败/);
  assert.equal(app.getSession().activeRequestId, null);
  assert.equal(app.button().props.disabled, false);
  const pending = app.button().props.onClick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.requests.length, 1);
  app.release(); await pending;
  assert.equal(app.states[0], "success");
});

test("request IDs are local and unique without access to Web Crypto", () => {
  const app = workspace({ crypto: new Proxy({}, { get() { throw new Error("Web Crypto must not be used"); } }) });
  const ids = Array.from({ length: 1000 }, () => app.sessionTools.createSearchRequestId());
  assert.equal(new Set(ids).size, ids.length);
});

test("confirmation displays original code but feedback and clipboard keep the own product identity", async () => {
  const candidate = id => ({ product: { id, product_key: `aliyun-inventory-production:${id}`,
    source_id: "aliyun-inventory-production", product_code: `OWN-${id}`, original_product_code: "SHARED-2677",
    image_path: null, category_name: null, warehouse_name: "Mock", size: "10cm", unit: "pcs",
    is_active: true, source_present: true, synced_at: "2026-10-12T00:00:00Z", packagings: [] }, similarity: 1 - id / 10 });
  const initialResult = { ...response(), candidates: [candidate(1), candidate(2)] };
  const copied = [];
  const app = workspace({ initialResult, clipboard: { writeText: async value => { copied.push(value); } } });
  const card = app.find(app.render(), node => node.type === "./SearchResultCard");
  await card.props.onConfirm();
  assert.deepEqual(JSON.parse(app.requests[0].init.body), { query_id: initialResult.query_id, selected_product_id: 1, no_match: false });
  assert.equal(app.getSession().confirmedId, 1);
  const alert = app.find(app.render(), node => node.type === "Alert" && node.props.type === "success");
  assert.equal(alert.props.title, "已确认商品：OWN-1");
  const original = app.find(alert.props.description, node => node.props.className === "ai-search-confirmed-original-code");
  assert.equal(original.props.children.join(""), "原货号：SHARED-2677");
  await app.find(alert.props.description, node => node.type === "Button").props.onClick();
  assert.deepEqual(copied, ["OWN-1"]);
  const legacy = structuredClone(initialResult);
  delete legacy.candidates[0].product.original_product_code;
  const legacyApp = workspace({ initialResult: legacy, confirmedId: 1 });
  const legacyAlert = legacyApp.find(legacyApp.render(), node => node.type === "Alert" && node.props.type === "success");
  assert.ok(!legacyApp.find(legacyAlert.props.description, node => node.props.className === "ai-search-confirmed-original-code"));
  await legacyApp.find(legacyAlert.props.description, node => node.type === "Button").props.onClick();
  const fallbackAlert = legacyApp.find(legacyApp.render(), node => node.type === "Alert" && node.props.type === "success");
  assert.equal(legacyApp.find(fallbackAlert.props.description, node => node.type === "Input").props.value, "OWN-1");
});
