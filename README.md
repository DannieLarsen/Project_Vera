# Project Vera — Local AI Desktop Chatbot

A **bulletproof** local-first AI chat application powered by Microsoft Foundry Local. Chat with AI models running entirely on your machine—no cloud, no subscriptions, no data sent anywhere.

**License:** Apache 2.0 (free, open-source)

---

## ✨ Features

- 🖥️ **100% Local** — All models and inference run on your PC via Foundry Local
- 🤖 **Multi-Model Support** — Swap between any Foundry-cached model (Phi, Qwen, etc.) with one click
- 💬 **Streaming Responses** — Real-time token streaming for smooth, interactive chat
- 📎 **File Attachments** — Drag-and-drop support for text, code, PDFs, and more
- 🎨 **Polished UI** — Frameless window, custom title bar, orange accent (OpenClaw-inspired)
- 🔧 **Auto-Management** — Service and model auto-start/stop with graceful cleanup
- ⚡ **Fast Model Loading** — Uses official `foundry-local-sdk` for synchronous, reliable loading

---

## 🚀 Getting Started

### Prerequisites

1. **Windows 10/11**
2. **Foundry Local** installed via Microsoft Store or WinGet:
   ```powershell
   winget install Microsoft.FoundryLocal
   ```
3. **Python 3.10+** (included in venv)
4. **At least one cached model** (download via AI Toolkit)

### Installation

1. Clone or download this repository
2. Create and activate a Python virtual environment:
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   ```
3. Install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

### Running

**From source:**
```powershell
python app.py
```

**Or use the pre-built EXE** (if available):
```powershell
.\dist\ProjectVera.exe
```

---

## 🔧 Architecture

### The Problem We Solved

The original implementation tried to:
- Call `foundry.exe` via `subprocess` (fragile, especially for App Execution Aliases)
- Hardcode the port as `5272` (Foundry uses a **dynamic port** like 49671)
- Parse CLI output with regex (error-prone, brittle)

**Result:** Models never loaded, health checks timed out, app crashed.

### The Solution: Official Microsoft SDK

We replaced all subprocess calls with the **official `foundry-local-sdk`** Python package, which provides:

| Feature | Old (Broken) | New (SDK) |
|---------|--------------|-----------|
| **Port Discovery** | Hardcoded `5272` ❌ | `FoundryLocalManager.endpoint` ✅ |
| **Service Start** | `subprocess.run(["foundry", "service", "start"])` ❌ | `FoundryLocalManager(bootstrap=True)` ✅ |
| **Model Loading** | `subprocess.run(["foundry", "model", "load", ...])` ❌ | `sdk.load_model(alias)` (synchronous, blocks until ready) ✅ |
| **Cache Listing** | Regex parsing CLI output ❌ | `sdk.list_cached_models()` (proper objects) ✅ |
| **Reliability** | App Execution Alias issues ❌ | Native Python, handles all edge cases ✅ |

### Code Structure

```
app.py (1,200 lines, single-file app)
├── FoundryManager (QObject)
│   ├── start_foundry()          — Initialize SDK, discover port
│   ├── load_model(alias)        — Load via SDK (blocks until ready)
│   ├── unload_model()           — Unload via SDK
│   └── list_cached()            — Get cached models
│
├── ModelListWorker (QObject)    — Fetch models in background thread
├── ModelSwitchWorker (QObject)  — Unload old model, load new one
├── StreamWorker (QObject)       — Stream chat completions tokens
├── HealthCheckWorker (QObject)  — Poll endpoint until warm (15 retries, 1s each)
│
├── ChatWindow (QMainWindow)     — Main UI
│   ├── Frameless, custom title bar
│   ├── Message feed with bubbles
│   ├── Drag-and-drop file support
│   └── Model selector combo box
│
└── MessageBubble (QFrame)       — Individual chat message
```

### Key Design Decisions

1. **Dynamic Port Discovery** — `FoundryLocalManager.endpoint` is set once after SDK init; passed to OpenAI client immediately
2. **Synchronous Model Load** — `sdk.load_model()` blocks until the model is truly ready; no guessing
3. **Short Health Check** — Only 15 retries (15s) because the SDK guarantees the model is ready
4. **Dual Model References** — Store both alias (for load/unload) and full ID (for chat completions)
5. **Clean Shutdown** — Unload model on app close (service keeps running for other apps)

---

## 📋 Requirements

See `requirements.txt`:

```
PySide6==6.7.2          # Qt UI framework
openai==1.42.0          # OpenAI-compatible client
foundry-local-sdk==0.5.1 # Official Foundry SDK (THE KEY FIX)
pypdf==6.8.0            # PDF support
```

---

## 🎯 Usage

1. **Start the app** → Foundry Local service auto-starts
2. **Wait for "Select a model to begin"** → Model list populated from cache
3. **Choose a model** → Model loads via `sdk.load_model()` (synchronous)
4. **Wait for "Ready"** → Health check confirms endpoint is warm
5. **Chat!** → Type or drag-and-drop files
6. **Close app** → Model unloads, service remains running

### Drag-and-Drop Supported Formats

- Text: `.txt`, `.md`, `.log`, `.csv`, `.json`
- Code: `.py`, `.js`, `.html`, `.xml`, `.yaml`, `.toml`, `.ini`, `.cfg`
- Documents: `.pdf` (requires pypdf)

---

## 🐛 Troubleshooting

### **App says "Startup failed"**

**Check the error message (hover over ↻)**

- **"foundry-local-sdk not installed"** → Run: `pip install foundry-local-sdk`
- **"Foundry Local is not installed"** → Run: `winget install Microsoft.FoundryLocal`

### **App shows "No models found"**

- Download a model via **AI Toolkit** → Models → Download
- Click ↻ to refresh the list
- Common models: Phi-3-mini, Qwen2.5-7b, Phi-3.5-mini

### **"Model not responding" after loading**

- **Rare issue** — click ↻ to re-run the health check (15s max)
- If it persists, close and reopen the app (will unload the broken model)

### **Slow model loading**

- First load of a large model (6–7GB) can take 2–3 minutes
- Subsequent loads are much faster
- Watch the title bar status for progress

### **App crashes on close**

- Normal if you close during model switching
- The cleanup thread has a 20s timeout before force-exit

---

## 🏗️ Building the Executable

Requires PyInstaller:

```powershell
pip install pyinstaller

# Build:
pyinstaller --onefile --windowed --name "ProjectVera" `
  --icon "Logo/icon.ico" `
  --collect-all PySide6 `
  --collect-all openai `
  --collect-all httpx `
  --collect-all foundry_local `
  --noconfirm app.py

# Output: dist\ProjectVera.exe (~260MB)
```

---

## 📦 What's Included

```
.
├── app.py                    # Single-file application (1,198 lines)
├── requirements.txt          # Python dependencies
├── Logo/
│   └── icon.ico             # V logo (taskbar + window icon)
├── dist/
│   └── ProjectVera.exe      # Pre-built executable
└── README.md                # This file
```

---

## 🔐 Privacy & Security

- ✅ **Zero cloud dependency** — All data stays on your PC
- ✅ **Zero telemetry** — No tracking, no analytics
- ✅ **Open source** — Inspect the code yourself
- ✅ **Local-only inference** — Models run on your hardware

Foundry Local does **not** upload models or responses to Microsoft or any cloud service.

---

## 🤝 Contributing

This is a personal project, but feel free to fork and adapt!

**Known limitations:**
- Windows only (uses ctypes Win32 API)
- Requires Foundry Local (Microsoft's local inference runtime)
- Chat history not persisted between sessions (in-memory only)

---

## 📝 License

Apache 2.0 — See LICENSE file for details.

---

## 🙏 Acknowledgments

- **Microsoft Foundry Local** — Local inference runtime
- **PySide6** — Qt bindings for Python
- **OpenAI SDK** — OpenAI-compatible chat completions
- **pypdf** — PDF parsing

---

## 📧 Questions?

If Foundry Local isn't working:
1. Verify it's installed: `foundry --version`
2. Check status: `foundry service status`
3. Restart service: `foundry service restart`
4. Download a model: Open AI Toolkit → Models → Download

If the app crashes, check the Python console output for the error message.

---

**Project Vera — Chat locally, think freely.** 🧠✨
