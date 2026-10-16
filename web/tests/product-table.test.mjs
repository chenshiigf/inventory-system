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
const exports = {};
const code = ts.transpileModule(readFileSync(path.join(root, "components/inventory/ProductTable.tsx"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
const require = name => {
  if (name === "antd") return Object.fromEntries(["Button", "Dropdown", "Popover", "Space", "Table", "Tag"].map(key => [key, key]));
  if (name === "next/link") return { default: "Link" };
  if (name === "@/components/inventory/ProductImage") return { default: "ProductImage" };
  if (name === "@/components/inventory/ProductRemarkEditor") return { default: "ProductRemarkEditor" };
  return requirePackage(name);
};
vm.runInThisContext(`(function(exports, require) {${code}\n})`)(exports, require);
const text = node => node == null || typeof node === "boolean" ? ""
  : typeof node !== "object" ? String(node)
  : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
function render(product) {
  return exports.default({ products: [product], total: 1, loading: false, batchMode: false,
    selectedIds: new Set(), currentPage: 1, pageSize: 100 }).props.columns;
}
const product = quantities => ({ id: 1, unit: "set", totalCartonCount: 37,
  packagings: quantities.map((packingQty, i) => ({ id: i + 1, packingQty, sortOrder: i, cartonCount: 10 + i })) });

test("table keeps single, empty and unknown packing quantities unchanged", () => {
  for (const [quantities, expected] of [[[18], "18"], [[], "—"], [[null], "—"]]) {
    const p = product(quantities);
    const column = render(p).find(item => item.key === "packagings");
    assert.equal(text(column.render(undefined, p)), expected);
  }
});

test("table shows only sorted packing numbers and separators, retaining data and detail popover", () => {
  for (const quantities of [[18, 24], [18, 24, 36], [18, null, 36]]) {
    const p = product(quantities);
    p.packagings.reverse();
    const original = structuredClone(p);
    const columns = render(p);
    const packing = columns.find(item => item.key === "packagings").render(undefined, p);
    assert.equal(text(packing), quantities.map(value => value ?? "—").join(" / "));
    assert.doesNotMatch(text(packing), /\d+种包装/);
    assert.equal(packing.type, "Popover");
    assert.equal(packing.props.title, "包装规格");
    assert.match(text(packing.props.content), /set\/箱 × 10箱/);
    assert.equal(text(columns.find(item => item.key === "totalCartonCount").render(undefined, p)), "37箱");
    assert.deepEqual(p, original);
  }
});
