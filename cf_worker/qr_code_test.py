#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["zxing-cpp==2.3.0", "pillow==11.3.0"]
# ///
"""The paired page's QR encoder, extracted from pair_page.ts and run in Node, checked by an
independent decoder (zxing-cpp): every version from 1 to 10, ASCII and multibyte UTF-8, and the
size limit. A code that only our own encoder can read would be worse than none."""

import json
import re
import subprocess
from pathlib import Path

import zxingcpp
from PIL import Image

WORKER_DIR = Path(__file__).resolve().parent
QUIET, SCALE = 4, 4


def encoder_source() -> str:
    page = (WORKER_DIR / "src" / "pair_page.ts").read_text()
    return page[page.index("// ---- QR codes"):page.index("// QrCode(text, label)")]


def matrices(texts: list[str]) -> list[list[str] | None]:
    script = encoder_source() + (
        f"\nconsole.log(JSON.stringify({json.dumps(texts)}.map((t) => {{ const m = qrEncode(t);"
        " return m && m.map((row) => row.map((d) => d ? '1' : '0').join('')); })));"
    )
    run = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def decode(rows: list[str]) -> str:
    size = len(rows)
    span = size + QUIET * 2
    image = Image.new("L", (span, span), 255)
    for y, row in enumerate(rows):
        for x, bit in enumerate(row):
            if bit == "1":
                image.putpixel((x + QUIET, y + QUIET), 0)
    image = image.resize((span * SCALE, span * SCALE), Image.NEAREST)
    found = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode)
    assert len(found) == 1, f"no QR code read at size {size}"
    return found[0].text


def main() -> None:
    # Byte capacities at level M for versions 1–10: each boundary and one past it.
    capacities = [14, 26, 42, 62, 84, 106, 122, 152, 180, 213]
    texts = ["a", "https://example.com/talk", "Ünïcödé ✓ 日本語 🐕", "line one\nline two"]
    for cap in capacities:
        texts.append(("0123456789abcdef" * 14)[:cap])
        if cap < 213:
            texts.append(("https://example.org/" + "x" * 300)[:cap + 1])
    results = matrices(texts + ["z" * 214, "€" * 72])
    *encoded, too_long, multibyte_too_long = results
    assert too_long is None and multibyte_too_long is None, "texts over 213 bytes must not encode"
    for text, rows in zip(texts, encoded):
        assert rows is not None, f"{len(text.encode())} bytes did not encode"
        assert decode(rows) == text, f"round trip failed for {text[:40]!r}"
    sizes = sorted({len(rows) for rows in encoded})
    assert sizes == [v * 4 + 17 for v in range(1, 11)], sizes
    for text, rows in zip(texts, encoded):
        if len(text.encode()) in capacities:
            version = (len(rows) - 17) // 4
            assert capacities[version - 1] == len(text.encode()), f"{len(text.encode())} bytes chose version {version}"
    print(f"qr_code_test: {len(texts)} codes decoded, versions 1–10, limit enforced")


if __name__ == "__main__":
    assert re.search(r"QR_MAX_BYTES = 213;", encoder_source())
    main()
