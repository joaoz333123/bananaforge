# BananaForge

**A fast, local-first Python desktop client for Google Gemini "Nano Banana" image generation and editing (`google-genai` + `CustomTkinter`).**

Created by **JZ DEV**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Google GenAI SDK](https://img.shields.io/badge/Google%20GenAI%20SDK-3.3.0-4285F4?logo=google&logoColor=white)](https://ai.google.dev/)
[![CustomTkinter](https://img.shields.io/badge/UI-CustomTkinter-E5A93C)](https://github.com/TomSchimansky/CustomTkinter)
[![License: MIT](https://img.shields.io/badge/License-MIT-2EA44F)](./LICENSE)

---

## Overview

**BananaForge** is a lightweight desktop workstation for generating and editing images using Google AI Studio's **Nano Banana** family of Gemini image models via the official `google-genai` Interactions API (`client.interactions.create`).

Instead of relying on a browser tab, BananaForge gives you a responsive dark-mode studio interface on Windows (and cross-platform Python environments) with local file saving, reference-image editing, a clickable output gallery, token & cost tracking, and built-in freeze/crash diagnostics.

---

## Supported Models (Google AI Studio)

BananaForge supports all 5 Nano Banana image models available in Google AI Studio:

| Selector in App | Model ID | Best For | Approx. Cost / Image (BRL)* |
| :--- | :--- | :--- | :--- |
| **Nano Banana 2.1** | `gemini-nano-banana-2.1` | Latest generation, factuality & multi-turn consistency | `~R$ 0,19` |
| **Nano Banana 2 Lite** | `gemini-3.1-flash-lite-image` | Lowest-cost, high-volume image generation & editing | `~R$ 0,19` |
| **Nano Banana 2** | `gemini-3.1-flash-image` | Flash speed with pro-level visual intelligence | `~R$ 0,38` |
| **Nano Banana Pro** | `gemini-3-pro-image` | State-of-the-art quality & heavy reasoning | `~R$ 0,76` |
| **Nano Banana (2.5)** | `gemini-2.5-flash-image` | Classic Gemini 2.5 Flash image model | `~R$ 0,22` |

*\*Approximate Paid Tier estimates displayed inside the UI (`USD_TO_BRL = 5.70`, rounded to 2 decimal places). Actual billing depends on your Google AI Studio tier and token usage.*

---

## Key Features

- **Text-to-Image & Image-to-Image:** Generate images from prompts or attach a reference image (`PNG`, `JPG`, `JPEG`, `WEBP`) for style transfer, sketch conversion, and targeted edits.
- **Resolution & Aspect Ratio Controls:** Choose between `1K`, `2K`, and `4K` resolutions and `1:1`, `16:9`, `9:16`, `4:3`, `3:4` aspect ratios.
- **Clickable History Gallery:** Automatically scans your output folder (`~/Pictures/NanoBanana` by default) and renders cached thumbnails in a right-hand gallery panel.
- **Live Token & Cost Summary:** Logs input, output, and thought tokens alongside estimated cost in **BRL (R$)** and **USD (US$)** capped at 2 decimal places.
- **Fast Workflow Shortcuts:** Press `Ctrl+Enter` inside the prompt box to generate immediately.
- **Built-in API Key Manager:** Configure or update your `GEMINI_API_KEY` directly from the top bar (saved locally in `.env`, excluded from Git).
- **Non-Blocking UI & Diagnostics:** Uses a thread-safe `queue.SimpleQueue` bridge, `faulthandler` crash dumps, and a `FreezeWatchdog` thread logging Python app diagnostics to `logs/app_errors_*.log`, plus a standalone `diagnose.py` benchmark script.

---

## Architecture & Project Structure

```text
bananaforge/
├── app.py              # Main CustomTkinter desktop app, Gemini API worker & UI queue
├── diagnose.py         # UI performance benchmark & Python error log inspector
├── requirements.txt    # Pinned Python dependencies (google-genai, customtkinter, pillow, python-dotenv)
├── .env.example        # Template for your GEMINI_API_KEY (copy to .env)
├── .gitignore          # Excludes .env, .venv/, logs/, output/, __pycache__/
├── launch.vbs          # Silent Windows launcher (pythonw.exe)
├── run.bat             # Windows batch launcher
└── LICENSE             # MIT License (JZ DEV)
```

---

## Getting Started

### Prerequisites

- **Python 3.10+** (tested on Python 3.14 on Windows)
- A **Google AI Studio API Key** ([get one here](https://aistudio.google.com/apikey))

### Installation

1. **Clone or fork this repository:**
   ```bash
   git clone https://github.com/joaoz333123/bananaforge.git
   cd bananaforge
   ```

2. **Create and activate a virtual environment:**
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   *(On macOS/Linux: `source .venv/bin/activate`)*

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure your API key:**
   - **Option A:** Copy `.env.example` to `.env` and paste your key:
     ```env
     GEMINI_API_KEY=your_actual_api_key_here
     ```
   - **Option B:** Launch the app and click **"Configurar API Key"** in the top-right corner.

5. **Run BananaForge:**
   ```bash
   python app.py
   ```
   Or on Windows, double-click `run.bat` or `launch.vbs` (launches without a console window).

---

## Diagnostics & Benchmarking

If you want to verify UI responsiveness or inspect Python runtime logs:

```bash
python diagnose.py
```

This benchmarks widget initialization, log insertion, 2K preview resizing, thumbnail gallery rendering, and checks the latest `logs/app_errors_*.log` for errors, slow operations (`> 150 ms`), or UI stalls (`> 2.5 s`).

---

## Contributing & Forking

Contributions, forks, and improvements are welcome!

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/your-feature`)
3. Make sure `.env` and `logs/` remain untracked
4. Commit your changes and open a Pull Request

---

## Author

Developed by **JZ DEV** (João Zanetti).

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](./LICENSE) file for details.  
You are free to use, modify, fork, and distribute this software for personal or commercial projects while keeping the original copyright attribution (**JZ DEV**).

### Responsible Use

While the source code is licensed under MIT, image generation requests made through this client are subject to Google's [Generative AI Prohibited Use Policy](https://policies.google.com/terms/generative-ai/use-policy) and [Google AI Studio Terms of Service](https://ai.google.dev/gemini-api/terms). Please use the tool ethically and responsibly.
