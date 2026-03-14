# Project Vera — Local AI Desktop Chatbot

A polished, privacy-first AI chat application for Windows that runs entirely on your machine.  
Choose between **Microsoft Foundry Local** or **Ollama** at launch — no cloud, no subscriptions, no data leaves your PC.

**License:** Apache 2.0 · Windows 10 / 11

---

## ✨ Features

| | |
|---|---|
| 🔒 **100% Local** | All inference runs on your PC — zero cloud calls, zero telemetry |
| ⚡ **Dual Backend** | Pick **Foundry Local** or **Ollama** from the visual launcher every time you open the app |
| 🔁 **Remembered Choice** | Your last backend is saved and pre-started automatically on next launch |
| 🔀 **Switch Anytime** | A badge in the title bar lets you swap backends without restarting |
| 🤖 **Multi-Model** | Swap between any locally available model with one click |
| 📡 **Streaming Responses** | Real-time token streaming with a Stop button and auto-retry on transient errors |
| 📎 **File Attachments** | Drag-and-drop `.txt`, `.py`, `.pdf`, `.csv`, `.json`, `.md` and more |
| 🗂️ **Persistent History** | All sessions stored in SQLite; browse, search, pin and rename from the sidebar |
| 🐶 **BitDog Mascot** | Pixel-art dog that walks while the model thinks, sits in the title bar |
| 🪟 **Windows 11 Snap Layout** | Hover the maximise button for the snap grid; drag the title bar to snap zones |
| 📐 **Corner Resize** | Native Win32 corner-drag resize — no flicker, no borders |

---

## 🚀 Getting Started

### Prerequisites

- **Windows 10 / 11**
- **Python 3.10+**
- At least one of the backends installed:

| Backend | Install |
|---------|---------|
| Foundry Local | `winget install Microsoft.FoundryLocal` then download a model via **AI Toolkit → Models** |
| Ollama | `winget install Ollama.Ollama` then `ollama pull llama3.2` (or any model) |

### Install from source

```powershell
git clone https://github.com/DannieLarsen/Project_Vera.git
cd Project_Vera
python -m venv venv
.\venv\Scripts\activate
pip install PySide6 openai pypdf
# Optional — only needed if using Foundry Local:
pip install foundry-local-sdk
```

### Run

```powershell
python app.py
```

### Pre-built executable

```powershell
.\dist\ProjectVera.exe
```

---

## 🖥️ Usage

1. **Launch** — the backend picker appears
2. **Choose Foundry Local or Ollama** — your choice is saved for next time
3. **Select a model** from the title-bar drop-down
4. **Wait for "● Ready"** in the status bar
5. **Chat** — type a message or drag-and-drop a file
6. **Browse history** — click ☰ to open the session sidebar
7. **Switch backend** — click the `Foundry` / `Ollama` badge in the title bar at any time
8. **Snap / resize** — hover □ for Win11 Snap Layout, or drag any corner

### Drag-and-Drop File Formats

`.txt` · `.md` · `.log` · `.csv` · `.json` · `.py` · `.js` · `.html` · `.xml` · `.yaml` · `.toml` · `.ini` · `.cfg` · `.pdf`

---

## 🏗️ Architecture

```
app.py  (single-file, ~2 800 lines)
│
├── FoundryManager (QObject)
│     start_foundry()     Bootstrap SDK, discover dynamic port
│     load_model(alias)   Synchronous load via foundry-local-sdk
│     unload_model()      Unload cleanly on model switch / exit
│     list_cached()       List all locally cached models
│
├── OllamaManager (QObject)
│     start_ollama()      Verify Ollama reachable at localhost:11434
│     load_model(name)    No-op — Ollama auto-loads on first inference
│     list_cached()       GET /api/tags → list of pulled models
│
├── ModelListWorker        Background thread — fetch model list from active backend
├── ModelSwitchWorker      Unload old / load new (Foundry); instant select (Ollama)
├── StreamWorker           Stream chat completions with 1-retry on transient errors
├── HealthCheckWorker      Poll endpoint until warm (Foundry only)
│
├── BitDogWidget (QWidget)
│     start()             Walking animation (while model is thinking)
│     sit(flip_ms)        Front-facing ear-flap (title bar)
│     stop()              Hide
│
└── ChatWindow (QMainWindow)
      QStackedWidget
        Page 0: Backend picker  — two logo cards, Foundry | Ollama
        Page 1: Chat UI
          Custom title bar  (☰ burger · BitDog · title · backend badge · model combo · window controls)
          Session sidebar   (SQLite, 6-month auto-cleanup, search, pin, rename, delete)
          Message feed      (streaming bubbles, markdown code blocks)
          Input bar         (text field + Send/Stop + drag-and-drop attachment chip)
      Win32 integration
          WS_THICKFRAME + WS_CAPTION  → Snap Layout support
          WM_NCCALCSIZE               → strips native chrome
          WM_NCHITTEST                → HTCAPTION / HTMAXBUTTON
          SC_SIZE corner drag         → flicker-free resize
```

### Key Design Decisions

| Concern | Approach |
|---------|----------|
| Dual backend | Common `status_update / ready / failed` signal contract; `StreamWorker` / `OpenAI` client unchanged |
| Foundry port | `FoundryLocalManager.endpoint` — dynamic, not hardcoded |
| Ollama endpoint | Fixed `http://localhost:11434/v1` — OpenAI-compatible, no extra SDK |
| Model loading | Foundry: synchronous `sdk.load_model()` + health-check; Ollama: instant select, auto-loads on first token |
| Snap Layout | `WS_CAPTION` + `WM_NCHITTEST → HTMAXBUTTON / HTCAPTION` |
| Corner resize | `WM_SYSCOMMAND SC_SIZE` via Win32 — smooth, no flicker |
| Chat persistence | SQLite via `sqlite3` stdlib — zero extra dependency |
| App icon | BitDog head rendered at runtime into a 7-size `QIcon`; embedded in the `.exe` via `bitdog.ico` |

---

## 📦 Requirements

```
PySide6>=6.7          # Qt6 UI framework
openai>=1.42          # OpenAI-compatible chat completions client
pypdf                 # PDF attachment reading (optional)
foundry-local-sdk     # Foundry Local backend (optional)
```

> Ollama requires no Python package — it exposes a built-in REST API.

---

## 🔧 Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Could not reach Ollama" | Make sure Ollama is running: `ollama serve` |
| "No models found" (Ollama) | Pull a model first: `ollama pull llama3.2` |
| "Could not start Foundry Local" | `winget install Microsoft.FoundryLocal` |
| "No models found" (Foundry) | Download a model via **AI Toolkit → Models → Download** |
| Model not responding | Click ↻ to retry the health check |
| Slow first response (Ollama) | Normal — Ollama loads the model on the first inference call |
| Slow first load (Foundry) | Large models (6–7 GB) can take 2–3 min; subsequent loads are faster |

---

## 🔨 Building the Executable

```powershell
# Generate the BitDog icon first
python make_icon.py

# Build the single-file .exe
pip install pyinstaller
pyinstaller ProjectVera.spec --noconfirm

# Output
.\dist\ProjectVera.exe
```

---

## 📁 Repository Layout

```
.
├── app.py                  # Single-file application (~2 800 lines)
├── make_icon.py            # Generates Logo/bitdog.ico from the BitDog sprite
├── ProjectVera.spec        # PyInstaller build spec
├── Logo/
│   ├── bitdog.ico          # Generated multi-resolution app icon
│   ├── Foundry local logo.png
│   ├── Ollama logo.png
│   └── *.png               # Other source artwork
└── README.md
```

---

## 🔒 Privacy & Security

- ✅ Zero cloud dependency — all data stays on your PC
- ✅ Zero telemetry — no tracking, no analytics
- ✅ Open source — inspect every line
- ✅ Local-only inference — models run entirely on your hardware

---

## 🤝 Contributing

Personal project — feel free to fork and adapt!

**Known limitations:**
- Windows only (uses ctypes Win32 API for window chrome and resize)
- Requires either Foundry Local or Ollama to be installed and running

---

## 📄 License

Apache 2.0 — see LICENSE file for details.

---

## 🙏 Acknowledgments

- **Microsoft Foundry Local** — local inference runtime
- **Ollama** — lightweight local model serving
- **PySide6** — Qt6 bindings for Python
- **openai-python** — OpenAI-compatible client used for both backends

- **OpenAI SDK**  OpenAI-compatible chat completions
- **pypdf**  PDF parsing

---

**Project Vera  Chat locally, think freely.** 
