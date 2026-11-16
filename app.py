"""BananaForge — Interface desktop para geração e edição de imagens via Gemini API.

Autor: JZ DEV
"""

from __future__ import annotations

import faulthandler
import logging
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from dotenv import load_dotenv, set_key
from PIL import Image

from clipboard_utils import copy_image_to_clipboard, paste_image_from_clipboard
from genai_service import (
    MODEL_PRICING_USD_PER_1M,
    MODELS,
    PROMPT_EXPANDER_MODEL,
    USD_TO_BRL,
    build_interaction_input as _build_interaction_input,
    create_image_interaction,
    detect_mime_type as _detect_mime_type,
    expand_prompt_with_gemini,
    format_api_response as _format_api_response,
    format_usage_summary as _format_usage_summary,
    sanitize_for_log as _sanitize_for_log,
)
from metadata_utils import (
    GenerationMetadata,
    format_metadata_badge,
    read_png_metadata,
    save_png_with_metadata,
)
from presets_manager import apply_preset_to_prompt, load_presets, save_preset

APP_DIR = Path(__file__).resolve().parent
ENV_PATH = APP_DIR / ".env"
LOGS_DIR = APP_DIR / "logs"
CLIPBOARD_CACHE_DIR = LOGS_DIR / "clipboard_cache"
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

RESOLUTIONS = ["1K", "2K", "4K"]
ASPECT_RATIOS = ["1:1", "16:9", "9:16", "4:3", "3:4"]
MAX_REF_IMAGES = 4
PRESET_PLACEHOLDER = "Aplicar preset..."

__all__ = [
    "APP_DIR",
    "DEFAULT_OUTPUT_DIR",
    "ENV_PATH",
    "LOGS_DIR",
    "MODEL_PRICING_USD_PER_1M",
    "MODELS",
    "NanoBananaApp",
    "USD_TO_BRL",
    "_build_interaction_input",
    "_detect_mime_type",
    "_format_api_response",
    "_format_usage_summary",
    "_sanitize_for_log",
]


class NanoBananaApp(ctk.CTk):
    """Janela principal do BananaForge (por JZ DEV)."""

    def __init__(self) -> None:
        super().__init__()
        load_dotenv(ENV_PATH)

        ctk.set_appearance_mode("dark")
        self.title("BananaForge — Gemini Image Studio • JZ DEV")
        self.geometry("1340x800")
        self.minsize(1100, 660)
        self.configure(fg_color=BG_APP)

        self.selected_model_key = ctk.StringVar(value="2.1")
        self.selected_resolution = ctk.StringVar(value="2K")
        self.selected_aspect = ctk.StringVar(value="1:1")
        self.output_dir_var = ctk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.api_key_status_var = ctk.StringVar()
        self.multi_turn_var = ctk.BooleanVar(value=False)
        self.selected_preset_var = ctk.StringVar(value=PRESET_PLACEHOLDER)

        self.attached_image_paths: list[Path] = []
        self.current_interaction_id: str | None = None
        self.current_metadata: GenerationMetadata | None = None
        self.presets: list[dict[str, str]] = load_presets()
        self.last_generated_path: Path | None = None
        self.last_pil_image: Image.Image | None = None
        self.preview_ctk_image: ctk.CTkImage | None = None
        self.is_generating = False
        self.is_expanding = False
        self.log_visible = False

        self._model_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._res_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        self._ref_thumb_refs: list[ctk.CTkImage] = []
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
        self._update_turn_status_label()
        self._render_ref_chips()
        self._log(f"Aplicativo iniciado. Log em arquivo: {self._log_file_path}")
        self._refresh_history()
        self.after(40, self._process_ui_queue)
        self._start_freeze_watchdog()

    @property
    def attached_image_path(self) -> Path | None:
        """Compatibilidade para leitura da primeira imagem anexada."""
        return self.attached_image_paths[0] if self.attached_image_paths else None

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
        """Painel esquerdo com seleção de modelo, presets, prompt, multi-turno, referências e saída."""
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
        panel.grid_rowconfigure(6, weight=1)

        # 1. Modelo
        ctk.CTkLabel(
            panel,
            text="MODELO",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, padx=18, pady=(14, 4), sticky="w")

        model_frame = ctk.CTkFrame(panel, fg_color="transparent")
        model_frame.grid(row=1, column=0, padx=18, pady=(0, 10), sticky="ew")

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
        res_header.grid(row=2, column=0, padx=18, pady=(2, 4), sticky="ew")
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
        res_row.grid(row=3, column=0, padx=18, pady=(0, 10), sticky="ew")
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

        # 3. Prompt + Presets + Expansão (Melhoria 5)
        prompt_header = ctk.CTkFrame(panel, fg_color="transparent")
        prompt_header.grid(row=4, column=0, padx=18, pady=(2, 4), sticky="ew")
        prompt_header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            prompt_header,
            text="DESCRIÇÃO DA IMAGEM (PROMPT)",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w")

        self.expand_prompt_btn = ctk.CTkButton(
            prompt_header,
            text="Expandir Prompt",
            width=116,
            height=24,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=ACCENT_AMBER,
            font=ctk.CTkFont(size=11, weight="bold"),
            command=self._on_expand_prompt_clicked,
        )
        self.expand_prompt_btn.grid(row=0, column=1, sticky="e")

        presets_bar = ctk.CTkFrame(panel, fg_color="transparent")
        presets_bar.grid(row=5, column=0, padx=18, pady=(0, 6), sticky="ew")
        presets_bar.grid_columnconfigure(0, weight=1)

        self.preset_menu = ctk.CTkOptionMenu(
            presets_bar,
            values=self._preset_menu_values(),
            variable=self.selected_preset_var,
            height=28,
            fg_color=BG_INPUT,
            button_color="#252A32",
            button_hover_color="#303640",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
            command=self._on_preset_selected,
        )
        self.preset_menu.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        save_preset_btn = ctk.CTkButton(
            presets_bar,
            text="+ Preset",
            width=76,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11),
            command=self._save_current_as_preset,
        )
        save_preset_btn.grid(row=0, column=1, sticky="e")

        self.prompt_box = ctk.CTkTextbox(
            panel,
            fg_color=BG_INPUT,
            border_width=1,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=13),
            wrap="word",
        )
        self.prompt_box.grid(row=6, column=0, padx=18, pady=(0, 8), sticky="nsew")
        self.prompt_box.bind("<Control-Return>", self._on_ctrl_enter)
        self.prompt_box.bind("<Control-KP_Enter>", self._on_ctrl_enter)
        self.prompt_box._textbox.bind("<Control-v>", self._on_paste_shortcut)
        self.prompt_box._textbox.bind("<Control-V>", self._on_paste_shortcut)
        self.bind("<Control-Return>", self._on_ctrl_enter)
        self.bind("<Control-KP_Enter>", self._on_ctrl_enter)
        self.bind("<Control-v>", self._on_paste_shortcut)
        self.bind("<Control-V>", self._on_paste_shortcut)

        # 4. Controle Multi-Turno (Melhoria 1)
        turn_frame = ctk.CTkFrame(
            panel,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        turn_frame.grid(row=7, column=0, padx=18, pady=(0, 8), sticky="ew")
        turn_frame.grid_columnconfigure(1, weight=1)

        self.multi_turn_checkbox = ctk.CTkCheckBox(
            turn_frame,
            text="Multi-turno",
            variable=self.multi_turn_var,
            width=104,
            fg_color=ACCENT_AMBER,
            hover_color=ACCENT_AMBER_HOVER,
            checkmark_color=ACCENT_TEXT,
            border_color=BORDER_SUBTLE,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._update_turn_status_label,
        )
        self.multi_turn_checkbox.grid(row=0, column=0, padx=(10, 6), pady=7, sticky="w")

        self.turn_status_label = ctk.CTkLabel(
            turn_frame,
            text="Sessão: nova",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.turn_status_label.grid(row=0, column=1, padx=4, pady=7, sticky="ew")

        self.reset_turn_btn = ctk.CTkButton(
            turn_frame,
            text="Nova sessão",
            width=86,
            height=24,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11),
            command=self._reset_conversation,
        )
        self.reset_turn_btn.grid(row=0, column=2, padx=(4, 8), pady=7, sticky="e")

        # 5. Múltiplas imagens de referência + Colar (Melhoria 3)
        attach_frame = ctk.CTkFrame(
            panel,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        attach_frame.grid(row=8, column=0, padx=18, pady=(0, 10), sticky="ew")
        attach_frame.grid_columnconfigure(0, weight=1)

        attach_top = ctk.CTkFrame(attach_frame, fg_color="transparent")
        attach_top.grid(row=0, column=0, padx=8, pady=(7, 4), sticky="ew")
        attach_top.grid_columnconfigure(2, weight=1)

        self.attach_btn = ctk.CTkButton(
            attach_top,
            text="Anexar imgs (0/4)",
            width=126,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=12),
            command=self._attach_image,
        )
        self.attach_btn.grid(row=0, column=0, padx=(0, 6))

        paste_btn = ctk.CTkButton(
            attach_top,
            text="Colar (Ctrl+V)",
            width=102,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11),
            command=self._paste_reference_image,
        )
        paste_btn.grid(row=0, column=1, padx=(0, 6))

        self.clear_attach_btn = ctk.CTkButton(
            attach_top,
            text="Limpar",
            width=60,
            height=28,
            fg_color="transparent",
            hover_color="#352024",
            text_color=DANGER_COLOR,
            font=ctk.CTkFont(size=11),
            command=self._clear_attachment,
        )
        self.clear_attach_btn.grid(row=0, column=2, sticky="e")

        self.ref_chips_frame = ctk.CTkFrame(attach_frame, fg_color="transparent")
        self.ref_chips_frame.grid(row=1, column=0, padx=8, pady=(0, 7), sticky="ew")

        # 6. Pasta para salvar
        ctk.CTkLabel(
            panel,
            text="PASTA PARA SALVAR",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=9, column=0, padx=18, pady=(0, 4), sticky="w")

        save_row = ctk.CTkFrame(panel, fg_color="transparent")
        save_row.grid(row=10, column=0, padx=18, pady=(0, 10), sticky="ew")
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

        # 7. Botão Gerar + Status
        self.generate_btn = ctk.CTkButton(
            panel,
            text="Gerar Imagem (Ctrl+Enter)",
            height=40,
            fg_color=ACCENT_AMBER,
            hover_color=ACCENT_AMBER_HOVER,
            text_color=ACCENT_TEXT,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_generate_clicked,
        )
        self.generate_btn.grid(row=11, column=0, padx=18, pady=(0, 6), sticky="ew")

        self.status_label = ctk.CTkLabel(
            panel,
            text="Pronto para gerar.",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.status_label.grid(row=12, column=0, padx=18, pady=(0, 12), sticky="ew")

    def _build_preview_panel(self) -> None:
        """Painel central para visualização da imagem, metadados e ações de arquivo."""
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

        self.copy_img_btn = ctk.CTkButton(
            header,
            text="Copiar imagem",
            width=112,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            state="disabled",
            command=self._copy_current_image,
        )
        self.copy_img_btn.grid(row=0, column=1, padx=(0, 8))

        self.open_img_btn = ctk.CTkButton(
            header,
            text="Abrir imagem",
            width=105,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            state="disabled",
            command=self._open_last_image,
        )
        self.open_img_btn.grid(row=0, column=2, padx=(0, 8))

        self.open_folder_btn = ctk.CTkButton(
            header,
            text="Abrir pasta",
            width=95,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            command=self._open_output_folder,
        )
        self.open_folder_btn.grid(row=0, column=3, padx=(0, 8))

        self.toggle_log_btn = ctk.CTkButton(
            header,
            text="Mostrar log",
            width=95,
            height=28,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            command=self._toggle_log,
        )
        self.toggle_log_btn.grid(row=0, column=4)

        self.preview_container = ctk.CTkFrame(
            preview,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.preview_container.grid(row=1, column=0, padx=18, pady=(0, 8), sticky="nsew")
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

        # Faixa de metadados e restauração rápida (Melhoria 2)
        self.meta_frame = ctk.CTkFrame(
            preview,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.meta_frame.grid(row=2, column=0, padx=18, pady=(0, 6), sticky="ew")
        self.meta_frame.grid_columnconfigure(0, weight=1)

        self.meta_info_label = ctk.CTkLabel(
            self.meta_frame,
            text="Metadados: selecione ou gere uma imagem para visualizar parâmetros.",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=420,
        )
        self.meta_info_label.grid(row=0, column=0, padx=10, pady=8, sticky="ew")

        meta_actions = ctk.CTkFrame(self.meta_frame, fg_color="transparent")
        meta_actions.grid(row=0, column=1, padx=(4, 8), pady=6, sticky="e")

        self.restore_meta_btn = ctk.CTkButton(
            meta_actions,
            text="Restaurar Config/Prompt",
            width=154,
            height=26,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(size=11),
            state="disabled",
            command=self._restore_from_metadata,
        )
        self.restore_meta_btn.grid(row=0, column=0, padx=(0, 6))

        self.use_as_ref_btn = ctk.CTkButton(
            meta_actions,
            text="Usar como Ref",
            width=96,
            height=26,
            fg_color="#252A32",
            hover_color="#313742",
            text_color=ACCENT_AMBER,
            font=ctk.CTkFont(size=11, weight="bold"),
            state="disabled",
            command=self._use_current_as_reference,
        )
        self.use_as_ref_btn.grid(row=0, column=1)

        self.saved_path_label = ctk.CTkLabel(
            preview,
            text="",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.saved_path_label.grid(row=3, column=0, padx=18, pady=(0, 8), sticky="ew")

        # Painel expansível de log
        self.log_frame = ctk.CTkFrame(
            preview,
            fg_color=BG_INPUT,
            corner_radius=8,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.log_frame.grid(row=4, column=0, padx=18, pady=(0, 14), sticky="ew")
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

    # --- Presets e Expansão de Prompt (Melhoria 5) ---
    def _preset_menu_values(self) -> list[str]:
        return [PRESET_PLACEHOLDER, *[p["name"] for p in self.presets]]

    def _on_preset_selected(self, choice: str) -> None:
        if choice == PRESET_PLACEHOLDER:
            return
        preset = next((p for p in self.presets if p["name"] == choice), None)
        if preset is None:
            self.selected_preset_var.set(PRESET_PLACEHOLDER)
            return
        current = self.prompt_box.get("1.0", "end").strip()
        updated = apply_preset_to_prompt(current, preset["suffix"])
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", updated)
        self.selected_preset_var.set(PRESET_PLACEHOLDER)
        self._log(f"Preset aplicado: {preset['name']}")

    def _save_current_as_preset(self) -> None:
        current = self.prompt_box.get("1.0", "end").strip()
        if not current:
            messagebox.showwarning(
                "Prompt vazio",
                "Digite o texto/estilo no prompt antes de salvar como preset.",
            )
            return
        dialog = ctk.CTkInputDialog(
            text="Nome do novo preset (salvará o texto atual do prompt):",
            title="Salvar Preset",
        )
        name = dialog.get_input()
        if name is None or not name.strip():
            return
        try:
            self.presets = save_preset(name.strip(), current)
            self.preset_menu.configure(values=self._preset_menu_values())
            self.selected_preset_var.set(PRESET_PLACEHOLDER)
            self._log(f"Preset salvo: {name.strip()}")
            self.status_label.configure(
                text=f"Preset '{name.strip()}' salvo.",
                text_color="#6FCF97",
            )
        except Exception as exc:
            messagebox.showerror("Erro ao salvar preset", str(exc))

    def _on_expand_prompt_clicked(self) -> None:
        if self.is_generating or self.is_expanding:
            return
        api_key = self._get_api_key()
        if not api_key:
            messagebox.showwarning(
                "API Key necessária",
                "Configure sua GEMINI_API_KEY no botão do topo antes de expandir o prompt.",
            )
            return
        raw_prompt = self.prompt_box.get("1.0", "end").strip()
        if not raw_prompt:
            messagebox.showwarning("Prompt vazio", "Escreva uma ideia base antes de expandir.")
            return

        self.is_expanding = True
        self.expand_prompt_btn.configure(state="disabled", text="Expandindo...")
        self.status_label.configure(
            text=f"Refinando prompt via {PROMPT_EXPANDER_MODEL}...",
            text_color=ACCENT_AMBER,
        )
        self._log(f"Expandindo prompt com {PROMPT_EXPANDER_MODEL}...")

        def worker() -> None:
            try:
                refined = expand_prompt_with_gemini(api_key, raw_prompt)
                self._ui_queue.put(lambda text=refined: self._on_expand_success(text))
            except Exception as exc:
                self._logger.exception("Falha ao expandir prompt:")
                self._ui_queue.put(lambda err=str(exc): self._on_expand_error(err))

        threading.Thread(target=worker, daemon=True).start()

    def _on_expand_success(self, refined_prompt: str) -> None:
        self.is_expanding = False
        self.expand_prompt_btn.configure(state="normal", text="Expandir Prompt")
        self.prompt_box.delete("1.0", "end")
        self.prompt_box.insert("1.0", refined_prompt)
        self.status_label.configure(
            text="Prompt refinado. Revise e clique em Gerar.",
            text_color="#6FCF97",
        )
        self._log("Prompt expandido com sucesso.")

    def _on_expand_error(self, error_msg: str) -> None:
        self.is_expanding = False
        self.expand_prompt_btn.configure(state="normal", text="Expandir Prompt")
        self.status_label.configure(text="Erro ao expandir prompt.", text_color=DANGER_COLOR)
        self._log(f"ERRO ao expandir prompt: {error_msg}")
        messagebox.showerror("Erro ao expandir prompt", error_msg)

    # --- Sessão Multi-Turno (Melhoria 1) ---
    def _update_turn_status_label(self) -> None:
        if self.current_interaction_id:
            short_id = f"{self.current_interaction_id[:12]}..."
            if self.multi_turn_var.get():
                self.turn_status_label.configure(
                    text=f"Ativa: {short_id}",
                    text_color=ACCENT_AMBER,
                )
            else:
                self.turn_status_label.configure(
                    text=f"Pronta ({short_id})",
                    text_color=TEXT_MUTED,
                )
        else:
            self.turn_status_label.configure(
                text="Sessão: nova",
                text_color=TEXT_MUTED,
            )

    def _reset_conversation(self) -> None:
        self.current_interaction_id = None
        self.multi_turn_var.set(False)
        self._update_turn_status_label()
        self._log("Sessão multi-turno resetada. Próxima geração iniciará nova conversa.")

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

    # --- Arquivos, Múltiplas Referências e Clipboard (Melhoria 3) ---
    def _attach_image(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Selecionar imagens de referência (até 4)",
            filetypes=[("Imagens", "*.png *.jpg *.jpeg *.webp"), ("Todos os arquivos", "*.*")],
        )
        if not paths:
            return
        self._add_reference_paths([Path(p) for p in paths])

    def _add_reference_paths(self, new_paths: list[Path]) -> None:
        existing = {p.resolve() for p in self.attached_image_paths if p.exists()}
        added: list[Path] = []
        skipped_limit = False
        for raw_path in new_paths:
            if not raw_path.exists() or not raw_path.is_file():
                continue
            resolved = raw_path.resolve()
            if resolved in existing:
                continue
            if len(self.attached_image_paths) >= MAX_REF_IMAGES:
                skipped_limit = True
                break
            self.attached_image_paths.append(raw_path)
            existing.add(resolved)
            added.append(raw_path)

        if added:
            self._render_ref_chips()
            names = ", ".join(p.name for p in added)
            self._log(f"Referência(s) adicionada(s): {names}")
        if skipped_limit:
            messagebox.showinfo(
                "Limite de referências",
                f"Máximo de {MAX_REF_IMAGES} imagens de referência por geração.",
            )

    def _remove_reference_path(self, target: Path) -> None:
        self.attached_image_paths = [p for p in self.attached_image_paths if p != target]
        self._render_ref_chips()
        self._log(f"Referência removida: {target.name}")

    def _clear_attachment(self) -> None:
        self.attached_image_paths.clear()
        self._render_ref_chips()
        self._log("Imagens de referência removidas.")

    def _render_ref_chips(self) -> None:
        for child in self.ref_chips_frame.winfo_children():
            child.destroy()
        self._ref_thumb_refs.clear()

        count = len(self.attached_image_paths)
        self.attach_btn.configure(text=f"Anexar imgs ({count}/{MAX_REF_IMAGES})")
        if count > 0:
            self.clear_attach_btn.configure(state="normal")
        else:
            self.clear_attach_btn.configure(state="disabled")
            ctk.CTkLabel(
                self.ref_chips_frame,
                text="Nenhuma referência (até 4 imgs • cole com Ctrl+V)",
                font=ctk.CTkFont(size=11),
                text_color=TEXT_MUTED,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=2, pady=2)
            return

        for idx, img_path in enumerate(self.attached_image_paths):
            chip = ctk.CTkFrame(
                self.ref_chips_frame,
                fg_color=BG_PANEL,
                corner_radius=6,
                border_width=1,
                border_color=BORDER_SUBTLE,
            )
            chip.grid(row=0, column=idx, padx=(0, 6), pady=2, sticky="w")

            try:
                with Image.open(img_path) as im:
                    thumb = im.convert("RGB")
                    thumb.thumbnail((34, 34))
                ctk_thumb = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
                self._ref_thumb_refs.append(ctk_thumb)
                ctk.CTkLabel(chip, image=ctk_thumb, text="").grid(
                    row=0, column=0, padx=(4, 3), pady=3
                )
            except Exception:
                ctk.CTkLabel(chip, text="IMG", font=ctk.CTkFont(size=9)).grid(
                    row=0, column=0, padx=(6, 3), pady=3
                )

            short_name = (
                img_path.name if len(img_path.name) <= 10 else f"{img_path.name[:8]}…"
            )
            ctk.CTkLabel(
                chip,
                text=short_name,
                font=ctk.CTkFont(size=10),
                text_color=TEXT_PRIMARY,
            ).grid(row=0, column=1, padx=(0, 2))

            ctk.CTkButton(
                chip,
                text="✕",
                width=20,
                height=20,
                fg_color="transparent",
                hover_color="#352024",
                text_color=DANGER_COLOR,
                font=ctk.CTkFont(size=10),
                command=lambda p=img_path: self._remove_reference_path(p),
            ).grid(row=0, column=2, padx=(0, 3), pady=2)

    def _on_paste_shortcut(self, event=None) -> str | None:
        """Se houver imagem no clipboard, anexa como referência; caso contrário, mantém colagem de texto."""
        is_textbox_event = event is not None and event.widget is self.prompt_box._textbox
        if event is not None and not is_textbox_event:
            focus_widget = self.focus_get()
            if focus_widget is self.prompt_box._textbox:
                return None

        pasted_path = paste_image_from_clipboard(CLIPBOARD_CACHE_DIR)
        if pasted_path is not None:
            self._add_reference_paths([pasted_path])
            self.status_label.configure(
                text=f"Imagem colada do clipboard: {pasted_path.name}",
                text_color="#6FCF97",
            )
            return "break"
        return None

    def _paste_reference_image(self) -> None:
        pasted_path = paste_image_from_clipboard(CLIPBOARD_CACHE_DIR)
        if pasted_path is None:
            messagebox.showinfo(
                "Área de transferência",
                "Nenhuma imagem válida encontrada na área de transferência.",
            )
            return
        self._add_reference_paths([pasted_path])
        self.status_label.configure(
            text=f"Imagem colada: {pasted_path.name}",
            text_color="#6FCF97",
        )

    def _copy_current_image(self) -> None:
        if self.last_pil_image is None:
            return
        ok = copy_image_to_clipboard(self.last_pil_image)
        if ok:
            self.status_label.configure(
                text="Imagem copiada para a área de transferência.",
                text_color="#6FCF97",
            )
            self._log("Imagem copiada para a área de transferência.")
        else:
            messagebox.showwarning(
                "Copiar imagem",
                "Não foi possível copiar a imagem para a área de transferência neste sistema.",
            )

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

    # --- Metadados e Restauração (Melhoria 2) ---
    def _update_metadata_panel(self, meta: GenerationMetadata | None, has_image: bool) -> None:
        self.current_metadata = meta
        self.use_as_ref_btn.configure(state="normal" if has_image else "disabled")
        if meta is None:
            self.restore_meta_btn.configure(state="disabled")
            msg = (
                "Metadados: não disponíveis nesta imagem (arquivo externo ou anterior)."
                if has_image
                else "Metadados: selecione ou gere uma imagem para visualizar parâmetros."
            )
            self.meta_info_label.configure(text=msg, text_color=TEXT_MUTED)
            return

        self.restore_meta_btn.configure(state="normal")
        self.meta_info_label.configure(
            text=format_metadata_badge(meta),
            text_color=TEXT_PRIMARY,
        )

    def _restore_from_metadata(self) -> None:
        meta = self.current_metadata
        if meta is None:
            return
        if meta.prompt:
            self.prompt_box.delete("1.0", "end")
            self.prompt_box.insert("1.0", meta.prompt)
        if meta.model_key in MODELS:
            self._select_model(meta.model_key)
        if meta.resolution in RESOLUTIONS:
            self._select_resolution(meta.resolution)
        if meta.aspect_ratio in ASPECT_RATIOS:
            self.selected_aspect.set(meta.aspect_ratio)
        if meta.interaction_id:
            self.current_interaction_id = meta.interaction_id
            self._update_turn_status_label()
        self.status_label.configure(
            text="Prompt e configurações restaurados dos metadados.",
            text_color="#6FCF97",
        )
        self._log(f"Metadados restaurados (modelo={meta.model_key}, res={meta.resolution}).")

    def _use_current_as_reference(self) -> None:
        if self.last_generated_path and self.last_generated_path.exists():
            self._add_reference_paths([self.last_generated_path])

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
        """Carrega uma imagem da galeria e seus metadados para o painel principal."""
        if not img_path.exists():
            self._refresh_history()
            return
        try:
            with Image.open(img_path) as im:
                im.load()
                pil_img = im.copy()
        except Exception as exc:
            self._log(f"ERRO ao abrir imagem da galeria ({img_path.name}): {exc}")
            return

        meta = read_png_metadata(img_path)
        self.last_generated_path = img_path
        self.last_pil_image = pil_img
        self.open_img_btn.configure(state="normal")
        self.copy_img_btn.configure(state="normal")
        self.saved_path_label.configure(text=f"Visualizando: {img_path}")
        self._update_metadata_panel(meta, has_image=True)
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
        ref_paths = list(self.attached_image_paths)
        prev_inter_id = self.current_interaction_id if self.multi_turn_var.get() else None

        self.is_generating = True
        self.generate_btn.configure(state="disabled", text="Gerando...")
        self.status_label.configure(
            text=f"Gerando com {model_id} ({resolution}, {aspect_ratio})...",
            text_color=ACCENT_AMBER,
        )
        ref_info = ", ".join(p.name for p in ref_paths) if ref_paths else "nenhuma"
        turn_info = prev_inter_id or "nova"
        self._log(
            f"Iniciando geração | modelo={model_id} | res={resolution} | "
            f"proporção={aspect_ratio} | refs=[{ref_info}] | turno_ant={turn_info}"
        )

        worker = threading.Thread(
            target=self._run_generation,
            args=(
                api_key,
                model_key,
                model_id,
                prompt,
                resolution,
                aspect_ratio,
                out_dir,
                ref_paths,
                prev_inter_id,
            ),
            daemon=True,
        )
        worker.start()

    def _run_generation(
        self,
        api_key: str,
        model_key: str,
        model_id: str,
        prompt: str,
        resolution: str,
        aspect_ratio: str,
        out_dir: Path,
        ref_paths: list[Path],
        prev_inter_id: str | None,
    ) -> None:
        """Executa a chamada à API em thread separada para não travar a interface."""
        try:
            import base64

            started_at = time.perf_counter()
            self._log("Enviando requisição para client.interactions.create...")
            interaction = create_image_interaction(
                api_key=api_key,
                model_id=model_id,
                prompt=prompt,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                ref_images=ref_paths,
                previous_interaction_id=prev_inter_id,
            )
            elapsed = time.perf_counter() - started_at
            status = getattr(interaction, "status", "unknown")
            inter_id = str(getattr(interaction, "id", "") or "")
            out_text = (getattr(interaction, "output_text", "") or "").strip()
            api_errors = getattr(interaction, "errors", None)
            if api_errors:
                self._logger.error("API retornou errors (%s): %s", inter_id, api_errors)
            self._log(f"API concluída em {elapsed:.2f}s | status={status} | id={inter_id or '-'}")
            if out_text:
                self._log(f"Texto da API: {out_text[:240]}")
            usage_summary = _format_usage_summary(interaction, model_id)
            self._log(usage_summary)

            output_img = getattr(interaction, "output_image", None)
            if not output_img or not getattr(output_img, "data", None):
                raise RuntimeError("A API não retornou nenhuma imagem na resposta.")

            image_bytes = base64.b64decode(output_img.data)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            prefix = MODELS.get(model_key, {}).get("prefix", "nb")
            out_path = out_dir / f"nanobanana_{prefix}_{resolution}_{timestamp}.png"

            meta = GenerationMetadata(
                prompt=prompt,
                model_key=model_key,
                model_id=model_id,
                resolution=resolution,
                aspect_ratio=aspect_ratio,
                interaction_id=inter_id,
                previous_interaction_id=prev_inter_id,
                ref_count=len(ref_paths),
                usage_summary=usage_summary,
                created_at=datetime.now().isoformat(timespec="seconds"),
            )
            pil_img = save_png_with_metadata(image_bytes, out_path, meta)
            self._log(
                f"Resposta recebida em {elapsed:.2f}s | {pil_img.width}x{pil_img.height}px | "
                f"{len(image_bytes) / 1024:.1f} KB"
            )
            self._ui_queue.put(
                lambda p=out_path, img=pil_img, m=meta: self._on_generation_success(p, img, m)
            )
        except Exception as exc:
            self._logger.exception("Falha na geração de imagem:")
            self._ui_queue.put(lambda err=str(exc): self._on_generation_error(err))

    def _on_generation_success(
        self,
        out_path: Path,
        pil_img: Image.Image,
        meta: GenerationMetadata | None = None,
    ) -> None:
        t0 = time.perf_counter()
        self.is_generating = False
        self.generate_btn.configure(state="normal", text="Gerar Imagem (Ctrl+Enter)")
        self.last_generated_path = out_path
        self.last_pil_image = pil_img
        self.open_img_btn.configure(state="normal")
        self.copy_img_btn.configure(state="normal")

        if meta and meta.interaction_id:
            self.current_interaction_id = meta.interaction_id
        self._update_turn_status_label()
        self._update_metadata_panel(meta, has_image=True)

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
