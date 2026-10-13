"""``pythonw -m bigscreen_tray [--settings]``: the BigScreen tray icon (one per user session)."""

import ctypes
import logging
import sys

from . import settings


def main():
    settings.setup_logging()
    # one copy only: a second launch (Run key + a manual start) just exits
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _mutex = kernel32.CreateMutexW(None, False, "Local\\BigScreenTray")
    if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
        logging.info("already running")
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)    # crisp menus and settings window on scaled displays
    except Exception:
        pass
    from .app import App
    logging.info("starting (%s)", sys.executable)
    App().run(open_settings="--settings" in sys.argv)
    logging.info("stopped")


if __name__ == "__main__":
    main()
