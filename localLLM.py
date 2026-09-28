#!/usr/bin/env python3
"""
Local Ollama chat (single-file).
- Background diagnostics at startup (check Ollama, start serve if needed, pull models).
- No HTTP API; uses ollama Python client directly.
- Cross-platform attempts to start 'ollama serve' if not running.
- Installs Python dependencies on the fly.
"""

import subprocess
import sys
import threading
import time
import shutil
import queue
import os

# -------------------------
# Auto-install Python deps
# -------------------------
def ensure(pkg_name, import_name=None):
    import importlib
    name = import_name or pkg_name
    try:
        return importlib.import_module(name)
    except Exception:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg_name])
        return importlib.import_module(name)

# We need the ollama Python client and tkinter (tkinter is stdlib).
ollama = ensure("ollama")
# requests is optional here; not used for local client but keep for future.
ensure("requests")

# -------------------------
# GUI imports (tkinter)
# -------------------------
import tkinter as tk
from tkinter import ttk, scrolledtext

# -------------------------
# Configuration
# -------------------------
SMALL_MODELS = [
    "tinyllama:1.1b",
    "phi3:mini",
    "phi:2",
    "llama3.2:1b",
    "llama3.2:3b",
    "gemma:2b",
    "qwen2:0.5b",
    "qwen2.5:0.5b",
    "qwen2.5:1.5b",
]

OLLAMA_CLI = shutil.which("ollama")  # path to CLI if available
DIAG_POLL_INTERVAL = 1.0  # seconds between reachability checks
DIAG_TIMEOUT = 60  # seconds to wait for ollama serve to become reachable

# Thread-safe queue for status messages from background threads to GUI
_status_q = queue.Queue()

# -------------------------
# Utility functions
# -------------------------
def append_status(msg):
    """Put a status message into the queue for the GUI to display."""
    _status_q.put(msg)

def is_ollama_reachable():
    """Return True if ollama Python client can list models (i.e., server reachable)."""
    try:
        # ollama.list() will raise if server not reachable
        info = ollama.list()
        # Some versions return dict with "models"; treat any non-exception as reachable
        return True
    except Exception:
        return False

def start_ollama_serve_background():
    """
    Try to start 'ollama serve' as a background process.
    Cross-platform best-effort:
      - On POSIX: start_new_session=True
      - On Windows: creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
    Returns subprocess.Popen or None on failure.
    """
    if not OLLAMA_CLI:
        append_status("❌ 'ollama' CLI not found in PATH. Install Ollama from https://ollama.com/download")
        return None

    cmd = [OLLAMA_CLI, "serve"]
    append_status(f"➡ Attempting to start 'ollama serve' in background: {cmd}")

    try:
        if os.name == "nt":
            # Windows
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NEW_PROCESS_GROUP,
                shell=False,
            )
        else:
            # POSIX (Linux, macOS)
            p = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                shell=False,
            )
        append_status("✅ Started 'ollama serve' process (background).")
        return p
    except Exception as e:
        append_status(f"❌ Failed to start 'ollama serve': {e}")
        return None

def pull_model_if_missing(model):
    """Pull a model using the ollama CLI if it's not present in ollama.list()."""
    try:
        info = ollama.list()
        available = []
        # handle different return shapes
        if isinstance(info, dict) and "models" in info:
            for m in info["models"]:
                # m may be dict with 'name' or a string
                if isinstance(m, dict) and "name" in m:
                    available.append(m["name"])
                elif isinstance(m, str):
                    available.append(m)
        else:
            # fallback: try to parse list-like
            try:
                for m in info:
                    if isinstance(m, dict) and "name" in m:
                        available.append(m["name"])
            except Exception:
                pass

        if model in available:
            append_status(f"✅ Model already available: {model}")
            return True

        if not OLLAMA_CLI:
            append_status(f"❌ Cannot pull {model}: 'ollama' CLI not found.")
            return False

        append_status(f"⬇ Pulling model: {model} (this may take a while)")
        subprocess.run([OLLAMA_CLI, "pull", model], check=True)
        append_status(f"✅ Pulled model: {model}")
        return True
    except subprocess.CalledProcessError as e:
        append_status(f"❌ 'ollama pull {model}' failed: {e}")
        return False
    except Exception as e:
        append_status(f"❌ Error checking/pulling model {model}: {e}")
        return False

# -------------------------
# Background diagnostics
# -------------------------
def background_diagnostics(stop_event):
    """
    Run the full checklist in background:
      1. Check if ollama reachable.
      2. If not reachable and CLI exists, try to start 'ollama serve'.
      3. Poll until reachable or timeout.
      4. Once reachable, check models and pull missing ones.
    All status updates are sent to the GUI via append_status().
    """
    append_status("Diagnostics: starting background checks...")

    # 0. Check that ollama Python client is importable (done earlier)
    append_status("Checking Ollama Python client availability...")
    try:
        _ = ollama  # already imported
        append_status("✅ Ollama Python client available.")
    except Exception as e:
        append_status(f"❌ Ollama Python client import failed: {e}")
        return

    # 1. Is server reachable?
    if is_ollama_reachable():
        append_status("✅ Ollama server reachable.")
    else:
        append_status("⚠ Ollama server not reachable.")
        # 2. If CLI exists, try to start serve
        if OLLAMA_CLI:
            p = start_ollama_serve_background()
            # 3. Poll until reachable or timeout
            start_time = time.time()
            while not stop_event.is_set():
                if is_ollama_reachable():
                    append_status("✅ Ollama server became reachable.")
                    break
                if time.time() - start_time > DIAG_TIMEOUT:
                    append_status("❌ Timeout waiting for Ollama to become reachable.")
                    append_status("➡ Try running 'ollama serve' manually in a terminal.")
                    break
                time.sleep(DIAG_POLL_INTERVAL)
        else:
            append_status("❌ 'ollama' CLI not found; cannot auto-start server.")
            append_status("➡ Install Ollama and run 'ollama serve' manually: https://ollama.com/download")
            return

    # 4. If reachable, ensure models
    if is_ollama_reachable():
        append_status("Checking for required models...")
        for model in SMALL_MODELS:
            if stop_event.is_set():
                append_status("Diagnostics: stopped.")
                return
            pull_model_if_missing(model)
        append_status("Diagnostics: model checks complete.")
    else:
        append_status("Skipping model checks because Ollama is not reachable.")

    append_status("Diagnostics: finished.")

# -------------------------
# GUI application
# -------------------------
class ChatApp(tk.Tk):
    def __init__(self, diag_stop_event):
        super().__init__()
        self.title("Local Ollama Chat — Background Diagnostics")
        self.geometry("800x600")
        self.diag_stop_event = diag_stop_event

        # Top frame: model selection and status
        top = tk.Frame(self)
        top.pack(fill="x", padx=8, pady=6)

        tk.Label(top, text="Model:").pack(side="left")
        self.model_var = tk.StringVar(value=SMALL_MODELS[0])
        self.model_combo = ttk.Combobox(
            top, textvariable=self.model_var, values=SMALL_MODELS, state="readonly", width=36
        )
        self.model_combo.pack(side="left", padx=6)

        self.status_var = tk.StringVar(value="Status: starting diagnostics...")
        self.status_label = tk.Label(top, textvariable=self.status_var, anchor="w")
        self.status_label.pack(side="left", padx=12, fill="x", expand=True)

        # Chat history
        self.chat_box = scrolledtext.ScrolledText(self, wrap="word", state="disabled", height=24)
        self.chat_box.pack(fill="both", expand=True, padx=8, pady=6)

        # Diagnostics pane (collapsible)
        diag_frame = tk.Frame(self)
        diag_frame.pack(fill="both", padx=8, pady=4)
        tk.Label(diag_frame, text="Diagnostics log:").pack(anchor="w")
        self.diag_box = scrolledtext.ScrolledText(diag_frame, wrap="word", state="disabled", height=8)
        self.diag_box.pack(fill="both", expand=True)

        # Input area
        input_frame = tk.Frame(self)
        input_frame.pack(fill="x", padx=8, pady=6)

        self.input_text = tk.Text(input_frame, height=4, wrap="word")
        self.input_text.pack(side="left", fill="both", expand=True)

        send_btn = tk.Button(input_frame, text="Send", command=self.on_send)
        send_btn.pack(side="left", padx=6)

        # Poll the status queue periodically
        self.after(200, self._poll_status_queue)

        # Bindings
        self.bind("<Control-Return>", lambda e: self.on_send())
        self.bind("<Command-Return>", lambda e: self.on_send())

    def _poll_status_queue(self):
        """Drain status queue and append to diagnostics pane and update status label."""
        updated = False
        last_msg = None
        while True:
            try:
                msg = _status_q.get_nowait()
            except queue.Empty:
                break
            updated = True
            last_msg = msg
            self._append_diag(msg)
        if updated and last_msg is not None:
            # show last message in status label (shortened)
            short = last_msg.splitlines()[0]
            self.status_var.set(f"Status: {short}")
        # schedule next poll
        self.after(200, self._poll_status_queue)

    def _append_diag(self, text):
        self.diag_box.configure(state="normal")
        self.diag_box.insert("end", f"{text}\n\n")
        self.diag_box.see("end")
        self.diag_box.configure(state="disabled")

    def _append_chat(self, who, text):
        self.chat_box.configure(state="normal")
        self.chat_box.insert("end", f"{who}: {text}\n\n")
        self.chat_box.see("end")
        self.chat_box.configure(state="disabled")

    def on_send(self):
        prompt = self.input_text.get("1.0", "end").strip()
        if not prompt:
            return
        model = self.model_var.get()
        self._append_chat("You", prompt)
        self.input_text.delete("1.0", "end")
        threading.Thread(target=self._call_ollama_thread, args=(model, prompt), daemon=True).start()

    def _call_ollama_thread(self, model, prompt):
        """Call ollama.chat in background and append response."""
        try:
            # Ensure server reachable before calling
            if not is_ollama_reachable():
                self._append_chat("Model", "[Ollama not reachable. Waiting for diagnostics to finish.]")
                return
            # Use ollama.chat (Python client)
            resp = ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
            # resp shape may vary; try to extract content
            content = ""
            try:
                content = resp["message"]["content"]
            except Exception:
                try:
                    content = str(resp)
                except Exception:
                    content = "[Received unexpected response format from Ollama]"
            self._append_chat("Model", content)
        except Exception as e:
            self._append_chat("Model", f"[Ollama error: {e}]")

# -------------------------
# Main entrypoint
# -------------------------
def main():
    # Create a stop event for background threads
    diag_stop_event = threading.Event()

    # Start diagnostics thread immediately (background)
    diag_thread = threading.Thread(target=background_diagnostics, args=(diag_stop_event,), daemon=True)
    diag_thread.start()

    # Start GUI
    app = ChatApp(diag_stop_event)
    try:
        app.mainloop()
    finally:
        # Signal background threads to stop if GUI closes
        diag_stop_event.set()

if __name__ == "__main__":
    main()
