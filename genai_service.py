"""Camada de serviço para chamadas à Gemini Interactions API (google-genai)."""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Sequence
from pathlib import Path

from google import genai

MODELS = {
    "2.1": {
        "id": "gemini-nano-banana-2.1",
        "prefix": "nb21",
        "label": "Nano Banana 2.1  (Novo • ~R$ 0,19/img)",
    },
    "2_lite": {
        "id": "gemini-3.1-flash-lite-image",
        "prefix": "nb2lite",
        "label": "Nano Banana 2 Lite  (Econômico • ~R$ 0,19/img)",
    },
    "2": {
        "id": "gemini-3.1-flash-image",
        "prefix": "nb2",
        "label": "Nano Banana 2  (Flash • ~R$ 0,38/img)",
    },
    "pro": {
        "id": "gemini-3-pro-image",
        "prefix": "pro",
        "label": "Nano Banana Pro  (Máxima qualidade • ~R$ 0,76/img)",
    },
    "1": {
        "id": "gemini-2.5-flash-image",
        "prefix": "nb1",
        "label": "Nano Banana  (2.5 Flash • ~R$ 0,22/img)",
    },
}

# Preços aproximados em USD por 1 milhão de tokens (Google AI Studio - Paid Tier)
MODEL_PRICING_USD_PER_1M = {
    "gemini-nano-banana-2.1": {"input": 1.50, "output": 30.00},
    "gemini-3.1-flash-lite-image": {"input": 0.25, "output": 30.00},
    "gemini-3.1-flash-image": {"input": 0.50, "output": 60.00},
    "gemini-3-pro-image": {"input": 2.00, "output": 120.00},
    "gemini-2.5-flash-image": {"input": 0.30, "output": 30.00},
}
USD_TO_BRL = 5.70
PROMPT_EXPANDER_MODEL = MODELS["2_lite"]["id"]


def detect_mime_type(file_path: Path) -> str:
    """Detecta o MIME type real da imagem (inspecionando bytes via PIL antes da extensão)."""
    try:
        from PIL import Image

        with Image.open(file_path) as im:
            fmt = (im.format or "").upper()
            if fmt == "PNG":
                return "image/png"
            if fmt in {"JPEG", "JPG"}:
                return "image/jpeg"
            if fmt == "WEBP":
                return "image/webp"
    except Exception:
        pass
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime and mime.startswith("image/"):
        return mime
    return "image/png"


def sanitize_for_log(obj: object, key_name: str = "") -> object:
    """Resume payloads binários/assinaturas longas para não travar o painel de log."""
    if isinstance(obj, dict):
        return {k: sanitize_for_log(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_for_log(item, key_name) for item in obj]
    if isinstance(obj, str):
        if key_name in {"data", "signature"} and len(obj) > 120:
            return f"<{key_name}: {len(obj)} chars>"
        if len(obj) > 1000:
            return f"{obj[:300]}... <truncado: {len(obj)} chars>"
    return obj


def format_api_response(interaction: object) -> str:
    """Serializa o objeto de retorno da API Gemini em JSON legível."""
    if hasattr(interaction, "model_dump"):
        raw = interaction.model_dump(exclude_none=True, mode="json")
    elif hasattr(interaction, "__dict__"):
        raw = vars(interaction)
    else:
        return repr(interaction)
    return json.dumps(sanitize_for_log(raw), indent=2, ensure_ascii=False, default=str)


def compute_interaction_cost(interaction: object, model_id: str) -> dict[str, float | int]:
    """Extrai contagem de tokens e custo estimado em USD e BRL de uma interação."""
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "thought_tokens": 0,
            "total_tokens": 0,
            "usd_cost": 0.0,
            "brl_cost": 0.0,
        }

    in_tok = int(getattr(usage, "total_input_tokens", None) or 0)
    out_tok = int(getattr(usage, "total_output_tokens", None) or 0)
    thought_tok = int(getattr(usage, "total_thought_tokens", None) or 0)
    api_total = int(getattr(usage, "total_tokens", None) or 0)
    total_consumed = (in_tok + out_tok) or api_total

    pricing = MODEL_PRICING_USD_PER_1M.get(model_id, {"input": 0.0, "output": 0.0})
    usd_cost = (in_tok / 1_000_000) * pricing["input"] + (out_tok / 1_000_000) * pricing["output"]
    brl_cost = usd_cost * USD_TO_BRL
    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "thought_tokens": thought_tok,
        "total_tokens": total_consumed,
        "usd_cost": round(usd_cost, 6),
        "brl_cost": round(brl_cost, 6),
    }


def format_usage_summary(interaction: object, model_id: str) -> str:
    """Calcula o total de tokens (input + output) e o custo aproximado em BRL."""
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return "Tokens consumidos: indisponível (API não retornou usage) | Valor aprox.: R$ --"

    cost_info = compute_interaction_cost(interaction, model_id)
    in_tok = int(cost_info["input_tokens"])
    out_tok = int(cost_info["output_tokens"])
    thought_tok = int(cost_info["thought_tokens"])
    total_consumed = int(cost_info["total_tokens"])
    usd_cost = float(cost_info["usd_cost"])
    brl_cost = float(cost_info["brl_cost"])
    brl_str = f"{brl_cost:.2f}".replace(".", ",")

    extra = f" | thought: {thought_tok}" if thought_tok else ""
    return (
        f"Tokens consumidos: {total_consumed} (input: {in_tok} + output: {out_tok}{extra}) "
        f"| Valor aprox.: R$ {brl_str} (US$ {usd_cost:.2f})"
    )


def build_interaction_input(
    prompt: str,
    ref_images: Sequence[Path] | Path | None = None,
) -> str | list[dict]:
    """Monta o payload de entrada para text-to-image ou edição com 1+ imagens de referência."""
    if ref_images is None:
        return prompt
    if isinstance(ref_images, Path):
        paths = [ref_images]
    else:
        paths = [p for p in ref_images if p is not None]

    if not paths:
        return prompt

    parts: list[dict] = [{"type": "text", "text": prompt}]
    for img_path in paths:
        image_bytes = img_path.read_bytes()
        parts.append(
            {
                "type": "image",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
                "mime_type": detect_mime_type(img_path),
            }
        )
    return parts


def create_image_interaction(
    api_key: str,
    model_id: str,
    prompt: str,
    resolution: str,
    aspect_ratio: str,
    ref_images: Sequence[Path] | Path | None = None,
    previous_interaction_id: str | None = None,
    fallback_image_path: Path | None = None,
) -> object:
    """Executa client.interactions.create com suporte a múltiplas referências e multi-turno."""
    client = genai.Client(api_key=api_key)
    interaction_input = build_interaction_input(prompt, ref_images)
    kwargs: dict[str, object] = {
        "model": model_id,
        "input": interaction_input,
        "response_format": {
            "type": "image",
            "aspect_ratio": aspect_ratio,
            "image_size": resolution,
        },
    }
    if previous_interaction_id:
        kwargs["previous_interaction_id"] = previous_interaction_id
    try:
        return client.interactions.create(**kwargs)
    except Exception as exc:
        if (
            previous_interaction_id
            and "thought block" in str(exc).lower()
            and fallback_image_path is not None
            and fallback_image_path.exists()
        ):
            existing_refs = (
                [ref_images]
                if isinstance(ref_images, Path)
                else [p for p in (ref_images or []) if p is not None]
            )
            fallback_refs = [
                fallback_image_path,
                *[p for p in existing_refs if p.resolve() != fallback_image_path.resolve()],
            ][:4]
            kwargs["input"] = build_interaction_input(prompt, fallback_refs)
            kwargs.pop("previous_interaction_id", None)
            return client.interactions.create(**kwargs)
        raise


def _extract_text_from_interaction(interaction: object) -> str:
    text = (getattr(interaction, "output_text", None) or "").strip()
    if text:
        return text
    outputs = getattr(interaction, "outputs", None)
    if isinstance(outputs, list):
        chunks: list[str] = []
        for item in outputs:
            item_text = getattr(item, "text", None)
            if isinstance(item_text, str) and item_text.strip():
                chunks.append(item_text.strip())
        if chunks:
            return "\n".join(chunks)
    return ""


def expand_prompt_with_gemini(
    api_key: str,
    raw_prompt: str,
    model_id: str = PROMPT_EXPANDER_MODEL,
) -> str:
    """Refina e detalha um prompt para geração de imagens preservando a intenção do usuário."""
    clean_prompt = raw_prompt.strip()
    if not clean_prompt:
        raise ValueError("Digite um prompt antes de expandir.")

    instruction = (
        "You are an expert prompt engineer for AI image generation (Gemini Nano Banana). "
        "Rewrite and enrich the following user prompt in clear, descriptive English so it produces "
        "a richer, visually coherent image, while strictly preserving the user's original subject, "
        "composition intent, and constraints. Add concrete details about lighting, framing, "
        "materials, and style only where helpful. Return ONLY the final prompt text, with no "
        "quotes, no markdown formatting, and no explanations.\n\n"
        f"User prompt: {clean_prompt}"
    )
    client = genai.Client(api_key=api_key)
    interaction = client.interactions.create(model=model_id, input=instruction)
    expanded = _extract_text_from_interaction(interaction).strip(" \n\r\"'")
    if not expanded:
        raise RuntimeError("O modelo de texto não retornou um prompt refinado.")
    return expanded
