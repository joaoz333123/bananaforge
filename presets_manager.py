"""Gerenciamento local de presets modulares de prompt para o BananaForge."""

from __future__ import annotations

import json
from pathlib import Path

PRESETS_FILE = Path(__file__).resolve().parent / "presets.json"

DEFAULT_PRESETS: list[dict[str, str]] = [
    {
        "name": "Foto de Produto (Estúdio)",
        "suffix": (
            "studio product photography, softbox lighting, clean neutral backdrop, "
            "sharp focus, 85mm lens, commercial quality"
        ),
        "category": "Fotografia",
    },
    {
        "name": "Cinematográfico 35mm",
        "suffix": (
            "cinematic 35mm film still, natural depth of field, volumetric atmospheric "
            "lighting, color graded, photorealistic"
        ),
        "category": "Fotografia",
    },
    {
        "name": "Ilustração Vetorial Flat",
        "suffix": (
            "clean flat vector illustration, minimal geometric shapes, balanced color "
            "palette, crisp edges, modern editorial style"
        ),
        "category": "Ilustração",
    },
    {
        "name": "Render Arquitetônico",
        "suffix": (
            "architectural visualization, physically based rendering, natural daylight, "
            "realistic materials, wide angle lens"
        ),
        "category": "3D & Render",
    },
    {
        "name": "Conceito Isométrico 3D",
        "suffix": (
            "isometric 3D diorama render, soft global illumination, clean clay and "
            "matte textures, studio background"
        ),
        "category": "3D & Render",
    },
]


def _normalize_presets(items: object) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return [dict(item) for item in DEFAULT_PRESETS]
    normalized: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        suffix = str(item.get("suffix", "")).strip()
        category = str(item.get("category", "Custom")).strip() or "Custom"
        if name and suffix:
            normalized.append({"name": name, "suffix": suffix, "category": category})
    return normalized


def _write_presets(presets: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(presets, indent=2, ensure_ascii=False), encoding="utf-8")


def load_presets(path: Path = PRESETS_FILE) -> list[dict[str, str]]:
    """Carrega os presets do arquivo JSON, criando-o com padrões se ainda não existir."""
    if not path.exists():
        defaults = [dict(item) for item in DEFAULT_PRESETS]
        _write_presets(defaults, path)
        return defaults
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        defaults = [dict(item) for item in DEFAULT_PRESETS]
        _write_presets(defaults, path)
        return defaults
    presets = _normalize_presets(raw)
    if not presets:
        presets = [dict(item) for item in DEFAULT_PRESETS]
        _write_presets(presets, path)
    return presets


def save_preset(
    name: str,
    suffix: str,
    category: str = "Custom",
    path: Path = PRESETS_FILE,
) -> list[dict[str, str]]:
    """Salva ou atualiza um preset pelo nome e persiste no disco."""
    clean_name = name.strip()
    clean_suffix = suffix.strip()
    clean_category = category.strip() or "Custom"
    if not clean_name or not clean_suffix:
        raise ValueError("Nome e texto do preset não podem ser vazios.")

    presets = load_presets(path)
    updated = False
    for entry in presets:
        if entry["name"].casefold() == clean_name.casefold():
            entry["name"] = clean_name
            entry["suffix"] = clean_suffix
            entry["category"] = clean_category
            updated = True
            break
    if not updated:
        presets.append(
            {"name": clean_name, "suffix": clean_suffix, "category": clean_category}
        )
    _write_presets(presets, path)
    return presets


def delete_preset(name: str, path: Path = PRESETS_FILE) -> list[dict[str, str]]:
    """Remove um preset pelo nome e atualiza o arquivo JSON."""
    clean_name = name.strip().casefold()
    presets = load_presets(path)
    remaining = [p for p in presets if p["name"].casefold() != clean_name]
    _write_presets(remaining, path)
    return remaining


def apply_preset_to_prompt(base_prompt: str, suffix: str) -> str:
    """Combina o prompt base com o sufixo do preset sem duplicação ou vírgulas extras."""
    clean_suffix = suffix.strip().strip(",")
    if not clean_suffix:
        return base_prompt.strip()

    clean_base = base_prompt.strip()
    if not clean_base:
        return clean_suffix

    if clean_suffix.casefold() in clean_base.casefold():
        return clean_base

    trimmed_base = clean_base.rstrip(" ,.;")
    return f"{trimmed_base}, {clean_suffix}"
