import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
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
  const compiledModule = { exports: {} }; modules.set(filename, compiledModule);
  const code = ts.transpileModule(readFileSync(filename, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  const localRequire = (name) => {
    if (!name.startsWith("@/")) return requirePackage(name);
    const file = `${name.slice(2)}.ts`;
    return load(existsSync(path.join(root, file)) ? file : `${name.slice(2)}.tsx`);
  };
  vm.runInThisContext(`(function(exports, require, module) {${code}\n})`, { filename })(compiledModule.exports, localRequire, compiledModule);
  return compiledModule.exports;
}
const { initialPreviewSession, previewSessionReducer: reduce, selectedPreviewRegion, validPreviewCrop } = load("lib/ai-search-preview-session.ts");
const { searchPreviewMock, confirmPreviewMock, previewProducts } = load("lib/mock/ai-search-preview.ts");
const crop = [0.05, 0.1, 0.2, 0.5];
function photo(id = "photo-one") {
  return reduce(initialPreviewSession(), { type: "photo", id, file: new File(["test"], "scene.png", { type: "image/png" }), image: "blob:test", width: 1500, height: 1000 });
}
function add(state, id) { return reduce(state, { type: "add", id, crop }); }
function target(state, id, requestId = `mock-${id}`) {
  const region = id === "whole" ? state.whole : state.regions.find((r) => r.id === id);
  return { sessionId: state.id, regionId: id, revision: region.revision, requestId };
}
function result(queryId, first = 0) {
  return { queryId, source: "mock", candidates: previewProducts.map((_, i) => ({ product: previewProducts[(first + i) % 5], score: 0.9 - i * 0.08 })) };
}
function searched(state, id, first = 0) {
  const binding = target(state, id, `mock-${id}-revision-${id === "whole" ? state.whole.revision : state.regions.find((r) => r.id === id).revision}`);
  state = reduce(state, { type: "start", ...binding });
  return reduce(state, { type: "success", ...binding, result: result(binding.requestId, first) });
}
function confirmed(state, id, key) {
  const binding = target(state, id);
  const region = id === "whole" ? state.whole : state.regions.find((r) => r.id === id);
  return reduce(state, { type: "confirm", ...binding, queryId: region.result.queryId, productKey: key ?? region.result.candidates[0].product.productKey });
}

test("single mode searches without crop and is independent from multiple-region selection", () => {
  let state = searched(photo(), "whole");
  assert.equal(selectedPreviewRegion(state).crop, null);
  state = confirmed(state, "whole");
  state = reduce(add(state, "one"), { type: "mode", mode: "multiple" });
  assert.equal(selectedPreviewRegion(state).id, "one");
  state = reduce(state, { type: "mode", mode: "single" });
  assert.equal(selectedPreviewRegion(state).status, "confirmed");
  assert.equal(state.confirmed[0].regionNumber, 0);
});

test("three crops retain separate Top5, query IDs and three confirmed products when selected", () => {
  let state = reduce(photo(), { type: "mode", mode: "multiple" });
  for (let i = 0; i < 3; i++) {
    state = confirmed(searched(add(state, `region-${i}`), `region-${i}`, i), `region-${i}`);
  }
  assert.equal(state.confirmed.length, 3);
  assert.equal(new Set(state.confirmed.map((i) => i.product.productKey)).size, 3);
  assert.equal(new Set(state.regions.map((r) => r.result.queryId)).size, 3);
  for (let i = 0; i < 3; i++) {
    state = reduce(state, { type: "select", id: `region-${i}` });
    assert.equal(selectedPreviewRegion(state).status, "confirmed");
    assert.equal(selectedPreviewRegion(state).result.candidates.length, 5);
  }
  assert.equal(state.searchCount, 3);
  const before = state;
  state = reduce(state, { type: "start", ...target(state, "region-0", "duplicate") });
  assert.equal(state, before, "viewing existing results cannot start another query");
});

test("crop revision invalidates old results, preserves attempts and ignores late replies", () => {
  let state = searched(add(photo(), "one"), "one");
  const old = target(state, "one", state.regions[0].result.queryId);
  state = reduce(state, { type: "revise", id: "one", crop: [0.4, 0.1, 0.3, 0.6] });
  assert.equal(state.regions[0].revision, 1);
  assert.equal(state.regions[0].result, null);
  assert.equal(state.regions[0].attempts.length, 1);
  assert.deepEqual(state.regions[0].attempts[0].crop, crop);
  assert.equal(reduce(state, { type: "success", ...old, result: result(old.requestId) }), state);
  const next = target(state, "one", "new-query");
  state = reduce(state, { type: "start", ...next });
  const interrupted = reduce(state, { type: "revise", id: "one", crop });
  assert.equal(reduce(interrupted, { type: "success", ...next, result: result(next.requestId) }), interrupted);
});

test("new photo, deleted region and wrong request ID reject stale responses and feedback", () => {
  let state = add(photo(), "one");
  const binding = target(state, "one");
  state = reduce(state, { type: "start", ...binding });
  assert.equal(reduce(state, { type: "success", ...binding, requestId: "wrong", result: result("wrong") }), state);
  const deleted = reduce(state, { type: "delete", id: "one" });
  assert.equal(reduce(deleted, { type: "success", ...binding, result: result(binding.requestId) }), deleted);
  const replacement = reduce(state, { type: "photo", id: "photo-two", file: new File(["next"], "next.png"), image: "blob:next", width: 1000, height: 1000 });
  assert.equal(reduce(replacement, { type: "success", ...binding, result: result(binding.requestId) }), replacement);
  assert.equal(replacement.regions.length, 0);
  assert.equal(replacement.confirmed.length, 0);
  state = searched(add(photo(), "one"), "one");
  assert.equal(reduce(state, { type: "confirm", ...target(state, "one"), queryId: "wrong", productKey: "mock:1" }), state);
});

test("reply after selection changes updates only its own region, not the visible region", () => {
  let state = reduce(add(add(photo(), "one"), "two"), { type: "mode", mode: "multiple" });
  const binding = target(state, "one");
  state = reduce(state, { type: "start", ...binding });
  state = reduce(state, { type: "select", id: "two" });
  state = reduce(state, { type: "success", ...binding, result: result(binding.requestId) });
  assert.equal(selectedPreviewRegion(state).id, "two");
  assert.equal(selectedPreviewRegion(state).result, null);
  assert.equal(state.regions[0].result.queryId, binding.requestId);
});

test("same product in different regions stays distinct; removing one never modifies another", () => {
  let state = confirmed(searched(add(photo(), "one"), "one"), "one");
  state = confirmed(searched(add(state, "two"), "two"), "two");
  assert.equal(state.confirmed.length, 2);
  assert.equal(state.confirmed[0].product.productKey, state.confirmed[1].product.productKey);
  assert.notEqual(state.confirmed[0].id, state.confirmed[1].id);
  const other = state.regions[1];
  assert.equal(reduce(state, { type: "delete", id: "one" }), state);
  state = reduce(state, { type: "remove", id: state.confirmed[0].id });
  assert.equal(state.regions[0].status, "searched");
  assert.equal(state.regions[1], other);
  state = reduce(state, { type: "delete", id: "one" });
  assert.equal(state.regions[0], other);
  assert.equal(state.confirmed.length, 1);
});

test("different candidate confirmation belongs to its query and replaces only that region", () => {
  let state = confirmed(searched(add(photo(), "one"), "one"), "one");
  state = confirmed(searched(add(state, "two"), "two"), "two");
  state = confirmed(state, "one", "mock:4");
  assert.equal(state.confirmed.length, 2);
  assert.equal(state.confirmed.find((i) => i.regionId === "one").product.productKey, "mock:4");
  assert.equal(state.confirmed.find((i) => i.regionId === "two").product.productKey, "mock:1");
});

test("ten-region cap, source bounds and minimum pixel crop are enforced with stable numbers", () => {
  let state = photo();
  for (let i = 1; i <= 10; i++) state = add(state, `r${i}`);
  assert.equal(add(state, "overflow"), state);
  assert.equal(validPreviewCrop([0, 0, 0.001, 0.001], 1500, 1000), false);
  assert.equal(validPreviewCrop([0, 0, 2, 1], 1500, 1000), false);
  assert.equal(validPreviewCrop([NaN, 0, 0.2, 0.2], 1500, 1000), false);
  state = add(reduce(state, { type: "delete", id: "r1" }), "r11");
  assert.equal(state.regions.at(-1).number, 11, "region numbers do not shift after deletion");
});

test("failure is recorded, has no automatic retry and blocks concurrent starts", () => {
  let state = add(add(photo(), "one"), "two");
  const binding = target(state, "one");
  state = reduce(state, { type: "start", ...binding });
  assert.equal(reduce(state, { type: "start", ...target(state, "two") }), state);
  state = reduce(state, { type: "failure", ...binding, error: "mock failure" });
  assert.equal(state.searchCount, 1);
  assert.equal(state.regions[0].status, "failed");
  assert.equal(state.regions[0].attempts[0].error, "mock failure");
  state = reduce(state, { type: "start", ...target(state, "one", "manual-retry") });
  assert.equal(state.searchCount, 2);
});

test("Mock search and confirmation work without any network or real product identities", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = () => { throw new Error("unexpected HTTP request"); };
  try {
    const found = await searchPreviewMock("mock-explicit", 2, false);
    assert.equal(found.source, "mock");
    assert.equal(found.candidates.length, 5);
    assert.equal(found.candidates[0].product.productCode, "DEMO-002");
    assert.ok(found.candidates.every((c) => c.product.productKey.startsWith("mock:") && c.product.image.startsWith("data:image/")));
    assert.deepEqual(await confirmPreviewMock(found.queryId), { saved: true, queryId: found.queryId });
    await assert.rejects(searchPreviewMock("mock-fail", 1, true), /模拟识别失败/);
  } finally { globalThis.fetch = original; }
});

test("preview page denies production before loading any Mock workspace", async () => {
  const previous = process.env.NODE_ENV;
  try {
    const page = load("app/ai-search-preview/page.tsx").default;
    for (const mode of ["production", "test"]) {
      process.env.NODE_ENV = mode;
      await assert.rejects(page(), /NEXT_HTTP_ERROR_FALLBACK;404/);
    }
  } finally { if (previous === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = previous; }
});

test("multi-region and indexing test page denies production without importing its workspace", async () => {
  const previous = process.env.NODE_ENV;
  try {
    const page = load("app/visual-search/page.tsx").default;
    for (const mode of ["production", "test"]) {
      process.env.NODE_ENV = mode;
      await assert.rejects(page(), /NEXT_HTTP_ERROR_FALLBACK;404/);
    }
    assert.equal(modules.has(path.join(root, "components/visual-search/VisualSearchWorkspace.tsx")), false);
  } finally { if (previous === undefined) delete process.env.NODE_ENV; else process.env.NODE_ENV = previous; }
});
