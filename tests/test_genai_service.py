"""Testes unitários para genai_service.py (multi-referência, multi-turno e expansão)."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from genai_service import (
    PROMPT_EXPANDER_MODEL,
    build_interaction_input,
    compute_interaction_cost,
    create_image_interaction,
    detect_mime_type,
    expand_prompt_with_gemini,
)


class TestGenaiService(unittest.TestCase):
    def test_build_interaction_input_no_refs(self) -> None:
        self.assertEqual(build_interaction_input("hello world", None), "hello world")
        self.assertEqual(build_interaction_input("hello world", []), "hello world")

    def test_build_interaction_input_single_and_multiple_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            img1 = Path(tmp) / "one.png"
            img2 = Path(tmp) / "two.jpg"
            img3 = Path(tmp) / "three.webp"
            img1.write_bytes(b"pngbytes")
            img2.write_bytes(b"jpgbytes")
            img3.write_bytes(b"webpbytes")

            single = build_interaction_input("edit single", img1)
            self.assertIsInstance(single, list)
            self.assertEqual(len(single), 2)
            self.assertEqual(single[0], {"type": "text", "text": "edit single"})
            self.assertEqual(
                single[1],
                {
                    "type": "image",
                    "data": base64.b64encode(b"pngbytes").decode("utf-8"),
                    "mime_type": "image/png",
                },
            )

            multi = build_interaction_input("combine refs", [img1, img2, img3])
            self.assertIsInstance(multi, list)
            self.assertEqual(len(multi), 4)
            self.assertEqual(multi[2]["mime_type"], "image/jpeg")
            self.assertEqual(multi[3]["mime_type"], "image/webp")

    @patch("genai_service.genai.Client")
    def test_create_image_interaction_with_and_without_previous_id(
        self, mock_client_cls: MagicMock
    ) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.interactions.create.return_value = SimpleNamespace(id="inter_new")

        # Sem previous_interaction_id
        create_image_interaction(
            api_key="test-key",
            model_id="gemini-nano-banana-2.1",
            prompt="a sunset",
            resolution="2K",
            aspect_ratio="16:9",
            ref_images=[],
            previous_interaction_id=None,
        )
        _, kwargs_first = mock_client.interactions.create.call_args
        self.assertNotIn("previous_interaction_id", kwargs_first)
        self.assertEqual(
            kwargs_first["response_format"],
            {"type": "image", "aspect_ratio": "16:9", "image_size": "2K"},
        )

        # Com previous_interaction_id
        create_image_interaction(
            api_key="test-key",
            model_id="gemini-nano-banana-2.1",
            prompt="make it rainy",
            resolution="2K",
            aspect_ratio="16:9",
            ref_images=[],
            previous_interaction_id="inter_prev_99",
        )
        _, kwargs_second = mock_client.interactions.create.call_args
        self.assertEqual(kwargs_second["previous_interaction_id"], "inter_prev_99")

    @patch("genai_service.genai.Client")
    def test_expand_prompt_with_gemini(self, mock_client_cls: MagicMock) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.interactions.create.return_value = SimpleNamespace(
            output_text='  "A warm sunlit ceramic coffee cup on walnut wood"  '
        )
        result = expand_prompt_with_gemini("test-key", "coffee cup")
        self.assertEqual(result, "A warm sunlit ceramic coffee cup on walnut wood")
        _, kwargs = mock_client.interactions.create.call_args
        self.assertEqual(kwargs["model"], PROMPT_EXPANDER_MODEL)
        self.assertIn("coffee cup", kwargs["input"])

    def test_detect_mime_type_reads_actual_image_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_png = Path(tmp) / "mislabeled.png"
            Image.new("RGB", (8, 8), "#112233").save(fake_png, format="JPEG")
            self.assertEqual(detect_mime_type(fake_png), "image/jpeg")

    def test_compute_interaction_cost(self) -> None:
        inter = SimpleNamespace(
            usage=SimpleNamespace(
                total_input_tokens=1000,
                total_output_tokens=1100,
                total_thought_tokens=200,
                total_tokens=2300,
            )
        )
        cost = compute_interaction_cost(inter, "gemini-3.1-flash-lite-image")
        self.assertEqual(cost["input_tokens"], 1000)
        self.assertEqual(cost["output_tokens"], 1100)
        self.assertGreater(cost["brl_cost"], 0.0)

    @patch("genai_service.genai.Client")
    def test_create_image_interaction_fallbacks_on_thought_block_error(
        self, mock_client_cls: MagicMock
    ) -> None:
        mock_client = mock_client_cls.return_value
        mock_client.interactions.create.side_effect = [
            RuntimeError("Model turns with images must start with a thought block"),
            SimpleNamespace(id="inter_fallback_ok"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            prev_img = Path(tmp) / "prev.png"
            Image.new("RGB", (8, 8), "#112233").save(prev_img, format="PNG")
            res = create_image_interaction(
                api_key="test-key",
                model_id="gemini-3.1-flash-lite-image",
                prompt="add grain",
                resolution="1K",
                aspect_ratio="1:1",
                ref_images=[],
                previous_interaction_id="inter_no_thought",
                fallback_image_path=prev_img,
            )
        self.assertEqual(res.id, "inter_fallback_ok")
        self.assertEqual(mock_client.interactions.create.call_count, 2)
        _, retry_kwargs = mock_client.interactions.create.call_args
        self.assertNotIn("previous_interaction_id", retry_kwargs)
        self.assertIsInstance(retry_kwargs["input"], list)


if __name__ == "__main__":
    unittest.main()
