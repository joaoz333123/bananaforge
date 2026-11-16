"""Leitura e escrita de metadados de geração em chunks de texto PNG."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from io import BytesIO
from pathlib import Path

from PIL import Image, PngImagePlugin

META_KEY = "bananaforge_meta"


@dataclass(frozen=True)
class GenerationMetadata:
    """Metadados estruturados de uma imagem gerada pelo BananaForge."""

    prompt: str
    model_key: str
    model_id: str
    resolution: str
    aspect_ratio: str
    interaction_id: str
    previous_interaction_id: str | None = None
    ref_count: int = 0
    usage_summary: str = ""
    created_at: str = ""


def save_png_with_metadata(
    image_bytes: bytes,
    out_path: Path,
    meta: GenerationMetadata | None = None,
) -> Image.Image:
    """Salva uma imagem PNG em disco embutindo metadados JSON no chunk tEXt."""
    with Image.open(BytesIO(image_bytes)) as im:
        im.load()
        pil_img = im.copy()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if meta is not None:
        pnginfo = PngImagePlugin.PngInfo()
        payload = json.dumps(asdict(meta), ensure_ascii=False)
        pnginfo.add_text(META_KEY, payload)
        pil_img.save(out_path, format="PNG", pnginfo=pnginfo)
    else:
        pil_img.save(out_path, format="PNG")
    return pil_img


def read_png_metadata(img_path: Path) -> GenerationMetadata | None:
    """Lê os metadados embutidos de um arquivo PNG, retornando None se ausentes ou inválidos."""
    if not img_path.exists() or not img_path.is_file():
        return None
    try:
        with Image.open(img_path) as im:
            raw = im.info.get(META_KEY)
            if raw is None and hasattr(im, "text") and isinstance(im.text, dict):
                raw = im.text.get(META_KEY)
    except Exception:
        return None

    if not raw or not isinstance(raw, str):
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    valid_names = {f.name for f in fields(GenerationMetadata)}
    filtered = {k: v for k, v in data.items() if k in valid_names}
    defaults = {
        "prompt": "",
        "model_key": "2.1",
        "model_id": "",
        "resolution": "2K",
        "aspect_ratio": "1:1",
        "interaction_id": "",
        "previous_interaction_id": None,
        "ref_count": 0,
        "usage_summary": "",
        "created_at": "",
    }
    defaults.update(filtered)
    try:
        return GenerationMetadata(
            prompt=str(defaults["prompt"]),
            model_key=str(defaults["model_key"]),
            model_id=str(defaults["model_id"]),
            resolution=str(defaults["resolution"]),
            aspect_ratio=str(defaults["aspect_ratio"]),
            interaction_id=str(defaults["interaction_id"]),
            previous_interaction_id=(
                str(defaults["previous_interaction_id"])
                if defaults["previous_interaction_id"]
                else None
            ),
            ref_count=int(defaults["ref_count"] or 0),
            usage_summary=str(defaults["usage_summary"]),
            created_at=str(defaults["created_at"]),
        )
    except (TypeError, ValueError):
        return None


def format_metadata_badge(meta: GenerationMetadata) -> str:
    """Formata um resumo compacto dos metadados para exibição na interface."""
    prev_label = (
        f"{meta.previous_interaction_id[:12]}..."
        if meta.previous_interaction_id
        else "Não"
    )
    header = (
        f"Modelo: {meta.model_id or meta.model_key} | {meta.resolution} ({meta.aspect_ratio}) | "
        f"Refs: {meta.ref_count} | Turno ant.: {prev_label}"
    )
    clean_prompt = " ".join(meta.prompt.split())
    if len(clean_prompt) > 140:
        clean_prompt = f"{clean_prompt[:137]}..."
    if clean_prompt:
        return f"{header}\nPrompt: {clean_prompt}"
    return header
