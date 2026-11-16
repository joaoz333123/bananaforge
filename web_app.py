"""BananaForge Web — Servidor FastAPI para rodar o BananaForge no navegador."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import time
from typing import Any

from dotenv import load_dotenv, set_key
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from genai_service import (
    MODELS,
    compute_interaction_cost,
    create_image_interaction,
    format_usage_summary,
)
from metadata_utils import (
    GenerationMetadata,
    format_metadata_badge,
    read_png_metadata,
    save_png_with_metadata,
)
from presets_manager import apply_preset_to_prompt, load_presets, save_preset

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
ENV_PATH = APP_DIR / ".env"
LOGS_DIR = APP_DIR / "logs"
WEB_REF_CACHE_DIR = LOGS_DIR / "web_ref_cache"
AUDIT_LOG_PATH = LOGS_DIR / "web_ui_audit.jsonl"
DEFAULT_OUTPUT_DIR = Path.home() / "Pictures" / "NanoBanana"

MAX_BUDGET_BRL = 10.00
RESOLUTIONS = ["1K", "2K", "4K"]
ASPECT_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4"]
BATCH_OPTIONS = [1, 2, 3, 4]
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

app = FastAPI(title="BananaForge Web", version="1.1.0")
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _get_api_key() -> str:
    load_dotenv(ENV_PATH, override=True)
    return os.getenv("GEMINI_API_KEY", "").strip()


def _mask_api_key(key: str) -> str:
    if not key:
        return "não configurada"
    if len(key) > 8:
        return f"{key[:4]}...{key[-4:]}"
    return "configurada"


def _resolve_output_dir(raw_dir: str | None) -> Path:
    text = (raw_dir or "").strip()
    return Path(text).expanduser() if text else DEFAULT_OUTPUT_DIR


def _read_audit_entries() -> list[dict[str, Any]]:
    if not AUDIT_LOG_PATH.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in AUDIT_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        clean = line.strip()
        if not clean:
            continue
        try:
            obj = json.loads(clean)
            if isinstance(obj, dict):
                entries.append(obj)
        except json.JSONDecodeError:
            continue
    return entries


def _get_cumulative_brl_spent() -> float:
    return round(sum(float(item.get("brl_cost", 0.0) or 0.0) for item in _read_audit_entries()), 6)


def _append_audit_log(entry: dict[str, Any]) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _validate_history_image(img_path_str: str, output_dir_str: str | None) -> Path:
    img_path = Path(img_path_str).expanduser().resolve()
    base_dir = _resolve_output_dir(output_dir_str).resolve()
    try:
        img_path.relative_to(base_dir)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Acesso negado fora da pasta de saída.") from exc
    if (
        not img_path.exists()
        or not img_path.is_file()
        or img_path.suffix.lower() not in ALLOWED_IMAGE_EXTS
    ):
        raise HTTPException(status_code=404, detail="Imagem não encontrada.")
    return img_path


def _meta_to_dict(meta: GenerationMetadata | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {
        "prompt": meta.prompt,
        "model_key": meta.model_key,
        "model_id": meta.model_id,
        "resolution": meta.resolution,
        "aspect_ratio": meta.aspect_ratio,
        "interaction_id": meta.interaction_id,
        "previous_interaction_id": meta.previous_interaction_id,
        "ref_count": meta.ref_count,
        "usage_summary": meta.usage_summary,
        "created_at": meta.created_at,
        "badge": format_metadata_badge(meta),
    }


class ApiKeyPayload(BaseModel):
    api_key: str = Field(min_length=1)


class PresetPayload(BaseModel):
    name: str = Field(min_length=1)
    suffix: str = Field(min_length=1)
    category: str = "Custom"


class RefImageData(BaseModel):
    name: str = "reference.png"
    data_url: str


class GeneratePayload(BaseModel):
    prompt: str = Field(min_length=1)
    model_key: str = "2.1"
    resolution: str = "2K"
    aspect_ratio: str = "1:1"
    batch_count: int = Field(default=1, ge=1, le=4)
    output_dir: str | None = None
    previous_interaction_id: str | None = None
    current_image_path: str | None = None
    ref_images: list[RefImageData] = Field(default_factory=list, max_length=4)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    key = _get_api_key()
    return {
        "api_key_configured": bool(key),
        "api_key_masked": _mask_api_key(key),
        "models": MODELS,
        "resolutions": RESOLUTIONS,
        "aspect_ratios": ASPECT_RATIOS,
        "batch_options": BATCH_OPTIONS,
        "default_output_dir": str(DEFAULT_OUTPUT_DIR),
        "presets": load_presets(),
        "max_ref_images": 4,
        "budget_limit_brl": MAX_BUDGET_BRL,
        "total_brl_spent": round(_get_cumulative_brl_spent(), 4),
    }


@app.get("/api/audit")
async def get_audit_log() -> dict[str, Any]:
    entries = _read_audit_entries()
    return {
        "total_entries": len(entries),
        "budget_limit_brl": MAX_BUDGET_BRL,
        "total_brl_spent": round(_get_cumulative_brl_spent(), 4),
        "entries": entries,
    }


@app.post("/api/api-key")
async def save_api_key(payload: ApiKeyPayload) -> dict[str, Any]:
    clean = payload.api_key.strip()
    if not clean:
        raise HTTPException(status_code=400, detail="API Key vazia.")
    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), "GEMINI_API_KEY", clean)
    os.environ["GEMINI_API_KEY"] = clean
    return {"api_key_configured": True, "api_key_masked": _mask_api_key(clean)}


@app.get("/api/presets")
async def get_presets() -> dict[str, Any]:
    return {"presets": load_presets()}


@app.post("/api/presets")
async def create_preset(payload: PresetPayload) -> dict[str, Any]:
    try:
        presets = save_preset(payload.name, payload.suffix, payload.category)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"presets": presets}


@app.post("/api/apply-preset")
async def apply_preset_endpoint(body: dict[str, str]) -> dict[str, str]:
    prompt = body.get("prompt", "")
    suffix = body.get("suffix", "")
    return {"prompt": apply_preset_to_prompt(prompt, suffix)}


@app.get("/api/history")
async def list_history(output_dir: str | None = Query(default=None)) -> dict[str, Any]:
    folder = _resolve_output_dir(output_dir)
    if not folder.exists():
        return {"output_dir": str(folder), "items": []}

    files = sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ALLOWED_IMAGE_EXTS),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    items = [
        {"name": p.name, "path": str(p), "mtime": p.stat().st_mtime}
        for p in files[:40]
    ]
    return {"output_dir": str(folder), "items": items}


@app.get("/api/history/image")
async def get_history_image(
    path: str = Query(...),
    output_dir: str | None = Query(default=None),
) -> FileResponse:
    img_path = _validate_history_image(path, output_dir)
    return FileResponse(str(img_path))


@app.get("/api/history/metadata")
async def get_history_metadata(
    path: str = Query(...),
    output_dir: str | None = Query(default=None),
) -> dict[str, Any]:
    img_path = _validate_history_image(path, output_dir)
    meta = read_png_metadata(img_path)
    return {"path": str(img_path), "metadata": _meta_to_dict(meta)}


@app.post("/api/generate")
async def generate_image_endpoint(payload: GeneratePayload) -> dict[str, Any]:
    api_key = _get_api_key()
    if not api_key:
        raise HTTPException(status_code=400, detail="Configure sua GEMINI_API_KEY antes de gerar.")

    if payload.model_key not in MODELS:
        raise HTTPException(status_code=400, detail=f"Modelo inválido: {payload.model_key}")
    if payload.resolution not in RESOLUTIONS:
        raise HTTPException(status_code=400, detail=f"Resolução inválida: {payload.resolution}")
    if payload.aspect_ratio not in ASPECT_RATIOS:
        raise HTTPException(status_code=400, detail=f"Proporção inválida: {payload.aspect_ratio}")

    spent_so_far = _get_cumulative_brl_spent()
    if spent_so_far >= MAX_BUDGET_BRL:
        raise HTTPException(
            status_code=400,
            detail=f"Orçamento de R$ {MAX_BUDGET_BRL:.2f} atingido (gasto atual: R$ {spent_so_far:.2f}).",
        )

    model_info = MODELS[payload.model_key]
    model_id = model_info["id"]
    out_dir = _resolve_output_dir(payload.output_dir)

    temp_ref_paths: list[Path] = []
    ref_names: list[str] = []
    if payload.ref_images:
        WEB_REF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for idx, ref in enumerate(payload.ref_images[:4]):
            raw_b64 = ref.data_url.split(",", 1)[1] if "," in ref.data_url else ref.data_url
            ext = Path(ref.name).suffix.lower()
            if ext not in ALLOWED_IMAGE_EXTS:
                ext = ".png"
            ref_file = WEB_REF_CACHE_DIR / f"web_ref_{int(time.time() * 1000)}_{idx}{ext}"
            ref_file.write_bytes(base64.b64decode(raw_b64))
            temp_ref_paths.append(ref_file)
            ref_names.append(ref.name)

    fallback_image_path: Path | None = None
    if payload.previous_interaction_id and payload.current_image_path:
        try:
            fallback_image_path = _validate_history_image(
                payload.current_image_path, payload.output_dir
            )
        except HTTPException:
            fallback_image_path = None

    batch_results: list[dict[str, Any]] = []
    total_started = time.perf_counter()

    try:
        for batch_idx in range(payload.batch_count):
            if _get_cumulative_brl_spent() >= MAX_BUDGET_BRL:
                raise HTTPException(
                    status_code=400,
                    detail="Orçamento máximo de R$ 10,00 atingido durante o lote.",
                )

            item_started = time.perf_counter()
            interaction = await asyncio.to_thread(
                create_image_interaction,
                api_key,
                model_id,
                payload.prompt,
                payload.resolution,
                payload.aspect_ratio,
                temp_ref_paths,
                payload.previous_interaction_id,
                fallback_image_path,
            )
            item_elapsed = time.perf_counter() - item_started
            output_img = getattr(interaction, "output_image", None)
            if not output_img or not getattr(output_img, "data", None):
                raise RuntimeError("A API não retornou nenhuma imagem na resposta.")

            image_bytes = base64.b64decode(output_img.data)
            inter_id = str(getattr(interaction, "id", "") or "")
            usage_summary = format_usage_summary(interaction, model_id)
            cost_info = compute_interaction_cost(interaction, model_id)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = f"_{batch_idx + 1}" if payload.batch_count > 1 else ""
            out_path = (
                out_dir
                / f"nanobanana_{model_info['prefix']}_{payload.resolution}_{timestamp}{suffix}.png"
            )
            if out_path.exists():
                out_path = (
                    out_dir
                    / f"nanobanana_{model_info['prefix']}_{payload.resolution}_{timestamp}_{batch_idx + 1}_{int(time.time() * 1000) % 1000}.png"
                )

            meta = GenerationMetadata(
                prompt=payload.prompt,
                model_key=payload.model_key,
                model_id=model_id,
                resolution=payload.resolution,
                aspect_ratio=payload.aspect_ratio,
                interaction_id=inter_id,
                previous_interaction_id=payload.previous_interaction_id,
                ref_count=len(temp_ref_paths),
                usage_summary=usage_summary,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            pil_img = await asyncio.to_thread(save_png_with_metadata, image_bytes, out_path, meta)

            new_total_brl = round(_get_cumulative_brl_spent() + float(cost_info["brl_cost"]), 6)
            _append_audit_log(
                {
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "status": "ok",
                    "model_key": payload.model_key,
                    "model_id": model_id,
                    "resolution": payload.resolution,
                    "aspect_ratio": payload.aspect_ratio,
                    "batch_count": payload.batch_count,
                    "batch_index": batch_idx + 1,
                    "prompt": payload.prompt,
                    "ref_names": ref_names,
                    "previous_interaction_id": payload.previous_interaction_id,
                    "interaction_id": inter_id,
                    "width": pil_img.width,
                    "height": pil_img.height,
                    "size_kb": round(len(image_bytes) / 1024, 1),
                    "elapsed_s": round(item_elapsed, 2),
                    "tokens": cost_info,
                    "brl_cost": cost_info["brl_cost"],
                    "usd_cost": cost_info["usd_cost"],
                    "cumulative_brl": new_total_brl,
                    "out_path": str(out_path),
                }
            )

            batch_results.append(
                {
                    "path": str(out_path),
                    "name": out_path.name,
                    "width": pil_img.width,
                    "height": pil_img.height,
                    "size_kb": round(len(image_bytes) / 1024, 1),
                    "elapsed_s": round(item_elapsed, 2),
                    "usage_summary": usage_summary,
                    "metadata": _meta_to_dict(meta),
                }
            )

        last_item = batch_results[-1]
        return {
            **last_item,
            "elapsed_s": round(time.perf_counter() - total_started, 2),
            "batch_count": len(batch_results),
            "batch_results": batch_results,
            "total_brl_spent": round(_get_cumulative_brl_spent(), 4),
        }
    except HTTPException as exc:
        _append_audit_log(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "status": "error",
                "model_key": payload.model_key,
                "model_id": model_id,
                "prompt": payload.prompt,
                "ref_names": ref_names,
                "previous_interaction_id": payload.previous_interaction_id,
                "error": str(exc.detail),
            }
        )
        raise
    except Exception as exc:
        _append_audit_log(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "status": "error",
                "model_key": payload.model_key,
                "model_id": model_id,
                "prompt": payload.prompt,
                "ref_names": ref_names,
                "previous_interaction_id": payload.previous_interaction_id,
                "error": str(exc),
            }
        )
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        for p in temp_ref_paths:
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web_app:app", host="127.0.0.1", port=8000, reload=False)
