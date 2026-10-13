import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const requirePackage = createRequire(import.meta.url);
function load(relative) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInThisContext(`(function(exports, require) {${code}\n})`)(exports, requirePackage);
  return exports;
}
const { packagingUpdateFields } = load("lib/inventory-products.ts");
const { selectedNavigationKey } = load("lib/navigation.ts");
const original = [{ id: 41, packingQty: 24, cartonCount: 121, sortOrder: 0 }, { id: 42, packingQty: 144, cartonCount: 0, sortOrder: 1 }];

test("actual installed Ant Design form submission drops unregistered ID and preserves registered ID", () => {
  const editor = readFileSync(path.join(root, "components/inventory/ProductEditorModal.tsx"), "utf8");
  assert.match(editor, /<Form\.Item name=\{\[field\.name, "id"\]\} hidden>/);
  // Run the installed FormStore in a child: its React scheduler keeps handles
  // alive, so exit explicitly after the asynchronous validation completes.
  const output = execFileSync(process.execPath, ["-e", `
    const {createRequire}=require('module'), path=require('path');
    const ar=createRequire(require.resolve('antd'));
    const dir=path.dirname(ar.resolve('@rc-component/form'));
    const {FormStore}=require(path.join(dir,'hooks/useForm.js'));
    const {HOOK_MARK}=require(path.join(dir,'FieldContext.js'));
    const form=new FormStore(()=>{}).getForm();
    const hooks=form.getInternalHooks(HOOK_MARK);
    hooks.setInitialValues({packagings:[{id:41,packingQty:24,cartonCount:121}],remark:'new'},true);
    const field=name=>({props:{},getNamePath:()=>name,isList:()=>false,onStoreChange:()=>{}});
    hooks.registerField(field(['packagings',0,'packingQty']));
    hooks.registerField(field(['remark']));
    (async()=>{
      const before=await form.validateFields();
      hooks.registerField(field(['packagings',0,'id']));
      const after=await form.validateFields();
      console.log(JSON.stringify({before,after})); process.exit(0);
    })().catch(e=>{console.error(e);process.exit(1)});
  `], { cwd: root, encoding: "utf8", timeout: 15000 });
  const { before, after } = JSON.parse(output);
  assert.deepEqual(before.packagings, [{ packingQty: 24 }]);
  assert.deepEqual(after.packagings, [{ packingQty: 24, id: 41 }]);
  assert.deepEqual(packagingUpdateFields(original.slice(0, 1), after.packagings), {});
});

test("ordinary edits omit unchanged packaging rows regardless of stock and hidden stock fields", () => {
  const submitted = original.map(({ id, packingQty }) => ({ id, packingQty }));
  assert.deepEqual(packagingUpdateFields(original, submitted), {});
  assert.equal(Object.hasOwn(JSON.parse(JSON.stringify({ remark: "new", ...packagingUpdateFields(original, submitted) })), "packagings"), false);
  for (const file of ["InventoryWorkspace.tsx", "ProductDetailWorkspace.tsx"]) {
    assert.match(readFileSync(path.join(root, "components/inventory", file), "utf8"), /\.\.\.packagingUpdateFields\(/);
  }
});

test("real packaging edits preserve stable IDs, new rows and removal intent without stock in PATCH", () => {
  assert.deepEqual(packagingUpdateFields(original, [{ id: 41, packingQty: 48 }, { id: 42, packingQty: 144 }]), {
    packagings: [{ id: 41, packing_qty: 48 }, { id: 42, packing_qty: 144 }],
  });
  assert.deepEqual(packagingUpdateFields(original, [{ id: 41, packingQty: 24 }]), { packagings: [{ id: 41, packing_qty: 24 }] });
  assert.deepEqual(packagingUpdateFields(original, [...original, { packingQty: 72 }]).packagings.at(-1), { packing_qty: 72 });
  assert.deepEqual(packagingUpdateFields(original, [...original].reverse()).packagings.map(p => p.id), [42, 41]);
  assert.equal(packagingUpdateFields(original, [{ packingQty: 24 }, { packingQty: 144 }]).packagings.some(p => "id" in p), false);
});

test("all current menu routes match with and without static-export trailing slash", () => {
  for (const [route, key] of [["/", "dashboard"], ["/products", "inventory"], ["/inventory-movements", "inventory-movements"],
    ["/categories", "categories"], ["/warehouses", "warehouses"], ["/products/import", "product-import"],
    ["/visual-search", "visual-search"], ["/ai-search", "ai-search"]]) {
    assert.equal(selectedNavigationKey(route), key);
    assert.equal(selectedNavigationKey(route === "/" ? route : `${route}/`), key);
  }
});

test("route changes, reload/back routes, details and import precedence select exactly one correct menu", () => {
  assert.deepEqual(["/products/", "/inventory-movements/", "/products/", "/inventory-movements/"].map(p => selectedNavigationKey(p)),
    ["inventory", "inventory-movements", "inventory", "inventory-movements"]);
  assert.equal(selectedNavigationKey("/products/41/"), "inventory");
  assert.equal(selectedNavigationKey("/products/import/"), "product-import");
  assert.equal(selectedNavigationKey("/products-other/"), null);
  assert.equal(selectedNavigationKey("/inventory-movements-extra/"), null);
  assert.equal(selectedNavigationKey("/ai-search-preview/", true), "ai-search");
  assert.equal(selectedNavigationKey("/ai-search-preview/", false), null);
});
