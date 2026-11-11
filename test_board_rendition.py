# /// script
# requires-python = ">=3.12"
# dependencies = ["pillow>=12,<13"]
# ///
"""uv run --script test_board_rendition.py: synthetic files only, no Sandbox."""
import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from board_rendition import CAP, rendition


class RenditionTest(unittest.TestCase):
    def test_large_generated_image(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "generated.png", Path(folder) / "board.webp"
            image = Image.frombytes("RGB", (1280, 1600), random.Random(0).randbytes(1280 * 1600 * 3))
            image.save(source)
            original = source.read_bytes()
            self.assertGreater(len(original), 2_455_749)
            size, dimensions = rendition(source, target)
            self.assertLessEqual(size, CAP)
            self.assertEqual(dimensions, (1280, 1600))
            self.assertEqual(source.read_bytes(), original)
            with Image.open(target) as converted:
                converted.load()
                self.assertEqual(converted.size, dimensions)
            with self.assertRaises(ValueError):
                rendition(source, target)
            with self.assertRaises(ValueError):
                rendition(source, source)

    def test_transparent_icon(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "icon.png", Path(folder) / "board.webp"
            Image.new("RGBA", (20, 20), (12, 34, 56, 0)).save(source)
            rendition(source, target)
            with Image.open(target) as converted:
                self.assertEqual(converted.getpixel((0, 0))[3], 0)


if __name__ == "__main__":
    unittest.main()
