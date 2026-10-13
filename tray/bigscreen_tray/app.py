"""Ties it together: settings, the hub poller, the action worker, hotkeys, the tray icon."""

from __future__ import annotations

import logging
import queue
import threading
import time
import webbrowser

from . import actions, settings
from .hub import SIDES, Hub, HubError, Wall
from .trayicon import TrayIcon, build_menu, icon_image
from .winput import HotkeyThread

log = logging.getLogger(__name__)

POLL_SEC, POLL_OFFLINE_SEC = 4.0, 10.0
LOCAL = {"open_panel", "settings"}


class App:
    def __init__(self):
        self.settings = settings.Settings()
        self.hub = Hub(self.settings.hub_url)
        self.wall = Wall()
        self.online = False
        self.last_error: str | None = None
        self.binding_errors: dict[str, str] = {}
        self._config_rev = None
        self._track_labels: dict = {}
        self._cache = settings.load_cache()
        self._kick = threading.Event()
        self._jobs: queue.Queue = queue.Queue()
        self._notified: dict[str, float] = {}
        self._window = None
        self._stopping = False
        self.hotkeys = HotkeyThread(on_fire=self._on_hotkey)
        self.icon = TrayIcon("BigScreen", icon_image(False), "BigScreen — connecting…", build_menu(self))

    # -- lifecycle ------------------------------------------------------------------------------
    def run(self, open_settings=False):
        self.hotkeys.start()
        self.apply_bindings()
        threading.Thread(target=self._poll_loop, name="poller", daemon=True).start()
        threading.Thread(target=self._work_loop, name="worker", daemon=True).start()

        def ready(icon):
            icon.visible = True
            if self.binding_errors:
                n = len(self.binding_errors)
                self.notify(f"{n} hotkey{'s' if n > 1 else ''} couldn't be set up — see Hotkeys & settings.")
            if open_settings:
                self.open_settings()

        self.icon.run(setup=ready)

    def quit(self):
        self._stopping = True
        self.hotkeys.stop()
        self.icon.stop()

    # -- hotkeys --------------------------------------------------------------------------------
    def apply_bindings(self) -> dict[str, str]:
        self.settings.save()
        self.binding_errors = self.hotkeys.set_bindings([b["combo"] for b in self.settings.bindings])
        return self.binding_errors

    def _on_hotkey(self, combo):              # on the hotkey thread: hand off and return at once
        a = self.settings.action_for(combo)
        if a:
            log.info("hotkey %s -> %s", combo, a)
            self.do(a)

    # -- actions --------------------------------------------------------------------------------
    def do(self, a: dict):
        if a.get("do") == "settings":
            self.open_settings()
        else:
            self._jobs.put(a)

    def _work_loop(self):
        while True:
            a = self._jobs.get()
            try:
                if a.get("do") == "open_panel":
                    webbrowser.open(self.settings.hub_url + "/")
                    continue
                note = actions.run(self.hub, a)
                if note:
                    self.notify(note)
            except HubError as e:
                self.notify(f"{actions.label(a, self.wall)}: {e}")
            except Exception:
                log.exception("action %s", a)
            self._kick.set()                 # show the result in the tooltip/menu soon

    def notify(self, msg: str):
        now = time.time()
        if now - self._notified.get(msg, 0) < 30:
            return
        self._notified[msg] = now
        log.info("notify: %s", msg)
        try:
            self.icon.notify(msg, "BigScreen")
        except Exception:
            log.exception("notify")

    # -- status ---------------------------------------------------------------------------------
    def _poll_loop(self):
        while not self._stopping:
            self.refresh()
            self._kick.wait(POLL_SEC if self.online else POLL_OFFLINE_SEC)
            if self._kick.is_set():
                time.sleep(0.4)              # let the display act on what was just asked
            self._kick.clear()

    def refresh(self):
        try:
            st = self.hub.display_status()
            rev = st.get("config_revision")
            if rev != self._config_rev:
                self._track_labels = self.hub.track_labels()
                self._config_rev = rev
            self.wall = Wall(st, self._track_labels)
            was, self.online, self.last_error = self.online, True, None
            if not was:
                log.info("connected to %s", self.hub.base_url)
            cache = self.wall.to_cache()
            if cache != self._cache:
                self._cache = cache
                settings.save_cache(cache)
        except HubError as e:
            if self.online or self.last_error != str(e):
                log.warning("offline: %s", e)
            self.online, self.last_error = False, str(e)
        self._update_icon()

    def _update_icon(self):
        if self.online:
            title = "BigScreen\n" + "\n".join(f"{s.capitalize()}: {self.wall.describe(s)}" for s in SIDES)
        else:
            title = f"BigScreen — can't reach the wall\n{self.last_error or ''}"
        title = title[:127]
        if getattr(self, "_shown", None) != (self.online, title):
            self._shown = (self.online, title)
            self.icon.icon = icon_image(self.online)
            self.icon.title = title

    def wall_for_settings(self) -> Wall:
        return self.wall if self.wall.known else Wall.from_cache(self._cache)

    def connection_text(self) -> str:
        if self.online:
            return "Connected. " + " · ".join(f"{s.capitalize()}: {self.wall.describe(s)}" for s in SIDES)
        return f"Not connected: {self.last_error or 'connecting…'}"

    def set_hub_url(self, url: str) -> str:
        """Try a new hub address; keep it if it answers. Returns a line for the settings window."""
        try:
            Hub(url).display_status()
        except HubError as e:
            return f"Didn't connect: {e}"
        self.settings.hub_url = url.rstrip("/")
        self.settings.save()
        self.hub = Hub(self.settings.hub_url)
        self._config_rev = None
        self.refresh()
        return self.connection_text()

    def icon_image(self, online=True):
        return icon_image(online, 64)

    # -- settings window --------------------------------------------------------------------------
    def open_settings(self):
        from .settings_window import SettingsWindow
        if self._window is None or not self._window.thread.is_alive():
            self._window = SettingsWindow(self)
        self._window.show()

