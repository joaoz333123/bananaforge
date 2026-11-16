"""Testes unitários para leitura e escrita de metadados PNG em metadata_utils.py."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from metadata_utils import (
    GenerationMetadata,
    format_metadata_badge,
    read_png_metadata,
    save_png_with_metadata,
)


def _make_sample_png_bytes(color: str = "#E5A93C") -> bytes:
    buf = BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return buf.getvalue()


class TestMetadataUtils(unittest.TestCase):
    def test_roundtrip_png_metadata(self) -> None:
        meta = GenerationMetadata(
            prompt="Minimalist ceramic mug on wooden desk",
            model_key="2.1",
            model_id="gemini-nano-banana-2.1",
            resolution="2K",
            aspect_ratio="16:9",
            interaction_id="inter_abc123456789",
            previous_interaction_id="inter_prev001",
            ref_count=2,
            usage_summary="Tokens consumidos: 1200",
            created_at="2026-11-16T14:15:00",
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "nested" / "sample.png"
            pil_img = save_png_with_metadata(_make_sample_png_bytes(), out_path, meta)
            self.assertEqual(pil_img.size, (32, 32))
            self.assertTrue(out_path.exists())

            loaded = read_png_metadata(out_path)
            self.assertEqual(loaded, meta)

            badge = format_metadata_badge(loaded)
            self.assertIn("gemini-nano-banana-2.1", badge)
            self.assertIn("2K (16:9)", badge)
            self.assertIn("Refs: 2", badge)
            self.assertIn("inter_prev00", badge)

    def test_read_plain_png_without_metadata_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain_path = Path(tmp) / "plain.png"
            plain_path.write_bytes(_make_sample_png_bytes("#131518"))
            self.assertIsNone(read_png_metadata(plain_path))

    def test_read_missing_file_returns_none(self) -> None:
        self.assertIsNone(read_png_metadata(Path("non_existent_image_12345.png")))


if __name__ == "__main__":
    unittest.main()
