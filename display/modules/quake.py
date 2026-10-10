"""Japan EEW screen: a full-wall takeover in the style of Japanese EEW viewer apps (NIED Kyoshin Monitor).

The hub pushes events (POST /quake) and, once a second, live readings from NIED's ~1,600 strong-motion
stations (POST /quake_obs; simulated for tests). The last event is also on disk (data/quake/event.json)
so a restart mid-event picks it up.

The screen is built from components — station map (270 px or full-wall), P/S wave fronts, shindo
number icons on every affected major city, bulletin, the city list (cities join as the shaking reaches
them, with intensity, live reading and S-wave countdown), a max-intensity history graph, a histogram,
a ranking of the strongest stations, seismograph traces, tickers, colour scale — which the THEMES at
the bottom arrange differently, from the calm Kyoshin Monitor look to a natural-disaster pachinko
machine.
"""

from __future__ import annotations

import base64
import colorsys
import json
import math
import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np

from common import kmoni
from common import quake_calc as qc
from display.gfx import Color
from display.modules.weather_alert import FI, draw_border, _dim

W, H = 448, 192
JST = timezone(timedelta(hours=9))
R = 6378137.0
_SCALE_STRIP = np.array([kmoni.intensity_colour(-3 + i / 6.0) for i in range(60)], dtype=np.uint8)[None, :, :]
_COLOUR_LUT = np.array([(0, 0, 0)] + [kmoni.intensity_colour((c - 1) / 20.0 - 3) for c in range(1, 256)], dtype=np.uint8)
_DARK_TEXT = ("1", "3", "4", "5-", "5+")         # shindo colours light enough for black text
_BANDS = ((0.5, 1.5), (1.5, 2.5), (2.5, 3.5), (3.5, 4.5), (4.5, 5.0), (5.0, 5.5), (5.5, 6.0), (6.0, 6.5), (6.5, 99))
_NEAR = {}
# font roles → (BDF under assets/fonts/, scale). Themes override the Japanese roles with other faces of the
# same size class: jp ≈ 12 px bold, jp_r ≈ 11–12 px, jp_s ≈ 7–8 px, jp_big ≈ 16–24 px.
BASE_FONTS = {"jp": ("alert/jp-mplus12b.bdf", 1), "jp_r": ("alert/jp-k8x12.bdf", 1), "jp_s": ("alert/jp-misaki_gothic_2nd.bdf", 1),
              "jp_big": ("alert/jp-k8x12.bdf", 2), "big": ("alert/10x20.bdf", 2), "huge": ("alert/10x20.bdf", 4),
              "key": ("alert/7x14B.bdf", 1), "num": ("alert/6x13B.bdf", 1), "small": ("alert/5x8.bdf", 1),
              "tiny": ("alert/4x6.bdf", 1), "arcade": ("alert/unscii-8-mcr.bdf", 2), "arcade1": ("alert/unscii-8-mcr.bdf", 1),
              "term": ("alert/t0-12b-uni.bdf", 1),
              "mid": ("alert/spleen-12x24.bdf", 1)}
_FONT_CACHE = {}
_MAP_CACHE = {}


def _jpf(name, scale=1):
    return (f"alert/jp-{name}.bdf", scale)


class _Fonts:
    """Role → font for one theme, each BDF loaded on first use and shared between themes."""

    def __init__(self, roles):
        self.roles = roles

    def __getitem__(self, role):
        spec = self.roles[role]
        if spec not in _FONT_CACHE:
            _FONT_CACHE[spec] = FI(*spec)
        return _FONT_CACHE[spec]

    def preload(self):
        for role in self.roles:
            self[role]


def _code_to_i(code):
    return (code - 1) / 20.0 - 3


def _jp(cls):
    return qc.SHINDO_JP.get(cls, cls)


def _nearest_station(la, lo):
    key = (la, lo)
    if key not in _NEAR:
        pts = np.array([(p[1], p[2]) for p in kmoni.points()])
        _NEAR[key] = int(np.argmin((pts[:, 0] - la) ** 2 + ((pts[:, 1] - lo) * math.cos(math.radians(la))) ** 2))
    return _NEAR[key]


def _rainbow(t, speed=120):
    return tuple(int(v * 255) for v in colorsys.hsv_to_rgb((t * speed % 360) / 360.0, 1, 1))


def shindo_icon(c, x, y, cls, font, centre=True, pad=1):
    """A shindo number tile (the class on its colour, black outline); returns its box."""
    txt = cls if cls in qc.SHINDO_COLOURS else "?"
    col = qc.SHINDO_COLOURS.get(cls, (128, 128, 128))
    w, h = font.width(txt) + 2 * pad + 1, font.h + 2 * pad
    if centre:
        x, y = int(x) - w // 2, int(y) - h // 2
    c.FillRect(x - 1, y - 1, w + 2, h + 2, Color(0, 0, 0))
    c.FillRect(x, y, w, h, Color(*col))
    font.draw(c, x + pad + 1, y + pad, (0, 0, 0) if cls in _DARK_TEXT else (255, 255, 255), txt)
    return (x - 1, y - 1, w + 2, h + 2)


class _Map:
    """One rendered map of Japan with its projection, station pixels and per-event distance grid."""

    def __init__(self, base, name):
        meta = json.load(open(os.path.join(base, f"{name}.json")))
        self.meta, self.w, self.h = meta, meta["w"], meta["h"]
        self.img = np.fromfile(os.path.join(base, f"{name}.bin"), dtype=np.uint8).reshape(self.h, self.w, 3)
        self.tint = (self.img.astype(np.float32) * 0.65 + np.array([120, 0, 0]) * 0.35).astype(np.uint8)
        x0, y0, x1, y1 = meta["merc"]
        xs = x0 + (np.arange(self.w) + 0.5) * meta["mpp"]
        ys = y1 - (np.arange(self.h) + 0.5) * meta["mpp"]
        self.lon = np.broadcast_to(np.degrees(xs / R), (self.h, self.w))
        self.lat = np.broadcast_to(np.degrees(2 * np.arctan(np.exp(ys / R)) - np.pi / 2)[:, None], (self.h, self.w))
        xy = np.array([self.project(p[1], p[2]) for p in kmoni.points()])
        self.sx, self.sy = xy[:, 0].astype(int), xy[:, 1].astype(int)
        self.dist_key = None

    def project(self, lat, lon):
        m = self.meta
        x = math.radians(lon) * R
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
        return (x - m["merc"][0]) / m["mpp"], (m["merc"][3] - y) / m["mpp"]

    def dist(self, ev):
        """Epicentral distance (km) of every pixel, cached per event."""
        key = (ev.get("id"), ev["lat"], ev["lon"])
        if self.dist_key != key:
            la, lo = np.radians(self.lat), np.radians(self.lon)
            p1, l1 = math.radians(ev["lat"]), math.radians(ev["lon"])
            a = np.sin((la - p1) / 2) ** 2 + np.cos(la) * math.cos(p1) * np.sin((lo - l1) / 2) ** 2
            self.d = (6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))).astype(np.float32)
            self.dist_key = key
        return self.d


class QuakeScreen:
    def __init__(self, data_dir, settings=None):
        self.data_dir = data_dir
        self.settings = settings or {}
        self.event = None
        self.obs = None                        # uint8 codes per station (0 = no data), see common.kmoni.encode
        self.obs_t = 0.0
        self.hist = deque(maxlen=240)          # per reading: (t, max intensity, stations per class)
        self.series = {}                       # city / "Epicentre" → recent intensities (traces)
        self.log = deque(maxlen=40)            # terminal theme
        self._maps, self._map_checked = {}, {}
        self._bags = {}
        self._theme_name = self.settings.get("theme") or "monitor"
        self._obs_draw = {}
        self._cities_at = (None, None)
        self._load_saved()
        if data_dir and self.settings.get("enabled"):          # parse the theme's Japanese fonts (~1 s each on the Pi) now,
            import threading                                    # not in the first frame of an earthquake
            threading.Thread(target=self._preload, daemon=True, name="eew-fonts").start()

    def _preload(self):
        time.sleep(20)
        try:
            self.fonts(self._theme_name).preload()
        except Exception:
            pass

    def adopt(self, prev):
        """Carry a live event (and its history) across config reloads."""
        for k in ("event", "obs", "obs_t", "hist", "series", "log"):
            setattr(self, k, getattr(prev, k))

    # -- data -----------------------------------------------------------------------------------
    def fonts(self, name=None):
        name = name or self._theme_name
        if name not in self._bags:
            self._bags[name] = _Fonts({**BASE_FONTS, **(THEMES.get(name, {}).get("fonts") or {})})
        return self._bags[name]

    def _load_saved(self):
        p = os.path.join(self.data_dir or "", "quake", "event.json")
        try:
            ev = json.load(open(p, encoding="utf-8"))
            if time.time() - float(ev.get("origin") or 0) < 300:
                self.event = ev
        except (OSError, ValueError):
            pass

    def set_event(self, ev):
        if not self.event or ev.get("id") != self.event.get("id"):
            self.obs = None
            self.hist.clear()
            self.series = {}
            self.log.clear()
        self.event = ev
        self.log.append(f"{datetime.now(JST):%H:%M:%S} EEW#{ev.get('serial', '-')} M{float(ev.get('mag') or 0):.1f} "
                        f"D{float(ev.get('depth') or 0):.0f} MAX {ev.get('max', '?')}")

    def set_obs(self, encoded, t):
        try:
            obs = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8).copy()
        except Exception:
            return
        self.obs, self.obs_t, self._obs_draw = obs, t, {}
        on = obs[obs > 0]
        if not len(on):
            return
        vals = _code_to_i(on.astype(np.float32))
        mx = float(vals.max())
        counts = [int(((vals >= lo) & (vals < hi)).sum()) for lo, hi in _BANDS]
        self.hist.append((t, mx, counts))
        targets = [(n, la, lo) for n, _, la, lo in qc.CITIES]
        if self.event:
            targets.append(("Epicentre", self.event["lat"], self.event["lon"]))
        for name, la, lo in targets:
            k = _nearest_station(la, lo)
            self.series.setdefault(name, deque(maxlen=200)).append(_code_to_i(float(obs[k])) if obs[k] else -3.0)
        top = int(np.argmax(obs))
        self.log.append(f"{datetime.fromtimestamp(t, JST):%H:%M:%S} MAX{mx:4.1f} {kmoni.points()[top][0]} N{sum(counts)}")

    def live_at(self, la, lo, now):
        """Live reading at the station nearest a place (or None)."""
        if self.obs is None or now - self.obs_t > 20:
            return None
        v = self.obs[_nearest_station(la, lo)]
        return _code_to_i(float(v)) if v else None

    def cities(self, ev, now):
        """Affected cities (see quake_calc.city_forecast), strongest first; computed once per frame."""
        if self._cities_at[0] != now:
            cs = [k for k in qc.city_forecast(ev, now) if k["listed"]]
            cs.sort(key=lambda k: (-qc.class_rank(k["class"]), k["ts"]))
            self._cities_at = (now, cs)
        return self._cities_at[1]

    def next_city(self, ev, now):
        """The city to count down to: strongest still waiting for its S wave, else the strongest hit."""
        cs = self.cities(ev, now)
        pending = [k for k in cs if k["s_in"] > 0]
        return (pending or cs or [None])[0]

    def _map(self, name):
        now = time.time()
        if name not in self._maps or now - self._map_checked.get(name, 0) > 10:     # files checked every 10 s
            self._map_checked[name] = now
            base = os.path.join(self.data_dir or "", "quake")
            try:
                mt = os.path.getmtime(os.path.join(base, f"{name}.bin"))
                if name not in self._maps or self._maps[name][0] != mt:
                    key = (base, name, mt)                     # parsed maps are shared (previews make many screens)
                    if key not in _MAP_CACHE:
                        _MAP_CACHE.clear() if len(_MAP_CACHE) > 4 else None
                        _MAP_CACHE[key] = _Map(base, name)
                    self._maps[name] = (mt, _MAP_CACHE[key])
            except (OSError, ValueError, KeyError):
                if name not in self._maps:
                    return self._map("japan") if name != "japan" else None
        return self._maps[name][1]

    def active(self):
        ev = self.event
        return bool(ev) and time.time() - float(ev.get("origin") or 0) < 600

    def theme(self, now):
        name = (self.event or {}).get("theme") or self.settings.get("theme") or "monitor"
        if name == "tour":
            names = list(THEMES)
            name = names[int(now // 12) % len(names)]
        self._theme_name = name if name in THEMES else "monitor"
        return THEMES[self._theme_name]

    # -- components -----------------------------------------------------------------------------
    def comp_map(self, c, ev, now, name="japan", x0=0, y0=0, shake=0.0, ring_cols=None, dot=2, dim=1.0, icons=True):
        mp = self._map(name)
        if not mp:
            return None
        elapsed = now - float(ev["origin"])
        d = mp.dist(ev)
        rs = qc.front_radius_km(elapsed, ev["depth"], qc.VS)
        rp = qc.front_radius_km(elapsed, ev["depth"], qc.VP)
        ring_w = mp.meta["mpp"] / 1000.0 * math.cos(math.radians(ev["lat"])) * 1.1
        if shake:                                                     # screen shake
            x0 += int(round(math.sin(now * 37) * shake))
            y0 += int(round(math.cos(now * 29) * shake * 0.6))
        x0c, y0c = max(0, x0), max(0, y0)
        ww, hh = min(W - x0c, mp.w - (x0c - x0)), min(H - y0c, mp.h - (y0c - y0))
        sub = c.buf[y0c:y0c + hh, x0c:x0c + ww]
        sy_, sx_ = slice(y0c - y0, y0c - y0 + hh), slice(x0c - x0, x0c - x0 + ww)
        img, tint = mp.img[sy_, sx_], mp.tint[sy_, sx_]
        base = np.where((d[sy_, sx_] < rs)[..., None], tint, img) if rs > 0 else img     # S-crossed area tinted red
        sub[:] = base if dim >= 1 else (base * dim).astype(np.uint8)
        # stations: weakest first so strong shaking draws on top
        if self.obs is not None and len(self.obs) == len(mp.sx) and now - self.obs_t < 20:
            key = (name, x0, y0, dot)
            if key not in self._obs_draw:
                obs = self.obs
                sx, sy = mp.sx + x0, mp.sy + y0
                ok = (obs > 0) & (sx >= x0c) & (sx < x0c + ww - dot) & (sy >= y0c) & (sy < y0c + hh - dot)
                idx = np.flatnonzero(ok)
                idx = idx[np.argsort(obs[idx], kind="stable")]
                self._obs_draw[key] = (sx[idx], sy[idx], _COLOUR_LUT[obs[idx]])
            xs, ys, cols = self._obs_draw[key]
            for dy in range(dot):
                for dx in range(dot):
                    c.buf[ys + dy, xs + dx] = cols
        ring_cols = ring_cols or ((70, 170, 255), (255, 50, 40))
        for r, col in ((rp, ring_cols[0]), (rs, ring_cols[1])):
            if r > 0:
                sub[((d > r - ring_w) & (d < r + ring_w))[sy_, sx_]] = col
        ex, ey = mp.project(ev["lat"], ev["lon"])
        on = int(now * 3) % 2 == 0
        for k in range(-4, 5):
            for (xx, yy) in ((ex + k + x0, ey + k + y0), (ex + k + x0, ey - k + y0)):
                if x0c <= xx < x0c + ww and y0c <= yy < y0c + hh:
                    c.buf[int(yy), int(xx)] = (255, 255, 255) if on else (255, 40, 30)
        if icons:                                                     # shindo numbers on affected cities
            font = self.fonts()["tiny"]
            boxes = []
            for k in self.cities(ev, now):
                x, y = mp.project(k["lat"], k["lon"])
                x, y = x + x0, y + y0
                w = font.width(k["class"]) + 5
                bx, by = int(x) - w // 2, int(y) - 5
                if bx < x0c or by < y0c or bx + w > x0c + ww or by + 10 > y0c + hh:
                    continue
                if any(bx < b[0] + b[2] and b[0] < bx + w and by < b[1] + b[3] and b[1] < by + 10 for b in boxes):
                    continue                                          # a stronger / bigger city already labelled here
                boxes.append(shindo_icon(c, x, y, k["class"], font))
        return mp

    def comp_scale(self, c, x, y, label=True):
        c.buf[y:y + 4, x:x + 60] = _SCALE_STRIP
        if label:
            f = self.fonts()["tiny"]
            f.draw(c, x, y - 7, (190, 190, 200), "-3")
            f.draw(c, x + 54, y - 7, (190, 190, 200), "7")

    def title(self, ev):
        if ev.get("source") == "p2p":
            return "震源・震度情報"
        if ev.get("cancel"):
            return "緊急地震速報 取消"
        return "緊急地震速報" + ("（警報）" if ev.get("warn") else "（予報）")

    def comp_header(self, c, ev, x, y, w, h, accent, font="jp", fg=(0, 0, 0)):
        f = self.fonts()
        c.FillRect(x, y, w, h, Color(*accent))
        f[font].draw(c, x + 4, y + (h - f[font].h) // 2, fg, self.title(ev))
        tag = ("TEST " if ev.get("test") else "") + (f"#{ev.get('serial')}" if ev.get("serial") else "") + (" FINAL" if ev.get("final") else "")
        f["tiny"].draw(c, x + w - 4 - f["tiny"].width(tag), y + (h - 6) // 2, fg, tag)

    def comp_hypo(self, c, ev, x, y, big="jp", small="jp_r"):
        f = self.fonts()
        f[big].draw(c, x, y, (255, 255, 255), ev.get("hypocenter") or "")
        y += f[big].line + 1
        ot = datetime.fromtimestamp(float(ev["origin"]), JST).strftime("%H:%M:%S")
        f[small].draw(c, x, y, (185, 185, 195), f"M{ev['mag']:.1f}  深さ{ev['depth']:.0f}km  {ot}")
        return y + f[small].line + 1

    def comp_max(self, c, ev, x, y, w=62, h=48, big="big", spin=False, now=0.0):
        f = self.fonts()
        cls = ev.get("max") or "0"
        if spin:                                                      # pachinko reel before it lands
            cls = qc.SHINDO[1 + int(now * 18) % 9]
        col = qc.SHINDO_COLOURS.get(cls, (128, 128, 128))
        c.FillRect(x, y, w, h, Color(*col))
        dark = (0, 0, 0) if cls in _DARK_TEXT else (255, 255, 255)
        f["jp_r" if h >= 40 else "jp_s"].draw(c, x + 3, y + 2, dark, "最大震度")
        if f[big].h + 12 > h:                                         # short tile: a smaller numeral
            big = "mid"
        s = _jp(cls)
        f[big].draw(c, x + 5, y + h - f[big].h - 3, dark, s[0])
        if len(s) > 1:
            f["jp"].draw(c, x + 7 + f[big].width(s[0]), y + h - 16, dark, s[1:])

    def comp_next(self, c, ev, now, x, y, w, h, num="big"):
        """Countdown box for the next city the S wave reaches."""
        f = self.fonts()
        k = self.next_city(ev, now)
        c.FillRect(x, y, w, h, Color(14, 14, 22))
        if not k:
            f["jp_r"].draw(c, x + 4, y + 3, (160, 160, 170), "主要都市")
            f["jp_r"].draw(c, x + 4, y + 17, (160, 160, 170), "揺れ到達前")
            return
        shindo_icon(c, x + 2, y + 2, k["class"], f["num"], centre=False)
        f["jp_r"].draw(c, x + 26, y + 2, (255, 255, 255), k["jp"])
        if k["s_in"] > 0:
            f["jp_s"].draw(c, x + w - 2 - f["jp_s"].width("S波まで"), y + 4, (200, 200, 210), "S波まで")
            t = f"{k['s_in']:.0f}"
            col = (255, 255, 255) if int(now * 2) % 2 else (255, 110, 110)
            f[num].draw(c, x + 4, y + h - f[num].h - 1, col, t)
            f["jp_r"].draw(c, x + 6 + f[num].width(t), y + h - 14, col, "秒")
        else:
            f["jp_big"].draw(c, x + 4, y + h - f["jp_big"].h - 1, (255, 90, 90), "到達")

    # list styles, roomiest first: (name font, icon font, icon padding, row height, countdown font)
    CITY_STYLES = [("jp_r", "small", 1, 14, "small"), ("jp_s", "tiny", 1, 11, "small"), ("jp_s", "tiny", 0, 9, "tiny")]

    def city_style(self, n, w, h, level=0):
        """The roomiest style that fits ``n`` cities in w×h: (style, columns, fits)."""
        for st in self.CITY_STYLES[level:]:
            for cols in (1, 2) if w >= 150 else (1,):
                if n <= (h // st[3]) * cols:
                    return st, cols, True
        return self.CITY_STYLES[-1], 2 if w >= 150 else 1, False

    def comp_cities(self, c, ev, now, x, y, w, h, level=0, timebar=False, eng=False):
        """The affected-city list: shindo icon, name, live reading, S countdown. As cities join it shrinks
        its rows, then splits into two columns, then scrolls smoothly through the rest."""
        f = self.fonts()
        cs = self.cities(ev, now)
        if not cs:
            f["jp_s" if w < 120 else "jp_r"].draw(c, x, y + 1, (150, 150, 160), "揺れ到達前" if w < 120 else "揺れ到達を待っています")
            return
        (name, icon, pad, row, num), cols, fits = self.city_style(len(cs), w, h, level)
        cw = (w - 4 * (cols - 1)) // cols
        per_col = -(-len(cs) // cols)
        off = 0
        if not fits:                                                   # scroll: 1.5 rows/s, pause at the top
            span = per_col * row + row
            off = int(max(0.0, (now % (span / 15.0 + 3)) - 3) * 15) % span
            keep = (c.buf[max(0, y - row - 2):y].copy(), c.buf[y + h:min(H, y + h + row + 2)].copy())
        for i, k in enumerate(cs):
            col_i, r = (i // per_col, i % per_col) if cols > 1 else (0, i)
            cx = x + col_i * (cw + 4)
            reps = (0, per_col * row + row) if not fits else (0,)
            for rep in reps:
                yy = y + r * row - off + rep
                if yy <= y - row or yy >= y + h:
                    continue
                self._city_row(c, ev, now, k, cx, yy, cw, name, icon, pad, row, num, timebar, eng)
        if not fits:
            top, bot = keep
            c.buf[y - len(top):y] = top
            c.buf[y + h:y + h + len(bot)] = bot
            c.FillRect(x + w - 1, y + int(h * off / (per_col * row + row)), 1, max(3, h * h // (per_col * row + row)),
                       Color(120, 120, 140))                           # scroll position

    def _city_row(self, c, ev, now, k, x, y, w, name, icon, pad, row, num, timebar, eng):
        f = self.fonts()
        ib = shindo_icon(c, x + 1, y + (row - f[icon].h - 2 * pad) // 2, k["class"], f[icon], centre=False, pad=pad)
        nx = x + ib[2] + 2
        f[name].draw(c, nx, y + (row - 1 - f[name].h) // 2, (235, 235, 240), k["name"].upper() if eng else k["jp"])
        rx = x + w - 2
        if k["s_in"] > 0:
            t, fn = f"{k['s_in']:.0f}s", f[num]
            col = (255, 255, 255) if int(now * 2) % 2 else (255, 110, 110)
        else:
            t, fn = ("HIT", f[num]) if eng else ("到達", f["jp_s" if row < 13 else "jp_r"])
            col = (255, 100, 100)
        fn.draw(c, rx - fn.width(t), y + (row - 1 - fn.h) // 2, col, t)
        v = self.live_at(k["lat"], k["lon"], now)
        if v is not None and v > -1 and w > 90:
            s = f"{v:.1f}"
            f["tiny"].draw(c, rx - 24 - f["tiny"].width(s), y + (row - 7) // 2, kmoni.intensity_colour(v), s)
        if timebar:                                                    # P → S progress at the city
            frac = max(0.0, min(1.0, (now - float(ev["origin"])) / max(1.0, k["ts"])))
            c.FillRect(nx, y + row - 2, int((rx - nx) * frac), 1, Color(*(255, 60, 40) if k["s_in"] <= 0 else (70, 170, 255)))

    def comp_graph(self, c, x, y, w, h, label="最大計測震度"):
        """Max station intensity over time, with shindo grid lines."""
        f = self.fonts()
        c.FillRect(x, y, w, h, Color(8, 8, 14))
        for lvl in (0, 2, 4, 6):
            c.FillRect(x, y + h - 1 - int((lvl + 3) / 10 * (h - 2)), w, 1, Color(30, 30, 44))
        pts = [v for _, v, _ in list(self.hist)[-w:]]
        prev = None
        for i, v in enumerate(pts):
            yy = y + h - 1 - int((max(-3, min(7, v)) + 3) / 10 * (h - 2))
            xx = x + w - len(pts) + i
            a, b = sorted((prev if prev is not None else yy, yy))
            c.FillRect(xx, a, 1, b - a + 1, Color(*kmoni.intensity_colour(v)))
            prev = yy
        if label:
            f["jp_s"].draw(c, x + 2, y + 1, (160, 160, 170), label)
        if pts:
            s = f"{pts[-1]:.1f}"
            f["small"].draw(c, x + w - 2 - f["small"].width(s), y + 1, kmoni.intensity_colour(pts[-1]), s)

    def comp_histogram(self, c, x, y, w, h, label="震度別観測点数"):
        """Stations per shindo class in the newest reading."""
        f = self.fonts()
        c.FillRect(x, y, w, h, Color(8, 8, 14))
        counts = self.hist[-1][2] if self.hist else [0] * 9
        bw = max(3, (w - 4) // 9)
        top = max(1, max(counts))
        for k, (cls, n) in enumerate(zip(qc.SHINDO[1:], counts)):
            bh = int((h - 10) * math.sqrt(n / top)) if n else 0
            c.FillRect(x + 2 + k * bw, y + h - 8 - bh, bw - 1, bh, Color(*qc.SHINDO_COLOURS[cls]))
            f["tiny"].draw(c, x + 2 + k * bw, y + h - 7, (150, 150, 160), cls[0])
        if label:
            f["jp_s"].draw(c, x + 2, y + 1, (160, 160, 170), label)

    def comp_ranking(self, c, x, y, w, rows, font="jp_s"):
        """Strongest stations right now: rank, region, bar, value."""
        f = self.fonts()
        if self.obs is None:
            return
        obs = self.obs
        pts = kmoni.points()
        lh = f[font].line + 1
        for r, k in enumerate(np.argsort(obs)[::-1][:rows]):
            if obs[k] == 0:
                break
            v = _code_to_i(float(obs[k]))
            col = kmoni.intensity_colour(v)
            yy = y + r * lh
            c.FillRect(x + 50, yy + 1, int((w - 66) * max(0.0, (v + 3) / 10)), lh - 3, Color(*_dim(col, 0.7)))
            f["tiny"].draw(c, x, yy + 1, (150, 150, 160), f"{r + 1:>2}")
            f[font].draw(c, x + 10, yy, (230, 230, 235), (pts[k][5] or pts[k][0])[:5])
            s = f"{v:.1f}"
            f["small"].draw(c, x + w - f["small"].width(s), yy, col, s)

    def comp_traces(self, c, ev, now, x, y, w, h, n=3):
        """Seismograph-style traces: the epicentre, then the strongest affected cities."""
        f = self.fonts()
        names = [("Epicentre", "震源付近")] + [(k["name"], k["jp"]) for k in self.cities(ev, now)[: n - 1]]
        lh = h // n
        for r, (name, jp) in enumerate(names[:n]):
            yy = y + r * lh
            c.FillRect(x, yy, w, lh - 1, Color(6, 10, 8))
            mid = yy + lh // 2
            c.FillRect(x, mid, w, 1, Color(25, 45, 30))
            ser = list(self.series.get(name, []))[-w:]
            prev = None
            for i, v in enumerate(ser):
                amp = max(0.0, v + 3) / 10 * (lh / 2 - 1)
                py = int(mid - math.sin(i * 2.7 + r) * amp * (1.0 if i % 2 else -0.8))
                px = x + w - len(ser) + i
                a, b = sorted((prev if prev is not None else py, py))
                c.FillRect(px, a, 1, b - a + 1, Color(*kmoni.intensity_colour(v)))
                prev = py
            f["jp_s"].draw(c, x + 2, yy + 1, (170, 200, 170), jp)
            if ser:
                s = f"{ser[-1]:.1f}"
                f["tiny"].draw(c, x + w - f["tiny"].width(s) - 2, yy + 2, kmoni.intensity_colour(ser[-1]), s)

    def comp_ticker(self, c, ev, now, x, y, w, h, bg, fg, font="jp_r", speed=60):
        f = self.fonts()[font]
        cs = self.cities(ev, now)
        towns = "　".join(f"{k['jp']} {_jp(k['class'])}" for k in cs[:8])
        msg = (f"　{self.title(ev)}　{ev.get('hypocenter', '')}で地震　M{ev['mag']:.1f}　最大震度{_jp(ev.get('max') or '0')}　"
               + (f"予想震度　{towns}　" if towns else "") + "強い揺れに警戒してください　")
        c.FillRect(x, y, w, h, Color(*bg))
        mw = max(1, f.width(msg))
        xx = x - int(now * speed) % mw
        sub = c.buf[y:y + h, :x].copy() if x > 0 else None
        while xx < x + w:
            f.draw(c, xx, y + (h - f.h) // 2, fg, msg)
            xx += mw
        if sub is not None:
            c.buf[y:y + h, :x] = sub
        if x + w < W:
            pass

    # -- drawing -------------------------------------------------------------------------------
    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        ev = self.event
        c.FillRect(0, 0, W, H, Color(0, 0, 0))
        if not ev:
            self.fonts()["key"].draw(c, 8, 8, (200, 200, 200), "No earthquake early warning.")
            return None
        elapsed = now - float(ev["origin"])
        accent = (150, 150, 160) if ev.get("cancel") else (255, 30, 30) if ev.get("warn") else (255, 200, 0)
        self.theme(now)["draw"](self, c, ev, now, elapsed, accent)
        return "done" if elapsed > 240 else None

    def jackpot(self, c, ev, now):
        """Invert the frame (blinking) for a moment as the S wave hits each city."""
        if any(-0.8 < k["s_in"] <= 0 for k in self.cities(ev, now)) and int(now * 8) % 2:
            c.buf[:] = 255 - c.buf


# =============================================================================================== themes
def _monitor(s, c, ev, now, el, accent):
    """Kyoshin Monitor: map on the left, bulletin and city list on the right."""
    f = s.fonts()
    mp = s.comp_map(c, ev, now)
    mw = mp.w if mp else 270
    s.comp_scale(c, 6, H - 9)
    f["tiny"].draw(c, 70, H - 10, (190, 190, 200), "SIMULATED" if ev.get("test") else "LIVE NIED")
    c.FillRect(mw, 0, 1, H, Color(*accent))
    x = mw + 6
    s.comp_header(c, ev, mw + 1, 0, W - mw - 1, 17, accent)
    y = s.comp_hypo(c, ev, x, 20)
    s.comp_max(c, ev, x, y)
    s.comp_next(c, ev, now, x + 66, y, W - x - 71, 48)
    y += 52
    s.comp_cities(c, ev, now, x, y, W - x - 6, H - y - 11)
    f["jp_s"].draw(c, x, H - 9, (140, 140, 150), f"+{el:.0f}秒  強震モニタ(防災科研)・気象庁")
    draw_border(c, "pulse" if ev.get("warn") else "chase", accent, now)


def _pachinko(s, c, ev, now, el, accent):
    """Everything at once: ticker, shaking map, flashing banner, slot reel, graphs, ranking, cities, traces."""
    f = s.fonts()
    k = s.next_city(ev, now)
    shake = max(0.0, min(3.0, k["intensity"] - 2.0)) if k and k["s_in"] <= 0 else 0.0
    mp = s.comp_map(c, ev, now, y0=11, shake=shake, ring_cols=((0, 255, 255), (255, 0, 120)))
    mw = mp.w if mp else 270
    s.comp_ticker(c, ev, now, 0, 0, W, 11, (255, 210, 0) if int(now * 4) % 2 else (255, 60, 0), (0, 0, 0), "jp_r", 90)
    if int(now * 3) % 2 == 0:
        txt = "強い揺れに警戒" if ev.get("warn") else "揺れに注意"
        c.FillRect(4, 14, f["jp"].width(txt) + 8, 15, Color(*accent))
        f["jp"].draw(c, 8, 15, (255, 255, 255), txt)
    s.comp_ranking(c, 4, 33, 104, 6)
    f["arcade1"].draw(c, 70, H - 10, _rainbow(now), f"M{ev['mag']:.1f} D{ev['depth']:.0f}KM +{el:.0f}S")
    s.comp_scale(c, 6, H - 7, label=False)
    x, rw = mw + 3, W - mw - 6
    c.FillRect(mw, 11, 1, H - 11, Color(*accent))
    s.comp_header(c, ev, x, 12, rw, 13, accent, "jp_r")
    s.comp_max(c, ev, x, 27, 50, 40, spin=el < 4, now=now)
    s.comp_next(c, ev, now, x + 53, 27, rw - 53, 40, "arcade")
    s.comp_graph(c, x, 70, rw // 2 - 1, 34)
    s.comp_histogram(c, x + rw // 2 + 1, 70, rw - rw // 2 - 1, 34)
    s.comp_cities(c, ev, now, x, 106, rw, 44, level=1)
    s.comp_traces(c, ev, now, x, 151, rw, 33)
    draw_border(c, "lights", _rainbow(now, 200), now)
    s.jackpot(c, ev, now)


def _fever(s, c, ev, now, el, accent):
    """Fever mode: the full-wall map with every panel floating on it, two tickers, rainbow everything."""
    f = s.fonts()
    k = s.next_city(ev, now)
    shake = max(0.0, min(4.0, k["intensity"] - 1.5)) if k and k["s_in"] <= 0 else 0.0
    s.comp_map(c, ev, now, "japan_wide", shake=shake, ring_cols=(_rainbow(now, 300), (255, 0, 160)))
    s.comp_ticker(c, ev, now, 0, 0, W, 13, _rainbow(now, 90), (0, 0, 0), "jp", 110)
    # left: big flashing max shindo, ranking, graph
    c.buf[14:H - 14, 0:132] = (c.buf[14:H - 14, 0:132] * 0.3).astype(np.uint8)
    s.comp_max(c, ev, 3, 16, 56, 44, spin=el < 5, now=now)
    s.comp_histogram(c, 62, 16, 68, 44, label="")
    s.comp_ranking(c, 3, 63, 126, 8)
    s.comp_graph(c, 3, H - 46, 126, 31, label="")
    # right: countdown + cities
    rx = W - 128
    c.buf[14:H - 14, rx:W] = (c.buf[14:H - 14, rx:W] * 0.3).astype(np.uint8)
    s.comp_next(c, ev, now, rx + 2, 16, 124, 44, "arcade")
    s.comp_cities(c, ev, now, rx + 2, 63, 124, H - 79, level=1)
    # centre: flashing banner
    if int(now * 4) % 2 == 0:
        txt = f"震度{_jp(ev.get('max') or '0')}"
        tw = f["jp_big"].width(txt)
        bx = (W - tw) // 2
        c.FillRect(bx - 6, 18, tw + 12, f["jp_big"].h + 6, Color(*_rainbow(now + 0.5, 240)))
        f["jp_big"].draw(c, bx, 21, (0, 0, 0), txt)
    s.comp_ticker(c, ev, now, 0, H - 13, W, 13, (0, 20, 110), (255, 255, 0), "jp_r", 70)
    draw_border(c, "lights", _rainbow(now, 400), now)
    s.jackpot(c, ev, now)


def _broadcast(s, c, ev, now, el, accent):
    """TV broadcast: full-wall map, red EEW box top-left, city panel right, blue crawl."""
    f = s.fonts()
    s.comp_map(c, ev, now, "japan_wide", dim=0.9)
    bh = 6 + f["jp_big"].line + f["jp_r"].line
    bw = max(150, f["jp_big"].width("緊急地震速報") + 12)
    c.FillRect(0, 0, bw, bh, Color(200, 0, 0))
    c.FillRect(0, bh, bw, 2, Color(255, 255, 255))
    f["jp_big"].draw(c, 6, 3, (255, 255, 255), "緊急地震速報")
    f["jp_r"].draw(c, 6, 4 + f["jp_big"].line, (255, 255, 255), "強い揺れに警戒" if ev.get("warn") else "揺れに注意")
    f["jp_s"].draw(c, bw - 6 - f["jp_s"].width("気象庁"), bh - 10, (255, 220, 220), "気象庁")
    px = W - 128
    c.buf[0:H - 18, px:W] = (c.buf[0:H - 18, px:W] * 0.25).astype(np.uint8)
    y = s.comp_hypo(c, ev, px + 4, 3, "jp", "jp_s")
    s.comp_max(c, ev, px + 4, y + 1, 50, 36)
    s.comp_next(c, ev, now, px + 57, y + 1, 68, 36, "key")
    s.comp_cities(c, ev, now, px + 4, y + 40, 120, H - 18 - y - 41, level=1)
    s.comp_ticker(c, ev, now, 0, H - 17, W, 17, (0, 30, 120), (255, 255, 255), "jp", 70)
    c.FillRect(0, H - 18, W, 1, Color(255, 255, 255))


def _dashboard(s, c, ev, now, el, accent):
    """Map plus instruments: max-intensity history, histogram, cities with P→S progress bars."""
    mp = s.comp_map(c, ev, now)
    mw = mp.w if mp else 270
    s.comp_scale(c, 6, H - 9)
    x, rw = mw + 4, W - mw - 8
    s.comp_header(c, ev, mw, 0, W - mw, 15, accent, "jp_r")
    y = s.comp_hypo(c, ev, x, 17, "jp_r", "jp_s")
    s.comp_graph(c, x, y, rw // 2 - 1, 40)
    s.comp_histogram(c, x + rw // 2 + 1, y, rw - rw // 2 - 1, 40)
    s.comp_cities(c, ev, now, x, y + 43, rw, H - y - 47, timebar=True)
    draw_border(c, "notch", accent, now)


def _terminal(s, c, ev, now, el, accent):
    """Green CRT: the map plus city readouts and a scrolling data log."""
    f = s.fonts()
    green = (60, 255, 110)
    mp = s.comp_map(c, ev, now, dim=0.8, ring_cols=(green, (255, 255, 255)))
    mw = mp.w if mp else 270
    x = mw + 5
    t, sf = f["term"], f["small"]
    c.FillRect(mw, 0, W - mw, H, Color(0, 10, 0))
    t.draw(c, x, 2, green, "> JMA EEW FEED")
    t.draw(c, x, 2 + t.line, (255, 255, 255), f"> M{ev['mag']:.1f} D{ev['depth']:.0f}KM MAX {ev.get('max')}")
    f["jp_big"].draw(c, x, 3 + 2 * t.line, (200, 255, 200), ev.get("hypocenter") or "")
    yy = 5 + 2 * t.line + f["jp_big"].line
    for k in s.cities(ev, now)[:5]:
        line = f"{k['name'][:9].upper():9} {k['class']:>2} " + (f"S-{k['s_in']:3.0f}s" if k["s_in"] > 0 else "ARRIVED")
        sf.draw(c, x, yy, qc.SHINDO_COLOURS[k["class"]], line)
        yy += sf.line
    yy += 2
    for ln in list(s.log)[-max(1, (H - yy - 4) // sf.line):]:
        sf.draw(c, x, yy, (40, 200, 80), ln[:32])
        yy += sf.line
    if int(now * 2) % 2:
        c.FillRect(x, min(yy, H - 10), 5, 7, Color(*green))
    c.buf[1::2, :] = (c.buf[1::2, :] * 0.75).astype(np.uint8)
    draw_border(c, "scanbars", green, now)


def _countdown(s, c, ev, now, el, accent):
    """One enormous number: seconds until the S wave reaches the next major city."""
    f = s.fonts()
    mp = s.comp_map(c, ev, now, dim=0.7)
    mw = mp.w if mp else 270
    x = mw + 8
    k = s.next_city(ev, now)
    f["jp_big"].draw(c, x, 4, accent, s.title(ev))
    y = 6 + f["jp_big"].line
    if k:
        shindo_icon(c, x, y, k["class"], f["num"], centre=False)
        f["jp_r"].draw(c, x + 24, y + 1, (230, 230, 240), f"{k['jp']} S波到達まで" if k["s_in"] > 0 else f"{k['jp']} S波到達")
        y += 18
        big = f"{k['s_in']:.0f}" if k["s_in"] > 0 else "0"
        col = (255, 255, 255) if k["s_in"] > 5 or int(now * 3) % 2 else (255, 60, 60)
        f["huge"].draw(c, x, y, col, big)
        f["jp_big"].draw(c, x + f["huge"].width(big) + 4, y + f["huge"].h - f["jp_big"].h, col, "秒")
        y += f["huge"].h + 4
        f["jp_r"].draw(c, x, y, qc.SHINDO_COLOURS[k["class"]], f"予想震度 {_jp(k['class'])}")
        f["jp_s"].draw(c, x, y + 15, (170, 170, 180), f"{ev.get('hypocenter', '')}  M{ev['mag']:.1f}")
    else:
        f["jp_r"].draw(c, x, y + 4, (200, 200, 210), "揺れ到達を待っています")
    draw_border(c, "glow", accent, now)


def _ranking(s, c, ev, now, el, accent):
    """Map plus a live league table of the strongest stations."""
    f = s.fonts()
    mp = s.comp_map(c, ev, now)
    mw = mp.w if mp else 270
    s.comp_scale(c, 6, H - 9)
    x = mw + 4
    s.comp_header(c, ev, mw, 0, W - mw, 15, accent, "jp_r")
    f["jp_r"].draw(c, x, 17, (255, 255, 255), f"{ev.get('hypocenter', '')} M{ev['mag']:.1f}")
    f["jp_s"].draw(c, x, 31, (150, 150, 160), "リアルタイム震度 上位観測点")
    s.comp_ranking(c, x, 41, W - x - 5, 16, "jp_s")
    draw_border(c, "corners", accent, now)


def _seismo(s, c, ev, now, el, accent):
    """Seismograph wall: traces for the epicentre and the strongest cities, then the city list."""
    mp = s.comp_map(c, ev, now)
    mw = mp.w if mp else 270
    x, rw = mw + 3, W - mw - 6
    s.comp_header(c, ev, mw, 0, W - mw, 15, accent, "jp_r")
    s.comp_traces(c, ev, now, x, 17, rw, 96, n=4)
    s.comp_cities(c, ev, now, x, 116, rw, H - 120, level=1)
    draw_border(c, "double", accent, now)


def _arcade(s, c, ev, now, el, accent):
    """Arcade cabinet: neon rings, a scoreboard in the Midway MCR font, chasing lights."""
    f = s.fonts()
    neon = _rainbow(now)
    mp = s.comp_map(c, ev, now, ring_cols=(neon, (255, 0, 200)), shake=1.0 if int(now * 8) % 3 == 0 else 0.0)
    mw = mp.w if mp else 270
    x = mw + 6
    a, a1 = f["arcade"], f["arcade1"]
    c.FillRect(mw, 0, W - mw, H, Color(10, 0, 20))
    a.draw(c, x, 4, neon, "QUAKE!")
    f["jp_big"].draw(c, x + 102, 2, (255, 255, 255) if int(now * 4) % 2 else (255, 0, 120), "地震")
    a1.draw(c, x, 24, (255, 255, 255), f"MAG   {ev['mag']:.1f}")
    a1.draw(c, x, 34, (255, 255, 255), f"DEPTH {ev['depth']:.0f}KM")
    a1.draw(c, x, 46, (255, 255, 0), "MAX SHINDO")
    cls = ev.get("max") or "0"
    a.draw(c, x + 104, 42, qc.SHINDO_COLOURS.get(cls, (255, 255, 255)), cls)
    a1.draw(c, x, 62, (0, 255, 255), "CITY        SH   S")
    yy = 72
    for k in s.cities(ev, now)[:8]:
        a1.draw(c, x, yy, (255, 255, 255), f"{k['name'][:10].upper():10}")
        a1.draw(c, x + 96, yy, qc.SHINDO_COLOURS[k["class"]], f"{k['class']:>2}")
        t = f"{k['s_in']:3.0f}" if k["s_in"] > 0 else "HIT"
        a1.draw(c, x + 128, yy, (255, 255, 255) if k["s_in"] > 0 or int(now * 6) % 2 else neon, t)
        yy += 10
    s.comp_graph(c, x, max(yy + 2, H - 30), W - x - 6, min(26, H - yy - 8), label="")
    draw_border(c, "lights", neon, now)


def _gogai(s, c, ev, now, el, accent):
    """号外 (newspaper extra): sepia map, vertical (tategaki) headlines in mincho, city list."""
    f = s.fonts()
    mp = s.comp_map(c, ev, now, dim=0.75, ring_cols=((230, 200, 140), (230, 40, 40)))
    mw = mp.w if mp else 270
    c.FillRect(mw, 0, W - mw, H, Color(24, 18, 12))
    c.FillRect(mw, 0, 2, H, Color(200, 40, 30))
    x = W - 6
    def tate(font, text, col, top):
        nonlocal x
        x -= font.h + 3
        y = top
        for ch in text:
            font.draw(c, x + (font.h - font.width(ch)) // 2, y, col, ch)
            y += font.line
            if y > H - font.line:
                break
    big = f["jp_big"]
    c.FillRect(W - big.h - 12, 4, big.h + 6, big.line * 2 + 6, Color(200, 30, 30))
    tate(big, "号外", (255, 245, 230), 7)
    x -= 3
    tate(f["jp"], f"{ev.get('hypocenter', '')}で地震", (240, 230, 210), 6)
    tate(f["jp"], f"最大震度{_jp(ev.get('max') or '0')}", qc.SHINDO_COLOURS.get(ev.get("max") or "0", (255, 255, 255)), 6)
    tate(f["jp_s"], f"M{ev['mag']:.1f} 深さ{ev['depth']:.0f}km", (200, 190, 170), 6)
    lw = x - mw - 8
    f["jp_r"].draw(c, mw + 6, 4, (230, 200, 140), "各地の予想震度")
    s.comp_cities(c, ev, now, mw + 6, 19, lw, H - 23, level=1)
    draw_border(c, "double", (200, 40, 30), now)


THEMES = {
    "monitor": {"label": "Kyoshin Monitor", "hint": "NIED's monitor look: station dots, waves, bulletin, cities",
                "draw": _monitor, "fonts": {}, "type": "M+ 12 bold · k8x12 · Misaki"},
    "pachinko": {"label": "Pachinko", "hint": "Ticker, shaking map, slot reel, graphs, ranking, cities, traces", "draw": _pachinko,
                 "fonts": {"jp": _jpf("shnm12b"), "jp_r": _jpf("k8x12L"), "jp_s": _jpf("misaki_gothic"), "jp_big": _jpf("kappa20b")},
                 "type": "Shinonome bold · k8x12L · Misaki · Kappa 20"},
    "fever": {"label": "Fever mode", "hint": "Full-wall map, every panel floating on it, two tickers, rainbow", "draw": _fever,
              "fonts": {"jp": _jpf("shnm12maru"), "jp_r": _jpf("shnm12b"), "jp_s": _jpf("misaki_gothic"), "jp_big": _jpf("ayu20")},
              "type": "Shinonome maru · Ayu 20"},
    "broadcast": {"label": "TV broadcast", "hint": "Full-wall map, red EEW box, city panel, blue crawl", "draw": _broadcast,
                  "fonts": {"jp_big": _jpf("jiskan16"), "jp_r": _jpf("shnm12b")}, "type": "Jiskan 16 · Shinonome bold · M+"},
    "dashboard": {"label": "Instrument panel", "hint": "Intensity history, histogram, cities with P→S bars", "draw": _dashboard,
                  "fonts": {"jp": _jpf("shnm12b"), "jp_r": _jpf("shnm12maru"), "jp_s": _jpf("k8x12S")},
                  "type": "Shinonome maru · k8x12S"},
    "terminal": {"label": "Data terminal", "hint": "Green CRT with city readouts and a live data log", "draw": _terminal,
                 "fonts": {"jp_r": _jpf("mplus10r"), "jp_big": _jpf("unifont")}, "type": "GNU Unifont · M+ 10 · t0"},
    "countdown": {"label": "Countdown", "hint": "One huge number: seconds until the S wave hits the next city", "draw": _countdown,
                  "fonts": {"jp_big": _jpf("shnm16min"), "jp_r": _jpf("shnm12min"), "jp_s": _jpf("misaki_mincho")},
                  "type": "Shinonome mincho 16/12 · Misaki mincho"},
    "ranking": {"label": "League table", "hint": "The strongest stations right now", "draw": _ranking,
                "fonts": {"jp_r": _jpf("shnm12min"), "jp_s": _jpf("k8x12S")}, "type": "Shinonome mincho · k8x12S"},
    "seismo": {"label": "Seismographs", "hint": "Traces at the epicentre and the strongest cities", "draw": _seismo,
               "fonts": {"jp_r": _jpf("k14"), "jp_s": _jpf("misaki_mincho")}, "type": "JIS X 0213 K14 · Misaki mincho"},
    "arcade": {"label": "Arcade cabinet", "hint": "Neon rings, Midway MCR font scoreboard, chasing lights", "draw": _arcade,
               "fonts": {"jp_big": _jpf("ayu18")}, "type": "Unscii MCR · Ayu 18"},
    "gogai": {"label": "号外 extra edition", "hint": "Newspaper extra: vertical mincho headlines, city list", "draw": _gogai,
              "fonts": {"jp_big": _jpf("kappa20b"), "jp": _jpf("shnm16b"), "jp_r": _jpf("shnm12min"), "jp_s": _jpf("misaki_mincho")},
              "type": "Kappa 20 · Shinonome 16 bold · mincho"},
}
