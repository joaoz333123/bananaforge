"""BananaForge — Interface desktop para geração e edição de imagens via Gemini API.

Autor: JZ DEV
"""

from __future__ import annotations

import base64
import faulthandler
import json
import logging
import mimetypes
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from dotenv import load_dotenv, set_key
from google import genai
from PIL import Image

APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR / ".env"
LOGS_DIR = APP_DIR / "logs"
DEFAULT_OUTPUT_DIR = Path.home() / "Pictures" / "NanoBanana"

# Paleta de cores sóbria estilo estúdio técnico
BG_APP = "#131518"
BG_PANEL = "#1B1E23"
BG_INPUT = "#14171B"
BORDER_SUBTLE = "#2B3038"
TEXT_PRIMARY = "#F1F3F5"
TEXT_MUTED = "#9AA1AC"
ACCENT_AMBER = "#E5A93C"
ACCENT_AMBER_HOVER = "#CF952C"
ACCENT_TEXT = "#131518"
DANGER_COLOR = "#D9534F"

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

RESOLUTIONS = ["1K", "2K", "4K"]
ASPECT_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4"]


def _detect_mime_type(file_path: Path) -> str:
    """Detecta o MIME type da imagem anexada."""
    mime, _ = mimetypes.guess_type(str(file_path))
    if mime and mime.startswith("image/"):
        return mime
    return "image/png"


def _sanitize_for_log(obj: object, key_name: str = "") -> object:
    """Resume payloads binários/assinaturas longas para não travar o painel de log."""
    if isinstance(obj, dict):
        return {k: _sanitize_for_log(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_log(item, key_name) for item in obj]
    if isinstance(obj, str):
        if key_name in {"data", "signature"} and len(obj) > 120:
            return f"<{key_name}: {len(obj)} chars>"
        if len(obj) > 1000:
            return f"{obj[:300]}... <truncado: {len(obj)} chars>"
    return obj


def _format_api_response(interaction: object) -> str:
    """Serializa o objeto de retorno da API Gemini em JSON legível."""
    if hasattr(interaction, "model_dump"):
        raw = interaction.model_dump(exclude_none=True, mode="json")
    elif hasattr(interaction, "__dict__"):
        raw = vars(interaction)
    else:
        return repr(interaction)
    return json.dumps(_sanitize_for_log(raw), indent=2, ensure_ascii=False, default=str)


def _format_usage_summary(interaction: object, model_id: str) -> str:
    """Calcula o total de tokens (input + output) e o custo aproximado em BRL."""
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return "Tokens consumidos: indisponível (API não retornou usage) | Valor aprox.: R$ --"

    in_tok = getattr(usage, "total_input_tokens", None) or 0
    out_tok = getattr(usage, "total_output_tokens", None) or 0
    thought_tok = getattr(usage, "total_thought_tokens", None) or 0
    api_total = getattr(usage, "total_tokens", None) or 0
    total_consumed = (in_tok + out_tok) or api_total

    pricing = MODEL_PRICING_USD_PER_1M.get(model_id, {"input": 0.0, "output": 0.0})
    usd_cost = (in_tok / 1_000_000) * pricing["input"] + (out_tok / 1_000_000) * pricing["output"]
    brl_cost = usd_cost * USD_TO_BRL
    brl_str = f"{brl_cost:.2f}".replace(".", ",")

    extra = f" | thought: {thought_tok}" if thought_tok else ""
    return (
        f"Tokens consumidos: {total_consumed} (input: {in_tok} + output: {out_tok}{extra}) "
        f"| Valor aprox.: R$ {brl_str} (US$ {usd_cost:.2f})"
    )


def _build_interaction_input(prompt: str, ref_image_path: Path | None) -> str | list[dict]:
    """Monta o payload de entrada para text-to-image ou edição com imagem de referência."""
    if ref_image_path is None:
        return prompt
    image_bytes = ref_image_path.read_bytes()
    return [
        {"type": "text", "text": prompt},
        {
            "type": "image",
            "data": base64.b64encode(image_bytes).decode("utf-8"),
            "mime_type": _detect_mime_type(ref_image_path),
        },
    ]


class NanoBananaApp(ctk.CTk):
    """Janela principal do BananaForge (por JZ DEV)."""

    def __init__(self) -> None:
        super().__init__()
        load_dotenv(ENV_PATH)

        ctk.set_appearance_mode("dark")
        self.title("BananaForge — Gemini Image Studio • JZ DEV")
        self.geometry("1320x760")
        self.minsize(1100, 660)
        self.configure(fg_color=BG_APP)

        self.selected_model_key = ctk.StringVar(value="2.1")
        self.selected_resolution = ctk.StringVar(value="2K")
        self.selected_aspect = ctk.StringVar(value="1:1")
        self.output_dir_var = ctk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.api_key_status_var = ctk.StringVar()

        self.attached_image_path: Path | None = None
        self.last_generated_path: Path | None = None
        self.last_pil_image: Image.Image | None = None
        self.preview_ctk_image: ctk.CTkImage | None = None
        self.is_generating = False
        self.log_visible = False

        self._model_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._res_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._history_items: dict[Path, ctk.CTkFrame] = {}
        self._history_thumb_refs: list[ctk.CTkImage] = []
        self._thumb_cache: dict[tuple[Path, float], Image.Image] = {}
        self._history_refresh_seq = 0
        self._resize_after_id: str | None = None
        self._last_render_key: tuple[int, int, int] | None = None
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()
        self._last_ui_heartbeat = time.monotonic()
        self._mainloop_active = False

        self._logger, self._log_file_path, self._log_stream = self._setup_file_logging()
        self._install_exception_hooks()

        self._build_ui()
        self._refresh_api_key_status()
        self._log(f"Aplicativo iniciado. Log em arquivo: {self._log_file_path}")
        self._refresh_history()
        self.after(40, self._process_ui_queue)
        self._start_freeze_watchdog()

    def _setup_file_logging(self) -> tuple[logging.Logger, Path, object]:
        """Configura arquivo de log exclusivo para erros, freezes e lentidão do app Python."""
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = LOGS_DIR / f"app_errors_{stamp}.log"
        log_stream = open(log_path, "a", encoding="utf-8", buffering=1)

        try:
            faulthandler.enable(file=log_stream, all_threads=True)
        except Exception:
            pass

        logger = logging.getLogger(f"nanobanana.errors.{stamp}")
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s")
        )
        logger.addHandler(handler)
        return logger, log_path, log_stream

    def _install_exception_hooks(self) -> None:
        """Captura exceções não tratadas na thread principal, threads filhas e callbacks Tkinter."""
        def handle_sys_exc(exc_type, exc_value, exc_tb) -> None:
            tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
            self._logger.error("Exceção não tratada (main):\n%s", tb_text)

        def handle_thread_exc(args: threading.ExceptHookArgs) -> None:
            tb_text = "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            )
            self._logger.error("Exceção não tratada (%s):\n%s", args.thread.name, tb_text)

        sys.excepthook = handle_sys_exc
        threading.excepthook = handle_thread_exc

    def report_callback_exception(self, exc, val, tb) -> None:  # type: ignore[override]
        """Registra erros de callbacks do Tkinter no arquivo de log."""
        tb_text = "".join(traceback.format_exception(exc, val, tb))
        self._logger.error("Erro em callback Tkinter:\n%s", tb_text)

    def _start_freeze_watchdog(self) -> None:
        """Monitora o loop da UI; se travar por >2.5s, grava stack trace de todas as threads."""
        def watchdog() -> None:
            dumped_current_freeze = False
            while True:
                time.sleep(1.0)
                if not self._mainloop_active:
                    continue
                stall = time.monotonic() - self._last_ui_heartbeat
                if stall > 2.5 and not dumped_current_freeze:
                    dumped_current_freeze = True
                    self._logger.warning(
                        "FREEZE DETECTADO: thread principal sem resposta há %.2fs. "
                        "Dump de stack traces:",
                        stall,
                    )
                    try:
                        faulthandler.dump_traceback(file=self._log_stream, all_threads=True)
                        self._log_stream.flush()
                    except Exception as exc:
                        self._logger.error("Falha ao gravar dump de freeze: %s", exc)
                elif stall <= 1.0 and dumped_current_freeze:
                    dumped_current_freeze = False
                    self._logger.info("UI voltou a responder.")

        threading.Thread(target=watchdog, name="FreezeWatchdog", daemon=True).start()

    def _process_ui_queue(self) -> None:
        """Executa callbacks agendados por threads secundárias na thread principal do Tkinter."""
        self._mainloop_active = True
        self._last_ui_heartbeat = time.monotonic()
        while True:
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                self._logger.exception("Erro ao executar callback da fila de UI:")
        if self.winfo_exists():
            self.after(40, self._process_ui_queue)

    def _build_ui(self) -> None:
        """Constrói o layout dividido em controles, visualização e galeria de imagens geradas."""
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_columnconfigure(2, weight=0)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        self._build_topbar()
        self._build_controls_panel()
        self._build_preview_panel()
        self._build_history_panel()

    def _build_topbar(self) -> None:
        """Barra superior com título e configuração rápida da GEMINI_API_KEY."""
        topbar = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=0,
            border_width=1,
            border_color=BORDER_SUBTLE,
            height=54,
        )
        topbar.grid(row=0, column=0, columnspan=3, sticky="ew")
        topbar.grid_columnconfigure(1, weight=1)

        brand_frame = ctk.CTkFrame(topbar, fg_color="transparent")
        brand_frame.grid(row=0, column=0, padx=20, pady=10, sticky="w")

        title_lbl = ctk.CTkLabel(
            brand_frame,
            text="BANANAFORGE",
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=TEXT_PRIMARY,
        )
        title_lbl.pack(side="left")

        credit_lbl = ctk.CTkLabel(
            brand_frame,
            text="by JZ DEV",
            font=ctk.CTkFont(family="Segoe UI", size=11, weight="bold"),
            text_color=ACCENT_AMBER,
        )
        credit_lbl.pack(side="left", padx=(10, 0), pady=(2, 0))

        status_lbl = ctk.CTkLabel(
            topbar,
            textvariable=self.api_key_status_var,
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=TEXT_MUTED,
        )
        status_lbl.grid(row=0, column=1, padx=10, pady=12, sticky="e")

        key_btn = ctk.CTkButton(
            topbar,
            text="Configurar API Key",
            width=140,
            height=30,
            fg_color=BG_INPUT,
            hover_color="#252A32",
            border_width=1,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
            command=self._prompt_api_key,
        )
        key_btn.grid(row=0, column=2, padx=(0, 20), pady=12, sticky="e")

    def _build_controls_panel(self) -> None:
        """Painel esquerdo com seleção de modelo, resolução, prompt, anexo e pasta de saída."""
        panel = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
            width=430,
        )
        panel.grid(row=1, column=0, padx=(16, 8), pady=16, sticky="nsew")
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(5, weight=1)

        # 1. Modelo (checkboxes mutuamente exclusivos)
        ctk.CTkLabel(
            panel,
            text="MODELO",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, padx=18, pady=(16, 6), sticky="w")

        model_frame = ctk.CTkFrame(panel, fg_color="transparent")
        model_frame.grid(row=1, column=0, padx=18, pady=(0, 12), sticky="ew")

        for idx, (key, info) in enumerate(MODELS.items()):
            cb = ctk.CTkCheckBox(
                model_frame,
                text=info["label"],
                fg_color=ACCENT_AMBER,
                hover_color=ACCENT_AMBER_HOVER,
                checkmark_color=ACCENT_TEXT,
                border_color=BORDER_SUBTLE,
                text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(size=13),
                command=lambda k=key: self._select_model(k),
            )
            cb.grid(row=idx, column=0, pady=2, sticky="w")
            self._model_checkboxes[key] = cb
        self._sync_model_checkboxes()

        # 2. Resolução e Proporção
        res_header = ctk.CTkFrame(panel, fg_color="transparent")
        res_header.grid(row=2, column=0, padx=18, pady=(4, 6), sticky="ew")
        res_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            res_header,
            text="RESOLUÇÃO",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            res_header,
            text="PROPORÇÃO",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=1, sticky="e")

        res_row = ctk.CTkFrame(panel, fg_color="transparent")
        res_row.grid(row=3, column=0, padx=18, pady=(0, 12), sticky="ew")
        res_row.grid_columnconfigure(3, weight=1)

        for idx, res in enumerate(RESOLUTIONS):
            cb = ctk.CTkCheckBox(
                res_row,
                text=res,
                width=64,
                fg_color=ACCENT_AMBER,
                hover_color=ACCENT_AMBER_HOVER,
                checkmark_color=ACCENT_TEXT,
                border_color=BORDER_SUBTLE,
                text_color=TEXT_PRIMARY,
                font=ctk.CTkFont(size=13),
                command=lambda r=res: self._select_resolution(r),
            )
            cb.grid(row=0, column=idx, padx=(0, 8), sticky="w")
            self._res_checkboxes[res] = cb
        self._sync_res_checkboxes()

        aspect_menu = ctk.CTkOptionMenu(
            res_row,
            values=ASPECT_RATIOS,
            variable=self.selected_aspect,
            width=96,
            fg_color=BG_INPUT,
            button_color="#252A32",
            button_hover_color="#303640",
            text_color=TEXT_PRIMARY,
        )
        aspect_menu.grid(row=0, column=3, sticky="e")

        # 3. Prompt
        ctk.CTkLabel(
            panel,
            text="DESCRIÇÃO DA IMAGEM (PROMPT)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=4, column=0, padx=18, pady=(4, 6), sticky="w")

        self.prompt_box = ctk.CTkTextbox(
            panel,
            fg_color=BG_INPUT,
            border_width=1,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=13),
            wrap="word",
        )
        self.prompt_box.grid(row=5, column=0, padx=18, pady=(0, 12), sticky="nsew")
        self.prompt_box.bind("<Control-Return>", self._on_ctrl_enter)
        self.prompt_box.bind("<Control-KP_Enter>", self._on_ctrl_enter)
        self.bind("<Control-Return>", self._on_ctrl_enter)
        self.bind("<Control-KP_Enter>", self._on_ctrl_enter)

        # 4. Anexar imagem de referência
        attach_frame = ctk.CTkFrame(
            panel,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        attach_frame.grid(row=6, column=0, padx=18, pady=(0, 12), sticky="ew")
        attach_frame.grid_columnconfigure(1, weight=1)

        attach_btn = ctk.CTkButton(
            attach_frame,
            text="Anexar imagem",
            width=120,
            height=30,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            command=self._attach_image,
        )
        attach_btn.grid(row=0, column=0, padx=10, pady=8)

        self.attach_label = ctk.CTkLabel(
            attach_frame,
            text="Nenhuma imagem anexada",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.attach_label.grid(row=0, column=1, padx=4, pady=8, sticky="ew")

        self.clear_attach_btn = ctk.CTkButton(
            attach_frame,
            text="✕",
            width=28,
            height=28,
            fg_color="transparent",
            hover_color="#352024",
            text_color=DANGER_COLOR,
            command=self._clear_attachment,
        )

        # 5. Pasta para salvar
        ctk.CTkLabel(
            panel,
            text="PASTA PARA SALVAR",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=7, column=0, padx=18, pady=(2, 6), sticky="w")

        save_row = ctk.CTkFrame(panel, fg_color="transparent")
        save_row.grid(row=8, column=0, padx=18, pady=(0, 14), sticky="ew")
        save_row.grid_columnconfigure(0, weight=1)

        save_entry = ctk.CTkEntry(
            save_row,
            textvariable=self.output_dir_var,
            fg_color=BG_INPUT,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
        )
        save_entry.grid(row=0, column=0, padx=(0, 8), sticky="ew")

        browse_btn = ctk.CTkButton(
            save_row,
            text="Escolher...",
            width=90,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            command=self._choose_output_dir,
        )
        browse_btn.grid(row=0, column=1)

        # 6. Botão Gerar + Status
        self.generate_btn = ctk.CTkButton(
            panel,
            text="Gerar Imagem (Ctrl+Enter)",
            height=42,
            fg_color=ACCENT_AMBER,
            hover_color=ACCENT_AMBER_HOVER,
            text_color=ACCENT_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_generate_clicked,
        )
        self.generate_btn.grid(row=9, column=0, padx=18, pady=(0, 8), sticky="ew")

        self.status_label = ctk.CTkLabel(
            panel,
            text="Pronto para gerar.",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.status_label.grid(row=10, column=0, padx=18, pady=(0, 14), sticky="ew")

    def _build_preview_panel(self) -> None:
        """Painel direito para visualização da imagem gerada e ações de arquivo."""
        preview = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        preview.grid(row=1, column=1, padx=8, pady=16, sticky="nsew")
        preview.grid_columnconfigure(0, weight=1)
        preview.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(preview, fg_color="transparent")
        header.grid(row=0, column=0, padx=18, pady=(14, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="VISUALIZAÇÃO",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w")

        self.open_img_btn = ctk.CTkButton(
            header,
            text="Abrir imagem",
            width=110,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            state="disabled",
            command=self._open_last_image,
        )
        self.open_img_btn.grid(row=0, column=1, padx=(0, 8))

        self.open_folder_btn = ctk.CTkButton(
            header,
            text="Abrir pasta",
            width=100,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            command=self._open_output_folder,
        )
        self.open_folder_btn.grid(row=0, column=2, padx=(0, 8))

        self.toggle_log_btn = ctk.CTkButton(
            header,
            text="Mostrar log",
            width=100,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            command=self._toggle_log,
        )
        self.toggle_log_btn.grid(row=0, column=3)

        self.preview_container = ctk.CTkFrame(
            preview,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.preview_container.grid(row=1, column=0, padx=18, pady=(0, 10), sticky="nsew")
        self.preview_container.grid_columnconfigure(0, weight=1)
        self.preview_container.grid_rowconfigure(0, weight=1)
        self.preview_container.bind("<Configure>", self._on_preview_resize)

        self.preview_label = ctk.CTkLabel(
            self.preview_container,
            text="A imagem gerada aparecerá aqui.",
            font=ctk.CTkFont(size=13),
            text_color=TEXT_MUTED,
        )
        self.preview_label.grid(row=0, column=0, padx=12, pady=12, sticky="nsew")

        self.saved_path_label = ctk.CTkLabel(
            preview,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.saved_path_label.grid(row=2, column=0, padx=18, pady=(0, 10), sticky="ew")

        # Painel expansível de log
        self.log_frame = ctk.CTkFrame(
            preview,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.log_frame.grid(row=3, column=0, padx=18, pady=(0, 14), sticky="ew")
        self.log_frame.grid_columnconfigure(0, weight=1)

        log_header = ctk.CTkFrame(self.log_frame, fg_color="transparent")
        log_header.grid(row=0, column=0, padx=12, pady=(8, 4), sticky="ew")
        log_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            log_header,
            text="LOG DE EXECUÇÃO",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w")

        clear_log_btn = ctk.CTkButton(
            log_header,
            text="Limpar",
            width=64,
            height=22,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_MUTED,
            font=ctk.CTkFont(size=11),
            command=self._clear_log,
        )
        clear_log_btn.grid(row=0, column=1, sticky="e")

        self.log_box = ctk.CTkTextbox(
            self.log_frame,
            height=130,
            fg_color=BG_APP,
            border_width=1,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
            state="disabled",
        )
        self.log_box.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        self.log_frame.grid_remove()

    def _build_history_panel(self) -> None:
        """Painel lateral direito com miniaturas das imagens geradas para seleção rápida."""
        history = ctk.CTkFrame(
            self,
            fg_color=BG_PANEL,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
            width=235,
        )
        history.grid(row=1, column=2, padx=(8, 16), pady=16, sticky="nsew")
        history.grid_propagate(False)
        history.grid_columnconfigure(0, weight=1)
        history.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(history, fg_color="transparent")
        header.grid(row=0, column=0, padx=14, pady=(14, 8), sticky="ew")
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text="IMAGENS GERADAS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w")

        refresh_btn = ctk.CTkButton(
            header,
            text="↻",
            width=28,
            height=26,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            command=self._refresh_history,
        )
        refresh_btn.grid(row=0, column=1, sticky="e")

        self.history_scroll = ctk.CTkScrollableFrame(
            history,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.history_scroll.grid(row=1, column=0, padx=12, pady=(0, 14), sticky="nsew")
        self.history_scroll.grid_columnconfigure(0, weight=1)

    # --- Callbacks de seleção ---
    def _select_model(self, key: str) -> None:
        self.selected_model_key.set(key)
        self._sync_model_checkboxes()

    def _sync_model_checkboxes(self) -> None:
        active = self.selected_model_key.get()
        for key, cb in self._model_checkboxes.items():
            cb.select() if key == active else cb.deselect()

    def _select_resolution(self, res: str) -> None:
        self.selected_resolution.set(res)
        self._sync_res_checkboxes()

    def _sync_res_checkboxes(self) -> None:
        active = self.selected_resolution.get()
        for res, cb in self._res_checkboxes.items():
            cb.select() if res == active else cb.deselect()

    # --- API Key ---
    def _get_api_key(self) -> str:
        load_dotenv(ENV_PATH, override=True)
        return os.getenv("GEMINI_API_KEY", "").strip()

    def _refresh_api_key_status(self) -> None:
        key = self._get_api_key()
        if key:
            masked = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "configurada"
            self.api_key_status_var.set(f"API Key: {masked}")
        else:
            self.api_key_status_var.set("API Key: não configurada")

    def _prompt_api_key(self) -> None:
        dialog = ctk.CTkInputDialog(
            text="Cole sua GEMINI_API_KEY do Google AI Studio\n(será salva localmente em .env):",
            title="Configurar GEMINI_API_KEY",
        )
        new_key = dialog.get_input()
        if new_key is not None and new_key.strip():
            ENV_PATH.touch(exist_ok=True)
            set_key(str(ENV_PATH), "GEMINI_API_KEY", new_key.strip())
            os.environ["GEMINI_API_KEY"] = new_key.strip()
            self._refresh_api_key_status()

    # --- Arquivos e anexos ---
    def _attach_image(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecionar imagem de referência",
            filetypes=[("Imagens", "*.png *.jpg *.jpeg *.webp"), ("Todos os arquivos", "*.*")],
        )
        if not path:
            return
        self.attached_image_path = Path(path)
        self.attach_label.configure(text=self.attached_image_path.name, text_color=TEXT_PRIMARY)
        self.clear_attach_btn.grid(row=0, column=2, padx=(0, 8), pady=8)
        self._log(f"Imagem de referência anexada: {self.attached_image_path}")

    def _clear_attachment(self) -> None:
        self.attached_image_path = None
        self.attach_label.configure(text="Nenhuma imagem anexada", text_color=TEXT_MUTED)
        self.clear_attach_btn.grid_forget()
        self._log("Imagem de referência removida.")

    def _choose_output_dir(self) -> None:
        folder = filedialog.askdirectory(title="Escolher pasta para salvar imagens")
        if folder:
            self.output_dir_var.set(folder)
            self._log(f"Pasta de saída alterada: {folder}")
            self._refresh_history()

    def _open_last_image(self) -> None:
        if self.last_generated_path and self.last_generated_path.exists():
            os.startfile(str(self.last_generated_path))

    def _open_output_folder(self) -> None:
        folder = Path(self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    # --- Log ---
    def _toggle_log(self) -> None:
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.grid()
            self.toggle_log_btn.configure(text="Ocultar log")
        else:
            self.log_frame.grid_remove()
            self.toggle_log_btn.configure(text="Mostrar log")
        if self.last_pil_image is not None:
            self.after(50, self._render_preview)

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _log(self, message: str) -> None:
        """Registra uma linha com timestamp no painel de log da interface de forma thread-safe."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        if threading.current_thread() is threading.main_thread():
            self._append_log(line)
        else:
            self._ui_queue.put(lambda text=line: self._append_log(text))

    def _append_log(self, line: str) -> None:
        ui_text = (
            line
            if len(line) <= 8000
            else f"{line[:8000]}\n... [texto longo truncado na UI; veja o arquivo em logs/]\n"
        )
        self.log_box.configure(state="normal")
        self.log_box.insert("end", ui_text)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # --- Galeria de imagens geradas ---
    def _refresh_history(self) -> None:
        """Recarrega as miniaturas em thread separada usando cache para não travar a UI."""
        self._history_refresh_seq += 1
        seq = self._history_refresh_seq
        out_dir = Path(self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR)

        def worker() -> None:
            if not out_dir.exists():
                self._ui_queue.put(lambda: self._apply_history_items(seq, [], "Nenhuma imagem ainda."))
                return

            exts = {".png", ".jpg", ".jpeg", ".webp"}
            files = sorted(
                (p for p in out_dir.iterdir() if p.is_file() and p.suffix.lower() in exts),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not files:
                self._ui_queue.put(lambda: self._apply_history_items(seq, [], "Nenhuma imagem na pasta."))
                return

            entries: list[tuple[Path, Image.Image | None]] = []
            for img_path in files[:40]:
                try:
                    mtime = img_path.stat().st_mtime
                    cache_key = (img_path, mtime)
                    thumb = self._thumb_cache.get(cache_key)
                    if thumb is None:
                        with Image.open(img_path) as im:
                            im.draft("RGB", (200, 200))
                            im.thumbnail((176, 120))
                            thumb = im.copy()
                        self._thumb_cache[cache_key] = thumb
                    entries.append((img_path, thumb))
                except Exception:
                    entries.append((img_path, None))

            self._ui_queue.put(lambda: self._apply_history_items(seq, entries, ""))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_history_items(
        self,
        seq: int,
        entries: list[tuple[Path, Image.Image | None]],
        empty_msg: str,
    ) -> None:
        """Renderiza os cards de miniaturas na thread principal."""
        if seq != self._history_refresh_seq:
            return

        for widget in self.history_scroll.winfo_children():
            widget.destroy()
        self._history_items.clear()
        self._history_thumb_refs.clear()

        if not entries:
            ctk.CTkLabel(
                self.history_scroll,
                text=empty_msg,
                font=ctk.CTkFont(size=12),
                text_color=TEXT_MUTED,
            ).grid(row=0, column=0, pady=18, padx=8)
            return

        for idx, (img_path, thumb) in enumerate(entries):
            is_active = img_path == self.last_generated_path
            card = ctk.CTkFrame(
                self.history_scroll,
                fg_color=BG_PANEL,
                corner_radius=8,
                border_width=2 if is_active else 1,
                border_color=ACCENT_AMBER if is_active else BORDER_SUBTLE,
                cursor="hand2",
            )
            card.grid(row=idx, column=0, padx=4, pady=(4, 6), sticky="ew")
            card.grid_columnconfigure(0, weight=1)

            if thumb is not None:
                ctk_thumb = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
                self._history_thumb_refs.append(ctk_thumb)
                img_lbl = ctk.CTkLabel(card, image=ctk_thumb, text="")
            else:
                img_lbl = ctk.CTkLabel(card, text="[Erro]", text_color=DANGER_COLOR)
            img_lbl.grid(row=0, column=0, padx=6, pady=(6, 4))

            name_lbl = ctk.CTkLabel(
                card,
                text=img_path.name,
                font=ctk.CTkFont(size=10),
                text_color=TEXT_MUTED,
                wraplength=172,
            )
            name_lbl.grid(row=1, column=0, padx=6, pady=(0, 6))

            for widget in (card, img_lbl, name_lbl):
                widget.bind("<Button-1>", lambda _e, p=img_path: self._select_history_image(p))

            self._history_items[img_path] = card

    def _select_history_image(self, img_path: Path) -> None:
        """Carrega uma imagem da galeria para o painel de visualização principal."""
        if not img_path.exists():
            self._refresh_history()
            return
        try:
            with Image.open(img_path) as im:
                pil_img = im.copy()
        except Exception as exc:
            self._log(f"ERRO ao abrir imagem da galeria ({img_path.name}): {exc}")
            return

        self.last_generated_path = img_path
        self.last_pil_image = pil_img
        self.open_img_btn.configure(state="normal")
        self.saved_path_label.configure(text=f"Visualizando: {img_path}")
        self._highlight_history_item(img_path)
        self._render_preview()
        self._log(f"Imagem selecionada na galeria: {img_path.name}")

    def _highlight_history_item(self, active_path: Path) -> None:
        for path, card in self._history_items.items():
            is_active = path == active_path
            card.configure(
                border_width=2 if is_active else 1,
                border_color=ACCENT_AMBER if is_active else BORDER_SUBTLE,
            )

    # --- Geração de imagem ---
    def _on_ctrl_enter(self, _event=None) -> str:
        self._on_generate_clicked()
        return "break"

    def _on_generate_clicked(self) -> None:
        if self.is_generating:
            return

        api_key = self._get_api_key()
        if not api_key:
            messagebox.showwarning(
                "API Key necessária",
                "Configure sua GEMINI_API_KEY no botão do topo antes de gerar.",
            )
            return

        prompt = self.prompt_box.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning("Prompt vazio", "Escreva o que você deseja gerar.")
            return

        model_key = self.selected_model_key.get()
        model_id = MODELS[model_key]["id"]
        resolution = self.selected_resolution.get()
        aspect_ratio = self.selected_aspect.get()
        out_dir = Path(self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR)
        ref_path = self.attached_image_path

        self.is_generating = True
        self.generate_btn.configure(state="disabled", text="Gerando...")
        self.status_label.configure(
            text=f"Gerando com {model_id} ({resolution}, {aspect_ratio})...",
            text_color=ACCENT_AMBER,
        )
        ref_info = ref_path.name if ref_path else "nenhuma"
        self._log(
            f"Iniciando geração | modelo={model_id} | res={resolution} | "
            f"proporção={aspect_ratio} | ref={ref_info}"
        )

        worker = threading.Thread(
            target=self._run_generation,
            args=(api_key, model_id, prompt, resolution, aspect_ratio, out_dir, ref_path),
            daemon=True,
        )
        worker.start()

    def _run_generation(
        self,
        api_key: str,
        model_id: str,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        out_dir: Path,
        ref_path: Path | None,
    ) -> None:
        """Executa a chamada à API em thread separada para não travar a interface."""
        try:
            started_at = time.perf_counter()
            client = genai.Client(api_key=api_key)
            interaction_input = _build_interaction_input(prompt, ref_path)
            self._log("Enviando requisição para client.interactions.create...")
            interaction = client.interactions.create(
                model=model_id,
                input=interaction_input,
                response_format={
                    "type": "image",
                    "aspect_ratio": aspect_ratio,
                    "image_size": resolution,
                },
            )
            elapsed = time.perf_counter() - started_at
            status = getattr(interaction, "status", "unknown")
            inter_id = getattr(interaction, "id", "-")
            out_text = (getattr(interaction, "output_text", "") or "").strip()
            api_errors = getattr(interaction, "errors", None)
            if api_errors:
                self._logger.error("API retornou errors (%s): %s", inter_id, api_errors)
            self._log(f"API concluída em {elapsed:.2f}s | status={status} | id={inter_id}")
            if out_text:
                self._log(f"Texto da API: {out_text[:240]}")
            self._log(_format_usage_summary(interaction, model_id))

            output_img = getattr(interaction, "output_image", None)
            if not output_img or not getattr(output_img, "data", None):
                raise RuntimeError("A API não retornou nenhuma imagem na resposta.")

            image_bytes = base64.b64decode(output_img.data)
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            prefix = next((info["prefix"] for info in MODELS.values() if info["id"] == model_id), "nb")
            out_path = out_dir / f"nanobanana_{prefix}_{resolution}_{timestamp}.png"
            out_path.write_bytes(image_bytes)

            with Image.open(BytesIO(image_bytes)) as im:
                im.load()
                pil_img = im.copy()
            self._log(
                f"Resposta recebida em {elapsed:.2f}s | {pil_img.width}x{pil_img.height}px | "
                f"{len(image_bytes) / 1024:.1f} KB"
            )
            self._ui_queue.put(lambda p=out_path, img=pil_img: self._on_generation_success(p, img))
        except Exception as exc:
            self._logger.exception("Falha na geração de imagem:")
            self._ui_queue.put(lambda err=str(exc): self._on_generation_error(err))

    def _on_generation_success(self, out_path: Path, pil_img: Image.Image) -> None:
        t0 = time.perf_counter()
        self.is_generating = False
        self.generate_btn.configure(state="normal", text="Gerar Imagem (Ctrl+Enter)")
        self.last_generated_path = out_path
        self.last_pil_image = pil_img
        self.open_img_btn.configure(state="normal")

        self.status_label.configure(text="Imagem gerada com sucesso.", text_color="#6FCF97")
        self.saved_path_label.configure(text=f"Salva em: {out_path}")
        self._log(f"Imagem salva em: {out_path}")
        self._refresh_history()
        self._render_preview()
        dt_ms = (time.perf_counter() - t0) * 1000
        if dt_ms > 150:
            self._logger.warning("Passo lento em _on_generation_success: %.1f ms", dt_ms)

    def _on_generation_error(self, error_msg: str) -> None:
        self.is_generating = False
        self.generate_btn.configure(state="normal", text="Gerar Imagem (Ctrl+Enter)")
        self.status_label.configure(text="Erro ao gerar imagem.", text_color=DANGER_COLOR)
        self._log(f"ERRO: {error_msg}")
        messagebox.showerror("Erro na geração", error_msg)

    # --- Redimensionamento do preview ---
    def _on_preview_resize(self, _event=None) -> None:
        if self.last_pil_image is None:
            return
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after(80, self._render_preview)

    def _render_preview(self, force: bool = False) -> None:
        self._resize_after_id = None
        if self.last_pil_image is None:
            return
        max_w = max(self.preview_container.winfo_width() - 28, 200)
        max_h = max(self.preview_container.winfo_height() - 28, 200)

        img_w, img_h = self.last_pil_image.size
        scale = min(max_w / img_w, max_h / img_h, 1.0)
        disp_size = (max(int(img_w * scale), 1), max(int(img_h * scale), 1))

        render_key = (id(self.last_pil_image), disp_size[0], disp_size[1])
        if not force and self._last_render_key == render_key:
            return
        self._last_render_key = render_key

        preview_img = (
            self.last_pil_image
            if disp_size == (img_w, img_h)
            else self.last_pil_image.resize(disp_size, Image.Resampling.BILINEAR)
        )
        self.preview_ctk_image = ctk.CTkImage(
            light_image=preview_img,
            dark_image=preview_img,
            size=disp_size,
        )
        self.preview_label.configure(image=self.preview_ctk_image, text="")


if __name__ == "__main__":
    app = NanoBananaApp()
    app.mainloop()
