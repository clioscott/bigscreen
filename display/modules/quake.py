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
# font roles → (BDF under assets/fonts/, scale — always 1: fonts are never scaled). Approved fonts only
# (control panel → Fonts). Themes override roles with other approved faces of the same size class:
# jp ≈ 12 px bold · jp_r ≈ 11–12 · jp_s ≈ 7–8 · jp_mid ≈ 16 · jp_big ≈ 18–24 · big ≈ 24 · mid ≈ 20 (Latin numerals).
_C = "candidates/"
BASE_FONTS = {"jp": (_C + "jp-kaname12b.bdf", 1), "jp_r": ("alert/jp-k8x12.bdf", 1), "jp_s": ("alert/jp-misaki_gothic_2nd.bdf", 1),
              "jp_mid": ("alert/jp-shnm16b.bdf", 1), "jp_big": ("alert/jp-kappa20b.bdf", 1),
              "big": (_C + "u8-win-crox4hb.bdf", 1), "mid": (_C + "u8-win-crox3hb.bdf", 1), "key": ("alert/7x14B.bdf", 1),
              "num": ("alert/7x13B.bdf", 1), "small": ("alert/5x8.bdf", 1), "tiny": ("alert/tom-thumb.bdf", 1),
              "arcade": (_C + "retro-pixel-thick.bdf", 1), "arcade1": (_C + "retro-pixel-cute-mono.bdf", 1),
              "term": ("alert/t0-12b-uni.bdf", 1), "kana": (_C + "jp-ark12-prop.bdf", 1)}
_FONT_CACHE = {}
MAP_CITY_RANK = 10              # map icons: the 10 biggest cities (when affected), plus any city expecting 5弱+
_MAP_CACHE = {}


def _jpf(name, scale=1):
    """A Japanese font by short name, in alert/ (the originals) or candidates/ (added by the font audit)."""
    path = f"alert/jp-{name}.bdf"
    if not os.path.exists(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                       "assets", "fonts", path)):
        path = f"candidates/jp-{name}.bdf"
    return (path, 1)


# seven-segment digits, drawn as graphics at any size (big countdowns without scaling a font)
_SEG = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd", "6": "afgedc", "7": "abc",
        "8": "abcdefg", "9": "abfgcd", "-": "g", " ": "", "E": "afged", "r": "eg", "o": "cdeg", "H": "bcefg", "L": "def",
        "P": "abefg", "S": "afgcd", "A": "abcefg", "F": "aefg", "C": "adef", "U": "bcdef"}


def seven_seg(c, x, y, text, dw, dh, t=None, on=(255, 40, 30), off=None, gap=None):
    """Draw ``text`` as seven-segment digits dw×dh px each (unlit segments in ``off`` if given); returns width."""
    t = t or max(2, dw // 6)
    gap = gap if gap is not None else max(2, dw // 4)
    half = dh // 2
    x0 = x
    for ch in text:
        if ch in ".:":
            dot = max(2, t)
            if ch == ":":
                c.FillRect(x, y + dh // 3 - dot // 2, dot, dot, Color(*on))
                c.FillRect(x, y + 2 * dh // 3 - dot // 2, dot, dot, Color(*on))
            else:
                c.FillRect(x, y + dh - dot, dot, dot, Color(*on))
            x += dot + gap
            continue
        segs = {"a": (x + t, y, dw - 2 * t, t), "b": (x + dw - t, y + t, t, half - t), "c": (x + dw - t, y + half, t, half - t),
                "d": (x + t, y + dh - t, dw - 2 * t, t), "e": (x, y + half, t, half - t), "f": (x, y + t, t, half - t),
                "g": (x + t, y + half - t // 2, dw - 2 * t, t)}
        lit = _SEG.get(ch, "")
        for name, (sx, sy, sw, sh) in segs.items():
            if name in lit:
                c.FillRect(sx, sy, sw, sh, Color(*on))
            elif off is not None:
                c.FillRect(sx, sy, sw, sh, Color(*off))
        x += dw + gap
    return x - x0 - gap


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


def _shade(region, shift=None):
    """Darken a canvas region in place: ≈ 0.31× (or 1/2**shift) with integer shifts, no float maths."""
    if shift:
        region >>= shift
    else:
        np.right_shift(region, 2, out=region)
        region += region >> 2


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

    def dimmed(self, k):
        """(map, S-tinted map) at brightness k, cached."""
        k = round(float(k), 2)
        if k >= 1:
            return self.img, self.tint
        hit = self.__dict__.setdefault("_dim", {}).get(k)
        if hit is None:
            hit = self._dim[k] = ((self.img * k).astype(np.uint8), (self.tint * k).astype(np.uint8))
        return hit

    def dmin_row(self, ev):
        self.dist(ev)
        return self._dmin[0]

    def dmin_col(self, ev):
        self.dist(ev)
        return self._dmin[1]

    def dist(self, ev):
        """Epicentral distance (km) of every pixel, cached per event."""
        key = (ev.get("id"), ev["lat"], ev["lon"])
        if self.dist_key != key:
            la, lo = np.radians(self.lat), np.radians(self.lon)
            p1, l1 = math.radians(ev["lat"]), math.radians(ev["lon"])
            a = np.sin((la - p1) / 2) ** 2 + np.cos(la) * math.cos(p1) * np.sin((lo - l1) / 2) ** 2
            self.d = (6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))).astype(np.float32)
            self._dmin = (self.d.min(axis=1), self.d.min(axis=0))
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
        self.max_mag = 0.0                     # highest magnitude across this event's EEW updates
        self.serials = []                      # [(serial, magnitude, max class)] — one per EEW update
        self.rep = None                        # nearest station to the epicentre that is reporting
        self.rep_hist = deque(maxlen=180)      # (t, intensity) readings from that station
        self._rep_order = (None, None)
        self._maps, self._map_checked = {}, {}
        self._bags = {}
        self._theme_name = self.settings.get("theme") or "kmoni"
        self._obs_draw = {}
        self._pix = {}
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
        for k in ("event", "obs", "obs_t", "hist", "series", "log", "max_mag", "rep", "rep_hist", "serials"):
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
            self.max_mag = 0.0
            self.rep = None
            self.rep_hist.clear()
            self.serials = []
        self.max_mag = max(self.max_mag, float(ev.get("mag") or 0))
        upd = (ev.get("serial") or len(self.serials) + 1, float(ev.get("mag") or 0), ev.get("max") or "0")
        if not self.serials or self.serials[-1][0] != upd[0]:
            self.serials.append(upd)
        else:
            self.serials[-1] = upd
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
        if self.event:                                      # the closest station with a reading → seismograph
            key = (self.event.get("id"), self.event["lat"], self.event["lon"])
            if self._rep_order[0] != key:
                pts = np.array([(p_[1], p_[2]) for p_ in kmoni.points()])
                d2 = (pts[:, 0] - self.event["lat"]) ** 2 + ((pts[:, 1] - self.event["lon"]) * math.cos(math.radians(self.event["lat"]))) ** 2
                self._rep_order = (key, np.argsort(d2))
            order = self._rep_order[1]
            live = order[obs[order] > 0] if len(obs) == len(order) else []
            if len(live):
                k = int(live[0])
                if k != self.rep:
                    self.rep = k
                    self.rep_hist.clear()
                self.rep_hist.append((t, _code_to_i(float(obs[k]))))
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
            key = (ev.get("id"), ev["lat"], ev["lon"], ev["mag"], ev["depth"], ev.get("source"))
            if getattr(self, "_city_key", None) != key:                # distances / intensities: once per event
                self._city_key, self._city_base = key, qc.city_forecast(ev, float(ev["origin"]))
            el = now - float(ev["origin"])
            p2p = ev.get("source") == "p2p"
            cs = []
            for b in self._city_base:
                if el >= b["tp"] or p2p or qc.class_rank(b["class"]) >= 4:
                    cs.append(dict(b, p_in=b["tp"] - el, s_in=b["ts"] - el, listed=True))
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
        name = (self.event or {}).get("theme") or self.settings.get("theme") or "kmoni"
        if name == "tour":
            names = list(THEMES)
            name = names[int(now // 12) % len(names)]
        self._theme_name = name if name in THEMES else "kmoni"
        return THEMES[self._theme_name]

    # -- components -----------------------------------------------------------------------------
    def _blit_cached(self, c, key, x, y, w, h, draw):
        """Components whose data only change with each reading (1 Hz) are drawn once per reading and
        pasted from a cache on the other frames."""
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        key = (key, x, y, w, h, self.obs_t, len(self.hist), self._theme_name)
        hit = self._pix.get(key[:5])
        if hit is not None and hit[0] == key:
            c.buf[y0:y1, x0:x1] = hit[1]
            return
        draw()
        self._pix[key[:5]] = (key, c.buf[y0:y1, x0:x1].copy())

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
        img, tint = mp.dimmed(dim)
        sub[:] = img[sy_, sx_]
        rmax = max(rp, rs) + ring_w
        if rs > 0 or rp > 0:                         # tint (S-crossed area) and rings: only in the fronts' bounding box
            rows = np.flatnonzero(mp.dmin_row(ev) < rmax)
            cols = np.flatnonzero(mp.dmin_col(ev) < rmax)
            if len(rows) and len(cols):
                r0, r1 = max(rows[0], sy_.start), min(rows[-1] + 1, sy_.stop)
                c0, c1 = max(cols[0], sx_.start), min(cols[-1] + 1, sx_.stop)
                if r1 > r0 and c1 > c0:
                    db = d[r0:r1, c0:c1]
                    tgt = c.buf[r0 + y0:r1 + y0, c0 + x0:c1 + x0]
                    if rs > 0:
                        m = db < rs
                        tgt[m] = tint[r0:r1, c0:c1][m]
                    self._ring_box = (db, tgt)
                else:
                    self._ring_box = None
            else:
                self._ring_box = None
        else:
            self._ring_box = None
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
        if self._ring_box is not None:
            db, tgt = self._ring_box
            for r, col in ((rp, ring_cols[0]), (rs, ring_cols[1])):
                if r > 0:
                    tgt[np.abs(db - r) < ring_w] = col
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
                if k["rank"] >= MAP_CITY_RANK and qc.class_rank(k["class"]) < qc.class_rank("5-"):
                    continue
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
        lab = f["jp_r" if h >= 40 else "jp_s"]
        lab.draw(c, x + 3, y + 2, dark, "最大震度")
        room = h - lab.line - 3                                       # below the label
        for cand in (big, "mid", "key"):                              # the biggest numeral that fits under it
            if f[cand].h <= room:
                big = cand
                break
        else:
            big = "key"
        s = _jp(cls)
        ny = max(y + lab.line + 1, y + h - f[big].h - 2)
        f[big].draw(c, x + 5, ny, dark, s[0])
        if len(s) > 1:
            sub = f["jp"] if f[big].h >= 20 else f["jp_s"]
            sub.draw(c, x + 7 + f[big].width(s[0]), ny + f[big].h - sub.h, dark, s[1:])

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

    def _layer_cached(self, c, key, x, y, w, h, draw):
        """Like _blit_cached for components drawn *over* something that moves (the map): the component is
        rendered once per key onto a sentinel colour and kept as (mask, pixels), then composited."""
        x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
        slot = (x, y, w, h)
        hit = self._pix.get(slot)
        if hit is None or hit[0] != key:
            region = c.buf[y0:y1, x0:x1]
            keep = region.copy()
            region[:] = (1, 2, 3)
            draw()
            px = region.copy()
            mask = np.any(px != (1, 2, 3), axis=2)
            region[:] = keep
            hit = self._pix[slot] = (key, mask, px)
        _, mask, px = hit
        c.buf[y0:y1, x0:x1][mask] = px[mask]

    def comp_cities(self, c, ev, now, x, y, w, h, level=0, timebar=False, eng=False):
        """The affected-city list: shindo icon, name, live reading, S countdown. As cities join it shrinks
        its rows, then splits into two columns, then scrolls smoothly through the rest."""
        cs = self.cities(ev, now)
        if not timebar and len(cs) <= 2 * (h // 9):                  # static enough to cache: no scrolling, no bars
            key = (self._theme_name, eng, level, int(now * 2) % 2, self.obs_t,
                   tuple((k["name"], k["class"], round(k["s_in"]) if k["s_in"] > 0 else None) for k in cs))
            fits = self.city_style(len(cs), w, h, level)[2]
            if fits:
                return self._layer_cached(c, key, x, y, w, h,
                                          lambda: self._comp_cities(c, ev, now, x, y, w, h, level, timebar, eng))
        self._comp_cities(c, ev, now, x, y, w, h, level, timebar, eng)

    def _comp_cities(self, c, ev, now, x, y, w, h, level=0, timebar=False, eng=False):
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

    # -- sidebar: epicentre, magnitude, Tokyo countdown, seismograph -------------------------------
    def fit_font(self, text, w, roles=("jp_mid", "jp", "jp_r", "jp_s")):
        """The largest of ``roles`` in which ``text`` fits ``w`` px."""
        f = self.fonts()
        for r in roles:
            if f[r].width(text) <= w:
                return f[r]
        return f[roles[-1]]

    def comp_epicentre(self, c, ev, x, y, w, label=True):
        """震源 and the hypocentre's name, as big as fits. Returns the y below it."""
        f = self.fonts()
        if label:
            f["jp_s"].draw(c, x, y, (150, 150, 165), "震源")
            y += f["jp_s"].line
        name = ev.get("hypocenter") or "震源不明"
        fn = self.fit_font(name, w)
        fn.draw(c, x, y, (255, 255, 255), name)
        return y + fn.line + 1

    def comp_magnitude(self, c, ev, x, y, w, h=26, tile=True):
        """M (highest so far) in a big numeral, depth and origin time, max-intensity tile on the right."""
        f = self.fonts()
        tw = 44 if tile else 0
        if tile:
            self.comp_max(c, ev, x + w - tw, y, tw, h)
        mt = f"M{max(self.max_mag, float(ev.get('mag') or 0)):.1f}"
        big = f["mid"] if f["mid"].width(mt) + 50 <= w - tw else f["key"]
        big.draw(c, x, y + (h - big.h) // 2, (255, 225, 120), mt)
        x2 = x + big.width(mt) + 5
        f["jp_r"].draw(c, x2, y, (210, 210, 220), f"深さ{ev['depth']:.0f}km")
        ot = datetime.fromtimestamp(float(ev["origin"]), JST).strftime("%H:%M:%S")
        f["small"].draw(c, x2, y + h - 8, (150, 150, 165), ot)
        return y + h + 2

    def tokyo(self, ev, now):
        s = qc.site_forecast(ev, now)["Tokyo"]
        return dict(s, jp="東京", name="Tokyo", lat=qc.SITES["Tokyo"][0], lon=qc.SITES["Tokyo"][1])

    def comp_tokyo(self, c, ev, now, x, y, w, h):
        """Tokyo: predicted intensity and the S-wave countdown (or "揺れの予想なし" when it won't be felt)."""
        f = self.fonts()
        k = self.tokyo(ev, now)
        cls = k["class"]
        col = qc.SHINDO_COLOURS[cls]
        c.FillRect(x, y, w, h, Color(*(_dim(col, 0.22) if cls != "0" else (14, 14, 22))))
        c.FillRect(x, y, 3, h, Color(*col))
        f["jp_r"].draw(c, x + 6, y + 2, (255, 255, 255), "東京")
        if cls == "0":
            f["jp_r"].draw(c, x + 6, y + h - 14, (160, 160, 170), "揺れの予想なし")
            d = f"{k['dist_km']:.0f}km"
            f["small"].draw(c, x + w - 3 - f["small"].width(d), y + 4, (150, 150, 165), d)
            return
        shindo_icon(c, x + 8 + f["jp_r"].width("東京"), y + 2, cls, f["num"], centre=False)
        v = self.live_at(k["lat"], k["lon"], now)
        colt = (255, 255, 255) if k["s_in"] > 10 or int(now * 3) % 2 else (255, 70, 70)
        t = f"{k['s_in']:.0f}" if k["s_in"] > 0 else None
        if h < 46 or w < 120:                       # compact: name + icon left, the countdown right-aligned
            big = f["mid"] if h >= 28 else f["key"]
            f["jp_s"].draw(c, x + 6, y + h - 10, (215, 215, 225), "S波まで" if t else "S波到達")
            if t:
                sec = f["jp_r"]
                rx = x + w - 3 - sec.width("秒")
                sec.draw(c, rx, y + h - 3 - sec.h, colt, "秒")
                big.draw(c, rx - 1 - big.width(t), y + (h - big.h) // 2, colt, t)
            else:
                f["jp"].draw(c, x + w - 3 - f["jp"].width("到達"), y + (h - f["jp"].h) // 2, (255, 110, 110), "到達")
            return
        lab = "S波到達まで" if t else "S波到達"
        f["jp_s"].draw(c, x + w - 3 - f["jp_s"].width(lab), y + 4, (215, 215, 225), lab)
        big = f["big"]
        if t:
            big.draw(c, x + 6, y + h - big.h - 2, colt, t)
            f["jp_r"].draw(c, x + 8 + big.width(t), y + h - 14, colt, "秒")
        else:
            f["jp"].draw(c, x + 6, y + h - 15, (255, 110, 110), "到達")
        if v is not None:
            sv = f"{v:.1f}"
            f["key"].draw(c, x + w - 3 - f["key"].width(sv), y + h - 16, kmoni.intensity_colour(v), sv)
            f["tiny"].draw(c, x + w - 3 - f["key"].width(sv) - 18, y + h - 9, (170, 170, 180), "LIVE")

    def comp_seismograph(self, c, ev, now, x, y, w, h, speed=15, label=True):
        """A scrolling seismograph for the nearest reporting station: each second's reading sets the
        envelope; the trace is drawn at ``speed`` px/s so it moves smoothly between readings."""
        f = self.fonts()
        c.FillRect(x, y, w, h, Color(6, 10, 8))
        ty = y
        if label:
            if self.rep is not None:
                pt = kmoni.points()[self.rep]
                f["jp_s"].draw(c, x + 2, y + 1, (170, 205, 170), f"最寄り観測点 {pt[5] or ''}")
                f["tiny"].draw(c, x + w - 2 - f["tiny"].width(pt[0]), y + 2, (110, 140, 110), pt[0])
            else:
                f["jp_s"].draw(c, x + 2, y + 1, (130, 150, 130), "観測点データ待ち")
            ty = y + 10
        th = y + h - ty
        mid = th // 2
        c.FillRect(x, ty + mid, w, 1, Color(25, 45, 30))
        if not self.rep_hist or th < 6:
            return
        tv = np.array([t for t, _ in self.rep_hist])
        vv = np.array([v for _, v in self.rep_hist])
        tc = now - (w - 1 - np.arange(w)) / speed
        env = np.interp(tc, tv, vv, left=-3.0, right=float(vv[-1]))
        idx = np.floor(tc * speed).astype(np.int64)
        tick = (idx % speed) == 0                              # second marks along the bottom
        c.buf[ty + th - 1, x:x + w][tick] = (40, 70, 45)
        noise = np.sin(idx * 12.9898 + 78.233) * 43758.5453
        noise = (noise - np.floor(noise)) * 2 - 1
        amp = np.clip((env + 3) / 10, 0, 1) ** 1.6 * (mid - 1) + 0.6
        yy = np.clip(np.round(mid - noise * amp).astype(int), 0, th - 1)
        prev = np.concatenate([yy[:1], yy[:-1]])
        lo, hi = np.minimum(prev, yy), np.maximum(prev, yy)
        rows = np.arange(th)[:, None]
        mask = (rows >= lo[None, :]) & (rows <= hi[None, :])
        cols = _COLOUR_LUT[np.clip(((env + 3) * 20 + 1).astype(int), 1, 255)]
        region = c.buf[ty:ty + th, x:x + w]
        region[mask] = np.broadcast_to(cols[None, :, :], (th, w, 3))[mask]
        v = float(vv[-1])
        sv = f"{v:.1f}"
        f["small"].draw(c, x + w - 2 - f["small"].width(sv), ty + 1, kmoni.intensity_colour(v), sv)

    # -- components after real EEW apps ------------------------------------------------------------
    def comp_card(self, c, ev, now, x, y, w, accent):
        """JQuake-style bulletin card: warning/forecast strip with the update number, the hypocentre,
        then three cells — 規模 (magnitude), 深さ (depth), 最大震度 (max intensity) — and the origin time."""
        f = self.fonts()
        c.FillRect(x, y, w, 14, Color(*accent))
        tag = "取消" if ev.get("cancel") else "警報" if ev.get("warn") else "予報"
        c.FillRect(x + 1, y + 1, f["jp_r"].width(tag) + 6, 12, Color(0, 0, 0))
        f["jp_r"].draw(c, x + 4, y + 1, accent, tag)
        sr = f"第{ev.get('serial') or 1}報" + (" 最終" if ev.get("final") else "")
        f["jp_r"].draw(c, x + w - 3 - f["jp_r"].width(sr), y + 1, (0, 0, 0), sr)
        y += 16
        y = self.comp_epicentre(c, ev, x + 1, y, w - 2, label=False)
        cw = (w - 4) // 3
        cells = [("規模", f"M{max(self.max_mag, float(ev.get('mag') or 0)):.1f}"), ("深さ", f"{ev['depth']:.0f}km")]
        for i, (lab, val) in enumerate(cells):
            cx = x + i * (cw + 2)
            c.FillRect(cx, y, cw, 34, Color(20, 22, 34))
            f["jp_s"].draw(c, cx + 2, y + 1, (150, 150, 170), lab)
            fn = f["mid"] if f["mid"].width(val) <= cw - 4 else f["key"]
            fn.draw(c, cx + (cw - fn.width(val)) // 2, y + 33 - fn.h, (255, 255, 255), val)
        mx = x + 2 * (cw + 2)
        self.comp_max(c, ev, mx, y, w - (mx - x), 34)
        y += 36
        o = datetime.fromtimestamp(float(ev["origin"]), JST)
        ot = f"{o.hour:02d}時{o.minute:02d}分{o.second:02d}秒"     # (strftime can't take kanji on every locale)
        f["jp_s"].draw(c, x + 1, y, (170, 170, 185), f"発生 {ot}")
        return y + f["jp_s"].line + 2

    _RING = {}

    def comp_dial(self, c, ev, now, cx, cy, r, site="Tokyo", jp="東京"):
        """EQMonitor-style countdown dial: the ring fills clockwise as the S wave approaches the site."""
        f = self.fonts()
        if r not in self._RING:
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
            dist = np.hypot(xx, yy)
            ang = (np.arctan2(xx, -yy) % (2 * np.pi)) / (2 * np.pi)
            self._RING[r] = ((dist <= r + 0.3) & (dist >= r - 4.2), ang)
        ring, ang = self._RING[r]
        k = qc.site_forecast(ev, now)[site]
        cls = k["class"]
        col = qc.SHINDO_COLOURS[cls] if cls != "0" else (90, 90, 100)
        x0, y0 = cx - r, cy - r
        box = c.buf[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1]
        if box.shape[:2] != ring.shape:
            return
        el = now - float(ev["origin"])
        ts = qc.arrival_s(k["dist_km"], ev["depth"], qc.VS)
        frac = 1.0 if cls == "0" else max(0.0, min(1.0, el / max(1.0, ts)))
        box[ring] = _dim(col, 0.25)
        box[ring & (ang <= frac)] = col if k["s_in"] > 0 or int(now * 4) % 2 else (255, 255, 255)
        if cls == "0":
            t, unit = "－", ""
        elif k["s_in"] > 0:
            t, unit = f"{k['s_in']:.0f}", "秒"
        else:
            t, unit = "到達", ""
        fn = f["mid"] if t[0].isdigit() else f["jp"]
        fn.draw(c, cx - fn.width(t) // 2, cy - fn.h // 2 - 3, (255, 255, 255), t)
        if unit:
            f["jp_s"].draw(c, cx - f["jp_s"].width(unit) // 2, cy + fn.h // 2 - 2, (210, 210, 220), unit)
        lab = f"{jp} {_jp(cls)}" if cls != "0" else jp
        f["jp_r"].draw(c, cx - f["jp_r"].width(lab) // 2, cy + r + 2, col, lab)

    def comp_meter(self, c, x, y, w, h, label=True):
        """Kiwi Monitor-style level meter: the strongest real-time intensity anywhere, with peak hold."""
        f = self.fonts()
        top = y + (10 if label else 0)
        bh = y + h - top - 8
        if label:
            f["jp_s"].draw(c, x, y, (160, 160, 175), "最大震度")
        c.FillRect(x, top, w, bh, Color(10, 10, 16))
        cur = self.hist[-1][1] if self.hist else -3.0
        peak = max((v for _, v, _ in self.hist), default=-3.0)
        lvl = int(max(0.0, min(1.0, (cur + 3) / 10)) * bh)
        bw = max(4, w - 14)
        for i in range(lvl):                               # bar coloured by level, bottom up
            v = -3 + 10 * i / max(1, bh)
            c.FillRect(x + 1, top + bh - 1 - i, bw, 1, Color(*kmoni.intensity_colour(v)))
        py = top + bh - 1 - int(max(0.0, min(1.0, (peak + 3) / 10)) * (bh - 1))
        c.FillRect(x, py, bw + 2, 1, Color(255, 255, 255))
        for lv in range(0, 8):                             # shindo scale ticks
            ty = top + bh - 1 - int((lv + 3) / 10 * (bh - 1))
            c.FillRect(x + bw + 2, ty, 2, 1, Color(120, 120, 130))
            if lv % 2 == 1 or lv == 0:
                f["tiny"].draw(c, x + bw + 5, ty - 3, (130, 130, 145), str(lv))
        sv = f"{cur:.1f}"
        f["small"].draw(c, x, y + h - 8, kmoni.intensity_colour(cur), sv)

    def comp_mag_history(self, c, x, y, w, h):
        """M推移: the magnitude in each EEW update, as a step chart (updates usually revise it upward)."""
        f = self.fonts()
        c.FillRect(x, y, w, h, Color(10, 10, 16))
        f["jp_s"].draw(c, x + 2, y + 1, (160, 160, 175), "M推移")
        pts = self.serials[-12:]
        if not pts:
            return
        mags = [m for _, m, _ in pts]
        lo, hi = min(mags) - 0.3, max(mags) + 0.3
        gx, gw, gy, gh = x + 4, w - 8, y + 11, h - 14
        step = gw / max(1, len(pts))
        prev = None
        for i, (sr, m, cls) in enumerate(pts):
            px = int(gx + i * step + step / 2)
            py = int(gy + gh - 1 - (m - lo) / max(0.1, hi - lo) * (gh - 1))
            if prev:
                c.FillRect(min(prev[0], px), prev[1], abs(px - prev[0]) + 1, 1, Color(120, 120, 150))
                c.FillRect(px, min(prev[1], py), 1, abs(py - prev[1]) + 1, Color(120, 120, 150))
            c.FillRect(px - 1, py - 1, 3, 3, Color(*qc.SHINDO_COLOURS.get(cls, (200, 200, 200))))
            prev = (px, py)
        s_ = f"第{pts[-1][0]}報 M{pts[-1][1]:.1f}"
        f["small"].draw(c, x + w - 2 - f["small"].width(s_[s_.index("M"):]), y + 1, (255, 225, 120), s_[s_.index("M"):])

    def comp_sidebar(self, c, ev, now, x, y, w, h):
        """Epicentre, magnitude, Tokyo, seismograph — stacked to fill w×h."""
        bottom = y + h
        y = self.comp_epicentre(c, ev, x, y, w)
        y = self.comp_magnitude(c, ev, x, y, w)
        rest = bottom - y
        th = 48 if rest >= 48 + 44 else 34
        self.comp_tokyo(c, ev, now, x, y, w, th)
        y += th + 3
        if bottom - y >= 20:
            self.comp_seismograph(c, ev, now, x, y, w, bottom - y)

    def comp_graph(self, c, x, y, w, h, label="最大計測震度"):
        self._blit_cached(c, ("comp_graph", label), x, y, w, h,
                          lambda: self._comp_graph(c, x, y, w, h, label))

    def _comp_graph(self, c, x, y, w, h, label="最大計測震度"):
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
        self._blit_cached(c, ("comp_histogram", label), x, y, w, h,
                          lambda: self._comp_histogram(c, x, y, w, h, label))

    def _comp_histogram(self, c, x, y, w, h, label="震度別観測点数"):
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
        self._blit_cached(c, ("comp_traces", n) + tuple(k["name"] for k in self.cities(ev, now)[:n - 1]),
                          x, y, w, h, lambda: self._comp_traces(c, ev, now, x, y, w, h, n))

    def _comp_traces(self, c, ev, now, x, y, w, h, n=3):
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
        tw = self.__dict__.setdefault("_tick_w", {})
        if (font, msg) not in tw:
            if len(tw) > 8:
                tw.clear()
            tw[(font, msg)] = max(1, f.width(msg))
        mw = tw[(font, msg)]
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
# Every theme draws the whole wall from the components above, in approved fonts at native size.
AMBER = (255, 176, 0)


def _lit(col):
    """A shindo colour lightened for lit segments (7's magenta and 6+'s crimson are too dark as LEDs)."""
    return tuple(min(255, int(v * 0.55 + 115)) for v in col)


def _divider(c, x, accent):
    c.FillRect(x, 0, 1, H, Color(*accent))


def _kmoni(s, c, ev, now, el, accent):
    """Kyoshin Monitor: the station map; beside it the bulletin — epicentre, magnitude, Tokyo, a seismograph."""
    f = s.fonts()
    mp = s.comp_map(c, ev, now)
    mw = mp.w if mp else 270
    s.comp_scale(c, 6, H - 9)
    f["tiny"].draw(c, 70, H - 9, (190, 190, 200), "SIMULATED" if ev.get("test") else "LIVE NIED")
    _divider(c, mw, accent)
    x = mw + 5
    s.comp_header(c, ev, mw + 1, 0, W - mw - 1, 16, accent)
    s.comp_sidebar(c, ev, now, x, 18, W - x - 5, H - 18 - 10)
    f["jp_s"].draw(c, x, H - 9, (130, 130, 145), f"+{el:.0f}秒  強震モニタ(防災科研)・気象庁")
    draw_border(c, "pulse" if ev.get("warn") else "chase", accent, now)


def _jquake(s, c, ev, now, el, accent):
    """After JQuake: the bulletin card (規模 / 深さ / 最大震度), a Tokyo countdown dial, an intensity meter."""
    mp = s.comp_map(c, ev, now)
    mw = mp.w if mp else 270
    s.comp_scale(c, 6, H - 9)
    c.FillRect(mw, 0, W - mw, H, Color(12, 13, 20))
    x, rw = mw + 4, W - mw - 8
    y = s.comp_card(c, ev, now, x, 3, rw, accent)
    r = min(32, (H - y - 18) // 2)
    s.comp_dial(c, ev, now, x + r + 4, y + r + 2, r)
    mx = x + 2 * r + 14
    s.comp_meter(c, mx, y, 30, H - y - 4)
    s.comp_seismograph(c, ev, now, mx + 34, y, W - 4 - (mx + 34), H - y - 4, label=False, speed=12)


def _broadcast(s, c, ev, now, el, accent):
    """TV broadcast: the full-wall map, a red title box top-left, the bulletin panel right, a crawl at the foot."""
    f = s.fonts()
    s.comp_map(c, ev, now, "japan_wide", dim=0.9)
    big = f["jp_big"]
    bh = 6 + big.line + f["jp_r"].line
    bw = max(150, big.width("緊急地震速報") + 12)
    c.FillRect(0, 0, bw, bh, Color(200, 0, 0))
    c.FillRect(0, bh, bw, 2, Color(255, 255, 255))
    big.draw(c, 6, 3, (255, 255, 255), "緊急地震速報")
    f["jp_r"].draw(c, 6, 4 + big.line, (255, 255, 255), "強い揺れに警戒" if ev.get("warn") else "揺れに注意")
    f["jp_s"].draw(c, bw - 6 - f["jp_s"].width("気象庁"), bh - 10, (255, 220, 220), "気象庁")
    s.comp_max(c, ev, 0, bh + 3, 64, 46)
    px_ = W - 130
    cb = H - 18
    _shade(c.buf[0:cb, px_:W], 2)
    y = s.comp_epicentre(c, ev, px_ + 4, 3, 122, label=False)
    y = s.comp_magnitude(c, ev, px_ + 4, y, 122, 24, tile=False)
    s.comp_tokyo(c, ev, now, px_ + 4, y, 122, 46)
    s.comp_seismograph(c, ev, now, px_ + 4, y + 49, 122, cb - 3 - (y + 49))
    s.comp_ticker(c, ev, now, 0, cb + 1, W, H - cb - 1, (0, 30, 120), (255, 255, 255), "jp_mid", 70)
    c.FillRect(0, cb, W, 1, Color(255, 255, 255))


def _segment(s, c, ev, now, el, accent):
    """Seven-segment LED countdown: seconds until the S wave reaches Tokyo, in giant lit segments."""
    f = s.fonts()
    mp = s.comp_map(c, ev, now, dim=0.6)
    mw = mp.w if mp else 270
    c.FillRect(mw, 0, W - mw, H, Color(6, 2, 2))
    _divider(c, mw, accent)
    x = mw + 8
    k = s.tokyo(ev, now)
    if k["class"] == "0":
        k = s.next_city(ev, now) or k
    lit, ghost = (255, 46, 30), (36, 8, 6)
    f["jp_r"].draw(c, x, 4, (230, 230, 240), f"{k['jp']} S波到達まで")
    shindo_icon(c, W - 8 - 20, 3, k["class"], f["num"], centre=False)
    secs = max(0, int(round(k["s_in"]))) if k.get("s_in") is not None else 0
    txt = f"{min(secs, 999):3d}"
    blink = k["s_in"] <= 5 and int(now * 4) % 2
    seven_seg(c, x, 22, txt.replace(" ", "8") if False else txt, 40, 74, 7, (255, 255, 255) if blink else lit, ghost, 8)
    f["jp_mid"].draw(c, x + 3 * 48 - 2, 22 + 74 - f["jp_mid"].h, lit, "秒")
    y = 104
    c.FillRect(x, y, W - x - 8, 1, Color(70, 20, 16))
    f["jp_s"].draw(c, x, y + 4, (190, 120, 110), "規模")
    seven_seg(c, x, y + 14, f"{max(s.max_mag, float(ev.get('mag') or 0)):.1f}", 14, 26, 3, AMBER, (30, 18, 0), 4)
    f["jp_s"].draw(c, x + 52, y + 4, (190, 120, 110), "深さ km")
    seven_seg(c, x + 52, y + 14, f"{float(ev.get('depth') or 0):3.0f}", 12, 26, 3, AMBER, (30, 18, 0), 3)
    s.comp_max(c, ev, W - 8 - 52, y + 4, 52, 36)
    y = s.comp_epicentre(c, ev, x, y + 44, W - x - 8, label=False)
    f["jp_s"].draw(c, x, H - 10, (150, 110, 100), s.title(ev))
    draw_border(c, "glow", accent, now)


def _departures(s, c, ev, now, el, accent):
    """A railway departure board: amber rows on black, one fact per row, a scrolling message line."""
    f = s.fonts()
    mp = s.comp_map(c, ev, now, dim=0.75)
    mw = mp.w if mp else 270
    x0 = mw + 2
    c.FillRect(mw, 0, W - mw, H, Color(4, 3, 0))
    jr, js = f["jp_r"], f["jp_s"]
    rh = jr.line + 6
    c.FillRect(x0, 2, W - x0 - 2, rh, Color(*AMBER))
    jr.draw(c, x0 + 4, 5, (0, 0, 0), s.title(ev))
    sr = f"第{ev.get('serial') or 1}報"
    jr.draw(c, W - 6 - jr.width(sr), 5, (0, 0, 0), sr)
    k = s.tokyo(ev, now)
    o = datetime.fromtimestamp(float(ev["origin"]), JST)
    rows = [("震源", ev.get("hypocenter") or "不明"), ("規模", f"M{max(s.max_mag, float(ev.get('mag') or 0)):.1f}"),
            ("深さ", f"{float(ev.get('depth') or 0):.0f}km"), ("最大震度", _jp(ev.get("max") or "0")),
            ("発生", f"{o.hour:02d}:{o.minute:02d}:{o.second:02d}"),
            ("東京", (f"震度{_jp(k['class'])}  {k['s_in']:.0f}秒" if k["s_in"] > 0 else f"震度{_jp(k['class'])}  到達")
             if k["class"] != "0" else "揺れの予想なし")]
    y = 4 + rh
    for i, (lab, val) in enumerate(rows):
        c.FillRect(x0, y, W - x0 - 2, rh - 1, Color(*((22, 14, 0) if i % 2 else (14, 9, 0))))
        js.draw(c, x0 + 4, y + (rh - js.h) // 2, (170, 110, 0), lab)
        colv = (255, 80, 40) if lab == "東京" and k["class"] != "0" and k["s_in"] > 0 and int(now * 2) % 2 else AMBER
        jr.draw(c, x0 + 52, y + 3, colv, val if jr.width(val) <= W - x0 - 60 else val[:9])
        y += rh
    # the Tokyo countdown in amber segments under the rows, the message line at the foot
    th = H - y - rh - 8
    if k["class"] != "0" and th >= 20:
        js.draw(c, x0 + 4, y + 4, (170, 110, 0), "東京")
        js.draw(c, x0 + 4, y + 4 + js.line, (170, 110, 0), "S波まで")
        secs = max(0, int(round(k["s_in"])))
        seven_seg(c, x0 + 52, y + 4, f"{min(secs, 999):3d}", max(10, th * 5 // 9), th - 4, max(2, th // 9), AMBER, (34, 22, 0))
    s.comp_ticker(c, ev, now, x0, H - rh - 2, W - x0 - 2, rh, (0, 0, 0), AMBER, "jp_r", 40)
    draw_border(c, "lights", AMBER, now)


def _pachinko(s, c, ev, now, el, accent):
    """Everything at once: a flashing ticker, a shaking map, a slot reel, graphs, the strongest stations,
    Tokyo, a seismograph, a katakana banner, chasing lights — and the screen flashes as the S wave hits."""
    f = s.fonts()
    k = s.next_city(ev, now)
    shake = max(0.0, min(3.0, k["intensity"] - 2.0)) if k and k["s_in"] <= 0 else 0.0
    mp = s.comp_map(c, ev, now, y0=11, shake=shake, ring_cols=((0, 255, 255), (255, 0, 120)))
    mw = mp.w if mp else 270
    s.comp_ticker(c, ev, now, 0, 0, W, 11, (255, 210, 0) if int(now * 4) % 2 else (255, 60, 0), (0, 0, 0), "jp_r", 90)
    if int(now * 3) % 2 == 0:
        txt = "ツヨイユレニケイカイ" if ev.get("warn") else "ユレニチュウイ"
        kf = f["kana"]
        c.FillRect(4, 14, kf.width(txt) + 8, kf.h + 4, Color(*accent))
        kf.draw(c, 8, 16, (255, 255, 255), txt)
    s.comp_ranking(c, 4, 34, 104, 6)
    f["arcade1"].draw(c, 70, H - 10, _rainbow(now), f"M{ev['mag']:.1f} D{ev['depth']:.0f}KM +{el:.0f}S")
    s.comp_scale(c, 6, H - 7, label=False)
    x, rw = mw + 3, W - mw - 6
    c.FillRect(mw, 11, 1, H - 11, Color(*accent))
    s.comp_header(c, ev, x, 12, rw, 13, accent, "jp_r")
    # slot reel: the max intensity spins in seven-segment digits before it lands
    c.FillRect(x, 27, 50, 40, Color(20, 0, 30))
    cls = qc.SHINDO[1 + int(now * 18) % 9] if el < 4 else (ev.get("max") or "0")
    seven_seg(c, x + 6, 31, cls[0], 18, 32, 4, _lit(qc.SHINDO_COLOURS.get(cls, (255, 255, 255))), (50, 0, 60))
    if len(cls) > 1:
        f["jp"].draw(c, x + 30, 52, (255, 255, 255), _jp(cls)[1:])
    s.comp_tokyo(c, ev, now, x + 53, 27, rw - 53, 40)
    y = s.comp_epicentre(c, ev, x, 70, rw, label=False)
    s.comp_graph(c, x, y, rw // 2 - 1, 32)
    s.comp_histogram(c, x + rw // 2 + 1, y, rw - rw // 2 - 1, 32)
    s.comp_seismograph(c, ev, now, x, y + 34, rw, H - 4 - (y + 34), speed=24)
    draw_border(c, "lights", _rainbow(now, 200), now)
    s.jackpot(c, ev, now)


def _fever(s, c, ev, now, el, accent):
    """Fever mode: the full-wall map with every panel floating on it, two tickers, rainbow everything."""
    f = s.fonts()
    k = s.next_city(ev, now)
    shake = max(0.0, min(4.0, k["intensity"] - 1.5)) if k and k["s_in"] <= 0 else 0.0
    s.comp_map(c, ev, now, "japan_wide", shake=shake, ring_cols=(_rainbow(now, 300), (255, 0, 160)))
    s.comp_ticker(c, ev, now, 0, 0, W, 13, _rainbow(now, 90), (0, 0, 0), "jp", 110)
    _shade(c.buf[14:H - 14, 0:132])
    s.comp_max(c, ev, 3, 16, 56, 44, spin=el < 5, now=now)
    s.comp_histogram(c, 62, 16, 68, 44, label="")
    s.comp_ranking(c, 3, 63, 126, 7)
    s.comp_graph(c, 3, H - 46, 126, 31, label="")
    rx = W - 128
    _shade(c.buf[14:H - 14, rx:W])
    s.comp_tokyo(c, ev, now, rx + 2, 16, 124, 46)
    y = s.comp_epicentre(c, ev, rx + 2, 65, 124, label=False)
    y = s.comp_magnitude(c, ev, rx + 2, y, 124, 24, tile=False)
    s.comp_seismograph(c, ev, now, rx + 2, y, 124, H - 16 - y, speed=30)
    if int(now * 4) % 2 == 0:
        txt = f"震度{_jp(ev.get('max') or '0')}"
        big = f["jp_big"]
        tw = big.width(txt)
        bx = (W - tw) // 2
        c.FillRect(bx - 6, 18, tw + 12, big.h + 6, Color(*_rainbow(now + 0.5, 240)))
        big.draw(c, bx, 21, (0, 0, 0), txt)
    s.comp_ticker(c, ev, now, 0, H - 13, W, 13, (0, 20, 110), (255, 255, 0), "jp_r", 70)
    draw_border(c, "lights", _rainbow(now, 400), now)
    s.jackpot(c, ev, now)


def _terminal(s, c, ev, now, el, accent):
    """Green CRT: Tokyo and nearest-station readouts, a seismograph trace and a scrolling data log."""
    f = s.fonts()
    green = (60, 255, 110)
    mp = s.comp_map(c, ev, now, dim=0.8, ring_cols=(green, (255, 255, 255)))
    mw = mp.w if mp else 270
    x = mw + 5
    t, sf = f["term"], f["small"]
    c.FillRect(mw, 0, W - mw, H, Color(0, 10, 0))
    t.draw(c, x, 2, green, "> JMA EEW FEED")
    t.draw(c, x, 2 + t.line, (255, 255, 255), f"> M{ev['mag']:.1f} D{ev['depth']:.0f}KM MAX {ev.get('max')}")
    f["jp_r"].draw(c, x, 3 + 2 * t.line, (200, 255, 200), ev.get("hypocenter") or "")
    yy = 5 + 2 * t.line + f["jp_r"].line
    k = s.tokyo(ev, now)
    ln = f"> TOKYO {k['class']:>2} " + ("--" if k["class"] == "0" else f"S-{k['s_in']:3.0f}s" if k["s_in"] > 0 else "ARRIVED")
    t.draw(c, x, yy, qc.SHINDO_COLOURS[k["class"]] if k["class"] != "0" else green, ln)
    yy += t.line
    if s.rep is not None and s.rep_hist:
        t.draw(c, x, yy, green, f"> STN {kmoni.points()[s.rep][0]} {s.rep_hist[-1][1]:4.1f}")
        yy += t.line
    s.comp_seismograph(c, ev, now, x, yy + 1, W - x - 6, 28, label=False)
    yy += 31
    for line_ in list(s.log)[-max(1, (H - yy - 4) // sf.line):]:
        sf.draw(c, x, yy, (40, 200, 80), line_[:32])
        yy += sf.line
    if int(now * 2) % 2:
        c.FillRect(x, min(yy, H - 10), 5, 7, Color(*green))
    c.buf[1::2, :] -= c.buf[1::2, :] >> 2
    draw_border(c, "scanbars", green, now)


def _gogai(s, c, ev, now, el, accent):
    """号外 (newspaper extra): sepia map, vertical (tategaki) mincho headlines, Tokyo and a seismograph."""
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
    tate(f["jp_mid"], f"{ev.get('hypocenter', '')}で地震", (240, 230, 210), 6)
    tate(f["jp_mid"], f"最大震度{_jp(ev.get('max') or '0')}", qc.SHINDO_COLOURS.get(ev.get("max") or "0", (255, 255, 255)), 6)
    tate(f["jp_s"], f"M{ev['mag']:.1f} 深さ{ev['depth']:.0f}km", (200, 190, 170), 6)
    lw = x - mw - 8
    f["jp_r"].draw(c, mw + 6, 4, (230, 200, 140), "首都への影響")
    s.comp_tokyo(c, ev, now, mw + 6, 19, lw, 46)
    f["jp_r"].draw(c, mw + 6, 70, (230, 200, 140), "震源近くの揺れ")
    s.comp_seismograph(c, ev, now, mw + 6, 85, lw, H - 90, label=False)
    draw_border(c, "double", (200, 40, 30), now)


def _seismo(s, c, ev, now, el, accent):
    """Seismograph lab: the nearest station's trace large, traces for the strongest cities, Tokyo."""
    mp = s.comp_map(c, ev, now)
    mw = mp.w if mp else 270
    x, rw = mw + 3, W - mw - 6
    s.comp_header(c, ev, mw, 0, W - mw, 15, accent, "jp_r")
    y = s.comp_epicentre(c, ev, x, 17, rw, label=False)
    s.comp_seismograph(c, ev, now, x, y, rw, 52, speed=20)
    s.comp_traces(c, ev, now, x, y + 54, rw, H - 42 - (y + 54), n=3)
    s.comp_tokyo(c, ev, now, x, H - 40, rw, 36)
    draw_border(c, "double", accent, now)


def _arcade(s, c, ev, now, el, accent):
    """Arcade cabinet: neon rings, a Retro Pixel scoreboard, a katakana banner, chasing lights."""
    f = s.fonts()
    neon = _rainbow(now)
    mp = s.comp_map(c, ev, now, ring_cols=(neon, (255, 0, 200)), shake=1.0 if int(now * 8) % 3 == 0 else 0.0)
    mw = mp.w if mp else 270
    x = mw + 6
    a, a1, kf = f["arcade"], f["arcade1"], f["kana"]
    c.FillRect(mw, 0, W - mw, H, Color(10, 0, 20))
    a.draw(c, x, 4, neon, "QUAKE ALERT")
    kf.draw(c, x + a.width("QUAKE ALERT") + 6, 3, (255, 255, 255) if int(now * 4) % 2 else (255, 0, 120), "ジシン")
    a1.draw(c, x, 22, (255, 255, 255), f"MAG   {max(s.max_mag, float(ev.get('mag') or 0)):.1f}")
    a1.draw(c, x, 32, (255, 255, 255), f"DEPTH {ev['depth']:.0f}KM")
    a1.draw(c, x, 46, (255, 255, 0), "MAX SHINDO")
    cls = ev.get("max") or "0"
    seven_seg(c, x + 100, 40, cls[0], 12, 20, 3, _lit(qc.SHINDO_COLOURS.get(cls, (255, 255, 255))), (40, 0, 60))
    if len(cls) > 1:
        a1.draw(c, x + 116, 50, (255, 255, 255), cls[1])
    k = s.tokyo(ev, now)
    a1.draw(c, x, 66, (0, 255, 255), "TOKYO")
    a1.draw(c, x + 48, 66, qc.SHINDO_COLOURS[k["class"]], "SHINDO " + k["class"])
    t = "SAFE" if k["class"] == "0" else f"{k['s_in']:.0f}" if k["s_in"] > 0 else "HIT!"
    if t.isdigit():
        seven_seg(c, x, 78, f"{int(t):3d}", 14, 24, 3, (255, 255, 255) if int(now * 6) % 2 else neon, (40, 0, 60), 4)
        a1.draw(c, x + 58, 92, (255, 255, 0), "SEC")
    else:
        a.draw(c, x, 82, (255, 255, 255) if int(now * 6) % 2 else neon, t)
    a1.draw(c, x, 108, (0, 255, 255), "NEAREST STATION")
    s.comp_seismograph(c, ev, now, x, 118, W - x - 6, H - 124, label=False, speed=25)
    draw_border(c, "lights", neon, now)


def _instruments(s, c, ev, now, el, accent):
    """Instrument panel (after EQMonitor / Kiwi Monitor): Tokyo dial, intensity meter, histogram, M updates."""
    mp = s.comp_map(c, ev, now)
    mw = mp.w if mp else 270
    s.comp_scale(c, 6, H - 9)
    x, rw = mw + 4, W - mw - 8
    s.comp_header(c, ev, mw, 0, W - mw, 15, accent, "jp_r")
    y = s.comp_epicentre(c, ev, x, 17, rw, label=False)
    y = s.comp_magnitude(c, ev, x, y, rw, 26)
    s.comp_dial(c, ev, now, x + 34, y + 36, 32)
    s.comp_meter(c, x + 74, y, 34, 84)
    s.comp_histogram(c, x + 112, y, rw - 112, 84, label="")
    s.comp_mag_history(c, x, y + 88, rw, H - 4 - (y + 88))
    draw_border(c, "notch", accent, now)


THEMES = {
    "kmoni": {"label": "Kyoshin Monitor", "hint": "Station dots and waves; epicentre, magnitude, Tokyo countdown, seismograph",
              "draw": _kmoni, "fonts": {}, "type": "Kaname 12 bold · k8x12 · Shinonome 16 bold · Misaki · Crox"},
    "jquake": {"label": "JQuake style", "hint": "Bulletin card (規模 / 深さ / 最大震度), Tokyo dial, intensity meter",
               "draw": _jquake, "fonts": {"jp": _jpf("u8-b12-b"), "jp_r": _jpf("fusion12-prop"), "jp_mid": _jpf("galmuri14"),
                                          "jp_s": _jpf("fusion8-prop")}, "type": "efont bold · Fusion Pixel · Galmuri 14"},
    "broadcast": {"label": "TV broadcast", "hint": "Full-wall map, red title box, bulletin panel, crawl",
                  "draw": _broadcast, "fonts": {"jp_big": _jpf("kappa20b"), "jp_r": _jpf("shnm12maru"), "jp_mid": _jpf("shnm16")},
                  "type": "Kappa 20 bold · Shinonome 16 · maru 12"},
    "segment": {"label": "LED countdown", "hint": "Giant seven-segment seconds until the S wave reaches Tokyo",
                "draw": _segment, "fonts": {"jp_r": _jpf("k8x12L"), "jp_mid": _jpf("shnm16b")},
                "type": "seven-segment graphics · k8x12 · Shinonome 16 bold"},
    "departures": {"label": "Departure board", "hint": "Amber railway-board rows: epicentre, magnitude, depth, Tokyo",
                   "draw": _departures, "fonts": {"jp_r": _jpf("k8x12"), "jp_s": _jpf("misaki_gothic")},
                   "type": "k8x12 · Misaki gothic"},
    "pachinko": {"label": "Pachinko", "hint": "Ticker, shaking map, slot reel, katakana banner, graphs, seismograph",
                 "draw": _pachinko, "fonts": {"jp": _jpf("shnm12marub"), "jp_r": _jpf("galmuri9"), "jp_mid": _jpf("maru16"),
                                              "jp_s": _jpf("galmurimono9")},
                 "type": "Marumoji 16 · Galmuri 9 · Ark Pixel kana · Retro Pixel"},
    "fever": {"label": "Fever mode", "hint": "Full-wall map, every panel floating on it, two tickers, rainbow",
              "draw": _fever, "fonts": {"jp": _jpf("u8-b14-b"), "jp_r": _jpf("u8-b12"), "jp_big": _jpf("ayu20"), "jp_mid": _jpf("u8-b16-b")},
              "type": "efont 12/14/16 · Ayu 20"},
    "terminal": {"label": "Data terminal", "hint": "Green CRT with Tokyo / station readouts and a live data log",
                 "draw": _terminal, "fonts": {"jp_r": _jpf("fusion10-mono"), "jp_mid": _jpf("galmurimono11")},
                 "type": "UW ttyp0 · Fusion Pixel 10 mono"},
    "gogai": {"label": "号外 extra edition", "hint": "Newspaper extra: vertical mincho headlines, Tokyo, seismograph",
              "draw": _gogai, "fonts": {"jp_big": _jpf("kappa20m"), "jp_mid": _jpf("shnm16minb"), "jp_r": _jpf("shnm12min"),
                                        "jp_s": _jpf("misaki_mincho")}, "type": "Kappa 20 · Shinonome mincho 16 bold / 12 · Misaki mincho"},
    "seismo": {"label": "Seismograph lab", "hint": "The nearest station's trace, traces at the strongest cities, Tokyo",
               "draw": _seismo, "fonts": {"jp_r": _jpf("galmurimono11"), "jp_mid": _jpf("u8-b14-b"), "jp_s": _jpf("galmurimono9")},
               "type": "Galmuri Mono · efont 14 bold"},
    "arcade": {"label": "Arcade cabinet", "hint": "Neon rings, Retro Pixel scoreboard, katakana banner, chasing lights",
               "draw": _arcade, "fonts": {}, "type": "Retro Pixel · Ark Pixel kana · seven-segment"},
    "instruments": {"label": "Instrument panel", "hint": "Tokyo countdown dial, intensity meter, histogram, M updates",
                    "draw": _instruments, "fonts": {"jp_r": _jpf("fusion10-prop"), "jp_mid": _jpf("jiskan16"), "jp_s": _jpf("fusion8-mono")},
                    "type": "Fusion Pixel 10 / 8 · Jiskan 16"},
}
