import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const modules = new Map();
function load(relative) {
  if (modules.has(relative)) return modules.get(relative);
  const exports = {};
  modules.set(relative, exports);
  const code = ts.transpileModule(readFileSync(path.join(root, relative), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInThisContext(`(function(exports, require) {${code}\n})`)(exports, name => load(`${name.slice(2)}.ts`));
  return exports;
}
const images = load("lib/ai-search-image.ts");
const crops = load("lib/image-crop.ts");

async function browserMock(run, { width = 4000, height = 3000, blobSize = 100, blobType = "image/jpeg", decodeError = false, abortOnEncode } = {}) {
  const previous = { document: globalThis.document, createImageBitmap: globalThis.createImageBitmap, fetch: globalThis.fetch };
  const calls = { draws: [], fills: [], qualities: [], closed: 0, network: 0, decodes: 0 };
  const bitmap = { width, height, close: () => { calls.closed += 1; } };
  const context = { fillRect: (...args) => calls.fills.push(args), drawImage: (...args) => calls.draws.push(args) };
  const canvas = { width: 0, height: 0, getContext: () => context, toBlob(callback, type, quality) {
    calls.qualities.push(quality);
    abortOnEncode?.();
    callback(blobSize === null ? null : new Blob([new Uint8Array(typeof blobSize === "function" ? blobSize(calls) : blobSize)], { type: blobType }));
  } };
  globalThis.document = { createElement: name => { assert.equal(name, "canvas"); return canvas; } };
  globalThis.createImageBitmap = async (file, options) => {
    calls.decodes += 1; assert.equal(options.imageOrientation, "from-image");
    if (decodeError) throw new Error("invalid image");
    return bitmap;
  };
  globalThis.fetch = async () => { calls.network += 1; throw new Error("External requests forbidden"); };
  try { await run({ calls, canvas, bitmap, context }); }
  finally { Object.assign(globalThis, previous); }
}
const original = () => new File(["original pixels"], "warehouse.webp", { type: "image/webp", lastModified: 123 });

test("12MP/24MP fit 1600 without upscaling; 2048 candidate stays below 3MP", () => {
  assert.deepEqual(images.searchImageSize(4000, 3000), { width: 1600, height: 1200 });
  assert.deepEqual(images.searchImageSize(6000, 4000), { width: 1600, height: 1066 });
  assert.deepEqual(images.searchImageSize(300, 200), { width: 300, height: 200 });
  const larger = images.searchImageSize(6000, 4000, 2048);
  assert.equal(larger.width, 2048);
  const square = images.searchImageSize(4000, 4000, 2048);
  assert.ok(square.width * square.height <= images.AI_IMAGE_MAX_PIXELS);
  assert.throws(() => images.searchImageSize(0, 1));
  assert.throws(() => images.searchImageSize(4000, 3000, 3000));
});

test("over-10MiB originals are accepted; only bounded JPEG is uploaded", async () => {
  await browserMock(async ({ calls, canvas, context }) => {
    const file = new File([new Uint8Array(11 * 1024 * 1024)], "large.jpg", { type: "image/jpeg" });
    const output = await images.prepareAISearchImage(file, null);
    assert.equal(file.size, 11 * 1024 * 1024);
    assert.equal(output.file.type, "image/jpeg");
    assert.ok(output.file.size <= images.AI_IMAGE_MAX_BYTES);
    assert.deepEqual([output.width, output.height], [1600, 1200]);
    assert.equal(output.serverCrop, null);
    assert.equal(context.fillStyle, "white", "transparent pixels flatten onto white");
    assert.equal(calls.closed, 1); assert.equal(calls.network, 0);
    assert.equal(canvas.width * canvas.height, 0);
  });
});

test("reverse drag crops original pixels before resize and re-crop never uses earlier output", async () => {
  await browserMock(async ({ calls, bitmap }) => {
    const file = original();
    const crop = crops.cropFromPoints([0.75, 0.8], [0.25, 0.2]);
    const output = await images.prepareAISearchImage(file, crop);
    assert.deepEqual(calls.draws[0].slice(0, 5), [bitmap, 1000, 600, 2000, 1800]);
    assert.deepEqual([output.width, output.height], [1600, 1440]);
    assert.deepEqual(output.originalCrop, crop);
    assert.equal(output.serverCrop, null);
    await images.prepareAISearchImage(file, [0.5, 0, 0.5, 1]);
    assert.deepEqual(calls.draws[1].slice(0, 5), [bitmap, 2000, 0, 2000, 3000]);
    assert.equal(await file.text(), "original pixels");
    assert.equal(calls.closed, 2); assert.equal(calls.network, 0);
  });
});

test("small source stays full resolution and half-pixel bounds match backend", async () => {
  await browserMock(async ({ calls }) => {
    const output = await images.prepareAISearchImage(original(), [0.005, 0.015, 0.3, 0.3]);
    assert.deepEqual(calls.draws[0].slice(1), [0, 2, 30, 30, 0, 0, 30, 30]);
    assert.deepEqual([output.width, output.height], [30, 30]);
  }, { width: 100, height: 100 });
});

test("EXIF-oriented dimensions are checked; invalid region/format/pixels fail closed", async () => {
  await browserMock(async ({ calls, canvas }) => {
    await assert.rejects(images.prepareAISearchImage(original(), null, { sourceSize: { width: 3000, height: 4000 } }), images.AIImageProcessingError);
    await assert.rejects(images.prepareAISearchImage(original(), [0.99, 0, 0.5, 1]), images.AIImageProcessingError);
    await assert.rejects(images.prepareAISearchImage(new File(["x"], "x.heic", { type: "image/heic" }), null), images.AIImageProcessingError);
    assert.equal(calls.closed, 2); assert.equal(calls.draws.length, 0); assert.equal(calls.network, 0);
    assert.equal(canvas.width, 0);
  });
  await browserMock(async ({ calls }) => {
    await assert.rejects(images.prepareAISearchImage(original(), null), images.AIImageProcessingError);
    assert.equal(calls.closed, 1); assert.equal(calls.network, 0);
  }, { width: 8000, height: 6000 });
});

test("bounded compression retries redraw original, never make API calls", async () => {
  await browserMock(async ({ calls, bitmap, canvas }) => {
    const output = await images.prepareAISearchImage(original(), null);
    assert.deepEqual(calls.qualities, [0.9, 0.8, 0.7, 0.9]);
    assert.equal(calls.draws.length, 2);
    assert.ok(calls.draws.every(draw => draw[0] === bitmap && draw[3] === 4000));
    assert.deepEqual([output.width, output.height], [1280, 960]);
    assert.equal(calls.closed, 1); assert.equal(canvas.width, 0); assert.equal(calls.network, 0);
  }, { blobSize: calls => calls.qualities.length <= 3 ? images.AI_IMAGE_MAX_BYTES + 1 : 100 });
});

test("decoder/encoder failure or exhausted compression releases resources and sends no requests", async () => {
  for (const config of [{ decodeError: true }, { blobSize: null }, { blobType: "image/png" }, { blobSize: images.AI_IMAGE_MAX_BYTES + 1 }]) {
    await browserMock(async ({ calls, canvas }) => {
      await assert.rejects(images.prepareAISearchImage(original(), null), images.AIImageProcessingError);
      assert.equal(calls.network, 0); assert.equal(canvas.width, 0);
      assert.equal(calls.closed, config.decodeError ? 0 : 1);
      assert.ok(calls.qualities.length <= 12);
    }, config);
  }
});

test("abort during encoding prevents upload and releases bitmap/canvas", async () => {
  const controller = new AbortController();
  await browserMock(async ({ calls, canvas }) => {
    await assert.rejects(images.prepareAISearchImage(original(), null, { signal: controller.signal }), { name: "AbortError" });
    assert.equal(calls.closed, 1); assert.equal(canvas.width, 0); assert.equal(calls.network, 0);
  }, { abortOnEncode: () => controller.abort() });
});
