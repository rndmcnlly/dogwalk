// Header checks before browser decode, not a complete image decoder. Animated formats
// are bounded by canvas size, not total decoded frames. SVG keeps its image-context isolation.
export const MAX_IMAGE_PIXELS = 16_000_000;
export const MAX_IMAGE_SIDE = 8192;

export function checkRasterSize(bytes: Uint8Array, kind: string): void {
  if (kind === "svg") return;
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const ascii = (start: number, end: number) => String.fromCharCode(...bytes.subarray(start, end));
  let width = 0, height = 0;
  if (kind === "png" && bytes.length >= 24 && ascii(12, 16) === "IHDR") {
    width = view.getUint32(16); height = view.getUint32(20);
  } else if (kind === "gif" && bytes.length >= 10) {
    width = view.getUint16(6, true); height = view.getUint16(8, true);
  } else if (kind === "jpeg") {
    // Walk length-prefixed segments up to the first start-of-frame marker.
    let offset = 2;
    while (offset + 1 < bytes.length && bytes[offset] === 0xff) {
      while (bytes[offset] === 0xff) offset++;
      const marker = bytes[offset++];
      if (marker === 0xda || marker === 0xd9) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      if (offset + 2 > bytes.length) break;
      const length = view.getUint16(offset);
      if (length < 2 || offset + length > bytes.length) break;
      if (length >= 8 && [0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf].includes(marker)) {
        height = view.getUint16(offset + 3); width = view.getUint16(offset + 5); break;
      }
      offset += length;
    }
  } else if (kind === "webp") {
    // RIFF chunks may include metadata before the image header.
    for (let offset = 12; offset + 8 <= bytes.length;) {
      const tag = ascii(offset, offset + 4), length = view.getUint32(offset + 4, true);
      const start = offset + 8;
      if (start + length > bytes.length) break;
      const u24 = (at: number) => bytes[at] | bytes[at + 1] << 8 | bytes[at + 2] << 16;
      if (tag === "VP8X" && length >= 10) {
        width = 1 + u24(start + 4); height = 1 + u24(start + 7); break;
      }
      if (tag === "VP8 " && length >= 10 && ascii(start + 3, start + 6) === "\u009d\u0001\u002a") {
        width = view.getUint16(start + 6, true) & 0x3fff;
        height = view.getUint16(start + 8, true) & 0x3fff; break;
      }
      if (tag === "VP8L" && length >= 5 && bytes[start] === 0x2f) {
        const packed = view.getUint32(start + 1, true);
        width = 1 + (packed & 0x3fff); height = 1 + ((packed >>> 14) & 0x3fff); break;
      }
      offset = start + length + (length & 1);
    }
  }
  if (!width || !height) throw new Error("that image has a missing or malformed dimension header");
  if (width > MAX_IMAGE_SIDE || height > MAX_IMAGE_SIDE || width * height > MAX_IMAGE_PIXELS) {
    throw new Error(`that image is ${width} × ${height}; at most ${MAX_IMAGE_SIDE} pixels per side and ${MAX_IMAGE_PIXELS / 1_000_000} megapixels; ask a session for a smaller rendition`);
  }
}

// Tokenize enough of the board grammar to ignore comments and quoted prose. Image
// Paths can be string literals or references to string-valued statements.
export function boardImagePaths(program: string): string[] {
  const tokens = Array.from(program.matchAll(/\/\/[^\n]*|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|[A-Za-z_]\w*|[^\s]/g), (match) => match[0])
    .filter((token) => !token.startsWith("//"));
  const paths: string[] = [];
  const bindings = new Map<string, string>();
  for (let i = 0; i < tokens.length - 2; i++) {
    if (/^[A-Za-z_]\w*$/.test(tokens[i]) && tokens[i + 1] === "=") {
      bindings.set(tokens[i], tokens[i + 3] === "(" ? "" : tokens[i + 2]);
    }
  }
  for (let i = 0; i < tokens.length - 2; i++) {
    if (tokens[i] !== "Image" || tokens[i + 1] !== "(") continue;
    let raw = tokens[i + 2];
    const seen = new Set<string>();
    while (bindings.has(raw) && !seen.has(raw)) { seen.add(raw); raw = bindings.get(raw)!; }
    if (!/^["']/.test(raw)) continue;
    paths.push(raw.slice(1, -1).replace(/\\(u[0-9a-fA-F]{4}|.)/g, (_, escape: string) => escape.length === 5
      ? String.fromCharCode(parseInt(escape.slice(1), 16))
      : escape === "n" ? "\n" : escape === "t" ? "\t" : escape === "r" ? "" : escape).trim());
  }
  return paths;
}
