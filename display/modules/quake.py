"""Japan EEW screen: a full-wall takeover in the style of Japanese EEW viewer apps.

Left: a dark map of Japan (rendered by the hub, data/quake/japan.bin) with the epicentre, the
P-wave (blue) and S-wave (red) fronts expanding at their real speeds, the area the S wave has
already crossed tinted red, and an estimated intensity square per prefecture (JMA colours).
Right: the bulletin — warning/forecast, hypocentre, magnitude, depth, maximum intensity — and
Tokyo / Yokohama with their predicted intensity and a countdown to the S wave.

The hub pushes events (POST /quake on the control server); the last one is also on disk
(data/quake/event.json) so a restart mid-event picks it up.
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from common import quake_calc as qc
from display.gfx import Canvas, Color
from display.modules.weather_alert import FI, draw_border, _dim

W, H = 448, 192
JST = timezone(timedelta(hours=9))
R = 6378137.0
# prefecture capitals and a few more cities: the estimated-intensity squares on the map
PLACES = [(43.06, 141.35), (41.77, 140.73), (43.77, 142.36), (42.98, 144.38), (42.92, 143.20), (44.02, 144.27),
          (40.82, 140.74), (40.51, 141.49), (39.70, 141.15), (39.64, 141.95), (39.08, 141.71), (38.27, 140.87),
          (38.43, 141.30), (39.72, 140.10), (38.24, 140.36), (37.75, 140.47), (37.05, 140.89), (36.34, 140.45),
          (36.08, 140.11), (36.57, 139.88), (36.39, 139.06), (35.86, 139.65), (35.61, 140.12), (35.73, 140.83),
          (34.99, 139.87), (35.69, 139.69), (35.66, 139.32), (35.45, 139.64), (35.28, 139.67), (35.26, 139.15),
          (37.90, 139.02), (37.45, 138.85), (37.15, 138.24), (36.70, 137.21), (36.59, 136.63), (37.39, 136.90),
          (36.07, 136.22), (35.66, 138.57), (36.65, 138.18), (36.24, 137.97), (35.39, 136.72), (36.14, 137.25),
          (34.98, 138.38), (35.10, 138.86), (34.71, 137.73), (35.18, 136.91), (34.73, 136.51), (35.00, 135.87),
          (35.02, 135.76), (34.69, 135.52), (34.69, 135.18), (34.69, 135.83), (34.23, 135.17), (35.50, 134.24),
          (35.47, 133.05), (34.66, 133.93), (34.40, 132.46), (34.19, 131.47), (34.07, 134.56), (34.34, 134.04),
          (33.84, 132.77), (33.56, 133.53), (33.61, 130.42), (33.25, 130.30), (32.74, 129.87), (32.79, 130.74),
          (33.24, 131.61), (31.91, 131.42), (31.56, 130.56)]
PREFS = {"北海道": (43.06, 141.35), "青森県": (40.82, 140.74), "岩手県": (39.70, 141.15), "宮城県": (38.27, 140.87),
         "秋田県": (39.72, 140.10), "山形県": (38.24, 140.36), "福島県": (37.75, 140.47), "茨城県": (36.34, 140.45),
         "栃木県": (36.57, 139.88), "群馬県": (36.39, 139.06), "埼玉県": (35.86, 139.65), "千葉県": (35.61, 140.12),
         "東京都": (35.69, 139.69), "神奈川県": (35.45, 139.64), "新潟県": (37.90, 139.02), "富山県": (36.70, 137.21),
         "石川県": (36.59, 136.63), "福井県": (36.07, 136.22), "山梨県": (35.66, 138.57), "長野県": (36.65, 138.18),
         "岐阜県": (35.39, 136.72), "静岡県": (34.98, 138.38), "愛知県": (35.18, 136.91), "三重県": (34.73, 136.51),
         "滋賀県": (35.00, 135.87), "京都府": (35.02, 135.76), "大阪府": (34.69, 135.52), "兵庫県": (34.69, 135.18),
         "奈良県": (34.69, 135.83), "和歌山県": (34.23, 135.17), "鳥取県": (35.50, 134.24), "島根県": (35.47, 133.05),
         "岡山県": (34.66, 133.93), "広島県": (34.40, 132.46), "山口県": (34.19, 131.47), "徳島県": (34.07, 134.56),
         "香川県": (34.34, 134.04), "愛媛県": (33.84, 132.77), "高知県": (33.56, 133.53), "福岡県": (33.61, 130.42),
         "佐賀県": (33.25, 130.30), "長崎県": (32.74, 129.87), "熊本県": (32.79, 130.74), "大分県": (33.24, 131.61),
         "宮崎県": (31.91, 131.42), "鹿児島県": (31.56, 130.56)}


class QuakeScreen:
    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.event = None
        self._map = None
        self._map_key = None
        self.f_jp = FI("mplus_j12b.bdf")
        self.f_jp_r = FI("mplus_j10r.bdf")
        self.f_big = FI("alert/10x20.bdf", 2)
        self.f_key = FI("alert/7x14B.bdf")
        self.f_small = FI("alert/5x8.bdf")
        self.f_tiny = FI("alert/4x6.bdf")
        self._load_saved()

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
        self.event = ev

    def _load_map(self):
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
            self._map = {"img": img, "meta": meta, "lon": np.broadcast_to(lon, (meta["h"], meta["w"])),
                         "lat": np.broadcast_to(lat[:, None], (meta["h"], meta["w"]))}
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
            self._distmap = 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
            self._dist_key = key
            self._places = [(self._project(la_, lo_), qc.shindo_class(qc.intensity_value(ev["mag"], ev["depth"],
                             qc.haversine_km(ev["lat"], ev["lon"], la_, lo_)))) for la_, lo_ in PLACES]
        return self._distmap

    def active(self):
        ev = self.event
        return bool(ev) and time.time() - float(ev.get("origin") or 0) < 600

    # -- drawing ------------------------------------------------------------------------------
    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        ev = self.event
        c.FillRect(0, 0, W, H, Color(0, 0, 0))
        if not ev:
            self.f_key.draw(c, 8, 8, (200, 200, 200), "No earthquake early warning.")
            return None
        mp = self._load_map()
        elapsed = now - float(ev["origin"])
        warn = ev.get("warn")
        accent = (255, 30, 30) if warn else (255, 200, 0)
        if ev.get("cancel"):
            accent = (150, 150, 160)
        mw = 0
        if mp:
            img = mp["img"].astype(np.int16)
            d = self._dist(ev)
            rs = qc.front_radius_km(elapsed, ev["depth"], qc.VS)
            rp = qc.front_radius_km(elapsed, ev["depth"], qc.VP)
            kmpp = mp["meta"]["mpp"] / 1000.0 * math.cos(math.radians(ev["lat"])) * 1.1   # ~1 px wide fronts
            if rs > 0:
                inside = d < rs
                img[inside] = (img[inside] * 0.6 + np.array([110, 0, 0]) * 0.4).astype(np.int16)
            for r, col in ((rp, (60, 170, 255)), (rs, (255, 40, 30))):
                if r > 0:
                    ring = np.abs(d - r) < kmpp
                    img[ring] = col
            mw = mp["meta"]["w"]
            c.buf[:, :mw] = img.clip(0, 255).astype(np.uint8)
            # estimated intensity per place (colour squares, like the apps)
            for (x, y), cls in self._places:
                if cls in ("0",) or not (0 <= x < mw and 0 <= y < H):
                    continue
                col = qc.SHINDO_COLOURS[cls]
                c.FillRect(int(x) - 2, int(y) - 2, 5, 5, Color(0, 0, 0))
                c.FillRect(int(x) - 1, int(y) - 1, 3, 3, Color(*col))
            for name, cls in (ev.get("observed") or {}).items():          # observed intensities (after the quake)
                if name not in PREFS:
                    continue
                x, y = self._project(*PREFS[name])
                c.FillRect(int(x) - 3, int(y) - 3, 7, 7, Color(255, 255, 255))
                c.FillRect(int(x) - 2, int(y) - 2, 5, 5, Color(*qc.SHINDO_COLOURS.get(cls, (128, 128, 128))))
            # epicentre ✕ (blinking) and Tokyo / Yokohama
            ex, ey = self._project(ev["lat"], ev["lon"])
            on = int(now * 3) % 2 == 0
            for k in range(-4, 5):
                for (xx, yy) in ((ex + k, ey + k), (ex + k, ey - k)):
                    if 0 <= xx < mw and 0 <= yy < H:
                        c.buf[int(yy), int(xx)] = (255, 255, 255) if on else (255, 40, 30)
            for name, (la, lo) in qc.SITES.items():
                x, y = self._project(la, lo)
                c.FillRect(int(x) - 1, int(y) - 1, 3, 3, Color(255, 255, 255))
            tx, ty = self._project(*qc.SITES["Tokyo"])
            self.f_tiny.draw(c, int(tx) + 4, int(ty) - 8, (255, 255, 255), "TOKYO")
            yx, yy_ = self._project(*qc.SITES["Yokohama"])
            self.f_tiny.draw(c, int(yx) - 30, int(yy_) + 3, (255, 255, 255), "YOKOHAMA")
            c.FillRect(mw, 0, 1, H, Color(*accent))
        # ---- bulletin panel
        x = mw + 6
        title = "緊急地震速報" + ("（警報）" if warn else "（予報）")
        if ev.get("source") == "p2p":
            title = "震源・震度情報"
        if ev.get("cancel"):
            title = "緊急地震速報 取消"
        c.FillRect(mw + 1, 0, W - mw - 1, 18, Color(*accent))
        self.f_jp.draw(c, x, 3, (0, 0, 0), title)
        tag = ("TEST " if ev.get("test") else "") + (f"#{ev.get('serial')}" if ev.get("serial") else "") + (" FINAL" if ev.get("final") else "")
        self.f_tiny.draw(c, W - 6 - self.f_tiny.width(tag), 7, (0, 0, 0), tag)
        y = 22
        self.f_jp.draw(c, x, y, (255, 255, 255), ev.get("hypocenter") or "")
        y += 15
        ot = datetime.fromtimestamp(float(ev["origin"]), JST).strftime("%H:%M:%S JST")
        self.f_small.draw(c, x, y, (180, 180, 190), f"M{ev['mag']:.1f}  depth {ev['depth']:.0f} km  {ot}")
        y += 11
        # maximum intensity tile
        cls = ev.get("max") or "0"
        col = qc.SHINDO_COLOURS.get(cls, (128, 128, 128))
        c.FillRect(x, y, 70, 44, Color(*col))
        self.f_jp_r.draw(c, x + 4, y + 2, (0, 0, 0), "最大震度")
        big = qc.SHINDO_JP.get(cls, cls)
        num, suf = big[0], big[1:]
        self.f_big.draw(c, x + 6, y + 6, (0, 0, 0), num)
        if suf:
            self.f_jp.draw(c, x + 30, y + 26, (0, 0, 0), suf)
        # Tokyo / Yokohama
        sites = qc.site_forecast(ev, now)
        yy = y
        for name, jp in (("Tokyo", "東京"), ("Yokohama", "横浜")):
            s = sites[name]
            sc = s["class"]
            sx = x + 76
            c.FillRect(sx, yy, W - sx - 6, 21, Color(*_dim(qc.SHINDO_COLOURS[sc], 0.35)))
            c.FillRect(sx, yy, 3, 21, Color(*qc.SHINDO_COLOURS[sc]))
            self.f_jp_r.draw(c, sx + 6, yy + 1, (255, 255, 255), jp)
            self.f_tiny.draw(c, sx + 6, yy + 13, (200, 200, 210), name.upper())
            self.f_key.draw(c, sx + 40, yy + 3, qc.SHINDO_COLOURS[sc], qc.SHINDO_JP[sc])
            if s["s_in"] > 0:
                t_txt = f"S {s['s_in']:.0f}s"
                self.f_key.draw(c, W - 8 - self.f_key.width(t_txt), yy + 3, (255, 255, 255) if int(now * 2) % 2 else (255, 90, 90), t_txt)
            else:
                self.f_small.draw(c, W - 8 - self.f_small.width("ARRIVED"), yy + 6, (255, 120, 120), "ARRIVED")
            yy += 23
        y += 50
        # warning areas (from the JMA bulletin) or observed prefectures
        areas = [(a["name"], a["class"]) for a in ev.get("areas") or []] or \
                sorted(((k, v) for k, v in (ev.get("observed") or {}).items()), key=lambda kv: -qc.class_rank(kv[1]))
        for name, acls in areas[: max(0, (H - y - 12) // 11)]:
            c.FillRect(x, y + 1, 8, 8, Color(*qc.SHINDO_COLOURS.get(acls, (128, 128, 128))))
            self.f_jp_r.draw(c, x + 12, y, (230, 230, 235), f"{name} {qc.SHINDO_JP.get(acls, acls)}")
            y += 11
        if not areas:
            self.f_small.draw(c, x, y + 2, (150, 150, 160), "Predicted intensity: squares on the map")
        foot = f"+{elapsed:.0f}s since origin  ·  JMA via Wolfx / P2PQuake"
        self.f_tiny.draw(c, x, H - 8, (140, 140, 150), foot)
        draw_border(c, "pulse" if warn else "chase", accent, now)
        return "done" if elapsed > 240 else None
