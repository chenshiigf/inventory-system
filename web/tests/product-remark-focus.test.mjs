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
const source = readFileSync(path.join(root, "components/inventory/ProductRemarkEditor.tsx"), "utf8");
function renderEditor(stateOverride) {
  const modules = new Map();
  const effects = [];
  function load(relative) {
    const filename = path.join(root, relative);
    if (modules.has(filename)) return modules.get(filename).exports;
    const compiledModule = { exports: {} }; modules.set(filename, compiledModule);
    const code = ts.transpileModule(readFileSync(filename, "utf8"), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    }).outputText;
    const localRequire = name => {
      if (name === "react") return { ...requirePackage("react"), useRef: current => ({ current }), useEffect: callback => effects.push(callback),
        useReducer: (_reducer, initial, initialize) => [stateOverride ?? initialize(initial), () => {}] };
      if (name === "antd") return { Alert: "Alert", Button: "Button", Input: { TextArea: "TextArea" }, Popover: "Popover", Tooltip: "Tooltip" };
      if (name === "@ant-design/icons") return { EditOutlined: "EditIcon" };
      return name.startsWith("@/") ? load(`${name.slice(2)}.ts`) : requirePackage(name);
    };
    vm.runInThisContext(`(function(exports, require, module) {${code}\n})`)(compiledModule.exports, localRequire, compiledModule);
    return compiledModule.exports;
  }
  const editor = load("components/inventory/ProductRemarkEditor.tsx").default;
  const rendered = editor({ product: { id: 41, productCode: "MOCK-FOCUS-41", remark: "原备注" }, disabled: false, onSave: async () => "" });
  return { popover: rendered.props.children.find(child => child?.type === "Popover"), effects };
}

test("first mount never autofocuses an unpositioned textarea; every completed open focuses without scrolling", () => {
  const { popover, effects } = renderEditor();
  const textarea = popover.props.content.props.children[0];
  assert.equal(textarea.props.autoFocus, undefined);
  const calls = [];
  textarea.props.ref.current = { focus: options => calls.push(options) };
  effects.forEach(effect => effect());
  assert.deepEqual(calls, []); // Rendering and lazy mount alone must not focus.
  popover.props.afterOpenChange(true);
  popover.props.afterOpenChange(true); // Reopen after an already mounted popup.
  assert.deepEqual(calls, [{ preventScroll: true }, { preventScroll: true }]);
});

test("close returns keyboard focus without scrolling, but never steals focus from an outside control", () => {
  const { popover } = renderEditor();
  const body = {}, inside = {}, outside = {};
  const previous = globalThis.document;
  const calls = [];
  popover.props.children.props.ref.current = { focus: options => calls.push(options) };
  popover.props.content.props.ref.current = { contains: node => node === inside };
  try {
    globalThis.document = { body, activeElement: inside };
    popover.props.afterOpenChange(false);
    globalThis.document.activeElement = body; // Hidden popup can make the browser blur to body.
    popover.props.afterOpenChange(false);
    globalThis.document.activeElement = outside;
    popover.props.afterOpenChange(false);
    assert.deepEqual(calls, [{ preventScroll: true }, { preventScroll: true }]);
  } finally {
    if (previous === undefined) delete globalThis.document; else globalThis.document = previous;
  }
});

test("focus fix adds neither scroll restoration nor navigation or submit behavior to the editor", () => {
  assert.doesNotMatch(source, /scrollTo\s*\(|scrollIntoView\s*\(|router\.|window\.location/);
  const trigger = renderEditor().popover.props.children;
  assert.equal(trigger.props.href, undefined);
  assert.notEqual(trigger.props.htmlType, "submit");
  assert.equal(trigger.props.disabled, false);
});

test("save failure restores input focus without scrolling and respects outside focus", () => {
  const { popover, effects } = renderEditor({ open: true, draft: "输入保留", saving: false, error: "Mock failure" });
  const previous = globalThis.document;
  const body = {}, outside = {}, calls = [];
  popover.props.content.props.children[0].props.ref.current = { focus: options => calls.push(options) };
  popover.props.content.props.ref.current = { contains: () => false };
  try {
    globalThis.document = { body, activeElement: body };
    effects.forEach(effect => effect());
    assert.deepEqual(calls, [{ preventScroll: true }]);
    globalThis.document.activeElement = outside;
    effects.forEach(effect => effect());
    assert.equal(calls.length, 1);
  } finally {
    if (previous === undefined) delete globalThis.document; else globalThis.document = previous;
  }
});
