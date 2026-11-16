"""Testes unitários para presets_manager.py."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from presets_manager import (
    DEFAULT_PRESETS,
    apply_preset_to_prompt,
    delete_preset,
    load_presets,
    save_preset,
)


class TestPresetsManager(unittest.TestCase):
    def test_load_creates_default_presets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "presets.json"
            loaded = load_presets(preset_path)
            self.assertTrue(preset_path.exists())
            self.assertEqual(len(loaded), len(DEFAULT_PRESETS))
            self.assertEqual(loaded[0]["name"], DEFAULT_PRESETS[0]["name"])

    def test_save_update_and_delete_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            preset_path = Path(tmp) / "presets.json"
            load_presets(preset_path)

            updated = save_preset("Meu Estilo", "neon noir, rain", "Custom", preset_path)
            self.assertTrue(any(p["name"] == "Meu Estilo" for p in updated))

            # Atualiza o mesmo preset (case-insensitive)
            updated2 = save_preset("meu estilo", "neon noir, fog", "Custom", preset_path)
            matches = [p for p in updated2 if p["name"].lower() == "meu estilo"]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["suffix"], "neon noir, fog")

            after_del = delete_preset("meu estilo", preset_path)
            self.assertFalse(any(p["name"].lower() == "meu estilo" for p in after_del))

    def test_apply_preset_to_prompt(self) -> None:
        self.assertEqual(
            apply_preset_to_prompt("", "studio lighting"),
            "studio lighting",
        )
        self.assertEqual(
            apply_preset_to_prompt("red sneaker,", "studio lighting"),
            "red sneaker, studio lighting",
        )
        # Evita duplicar sufixo já presente
        self.assertEqual(
            apply_preset_to_prompt("red sneaker, studio lighting", "studio lighting"),
            "red sneaker, studio lighting",
        )


if __name__ == "__main__":
    unittest.main()
