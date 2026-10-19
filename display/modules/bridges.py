"""Bridges now: a grid of the latest DriveBC stills from Metro Vancouver's bridges (hub/jobs/bridges.py).

Panels: ``bridges`` (one side: 2×2, paging through the rest), ``bridges_full`` (4×2 under the ticker) and
``bridges_wall`` (4×2 on the whole wall). The hub has already cut each image to the tile size, so drawing is copying.
Each tile: the bridge's name over a dark band, and the exact time the picture was taken (13:29:17) in the corner,
coloured by its age against that camera's own
refresh period (yellow past 1.5×, or when DriveBC says it's delayed; red past 3×); a camera that is off gets a dark
tile saying so.
"""

from __future__ import annotations

import json
import os
import time

import numpy as np

from display.gfx import Color, graphics
from display.modules.webcam import _outlined, _shade, _tw

FONT_LABEL = "fonts/alert/5x8.bdf"
FONT_AGE = "fonts/alert/5x8.bdf"
PAGE_SEC = 8.0
GAP = 1


def _age_text(sec):
    if sec is None:
        return "?"
    m = int(sec // 60)
    return "now" if m < 1 else f"{m}m" if m < 100 else f"{m // 60}h"


class BridgesPanel:
    def __init__(self, cfg, data_dir):
        s = cfg.modules.get("bridges") or {}
        try:
            self.page_sec = max(1.0, float(s.get("page_sec", PAGE_SEC)))
        except (TypeError, ValueError):
            self.page_sec = PAGE_SEC
        self.dir = data_dir or ""
        self.meta = {}
        self.tiles = {}                      # tile file name -> (h, w, 3) array
        self._mtime = None
        self._waiting = None                 # (mtime, since): a new bridges.json whose tiles haven't all arrived yet
        self._checked = 0.0
        self.started = {}                    # (panel, zone x) -> when this play began (each side pages on its own)
        self.passes = {}
        self.fonts = {}
        self.white = Color(235, 235, 235)
        self.warn = Color(255, 200, 40)
        self.bad = Color(255, 70, 60)
        self.dim = Color(110, 110, 110)

    def font(self, path):
        f = self.fonts.get(path)
        if f is None:
            f = self.fonts[path] = graphics.Font()
            f.LoadFont(path)
        return f

    # data -------------------------------------------------------------------------------------------------
    def _load(self, now):
        if now - self._checked < 1.0:
            return
        self._checked = now
        path = os.path.join(self.dir, "bridges.json")
        try:
            mt = os.stat(path).st_mtime
        except OSError:
            return
        if mt == self._mtime:
            return
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            # bridges.json reaches the Pi in the main sync pass, the tiles it names in a second one: keep showing the
            # previous set until they're all here (or 30 s have gone by, then show what there is)
            names = {(kind, n) for c in meta.get("cams", []) if c.get("on") for kind, n in (c.get("tiles") or {}).items()}
            missing = [n for _, n in names if not os.path.exists(os.path.join(self.dir, "bridges", n))]
            if missing:
                if not self._waiting or self._waiting[0] != mt:
                    self._waiting = (mt, now)
                if now - self._waiting[1] < 30:
                    return
            tiles = {}
            for kind, n in names:
                tw, th = meta.get(f"tile_{kind}", (111, 66 if kind == "s" else 95))
                fp = os.path.join(self.dir, "bridges", n)
                a = np.fromfile(fp, dtype=np.uint8) if os.path.exists(fp) else np.zeros(0, np.uint8)
                if a.size == tw * th * 3:
                    tiles[n] = a.reshape(th, tw, 3)
            self.meta, self.tiles, self._mtime, self._waiting = meta, tiles, mt, None
        except (OSError, ValueError) as e:
            print(f"[bridges] load: {e}")

    # drawing ------------------------------------------------------------------------------------------------
    def draw(self, c, zone, entry, transition):
        x, y, w, h = zone
        now = time.time()
        self._load(now)
        key = (entry.get("panel", "bridges"), x)
        if transition or key not in self.started:
            self.started[key], self.passes[key] = now, 0
        cams = self.meta.get("cams") or []
        if not cams:
            graphics.DrawText(c, self.font(FONT_LABEL), x + 4, y + 12, self.dim, "Bridges: waiting for DriveBC...")
            return "done" if now - self.started[key] > 5 else None
        kind = "l" if h >= 150 else "s"
        tw, th = self.meta.get(f"tile_{kind}", (111, 66 if kind == "s" else 95))
        cols = max(1, (w + GAP) // (tw + GAP))
        rows = max(1, (h + GAP) // (th + GAP))
        per = cols * rows
        pages = max(1, -(-len(cams) // per))
        elapsed = now - self.started[key]
        page = int(elapsed // self.page_sec) % pages
        gx = x + (w - (cols * tw + (cols - 1) * GAP)) // 2
        gy = y + (h - (rows * th + (rows - 1) * GAP)) // 2
        for i, cam in enumerate(cams[page * per:(page + 1) * per]):
            cx, cy = gx + (i % cols) * (tw + GAP), gy + (i // cols) * (th + GAP)
            self._tile(c, cx, cy, tw, th, cam, kind, now)
        if pages > 1:                                       # page dots, in the top-right tile's name band
            right = gx + cols * tw + (cols - 1) * GAP
            for p in range(pages):
                c.FillRect(right - 3 - (pages - 1 - p) * 5, gy + 4, 3, 3, self.white if p == page else self.dim)
        done_passes = int(elapsed // (self.page_sec * pages))
        if done_passes > self.passes[key]:
            self.passes[key] = done_passes
            return "done"
        return None

    def _tile(self, c, x, y, w, h, cam, kind, now):
        lf, af = self.font(FONT_LABEL), self.font(FONT_AGE)
        arr = self.tiles.get((cam.get("tiles") or {}).get(kind))
        if arr is not None:
            c.Blit(arr, x, y)
        else:
            c.FillRect(x, y, w, h, Color(12, 12, 16))
            msg = cam.get("why") or "no image"
            graphics.DrawText(c, af, x + (w - _tw(af, msg)) // 2, y + h // 2 + 3, self.dim, msg)
        label = cam.get("label", "")
        _shade(c, x, y, w, lf.height + 2, 0.35)
        graphics.DrawText(c, lf, x + 2, y + 1 + lf.baseline, self.white, label)
        if arr is None:
            return
        age = now - cam["updated"] if cam.get("updated") else None
        period = max(120.0, float(cam.get("period") or 300))
        col = self.bad if age is None or age > 3 * period else self.warn if age > 1.5 * period or cam.get("delayed")             else self.white
        txt = time.strftime("%H:%M:%S", time.localtime(cam["updated"])) if cam.get("updated") else "?"   # exact capture time
        _outlined(c, af, x + w - _tw(af, txt) - 2, y + h - 2, col, txt)


def render_preview(data_dir, size, panel="bridges_full", elapsed=0.0):
    """The wall's pixels for one zone (control panel preview)."""
    from display.gfx import Canvas

    class _Cfg:
        modules = {}

    w, h = size
    c = Canvas(w, h)
    p = BridgesPanel(_Cfg(), data_dir)
    p.started[(panel, 0)], p.passes[(panel, 0)] = time.time() - elapsed, 0
    p.draw(c, (0, 0, w, h), {"panel": panel}, False)
    return c.buf.copy()
