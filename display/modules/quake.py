"""Japan EEW screen: a full-wall takeover in the style of Japanese EEW viewer apps (NIED Kyoshin Monitor).

Left: a dark map of Japan (rendered by the hub, data/quake/japan.bin) with ~1,600 strong-motion
stations as dots coloured by their live intensity (NIED Kyoshin Monitor, pushed every second by the
hub — or simulated for tests), the P-wave (blue) and S-wave (red) fronts expanding at their real
speeds, the area the S wave has crossed tinted red, the epicentre, Tokyo and Yokohama.
Right: the bulletin — warning/forecast, hypocentre, magnitude, depth, maximum intensity — and
Tokyo / Yokohama with their predicted intensity and a countdown to the S wave.

The hub pushes events (POST /quake) and station readings (POST /quake_obs); the last event is
also on disk (data/quake/event.json) so a restart mid-event picks it up.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from common import kmoni
from common import quake_calc as qc
from display.gfx import Color
from display.modules.weather_alert import FI, draw_border, _dim

W, H = 448, 192
JST = timezone(timedelta(hours=9))
R = 6378137.0
# fallback (no station data): predicted intensity at prefecture capitals and a few cities
PLACES = [(43.06, 141.35), (41.77, 140.73), (43.77, 142.36), (42.98, 144.38), (42.92, 143.20), (40.82, 140.74),
          (40.51, 141.49), (39.70, 141.15), (39.64, 141.95), (38.27, 140.87), (38.43, 141.30), (39.72, 140.10),
          (38.24, 140.36), (37.75, 140.47), (37.05, 140.89), (36.34, 140.45), (36.57, 139.88), (36.39, 139.06),
          (35.86, 139.65), (35.61, 140.12), (35.73, 140.83), (35.69, 139.69), (35.45, 139.64), (37.90, 139.02),
          (37.45, 138.85), (36.70, 137.21), (36.59, 136.63), (37.39, 136.90), (36.07, 136.22), (35.66, 138.57),
          (36.65, 138.18), (35.39, 136.72), (34.98, 138.38), (34.71, 137.73), (35.18, 136.91), (34.73, 136.51),
          (35.02, 135.76), (34.69, 135.52), (34.69, 135.18), (34.23, 135.17), (35.50, 134.24), (35.47, 133.05),
          (34.66, 133.93), (34.40, 132.46), (34.19, 131.47), (34.07, 134.56), (33.84, 132.77), (33.56, 133.53),
          (33.61, 130.42), (32.74, 129.87), (32.79, 130.74), (33.24, 131.61), (31.91, 131.42), (31.56, 130.56)]
_SCALE_STRIP = np.array([kmoni.intensity_colour(-3 + i / 6.0) for i in range(60)], dtype=np.uint8)[None, :, :]
_COLOUR_LUT = np.array([(0, 0, 0)] + [kmoni.intensity_colour((c - 1) / 20.0 - 3) for c in range(1, 256)], dtype=np.uint8)


class QuakeScreen:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.event = None
        self.obs = None                 # uint8 codes per station (0 = no data), see common.kmoni.encode
        self.obs_t = 0.0
        self._map = None
        self._map_key = None
        self._fonts = None
        self._load_saved()

    def fonts(self):
        if self._fonts is None:                       # Japanese fonts are big: load on first use only
            self._fonts = {"jp": FI("alert/jp-mplus12b.bdf"), "jp_r": FI("alert/jp-k8x12.bdf"),
                           "jp_s": FI("alert/jp-misaki_gothic_2nd.bdf"),
                           "big": FI("alert/10x20.bdf", 2), "key": FI("alert/7x14B.bdf"),
                           "small": FI("alert/5x8.bdf"), "tiny": FI("alert/4x6.bdf")}
        return self._fonts

    # -- data ---------------------------------------------------------------------------------
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
        self.event = ev

    def set_obs(self, encoded, t):
        import base64
        try:
            self.obs = np.frombuffer(base64.b64decode(encoded), dtype=np.uint8).copy()
            self.obs_t = t
            self._obs_draw = None                 # recomputed on the next frame
        except Exception:
            pass

    def _load_map(self):
        if self._map is not None and time.time() - getattr(self, "_map_checked", 0) < 10:
            return self._map                              # files are checked every 10 s, not every frame
        self._map_checked = time.time()
        base = os.path.join(self.data_dir or "", "quake")
        try:
            meta = json.load(open(os.path.join(base, "japan.json")))
            key = (meta["mpp"], os.path.getmtime(os.path.join(base, "japan.bin")))
        except (OSError, ValueError, KeyError):
            return None
        if key != self._map_key:
            img = np.fromfile(os.path.join(base, "japan.bin"), dtype=np.uint8).reshape(meta["h"], meta["w"], 3)
            x0, y0, x1, y1 = meta["merc"]
            xs = x0 + (np.arange(meta["w"]) + 0.5) * meta["mpp"]
            ys = y1 - (np.arange(meta["h"]) + 0.5) * meta["mpp"]
            lon = np.degrees(xs / R)
            lat = np.degrees(2 * np.arctan(np.exp(ys / R)) - np.pi / 2)
            tint = (img.astype(np.float32) * 0.65 + np.array([120, 0, 0]) * 0.35).astype(np.uint8)
            self._map = {"img": img, "tint": tint, "meta": meta, "lon": np.broadcast_to(lon, (meta["h"], meta["w"])),
                         "lat": np.broadcast_to(lat[:, None], (meta["h"], meta["w"]))}
            pts = kmoni.points()
            sx = np.array([self._project(p[1], p[2])[0] for p in pts])
            sy = np.array([self._project(p[1], p[2])[1] for p in pts])
            self._st = (sx.astype(int), sy.astype(int))
            self._map_key = key
            self._dist_key = None
        return self._map

    def _project(self, lat, lon):
        m = self._map["meta"]
        x = math.radians(lon) * R
        y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
        return (x - m["merc"][0]) / m["mpp"], (m["merc"][3] - y) / m["mpp"]

    def _dist(self, ev):
        """Epicentral distance (km) for every map pixel, cached per event."""
        key = (ev.get("id"), ev["lat"], ev["lon"])
        if getattr(self, "_dist_key", None) != key:
            la, lo = np.radians(self._map["lat"]), np.radians(self._map["lon"])
            p1, l1 = math.radians(ev["lat"]), math.radians(ev["lon"])
            a = np.sin((la - p1) / 2) ** 2 + np.cos(la) * math.cos(p1) * np.sin((lo - l1) / 2) ** 2
            self._distmap = (6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))).astype(np.float32)
            self._dist_key = key
            self._places = [(self._project(la_, lo_), qc.shindo_class(qc.intensity_value(ev["mag"], ev["depth"],
                             qc.haversine_km(ev["lat"], ev["lon"], la_, lo_)))) for la_, lo_ in PLACES]
        return self._distmap

    def active(self):
        ev = self.event
        return bool(ev) and time.time() - float(ev.get("origin") or 0) < 600

    # -- drawing ------------------------------------------------------------------------------
    def _draw_map(self, c, ev, now, elapsed):
        mp = self._map
        mw = mp["meta"]["w"]
        d = self._dist(ev)
        rs = qc.front_radius_km(elapsed, ev["depth"], qc.VS)
        rp = qc.front_radius_km(elapsed, ev["depth"], qc.VP)
        ring_w = mp["meta"]["mpp"] / 1000.0 * math.cos(math.radians(ev["lat"])) * 1.1
        if rs > 0:                                             # the area the S wave has crossed, tinted red
            c.buf[:, :mw] = np.where((d < rs)[..., None], mp["tint"], mp["img"])
        else:
            c.buf[:, :mw] = mp["img"]
        # stations: live (or simulated) intensity, weakest first so strong shaking draws on top
        sx, sy = self._st
        obs = self.obs
        if obs is not None and len(obs) == len(sx) and now - self.obs_t < 20:
            if getattr(self, "_obs_draw", None) is None:          # once per reading: sorted, clipped, coloured
                ok = (obs > 0) & (sx >= 0) & (sx < mw - 1) & (sy >= 0) & (sy < H - 1)
                idx = np.flatnonzero(ok)
                idx = idx[np.argsort(obs[idx], kind="stable")]     # weakest first, strongest drawn last
                self._obs_draw = (sx[idx], sy[idx], _COLOUR_LUT[obs[idx]])
            xs, ys, cols = self._obs_draw
            for dy in (0, 1):
                for dx in (0, 1):
                    c.buf[ys + dy, xs + dx] = cols
        else:                                        # no readings yet: predicted intensity squares
            for (x, y), cls in self._places:
                if cls == "0" or not (0 <= x < mw and 0 <= y < H):
                    continue
                c.FillRect(int(x) - 2, int(y) - 2, 5, 5, Color(0, 0, 0))
                c.FillRect(int(x) - 1, int(y) - 1, 3, 3, Color(*qc.SHINDO_COLOURS[cls]))
        for r, col in ((rp, (70, 170, 255)), (rs, (255, 50, 40))):
            if r > 0:
                ring = (d > r - ring_w) & (d < r + ring_w)
                c.buf[:, :mw][ring] = col
        ex, ey = self._project(ev["lat"], ev["lon"])
        on = int(now * 3) % 2 == 0
        for k in range(-4, 5):
            for (xx, yy) in ((ex + k, ey + k), (ex + k, ey - k)):
                if 0 <= xx < mw and 0 <= yy < H:
                    c.buf[int(yy), int(xx)] = (255, 255, 255) if on else (255, 40, 30)
        f = self.fonts()
        for name, (la, lo), dx, dy in (("TOKYO", qc.SITES["Tokyo"], 5, -9), ("YOKOHAMA", qc.SITES["Yokohama"], -34, 3)):
            x, y = self._project(la, lo)
            c.FillRect(int(x) - 1, int(y) - 1, 3, 3, Color(255, 255, 255))
            f["tiny"].draw(c, int(x) + dx, int(y) + dy, (255, 255, 255), name)
        # Kyoshin Monitor colour scale, bottom left
        c.buf[H - 9:H - 5, 6:66] = _SCALE_STRIP
        f["tiny"].draw(c, 6, H - 16, (190, 190, 200), "-3")
        f["tiny"].draw(c, 58, H - 16, (190, 190, 200), "7")
        live = "LIVE NIED" if (obs is not None and not ev.get("test")) else ("SIMULATED" if obs is not None else "PREDICTED")
        f["tiny"].draw(c, 70, H - 10, (190, 190, 200), live)
        return mw

    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        ev = self.event
        c.FillRect(0, 0, W, H, Color(0, 0, 0))
        f = self.fonts()
        if not ev:
            f["key"].draw(c, 8, 8, (200, 200, 200), "No earthquake early warning.")
            return None
        elapsed = now - float(ev["origin"])
        warn = ev.get("warn")
        accent = (255, 30, 30) if warn else (255, 200, 0)
        if ev.get("cancel"):
            accent = (150, 150, 160)
        mw = self._draw_map(c, ev, now, elapsed) if self._load_map() else 0
        c.FillRect(mw, 0, 1, H, Color(*accent))
        # ---- bulletin panel
        x = mw + 6
        title = "緊急地震速報" + ("（警報）" if warn else "（予報）")
        if ev.get("source") == "p2p":
            title = "震源・震度情報"
        if ev.get("cancel"):
            title = "緊急地震速報 取消"
        c.FillRect(mw + 1, 0, W - mw - 1, 17, Color(*accent))
        f["jp"].draw(c, x, 2, (0, 0, 0), title)
        tag = ("TEST " if ev.get("test") else "") + (f"#{ev.get('serial')}" if ev.get("serial") else "") + (" FINAL" if ev.get("final") else "")
        f["tiny"].draw(c, W - 5 - f["tiny"].width(tag), 6, (0, 0, 0), tag)
        y = 20
        f["jp"].draw(c, x, y, (255, 255, 255), ev.get("hypocenter") or "")
        y += 15
        ot = datetime.fromtimestamp(float(ev["origin"]), JST).strftime("%H:%M:%S")
        f["jp_r"].draw(c, x, y, (185, 185, 195), f"M{ev['mag']:.1f}  深さ{ev['depth']:.0f}km  {ot}")
        y += 14
        # maximum intensity tile
        cls = ev.get("max") or "0"
        col = qc.SHINDO_COLOURS.get(cls, (128, 128, 128))
        c.FillRect(x, y, 62, 48, Color(*col))
        dark = (0, 0, 0) if cls in ("1", "3", "4", "5-", "5+") else (255, 255, 255)
        f["jp_r"].draw(c, x + 4, y + 2, dark, "最大震度")
        big = qc.SHINDO_JP.get(cls, cls)
        f["big"].draw(c, x + 6, y + 10, dark, big[0])
        if len(big) > 1:
            f["jp"].draw(c, x + 30, y + 31, dark, big[1:])
        # Tokyo / Yokohama
        sites = qc.site_forecast(ev, now)
        yy = y
        sx = x + 66
        for name, jp in (("Tokyo", "東京"), ("Yokohama", "横浜")):
            s = sites[name]
            sc = s["class"]
            scol = qc.SHINDO_COLOURS[sc]
            c.FillRect(sx, yy, W - sx - 5, 23, Color(*_dim(scol, 0.28)))
            c.FillRect(sx, yy, 3, 23, Color(*scol))
            f["jp_r"].draw(c, sx + 6, yy + 1, (255, 255, 255), jp)
            f["tiny"].draw(c, sx + 6, yy + 14, (200, 200, 210), name.upper())
            f["jp"].draw(c, sx + 42, yy + 5, scol, qc.SHINDO_JP[sc])
            if s["s_in"] > 0:
                t_txt = f"{s['s_in']:.0f}s"
                blink = (255, 255, 255) if int(now * 2) % 2 else (255, 110, 110)
                f["key"].draw(c, W - 8 - f["key"].width(t_txt), yy + 1, blink, t_txt)
                f["tiny"].draw(c, W - 8 - f["tiny"].width("S-WAVE"), yy + 16, (200, 200, 210), "S-WAVE")
            else:
                f["jp_r"].draw(c, W - 8 - f["jp_r"].width("到達"), yy + 6, (255, 120, 120), "到達")
            yy += 25
        y += 54
        # warning areas (from the JMA bulletin) or observed prefectures
        areas = [(a["name"], a["class"]) for a in ev.get("areas") or []] or \
                sorted(((k, v) for k, v in (ev.get("observed") or {}).items()), key=lambda kv: -qc.class_rank(kv[1]))
        for name, acls in areas[: max(0, (H - y - 12) // 12)]:
            c.FillRect(x, y + 2, 8, 8, Color(*qc.SHINDO_COLOURS.get(acls, (128, 128, 128))))
            f["jp_r"].draw(c, x + 12, y, (230, 230, 235), f"{name} {qc.SHINDO_JP.get(acls, acls)}")
            y += 12
        if not areas:
            f["jp_r"].draw(c, x, y + 1, (160, 160, 170), "予想震度は推計です")
            f["tiny"].draw(c, x, y + 14, (130, 130, 140), "Tokyo & Yokohama: estimates")
        f["jp_s"].draw(c, x, H - 9, (140, 140, 150), f"+{elapsed:.0f}秒  強震モニタ(防災科研)・気象庁")
        draw_border(c, "pulse" if warn else "chase", accent, now)
        return "done" if elapsed > 240 else None
