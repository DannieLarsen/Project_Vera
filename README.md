# Project Vera  Local AI Desktop Chatbot

A polished, local-first AI chat application powered by Microsoft Foundry Local. Chat with AI models running entirely on your machine  no cloud, no subscriptions, no data sent anywhere.

**License:** Apache 2.0 (free, open-source)

---

##  Features

-  **100% Local**  All models and inference run on your PC via Foundry Local
-  **Multi-Model Support**  Swap between any Foundry-cached model with one click
-  **Streaming Responses**  Real-time token streaming with a Stop button
-  **Stream Retry**  Automatically recovers from transient stream errors
-  **File Attachments**  Drag-and-drop support for text, code, PDFs, and more
-  **Persistent Chat History**  All sessions saved in SQLite; browse from the sidebar
-  **Polished UI**  Frameless window, custom title bar with pixel-art BitDog mascot, orange accent
-  **Windows 11 Snap Layout**  Hover the maximise button to see the snap grid; drag the title bar to snap zones
-  **Corner Resize**  Drag any corner to resize (Win32 SC_SIZE, no native chrome)
-  **Auto-Management**  Service and model auto-start/stop with graceful cleanup
-  **Fast Model Loading**  Uses official `foundry-local-sdk` for synchronous, reliable loading

---

##  Getting Started

### Prerequisites

1. **Windows 10/11** (Win32 APIs used for window chrome)
2. **Foundry Local** installed:
   ```powershell
   winget install Microsoft.FoundryLocal
   ```
3. **Python 3.10+**
4. **At least one cached model** (download via AI Toolkit  Models)

### Installation

```powershell
git clone <repo-url>
cd "Basic Local Agent"
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

### Running from source

```powershell
python app.py
```

### Pre-built executable

```powershell
.\dist\ProjectVera.exe
```

---

##  Architecture

### Code Structure

```
app.py (~2,300 lines, single-file application)
 FoundryManager (QObject)
    start_foundry()           Init SDK, discover dynamic port
    load_model(alias)         Synchronous load via SDK
    unload_model()            Unload via SDK
    list_cached()             List cached models

 ModelListWorker               Fetch model list in background thread
 ModelSwitchWorker             Unload old / load new model
 StreamWorker                  Stream chat completions with retry
 HealthCheckWorker             Poll endpoint until warm

 BitDogWidget (QWidget)        Pixel-art dog mascot
    start()                   Walking animation
    sit(flip_ms)              Front-facing ear-flap animation
    stop()                    Idle

 ChatWindow (QMainWindow)
    Frameless window with WS_THICKFRAME + WS_CAPTION
    nativeEvent  WM_NCCALCSIZE strips chrome; WM_NCHITTEST drives Snap Layout
    eventFilter  corner-zone detection  Win32 SC_SIZE resize
    Custom title bar (burger menu, BitDog, title, model selector, window controls)
    Chat history sidebar (SQLite, 6-month auto-cleanup)
    Message feed with streaming bubbles
    Drag-and-drop file support

 MessageBubble (QFrame)        Individual chat message with Markdown rendering
```

### Key Design Decisions

| Concern | Approach |
|---------|----------|
| Port discovery | `FoundryLocalManager.endpoint` (dynamic port, not hardcoded 5272) |
| Model loading | `sdk.load_model()`  synchronous, blocks until ready |
| Snap Layout | `WS_CAPTION` + `WM_NCHITTEST  HTMAXBUTTON / HTCAPTION` |
| Corner resize | `WM_SYSCOMMAND SC_SIZE` via Win32  smooth, no flicker |
| Chat persistence | SQLite via `sqlite3` stdlib  zero extra dependency |
| Stream reliability | Exponential-back-off retry on `APIConnectionError` / `APIStatusError` |

---

##  Requirements

```
PySide6>=6.7          # Qt UI framework
openai>=1.42          # OpenAI-compatible client
foundry-local-sdk     # Official Foundry SDK
pypdf                 # PDF attachment support
```

---

##  Usage

1. **Launch**  Foundry Local service auto-starts
2. **Select a model** from the title-bar drop-down  loads synchronously
3. **Wait for "Ready"** in the status bar
4. **Chat**  type or drag-and-drop files
5. **Browse history**  click the  burger menu to open the sidebar
6. **Snap**  hover the  button for Win11 Snap Layout, or drag the title bar to an edge
7. **Close**  model unloads cleanly; service keeps running for other apps

### Drag-and-Drop Supported Formats

- Text / code: `.txt`, `.md`, `.log`, `.csv`, `.json`, `.py`, `.js`, `.html`, `.xml`, `.yaml`, `.toml`, `.ini`, `.cfg`
- Documents: `.pdf`

---

##  Troubleshooting

| Symptom | Fix |
|---------|-----|
| "Startup failed" | Run `pip install foundry-local-sdk` or `winget install Microsoft.FoundryLocal` |
| "No models found" | Download a model in AI Toolkit  Models  Download, then click  |
| Model not responding | Click  to retry; if stuck, close and reopen the app |
| Slow first load | Large models (67 GB) can take 23 min; subsequent loads are faster |

---

##  Building the Executable

```powershell
pip install pyinstaller
pyinstaller ProjectVera.spec
# Output: dist\ProjectVera.exe
```

---

##  Repository Layout

```
.
 app.py              # Single-file application (~2,300 lines)
 ProjectVera.spec    # PyInstaller build spec
 requirements.txt    # Python dependencies
 Logo/
    icon.ico        # App icon (taskbar + window)
    *.png           # Source artwork
 README.md
```

---

##  Privacy & Security

-  Zero cloud dependency  all data stays on your PC
-  Zero telemetry  no tracking, no analytics
-  Open source  inspect the code yourself
-  Local-only inference  models run on your hardware

---

##  Contributing

Personal project  feel free to fork and adapt!

**Known limitations:**
- Windows only (uses ctypes Win32 API for window chrome and resize)
- Requires Foundry Local (Microsoft's local inference runtime)

---

##  License

Apache 2.0  see LICENSE file for details.

---

##  Acknowledgments

- **Microsoft Foundry Local**  local inference runtime
- **PySide6**  Qt bindings for Python
- **OpenAI SDK**  OpenAI-compatible chat completions
- **pypdf**  PDF parsing

---

**Project Vera  Chat locally, think freely.** 
