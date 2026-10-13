"""Map animation playback (packs rendered by the hub: data/anim/<track>.bin + .json).

All compositing (basemap, satellite, radar, totals, coastlines) happens on the
server; the Pi just blits one pre-rendered frame per draw and adds crisp
pixel-font text on top: time label, legend, attribution.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone

import numpy as np

from display.gfx import Color, graphics
from display.modules.date_display import _WEEKDAY_COLORS, _WEEKDAY_JIS


def _lum(rgb):
    """WCAG relative luminance of an sRGB colour."""
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


# per weekday (Mon=0): the status bar's kanji colour, the text that reads best on it, and its dim shade
_DAY_BG = tuple(Color(*rgb) for rgb in _WEEKDAY_COLORS)
_DAY_FG = tuple(Color(0, 0, 0) if (_lum(rgb) + 0.05) / 0.05 >= 1.05 / (_lum(rgb) + 0.05) else Color(255, 255, 255)
                for rgb in _WEEKDAY_COLORS)
_DAY_DIM = tuple(Color(*(max(18, int(v * 0.28)) for v in rgb)) for rgb in _WEEKDAY_COLORS)


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
        self.kanji = graphics.Font()                   # the status bar's weekday kanji font (12 px glyphs)
        try:
            self.kanji.LoadFont(cfg.get_font_path("mplus12bold") or "fonts/mplus_j12b.bdf")
        except Exception:
            self.kanji = None
        self.white = Color(255, 255, 255)
        self.shadow = Color(0, 0, 0)
        self.dim = Color(170, 170, 170)
        self.marker = Color(255, 230, 90)
        self.forecast_bg = Color(200, 80, 0)          # FORECAST tag
        self._started = {}
        self._loops_seen = {}
        self._fonts = {}
        self._time_cache = {}

    def _font(self, name):
        """Label fonts by file name (assets/fonts/<name>), cached."""
        f = self._fonts.get(name)
        if f is None:
            f = graphics.Font()
            try:
                f.LoadFont(f"fonts/{name}")
            except Exception:
                f = self.font
            self._fonts[name] = f
        return f

    @staticmethod
    def _color(hex_or_none, default):
        if not hex_or_none:
            return default
        h = hex_or_none.lstrip("#")
        return Color(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _label_text(self, c, font, x, y, s, color, outline):
        if outline:
            for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)):
                graphics.DrawText(c, font, x + dx, y + dy, self.shadow, s)
        graphics.DrawText(c, font, x, y, color, s)

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
            self._loops_seen[tid] = 0
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

        # bottom edge: a progress bar for the animation, and the frame's local time ("MON 12:10P") above it
        times, days = self._times(tid, meta)
        self._progress(c, x, y + h - 2, w, idx, n, times, days, now)
        if meta.get("timestamp", True) and idx < len(times):
            t = times[idx]
            wd = t.weekday()                                   # weekday on a chip in the status bar's kanji colour
            day = f"{t:%a}".upper()
            c.FillRect(x + 1, y + h - 11, self._tw(day) + 3, 8, _DAY_BG[wd])
            graphics.DrawText(c, self.font, x + 3, y + h - 4, _DAY_FG[wd], day)
            if self.kanji:                                     # and its kanji above, coloured on black, same width
                c.FillRect(x + 1, y + h - 25, self._tw(day) + 3, 14, self.shadow)
                graphics.DrawText(c, self.kanji, x + 2, y + h - 13, _DAY_BG[wd], chr(_WEEKDAY_JIS[wd]))  # ink centred
            lx = x + 4 + self._tw(day) + 2
            lx += self._text(c, lx, y + h - 4, f"{t.hour % 12 or 12}:{t:%M}{'A' if t.hour < 12 else 'P'}") + 4
            if t.timestamp() > now + 120:                      # model forecast / radar nowcast frame
                tag = "FORECAST"
                c.FillRect(lx - 1, y + h - 11, self._tw(tag) + 3, 8, self.forecast_bg)
                graphics.DrawText(c, self.font, lx + 1, y + h - 4, self.white, tag)
        self._text(c, x + w - self._tw("ECCC") - 2, y + 7, "ECCC", self.dim)

        if meta.get("legend"):
            self._legend(c, x, y, w, meta["legend"])
        done_loops = int(elapsed // loop)
        if done_loops > self._loops_seen.get(tid, 0):        # report each completed loop once
            self._loops_seen[tid] = done_loops
            return "done"
        return None

    def _times(self, tid, meta):
        """Frame times as local datetimes, and the days they span as [(start, end, weekday)] in fractions of
        the loop (a day starts where local midnight falls between two frames) — parsed once per pack."""
        key = id(meta)                                      # a reloaded pack is a new meta object
        cached = self._time_cache.get(tid)
        if cached and cached[0] == key:
            return cached[1], cached[2]
        out = [datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone()
               for s in meta.get("times") or []]
        days = []
        if len(out) > 1:
            start = 0.0
            for i in range(1, len(out)):
                a, b = out[i - 1], out[i]
                if b.date() != a.date():
                    mid = b.replace(hour=0, minute=0, second=0, microsecond=0)
                    span = (b - a).total_seconds()
                    f = (i - 1 + ((mid - a).total_seconds() / span if span > 0 else 1.0)) / (len(out) - 1)
                    days.append((start, f, a.weekday()))
                    start = f
            days.append((start, 1.0, out[-1].weekday()))
        self._time_cache[tid] = (key, out, days)
        return out, days

    def _progress(self, c, x, y, w, idx, n, times, days, now):
        """2-px bar along the bottom, one stretch per day in that day's weekday colour (played part bright,
        the rest dim), a notch at each midnight; nowcast (future) frames checkered."""
        if n <= 1 or not days:
            c.FillRect(x, y, w, 2, self.white)
            return
        px = x + round(w * idx / (n - 1))
        for a, b, wd in days:
            sx, ex = x + round(w * a), x + round(w * b)
            c.FillRect(sx, y, ex - sx, 2, _DAY_DIM[wd])
            if px > sx:
                c.FillRect(sx, y, min(px, ex) - sx, 2, _DAY_BG[wd])
        future = next((i for i, t in enumerate(times) if t.timestamp() > now + 120), None)
        if future is not None:                                  # the forecast part: checkered (amber is Tuesday's colour)
            for cx in range(x + round(w * future / (n - 1)), x + w):
                c.FillRect(cx, y + (cx & 1), 1, 1, self.shadow)
        for a, _, _ in days[1:]:                                # midnight notches, poking up above the bar
            c.FillRect(x + round(w * a), y - 1, 1, 3, self.white)

    def default_loops(self, tid):
        pack = self.store.get(tid)
        return int(pack[0].get("loops", 2)) if pack else 2

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
            self._text(c, cx, cy + 5, scale.get("text") or f"{scale['bands'][-1][0]:g}{scale['unit']}", self.dim)
            cy += 7
