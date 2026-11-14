// node --experimental-strip-types cf_worker/board_images_test.ts
import assert from "node:assert/strict";
import {
  ACCESS_COMPONENTS, boardElements, boardImagePaths, checkRasterSize, UPLOAD_ROOT, uploadClaimScript, uploadFileName, uploadFolder,
} from "./src/board_images.ts";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, realpathSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

function png(width: number, height: number): Uint8Array {
  const bytes = new Uint8Array(24), view = new DataView(bytes.buffer);
  bytes.set(new TextEncoder().encode("IHDR"), 12);
  view.setUint32(16, width); view.setUint32(20, height);
  return bytes;
}
function webp(tag: string, data: number[]): Uint8Array {
  const bytes = new Uint8Array(20 + data.length + (data.length & 1));
  bytes.set(new TextEncoder().encode(tag), 12);
  new DataView(bytes.buffer).setUint32(16, data.length, true);
  bytes.set(data, 20); return bytes;
}
checkRasterSize(png(1280, 1600), "png");
checkRasterSize(png(4000, 4000), "png");
assert.throws(() => checkRasterSize(png(4001, 4000), "png"), /megapixels/);
assert.throws(() => checkRasterSize(png(8193, 1), "png"), /per side/);
assert.throws(() => checkRasterSize(png(0, 1), "png"), /malformed/);
assert.throws(() => checkRasterSize(new Uint8Array(8), "png"), /malformed/);
const gif = new Uint8Array(10);
new DataView(gif.buffer).setUint16(6, 1280, true);
new DataView(gif.buffer).setUint16(8, 1600, true);
checkRasterSize(gif, "gif");
gif[7] = 0xff;
assert.throws(() => checkRasterSize(gif, "gif"), /per side/);
const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0, 4, 0, 0, 0xff, 0xc2, 0, 8, 8, 6, 64, 5, 0, 3]);
checkRasterSize(jpeg, "jpeg");
jpeg[13] = 0xff;
assert.throws(() => checkRasterSize(jpeg, "jpeg"), /per side/);
for (const kind of ["png", "gif", "jpeg", "webp"]) {
  for (let size = 0; size < 24; size++) {
    assert.throws(() => checkRasterSize(new Uint8Array(size), kind), /malformed/);
  }
}
checkRasterSize(webp("VP8X", [0, 0, 0, 0, 0xff, 4, 0, 0x3f, 6, 0]), "webp");
checkRasterSize(webp("VP8 ", [0, 0, 0, 0x9d, 1, 0x2a, 0, 5, 0x40, 6]), "webp");
const lossless = new Uint8Array(5);
lossless[0] = 0x2f;
new DataView(lossless.buffer).setUint32(1, 1279 | (1599 << 14), true);
checkRasterSize(webp("VP8L", [...lossless]), "webp");
assert.throws(() => checkRasterSize(webp("VP8X", [0, 0, 0, 0, 0xff, 0xff, 0xff, 1, 0, 0]), "webp"), /per side/);
checkRasterSize(new TextEncoder().encode('<svg viewBox="0 0 10 10"/>'), "svg");

assert.deepEqual(boardImagePaths(`
root = Stack([pic, Text("Image('not-a-file.png', 'example')")])
// Image("comment.png", "ignored")
pic = Image /* not legal board syntax */ ("also-not-legal.png", "x")
real = Image ( // whitespace and line comments are legal
  "bayleaf/chart\\u002epng", "Plot")
icon = Image('icon.png', '')
`), ["bayleaf/chart.png", "icon.png"]);
assert.deepEqual(boardImagePaths('root = Stack([Image("a.png", "Image(\\\"ghost.png\\\", x)")])'), ["a.png"]);
assert.deepEqual(boardImagePaths('root = Stack([Image(path, "x")])\npath = alias\nalias = "chart.png"'), ["chart.png"]);
assert.deepEqual(boardImagePaths('root = Stack([Image(path, "x")])\npath = alias\nalias = path'), []);
assert.deepEqual(boardImagePaths('root = Stack([Image(path, "x")])\npath = "old.png"\npath = "new.png"'), ["new.png"]);

// Board elements: files a board shows and Access Elements, each with its target as written.
const elements = boardElements(`
root = Stack([shot, Document("REPORT.md", "The report"), app, Preview(3000.0), Terminal(), Terminal(label), Desktop("The desktop"),
  SshAccess("Copy this"), Download('out/report.zip', "Get it"), Text("Preview(9) and Terminal('ghost')"), Preview(unbound),
  Upload("talk-slides", "Drop slides"), Upload()])
// Preview(4444)
shot = Image("shot.png", "x")
app = Preview(port, "The dev server")
port = 5173
label = "Build shell"
`);
assert.deepEqual(elements, [
  { component: "Document", target: "REPORT.md" },
  { component: "Preview", target: "3000" },
  { component: "Terminal", target: "" },
  { component: "Terminal", target: "" },
  { component: "Desktop", target: "" },
  { component: "SshAccess", target: "" },
  { component: "Download", target: "out/report.zip" },
  { component: "Upload", target: "talk-slides" },
  { component: "Upload", target: "" },
  { component: "Image", target: "shot.png" },
  { component: "Preview", target: "5173" },
]);
// The page derives the same grant keys from the same program.
assert.deepEqual(
  elements.filter((item) => item.component in ACCESS_COMPONENTS).map((item) => `${ACCESS_COMPONENTS[item.component]}:${item.target}`),
  ["preview:3000", "terminal:", "terminal:", "desktop:", "ssh:", "download:out/report.zip", "upload:talk-slides", "upload:", "preview:5173"],
);
assert.deepEqual(boardElements("root = Stack([Preview(0080), Desktop(), SshAccess()])"),
  [{ component: "Preview", target: "80" }, { component: "Desktop", target: "" }, { component: "SshAccess", target: "" }]);
assert.deepEqual(boardImagePaths('root = Stack([Document("a.png"), Image("b.png"), Download("c.png")])'), ["b.png"]);

// Upload folders stay inside the upload root; received names flatten to one plain segment.
assert.equal(uploadFolder(""), UPLOAD_ROOT);
assert.equal(uploadFolder("talk-slides"), `${UPLOAD_ROOT}/talk-slides`);
for (const said of ["~/uploads/talk-slides", "uploads/talk-slides", "/home/daytona/uploads/talk-slides", "talk-slides/"]) {
  assert.equal(uploadFolder(said), `${UPLOAD_ROOT}/talk-slides`, said);
}
assert.equal(uploadFolder("~/uploads"), UPLOAD_ROOT);
assert.equal(uploadFolder("a/b c/d"), `${UPLOAD_ROOT}/a/b c/d`);
for (const bad of ["../x", "a/../b", "..", ".ssh", "a/.git", "a/b/c/d", "/etc", "a//b", "a$b", "a\\b", "~/x", "a/ b", "a /b"]) {
  assert.throws(() => uploadFolder(bad), /inside ~\/uploads/, bad);
}
assert.deepEqual(uploadFileName("Talk slides.pdf"), { name: "Talk slides.pdf", stem: "Talk slides", extension: ".pdf" });
assert.deepEqual(uploadFileName("../../.bashrc"), { name: "bashrc", stem: "bashrc", extension: "" });
assert.equal(uploadFileName("C:\\Users\\me\\notes.txt").name, "notes.txt");
assert.equal(uploadFileName(".env").name, "env");
assert.equal(uploadFileName("Café Ünïcode 日本.png").name, "Café Ünïcode 日本.png");
assert.equal(uploadFileName("Cafe\u0301.txt").name, "Café.txt");
assert.equal(uploadFileName('a"b;c\n.md').name, "a_b_c.md");
assert.equal(uploadFileName("").name, "upload");
assert.equal(uploadFileName("...").name, "upload");
assert.deepEqual(uploadFileName(".pdf"), { name: "pdf", stem: "pdf", extension: "" });
const long = uploadFileName(`${"x".repeat(300)}.tar.gz`);
assert.equal(new TextEncoder().encode(long.name).length, 200);
assert.ok(long.name.endsWith("x.gz"));
assert.equal(long.extension, ".gz");
assert.equal(uploadFileName(`archive.${"y".repeat(20)}`).extension, "");
// Multibyte names stay under the byte cap and are never cut inside a character.
const wide = uploadFileName(`${"\u{1F415}\u732B".repeat(80)}.png`);
assert.ok(new TextEncoder().encode(wide.name).length <= 200);
assert.ok(!/[\uD800-\uDFFF]$/.test(wide.stem.slice(-1)) || /\p{L}|\p{N}/u.test(wide.stem.slice(-2)));

// The claim script itself, run by bash against a real folder (GNU realpath; grealpath on macOS).
const gnu = spawnSync("realpath", ["-e", "/"]).status === 0 ? "realpath" : spawnSync("grealpath", ["-e", "/"]).status === 0 ? "grealpath" : null;
if (gnu) {
  const base = realpathSync(mkdtempSync(join(tmpdir(), "dogwalk-upload-")));
  const bin = join(base, "bin");
  mkdirSync(bin);
  writeFileSync(join(bin, "realpath"), `#!/bin/sh\nexec ${gnu} "$@"\n`, { mode: 0o755 });
  const root = join(base, "uploads");
  const claim = (folder: string, stem: string, ext: string) => spawnSync("bash", ["-c", uploadClaimScript(root, folder, stem, ext)],
    { env: { ...process.env, PATH: `${bin}:${process.env.PATH}` }, encoding: "utf8" });
  const first = claim(`${root}/talk/slides`, "deck", ".pdf");
  assert.equal(first.status, 0, first.stderr);
  assert.equal(first.stdout, `${root}/talk/slides/deck.pdf`);
  assert.equal(claim(`${root}/talk/slides`, "deck", ".pdf").stdout, `${root}/talk/slides/deck (2).pdf`);
  assert.equal(claim(root, "it's", "").stdout, `${root}/it's`);
  // A dangling link where the name would go is skipped, not followed.
  symlinkSync(join(base, "elsewhere"), `${root}/talk/link.txt`);
  assert.equal(claim(`${root}/talk`, "link", ".txt").stdout, `${root}/talk/link (2).txt`);
  assert.deepEqual(readdirSync(base).sort(), ["bin", "uploads"]);
  // A symlinked folder step is refused before anything is made beneath it.
  mkdirSync(join(base, "outside"));
  symlinkSync(join(base, "outside"), `${root}/escape`);
  assert.equal(claim(`${root}/escape/deeper`, "x", "").status, 3);
  assert.deepEqual(readdirSync(join(base, "outside")), []);
  // A symlinked root is refused outright.
  const other = join(base, "uploads2");
  symlinkSync(join(base, "outside"), other);
  assert.equal(spawnSync("bash", ["-c", uploadClaimScript(other, `${other}/a`, "x", "")], { env: { ...process.env, PATH: `${bin}:${process.env.PATH}` } }).status, 3);
  assert.deepEqual(readdirSync(join(base, "outside")), []);
  execFileSync("rm", ["-rf", base]);
} else {
  console.log("skipping the claim script run: no GNU realpath");
}
console.log("board image header limits, path capture, board elements, and upload names: passed");
