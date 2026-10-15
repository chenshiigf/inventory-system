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
function workspace({ crypto = {}, failInitialization = false, crop = null } = {}) {
  const requests = [], states = [], refs = [], modules = new Map();
  let stateIndex = 0, refIndex = 0, contextValue, resolveSearch;
  let initializationFailures = failInitialization ? 1 : 0;
  const jsx = (type, props) => ({ type, props: props ?? {} });
  const context = vm.createContext({
    crypto, isSecureContext: false, File, FormData, Response, DOMException, AbortSignal,
    AbortController: class {
      constructor() {
        if (initializationFailures-- > 0) throw new Error("Request initialization failed");
        return new AbortController();
      }
    },
    window: { location: { protocol: "http:", origin: "http://inventory.example:8082" }, matchMedia: () => ({ matches: false }) },
    process: { env: { NODE_ENV: "production" } },
    fetch: async (url, init) => {
      requests.push({ url, init });
      return new Promise(resolve => { resolveSearch = () => resolve(Response.json(response())); });
    },
  });
  const ui = Object.fromEntries(["Button", "Card", "Alert", "Empty", "Image", "Input", "Spin"].map(name => [name, name]));
  ui.App = { useApp: () => ({ message: { success() {}, warning() {}, error() {} } }) };
  const react = {
    useEffect() {},
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
  const Component = load("components/ai-search/AISearchWorkspace.tsx").default;
  function render() {
    stateIndex = refIndex = 0;
    contextValue = { session, replacePhoto() {}, setCrop() {}, confirmSelection() {},
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
  return { context, requests, sessionTools, render, button, states, getSession: () => session,
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
