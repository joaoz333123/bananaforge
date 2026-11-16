"""Ferramenta de diagnóstico para medir latência da UI e inspecionar logs de erro/freeze."""

from __future__ import annotations

import time
from pathlib import Path

from PIL import Image

import app


def _benchmark_ui() -> None:
    """Executa etapas críticas da interface medindo o tempo em ms na thread principal."""
    print("=== Benchmark de Performance da UI ===")
    t0 = time.perf_counter()
    studio = app.NanoBananaApp()
    print(f"1. Inicialização da janela: {(time.perf_counter() - t0) * 1000:.1f} ms")

    def run_checks() -> None:
        # 2. Inserção de log na UI
        t1 = time.perf_counter()
        for i in range(10):
            studio._log(f"Diagnóstico linha {i}")
        studio.update_idletasks()
        print(f"2. 10 inserções no log_box: {(time.perf_counter() - t1) * 1000:.1f} ms")

        # 3. Renderização de imagem 2K (2048x2048) no preview
        t2 = time.perf_counter()
        img_2k = Image.new("RGB", (2048, 2048), "#22252A")
        studio.last_pil_image = img_2k
        studio._render_preview(force=True)
        studio.update_idletasks()
        print(f"3. Render preview 2048x2048: {(time.perf_counter() - t2) * 1000:.1f} ms")

        # 4. Leitura da galeria
        t3 = time.perf_counter()
        studio._refresh_history()
        studio.after(400, lambda: finish(t3))

    def finish(t_hist: float) -> None:
        print(
            f"4. Galeria carregada ({len(studio._history_items)} itens): "
            f"{(time.perf_counter() - t_hist) * 1000:.1f} ms"
        )
        studio.destroy()

    studio.after(150, run_checks)
    studio.mainloop()


def _inspect_logs() -> None:
    """Resume os arquivos de log em logs/ destacando erros, freezes e passos lentos."""
    print("\n=== Inspeção de Logs (logs/) ===")
    if not app.LOGS_DIR.exists():
        print("Pasta logs/ ainda não existe.")
        return

    log_files = sorted(app.LOGS_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not log_files:
        print("Nenhum arquivo .log encontrado.")
        return

    for lf in log_files[:5]:
        size_kb = lf.stat().st_size / 1024
        lines = lf.read_text(encoding="utf-8", errors="replace").splitlines()
        freezes = sum(1 for ln in lines if "FREEZE DETECTADO" in ln)
        errors = sum(1 for ln in lines if "[ERROR]" in ln)
        slows = sum(1 for ln in lines if "Passo lento" in ln)
        print(
            f"- {lf.name} ({size_kb:.1f} KB, {len(lines)} linhas) | "
            f"erros={errors}, freezes={freezes}, lentos={slows}"
        )


if __name__ == "__main__":
    _benchmark_ui()
    _inspect_logs()
