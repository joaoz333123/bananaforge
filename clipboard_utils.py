"""Integração com área de transferência (colar imagens e copiar resultado no Windows)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
from io import BytesIO
from pathlib import Path
import sys

from PIL import Image, ImageGrab

SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
CF_DIB = 8
GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040


def paste_image_from_clipboard(cache_dir: Path) -> Path | None:
    """Lê uma imagem ou arquivo de imagem da área de transferência, salvando-a se necessário."""
    try:
        clip = ImageGrab.grabclipboard()
    except Exception:
        return None

    if isinstance(clip, Image.Image):
        cache_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = cache_dir / f"clip_{stamp}.png"
        clip.convert("RGBA").save(out_path, format="PNG")
        return out_path

    if isinstance(clip, list):
        for item in clip:
            candidate = Path(str(item))
            if (
                candidate.exists()
                and candidate.is_file()
                and candidate.suffix.lower() in SUPPORTED_IMAGE_EXTS
            ):
                return candidate
    return None


def copy_image_to_clipboard(pil_img: Image.Image) -> bool:
    """Copia uma imagem PIL para a área de transferência do Windows via CF_DIB (sem deps extras)."""
    if sys.platform != "win32":
        return False

    buf = BytesIO()
    pil_img.convert("RGB").save(buf, format="BMP")
    bmp_data = buf.getvalue()
    if len(bmp_data) <= 14:
        return False
    dib_data = bmp_data[14:]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)

    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
    kernel32.GlobalFree.restype = ctypes.c_void_p

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = []
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.CloseClipboard.argtypes = []
    user32.CloseClipboard.restype = wintypes.BOOL

    h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(dib_data))
    if not h_mem:
        return False

    ptr = kernel32.GlobalLock(h_mem)
    if not ptr:
        kernel32.GlobalFree(h_mem)
        return False

    try:
        ctypes.memmove(ptr, dib_data, len(dib_data))
    finally:
        kernel32.GlobalUnlock(h_mem)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(h_mem)
        return False

    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_DIB, h_mem):
            kernel32.GlobalFree(h_mem)
            return False
        # Após SetClipboardData bem-sucedido, o sistema operacional assume a posse de h_mem.
        return True
    finally:
        user32.CloseClipboard()
