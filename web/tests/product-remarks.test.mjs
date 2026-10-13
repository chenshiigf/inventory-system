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
  const compiledModule = { exports: {} }; modules.set(filename, compiledModule);
  const code = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const localRequire = name => name.startsWith("@/") ? load(`${name.slice(2)}.ts`) : requirePackage(name);
  vm.runInThisContext(`(function(exports, require, module) {${code}\n})`)(compiledModule.exports, localRequire, compiledModule);
  return compiledModule.exports;
}
const remarks = load("lib/product-remarks.ts");
const originalProduct = { id: 41, productCode: "TEST-41", remark: "旧备注", totalCartonCount: 121,
  packagings: [{ id: 81, packingQty: 24, cartonCount: 121, sortOrder: 0 }] };

test("add, modify and clear send exactly one remark-only PATCH through the existing API", async () => {
  const previous = globalThis.fetch;
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
  let calls = 0;
  try {
    for (const [draft, expected] of [["新增备注", "新增备注"], [" 修改备注 ", "修改备注"], ["", null], [" \n ", null]]) {
      globalThis.fetch = async (url, init) => {
        calls += 1;
        assert.equal(url, `${base.replace(/\/+$/, "")}/api/products/41`);
        assert.equal(init.method, "PATCH");
        assert.deepEqual(JSON.parse(init.body), { remark: expected });
        assert.equal(init.headers["Content-Type"], "application/json");
        return Response.json({ id: 41, remark: expected });
      };
      assert.equal(await remarks.saveProductRemark(41, draft), expected ?? "");
    }
    assert.equal(calls, 4);
  } finally { globalThis.fetch = previous; }
});

test("401, 403, server and network failures keep typed input; no automatic retry or optimistic change", async () => {
  const previous = globalThis.fetch;
  try {
    for (const status of [401, 403, 500, "network"]) {
      let calls = 0;
      let state = remarks.remarkEditorReducer(remarks.initialRemarkEditor(), { type: "open", remark: "旧备注" });
      state = remarks.remarkEditorReducer(state, { type: "edit", draft: "未保存的输入" });
      state = remarks.remarkEditorReducer(state, { type: "save" });
      globalThis.fetch = async () => { calls += 1; if (status === "network") throw new Error("offline"); return Response.json({ detail: "保存失败" }, { status }); };
      await assert.rejects(remarks.saveProductRemark(41, state.draft), error => {
        state = remarks.remarkEditorReducer(state, { type: "failed", error: remarks.remarkErrorMessage(error) });
        return true;
      });
      assert.equal(calls, 1);
      assert.equal(state.open, true); assert.equal(state.saving, false);
      assert.equal(state.draft, "未保存的输入"); assert.ok(state.error);
      if (status === 401) assert.match(state.error, /登录/);
      if (status === 403) assert.match(state.error, /无权编辑/);
      assert.equal(originalProduct.remark, "旧备注");
    }
  } finally { globalThis.fetch = previous; }
});

test("cancel discards only the draft; pending save blocks dismissal, edits and duplicate starts", () => {
  let state = remarks.remarkEditorReducer(remarks.initialRemarkEditor(), { type: "open", remark: "旧备注" });
  state = remarks.remarkEditorReducer(state, { type: "edit", draft: "新增" });
  assert.deepEqual(remarks.remarkEditorReducer(state, { type: "cancel" }), remarks.initialRemarkEditor());
  state = remarks.remarkEditorReducer(state, { type: "save" });
  for (const action of [{ type: "cancel" }, { type: "save" }, { type: "edit", draft: "错位" }, { type: "open", remark: "其他" }]) {
    assert.equal(remarks.remarkEditorReducer(state, action), state);
  }
  assert.deepEqual(remarks.remarkEditorReducer(state, { type: "saved" }), remarks.initialRemarkEditor());
});

test("successful list update changes only the matching remark, preserves IDs, packaging, stock and row order", () => {
  const other = { ...originalProduct, id: 42 };
  const result = remarks.applyRemarkToProducts([originalProduct, other], 41, "已保存");
  assert.deepEqual(result.map(product => product.id), [41, 42]);
  assert.equal(result[0].remark, "已保存"); assert.equal(result[1], other);
  assert.equal(result[0].packagings, originalProduct.packagings);
  assert.equal(result[0].totalCartonCount, 121);
  assert.deepEqual({ ...result[0], remark: "旧备注" }, originalProduct);
});

test("an older in-flight page/filter response cannot overwrite a saved note; fresh responses remain authoritative", () => {
  const updates = new Map([[41, { revision: 2, remark: "刚保存" }]]);
  assert.equal(remarks.applyRemarkUpdatesToResponse([originalProduct], updates, 1)[0].remark, "刚保存");
  assert.equal(remarks.applyRemarkUpdatesToResponse([originalProduct], updates, 2)[0], originalProduct);
  assert.equal(remarks.applyRemarkUpdatesToResponse([{ ...originalProduct, id: 42 }], updates, 1)[0].remark, "旧备注");
});

test("invalid length never calls API and mismatched response ID is rejected", async () => {
  const previous = globalThis.fetch;
  let calls = 0;
  try {
    globalThis.fetch = async () => { calls += 1; return Response.json({ id: 42, remark: "wrong" }); };
    await assert.rejects(remarks.saveProductRemark(41, "x".repeat(2001)), /2000/);
    assert.equal(calls, 0);
    await assert.rejects(remarks.saveProductRemark(41, "备注"), /无法核对/);
    assert.equal(calls, 1);
  } finally { globalThis.fetch = previous; }
});
