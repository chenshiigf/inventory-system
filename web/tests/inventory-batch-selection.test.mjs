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
function application(overrides = {}, initialQuery = "", workspacePath = "components/inventory/InventoryWorkspace.tsx", workspaceProps = {}) {
  const modules = new Map(), instances = new Map(), dialogs = [], requests = [], messages = [];
  let active, seen, effects, tree, selection, ai, page = "inventory", search = initialQuery, modalProps = null;
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
  const ui = Object.fromEntries(["Alert", "Button", "Segmented", "Modal", "Table", "InputNumber", "ConfigProvider", "Card", "Select", "Tag", "Tooltip", "Empty", "Spin", "Image"].map(name => [name, name]));
  ui.Input = { TextArea: "TextArea" };
  ui.DatePicker = { RangePicker: "RangePicker" };
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
      if (name === "next/link") return { default: "Link" };
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
  const Workspace = load(workspacePath).default;
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
    tree = resolve(jsx(Root, { children: [jsx(Probe), page === "inventory" ? jsx(Workspace, workspaceProps) : "AI / other page",
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
    find: predicate => find(tree, predicate),
    query: () => new URLSearchParams(search),
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

// Bug 03: exercise the workspace's actual edit/save handlers with its URL state
// and the root batch-selection provider, without a backend or browser.
const editRecord = (unit = "pcs") => ({
  id: 41, product_code: "MOCK-41", original_product_code: null, is_active: true, category_id: 2, warehouse_id: 3,
  category_name: "Mock category", warehouse_name: "Mock warehouse", image_path: null,
  thumbnail_path: null, size: "20x30", unit, price: "1.00", total_carton_count: 20,
  packagings: [{ id: 410, packing_qty: 24, carton_count: 20, sort_order: 0 }], remark: "Mock remark",
});
const editValues = unit => ({
  originalProductCode: "",
  categoryPath: [1, 2], warehouseId: 3, imagePath: null, thumbnailPath: null,
  size: "20x30", unit, price: "1.00", remark: "Mock remark",
  packagings: [{ id: 410, packingQty: 24, cartonCount: 20 }],
});
async function editingApplication(overrides = {}, pageSize = 50) {
  const app = application({
    listProducts: async () => ({ items: [editRecord()], total: 180 }),
    listWarehouses: async () => [{ id: 3, name: "Mock warehouse" }],
    listCategories: async () => [{ id: 1, name: "Mock parent", children: [{ id: 2, name: "Mock category", children: [] }] }],
    ...overrides,
  });
  app.act(() => app.node("InventoryToolbar").props.onWarehouseChange(3));
  app.act(() => app.node("InventoryToolbar").props.onCategoryChange([1, 2]));
  app.act(() => app.node("InventoryToolbar").props.onSearchChange("MOCK"));
  app.act(() => app.node("InventoryToolbar").props.onStatusChange("all"));
  app.act(() => app.node("InventoryToolbar").props.onStockRangeChange(1, 100));
  app.act(() => app.node("ProductTable").props.onPageChange(1, pageSize));
  app.act(() => app.node("ProductTable").props.onPageChange(3, pageSize));
  await app.settle();
  app.act(() => app.node("ProductTable").props.onEdit(app.node("ProductTable").props.products[0]));
  return app;
}

for (const [before, after] of [["pcs", "set"], ["set", "pcs"]]) {
  test(`editing unit ${before} -> ${after} keeps all URL conditions, refreshes the row and preserves batch selection`, async () => {
    let record = editRecord(before);
    const app = await editingApplication({
      listProducts: async () => ({ items: [record], total: 180 }),
      updateProduct: async (id, payload) => { assert.equal(id, 41); record = { ...record, unit: payload.unit }; return record; },
    });
    app.start(); app.select(product(41), product(99));
    const query = app.query().toString(), snapshots = app.selection().selectedProducts;
    const listCalls = app.requests.filter(([method]) => method === "listProducts").length;
    await app.node("ProductEditorModal").props.onSave(editValues(after)); app.render(); await app.settle();
    assert.equal(app.query().toString(), query);
    assert.deepEqual(Object.fromEntries(app.query()), {
      warehouse_id: "3", category_id: "2", status: "all", stock_min: "1", stock_max: "100",
      search: "MOCK", page: "3", page_size: "50",
    });
    const table = app.node("ProductTable").props;
    assert.equal(table.currentPage, 3); assert.equal(table.pageSize, 50);
    assert.equal(table.products[0].unit, after);
    assert.equal(app.requests.filter(([method]) => method === "listProducts").length, listCalls + 1);
    const latestList = app.requests.filter(([method]) => method === "listProducts").at(-1)[1];
    assert.deepEqual({ ...latestList }, {
      page: 3, pageSize: 50, search: "MOCK", categoryId: 2, warehouseId: 3,
      status: "all", stockMin: 1, stockMax: 100,
    });
    const patch = app.requests.find(([method]) => method === "updateProduct")[2];
    assert.equal(patch.unit, after);
    assert.equal(Object.hasOwn(patch, "packagings"), false);
    assert.equal(Object.hasOwn(patch, "carton_count"), false);
    assert.equal(app.selection().batchMode, true); assert.equal(app.selection().selectedProducts, snapshots);
    assert.equal(app.node("ProductEditorModal"), undefined);
  });
}

test("failed PATCH and cancelled editing keep URL conditions, row data and batch state without reloading", async () => {
  const app = await editingApplication({ updateProduct: async () => { throw new Error("rejected"); } });
  app.start(); app.select(product(41));
  const query = app.query().toString(), listCalls = app.requests.filter(([method]) => method === "listProducts").length;
  await assert.rejects(app.node("ProductEditorModal").props.onSave(editValues("set")), /rejected/); app.render();
  assert.ok(app.node("ProductEditorModal"));
  assert.equal(app.node("ProductTable").props.products[0].unit, "pcs");
  app.act(() => app.node("ProductEditorModal").props.onCancel());
  assert.equal(app.query().toString(), query);
  assert.equal(app.requests.filter(([method]) => method === "listProducts").length, listCalls);
  assert.equal(app.selection().batchMode, true); assert.equal(app.selection().selectedCount, 1);
});

test("loading, failed or empty list refresh after editing never resets page or filters", async () => {
  const app = await editingApplication({ updateProduct: async () => editRecord("set") });
  const query = app.query().toString(), pending = deferred();
  app.api.listProducts = () => pending.promise;
  await app.node("ProductEditorModal").props.onSave(editValues("set")); app.render();
  assert.equal(app.node("ProductTable").props.loading, true); assert.equal(app.query().toString(), query);
  pending.reject(new Error("list offline")); await app.settle();
  assert.equal(app.node("ProductTable").props.products.length, 0); assert.equal(app.query().toString(), query);
  app.api.listProducts = async () => ({ items: [], total: 180 });
  const retry = find(app.find(node => node.props?.title === "商品服务暂不可用"), node => node.type === "Button");
  app.act(() => retry.props.onClick()); await app.settle();
  assert.equal(app.node("ProductTable").props.products.length, 0); assert.equal(app.query().toString(), query);
});

test("creation retains its existing search/category/page reset and preserves the remaining list settings", async () => {
  const app = await editingApplication({ createProduct: async () => editRecord() });
  app.act(() => app.node("ProductEditorModal").props.onCancel());
  // The workspace's new-product button is held in PageHeading's action props.
  const add = find(app.node("PageHeading"), node => node.props?.className === "add-product-button");
  assert.equal(add.props.disabled, false);
  app.act(() => add.props.onClick());
  await app.node("ProductEditorModal").props.onSave(editValues("pcs")); app.render(); await app.settle();
  assert.deepEqual(Object.fromEntries(app.query()), {
    warehouse_id: "3", status: "all", stock_min: "1", stock_max: "100", page_size: "50",
  });
  assert.equal(app.node("ProductTable").props.currentPage, 1);
  assert.equal(app.requests.filter(([method]) => method === "createProduct").length, 1);
  assert.equal(app.requests.some(([method]) => method === "updateProduct"), false);
});

for (const [query, pageSize] of [["", 100], ["page_size=20", 20], ["page_size=50", 50], ["page_size=100", 100]]) {
  test(`/products${query ? `?${query}` : ""} keeps UI/API page size ${pageSize} through filters, gallery and batch navigation`, async () => {
    const app = application({}, query);
    assert.equal(app.node("ProductTable").props.pageSize, pageSize);
    assert.equal(app.requests.find(([method]) => method === "listProducts")[1].pageSize, pageSize);
    app.start(); app.select(product(1), product(21));
    app.act(() => app.node("InventoryToolbar").props.onCategoryChange([1, 2]));
    app.act(() => app.node("InventoryToolbar").props.onSearchChange("Mock"));
    app.act(() => app.node("Segmented").props.onChange("gallery"));
    assert.equal(app.node("ProductGallery").props.pageSize, pageSize);
    assert.equal(app.query().get("page_size"), pageSize === 100 ? null : String(pageSize));
    assert.equal(app.query().get("category_id"), "2"); assert.equal(app.query().get("search"), "Mock");
    assert.ok(app.requests.filter(([method]) => method === "listProducts").every(([, params]) => params.pageSize === pageSize));
    app.navigate("ai"); app.navigate("inventory"); await app.settle();
    assert.equal(app.selection().batchMode, true); assert.equal(app.selection().selectedCount, 2);
    assert.equal(app.node("ProductGallery").props.pageSize, pageSize);
  });
}

for (const pageSize of [20, 50, 100]) {
  test(`editing a product keeps the selected page size ${pageSize}`, async () => {
    const app = await editingApplication({
      updateProduct: async () => editRecord("set"),
      listProducts: async () => ({ items: [editRecord("set")], total: 500 }),
    }, pageSize);
    await app.node("ProductEditorModal").props.onSave(editValues("set")); app.render(); await app.settle();
    assert.equal(app.node("ProductTable").props.pageSize, pageSize);
    assert.equal(app.node("ProductTable").props.currentPage, 3);
    assert.equal(app.query().get("page_size"), pageSize === 100 ? null : String(pageSize));
    assert.equal(app.requests.filter(([method]) => method === "listProducts").at(-1)[1].pageSize, pageSize);
  });
}

test("inventory movements retains its own 20-row UI and API default", async () => {
  const app = application({
    listInventoryMovements: async () => ({ items: [], total: 0, page: 1, page_size: 20 }),
  }, "", "components/inventory/InventoryMovementsWorkspace.tsx");
  await app.settle();
  assert.equal(app.node("Table").props.pagination.pageSize, 20);
  assert.equal(app.requests.find(([method]) => method === "listInventoryMovements")[1].pageSize, 20);
});

for (const input of [" 2677-35 ", "  "]) {
  test(`list editing sends original code ${JSON.stringify(input)} without changing list or batch state`, async () => {
    let record = { ...editRecord(), original_product_code: "before" };
    const app = await editingApplication({
      listProducts: async () => ({ items: [record], total: 180 }),
      updateProduct: async (_id, payload) => { record = { ...record, ...payload }; return record; },
    });
    assert.equal(app.node("ProductEditorModal").props.product.originalProductCode, "before");
    app.start(); app.select(product(41), product(99));
    const query = app.query().toString(), selection = app.selection().selectedProducts;
    await app.node("ProductEditorModal").props.onSave({ ...editValues("pcs"), originalProductCode: input });
    app.render(); await app.settle();
    const payload = app.requests.find(([method]) => method === "updateProduct")[2];
    assert.equal(payload.original_product_code, input.trim() || null);
    assert.equal(Object.hasOwn(payload, "packagings"), false);
    assert.equal(app.node("ProductTable").props.products[0].originalProductCode, input.trim() || null);
    assert.equal(app.query().toString(), query);
    assert.equal(app.selection().selectedProducts, selection);
  });

  test(`detail editing displays and sends original code ${JSON.stringify(input)}`, async () => {
    let record = { ...editRecord(), original_product_code: "before" };
    const app = application({
      getProduct: async () => record,
      listInventoryMovements: async () => ({ items: [], total: 0 }),
      updateProduct: async (_id, payload) => { record = { ...record, ...payload }; return record; },
    }, "", "components/inventory/ProductDetailWorkspace.tsx", { productId: "41" });
    await app.settle();
    const display = () => app.find(node => node.props?.className === "product-detail-original-code").props.children[1].props.children;
    assert.equal(display(), "before");
    const edit = app.find(node => node.type === "Button" && node.props?.icon?.type === "EditOutlined");
    app.act(() => edit.props.onClick()); await app.settle();
    assert.equal(app.node("ProductEditorModal").props.product.originalProductCode, "before");
    await app.node("ProductEditorModal").props.onSave({ ...editValues("pcs"), originalProductCode: input });
    app.render(); await app.settle();
    const payload = app.requests.find(([method]) => method === "updateProduct")[2];
    assert.equal(payload.original_product_code, input.trim() || null);
    assert.equal(Object.hasOwn(payload, "packagings"), false);
    assert.equal(display(), input.trim() || "—");
  });
}

test("new product sends original code through the existing creation path", async () => {
  const app = await editingApplication({ createProduct: async () => editRecord() });
  app.act(() => app.node("ProductEditorModal").props.onCancel());
  const add = find(app.node("PageHeading"), node => node.props?.className === "add-product-button");
  app.act(() => add.props.onClick());
  await app.node("ProductEditorModal").props.onSave({ ...editValues("pcs"), originalProductCode: " 2677-35 " });
  const payload = app.requests.find(([method]) => method === "createProduct")[1];
  assert.equal(payload.original_product_code, "2677-35");
  assert.equal(Object.hasOwn(payload, "product_code"), false);
  assert.deepEqual(Array.from(payload.packagings, row => ({ ...row })), [{ packing_qty: 24 }]);
});
