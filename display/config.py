"""Display-side view of the hub config.

The hub syncs config.json into the data dir (/dev/shm/bigscreen). The control
server can layer a temporary *preview* on top (deep-merged), which the panel
uses for "try it on the display, then keep or revert".
"""

import copy
import json
import os
import threading


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
        self._preview = None
        self._preview_until = 0.0
        self._lock = threading.Lock()
        self.generation = 0          # bumps whenever the effective config changes

    def _read(self):
        for p in (self.path, self.fallback):
            if p and os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    return json.load(f), os.stat(p).st_mtime
        raise FileNotFoundError(f"no config at {self.path}")

    def poll(self, now):
        """Reload if the file changed or the preview expired. Returns True if config changed."""
        changed = False
        with self._lock:
            try:
                st = os.stat(self.path).st_mtime if os.path.exists(self.path) else None
                if self._base is None or (st is not None and st != self._mtime):
                    self._base, self._mtime = self._read()
                    changed = True
                    if st is not None:
                        self._remember()
            except (OSError, ValueError) as e:
                print(f"[config] reload failed, keeping previous: {e}")
            if self._preview is not None and self._preview_until and now > self._preview_until:
                self._preview = None
                changed = True
                print("[config] preview expired — reverted")
            if changed:
                self.generation += 1
        return changed

    def _remember(self):
        try:
            tmp = self.fallback + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._base, f)
            os.replace(tmp, self.fallback)
        except OSError:
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
