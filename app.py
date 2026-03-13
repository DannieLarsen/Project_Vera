"""
Project Vera — Local AI Desktop Chatbot
Powered by Microsoft Foundry Local
License: Apache 2.0 (no cost, no cloud)
"""

import sys
import os
import ctypes
import threading
import re
import time
import sqlite3
import uuid
import json
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from pathlib import Path

# ── Tell Windows to use our icon in the taskbar (not python.exe's icon) ──────
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Vera.ProjectVera.1"
        )
    except Exception:
        pass

from PySide6.QtCore import Qt, QEvent, Signal, QObject, QThread, QPoint, QRect, QTimer, QPropertyAnimation, QEasingCurve, QSize
from PySide6.QtGui import QFont, QColor, QPalette, QTextCursor, QIcon, QPainter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QTextEdit, QLineEdit, QPushButton,
    QLabel, QSizePolicy, QScrollArea, QFrame, QGraphicsDropShadowEffect,
    QComboBox, QListWidget, QListWidgetItem, QSplitter, QMenu, QInputDialog,
    QMessageBox
)
from openai import OpenAI

# ── Foundry Local connection ──────────────────────────────────────────────────
# Port is dynamic — discovered at runtime via foundry-local-sdk.
MODEL_NAME = "qwen2.5-1.5b-instruct-generic-cpu:1"  # fallback only

SYSTEM_PROMPT = (
    "You are a helpful, concise AI assistant named James. "
    "Answer clearly and accurately. "
    "Use markdown for code blocks when relevant."
)

# ── OpenClaw-inspired color palette ──────────────────────────────────────────
BG_MAIN      = "#1a1a1a"   # near-black background
BG_PANEL     = "#242424"   # slightly lighter panel
BG_INPUT     = "#2e2e2e"   # input field background
BG_USER_MSG  = "#2c2c2c"   # user bubble
BG_BOT_MSG   = "#1f1f1f"   # assistant bubble
ACCENT       = "#e8660a"   # OpenClaw orange
ACCENT_HOVER = "#ff7a1a"   # lighter orange on hover
TEXT_PRIMARY = "#f0f0f0"   # main text
TEXT_MUTED   = "#888888"   # timestamps / labels
BORDER       = "#3a3a3a"   # subtle borders

# ── Persistent storage paths ──────────────────────────────────────────────────
_APP_DIR  = Path(os.getenv("APPDATA", Path.home())) / "ProjectVera"
_DB_PATH  = _APP_DIR / "history.db"
_CFG_PATH = _APP_DIR / "config.json"
_APP_DIR.mkdir(parents=True, exist_ok=True)

# ── Session dataclass ─────────────────────────────────────────────────────────
@dataclass
class ChatSession:
    id:         str      = field(default_factory=lambda: str(uuid.uuid4()))
    name:       str      = "New Chat"
    created_at: str      = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str      = field(default_factory=lambda: datetime.utcnow().isoformat())
    model_name: str      = ""
    pinned:     bool     = False
    preview:    str      = ""   # first user message, truncated to 80 chars

# ── Config helpers ────────────────────────────────────────────────────────────
def _load_config() -> dict:
    try:
        return json.loads(_CFG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_config(cfg: dict):
    try:
        _CFG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Database worker ───────────────────────────────────────────────────────────
class DBWorker(QObject):
    """Handles all SQLite operations on a dedicated thread."""
    sessions_loaded  = Signal(list)      # list[ChatSession]
    messages_loaded  = Signal(str, list) # session_id, list[dict]
    session_saved    = Signal(str)       # session_id
    message_saved    = Signal(str)       # session_id
    error_occurred   = Signal(str)

    def __init__(self, db_path: Path):
        super().__init__()
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def init_db(self):
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL DEFAULT 'New Chat',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                model_name TEXT NOT NULL DEFAULT '',
                pinned     INTEGER NOT NULL DEFAULT 0,
                preview    TEXT NOT NULL DEFAULT '',
                archived   INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role       TEXT NOT NULL,
                content    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_updated
                ON sessions(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, created_at);
        """)
        self._conn.commit()

    def load_sessions(self):
        try:
            cur = self._conn.execute(
                "SELECT * FROM sessions WHERE archived=0 "
                "ORDER BY pinned DESC, updated_at DESC"
            )
            sessions = []
            for row in cur.fetchall():
                sessions.append(ChatSession(
                    id=row["id"], name=row["name"],
                    created_at=row["created_at"], updated_at=row["updated_at"],
                    model_name=row["model_name"], pinned=bool(row["pinned"]),
                    preview=row["preview"]
                ))
            self.sessions_loaded.emit(sessions)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def load_messages(self, session_id: str):
        try:
            cur = self._conn.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id=? ORDER BY created_at", (session_id,)
            )
            msgs = [{"role": r["role"], "content": r["content"]}
                    for r in cur.fetchall()]
            self.messages_loaded.emit(session_id, msgs)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def upsert_session(self, s: ChatSession):
        try:
            self._conn.execute(
                "INSERT INTO sessions (id,name,created_at,updated_at,"
                "model_name,pinned,preview,archived) VALUES (?,?,?,?,?,?,?,0) "
                "ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name, updated_at=excluded.updated_at, "
                "model_name=excluded.model_name, pinned=excluded.pinned, "
                "preview=excluded.preview",
                (s.id, s.name, s.created_at, s.updated_at,
                 s.model_name, int(s.pinned), s.preview)
            )
            self._conn.commit()
            self.session_saved.emit(s.id)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def save_message(self, session_id: str, role: str, content: str):
        try:
            now = datetime.utcnow().isoformat()
            self._conn.execute(
                "INSERT INTO messages (session_id,role,content,created_at) VALUES (?,?,?,?)",
                (session_id, role, content, now)
            )
            self._conn.execute(
                "UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id)
            )
            self._conn.commit()
            self.message_saved.emit(session_id)
        except Exception as e:
            self.error_occurred.emit(str(e))

    def rename_session(self, session_id: str, new_name: str):
        try:
            self._conn.execute(
                "UPDATE sessions SET name=?, updated_at=? WHERE id=?",
                (new_name, datetime.utcnow().isoformat(), session_id)
            )
            self._conn.commit()
        except Exception as e:
            self.error_occurred.emit(str(e))

    def toggle_pin(self, session_id: str, pinned: bool):
        try:
            self._conn.execute(
                "UPDATE sessions SET pinned=? WHERE id=?", (int(pinned), session_id)
            )
            self._conn.commit()
        except Exception as e:
            self.error_occurred.emit(str(e))

    def archive_session(self, session_id: str):
        try:
            self._conn.execute(
                "UPDATE sessions SET archived=1 WHERE id=?", (session_id,)
            )
            self._conn.commit()
        except Exception as e:
            self.error_occurred.emit(str(e))

    def cleanup_old(self, months: int = 6):
        """Hard-delete non-pinned sessions older than `months` months."""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=months * 30)).isoformat()
            cur = self._conn.execute(
                "SELECT id FROM sessions WHERE updated_at < ? AND pinned=0",
                (cutoff,)
            )
            old_ids = [r["id"] for r in cur.fetchall()]
            for sid in old_ids:
                self._conn.execute("DELETE FROM messages WHERE session_id=?", (sid,))
                self._conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            if old_ids:
                self._conn.execute("VACUUM")
            self._conn.commit()
        except Exception as e:
            self.error_occurred.emit(str(e))

    def close(self):
        if self._conn:
            try:
                self._conn.execute("VACUUM")
                self._conn.close()
            except Exception:
                pass


# ── Cleanup worker (hourly timer) ─────────────────────────────────────────────
class CleanupWorker(QObject):
    def __init__(self, db_worker: "DBWorker"):
        super().__init__()
        self._db = db_worker

    def run_cleanup(self):
        self._db.cleanup_old(months=6)


# ── Sidebar session item widget ───────────────────────────────────────────────
class HistoryItemWidget(QWidget):
    """One row in the history panel: name + relative date + message preview."""

    def __init__(self, session: ChatSession, parent=None):
        super().__init__(parent)
        self.session = session
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setSpacing(4)

        self._pin_lbl = QLabel("📌 " if self.session.pinned else "")
        self._pin_lbl.setStyleSheet(
            "color: #e8660a; font-size: 10px; background: transparent;"
        )
        self._pin_lbl.setFixedWidth(18)

        self.name_lbl = QLabel(self.session.name)
        self.name_lbl.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 13px; font-weight: 600; "
            "background: transparent;"
        )

        self._date_lbl = QLabel(self._fmt_date(self.session.updated_at))
        self._date_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 10px; background: transparent;"
        )

        top_row.addWidget(self._pin_lbl)
        top_row.addWidget(self.name_lbl, stretch=1)
        top_row.addWidget(self._date_lbl)

        self.preview_lbl = QLabel(self.session.preview or "")
        self.preview_lbl.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;"
        )

        layout.addLayout(top_row)
        layout.addWidget(self.preview_lbl)

    @staticmethod
    def _fmt_date(iso: str) -> str:
        try:
            dt    = datetime.fromisoformat(iso)
            today = datetime.utcnow().date()
            if dt.date() == today:
                return dt.strftime("%H:%M")
            if dt.date() == today - timedelta(days=1):
                return "Yesterday"
            return dt.strftime("%d %b")
        except Exception:
            return ""

    def refresh(self, session: ChatSession):
        self.session = session
        self.name_lbl.setText(session.name)
        self.preview_lbl.setText(session.preview or "")
        self._date_lbl.setText(self._fmt_date(session.updated_at))
        self._pin_lbl.setText("📌 " if session.pinned else "")


class FoundryManager(QObject):
    """Manages the Foundry Local service via the official Python SDK.
    Handles service start, model load/unload, and dynamic port discovery."""
    status_update = Signal(str, str)   # (text, colour)
    ready         = Signal()
    failed        = Signal(str)

    def __init__(self):
        super().__init__()
        self.model: str = ""          # currently loaded model alias/id
        self._sdk = None               # FoundryLocalManager instance
        self.endpoint: str = ""        # e.g. http://127.0.0.1:PORT/v1
        self.api_key: str = "OPENAI_API_KEY"

    # ── called in a QThread ──────────────────────────────────────────────────
    def start_foundry(self):
        """Initialise the SDK (auto-starts the service) and discover the port."""
        try:
            from foundry_local import FoundryLocalManager
        except ImportError:
            self.failed.emit(
                "The 'foundry-local-sdk' Python package is not installed.\n"
                "Run:  pip install foundry-local-sdk"
            )
            return

        self.status_update.emit("● Starting Foundry Local service…", "#e8660a")
        try:
            # bootstrap=True starts the service if it isn't running.
            # We don't pass a model — the user picks one from the dropdown.
            self._sdk = FoundryLocalManager(bootstrap=True)
            self.endpoint = self._sdk.endpoint   # http://127.0.0.1:PORT/v1
            self.api_key  = self._sdk.api_key
        except Exception as e:
            self.failed.emit(
                f"Could not start Foundry Local:\n{e}\n\n"
                "Make sure Foundry Local is installed:\n"
                "  winget install Microsoft.FoundryLocal"
            )
            return

        self.ready.emit()

    def list_cached(self) -> list:
        """Return the list of locally cached model info objects."""
        if not self._sdk:
            return []
        try:
            return self._sdk.list_cached_models()
        except Exception:
            return []

    def load_model(self, alias_or_id: str):
        """Load a model into the running service. Blocks until ready."""
        if not self._sdk:
            raise RuntimeError("Foundry SDK not initialised")
        self._sdk.load_model(alias_or_id)
        self.model = alias_or_id

    def unload_model(self):
        """Unload the currently loaded model."""
        if self._sdk and self.model:
            try:
                self._sdk.unload_model(self.model)
            except Exception:
                pass
            self.model = ""

    def stop_foundry(self):
        """Clean up: unload the model (service keeps running for other apps)."""
        self.unload_model()


# ── Model name display helper ─────────────────────────────────────────────────
def _model_display_name(alias_or_id: str) -> str:
    """Convert a Foundry alias or model ID to a short display name.
    e.g. 'qwen2.5-7b' → 'Qwen2.5-7b'  (aliases are already short)."""
    name = re.sub(r":\d+$", "", alias_or_id)
    name = re.sub(r"-generic-(?:cpu|gpu)", "", name, flags=re.IGNORECASE)
    name = re.sub(r"-instruct$", "", name, flags=re.IGNORECASE)
    return name[:1].upper() + name[1:] if name else alias_or_id


# ── Model list worker ─────────────────────────────────────────────────────────
class ModelListWorker(QObject):
    """Discovers all models cached locally via the foundry-local-sdk."""
    models_ready = Signal(list)   # list of (alias, model_id) tuples

    def __init__(self, fm: FoundryManager):
        super().__init__()
        self._fm = fm

    def run(self):
        models: list[tuple[str, str]] = []
        try:
            for m in self._fm.list_cached():
                models.append((m.alias, m.id))
        except Exception:
            pass
        self.models_ready.emit(models)


# ── Model switch worker ───────────────────────────────────────────────────────
class ModelSwitchWorker(QObject):
    """Unloads the current model and loads a new one via the SDK."""
    status_update = Signal(str, str)
    done          = Signal(str)   # emits the new model alias on success
    failed        = Signal(str)

    def __init__(self, fm: FoundryManager, old_model: str, new_model: str):
        super().__init__()
        self._fm        = fm
        self._old_model = old_model
        self._new_model = new_model

    def run(self):
        if self._old_model:
            self.status_update.emit("● Stopping current model…", "#e8660a")
            self._fm.unload_model()

        self.status_update.emit("● Loading model…", "#e8660a")
        try:
            self._fm.load_model(self._new_model)
        except Exception as e:
            self.failed.emit(f"Could not load '{self._new_model}': {e}")
            return

        self.done.emit(self._new_model)


# ── Streaming worker ──────────────────────────────────────────────────────────
class StreamWorker(QObject):
    """Runs in a QThread. Emits one signal per token, then finished."""
    token_received = Signal(str)
    finished       = Signal()
    error_occurred = Signal(str)

    def __init__(self, client: OpenAI, messages: list, model_name: str = MODEL_NAME):
        super().__init__()
        self.client     = client
        self.messages   = messages
        self.model_name = model_name
        self._stop      = False

    def stop(self):
        """Request the streaming loop to exit on the next chunk."""
        self._stop = True

    _TRANSIENT_KEYWORDS = [
        "incomplete chunked read", "peer closed", "connection reset",
        "connection was closed", "incomplete message", "remotedisconnected",
    ]

    def _is_transient(self, err: str) -> bool:
        low = err.lower()
        return any(k in low for k in self._TRANSIENT_KEYWORDS)

    def run(self):
        MAX_RETRIES = 1
        for attempt in range(MAX_RETRIES + 1):
            try:
                stream = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=self.messages,
                    stream=True,
                    temperature=0.7,
                    max_tokens=2048,
                    timeout=120.0,   # per-chunk read timeout (seconds)
                )
                for chunk in stream:
                    if self._stop:
                        break
                    delta = chunk.choices[0].delta.content
                    if delta:
                        self.token_received.emit(delta)
                break   # success — exit retry loop
            except Exception as e:
                if self._stop:
                    break
                err = str(e)
                if self._is_transient(err) and attempt < MAX_RETRIES:
                    time.sleep(2)   # brief pause then retry
                    continue
                # Final failure — surface it
                if "timeout" in err.lower() or "timed out" in err.lower():
                    err = ("Model stopped responding (timeout).\n"
                           "The file may be too large. Try a smaller excerpt.")
                elif self._is_transient(err):
                    err = ("Foundry Local dropped the connection.\n"
                           "The model may have run out of memory.\n"
                           "Try with a smaller file or shorter prompt.")
                self.error_occurred.emit(err)
                break
        self.finished.emit()


# ── Startup health-check worker ──────────────────────────────────────────────
class HealthCheckWorker(QObject):
    status_update = Signal(str, str)
    connected    = Signal()
    disconnected = Signal()

    def __init__(self, client: OpenAI, model_name: str = MODEL_NAME):
        super().__init__()
        self.client     = client
        self.model_name = model_name

    def run(self):
        # SDK load_model() is synchronous — the model should be ready.
        # A brief retry loop covers any transient HTTP delay.
        for attempt in range(15):
            try:
                self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[{"role": "user", "content": "."}],
                    max_tokens=1,
                    stream=False,
                )
                self.connected.emit()
                return
            except Exception:
                elapsed = attempt + 1
                self.status_update.emit(
                    f"● Checking model… ({elapsed}s)", "#e8660a"
                )
                if attempt < 14:
                    time.sleep(1)
        self.disconnected.emit()


# ── Bit-dog walking animation ────────────────────────────────────────────────
class BitDogWidget(QWidget):
    """
    Pixel-art dog that walks left and right inside the assistant bubble
    while the model is thinking.  Disappears on the first streamed token.
    """
    SCALE     = 3    # screen-pixels per sprite-grid cell
    W_CELLS   = 13   # sprite width  in grid cells
    H_CELLS   = 10   # sprite height in grid cells
    MOVE_STEP = 2    # px moved per movement tick
    MOVE_MS   = 30   # movement timer interval (ms)
    FRAME_MS  = 160  # walk-cycle timer interval (ms)

    # Right-facing walk frames.  Cell values:
    #   0 = transparent   1 = body (orange)   2 = dark (tail tip)   3 = eye (light)
    # Head faces RIGHT; tail is on the LEFT side of the grid.
    _FR = [
        # Frame A — legs together
        [
            [0,0,0,0,0,0,0,0,0,1,1,0,0],
            [0,0,0,0,0,0,0,0,1,1,1,0,0],
            [0,0,0,0,1,1,1,1,1,3,1,1,0],
            [0,0,0,0,1,1,1,1,1,1,1,1,0],
            [2,2,1,1,1,1,1,1,1,1,1,0,0],
            [0,0,1,1,1,1,1,1,1,1,0,0,0],
            [0,0,1,1,0,0,0,1,1,0,0,0,0],
            [0,0,1,1,0,0,0,1,1,0,0,0,0],
            [0,0,0,1,0,0,0,1,0,0,0,0,0],
            [0,0,0,0,0,0,0,0,0,0,0,0,0],
        ],
        # Frame B — legs spread
        [
            [0,0,0,0,0,0,0,0,0,1,1,0,0],
            [0,0,0,0,0,0,0,0,1,1,1,0,0],
            [0,0,0,0,1,1,1,1,1,3,1,1,0],
            [0,0,0,0,1,1,1,1,1,1,1,1,0],
            [2,2,1,1,1,1,1,1,1,1,1,0,0],
            [0,0,1,1,1,1,1,1,1,1,0,0,0],
            [0,1,1,0,0,0,0,0,1,1,0,0,0],
            [1,1,0,0,0,0,0,0,0,1,1,0,0],
            [1,0,0,0,0,0,0,0,0,0,1,0,0],
            [0,0,0,0,0,0,0,0,0,0,0,0,0],
        ],
    ]

    # Front-facing face frames for sit() mode.
    # 'R' state = ears perked UP, 'L' state = ears flopped DOWN.
    _FS_UP = [
        [0,1,1,0,0,0,0,0,0,0,1,1,0],  # ear tips
        [0,1,1,0,0,0,0,0,0,0,1,1,0],  # ears
        [0,1,1,1,1,1,1,1,1,1,1,1,0],  # head top merges with ears
        [0,0,1,1,1,1,1,1,1,1,1,0,0],  # head
        [0,0,1,3,1,1,1,1,1,3,1,0,0],  # eyes
        [0,0,1,1,1,1,1,1,1,1,1,0,0],  # face
        [0,0,1,1,1,2,2,2,1,1,1,0,0],  # nose
        [0,0,0,1,1,1,1,1,1,1,0,0,0],  # lower face
        [0,0,0,0,1,1,1,1,1,0,0,0,0],  # chin
        [0,0,0,0,0,0,0,0,0,0,0,0,0],  # empty
    ]
    _FS_DOWN = [
        [0,0,0,0,0,0,0,0,0,0,0,0,0],  # empty (ears no longer up)
        [0,0,0,1,1,1,1,1,1,1,0,0,0],  # head top
        [0,1,1,1,1,1,1,1,1,1,1,1,0],  # head full width, ears attach
        [0,1,0,1,1,1,1,1,1,1,0,1,0],  # ears drooping at cols 1 & 11
        [0,1,0,1,3,1,1,1,3,1,0,1,0],  # eyes + ears
        [0,1,0,1,1,1,1,1,1,1,0,1,0],  # face + ears
        [0,0,0,1,1,2,2,2,1,1,0,0,0],  # nose
        [0,0,0,1,1,1,1,1,1,1,0,0,0],  # lower face
        [0,0,0,0,1,1,1,1,1,0,0,0,0],  # chin
        [0,0,0,0,0,0,0,0,0,0,0,0,0],  # empty
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.H_CELLS * self.SCALE + 10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("background: transparent;")

        # Build mirrored left-facing frames
        self._frames = {
            'R': self._FR,
            'L': [[list(reversed(row)) for row in f] for f in self._FR],
        }
        self._sit_frames = {
            'R': self._FS_UP,   # ears perked up
            'L': self._FS_DOWN, # ears flopped down
        }

        self._x     = 0
        self._dir   = 1      # +1 right, -1 left
        self._face  = 'R'
        self._frame = 0
        self._sit   = False

        self._col_body = QColor(ACCENT)
        self._col_dark = QColor(ACCENT).darker(180)
        self._col_eye  = QColor("#d8d8d8")

        self._move_timer  = QTimer(self)
        self._frame_timer = QTimer(self)
        self._sit_timer   = QTimer(self)
        self._move_timer.timeout.connect(self._tick_move)
        self._frame_timer.timeout.connect(self._tick_frame)
        self._sit_timer.timeout.connect(self._tick_sit)

    def start(self):
        self._sit = False
        self._x, self._dir, self._face, self._frame = 0, 1, 'R', 0
        self.show()
        self._sit_timer.stop()
        self._move_timer.start(self.MOVE_MS)
        self._frame_timer.start(self.FRAME_MS)

    def sit(self, flip_ms: int = 2000):
        """Stationary sit pose — flips direction every flip_ms milliseconds."""
        self._sit = True
        self._face  = 'R'
        self._frame = 0
        self._x     = 0
        self.show()
        self._move_timer.stop()
        self._frame_timer.stop()
        self._sit_timer.start(flip_ms)

    def stop(self):
        self._move_timer.stop()
        self._frame_timer.stop()
        self._sit_timer.stop()
        self._sit = False
        self.hide()

    def _tick_sit(self):
        self._face = 'L' if self._face == 'R' else 'R'
        self.update()

    def _tick_move(self):
        sprite_w = self.W_CELLS * self.SCALE
        max_x = max(0, self.width() - sprite_w - 4)
        self._x = max(0, min(self._x + self.MOVE_STEP * self._dir, max_x))
        if self._x >= max_x:
            self._dir, self._face = -1, 'L'
        elif self._x <= 0:
            self._dir, self._face = 1, 'R'
        self.update()

    def _tick_frame(self):
        self._frame = (self._frame + 1) % 2
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if self._sit:
            grid  = self._sit_frames[self._face]
            s     = self.SCALE
            x_off = max(0, (self.width() - self.W_CELLS * s) // 2)
        else:
            grid  = self._frames[self._face][self._frame]
            s     = self.SCALE
            x_off = self._x
        y_off = (self.height() - self.H_CELLS * s) // 2
        for r, row in enumerate(grid):
            for c, cell in enumerate(row):
                if cell == 0:
                    continue
                color = (self._col_body if cell == 1
                         else self._col_dark if cell == 2
                         else self._col_eye)
                painter.fillRect(x_off + c * s, y_off + r * s, s, s, color)
        painter.end()


# ── Message bubble widget ─────────────────────────────────────────────────────
class MessageBubble(QFrame):
    """A single chat message bubble."""

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self.role = role
        self._setup_ui()

    def _setup_ui(self):
        is_user = self.role == "user"

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_USER_MSG if is_user else BG_BOT_MSG};
                border-radius: 10px;
                border: 1px solid {BORDER};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        # Role label
        role_label = QLabel("You" if is_user else "Assistant")
        role_label.setStyleSheet(f"""
            color: {ACCENT if is_user else TEXT_MUTED};
            font-size: 11px;
            font-weight: 600;
            background: transparent;
            border: none;
        """)
        layout.addWidget(role_label)

        # Walking dog — assistant bubbles only, hidden until show_thinking()
        self._thinking = False
        self._dog: BitDogWidget | None = None
        if not is_user:
            self._dog = BitDogWidget(self)
            self._dog.hide()
            layout.addWidget(self._dog)

        # Message text
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFrameShape(QFrame.Shape.NoFrame)
        self.text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Minimum
        )
        self.text_edit.setStyleSheet(f"""
            QTextEdit {{
                color: {TEXT_PRIMARY};
                background-color: transparent;
                border: none;
                font-size: 14px;
                line-height: 1.5;
            }}
        """)
        self.text_edit.document().contentsChanged.connect(self._adjust_height)
        layout.addWidget(self.text_edit)

    def _adjust_height(self):
        doc = self.text_edit.document()
        # viewport().width() is 0 right after show() — fall back to widget width
        width = self.text_edit.viewport().width()
        if width <= 0:
            width = self.text_edit.width()
        if width > 0:
            doc.setTextWidth(width)
        doc_height = int(doc.size().height())
        self.text_edit.setFixedHeight(max(doc_height + 4, 24))
        self.updateGeometry()

    def append_text(self, text: str):
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.text_edit.setTextCursor(cursor)
        self._adjust_height()

    def set_text(self, text: str):
        self.text_edit.setPlainText(text)
        self._adjust_height()

    def show_thinking(self):
        """Show the walking dog above the (empty) text area."""
        if self._dog is None:
            return
        self._thinking = True
        self.text_edit.setFixedHeight(0)   # collapse so bubble fits dog only
        self._dog.start()
        self.updateGeometry()

    def stop_thinking(self):
        """Hide the dog.  Safe to call multiple times."""
        if not self._thinking:
            return
        self._thinking = False
        if self._dog:
            self._dog.stop()
        self.text_edit.setFixedHeight(24)   # restore minimum before text flows in
        self._adjust_height()


# ── Main window ───────────────────────────────────────────────────────────────
class ChatWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Project Vera")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(860, 560)
        self.resize(1100, 720)
        self._drag_pos = QPoint()
        self.setAcceptDrops(True)
        QApplication.instance().installEventFilter(self)

        # OpenAI client — created after the SDK discovers the dynamic port
        self.client: OpenAI | None = None

        # Conversation history sent to the model (in-session only)
        self.history: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Active thread / worker (kept as attrs to avoid GC)
        self._thread = None
        self._worker = None
        self._current_bubble: MessageBubble | None = None

        # Pending file attachment (staged by drag-and-drop, sent with next message)
        self._pending_attachment: dict | None = None  # {name, content, display}

        # Active model (set when the user selects from the combo box)
        self._active_model: str = ""      # alias used for load/unload
        self._active_model_id: str = ""   # full ID used for chat completions
        self._model_map:    dict = {}     # display_name -> alias
        self._id_map:       dict = {}     # alias -> model_id

        # ── Chat history / session state ──────────────────────────────────────
        self._current_session: ChatSession | None = None
        self._sessions: list[ChatSession] = []      # cached sidebar list
        self._session_item_map: dict[str, QListWidgetItem] = {}  # id -> list item
        self._sidebar_collapsed: bool = False
        self._sidebar_width: int = 260

        # ── DB worker on its own thread ───────────────────────────────────────
        self._db_thread = QThread()
        self._db = DBWorker(_DB_PATH)
        self._db.moveToThread(self._db_thread)
        self._db_thread.started.connect(self._db.init_db)
        self._db_thread.started.connect(self._db.load_sessions)
        self._db.sessions_loaded.connect(self._on_sessions_loaded)
        self._db.messages_loaded.connect(self._on_messages_loaded)
        self._db_thread.start()

        # ── Hourly cleanup timer ──────────────────────────────────────────────
        self._cleanup_worker = CleanupWorker(self._db)
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.timeout.connect(self._cleanup_worker.run_cleanup)
        self._cleanup_timer.start(3_600_000)   # 1 hour

        self._setup_ui()
        self._apply_theme()
        self._restore_config()
        self._start_foundry()

    # ── Foundry lifecycle ─────────────────────────────────────────────────────
    def _start_foundry(self):
        """Launch Foundry service; model is loaded when the user selects one."""
        self._fm_thread = QThread()
        self._fm = FoundryManager()
        self._fm.moveToThread(self._fm_thread)
        self._fm_thread.started.connect(self._fm.start_foundry)
        self._fm.status_update.connect(self._on_foundry_status)
        self._fm.ready.connect(self._on_foundry_ready)
        self._fm.failed.connect(self._on_foundry_failed)
        self._fm.ready.connect(self._fm_thread.quit)
        self._fm.failed.connect(self._fm_thread.quit)
        self._fm_thread.finished.connect(self._fm_thread.deleteLater)
        self._fm_thread.start()

    def _on_foundry_status(self, text: str, colour: str):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"color: {colour}; font-size: 11px; background: transparent;"
        )

    def _on_foundry_ready(self):
        # Create the OpenAI client now that the SDK has discovered the port
        self.client = OpenAI(
            base_url=self._fm.endpoint,
            api_key=self._fm.api_key,
        )
        # Populate the model list; model loads when the user picks one
        self._populate_model_combo()
        self.status_label.setText("● Select a model to begin")
        self.status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;"
        )

    def _on_foundry_failed(self, msg: str):
        self.status_label.setText("● Startup failed")
        self.status_label.setStyleSheet(
            "color: #e53935; font-size: 11px; background: transparent;"
        )
        self.retry_btn.setToolTip(msg)
        self.retry_btn.show()

    def _check_connection(self):
        self.status_label.setText("● Connecting…")
        self.status_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;")
        self.retry_btn.hide()
        self._hc_thread = QThread()
        # Use the full model ID for the health check (that's what the endpoint expects)
        model_id = getattr(self, '_active_model_id', self._active_model)
        self._hc_worker = HealthCheckWorker(self.client, model_id)
        self._hc_worker.moveToThread(self._hc_thread)
        self._hc_thread.started.connect(self._hc_worker.run)
        self._hc_worker.status_update.connect(self._on_foundry_status)
        self._hc_worker.connected.connect(self._on_connected)
        self._hc_worker.disconnected.connect(self._on_disconnected)
        self._hc_worker.connected.connect(self._hc_thread.quit)
        self._hc_worker.disconnected.connect(self._hc_thread.quit)
        self._hc_worker.connected.connect(self._hc_worker.deleteLater)
        self._hc_worker.disconnected.connect(self._hc_worker.deleteLater)
        self._hc_thread.finished.connect(self._hc_thread.deleteLater)
        self._hc_thread.start()

    def _on_connected(self):
        self.status_label.setText("● Ready")
        self.status_label.setStyleSheet(f"color: #4caf50; font-size: 11px; background: transparent;")
        self.retry_btn.hide()
        self.send_btn.setEnabled(True)  # safe for both startup and post-switch

    def _on_disconnected(self):
        if self._active_model:
            # A model was chosen but the endpoint never warmed up.
            self.status_label.setText("● Model not responding — click ↻ to retry")
            self.status_label.setStyleSheet(f"color: #e53935; font-size: 11px; background: transparent;")
            self.retry_btn.setToolTip(
                f"The inference endpoint for '{self._active_model}' did not respond\n"
                "within 60 s. Click to try warming it up again."
            )
            # Retry should re-run the health check, NOT restart the whole service
            try:
                self.retry_btn.clicked.disconnect()
            except Exception:
                pass
            self.retry_btn.clicked.connect(self._retry_connection)
        else:
            self.status_label.setText("● Foundry Local not running")
            self.status_label.setStyleSheet(f"color: #e53935; font-size: 11px; background: transparent;")
            self.retry_btn.setToolTip(
                "Foundry Local service is not reachable.\n"
                "Click here to try starting it again."
            )
            try:
                self.retry_btn.clicked.disconnect()
            except Exception:
                pass
            self.retry_btn.clicked.connect(self._start_foundry)
        self.retry_btn.show()

    def _retry_connection(self):
        """Re-run just the health check without restarting the whole service."""
        self.retry_btn.hide()
        self._check_connection()

    # ── Config save / restore ─────────────────────────────────────────────────
    def _restore_config(self):
        cfg = _load_config()
        w = cfg.get("window", {})
        if w.get("width") and w.get("height"):
            self.resize(w["width"], w["height"])
        sidebar_cfg = cfg.get("sidebar", {})
        self._sidebar_width     = sidebar_cfg.get("width", 260) or 260
        self._sidebar_collapsed = sidebar_cfg.get("collapsed", False)
        actual_w = 0 if self._sidebar_collapsed else self._sidebar_width
        self._sidebar.setMinimumWidth(actual_w)
        self._sidebar.setMaximumWidth(actual_w)
        self._burger_btn.setToolTip(
            "Show history panel  (Ctrl+\\)" if self._sidebar_collapsed
            else "Hide history panel  (Ctrl+\\)"
        )
        # Always start with a fresh blank chat — never restore last session
        self._last_session_id = ""

    # ── Sidebar toggle ────────────────────────────────────────────────────────
    def _toggle_sidebar(self):
        self._sidebar_collapsed = not self._sidebar_collapsed
        # Preserve last non-zero width so expand restores correct size
        if not self._sidebar_collapsed and self._sidebar_width == 0:
            self._sidebar_width = 260
        target_w = 0 if self._sidebar_collapsed else self._sidebar_width
        start_w  = self._sidebar.width()

        anim  = QPropertyAnimation(self._sidebar, b"minimumWidth", self)
        anim2 = QPropertyAnimation(self._sidebar, b"maximumWidth", self)
        for a in (anim, anim2):
            a.setDuration(220)
            a.setEasingCurve(QEasingCurve.Type.InOutQuad)
            a.setStartValue(start_w)
            a.setEndValue(target_w)
            a.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

        # Update burger tooltip
        collapsed = self._sidebar_collapsed
        self._burger_btn.setToolTip(
            "Show history panel  (Ctrl+\\)" if collapsed
            else "Hide history panel  (Ctrl+\\)"
        )

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Backslash and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._toggle_sidebar()
        elif event.key() == Qt.Key.Key_N and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self._new_chat()
        else:
            super().keyPressEvent(event)

    # ── Session DB callbacks ──────────────────────────────────────────────────
    def _on_sessions_loaded(self, sessions: list):
        self._sessions = sessions
        self._session_item_map.clear()
        self._session_list.clear()
        for s in sessions:
            self._add_session_to_list(s)
        # Restore last open session
        last_id = getattr(self, "_last_session_id", "")
        if last_id:
            self._open_session_by_id(last_id)

    def _on_messages_loaded(self, session_id: str, msgs: list):
        """Restore a full session's messages into the chat feed."""
        if not self._current_session or self._current_session.id != session_id:
            return
        # Rebuild history (system prompt + loaded messages)
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}] + msgs
        # Re-draw feed
        self._clear_feed()
        for msg in msgs:
            bubble = self._add_bubble(msg["role"])
            bubble.set_text(msg["content"])
        self._scroll_to_bottom()

    # ── Session CRUD ──────────────────────────────────────────────────────────
    def _new_chat(self):
        """Start a fresh session (not saved to DB until first message)."""
        self._current_session = ChatSession(model_name=self._active_model)
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._clear_feed()
        self._pending_attachment = None
        self.attach_bar.hide()
        self.input_field.setPlaceholderText("How can I help you today?")
        self.input_field.setFocus()
        # Deselect list
        self._session_list.clearSelection()

    def _open_session_by_id(self, session_id: str):
        item = self._session_item_map.get(session_id)
        if item:
            self._session_list.setCurrentItem(item)
            self._on_session_clicked(item)

    def _on_session_clicked(self, item: QListWidgetItem):
        widget = self._session_list.itemWidget(item)
        if not widget:
            return
        session = widget.session
        if self._current_session and self._current_session.id == session.id:
            return   # already open
        self._current_session = session
        self.history = [{"role": "system", "content": SYSTEM_PROMPT}]
        self._clear_feed()
        # Load messages from DB in background
        self._db.load_messages(session.id)

    def _add_session_to_list(self, session: ChatSession, prepend: bool = False) -> QListWidgetItem:
        item = QListWidgetItem()
        widget = HistoryItemWidget(session)
        item.setSizeHint(QSize(self._sidebar_width, 62))
        self._session_item_map[session.id] = item
        if prepend:
            self._session_list.insertItem(0, item)
        else:
            self._session_list.addItem(item)
        self._session_list.setItemWidget(item, widget)
        return item

    def _update_session_in_list(self, session: ChatSession):
        """Move updated session to top of list and refresh its widget."""
        item = self._session_item_map.get(session.id)
        if not item:
            self._add_session_to_list(session, prepend=True)
            return
        row = self._session_list.row(item)
        if row != 0:
            self._session_list.takeItem(row)
            self._session_list.insertItem(0, item)
            self._session_item_map[session.id] = item
        widget = self._session_list.itemWidget(item)
        if widget:
            widget.refresh(session)

    def _filter_sessions(self, text: str):
        for i in range(self._session_list.count()):
            item = self._session_list.item(i)
            widget = self._session_list.itemWidget(item)
            if widget:
                match = (text.lower() in widget.session.name.lower()
                         or text.lower() in (widget.session.preview or "").lower())
                item.setHidden(not match if text else False)

    def _on_session_context_menu(self, pos):
        item = self._session_list.itemAt(pos)
        if not item:
            return
        widget = self._session_list.itemWidget(item)
        if not widget:
            return
        session = widget.session

        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {BG_PANEL}; color: {TEXT_PRIMARY};
                border: 1px solid {BORDER}; border-radius: 6px; padding: 4px;
            }}
            QMenu::item {{ padding: 6px 20px; border-radius: 4px; }}
            QMenu::item:selected {{ background-color: rgba(232,102,10,0.25); }}
        """)

        rename_act = menu.addAction("✏  Rename")
        pin_act    = menu.addAction("📌  Unpin" if session.pinned else "📌  Pin")
        menu.addSeparator()
        delete_act = menu.addAction("🗑  Delete")

        action = menu.exec(self._session_list.mapToGlobal(pos))

        if action == rename_act:
            new_name, ok = QInputDialog.getText(
                self, "Rename", "Session name:", text=session.name
            )
            if ok and new_name.strip():
                session.name = new_name.strip()
                widget.refresh(session)
                self._db.rename_session(session.id, session.name)

        elif action == pin_act:
            session.pinned = not session.pinned
            widget.refresh(session)
            self._db.toggle_pin(session.id, session.pinned)
            self._db.load_sessions()   # re-sort (pinned first)

        elif action == delete_act:
            reply = QMessageBox.question(
                self, "Delete session",
                f"Delete '{session.name}'?\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._db.archive_session(session.id)
                row = self._session_list.row(item)
                self._session_list.takeItem(row)
                del self._session_item_map[session.id]
                if self._current_session and self._current_session.id == session.id:
                    self._new_chat()

    def _clear_feed(self):
        """Remove all message bubbles from the feed; restore welcome label."""
        while self.feed_layout.count() > 1:   # keep the trailing stretch
            item = self.feed_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Re-add welcome label
        welcome = QLabel("Where should we begin?")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome.setWordWrap(True)
        welcome.setStyleSheet(
            f"color: {ACCENT}; font-size: 42px; font-weight: 700; "
            "background: transparent; padding: 40px 20px;"
        )
        self.feed_layout.insertWidget(0, welcome)
        self._welcome_label = welcome

    # ── Model switcher ────────────────────────────────────────────────────────
    def _populate_model_combo(self):
        """Fetch available models from the SDK and populate the combo box."""
        self._ml_thread = QThread()
        self._ml_worker = ModelListWorker(self._fm)
        self._ml_worker.moveToThread(self._ml_thread)
        self._ml_thread.started.connect(self._ml_worker.run)
        self._ml_worker.models_ready.connect(self._on_models_ready)
        self._ml_worker.models_ready.connect(self._ml_thread.quit)
        self._ml_worker.models_ready.connect(self._ml_worker.deleteLater)
        self._ml_thread.finished.connect(self._ml_thread.deleteLater)
        self._ml_thread.start()

    def _on_models_ready(self, models: list):
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self._model_map = {}   # display_name -> alias (used for load_model)
        self._id_map   = {}   # alias -> model_id (used for chat completions)
        if models:
            self.model_combo.addItem("— Select model —")
            for alias, model_id in models:
                display = _model_display_name(alias)
                self._model_map[display] = alias
                self._id_map[alias] = model_id
                self.model_combo.addItem(display)
            self.model_combo.setCurrentIndex(0)
            self.model_combo.blockSignals(False)
            self.model_combo.show()
        else:
            self.model_combo.blockSignals(False)
            self.status_label.setText("● No models found — download one in AI Toolkit")
            self.status_label.setStyleSheet(
                "color: #e53935; font-size: 11px; background: transparent;"
            )
            self.retry_btn.show()
            self.retry_btn.setToolTip(
                "No models found in Foundry cache.\n"
                "Download a model via AI Toolkit → Models, then click ↻."
            )

    def _on_model_combo_changed(self, display_name: str):
        if display_name == "— Select model —":
            return
        new_model = self._model_map.get(display_name)
        if new_model and new_model != self._active_model:
            self._switch_model(new_model)

    def _switch_model(self, new_model: str):
        self.send_btn.setEnabled(False)
        self.model_combo.setEnabled(False)
        self._sw_thread = QThread()
        self._sw_worker = ModelSwitchWorker(self._fm, self._active_model, new_model)
        self._sw_worker.moveToThread(self._sw_thread)
        self._sw_thread.started.connect(self._sw_worker.run)
        self._sw_worker.status_update.connect(self._on_foundry_status)
        self._sw_worker.done.connect(self._on_switch_done)
        self._sw_worker.failed.connect(self._on_switch_failed)
        self._sw_worker.done.connect(self._sw_thread.quit)
        self._sw_worker.failed.connect(self._sw_thread.quit)
        self._sw_worker.done.connect(self._sw_worker.deleteLater)
        self._sw_worker.failed.connect(self._sw_worker.deleteLater)
        self._sw_thread.finished.connect(self._sw_thread.deleteLater)
        self._sw_thread.start()

    def _on_switch_done(self, new_model: str):
        self._active_model = new_model
        # Resolve the full model ID for use in chat completions
        self._active_model_id = self._id_map.get(new_model, new_model)
        self._fm.model = new_model  # keep FoundryManager in sync for graceful shutdown
        # Remove the "— Select model —" placeholder on first successful load
        placeholder_idx = self.model_combo.findText("— Select model —")
        if placeholder_idx >= 0:
            self.model_combo.blockSignals(True)
            self.model_combo.removeItem(placeholder_idx)
            idx = self.model_combo.findText(_model_display_name(new_model))
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            self.model_combo.blockSignals(False)
        self.model_combo.setEnabled(True)
        # Leave send_btn disabled — _check_connection enables it only once the
        # inference endpoint for the new model is actually warm.
        self._check_connection()

    def _on_switch_failed(self, error: str):
        # Revert the combo to the model that is still actually loaded
        self.model_combo.blockSignals(True)
        idx = self.model_combo.findText(_model_display_name(self._active_model))
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        self.model_combo.blockSignals(False)
        self.model_combo.setEnabled(True)
        self.send_btn.setEnabled(True)
        self.status_label.setText("● Switch failed")
        self.status_label.setStyleSheet("color: #e53935; font-size: 11px; background: transparent;")
        self.retry_btn.setToolTip(error)
        self.retry_btn.show()

    # ── UI construction ───────────────────────────────────────────────────────
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(12, 12, 12, 12)  # space for shadow bleed
        outer.setSpacing(0)

        # ── Bordered container ──────────────────────────────────────────────
        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setStyleSheet(f"""
            QFrame#container {{
                background-color: {BG_MAIN};
                border: 1px solid {ACCENT};
                border-radius: 10px;
            }}
        """)
        root = QVBoxLayout(self.container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        outer.addWidget(self.container)

        # ── Title bar ───────────────────────────────────────────────────────
        self.title_bar = QFrame()
        self.title_bar.setFixedHeight(48)
        self.title_bar.setStyleSheet(
            f"background-color: {BG_PANEL}; "
            f"border-bottom: 1px solid {BORDER}; "
            f"border-top-left-radius: 10px; "
            f"border-top-right-radius: 10px;"
        )
        self.title_bar.mousePressEvent       = self._tb_mouse_press
        self.title_bar.mouseMoveEvent        = self._tb_mouse_move
        self.title_bar.mouseDoubleClickEvent = self._tb_double_click

        h_layout = QHBoxLayout(self.title_bar)
        h_layout.setContentsMargins(16, 0, 8, 0)
        h_layout.setSpacing(8)

        title_dog = BitDogWidget()
        title_dog.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        title_dog.setFixedWidth(46)

        title = QLabel("Project Vera")
        title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: 700; background: transparent;"
        )

        self.status_label = QLabel("● Connecting…")
        self.status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;"
        )

        self.retry_btn = QPushButton("↻")
        self.retry_btn.setFixedSize(26, 26)
        self.retry_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.retry_btn.setToolTip("Retry: start Foundry service and load model")
        self.retry_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                color: {TEXT_MUTED}; font-size: 16px;
                border-radius: 6px; padding: 0;
            }}
            QPushButton:hover {{
                background-color: rgba(232,102,10,0.15); color: {ACCENT};
            }}
        """)
        self.retry_btn.clicked.connect(self._start_foundry)
        self.retry_btn.hide()

        self.model_combo = QComboBox()
        self.model_combo.setFixedHeight(28)
        self.model_combo.setMinimumWidth(160)
        self.model_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_combo.setToolTip("Switch active model")
        self.model_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {BG_INPUT}; color: {TEXT_PRIMARY};
                border: 1px solid {BORDER}; border-radius: 6px;
                padding: 0 8px; font-size: 12px;
            }}
            QComboBox:hover {{ border: 1px solid {ACCENT}; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background-color: {BG_PANEL}; color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                selection-background-color: rgba(232,102,10,0.3);
            }}
        """)
        self.model_combo.currentTextChanged.connect(self._on_model_combo_changed)
        self.model_combo.hide()

        btn_style = """
            QPushButton {{
                background: transparent; border: none; color: {accent};
                font-size: %(size)s;
                min-width: 30px; max-width: 30px;
                min-height: 30px; max-height: 30px;
                border-radius: 6px; padding: 0;
            }}
            QPushButton:hover {{ background-color: rgba(232,102,10,0.15); }}
        """.format(accent=ACCENT)

        min_btn = QPushButton("⎯")
        min_btn.setStyleSheet(btn_style % {"size": "15px"})
        min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        min_btn.setToolTip("Minimize")
        min_btn.clicked.connect(self.showMinimized)

        max_btn = QPushButton("□")
        self._max_btn = max_btn
        max_btn.setStyleSheet(btn_style % {"size": "16px"})
        max_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        max_btn.setToolTip("Maximise")
        max_btn.clicked.connect(self._toggle_maximise)

        close_btn = QPushButton("✕")
        close_btn.setStyleSheet(
            btn_style % {"size": "14px"} +
            "QPushButton:hover { background-color: #c0392b; color: #ffffff; }"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setToolTip("Close")
        close_btn.clicked.connect(self.close)

        # Burger button — always visible, toggles sidebar
        self._burger_btn = QPushButton("☰")
        self._burger_btn.setFixedSize(32, 32)
        self._burger_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._burger_btn.setToolTip("Toggle history panel  (Ctrl+\\)")
        self._burger_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none;
                color: {TEXT_MUTED}; font-size: 18px;
                border-radius: 6px; padding: 0;
            }}
            QPushButton:hover {{
                background-color: rgba(232,102,10,0.15); color: {ACCENT};
            }}
        """)
        self._burger_btn.clicked.connect(self._toggle_sidebar)

        h_layout.addWidget(self._burger_btn)
        h_layout.addSpacing(4)
        h_layout.addWidget(title_dog)
        h_layout.addWidget(title)
        h_layout.addSpacing(12)
        h_layout.addWidget(self.model_combo)
        h_layout.addStretch()
        h_layout.addWidget(self.status_label)
        h_layout.addWidget(self.retry_btn)
        h_layout.addSpacing(12)
        h_layout.addWidget(min_btn)
        h_layout.addWidget(max_btn)
        h_layout.addWidget(close_btn)
        root.addWidget(self.title_bar)
        title_dog.sit(flip_ms=2000)

        # ── Body: sidebar + chat pane ───────────────────────────────────────
        body = QWidget()
        body.setStyleSheet("background: transparent;")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        root.addWidget(body, stretch=1)

        # ── Left sidebar ────────────────────────────────────────────────────
        self._sidebar = QFrame()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(self._sidebar_width)
        self._sidebar.setStyleSheet(f"""
            QFrame#sidebar {{
                background-color: {BG_PANEL};
                border-right: 1px solid {BORDER};
                border-bottom-left-radius: 10px;
            }}
        """)
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(0)

        # Sidebar header
        sb_header = QFrame()
        sb_header.setFixedHeight(48)
        sb_header.setStyleSheet(
            f"background-color: {BG_PANEL}; border-bottom: 1px solid {BORDER};"
        )
        sb_h = QHBoxLayout(sb_header)
        sb_h.setContentsMargins(14, 0, 8, 0)
        sb_h.setSpacing(6)

        sb_title = QLabel("History")
        sb_title.setStyleSheet(
            f"color: {TEXT_PRIMARY}; font-size: 13px; font-weight: 600; background: transparent;"
        )

        sb_h.addWidget(sb_title, stretch=1)
        sidebar_layout.addWidget(sb_header)

        # Search bar
        self._search_field = QLineEdit()
        self._search_field.setPlaceholderText("Search conversations…")
        self._search_field.setFixedHeight(34)
        self._search_field.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_INPUT}; color: {TEXT_PRIMARY};
                border: none; border-bottom: 1px solid {BORDER};
                padding: 0 12px; font-size: 12px;
            }}
            QLineEdit:focus {{ border-bottom: 1px solid {ACCENT}; }}
        """)
        self._search_field.textChanged.connect(self._filter_sessions)
        sidebar_layout.addWidget(self._search_field)

        # Session list
        self._session_list = QListWidget()
        self._session_list.setFrameShape(QFrame.Shape.NoFrame)
        self._session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._session_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {BG_PANEL}; border: none; outline: none;
            }}
            QListWidget::item {{
                border-bottom: 1px solid {BORDER};
                padding: 0;
            }}
            QListWidget::item:hover {{
                background-color: #2e2e2e;
            }}
            QListWidget::item:selected {{
                background-color: #1f1f1f;
                border-left: 3px solid {ACCENT};
            }}
        """)
        self._session_list.verticalScrollBar().setStyleSheet(f"""
            QScrollBar:vertical {{
                background: {BG_PANEL}; width: 4px; border-radius: 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER}; border-radius: 2px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self._session_list.itemClicked.connect(self._on_session_clicked)
        self._session_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._session_list.customContextMenuRequested.connect(self._on_session_context_menu)
        sidebar_layout.addWidget(self._session_list, stretch=1)

        # New Chat button
        new_chat_btn = QPushButton("+ New Chat")
        new_chat_btn.setFixedHeight(42)
        new_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_chat_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {ACCENT};
                border: none;
                border-top: 1px solid {BORDER};
                border-bottom-left-radius: 10px;
                font-size: 13px; font-weight: 600; padding: 0 16px;
                letter-spacing: 0.5px;
            }}
            QPushButton:hover {{
                background-color: rgba(232, 102, 10, 0.12);
                color: {ACCENT_HOVER};
            }}
            QPushButton:pressed {{
                background-color: rgba(232, 102, 10, 0.22);
            }}
        """)
        new_chat_btn.clicked.connect(self._new_chat)
        sidebar_layout.addWidget(new_chat_btn)

        body_layout.addWidget(self._sidebar)

        # ── Right: chat area ────────────────────────────────────────────────
        chat_frame = QFrame()
        chat_frame.setStyleSheet("background: transparent;")
        chat_layout = QVBoxLayout(chat_frame)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)
        body_layout.addWidget(chat_frame, stretch=1)

        # Scroll area (message feed)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet(f"background-color: {BG_MAIN}; border: none;")
        self.scroll_area.verticalScrollBar().setStyleSheet(f"""
            QScrollBar:vertical {{
                background: {BG_MAIN}; width: 6px; border-radius: 3px;
            }}
            QScrollBar::handle:vertical {{
                background: {BORDER}; border-radius: 3px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)

        self.feed_widget = QWidget()
        self.feed_widget.setStyleSheet(f"background-color: {BG_MAIN};")
        self.feed_layout = QVBoxLayout(self.feed_widget)
        self.feed_layout.setContentsMargins(60, 20, 60, 20)
        self.feed_layout.setSpacing(10)
        self.feed_layout.addStretch()
        self.scroll_area.setWidget(self.feed_widget)
        chat_layout.addWidget(self.scroll_area, stretch=1)

        # Welcome message
        welcome = QLabel("Where should we begin?")
        welcome.setAlignment(Qt.AlignmentFlag.AlignCenter)
        welcome.setWordWrap(True)
        welcome.setStyleSheet(
            f"color: {ACCENT}; font-size: 42px; font-weight: 700; "
            "background: transparent; padding: 40px 20px;"
        )
        self.feed_layout.insertWidget(0, welcome)
        self._welcome_label = welcome

        # Input bar
        input_bar = QFrame()
        input_bar.setStyleSheet(
            f"background-color: {BG_PANEL}; border-top: 1px solid {BORDER}; "
            f"border-bottom-left-radius: 10px; border-bottom-right-radius: 10px;"
        )
        i_outer = QVBoxLayout(input_bar)
        i_outer.setContentsMargins(20, 8, 20, 12)
        i_outer.setSpacing(6)

        self.attach_bar = QFrame()
        self.attach_bar.setStyleSheet("background: transparent;")
        attach_row = QHBoxLayout(self.attach_bar)
        attach_row.setContentsMargins(0, 0, 0, 0)
        attach_row.setSpacing(6)

        self.attach_chip = QLabel()
        self.attach_chip.setStyleSheet(f"""
            QLabel {{
                background-color: {BG_INPUT}; color: {ACCENT};
                border: 1px solid {ACCENT}; border-radius: 6px;
                padding: 2px 10px; font-size: 12px;
            }}
        """)

        self.attach_clear_btn = QPushButton("✕")
        self.attach_clear_btn.setFixedSize(22, 22)
        self.attach_clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attach_clear_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none;
                color: {TEXT_MUTED}; font-size: 12px; }}
            QPushButton:hover {{ color: #e53935; }}
        """)
        self.attach_clear_btn.setToolTip("Remove attachment")
        self.attach_clear_btn.clicked.connect(self._clear_attachment)

        attach_row.addWidget(self.attach_chip)
        attach_row.addWidget(self.attach_clear_btn)
        attach_row.addStretch()
        self.attach_bar.hide()
        i_outer.addWidget(self.attach_bar)

        i_layout = QHBoxLayout()
        i_layout.setContentsMargins(0, 0, 0, 0)
        i_layout.setSpacing(10)
        i_outer.addLayout(i_layout)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("How can I help you today?")
        self.input_field.setFixedHeight(42)
        self.input_field.setStyleSheet(f"""
            QLineEdit {{
                background-color: {BG_INPUT}; color: {TEXT_PRIMARY};
                border: 1px solid {BORDER}; border-radius: 8px;
                padding: 0 14px; font-size: 14px;
            }}
            QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
        """)
        self.input_field.returnPressed.connect(self._send_message)

        self.send_btn = QPushButton("Send")
        self.send_btn.setFixedSize(80, 42)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT}; color: #ffffff;
                border: none; border-radius: 8px;
                font-size: 14px; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
            QPushButton:disabled {{ background-color: {BORDER}; color: {TEXT_MUTED}; }}
        """)
        self.send_btn.clicked.connect(self._send_message)
        self.send_btn.setEnabled(False)

        i_layout.addWidget(self.input_field)
        i_layout.addWidget(self.send_btn)
        chat_layout.addWidget(input_bar)

        self._setup_drop_overlay()


    # ── Drop-zone overlay ─────────────────────────────────────────────────────
    def _setup_drop_overlay(self):
        """Translucent overlay shown when a supported file is dragged over the window."""
        self._drop_overlay = QLabel(self)
        self._drop_overlay.setText("⬇   Drop file here")
        self._drop_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._drop_overlay.setStyleSheet(f"""
            QLabel {{
                background-color: rgba(26, 26, 26, 0.90);
                color: {ACCENT};
                font-size: 28px;
                font-weight: 700;
                border: 2px dashed {ACCENT};
                border-radius: 10px;
            }}
        """)
        self._drop_overlay.hide()
        self._position_overlay()

    def _position_overlay(self):
        if hasattr(self, '_drop_overlay'):
            m = 12  # matches the outer shadow-bleed margin
            self._drop_overlay.setGeometry(m, m, self.width() - 2 * m, self.height() - 2 * m)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_overlay()

    # ── Drag & Drop ───────────────────────────────────────────────────────────
    _SUPPORTED_EXTS = {
        '.txt', '.csv', '.md', '.py', '.json', '.log',
        '.html', '.xml', '.yaml', '.yml', '.toml',
        '.ini', '.cfg', '.pdf',
    }

    def dragEnterEvent(self, event):
        md = event.mimeData()
        if md.hasUrls() and any(
            Path(u.toLocalFile()).suffix.lower() in self._SUPPORTED_EXTS
            for u in md.urls()
        ):
            event.acceptProposedAction()
            self._set_drop_highlight(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drop_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drop_highlight(False)
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if local and Path(local).suffix.lower() in self._SUPPORTED_EXTS:
                self._send_file_message(local)
                break  # one file at a time
        event.acceptProposedAction()

    def _set_drop_highlight(self, active: bool):
        if hasattr(self, '_drop_overlay'):
            if active:
                self._drop_overlay.show()
                self._drop_overlay.raise_()
            else:
                self._drop_overlay.hide()

    # ── File content reader ───────────────────────────────────────────────────
    @staticmethod
    def _read_file_content(path: str) -> str:
        ext = Path(path).suffix.lower()
        if ext == '.pdf':
            try:
                from pypdf import PdfReader
                reader = PdfReader(path)
                pages = [page.extract_text() or "" for page in reader.pages]
                return "\n\n".join(pages).strip()
            except ImportError:
                return "[PDF support not installed. Run: pip install pypdf]"
            except Exception as e:
                return f"[Could not read PDF: {e}]"
        else:
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    return f.read()
            except Exception as e:
                return f"[Could not read file: {e}]"

    def _send_file_message(self, path: str):
        """Stage a dropped file as a pending attachment — does NOT send yet."""
        if not self.send_btn.isEnabled():
            return  # don't interrupt active streaming

        name = Path(path).name
        raw = self._read_file_content(path)

        MAX_CHARS = 12_000
        truncated = len(raw) > MAX_CHARS
        content = raw[:MAX_CHARS] if truncated else raw

        display = f"📎 {name}" + (f"  · first {MAX_CHARS:,} chars" if truncated else "")
        self._pending_attachment = {"name": name, "content": content, "truncated": truncated}

        self.attach_chip.setText(display)
        self.attach_bar.show()
        self.input_field.setFocus()
        self.input_field.setPlaceholderText("Add a message for the attached file, then Send…")

    def _clear_attachment(self):
        self._pending_attachment = None
        self.attach_bar.hide()
        self.input_field.setPlaceholderText("How can I help you today?")

    # ── Frameless window drag & controls ─────────────────────────────────────
    def _tb_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _tb_mouse_move(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton and not self._drag_pos.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def _tb_double_click(self, event):
        self._toggle_maximise()

    # ── Edge / corner resize ──────────────────────────────────────────────────
    # The outer layout has a 12px transparent shadow margin, so the visible
    # container starts at (12, 12).  Corner zones must lie inside that content.
    _SHADOW_MARGIN  = 12   # transparent bleed around the container
    _RESIZE_MARGIN  = 22   # extra px of visible content included in corner zone

    def _resize_edge_at(self, pos: QPoint):
        """Return corner tag or None.  Zone starts at shadow edge into content."""
        if self.isMaximized():
            return None
        sh = self._SHADOW_MARGIN
        m  = self._RESIZE_MARGIN
        x, y, w, h = pos.x(), pos.y(), self.width(), self.height()
        left   = x < sh + m
        right  = x >= w - sh - m
        top    = y < sh + m
        bottom = y >= h - sh - m
        if top    and left:  return 'tl'
        if top    and right: return 'tr'
        if bottom and left:  return 'bl'
        if bottom and right: return 'br'
        return None

    _EDGE_CURSORS = {
        'tl': Qt.CursorShape.SizeFDiagCursor,
        'br': Qt.CursorShape.SizeFDiagCursor,
        'tr': Qt.CursorShape.SizeBDiagCursor,
        'bl': Qt.CursorShape.SizeBDiagCursor,
    }

    def eventFilter(self, obj, event):
        """App-wide filter: show resize cursor + start native corner resize."""
        etype = event.type()
        if etype == QEvent.Type.MouseMove:
            try:
                gpos = event.globalPosition().toPoint()
            except AttributeError:
                return False
            pos = self.mapFromGlobal(gpos)
            edge = self._resize_edge_at(pos)
            if edge:
                self.setCursor(self._EDGE_CURSORS[edge])
            else:
                self.unsetCursor()
        elif etype == QEvent.Type.MouseButtonPress:
            try:
                if event.button() != Qt.MouseButton.LeftButton:
                    return False
                gpos = event.globalPosition().toPoint()
            except AttributeError:
                return False
            pos  = self.mapFromGlobal(gpos)
            edge = self._resize_edge_at(pos)
            if edge:
                self._start_corner_resize(edge)
                return True
        return False

    def _start_corner_resize(self, edge):
        """Tell Windows to start a native resize from the given corner."""
        if sys.platform != "win32":
            return
        import ctypes
        # WMSZ constants: 4=TOPLEFT 5=TOPRIGHT 7=BOTTOMLEFT 8=BOTTOMRIGHT
        dir_map = {'tl': 4, 'tr': 5, 'bl': 7, 'br': 8}
        hwnd = int(self.winId())
        ctypes.windll.user32.ReleaseCapture()
        ctypes.windll.user32.SendMessageW(
            hwnd, 0x0112, 0xF000 + dir_map[edge], 0
        )

    def _toggle_maximise(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            self._apply_win32_thick_frame()
            # Delay until the Win32 taskbar button has been created
            QTimer.singleShot(150, self._apply_win32_taskbar_icon)

    def nativeEvent(self, eventType, message):
        """Handle WM_NCCALCSIZE + WM_NCHITTEST for Snap Layout support."""
        if sys.platform == "win32" and eventType == b"windows_generic_MSG":
            import ctypes
            import ctypes.wintypes as wt

            class _MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd",    wt.HWND),
                    ("message", wt.UINT),
                    ("wParam",  wt.WPARAM),
                    ("lParam",  wt.LPARAM),
                    ("time",    wt.DWORD),
                    ("ptX",     wt.LONG),
                    ("ptY",     wt.LONG),
                ]

            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents

            if msg.message == 0x0083 and msg.wParam:  # WM_NCCALCSIZE
                # Return 0 → entire window rect is client area; strips native
                # title bar chrome while keeping WS_CAPTION snap capabilities.
                return True, 0

            if msg.message == 0x0084:  # WM_NCHITTEST
                sx = ctypes.c_int16(msg.lParam & 0xFFFF).value
                sy = ctypes.c_int16((msg.lParam >> 16) & 0xFFFF).value
                pos = self.mapFromGlobal(QPoint(sx, sy))
                sh  = self._SHADOW_MARGIN  # 12 px transparent shadow margin

                # Maximize button → triggers Win11 Snap Layout flyout on hover
                if hasattr(self, '_max_btn') and not self.isMaximized():
                    bp = self._max_btn.mapTo(self, QPoint(0, 0))
                    br = QRect(bp, self._max_btn.size())
                    if br.contains(pos):
                        return True, 9  # HTMAXBUTTON

                # Title bar → AeroSnap drag-to-edge / snap zones
                if (sh < pos.x() < self.width() - sh
                        and sh < pos.y() < sh + self.title_bar.height()):
                    # Let interactive controls (buttons, combo) handle their
                    # own clicks — only treat empty space as caption.
                    child = self.childAt(pos)
                    if child is not None:
                        from PySide6.QtWidgets import QAbstractButton, QComboBox
                        if isinstance(child, (QAbstractButton, QComboBox)):
                            return True, 1  # HTCLIENT
                    return True, 2  # HTCAPTION

        return super().nativeEvent(eventType, message)

    def _apply_win32_thick_frame(self):
        """Add WS_THICKFRAME + WS_CAPTION so Windows enables Snap Layout."""
        try:
            import ctypes
            GWL_STYLE     = -16
            WS_THICKFRAME = 0x00040000
            WS_CAPTION    = 0x00C00000  # required for snap layout
            hwnd  = int(self.winId())
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_CAPTION)
            # Notify Windows of frame change without moving/sizing the window
            ctypes.windll.user32.SetWindowPos(
                hwnd, None, 0, 0, 0, 0,
                0x0001 | 0x0002 | 0x0004 | 0x0020  # NOSIZE|NOMOVE|NOZORDER|FRAMECHANGED
            )
        except Exception:
            pass

    def _apply_win32_taskbar_icon(self):
        """Force the correct icon onto the Win32 taskbar button.
        Frameless + translucent windows need this done after the event loop
        has processed the show event, otherwise the taskbar button doesn't
        exist yet and the call is a no-op."""
        try:
            icon_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "Logo", "icon.ico"
            )
            if not os.path.exists(icon_path):
                return

            hwnd = int(self.winId())

            # 1. Ensure WS_EX_APPWINDOW is set (groups window under our icon,
            #    not python.exe) and WS_EX_TOOLWINDOW is clear.
            GWL_EXSTYLE      = -20
            WS_EX_APPWINDOW  = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)

            # 2. Notify Windows the frame has changed so it re-reads the style.
            SWP_NOMOVE = 0x0002; SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004; SWP_FRAMECHANGED = 0x0020
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
            )

            # 3. Load the ICO and send WM_SETICON for both big and small sizes.
            hicon = ctypes.windll.user32.LoadImageW(
                None, icon_path, 1,   # IMAGE_ICON
                0, 0, 0x0010 | 0x0040  # LR_LOADFROMFILE | LR_DEFAULTSIZE
            )
            if hicon:
                ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, hicon)  # ICON_BIG
                ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, hicon)  # ICON_SMALL
        except Exception:
            pass

    def _apply_theme(self):
        self.setStyleSheet("QMainWindow { background: transparent; }")
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("transparent"))
        self.setPalette(palette)
        QApplication.setFont(QFont("Segoe UI", 10))

        # Soft orange glow shadow on the bordered container
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(ACCENT))
        self.container.setGraphicsEffect(shadow)

    # ── Messaging logic ───────────────────────────────────────────────────────
    def _add_bubble(self, role: str, text: str = "") -> MessageBubble:
        """Insert a message bubble into the feed and return it."""
        # Remove welcome label on first message
        if self._welcome_label is not None:
            self._welcome_label.hide()
            self._welcome_label = None

        bubble = MessageBubble(role)
        if text:
            bubble.set_text(text)

        # Insert before the trailing stretch
        count = self.feed_layout.count()
        self.feed_layout.insertWidget(count - 1, bubble)
        self._scroll_to_bottom()
        return bubble

    def _scroll_to_bottom(self):
        QApplication.processEvents()
        sb = self.scroll_area.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _send_message(self):
        text = self.input_field.text().strip()
        # Allow sending with only an attachment and no typed text
        if not text and self._pending_attachment is None:
            return
        if not self.send_btn.isEnabled():
            return

        self.input_field.clear()
        # Transform Send → Stop while streaming
        self.send_btn.setText("■ Stop")
        self.send_btn.setStyleSheet("""
            QPushButton {
                background-color: #c0392b; color: #ffffff;
                border: none; border-radius: 8px;
                font-size: 14px; font-weight: 600;
            }
            QPushButton:hover { background-color: #e53935; }
        """)
        self.send_btn.setEnabled(True)
        try:
            self.send_btn.clicked.disconnect()
        except Exception:
            pass
        self.send_btn.clicked.connect(self._stop_streaming)
        self.status_label.setText("● Thinking…")
        self.status_label.setStyleSheet(f"color: {ACCENT}; font-size: 11px; background: transparent;")

        # Build display text and model message, folding in any pending attachment
        att = self._pending_attachment
        if att:
            self._clear_attachment()
            name, content, truncated = att["name"], att["content"], att["truncated"]
            chip = f"📎 {name}" + (f"  · first 12,000 chars" if truncated else "")
            display = f"{chip}\n{text}" if text else chip
            model_content = f"[Attached file: {name}]\n\n{content}"
            if truncated:
                model_content += f"\n\n[Note: file truncated to 12,000 characters]"
            if text:
                model_content += f"\n\n{text}"
        else:
            display = text
            model_content = text

        # Show user bubble
        self._add_bubble("user", display)

        # Add to history
        self.history.append({"role": "user", "content": model_content})

        # ── Persist to DB ─────────────────────────────────────────────────────
        if self._current_session is None:
            self._current_session = ChatSession(model_name=self._active_model)
        if len(self.history) == 2:
            # First real message: auto-name the session and save it
            preview = display[:80].replace("\n", " ")
            name    = display[:40].replace("\n", " ") or "New Chat"
            self._current_session.name    = name
            self._current_session.preview = preview
            self._current_session.model_name = self._active_model
            self._current_session.updated_at = datetime.utcnow().isoformat()
            self._db.upsert_session(self._current_session)
            # Add to sidebar immediately
            if self._current_session.id not in self._session_item_map:
                self._add_session_to_list(self._current_session, prepend=True)
        self._db.save_message(self._current_session.id, "user", model_content)


        # Create empty assistant bubble — tokens stream into it
        self._current_bubble = self._add_bubble("assistant")
        self._current_bubble.show_thinking()
        QApplication.processEvents()  # settle layout so text_edit has a valid width

        # Start streaming thread
        self._thread = QThread()
        model_id = getattr(self, '_active_model_id', self._active_model)
        self._worker = StreamWorker(self.client, list(self.history), model_id)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.token_received.connect(self._on_token)
        self._worker.error_occurred.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)

        self._thread.start()

    def _on_token(self, token: str):
        if self._current_bubble:
            self._current_bubble.stop_thinking()  # no-op after first call
            self._current_bubble.append_text(token)
            self._scroll_to_bottom()

    def _stop_streaming(self):
        """Signal the worker to stop; _on_finished will restore the UI once the
        thread has actually wound down, preventing QThread destruction crashes."""
        if self._worker is not None:
            self._worker.stop()
        # Stop the dog immediately
        if self._current_bubble:
            self._current_bubble.stop_thinking()
        # Disable button until _on_finished fires — prevents sending while thread
        # is still alive (which would replace self._thread and crash Qt)
        self.send_btn.setText("Stopping…")
        self.send_btn.setEnabled(False)
        self.status_label.setText("● Stopping…")
        self.status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px; background: transparent;"
        )

    def _on_error(self, error: str):
        if self._current_bubble:
            self._current_bubble.stop_thinking()
            endpoint = self._fm.endpoint if hasattr(self, '_fm') else 'unknown'
            self._current_bubble.set_text(
                f"⚠ Could not reach Foundry Local.\n\n"
                f"Endpoint: {endpoint}\n\nError: {error}"
            )

    def _on_finished(self):
        # Save assistant reply to history
        if self._current_bubble:
            self._current_bubble.stop_thinking()  # safety net if no tokens arrived
            reply = self._current_bubble.text_edit.toPlainText()
            if reply:
                self.history.append({"role": "assistant", "content": reply})
                # Persist to DB and update sidebar widget
                if self._current_session:
                    self._db.save_message(self._current_session.id, "assistant", reply)
                    self._current_session.updated_at = datetime.utcnow().isoformat()
                    self._update_session_in_list(self._current_session)

        # Restore Stop → Send button
        self.send_btn.setText("Send")
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT}; color: #ffffff;
                border: none; border-radius: 8px;
                font-size: 14px; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}
            QPushButton:disabled {{ background-color: {BORDER}; color: {TEXT_MUTED}; }}
        """)
        self.send_btn.setEnabled(True)
        try:
            self.send_btn.clicked.disconnect()
        except Exception:
            pass
        self.send_btn.clicked.connect(self._send_message)
        self.input_field.setFocus()
        self.status_label.setText("● Ready")
        self.status_label.setStyleSheet(f"color: #4caf50; font-size: 11px; background: transparent;")
        self._current_bubble = None

    def closeEvent(self, event):
        """Save config, shut down DB, stop Foundry on close."""
        self.status_label.setText("● Shutting down…")
        QApplication.processEvents()

        # Save window + sidebar state
        sidebar_w = self._sidebar_width if not self._sidebar_collapsed else self._sidebar_width
        _save_config({
            "window":  {"width": self.width(), "height": self.height()},
            "sidebar": {"width": sidebar_w, "collapsed": self._sidebar_collapsed},
            "chat":    {
                "current_session_id": (
                    self._current_session.id if self._current_session else ""
                )
            },
        })

        # Close DB connection
        if hasattr(self, "_db"):
            self._db.close()
        if hasattr(self, "_db_thread"):
            self._db_thread.quit()
            self._db_thread.wait(3000)

        # Unload Foundry model
        if hasattr(self, '_fm'):
            t = threading.Thread(target=self._fm.stop_foundry, daemon=True)
            t.start()
            t.join(timeout=20)

        event.accept()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("Project Vera")
    app.setStyle("Fusion")

    # Set taskbar + window icon
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Logo", "icon.ico")
    if os.path.exists(icon_path):
        icon = QIcon(icon_path)
        app.setWindowIcon(icon)

    window = ChatWindow()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    window.show()

    sys.exit(app.exec())
