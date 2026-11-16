"""Testes unitários para clipboard_utils.py."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from clipboard_utils import copy_image_to_clipboard, paste_image_from_clipboard


class TestClipboardUtils(unittest.TestCase):
    @patch("clipboard_utils.ImageGrab.grabclipboard")
    def test_paste_pil_image_creates_cached_png(self, mock_grab: MagicMock) -> None:
        mock_grab.return_value = Image.new("RGB", (24, 24), "#E5A93C")
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "clip_cache"
            result = paste_image_from_clipboard(cache_dir)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertTrue(result.exists())
            self.assertEqual(result.suffix, ".png")
            with Image.open(result) as im:
                self.assertEqual(im.size, (24, 24))

    @patch("clipboard_utils.ImageGrab.grabclipboard")
    def test_paste_file_list_filters_supported_images(self, mock_grab: MagicMock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            txt_file = Path(tmp) / "notes.txt"
            png_file = Path(tmp) / "ref.webp"
            txt_file.write_text("abc", encoding="utf-8")
            Image.new("RGB", (10, 10), "#112233").save(png_file, format="WEBP")

            mock_grab.return_value = [str(txt_file), str(png_file)]
            result = paste_image_from_clipboard(Path(tmp) / "cache")
            self.assertEqual(result, png_file)

    @patch("clipboard_utils.ImageGrab.grabclipboard")
    def test_paste_empty_returns_none(self, mock_grab: MagicMock) -> None:
        mock_grab.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(paste_image_from_clipboard(Path(tmp)))

    @patch("clipboard_utils.sys.platform", "linux")
    def test_copy_non_windows_returns_false(self) -> None:
        img = Image.new("RGB", (8, 8), "#000000")
        self.assertFalse(copy_image_to_clipboard(img))


if __name__ == "__main__":
    unittest.main()
