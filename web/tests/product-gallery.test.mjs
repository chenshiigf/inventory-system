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
function findAll(node, predicate) {
  if (!node || typeof node !== "object") return [];
  if (Array.isArray(node)) return node.flatMap(child => findAll(child, predicate));
  return [...(predicate(node) ? [node] : []), ...findAll(node.props?.children, predicate)];
}
const text = node => node === null || node === undefined || typeof node === "boolean" ? ""
  : typeof node !== "object" ? String(node)
  : Array.isArray(node) ? node.map(text).join("") : text(node.props?.children);
const product = (quantities = [18], unit = "pcs") => ({
  id: 41, productCode: "01-01-001", isActive: true, size: "SIZE-MUST-NOT-SHOW",
  price: "987.65", unit, totalCartonCount: 123, imagePath: "/mock/main.jpg", thumbnailPath: "/mock/thumb.jpg",
  packagings: quantities.map((packingQty, i) => ({ id: i + 1, packingQty, sortOrder: i, cartonCount: 5 })),
});
function gallery(products, overrides = {}) {
  const events = [];
  const exports = {};
  const code = ts.transpileModule(readFileSync(path.join(root, "components/inventory/ProductGallery.tsx"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const require = name => {
    if (name === "antd") return Object.fromEntries(["Button", "Checkbox", "Empty", "Pagination", "Skeleton", "Tag"].map(key => [key, key]));
    if (name === "next/navigation") return { useRouter: () => ({ push: href => events.push(["navigate", href]) }) };
    if (name === "@/components/inventory/ProductImage") return { default: "ProductImage" };
    return requirePackage(name);
  };
  vm.runInThisContext(`(function(exports, require) {${code}\n})`)(exports, require);
  const props = {
    products, total: 500, loading: false, batchMode: false, selectedIds: new Set(),
    currentPage: 2, pageSize: 100, getProductDetailHref: id => `/products/detail?id=${id}`,
    onBeforeProductDetail: () => events.push(["before-detail"]),
    onProductSelectionChange: (p, selected) => events.push(["select", p.id, selected]),
    onStockIn: p => events.push(["in", p.id]), onStockOut: p => events.push(["out", p.id]),
    onPageChange: (page, size) => events.push(["page", page, size]), ...overrides,
  };
  const tree = exports.default(props);
  const nodes = selector => findAll(tree, node => node.props?.className === selector);
  return { tree, events, nodes, all: predicate => findAll(tree, predicate) };
}

test("single packing shows the actual pcs/set unit; missing units are never guessed", () => {
  for (const [unit, expected] of [["pcs", "18 pcs/箱"], ["set", "18 set/箱"], [null, "18 —/箱"]]) {
    const card = gallery([product([18], unit)]);
    const packing = card.nodes("product-gallery-packing")[0];
    assert.equal(text(packing), expected); assert.equal(packing.props.title, expected);
  }
});

test("multiple packings follow sortOrder without changing product data", () => {
  const p = product([24, 18], "set");
  p.packagings[0].sortOrder = 1; p.packagings[1].sortOrder = 0;
  const original = structuredClone(p);
  assert.equal(text(gallery([p]).nodes("product-gallery-packing")[0]), "18 / 24 set/箱");
  assert.deepEqual(p, original);
});

test("empty/null packings stay unknown and mixed missing quantities retain their position", () => {
  for (const quantities of [[], [null], [null, null]]) {
    assert.equal(text(gallery([product(quantities)]).nodes("product-gallery-packing")[0]), "—");
  }
  assert.equal(text(gallery([product([18, null, 24])]).nodes("product-gallery-packing")[0]), "18 / — / 24 pcs/箱");
});

test("long code/specification has full hover text; cards show no size, price or invented original code", () => {
  const p = product(Array.from({ length: 30 }, (_, i) => i + 1));
  p.productCode = "LONG-CODE-".repeat(30);
  const card = gallery([p]);
  const code = card.nodes("product-gallery-code")[0], packing = card.nodes("product-gallery-packing")[0];
  assert.equal(code.props.title, p.productCode); assert.equal(text(code), p.productCode);
  assert.equal(packing.props.title, text(packing));
  const content = text(card.nodes("product-gallery-content")[0]);
  assert.doesNotMatch(content, /SIZE-MUST-NOT-SHOW|987\.65|25569/);
  assert.equal(card.nodes("product-gallery-code-secondary").length, 0);
  p.productCode = null;
  assert.equal(text(gallery([p]).nodes("product-gallery-code")[0]), "—");
});

test("stock and lazy thumbnail props remain unchanged; inactive cards keep their packing row", () => {
  const p = { ...product(), totalCartonCount: 0, isActive: false };
  const card = gallery([p]);
  assert.equal(text(card.nodes("product-gallery-stock")[0]), "0箱");
  const image = card.all(node => node.type === "ProductImage")[0];
  assert.equal(image.props.thumbnailPath, p.thumbnailPath); assert.equal(image.props.imagePath, p.imagePath);
  assert.equal(image.props.loading, "lazy"); assert.equal(image.props.enablePreview, false);
  assert.equal(image.props.width, 320); assert.equal(image.props.height, 220);
  assert.equal(card.nodes("product-gallery-actions").length, 0);
  assert.equal(text(card.nodes("product-gallery-packing")[0]), "18 pcs/箱");
});

test("details, stock buttons, keyboard and batch checkbox retain their original event paths", () => {
  const card = gallery([product()]);
  const article = card.all(node => node.type === "article")[0];
  article.props.onClick();
  assert.deepEqual(card.events, [["before-detail"], ["navigate", "/products/detail?id=41"]]);
  let stopped = 0, prevented = 0;
  const event = { key: "Enter", stopPropagation: () => stopped++, preventDefault: () => prevented++ };
  const buttons = card.all(node => node.type === "Button");
  buttons[0].props.onClick(event); buttons[1].props.onClick(event);
  buttons[0].props.onKeyDown(event);
  assert.equal(stopped, 3); assert.deepEqual(card.events.slice(-2), [["in", 41], ["out", 41]]);
  article.props.onKeyDown(event); assert.equal(prevented, 1);
  const batch = gallery([product()], { batchMode: true, selectedIds: new Set([41]) });
  const batchArticle = batch.all(node => node.type === "article")[0];
  assert.equal(batchArticle.props.role, "checkbox"); assert.equal(batchArticle.props["aria-checked"], true);
  batchArticle.props.onClick();
  batch.all(node => node.type === "Checkbox")[0].props.onChange();
  assert.deepEqual(batch.events, [["select", 41, false], ["select", 41, false]]);
});

test("gallery paging remains controlled with 20/50/100 choices", () => {
  const card = gallery([product()]);
  const paging = card.all(node => node.type === "Pagination")[0].props;
  assert.equal(paging.current, 2); assert.equal(paging.pageSize, 100); assert.equal(paging.total, 500);
  assert.deepEqual(paging.pageSizeOptions, [20, 50, 100]);
  paging.onChange(3, 50); assert.deepEqual(card.events, [["page", 3, 50]]);
});
