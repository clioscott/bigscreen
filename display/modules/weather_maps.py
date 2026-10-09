"""Map animation playback (packs rendered by the hub: data/anim/<track>.bin + .json).

All compositing (basemap, satellite, radar, totals, coastlines) happens on the
server; the Pi just blits one pre-rendered frame per draw and adds crisp
pixel-font text on top: location markers, time label, legend, attribution.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

import numpy as np

from display.gfx import Color, graphics


class MapStore:
    """Watches <data_dir>/anim and loads changed packs in a background thread, so a
    multi-MB read never stalls the render loop."""

    def __init__(self, data_dir):
        self.dir = os.path.join(data_dir, "anim")
        self.packs = {}          # track -> (meta, frames)
        self._mtimes = {}
        threading.Thread(target=self._loop, name="map-loader", daemon=True).start()

    def get(self, track):
        return self.packs.get(track)

    def _loop(self):
        while True:
            try:
                self._scan()
            except Exception as e:
                print(f"[maps] scan error: {e}")
            time.sleep(2.0)

    def _scan(self):
        if not os.path.isdir(self.dir):
            return
        names = {f[:-5] for f in os.listdir(self.dir) if f.endswith(".json")}
        for tid in list(self.packs):
            if tid not in names:
                self.packs.pop(tid, None)
                self._mtimes.pop(tid, None)
        for tid in names:
            jp, bp = os.path.join(self.dir, f"{tid}.json"), os.path.join(self.dir, f"{tid}.bin")
            try:
                mt = (os.stat(jp).st_mtime, os.stat(bp).st_mtime)
            except FileNotFoundError:
                continue
            if self._mtimes.get(tid) == mt:
                continue
            with open(jp, encoding="utf-8") as f:
                meta = json.load(f)
            frames = np.fromfile(bp, dtype=np.uint8)
            n, h, w = meta["n"], meta["h"], meta["w"]
            if frames.size != n * h * w * 3:
                continue                          # .bin and .json from different renders; retry next scan
            self.packs[tid] = (meta, frames.reshape(n, h, w, 3))
            self._mtimes[tid] = mt


class MapPanel:
    def __init__(self, cfg, store):
        self.store = store
        self.cfg = cfg
        self.font = graphics.Font()
        p = cfg.get_font_path("small")
        if p:
            self.font.LoadFont(p)
        self.white = Color(255, 255, 255)
        self.shadow = Color(0, 0, 0)
        self.dim = Color(170, 170, 170)
        self.marker = Color(255, 230, 90)
        self._started = {}

    def _text(self, c, x, y, s, color=None):
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)):
            graphics.DrawText(c, self.font, x + dx, y + dy, self.shadow, s)
        return graphics.DrawText(c, self.font, x, y, color or self.white, s)

    def _tw(self, s):
        return sum(max(0, self.font.CharacterWidth(ord(ch))) for ch in s)

    def draw(self, c, zone, entry, transition):
        x, y, w, h = zone
        tid = entry.get("track")
        now = time.time()
        pack = self.store.get(tid)
        if transition or tid not in self._started:
            self._started[tid] = now
        if pack is None:
            self._text(c, x + 4, y + 12, f"Map '{tid}' loading...", self.dim)
            return "done" if now - self._started[tid] > 5 else None
        meta, frames = pack
        n = len(frames)
        fps = max(1.0, float(meta.get("fps", 8)))
        loop = n / fps + float(meta.get("hold_last_sec", 1.5))
        elapsed = now - self._started[tid]
        idx = min(n - 1, int((elapsed % loop) * fps))
        fr = frames[idx]
        c.Blit(fr[: min(h, fr.shape[0]), : min(w, fr.shape[1])], x, y)

        # markers: 3x3 dot + name
        for m in meta.get("markers") or []:
            mx, my = x + m["x"], y + m["y"]
            c.FillRect(mx - 1, my - 1, 3, 3, self.shadow)
            c.SetPixel(mx, my, *self.marker.rgb)
            if m.get("name"):
                self._text(c, mx + 4, my + 3, m["name"], self.marker)

        # time label (local)
        times = meta.get("times") or []
        if meta.get("timestamp", True) and idx < len(times):
            t = datetime.strptime(times[idx], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone()
            if meta.get("kind") == "accumulation":
                t0 = datetime.strptime(times[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone()
                label = f"{meta.get('label', '')}  since {t0:%a %H:%M} -> {t:%H:%M}"
            else:
                age = (datetime.now(timezone.utc) - t.astimezone(timezone.utc)).total_seconds() / 60
                label = f"{meta.get('label', '')}  {t:%H:%M}" + ("" if age < 15 else f" (-{age / 60:.1f}h)")
            self._text(c, x + 2, y + h - 2, label)
        self._text(c, x + w - self._tw("ECCC") - 2, y + 7, "ECCC", self.dim)

        if meta.get("legend"):
            self._legend(c, x, y, w, meta["legend"])
        return "done" if elapsed >= loop * max(1, int(meta.get("loops", 2))) else None

    def _legend(self, c, x, y, w, legend):
        """Compact swatch strip per scale (Rain / Snow) at the top-left."""
        cy = y + 2
        for scale in legend:
            cx = x + 2
            cx += self._text(c, cx, cy + 5, scale["label"][0], self.dim) + 2
            for t, col in scale["bands"]:
                rgb = tuple(int(col.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
                c.FillRect(cx, cy, 4, 5, Color(*rgb))
                cx += 4
            cx += 2
            self._text(c, cx, cy + 5, f"{scale['bands'][-1][0]:g}{scale['unit']}", self.dim)
            cy += 7
