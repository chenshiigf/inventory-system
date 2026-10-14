// Optional real-browser regression harness (no API/backend or model connection).
// Run: node tests/ai-search-image.browser.mjs
// Open the printed loopback URL. Optional --photo <local field photo> enables a
// private, offline 1600/2048 comparison; outputs are not saved or uploaded.
import { createServer } from "node:http";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const photoFlag = process.argv.indexOf("--photo");
const photo = photoFlag < 0 ? null : readFileSync(process.argv[photoFlag + 1]);
const files = new Map(["ai-search-image", "image-crop"].map(name => [`/${name}.js`, ts.transpileModule(
  readFileSync(path.join(root, `lib/${name}.ts`), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } },
).outputText.replace('"@/lib/image-crop"', '"/image-crop.js"')]));

async function suite() {
  const { prepareAISearchImage, readAIPhoto } = await import("/ai-search-image.js");
  const rows = [];
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const blob = (canvas, type = "image/jpeg") => new Promise(resolve => canvas.toBlob(resolve, type, 0.95));
  async function image(width, height, type = "image/jpeg") {
    const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
    const c = canvas.getContext("2d");
    const colors = ["red", "lime", "blue", "yellow"];
    colors.forEach((color, i) => { c.fillStyle = color; c.fillRect((i % 2) * width / 2, Math.floor(i / 2) * height / 2, width / 2, height / 2); });
    const bytes = await blob(canvas, type); canvas.width = canvas.height = 0;
    return new File([bytes], "scene.jpg", { type });
  }
  function exif(bytes, orientation) {
    const app = new Uint8Array(36), v = new DataView(app.buffer);
    app.set([255, 225, 0, 34, 69, 120, 105, 102, 0, 0, 73, 73, 42, 0, 8, 0, 0, 0, 1, 0]);
    v.setUint16(20, 274, true); v.setUint16(22, 3, true); v.setUint32(24, 1, true); v.setUint16(28, orientation, true);
    return new File([bytes.slice(0, 2), app, bytes.slice(2)], `exif-${orientation}.jpg`, { type: "image/jpeg" });
  }
  async function color(file) {
    const bitmap = await createImageBitmap(file);
    const canvas = document.createElement("canvas"); canvas.width = canvas.height = 1;
    canvas.getContext("2d").drawImage(bitmap, bitmap.width / 2, bitmap.height / 2, 1, 1, 0, 0, 1, 1);
    const rgb = [...canvas.getContext("2d").getImageData(0, 0, 1, 1).data].slice(0, 3);
    bitmap.close(); canvas.width = canvas.height = 0; return rgb;
  }
  for (const [width, height] of [[320, 200], [4000, 3000], [6000, 4000]]) {
    const file = await image(width, height);
    const out = await prepareAISearchImage(file, null);
    check(out.width <= 1600 && out.height <= 1600 && out.file.size <= 2097152, "Output limit");
    const crop = await prepareAISearchImage(file, [0, 0, .5, .5]);
    const rgb = await color(crop.file); check(rgb[0] > 240 && rgb[1] < 10 && rgb[2] < 10, "Crop picks original quadrant");
    rows.push({ case: `${width}x${height}`, original_bytes: file.size, output_bytes: out.file.size, width: out.width, height: out.height, crop_bytes: crop.file.size });
  }
  const base = await image(80, 40);
  const big = new File([base, new Uint8Array(11 * 1024 * 1024)], "over10mb.jpg", { type: "image/jpeg" });
  const out = await prepareAISearchImage(big, null);
  check(big.size > 10485760 && out.width === 80 && out.file.size < 2097152, "Large original rejected or upscaled");
  rows.push({ case: "over10MiB-valid-JPEG", original_bytes: big.size, output_bytes: out.file.size });
  const expected = { 1: [255, 0, 0], 2: [0, 255, 0], 3: [255, 255, 0], 4: [0, 0, 255], 5: [255, 0, 0], 6: [0, 0, 255], 7: [255, 255, 0], 8: [0, 255, 0] };
  const bytes = new Uint8Array(await base.arrayBuffer());
  for (let orientation = 1; orientation <= 8; orientation++) {
    const file = exif(bytes, orientation), bitmap = await readAIPhoto(file);
    const width = bitmap.width, height = bitmap.height; bitmap.close();
    const preview = new Image(), url = URL.createObjectURL(file); preview.src = url;
    await preview.decode();
    check(preview.naturalWidth === width && preview.naturalHeight === height, "Native preview orientation mismatch"); URL.revokeObjectURL(url);
    const crop = await prepareAISearchImage(file, [0, 0, .5, .5], { sourceSize: { width, height } });
    const rgb = await color(crop.file);
    check(rgb.every((value, i) => Math.abs(value - expected[orientation][i]) < 12), `EXIF ${orientation} wrong pixels: ${rgb}`);
    const whole = await prepareAISearchImage(file, null);
    const next = await createImageBitmap(whole.file);
    check(next.width === width && next.height === height, "Output rotated twice"); next.close();
    rows.push({ case: `EXIF-${orientation}`, width, height, crop_color: rgb });
  }
  for (const type of ["image/png", "image/webp"]) {
    const canvas = document.createElement("canvas"); canvas.width = canvas.height = 200;
    const file = new File([await blob(canvas, type)], "transparent", { type }); canvas.width = canvas.height = 0;
    const output = await prepareAISearchImage(file, null), rgb = await color(output.file);
    check(rgb.every(value => value > 250), "Transparency must become white");
    rows.push({ case: type, output_bytes: output.file.size, color: rgb });
  }
  if (window.hasFieldPhoto) {
    const file = new File([await (await fetch("/field.jpg")).blob()], "field.jpg", { type: "image/jpeg" });
    const crop = [0, 0, 1, 1]; // User may re-run with an independently selected region below.
    window.compareFieldPhoto = async (region = crop) => {
      const comparisons = [];
      for (const longEdge of [1600, 2048]) {
        const output = await prepareAISearchImage(file, region, { longEdge });
        const img = new Image(); img.src = URL.createObjectURL(output.file); img.alt = `${longEdge} candidate`;
        document.body.append(img); await img.decode(); URL.revokeObjectURL(img.src);
        comparisons.push({ longEdge, bytes: output.file.size, width: output.width, height: output.height });
      }
      return comparisons;
    };
    rows.push({ case: "field-photo", original_bytes: file.size, comparisons: await window.compareFieldPhoto() });
  }
  return rows;
}
const html = `<!doctype html><meta charset="utf-8"><title>Offline AI image regression</title><style>body{font:16px sans-serif}img{max-width:48%;vertical-align:top}</style><pre id="result">Running local Canvas tests…</pre><script type="module">window.hasFieldPhoto=${photo !== null};window.done=(${suite.toString()})().then(rows=>{window.results=rows;document.querySelector('#result').textContent=JSON.stringify(rows,null,2);return rows}).catch(error=>{document.querySelector('#result').textContent=error.stack;throw error});</script>`;
const server = createServer((req, res) => {
  if (req.method !== "GET") { res.writeHead(405).end(); return; }
  if (files.has(req.url)) { res.setHeader("Content-Type", "text/javascript"); res.end(files.get(req.url)); }
  else if (req.url === "/field.jpg" && photo) { res.setHeader("Content-Type", "image/jpeg"); res.end(photo); }
  else if (req.url === "/") { res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(html); }
  else res.writeHead(404).end();
});
server.listen(0, "127.0.0.1", () => console.log(`Offline-only image tests: http://127.0.0.1:${server.address().port}/`));
