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
  if (modules.has(relative)) return modules.get(relative).exports;
  const compiledModule = { exports: {} };
  modules.set(relative, compiledModule);
  const code = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const require = name => {
    if (name === "antd") return Object.fromEntries(["Button", "Card", "Tag", "Collapse", "Modal", "Spin", "Alert"].map(key => [key, key]));
    if (name === "@/components/inventory/ProductImage") return { default: "ProductImage" };
    if (name.startsWith("@/")) return load(`${name.slice(2)}.ts`);
    return requirePackage(name);
  };
  vm.runInThisContext(`(function(exports, require, module) {${code}\n})`)(compiledModule.exports, require, compiledModule);
  return compiledModule.exports;
}
const SearchResultCard = load("components/ai-search/SearchResultCard.tsx").default;
const SimilarProducts = load("components/ai-search/SimilarProducts.tsx").default;
const ProductReadonlyModal = load("components/ai-search/ProductReadonlyModal.tsx").default;
const text = node => node == null || typeof node === "boolean" ? ""
  : typeof node !== "object" ? String(node)
  : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const product = (originalProductCode, id = 1) => ({ productId: id, productKey: `source:${id}`,
  productCode: `OWN-${id}`, originalProductCode, productName: "Mock product", image: null,
  size: "10cm", packagings: [], warehouse: "Mock warehouse", imageSyncedAt: null, active: true });
const noop = () => {};

test("main match, Top5 alternatives and readonly details show optional original codes without empty rows", () => {
  for (const original of [undefined, null, "2677-35", "LONG-".repeat(20)]) {
    const p = product(original);
    const main = SearchResultCard({ product: p, confirmed: false, confirming: false, disabled: false,
      onConfirm: noop, onRestart: noop, onDetails: noop });
    const modal = ProductReadonlyModal({ product: p, open: true, loading: false, error: null,
      confirming: false, confirmed: false, disabled: false, onClose: noop, onConfirm: noop });
    const similar = SimilarProducts({ products: [{ product: p, score: .8 }], disabled: false, onDetails: noop }).props.items[0].children;
    for (const node of [main, modal, similar]) {
      assert.match(text(node), /自家货号[：]?OWN-1/);
      assert.equal(text(node).includes("原货号"), Boolean(original));
      if (original) assert.ok(text(node).includes(original), "long original code is retained for wrapping");
    }
  }
});

test("identical original codes retain separate alternative cards and detail actions", () => {
  const products = [product("SHARED-2677", 1), product("SHARED-2677", 2)];
  const selected = [];
  const list = SimilarProducts({ products: products.map(p => ({ product: p, score: .8 })),
    disabled: false, onDetails: p => selected.push(p) }).props.items[0].children;
  const cards = list.props.children;
  assert.equal(cards.length, 2);
  assert.notEqual(cards[0].key, cards[1].key);
  cards.forEach(card => card.props.children[2].props.onClick());
  assert.deepEqual(selected, products);
});
