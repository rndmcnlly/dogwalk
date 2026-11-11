// node --experimental-strip-types cf_worker/board_images_test.ts
import assert from "node:assert/strict";
import { boardImagePaths, checkRasterSize } from "./src/board_images.ts";

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
console.log("board image header limits and path capture: passed");
