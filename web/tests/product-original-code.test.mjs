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
function load(relative, mocks = {}) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInThisContext(`(function(exports, require) {${code}\n})`)(exports, name => mocks[name] ?? requirePackage(name));
  return exports;
}
function find(node, predicate) {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) return node.map(child => find(child, predicate)).find(Boolean);
  return predicate(node) ? node : find(node.props?.children, predicate);
}
const record = original => ({
  id: 41, product_code: "02-01-220", original_product_code: original, is_active: true,
  category_id: 2, warehouse_id: 3, image_path: null, thumbnail_path: null,
  size: "20x30", unit: "pcs", price: "1.00", remark: null,
  packagings: [{ id: 410, packing_qty: 24, carton_count: 20, sort_order: 0 }], total_carton_count: 20,
});
const { toInventoryProduct } = load("lib/inventory-products.ts");

function editor(product) {
  const events = [], form = { submit() { events.push("submit"); } };
  const Form = Object.assign(function Form() {}, {
    useForm: () => [form], useWatch: () => "pcs", Item: "FormItem", List: "FormList",
  });
  const messageApi = { error: value => events.push(["error", value]) };
  const ui = Object.fromEntries(["Alert", "Cascader", "Button", "Dropdown", "Input", "InputNumber", "Modal", "Select", "Upload"].map(key => [key, key]));
  ui.Form = Form; ui.message = { useMessage: () => [messageApi, null] };
  const Component = load("components/inventory/ProductEditorModal.tsx", {
    react: { useState: initial => [initial, () => {}] }, antd: ui,
    "@ant-design/icons": {},
    "@/lib/categories": { getCategoryPath: () => [1, 2], hasSecondLevelCategories: () => true, toCategoryOptions: () => [] },
    "@/lib/api/product-images": {}, "@/components/inventory/ProductImage": { default: "ProductImage" },
  }).default;
  const tree = Component({ product, categories: [], warehouses: [], defaultWarehouseId: 3,
    onSave: async values => events.push(["save", values]), onCancel: () => events.push("cancel") });
  return { events, tree, form: find(tree, node => node.type === Form),
    field: find(tree, node => node.type === "FormItem" && node.props.name === "originalProductCode"),
    modal: find(tree, node => node.type === "Modal") };
}

test("API mapping preserves nullable original code without replacing product identity or packaging", () => {
  for (const original of [null, "2677-35", "长".repeat(100)]) {
    const source = record(original), before = structuredClone(source), product = toInventoryProduct(source);
    assert.equal(product.originalProductCode, original);
    assert.equal(product.productCode, source.product_code);
    assert.equal(product.totalCartonCount, 20); assert.equal(product.packagings[0].id, 410);
    assert.deepEqual(source, before);
  }
});

test("new and edit forms register an optional clearable original code input and restore the initial value", () => {
  for (const product of [undefined, toInventoryProduct(record(null)), toInventoryProduct(record("2677-35"))]) {
    const rendered = editor(product);
    assert.equal(rendered.form.props.initialValues.originalProductCode, product?.originalProductCode ?? "");
    assert.equal(rendered.field.props.label, "原货号");
    assert.equal(rendered.field.props.rules, undefined);
    assert.equal(rendered.field.props.children.props.allowClear, true);
    assert.equal(rendered.field.props.children.props.maxLength, 100);
    assert.equal(rendered.modal.props.width, 1140);
    rendered.modal.props.onCancel();
    assert.deepEqual(rendered.events, ["cancel"]);
  }
});

test("actual editor submit handler trims filled/cleared original codes and retains packaging IDs", async () => {
  for (const originalProductCode of [" 2677-35 ", "   "]) {
    const product = toInventoryProduct(record("before")), rendered = editor(product);
    const values = { ...rendered.form.props.initialValues, originalProductCode };
    await rendered.form.props.onFinish(values);
    const submitted = rendered.events.find(([event]) => event === "save")[1];
    assert.equal(submitted.originalProductCode, originalProductCode.trim());
    assert.deepEqual(submitted.packagings, values.packagings);
    assert.equal(submitted.packagings[0].id, 410);
    assert.equal(product.originalProductCode, "before");
  }
});
