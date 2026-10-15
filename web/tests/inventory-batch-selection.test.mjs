import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const tick = () => new Promise(resolve => setImmediate(resolve));
const product = (id, isActive = true) => ({ id, productCode: `MOCK-${id}`, isActive, totalCartonCount: 20 });
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function find(node, predicate) {
  if (!node || typeof node !== "object") return null;
  if (Array.isArray(node)) return node.map(child => find(child, predicate)).find(Boolean);
  if (predicate(node)) return node;
  return Object.values(node.props ?? {}).map(child => find(child, predicate)).find(Boolean);
}

// In-memory hook/UI harness, following ai-search-http.test.mjs. Runs the actual
// root providers, selection hook, workspace handlers and stock-out modal. It is
// not a browser renderer; APIs are mocked and unexpected fetches are forbidden.
function application(overrides = {}) {
  const modules = new Map(), instances = new Map(), dialogs = [], requests = [], messages = [];
  let active, seen, effects, tree, selection, ai, page = "inventory", search = "", modalProps = null;
  const api = {
    listProducts: async () => ({ items: [], total: 0 }),
    listCategories: async () => [], listWarehouses: async () => [],
    batchUpdateProductCategory: async () => ({ updated_count: 1 }),
    batchActivateProducts: async () => ({ updated_count: 1 }),
    batchDeactivateProducts: async () => ({ updated_count: 1 }),
    exportBatchQuote: async () => new Blob(["mock"]),
    previewBatchStockOut: async ids => ({ products: ids.map(id => ({
      product_id: id, product_code: `MOCK-${id}`, is_active: true,
      packagings: [{ id: id * 10, packing_qty: 24, carton_count: 20 }],
    })) }),
    commitBatchStockOut: async () => ({}),
    ...overrides,
  };
  const jsx = (type, props) => ({ type, props: props ?? {} });
  const slot = () => [active.hooks, active.index++];
  const changed = (a, b) => !a || !b || a.length !== b.length || a.some((v, i) => !Object.is(v, b[i]));
  const react = {
    createContext(value) { const context = { value }; context.Provider = { context }; return context; },
    useContext: context => context.value,
    useState(initial) {
      const [hooks, index] = slot();
      if (!(index in hooks)) hooks[index] = typeof initial === "function" ? initial() : initial;
      return [hooks[index], next => { hooks[index] = typeof next === "function" ? next(hooks[index]) : next; }];
    },
    useReducer(reducer, initial, initialize) {
      const [state, set] = react.useState(() => initialize ? initialize(initial) : initial);
      return [state, action => set(current => reducer(current, action))];
    },
    useRef(value) { const [hooks, index] = slot(); return hooks[index] ??= { current: value }; },
    useMemo(callback, deps) {
      const [hooks, index] = slot();
      if (!hooks[index] || changed(hooks[index].deps, deps)) hooks[index] = { deps, value: callback() };
      return hooks[index].value;
    },
    useCallback: (callback, deps) => react.useMemo(() => callback, deps),
    useEffect(callback, deps) {
      const instance = active, index = active.index++;
      if (!instance.hooks[index] || changed(instance.hooks[index].deps, deps)) {
        const previous = instance.hooks[index];
        instance.hooks[index] = { deps };
        effects.push(() => { previous?.cleanup?.(); instance.hooks[index].cleanup = callback(); });
      }
    },
  };
  const message = Object.fromEntries(["error", "warning", "success"].map(name => [name, text => messages.push([name, text])]));
  const ui = Object.fromEntries(["Alert", "Button", "Segmented", "Modal", "Table", "InputNumber", "ConfigProvider"].map(name => [name, name]));
  ui.Input = { TextArea: "TextArea" };
  ui.Typography = { Title: "Title", Text: "Text" };
  ui.App = Object.assign("AntApp", { useApp: () => ({ message, modal: { confirm: config => dialogs.push(config) } }) });
  ui.message = { ...message, useMessage: () => [message, null] };
  const router = {
    replace(href) { search = href.split("?")[1] ?? ""; },
    push(href) { page = href === "/products" ? "inventory" : "other"; },
  };
  const context = vm.createContext({
    URLSearchParams, Map, Set, AbortController, Blob, File, DOMException,
    process: { env: { NODE_ENV: "production" } },
    URL: class extends URL {
      static createObjectURL() { return "blob:mock"; }
      static revokeObjectURL() {}
    },
    window: { scrollTo() {}, sessionStorage: { getItem: () => null } },
    document: { createElement: () => ({ click() { requests.push("download"); } }) },
    fetch() { throw new Error("Unexpected network request"); },
  });
  const realComponents = new Set([
    "components/inventory/ProductBatchSelectionProvider.tsx", "components/ai-search/AISearchSessionProvider.tsx",
  ]);
  function load(relative) {
    if (modules.has(relative)) return modules.get(relative).exports;
    const compiledModule = { exports: {} }; modules.set(relative, compiledModule);
    const code = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const require = name => {
      if (name === "react") return react;
      if (name === "react/jsx-runtime") return { jsx, jsxs: jsx, Fragment: "Fragment" };
      if (name === "antd") return ui;
      if (name === "antd/locale/zh_CN") return {};
      if (name === "@ant-design/icons") return new Proxy({}, { get: (_, key) => String(key) });
      if (name === "next/navigation") return { useRouter: () => router, useSearchParams: () => new URLSearchParams(search) };
      if (["@/lib/api/products", "@/lib/api/categories", "@/lib/api/warehouses", "@/lib/api/inventory-movements"].includes(name)) {
        return new Proxy({}, { get: (_, method) => (...args) => {
          requests.push([method, ...args]);
          if (!api[method]) throw new Error(`Unexpected API: ${method}`);
          return api[method](...args);
        } });
      }
      const base = name.startsWith("@/") ? name.slice(2) : path.posix.join(path.posix.dirname(relative), name);
      const target = [".ts", ".tsx"].map(ext => base + ext).find(file => existsSync(path.join(root, file)));
      if (target && (target.startsWith("lib/") || target.startsWith("hooks/") || realComponents.has(target))) return load(target);
      if (base.startsWith("components/")) return { default: path.posix.basename(base) };
      throw new Error(`Unexpected import: ${name}`);
    };
    vm.runInContext(`(function(exports, require, module) {${code}\n})`, context)(compiledModule.exports, require, compiledModule);
    return compiledModule.exports;
  }
  const Root = load("app/providers.tsx").default;
  const Workspace = load("components/inventory/InventoryWorkspace.tsx").default;
  const StockOut = load("components/inventory/BatchStockOutModal.tsx").default;
  function Probe() {
    selection = load("components/inventory/ProductBatchSelectionProvider.tsx").useProductBatchSelectionContext();
    ai = load("components/ai-search/AISearchSessionProvider.tsx").useAISearchSession();
    return null;
  }
  function resolve(node, key) {
    if (!node || typeof node !== "object") return node;
    if (Array.isArray(node)) return node.map((child, i) => resolve(child, `${key}/${i}`));
    if (node.type?.context) {
      const ctx = node.type.context, previous = ctx.value;
      ctx.value = node.props.value;
      const result = resolve(node.props.children, `${key}/context`);
      ctx.value = previous;
      return result;
    }
    if (typeof node.type === "function") {
      const id = `${key}/${node.type.name}`;
      seen.add(id);
      const instance = instances.get(id) ?? { hooks: [] };
      instances.set(id, instance); active = instance; active.index = 0;
      return resolve(node.type(node.props), id);
    }
    return { ...node, props: { ...node.props, children: resolve(node.props.children, `${key}/children`) } };
  }
  function render() {
    seen = new Set(); effects = [];
    tree = resolve(jsx(Root, { children: [jsx(Probe), page === "inventory" ? jsx(Workspace) : "AI / other page",
      modalProps ? jsx(StockOut, modalProps) : null] }), "root");
    for (const [key, instance] of instances) {
      if (!seen.has(key)) { instance.hooks.forEach(hook => hook?.cleanup?.()); instances.delete(key); }
    }
    effects.forEach(effect => effect());
    return tree;
  }
  render();
  const node = type => find(tree, item => item.type === type);
  const app = {
    api, dialogs, requests, messages, render, node,
    selection: () => selection, ai: () => ai,
    act(callback) { const value = callback(); render(); return value; },
    navigate(next) { page = next; modalProps = null; render(); },
    select(...products) { selection.addProducts(products); render(); },
    start() { node("Button").props.onClick(); render(); },
    bar: () => node("BatchActionBar").props,
    async settle() { await tick(); render(); },
    modal() {
      modalProps = node("BatchStockOutModal").props; render();
      return node("OperationSuccessModal");
    },
    async prepareStockOut() {
      app.act(() => app.bar().onBatchOutbound()); app.modal(); await app.settle();
      const table = find(tree, item => item.props?.className === "batch-stock-out-table");
      const input = find(table.props.columns[4].render(null, table.props.dataSource[0]), item => item.type === "InputNumber");
      app.act(() => input.props.onChange(3));
    },
    commit() {
      const confirm = find(tree, item => item.props?.className === "batch-stock-out-confirm-modal");
      assert.equal(confirm.props.confirmLoading, false);
      confirm.props.onOk(); render();
    },
    success: () => find(tree, item => item.type === "OperationSuccessModal" && item.props.title === "批量出库成功"),
    ApiResponseError: load("lib/api/client.ts").ApiResponseError,
  };
  return app;
}

test("root providers retain mode, IDs, snapshots and AI session across workspace unmount/remount; reload starts empty", () => {
  const app = application(); app.start(); app.select(product(1), product(21, false));
  const snapshots = app.selection().selectedProducts;
  app.act(() => app.ai().replacePhoto(new File(["offline"], "mock.jpg"), 10, 10));
  const aiSession = app.ai().session;
  app.navigate("ai"); app.navigate("inventory");
  assert.equal(app.selection().batchMode, true);
  assert.equal(app.selection().selectedProducts, snapshots);
  assert.deepEqual(Array.from(app.selection().selectedProductIds), [1, 21]);
  assert.equal(app.bar().selectedCount, 2);
  assert.equal(app.ai().session, aiSession);
  assert.deepEqual({ ...snapshots[1] }, product(21, false));
  const fresh = application();
  assert.equal(fresh.selection().batchMode, false); assert.equal(fresh.selection().selectedCount, 0);
});

test("cross-page deduplication, cancellation and 100-product limit still use the original registry", () => {
  const app = application(); app.start();
  app.act(() => app.node("ProductTable").props.onProductSelectionChange(product(1), true));
  app.act(() => app.node("ProductTable").props.onPageChange(2, 20));
  app.act(() => app.node("ProductTable").props.onCurrentPageSelectionChange([product(1), product(21)], true));
  assert.equal(app.selection().selectedCount, 2);
  app.act(() => app.node("ProductTable").props.onCurrentPageSelectionChange([product(21)], false));
  app.select(...Array.from({ length: 99 }, (_, i) => product(i + 2)));
  assert.equal(app.selection().selectedCount, 100);
  app.act(() => app.node("ProductTable").props.onProductSelectionChange(product(101), true));
  assert.equal(app.selection().selectedCount, 100);
  assert.equal(app.selection().selectedProductIds.has(101), false);
  app.act(() => app.node("ProductTable").props.onCurrentPageSelectionChange([product(101), product(102)], true));
  assert.equal(app.selection().selectedCount, 100);
  assert.equal(app.messages.filter(([kind]) => kind === "warning").length, 2);
});

test("table/gallery, filters and pagination share selection without discarding invisible products", () => {
  const app = application(); app.start(); app.select(product(1), product(21));
  const ids = app.node("ProductTable").props.selectedIds;
  app.act(() => app.node("Segmented").props.onChange("gallery"));
  assert.equal(app.node("ProductGallery").props.selectedIds, ids);
  app.act(() => app.node("InventoryToolbar").props.onSearchChange("not-selected"));
  assert.equal(app.selection().selectedCount, 2);
  app.act(() => app.node("ProductGallery").props.onPageChange(3, 20));
  app.act(() => app.node("Segmented").props.onChange("table"));
  assert.equal(app.node("ProductTable").props.selectedIds, ids);
});

test("pending or failed list loads after navigation never prune selection", async () => {
  const pending = deferred();
  const app = application({ listProducts: () => pending.promise });
  app.start(); app.select(product(99)); app.navigate("ai"); app.navigate("inventory");
  assert.equal(app.node("ProductTable").props.loading, true);
  assert.equal(app.bar().selectedCount, 1);
  pending.reject(new Error("offline")); await app.settle();
  assert.equal(app.node("ProductTable").props.loading, false);
  assert.equal(app.node("ProductTable").props.products.length, 0);
  assert.equal(app.bar().selectedCount, 1);
});

test("clear keeps batch mode; exit clears both and navigation cannot resurrect them", () => {
  const app = application(); app.start(); app.select(product(1));
  app.act(() => app.bar().onClearAll());
  assert.equal(app.selection().batchMode, true); assert.equal(app.selection().selectedCount, 0);
  app.select(product(2)); app.act(() => app.bar().onExit());
  app.navigate("ai"); app.navigate("inventory");
  assert.equal(app.selection().batchMode, false); assert.equal(app.selection().selectedCount, 0);
});

test("category success clears selection only; cancel and failure preserve it", async () => {
  const app = application(); app.start(); app.select(product(1));
  app.act(() => app.node("BatchCategoryModal").props.onCancel());
  assert.equal(app.selection().selectedCount, 1);
  app.api.batchUpdateProductCategory = async () => { throw new Error("rejected"); };
  await app.node("BatchCategoryModal").props.onConfirm(2); app.render();
  assert.equal(app.selection().selectedCount, 1);
  app.api.batchUpdateProductCategory = async () => ({ updated_count: 1 });
  await app.node("BatchCategoryModal").props.onConfirm(2); app.render();
  assert.equal(app.selection().selectedCount, 0); assert.equal(app.selection().batchMode, true);
});

for (const active of [true, false]) {
  test(`batch ${active ? "deactivation" : "activation"} preserves selection on failure and clears only selection on success`, async () => {
    const app = application(); app.start(); app.select(product(1, active));
    const method = active ? "batchDeactivateProducts" : "batchActivateProducts";
    app.act(() => active ? app.bar().onDeactivate() : app.bar().onActivate());
    app.api[method] = async () => { throw new Error("rejected"); };
    await assert.rejects(app.dialogs.at(-1).onOk()); app.render();
    assert.equal(app.selection().selectedCount, 1);
    app.api[method] = async () => ({ updated_count: 1 });
    await app.dialogs.at(-1).onOk(); app.render();
    assert.equal(app.selection().selectedCount, 0); assert.equal(app.selection().batchMode, true);
  });
}

test("quote export success, failure and cancel keep selection and mode", async () => {
  const app = application(); app.start(); app.select(product(1));
  const props = app.node("BatchQuoteExportModal").props;
  props.onCancel();
  await props.onConfirm({ customerName: "Mock", quoteDate: "2026-10-15" }); app.render();
  assert.ok(app.requests.includes("download"));
  app.api.exportBatchQuote = async () => { throw new Error("offline"); };
  await app.node("BatchQuoteExportModal").props.onConfirm({ quoteDate: "2026-10-15" }); app.render();
  assert.equal(app.selection().batchMode, true); assert.equal(app.selection().selectedCount, 1);
});

for (const leave of ["secondaryAction", "primaryAction", "onClose", "navigation"]) {
  test(`stock-out success clears immediately, preserves its summary and stays clear through ${leave}`, async () => {
    const pending = deferred();
    const app = application({ commitBatchStockOut: () => pending.promise });
    app.start(); app.select(product(1), product(21)); await app.prepareStockOut(); app.commit();
    assert.equal(app.selection().selectedCount, 2); assert.equal(app.selection().batchMode, true);
    pending.resolve({}); await app.settle();
    assert.equal(app.selection().selectedCount, 0); assert.equal(app.selection().batchMode, false);
    const success = app.success();
    assert.equal(success.props.open, true);
    assert.match(success.props.description, /1 个商品（1 个包装规格）.*3 箱/);
    assert.deepEqual(Array.from(app.node("BatchStockOutModal").props.productIds), [1, 21]);
    if (leave === "onClose") success.props.onClose();
    else if (leave !== "navigation") success.props[leave].onClick();
    app.navigate("ai"); app.navigate("inventory");
    assert.equal(app.selection().selectedCount, 0); assert.equal(app.selection().batchMode, false);
  });
}

test("stock-out cancel, network failure and stock conflict never clear selection", async () => {
  const app = application(); app.start(); app.select(product(1));
  app.act(() => app.bar().onBatchOutbound());
  app.act(() => app.node("BatchStockOutModal").props.onClose());
  assert.equal(app.selection().selectedCount, 1);
  await app.prepareStockOut();
  for (const error of [new Error("unknown result"), new app.ApiResponseError("stock conflict", 409)]) {
    app.api.commitBatchStockOut = async () => { throw error; };
    app.commit(); await app.settle(); await app.settle();
    assert.equal(app.selection().selectedCount, 1); assert.equal(app.selection().batchMode, true);
    assert.equal(app.success().props.open, false);
  }
});

test("late stock-out success after remount cannot clear a new selection, even with the same IDs", async () => {
  const pending = deferred();
  const app = application({ commitBatchStockOut: () => pending.promise });
  app.start(); app.select(product(1)); await app.prepareStockOut(); app.commit();
  app.navigate("ai"); app.navigate("inventory");
  app.act(() => app.bar().onExit()); app.start(); app.select(product(1));
  pending.resolve({}); await app.settle();
  assert.equal(app.selection().selectedCount, 1); assert.equal(app.selection().batchMode, true);
});

test("late stock-out success clears the original selection when only navigation occurred", async () => {
  const pending = deferred();
  const app = application({ commitBatchStockOut: () => pending.promise });
  app.start(); app.select(product(1)); await app.prepareStockOut(); app.commit();
  app.navigate("ai"); pending.resolve({}); await app.settle(); app.navigate("inventory");
  assert.equal(app.selection().selectedCount, 0); assert.equal(app.selection().batchMode, false);
});

test("late category/status successes cannot clear a selection changed after navigation", async () => {
  for (const method of ["batchUpdateProductCategory", "batchDeactivateProducts", "batchActivateProducts"]) {
    const pending = deferred(), app = application({ [method]: () => pending.promise });
    app.start(); app.select(product(1, method !== "batchActivateProducts"));
    let operation;
    if (method === "batchUpdateProductCategory") operation = app.node("BatchCategoryModal").props.onConfirm(2);
    else {
      app.act(() => method === "batchDeactivateProducts" ? app.bar().onDeactivate() : app.bar().onActivate());
      operation = app.dialogs.at(-1).onOk();
    }
    app.navigate("ai"); app.navigate("inventory");
    app.act(() => app.bar().onClearAll()); app.select(product(2));
    pending.resolve({ updated_count: 1 }); await operation; app.render();
    assert.deepEqual(Array.from(app.selection().selectedProductIds), [2]);
    assert.equal(app.selection().batchMode, true);
  }
});
