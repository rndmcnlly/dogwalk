# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow>=12,<13"]
# ///
"""Make a bounded Whiteboard WebP: uv run --script board_rendition.py INPUT OUTPUT.

An explicit file conversion, not an upload or a change to the original image.
"""
import argparse
import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps

CAP = 1_500_000
Image.MAX_IMAGE_PIXELS = 16_000_000


def rendition(source: Path, target: Path) -> tuple[int, tuple[int, int]]:
    if source.resolve() == target.resolve() or target.exists():
        raise ValueError("choose a new output path, not an existing file")
    if target.suffix.lower() != ".webp":
        raise ValueError("output must end in .webp")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source) as original:
            if original.width * original.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError("input exceeds 16 megapixels")
            # The first frame is deliberate: a board rendition is a still image.
            image = ImageOps.exif_transpose(original).convert("RGBA" if "A" in original.getbands() or "transparency" in original.info else "RGB")
    image.thumbnail((2048, 2048))
    while True:
        for quality in (85, 70, 55):
            output = io.BytesIO()
            image.save(output, "WEBP", quality=quality, method=6)
            if output.tell() <= CAP:
                with target.open("xb") as destination:
                    destination.write(output.getvalue())
                return output.tell(), image.size
        image.thumbnail((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    size, dimensions = rendition(args.source, args.target)
    print(f"{args.target}: {dimensions[0]} × {dimensions[1]}, {size} bytes (still-image rendition)")
