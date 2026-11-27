"""Display-side view of the hub config.

The hub syncs config.json into the data dir (/dev/shm/bigscreen). The control
server can layer a temporary *preview* on top (deep-merged), which the panel
uses for "try it on the display, then keep or revert".
"""

import copy
import json
import os
import threading
import time

try:                                 # on the Pi: ~5x faster than json on the ~700 KB config, which matters because
    import orjson                    # a parse holds the GIL (the render loop waits for it) wherever it runs

    _dumps = orjson.dumps

    def _loads(raw):
        try:
            return orjson.loads(raw)
        except ValueError:           # (json's NaN / Infinity, which orjson refuses)
            return json.loads(raw)
except ImportError:
    def _loads(raw):
        return json.loads(raw)

    def _dumps(data):
        return json.dumps(data).encode("utf-8")

WATCH_SEC = 0.5                      # how often the watcher thread looks at config.json


def deep_merge(base, overlay):
    out = copy.deepcopy(base)
    for k, v in (overlay or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """Same accessors the v1 modules use (system/fonts/colors/layout/modules, get_font_path…)."""

    def __init__(self, data):
        self._data = data

    @property
    def raw(self):
        return self._data

    @property
    def system(self):
        return self._data.get("system", {})

    @property
    def fonts(self):
        return self._data.get("fonts", {})

    @property
    def colors(self):
        return self._data.get("colors", {})

    @property
    def layout(self):
        return self._data.get("layout", {})

    @property
    def modules(self):
        return self._data.get("modules", {})

    @property
    def revision(self):
        return self._data.get("revision")

    def get_font_path(self, font_name):
        from display.gfx import resolve_asset
        font_def = self.fonts.get(font_name)
        return resolve_asset(font_def["file"]) if font_def else None

    def get_font_offset(self, font_name):
        font_def = self.fonts.get(font_name)
        return font_def.get("y_offset", 0) if font_def else 0

    def get_color(self, color_name):
        return tuple(self.colors.get(color_name, [255, 255, 255]))


class ConfigSource:
    """Watches <data_dir>/config.json and holds an optional in-memory preview overlay."""

    def __init__(self, path, fallback=None):
        self.path = path
        # Last good config, kept on disk so the display can start before the hub's first sync
        # after a reboot (/dev/shm is empty then).
        self.fallback = fallback or os.path.expanduser("~/.bigscreen-display-config.json")
        self._mtime = None
        self._base = None
        self._loaded = None          # (data, mtime) read by the watcher thread, waiting for poll() to take it
        self._seen = None            # the mtime the watcher last read
        self._watcher = None
        self._preview = None
        self._preview_until = 0.0
        self._lock = threading.Lock()
        self.generation = 0          # bumps whenever the effective config changes

    def _read(self):
        for p in (self.path, self.fallback):
            if p and os.path.exists(p):
                mtime = os.stat(p).st_mtime
                with open(p, "rb") as f:
                    return _loads(f.read()), mtime
        raise FileNotFoundError(f"no config at {self.path}")

    def poll(self, now):
        """Take in a changed config or an expired preview. Returns True if config changed.

        Called by the render loop every frame, so it only swaps in what the watcher thread already read: the
        config is ~700 KB of JSON, and reading it here (and writing the fallback copy to the SD card) held a
        frame up on every save. The first load is done here, since the display can't start without it."""
        changed = False
        with self._lock:
            if self._base is None:
                try:
                    self._base, self._mtime = self._read()
                    # (started on the fallback copy: the watcher takes config.json as soon as it's synced)
                    self._seen = self._mtime if os.path.exists(self.path) else None
                    changed = True
                except (OSError, ValueError) as e:
                    print(f"[config] load failed: {e}")
            elif self._loaded is not None:
                (self._base, self._mtime), self._loaded = self._loaded, None
                changed = True
            if self._preview is not None and self._preview_until and now > self._preview_until:
                self._preview = None
                changed = True
                print("[config] preview expired — reverted")
            if changed:
                self.generation += 1
            start = self._watcher is None and self._base is not None
            if start:
                self._watcher = threading.Thread(target=self._watch, name="config-watch", daemon=True)
        if start:
            self._remember(self._base)
            self._watcher.start()
        return changed

    def _watch(self):
        """Reads config.json when it changes and hands it to poll(); keeps the fallback copy current."""
        while True:
            time.sleep(WATCH_SEC)
            try:
                st = os.stat(self.path).st_mtime
            except OSError:
                continue                      # not synced yet (after a reboot): running on the fallback copy
            if st == self._seen:
                continue
            self._seen = st                   # (a file that fails to parse isn't tried again until it changes)
            try:
                with open(self.path, "rb") as f:
                    data = _loads(f.read())
            except (OSError, ValueError) as e:
                print(f"[config] reload failed, keeping previous: {e}")
                continue
            with self._lock:
                self._loaded = (data, st)
            self._remember(data)

    def _remember(self, data):
        if not os.path.exists(self.path):
            return                            # loaded from the fallback itself
        try:
            tmp = self.fallback + ".tmp"
            with open(tmp, "wb") as f:
                f.write(_dumps(data))
            os.replace(tmp, self.fallback)
        except (OSError, TypeError, ValueError):
            pass

    def effective(self):
        with self._lock:
            data = deep_merge(self._base, self._preview) if self._preview else self._base
        return Config(data)

    def set_preview(self, overlay, ttl_sec, now):
        with self._lock:
            self._preview = overlay
            self._preview_until = now + ttl_sec if ttl_sec else 0.0
            self.generation += 1

    def clear_preview(self):
        with self._lock:
            had = self._preview is not None
            self._preview = None
            self._preview_until = 0.0
            if had:
                self.generation += 1
        return had

    def preview_info(self, now):
        with self._lock:
            if self._preview is None:
                return None
            return {"keys": sorted(self._preview.keys()),
                    "expires_in": round(self._preview_until - now, 1) if self._preview_until else None}
