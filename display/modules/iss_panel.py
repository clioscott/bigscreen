"""The Space Map (the ISS map as its own rotation entry; control panel → Space Map): the full ISS map (display/modules/issmap.py) with
its caption, on one side (``iss_map``), the full width (``iss_map_full``), above the clock (``iss_map_tall``) or the
whole wall (``iss_map_wall``). It plays for the entry's own seconds.

Settings: ``modules.iss_map``, shaped like a webcam in space (``label``, ``overlay`` for the caption, ``map`` with
``fixed`` and ``extras``), so the webcam caption and the map draw it as they do for the ISS webcam. Until it's been
saved once, the first webcam in space lends its caption and map settings."""

from __future__ import annotations

import json
import os
import time

from display.gfx import graphics
from display.modules.webcam_caption import FONTS, draw_caption, for_shape, shape_of_panel

DEFAULT_HUB = "http://192.168.1.111:8800"


def settings_of(modules) -> dict:
    """modules.iss_map as a webcam-shaped dict (space: iss, its map always on), or the first ISS webcam's settings."""
    modules = modules or {}
    s = modules.get("iss_map") if isinstance(modules.get("iss_map"), dict) else None
    if not s:
        cams = ((modules.get("webcams") or {}).get("cams") or {})
        cam = next((c for c in cams.values() if isinstance(c, dict) and c.get("space")), None) or {}
        s = {"label": cam.get("label") or "International Space Station", "overlay": cam.get("overlay") or {},
             "overlays": cam.get("overlays") or {}, "map": cam.get("map") or {}}
    out = {**s, "space": "iss"}
    out["map"] = {**(s.get("map") if isinstance(s.get("map"), dict) else {}), "when_down": True}
    return out


class IssMapPanel:
    def __init__(self, cfg, data_dir=None):
        mods = getattr(cfg, "modules", None) or {}
        self.cam = settings_of(mods)
        self.hub = ((mods.get("webcams") or {}).get("hub_url")) or DEFAULT_HUB
        self.data_dir = data_dir
        if data_dir:
            from display.modules import issmap
            issmap.DATA_DIR = data_dir
        self.fonts, self.started, self.boxes = {}, {}, {}
        self._iss, self._mtime, self._checked = None, None, 0.0

    def font(self, size):
        f = self.fonts.get(size)
        if f is None:
            f = graphics.Font()
            try:
                f.LoadFont(FONTS.get(size, size))
            except Exception:
                f.LoadFont(FONTS["small"])
            self.fonts[size] = f
        return f

    def _space(self, now):
        """data/iss.json, re-read when it changes (checked every 5 s)."""
        if self.data_dir and now - self._checked > 5:
            self._checked = now
            path = os.path.join(self.data_dir, "iss.json")
            try:
                mt = os.stat(path).st_mtime
                if mt != self._mtime:
                    with open(path, encoding="utf-8") as f:
                        self._iss = json.load(f)
                    self._mtime = mt
            except (OSError, ValueError):
                pass
        return self._iss

    def draw(self, c, zone, entry, transition):
        from display.modules import issmap, mappanels, space
        now = time.time()
        key = (entry.get("panel"), tuple(zone))
        if transition or key not in self.started:
            self.started[key] = now
        data = self._space(now)
        shape = shape_of_panel(entry.get("panel"), zone)
        issmap.draw_full(c, zone, data, now - space.LAG, fixed=bool(self.cam["map"].get("fixed")),
                         caption_box=self.boxes.get(key), extras=issmap.extras_of(self.cam, space_map=True),
                         live=issmap.live_from(self.hub, mappanels.history_keys(self.cam["map"])),
                         mapset=self.cam["map"], view=issmap.view_of(self.cam, shape))
        cam = for_shape(self.cam, shape)                                       # the caption settings of its size
        self.boxes[key] = draw_caption(c, zone, cam, now - self.started[key], self.font, extra={"space": data})
        return None


def render_preview(settings, shape, size, data, live, elapsed=30.0):
    """The Space Map as the wall draws it at a size (shape: left | right | full | tall | wall; size: (w, h)) for the
    control panel: the map with its caption, the moving data where it is now."""
    from display.gfx import Canvas
    from display.modules import issmap, space
    w, h = size
    cam = for_shape(settings, shape)
    fonts = {}

    def font_for(sz):
        f = fonts.get(sz)
        if f is None:
            f = fonts[sz] = graphics.Font()
            try:
                f.LoadFont(FONTS.get(sz, sz))
            except Exception:
                f.LoadFont(FONTS["small"])
        return f

    box = draw_caption(Canvas(w, h), (0, 0, w, h), cam, elapsed, font_for, extra={"space": data})   # (where it goes)
    c = Canvas(w, h)
    ex = issmap.extras_of(settings, space_map=True)
    now = time.time()
    img, paths = issmap.render(w, h, data, now - space.LAG, True, bool(settings["map"].get("fixed")),
                               issmap.reserve_for(box, h), ex, live, settings["map"],
                               issmap.view_of(settings, shape), tuple(box) if box else None)
    c.Blit(img, 0, 0)
    issmap.flow(c.buf, paths, now, ex, w >= issmap.BIG_W)
    draw_caption(c, (0, 0, w, h), cam, elapsed, font_for, extra={"space": data})
    return c.buf.copy()
