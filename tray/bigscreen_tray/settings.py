"""Settings (``%LOCALAPPDATA%\\BigScreenTray\\settings.json``), the wall cache, and start-with-Windows."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import threading
import winreg
from pathlib import Path

HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "BigScreenTray"
SETTINGS_FILE = HOME / "settings.json"
CACHE_FILE = HOME / "wall-cache.json"         # last known rotation/categories, so hotkey settings work offline
LOG_FILE = HOME / "tray.log"

DEFAULT_HUB = "http://192.168.1.111:8800"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "BigScreenTray"

_lock = threading.Lock()


def setup_logging():
    HOME.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])
    threading.excepthook = lambda a: logging.getLogger("thread").error(
        "uncaught in %s", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data):
    with _lock:
        HOME.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)


class Settings:
    def __init__(self):
        d = _read(SETTINGS_FILE, {})
        self.hub_url: str = (d.get("hub_url") or DEFAULT_HUB).rstrip("/")
        # [{"combo": "ctrl+alt+right", "action": {...}}]; one action per combo
        self.bindings: list[dict] = [b for b in d.get("bindings", [])
                                     if isinstance(b, dict) and b.get("combo") and isinstance(b.get("action"), dict)]

    def save(self):
        _write(SETTINGS_FILE, {"hub_url": self.hub_url, "bindings": self.bindings})

    def action_for(self, combo: str) -> dict | None:
        return next((b["action"] for b in self.bindings if b["combo"] == combo), None)


def load_cache() -> dict:
    return _read(CACHE_FILE, {})


def save_cache(data: dict):
    _write(CACHE_FILE, data)


# -- start with Windows -----------------------------------------------------------------------
def launch_command() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        exe = exe.with_name("pythonw.exe")    # no console window
    return f'"{exe}" -I -m bigscreen_tray'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
            return True
    except OSError:
        return False


def set_autostart(on: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_VALUE)
            except FileNotFoundError:
                pass
