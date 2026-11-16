# BananaForge (nano-banana-studio) — Arquitetura e Guia de Desenvolvimento

## Visão Geral
Aplicativo desktop local-first em Python (`CustomTkinter` + `google-genai` + `Pillow`) para geração e edição de imagens usando os modelos **Nano Banana** do Google AI Studio.

## Estrutura de Arquivos
- `app.py`: Interface principal `NanoBananaApp(ctk.CTk)`, gerenciamento de threads (`_ui_queue`), watchdogs de freeze/erro, galeria de histórico e integração dos painéis.
- `genai_service.py`: Camada de acesso à Interactions API (`client.interactions.create`), suporte a múltiplas referências (`build_interaction_input`), edição multi-turno (`previous_interaction_id`), cálculo de custos/tokens e expansão de prompt via `gemini-3.1-flash-lite`.
- `metadata_utils.py`: Persistência e leitura de metadados estruturados (`GenerationMetadata`) em chunks de texto PNG (`bananaforge_meta`) usando `PIL.PngImagePlugin.PngInfo`.
- `presets_manager.py`: Carregamento, criação, remoção e aplicação de presets modulares em `presets.json`.
- `clipboard_utils.py`: Colagem de imagem/arquivo da área de transferência (`PIL.ImageGrab.grabclipboard`) com cache em `logs/clipboard_cache/` e cópia de imagem para o clipboard do Windows via `ctypes` (`CF_DIB`).
- `diagnose.py`: Benchmark de inicialização/renderização da UI e inspetor dos arquivos `logs/app_errors_*.log`.
- `tests/`: Suíte de testes unitários (`unittest`) com mocks da API Gemini.

## Regras de Negócio e Execução
- **Ambiente virtual:** Usar sempre `.venv\Scripts\python.exe` (Windows).
- **API Key:** Configurada via `.env` (`GEMINI_API_KEY`) ou pelo botão **Configurar API Key** na barra superior.
- **Limites na UI:** Máximo de `4` imagens de referência simultâneas (`MAX_REF_IMAGES = 4`).
- **Execução da aplicação:**
  ```powershell
  .\.venv\Scripts\python.exe app.py
  ```
- **Testes unitários:**
  ```powershell
  .\.venv\Scripts\python.exe -m unittest discover -s tests -v
  ```
- **Diagnóstico de UI e logs:**
  ```powershell
  .\.venv\Scripts\python.exe diagnose.py
  ```
