"""Time zone map: the US and Canada shaded by UTC offset, with the time in each (hub/jobs/timezones.py).

Panels: ``timezones`` (one side: map + a narrow legend of times), ``timezones_full`` (map + legend under the ticker)
and ``timezones_wall`` (map + legend on the whole wall). Canada and the US follow different rules now, so the legend
has a section per country (every offset each one uses, with its time), Canada's areas are a shade brighter and the
border is a heavy line. The hub rasterises the map once (which IANA zone each pixel
is in, plus state/province and country lines) and lists every offset the zones will have over the next year
("states"), so the Pi only colours pixels by offset and writes the times; both change exactly when clocks do.

A clock change ("change": every transition of one night, all zones) is announced ``warn_days`` before it: the screen
then also shows the map as it will be, labelled ``upcoming_label`` (zones whose rules are new this year hatched), and
switches at the change. An entry with ``as_of`` (epoch seconds, from the control panel's test buttons) shows the wall
as it would be at that moment, with the real time of day, labelled TEST.

``modules.timezones`` (all optional, DEFAULTS fill the gaps):
    title, upcoming_label, now_label, test_label   header text
    warn_days        days before a change that the upcoming map shows (0: never)
    warn_pages       both (upcoming then now) | upcoming
    page_sec         seconds per page
    time_format      12h | 24h
    names            {"PST": "Pacific", …}: zone abbreviation → the name a group of zones gets
    offset_names     {"CA:-420": "BC time", "-480": "…"}: a fixed name for an offset (minutes east of UTC) in one
                     country or both, over ``names``
    country_labels, country_short   {"CA": "CANADA", "US": "UNITED STATES"}: legend section headings (short: narrow)
    canada_boost     how much brighter Canada's areas are than the US's (1 = the same)
    places           {"America/Vancouver": "BC", …}: the short place each zone adds to its row ("" = none)
    show_places, show_utc   the legend's second line
    map_labels       auto (when there's no legend) | on | off: the time written on the map in each offset's largest area
    cities           [{name, lat, lon, on}], drawn in order where there's room (all off by default); city_times adds
                     each one's time
    insets           Alaska and Hawaii
    colors           {"-480": "#rrggbb"}: each offset's colour; fill: how bright the areas are (0.15–1)
    highlight_new    hatch zones whose offset is new this year on the upcoming map
"""

from __future__ import annotations

import json
import math
import os
import time
import zlib
from datetime import datetime, timedelta, timezone

import numpy as np

from display.gfx import Canvas, Color, graphics

FONT_TIME = "fonts/alert/7x13B.bdf"
FONT_TIME_BIG = "fonts/alert/9x15B.bdf"         # legend rows with room for it (the whole wall)
FONT_NAME = "fonts/alert/6x10.bdf"
FONT_SMALL = "fonts/alert/5x7.bdf"
FONT_MAP = "fonts/alert/6x10.bdf"

DEFAULT_CITIES = [
    {"name": "Vancouver", "lat": 49.28, "lon": -123.12, "on": False},
    {"name": "Seattle", "lat": 47.61, "lon": -122.33, "on": False},
    {"name": "Calgary", "lat": 51.05, "lon": -114.07, "on": False},
    {"name": "Edmonton", "lat": 53.55, "lon": -113.49, "on": False},
    {"name": "Regina", "lat": 50.45, "lon": -104.61, "on": False},
    {"name": "Winnipeg", "lat": 49.90, "lon": -97.14, "on": False},
    {"name": "Toronto", "lat": 43.65, "lon": -79.38, "on": False},
    {"name": "Montréal", "lat": 45.50, "lon": -73.57, "on": False},
    {"name": "Halifax", "lat": 44.65, "lon": -63.57, "on": False},
    {"name": "St. John's", "lat": 47.56, "lon": -52.71, "on": False},
    {"name": "Los Angeles", "lat": 34.05, "lon": -118.24, "on": False},
    {"name": "San Francisco", "lat": 37.77, "lon": -122.42, "on": False},
    {"name": "Phoenix", "lat": 33.45, "lon": -112.07, "on": False},
    {"name": "Denver", "lat": 39.74, "lon": -104.99, "on": False},
    {"name": "Dallas", "lat": 32.78, "lon": -96.80, "on": False},
    {"name": "Chicago", "lat": 41.88, "lon": -87.63, "on": False},
    {"name": "Atlanta", "lat": 33.75, "lon": -84.39, "on": False},
    {"name": "New York", "lat": 40.71, "lon": -74.01, "on": False},
    {"name": "Miami", "lat": 25.76, "lon": -80.19, "on": False},
    {"name": "Anchorage", "lat": 61.22, "lon": -149.90, "on": False},
    {"name": "Honolulu", "lat": 21.31, "lon": -157.86, "on": False},
]

# the short place each zone adds to its legend row; "" for zones the row's name already covers (most of the US) or
# another zone names
DEFAULT_PLACES = {
    "Pacific/Honolulu": "", "America/Adak": "Aleutians", "America/Anchorage": "", "America/Juneau": "", "America/Sitka": "",
    "America/Metlakatla": "", "America/Yakutat": "", "America/Nome": "",
    "America/Los_Angeles": "", "America/Vancouver": "BC", "America/Whitehorse": "YT", "America/Dawson": "",
    "America/Dawson_Creek": "", "America/Fort_Nelson": "", "America/Creston": "",
    "America/Phoenix": "AZ", "America/Denver": "", "America/Boise": "", "America/Edmonton": "AB",
    "America/Inuvik": "NT", "America/Cambridge_Bay": "NU", "America/Regina": "SK", "America/Swift_Current": "",
    "America/Chicago": "", "America/Indiana/Knox": "", "America/Indiana/Tell_City": "",
    "America/Menominee": "", "America/North_Dakota/Center": "", "America/North_Dakota/New_Salem": "",
    "America/North_Dakota/Beulah": "", "America/Winnipeg": "MB", "America/Rankin_Inlet": "", "America/Resolute": "",
    "America/Atikokan": "", "America/Toronto": "ON QC", "America/Iqaluit": "", "America/New_York": "",
    "America/Detroit": "", "America/Kentucky/Louisville": "", "America/Kentucky/Monticello": "",
    "America/Indiana/Indianapolis": "", "America/Indiana/Marengo": "", "America/Indiana/Petersburg": "",
    "America/Indiana/Vevay": "", "America/Indiana/Vincennes": "", "America/Indiana/Winamac": "",
    "America/Halifax": "NS PE", "America/Glace_Bay": "", "America/Moncton": "NB", "America/Goose_Bay": "Labrador",
    "America/Blanc-Sablon": "", "America/St_Johns": "NL",
}

DEFAULT_NAMES = {"HST": "Hawaii", "HDT": "Aleutian", "AKST": "Alaska", "AKDT": "Alaska", "PST": "Pacific",
                 "PDT": "Pacific", "MST": "Mountain", "MDT": "Mountain", "CST": "Central", "CDT": "Central",
                 "EST": "Eastern", "EDT": "Eastern", "AST": "Atlantic", "ADT": "Atlantic", "NST": "Nfld",
                 "NDT": "Nfld"}

DEFAULT_COLORS = {"-600": "#a77bff", "-540": "#ff78d2", "-480": "#ff5f5f", "-420": "#ff9d3a", "-360": "#f5e04a",
                  "-300": "#5fdc6e", "-240": "#3ccfe0", "-210": "#5b8cff", "-180": "#8f9bff", "-150": "#3fe0b0"}

COUNTRIES = ("CA", "US")                      # legend sections, in this order

DEFAULTS = {
    "title": "TIME ZONES", "upcoming_label": "UPCOMING", "now_label": "NOW", "test_label": "TEST",
    "country_labels": {"CA": "CANADA", "US": "UNITED STATES"}, "country_short": {"CA": "CANADA", "US": "US"},
    "warn_days": 14, "warn_pages": "both", "page_sec": 12, "time_format": "12h",
    "names": DEFAULT_NAMES, "offset_names": {}, "places": {}, "show_places": True, "show_utc": True,
    "map_labels": "auto", "cities": DEFAULT_CITIES, "city_times": False, "insets": True,
    "colors": DEFAULT_COLORS, "fill": 0.45, "canada_boost": 1.4, "highlight_new": True,
}


def settings(raw):
    """modules.timezones over DEFAULTS (names, places and colours merge key by key)."""
    s = dict(DEFAULTS)
    raw = raw if isinstance(raw, dict) else {}
    for k, v in raw.items():
        if k in ("names", "colors", "places", "offset_names", "country_labels", "country_short") and isinstance(v, dict):
            s[k] = {**DEFAULTS[k], **v}
        elif v is not None:
            s[k] = v
    s["places"] = {**DEFAULT_PLACES, **(s.get("places") or {})}
    return s


# --- projection (shared with the hub, which rasterises with the same frames) -----------------------------------------
def lcc(lon0, lat1, lat2, lat0):
    """Spherical Lambert conformal conic: (lon, lat) degrees → (x, y) in earth radii, y up."""
    p1, p2, p0 = (math.radians(a) for a in (lat1, lat2, lat0))
    t = lambda p: np.tan(np.pi / 4 + p / 2)
    n = math.log(math.cos(p1) / math.cos(p2)) / math.log(t(p2) / t(p1))
    f = math.cos(p1) * t(p1) ** n / n
    r0 = f / t(p0) ** n

    def fwd(lon, lat):
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        r = f / t(np.radians(lat)) ** n
        a = n * np.radians(lon - lon0)
        return r * np.sin(a), r0 - r * np.cos(a)
    return fwd


# main map (lon0, lat1, lat2, lat0) and the places that must fit: BC's corner at 60°N 139°W, Haida Gwaii, the
# Florida Keys, Newfoundland and the tip of Quebec (the territories show where the frame's top cuts them)
PROJ_MAIN = (-96.0, 35.0, 55.0, 45.0)
EXTENT_MAIN = [(-139.1, 60.0), (-133.2, 54.0), (-124.8, 48.4), (-124.4, 40.4), (-117.1, 32.5), (-97.1, 25.8),
               (-81.8, 24.5), (-52.6, 47.6), (-55.6, 52.0), (-64.5, 60.4), (-73.0, 62.6)]
INSETS = {   # name: projection, lon/lat clip, where in the map (fractions of its width/height: x, y, w, h)
    "alaska": {"proj": (-152.0, 55.0, 65.0, 60.0), "clip": (-172.0, 51.0, -129.9, 71.5), "box": (0.0, 0.70, 0.20, 0.30)},
    "hawaii": {"proj": (-157.0, 19.0, 22.0, 20.5), "clip": (-160.6, 18.8, -154.7, 22.4), "box": (0.205, 0.83, 0.12, 0.17)},
}
LEGEND_MIN_W = 56          # narrower than LEGEND_FULL_W: the compact legend (times only)
LEGEND_FULL_W = 128


def _extent(proj, pts):
    xs, ys = lcc(*proj)([p[0] for p in pts], [p[1] for p in pts])
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


_MAIN_BOX = _extent(PROJ_MAIN, EXTENT_MAIN)
ASPECT = (_MAIN_BOX[2] - _MAIN_BOX[0]) / (_MAIN_BOX[3] - _MAIN_BOX[1])


def layout(w, h):
    """Where the map and the legend go in a w×h zone: {"map": (x, y, w, h), "legend": (x, y, w, h) | None}."""
    mw = min(w, int(round(h * ASPECT)))
    if w - mw - 2 >= LEGEND_MIN_W:
        return {"map": (0, 0, mw, h), "legend": (mw + 2, 0, w - mw - 2, h)}
    mh = min(h, int(round(w / ASPECT)))
    return {"map": ((w - mw) // 2 if mw < w else 0, (h - mh) // 2, min(w, mw), mh), "legend": None}


def frames(mw, mh):
    """The projections that make up a mw×mh map: main first, then the insets (pixel boxes inside the map)."""
    x0, y0, x1, y1 = _MAIN_BOX
    k = min(mw / (x1 - x0), mh / (y1 - y0))
    out = [{"name": "main", "proj": list(PROJ_MAIN), "k": k, "ox": (mw - k * (x1 - x0)) / 2 - k * x0,
            "oy": (mh - k * (y1 - y0)) / 2 + k * y1, "box": None, "clip": None}]
    for name, ins in INSETS.items():
        bx, by, bw, bh = ins["box"]
        box = [int(bx * mw), int(by * mh), max(8, int(bw * mw)), max(6, int(bh * mh))]
        box[1] = min(box[1], mh - box[3])
        lo0, la0, lo1, la1 = ins["clip"]
        ex = _extent(ins["proj"], [(lo0, la0), (lo1, la0), (lo0, la1), (lo1, la1), ((lo0 + lo1) / 2, la0)])
        ki = min((box[2] - 2) / (ex[2] - ex[0]), (box[3] - 2) / (ex[3] - ex[1]))
        out.append({"name": name, "proj": list(ins["proj"]), "k": ki,
                    "ox": box[0] + (box[2] - ki * (ex[2] - ex[0])) / 2 - ki * ex[0],
                    "oy": box[1] + (box[3] - ki * (ex[3] - ex[1])) / 2 + ki * ex[3], "box": box, "clip": list(ins["clip"])})
    return out


def to_px(frame, lon, lat):
    x, y = lcc(*frame["proj"])(lon, lat)
    return frame["ox"] + frame["k"] * x, frame["oy"] - frame["k"] * y


def place(frames_, lon, lat, mw, mh):
    """Pixel (x, y) of a point on the map (an inset when it's in one), or None when it's off the map."""
    for f in frames_[1:]:
        lo0, la0, lo1, la1 = f["clip"]
        if lo0 <= lon <= lo1 and la0 <= lat <= la1:
            x, y = to_px(f, lon, lat)
            return int(round(float(x))), int(round(float(y))), f["name"]
    x, y = to_px(frames_[0], lon, lat)
    x, y = int(round(float(x))), int(round(float(y)))
    if 0 <= x < mw and 0 <= y < mh and not any(f["box"][0] <= x < f["box"][0] + f["box"][2]
                                               and f["box"][1] <= y < f["box"][1] + f["box"][3] for f in frames_[1:]):
        return x, y, "main"
    return None


# --- what the data says at a moment ---------------------------------------------------------------------------------
def state_at(data, t):
    """The state (offsets of every zone) in effect at t."""
    st = data.get("states") or []
    best = st[0] if st else None
    for s in st:
        if s["from"] <= t:
            best = s
        else:
            break
    return best


def pages_at(data, t, s):
    """[("now"|"upcoming", state, change|None), …] for a moment t: the upcoming map while a change is within
    warn_days (first, or alone with warn_pages "upcoming")."""
    now = state_at(data, t)
    days = float(s.get("warn_days") or 0)
    # a change is still coming until its night's last transition (Newfoundland goes 6.5 h before Alaska)
    nxt = next((c for c in data.get("changes") or [] if c["end"] > t), None)
    if nxt is None or days <= 0 or t < nxt["start"] - days * 86400:
        return [("now", now, None)]
    up = [("upcoming", state_at(data, nxt["end"]), nxt)]
    return up if s.get("warn_pages") == "upcoming" else up + [("now", now, nxt)]


def fmt_offset(m):
    sign = "+" if m >= 0 else "-"
    h, mm = divmod(abs(int(m)), 60)
    return f"UTC{sign}{h}" + (f":{mm:02d}" if mm else "")


def groups_of(data, state, s):
    """The offsets in a state per country (Canada first, each west to east), since the two follow different rules:
    [{cc, off, zones (indices), name, places}]."""
    zones = data.get("zones") or []
    by = {}
    for i, off in enumerate(state["offs"]):
        by.setdefault((zones[i].get("cc", "") if i < len(zones) else "", off), []).append(i)
    order = lambda k: (COUNTRIES.index(k[0]) if k[0] in COUNTRIES else len(COUNTRIES), k[1])
    out = []
    for cc, off in sorted(by, key=order):
        idx = by[(cc, off)]
        # named after its zones' abbreviations, by area (Atikokan's EST doesn't make the Central zones "Eastern")
        weight = {}
        for i in idx:
            a = state["abbrs"][i]
            weight[a] = weight.get(a, 0) + float(zones[i].get("area") or 1)
        named = s["offset_names"].get(f"{cc}:{off}") or s["offset_names"].get(str(off))
        if not named:
            seen = []
            for a in sorted(weight, key=lambda a: (-weight[a], a)):
                n = s["names"].get(a) or a
                if n not in seen:
                    seen.append(n)
            named = "/".join(seen[:2])
        places = []
        for i in idx:
            p = s["places"].get(zones[i]["id"], "")
            if p and p not in places:
                places.append(p)
        out.append({"cc": cc, "off": off, "zones": idx, "name": named, "places": places})
    return out


def _hex(c, default=(200, 200, 200)):
    try:
        c = str(c).lstrip("#")
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return default


def color_of(s, off):
    c = (s.get("colors") or {}).get(str(off))
    if c:
        return _hex(c)
    hue = ((off + 720) * 7 % 360) / 360.0                     # an offset nobody picked a colour for: a stable hue
    import colorsys
    return tuple(int(v * 255) for v in colorsys.hsv_to_rgb(hue, 0.6, 1.0))


def clock_text(t, off, fmt):
    d = datetime.fromtimestamp(t, timezone.utc) + timedelta(minutes=off)
    if fmt == "24h":
        return d.strftime("%H:%M"), ""
    return d.strftime("%I:%M").lstrip("0"), d.strftime("%p")


def change_words(c):
    """'SUN NOV 1' for a change: the night's last transition (Alaska's, at 2 am), on the Pacific date."""
    d = datetime.fromtimestamp(c["end"], timezone.utc) - timedelta(hours=9)
    return d.strftime("%a %b ").upper() + str(d.day)


# --- drawing --------------------------------------------------------------------------------------------------------
class TzMapPanel:
    def __init__(self, cfg, data_dir):
        self.s = settings((getattr(cfg, "modules", None) or {}).get("timezones"))
        self.dir = data_dir or ""
        self.data = {}
        self.maps = {}                         # "WxH" -> {"idx", "lines", "frames", "rect"}
        self._mtime = None
        self._checked = 0.0
        self._bg = {}                          # cache key -> rendered map (h, w, 3)
        self._frames = {}                      # cache key -> the whole zone, drawn (it changes once a minute)
        self._sig = json.dumps(self.s, sort_keys=True)
        self.started, self.passes = {}, {}
        self.fonts = {}

    def font(self, path):
        f = self.fonts.get(path)
        if f is None:
            f = self.fonts[path] = graphics.Font()
            f.LoadFont(path)
        return f

    def tw(self, path, s):
        f = self.font(path)
        return sum(max(0, f.CharacterWidth(ord(ch))) for ch in s)

    # data ------------------------------------------------------------------------------------------------------
    def _load(self, now):
        if now - self._checked < 2.0:
            return
        self._checked = now
        path = os.path.join(self.dir, "tzmap.json")
        try:
            mt = os.stat(path).st_mtime
        except OSError:
            return
        if mt == self._mtime:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            maps = {}
            for key, m in (data.get("maps") or {}).items():
                arrs = {}
                for kind in ("idx", "lines"):
                    fp = os.path.join(self.dir, "tzmap", m[kind])
                    raw = open(fp, "rb").read() if os.path.exists(fp) else b""
                    if zlib.crc32(raw) != m["crc"][kind]:          # not all here yet: try again on the next look
                        self._checked = now - 1.0
                        return
                    w, h = m["size"]
                    arrs[kind] = np.frombuffer(raw, np.uint8).reshape(h, w)
                maps[key] = {**m, **arrs}
            self.data, self.maps, self._mtime, self._bg = data, maps, mt, {}
        except (OSError, ValueError, KeyError) as e:
            print(f"[timezones] load: {e}")

    def _map_for(self, w, h):
        m = self.maps.get(f"{w}x{h}")
        if m is not None:
            return m
        fits = [m for m in self.maps.values() if m["zone"][0] <= w and m["zone"][1] <= h]
        return max(fits, key=lambda m: m["zone"][0] * m["zone"][1]) if fits else None

    def changing(self, days_after=0, t=None):
        """For the timechange rotation rule: a change is within warn_days, or happened under days_after days ago."""
        t = t or time.time()
        self._load(t)
        if not self.data:
            return False
        if any(k == "upcoming" for k, _, _ in pages_at(self.data, t, self.s)):
            return True
        return any(c["end"] <= t < c["end"] + float(days_after) * 86400 for c in self.data.get("changes") or [])

    # map ------------------------------------------------------------------------------------------------------
    def _background(self, m, state, new, groups):
        key = (id(m["idx"]), state["from"], tuple(sorted(new)))
        bg = self._bg.get(key)
        if bg is not None:
            return bg
        idx, lines = m["idx"], m["lines"]
        n = len(state["offs"])
        offs = np.array([0] + list(state["offs"]), np.int32)
        zones = self.data.get("zones") or []
        ccs = [""] + [zones[i].get("cc", "") if i < len(zones) else "" for i in range(n)]
        # one colour per offset; Canada a shade brighter than the US, so the two read as separate areas
        fill = min(1.0, max(0.15, float(self.s.get("fill") or 0.45)))
        ca = min(1.0, fill * float(self.s.get("canada_boost") or 1.0))
        full = np.zeros((n + 1, 3), np.float32)
        lut = np.zeros((n + 1, 3), np.uint8)
        for i, off in enumerate(state["offs"]):
            full[i + 1] = color_of(self.s, off)
            lut[i + 1] = (full[i + 1] * (ca if ccs[i + 1] == "CA" else fill)).astype(np.uint8)
        g = np.minimum(idx, n)
        img = lut[g]
        land = idx > 0
        if new and self.s.get("highlight_new", True):              # hatch the zones whose rules are new this year
            yy, xx = np.indices(idx.shape)
            hatch = np.isin(idx, [i + 1 for i in new]) & ((xx + yy) % 4 == 0)
            img[hatch] = full[g[hatch]].astype(np.uint8)
        prov = (lines & 1).astype(bool) & land
        img[prov] = (img[prov].astype(np.uint16) * 45 // 100).astype(np.uint8)
        def edges(v):
            """Land pixels whose right or lower neighbour is land with another value."""
            e = np.zeros_like(land)
            e[:, :-1] |= land[:, :-1] & land[:, 1:] & (v[:, :-1] != v[:, 1:])
            e[:-1, :] |= land[:-1, :] & land[1:, :] & (v[:-1, :] != v[1:, :])
            return e
        # offset boundaries: 1 px white
        img[edges(np.where(land, offs[g], -99999))] = (255, 255, 255)
        # the Canada/US border (from the zones' countries, so it lines up with the colours): a 2 px line with a
        # dark pixel either side
        cc = np.array([{"CA": 1, "US": 2}.get(c, 3) for c in ccs], np.int8)[g]
        b = edges(np.where(land, cc, 0))
        b2 = b.copy()
        b2[1:, :] |= b[:-1, :]
        b2[:, 1:] |= b[:, :-1]
        halo = np.zeros_like(b2)
        halo[1:, :] |= b2[:-1, :]
        halo[:-1, :] |= b2[1:, :]
        halo[:, 1:] |= b2[:, :-1]
        halo[:, :-1] |= b2[:, 1:]
        img[halo & land & ~b2] = 0
        img[b2 & land] = (235, 235, 235)
        if self.s.get("insets", True):
            img[(lines & 4).astype(bool)] = (80, 80, 80)
        else:
            for f in m["frames"][1:]:
                bx, by, bw, bh = f["box"]
                img[by:by + bh, bx:bx + bw] = 0
        if len(self._bg) > 6:
            self._bg.clear()
        self._bg[key] = img
        return img

    # drawing --------------------------------------------------------------------------------------------------
    def draw(self, c, zone, entry, transition):
        x, y, w, h = zone
        now = time.time()
        self._load(now)
        key = (entry.get("panel", "timezones"), x)
        if transition or key not in self.started:
            self.started[key], self.passes[key] = now, 0
        m = self._map_for(w, h)
        if not self.data.get("states") or m is None:
            graphics.DrawText(c, self.font(FONT_SMALL), x + 4, y + 12, Color(110, 110, 110), "Time zones: waiting for the hub...")
            return "done" if now - self.started[key] > 5 else None
        as_of = entry.get("as_of")
        t = float(as_of) if isinstance(as_of, (int, float)) and not isinstance(as_of, bool) else now
        pages = pages_at(self.data, t, self.s)
        psec = max(3.0, float(self.s.get("page_sec") or 12))
        elapsed = now - self.started[key]
        kind, state, change = pages[int(elapsed // psec) % len(pages)]
        self.render(c, zone, m, kind, state, change, now, test=t if as_of is not None else None)
        done = int(elapsed // (psec * len(pages)))
        if done > self.passes[key]:
            self.passes[key] = done
            return "done"
        return None

    def render(self, c, zone, m, kind, state, change, now, test=None):
        """Draw the zone, from a copy made earlier this minute when nothing else differs."""
        x, y, w, h = zone
        key = (m["key"], kind, state["from"], (change or {}).get("start"), int(now // 60), test, id(self.data))
        img = self._frames.get(key)
        if img is None:
            scratch = Canvas(m["zone"][0], m["zone"][1])
            self._draw(scratch, m, kind, state, change, now, test)
            img = scratch.buf
            if len(self._frames) > 4:
                self._frames.clear()
            self._frames[key] = img
        c.Blit(img, x + (w - m["zone"][0]) // 2, y + (h - m["zone"][1]) // 2)

    def _draw(self, c, m, kind, state, change, now, test):
        x = y = 0
        groups = groups_of(self.data, state, self.s)
        new = (change or {}).get("new", []) if kind == "upcoming" else []
        mx, my, mw, mh = m["rect"]
        c.Blit(self._background(m, state, new, groups), x + mx, y + my)
        lay = layout(*m["zone"])
        mode = self.s.get("map_labels", "auto")
        taken = []                               # label boxes (map pixels) the city names keep clear of
        if mode == "on" or (mode == "auto" and lay["legend"] is None):
            taken = self._map_labels(c, x + mx, y + my, m, state, groups, now)
        self._cities(c, x + mx, y + my, m, state, now, taken)
        if lay["legend"] is not None:
            lx, ly, lw, lh = lay["legend"]
            self._legend(c, x + lx, y + ly, lw, lh, groups, kind, change, now, new, test)
        else:
            self._header_on_map(c, x + mx, y + my, mw, kind, change, test)

    def _tag(self, c, x, y, text, bg, fg=Color(0, 0, 0)):
        f = self.font(FONT_SMALL)
        tw = self.tw(FONT_SMALL, text)
        c.FillRect(x, y, tw + 4, f.height + 1, bg)
        graphics.DrawText(c, f, x + 2, y + f.baseline + 1, fg, text)
        return tw + 4

    def _header_tags(self, c, x, y, w, kind, change, test):
        """UPCOMING · SUN NOV 1 / NOW / TEST tags; returns their height."""
        cx = x
        f = self.font(FONT_SMALL)
        if test is not None:
            cx += self._tag(c, cx, y, str(self.s.get("test_label") or "TEST"), Color(220, 60, 220)) + 2
        if kind == "upcoming":
            cx += self._tag(c, cx, y, f"{self.s.get('upcoming_label') or 'UPCOMING'}", Color(255, 210, 40)) + 2
            graphics.DrawText(c, f, cx, y + f.baseline + 1, Color(255, 210, 40), change_words(change))
        elif change is not None:
            cx += self._tag(c, cx, y, str(self.s.get("now_label") or "NOW"), Color(200, 200, 200)) + 2
            graphics.DrawText(c, f, cx, y + f.baseline + 1, Color(150, 150, 150), "CHANGES " + change_words(change))
        elif test is not None:
            d = datetime.fromtimestamp(test, timezone.utc) - timedelta(hours=8)
            graphics.DrawText(c, f, cx, y + f.baseline + 1, Color(220, 60, 220), d.strftime("%b ").upper() + str(d.day))
        return f.height + 2 if (test is not None or change is not None) else 0

    def _header_on_map(self, c, x, y, w, kind, change, test):
        if kind == "upcoming" or change is not None or test is not None:
            words = 12 + len(self.s.get("upcoming_label") or "") + len(self.s.get("test_label") or "")
            c.FillRect(x, y, min(w, words * 5 + 30), self.font(FONT_SMALL).height + 3, Color(0, 0, 0))
        self._header_tags(c, x + 1, y + 1, w, kind, change, test)

    def _sections(self, groups):
        """[(country label, short label, groups)] in legend order: every offset shows in each country it's in."""
        out = []
        for cc in sorted({g["cc"] for g in groups}, key=lambda k: COUNTRIES.index(k) if k in COUNTRIES else 9):
            out.append((str(self.s["country_labels"].get(cc, cc)), str(self.s["country_short"].get(cc, cc)),
                        [g for g in groups if g["cc"] == cc]))
        return out

    def _legend(self, c, x, y, w, h, groups, kind, change, now, new, test):
        if w < LEGEND_FULL_W:
            return self._legend_compact(c, x, y, w, h, groups, kind, change, now, test)
        tf, nf, sf = self.font(FONT_TIME), self.font(FONT_NAME), self.font(FONT_SMALL)
        title = str(self.s.get("title") or "")
        hy = y
        if title:
            graphics.DrawText(c, nf, x, hy + nf.baseline, Color(235, 235, 235), title)
            hy += nf.height + 1
        hy += self._header_tags(c, x, hy, w, kind, change, test)
        sections = self._sections(groups)
        if not sections:
            return
        # a column per country, each with a row per offset
        col_w = (w - 4 * (len(sections) - 1)) // len(sections)
        head_h = sf.height + 3
        avail = y + h - hy - head_h
        rows = max(len(gs) for _, _, gs in sections)
        row_h = min(44, avail // rows)
        second = bool(self.s.get("show_places", True) or self.s.get("show_utc", True))
        big = self.font(FONT_TIME_BIG)
        if row_h >= big.height + nf.height + (sf.height if second else 0) + 3:
            mode = "tall"                                     # big clock, the name on a line of its own
        elif second and row_h >= tf.height + sf.height + 2:
            mode = "two"
        else:
            mode = "one"                                      # no room for the offset and places
        newset = set(new)
        for si, (label, short, gs) in enumerate(sections):
            cx = x + si * (col_w + 4)
            text = label if self.tw(FONT_SMALL, label) <= col_w else short
            graphics.DrawText(c, sf, cx, hy + sf.baseline, Color(235, 235, 235), text)
            c.FillRect(cx, hy + sf.height + 1, col_w, 1, Color(90, 90, 90))
            for i, g in enumerate(gs):
                self._row(c, cx, hy + head_h + i * row_h, col_w, row_h, g, mode, now, newset)

    def _row(self, c, cx, cy, col_w, row_h, g, mode, now, newset):
        nf, sf = self.font(FONT_NAME), self.font(FONT_SMALL)
        time_font = FONT_TIME_BIG if mode == "tall" else FONT_TIME
        tf = self.font(time_font)
        used = tf.height + (nf.height if mode == "tall" else 0) + (sf.height + 1 if mode != "one" else 0)
        rgb = color_of(self.s, g["off"])
        col = Color(*rgb)
        c.FillRect(cx, cy, 3, row_h - 2, col)
        cy += max(0, (row_h - used - 2) // 2)
        tt, ap = clock_text(now, g["off"], self.s.get("time_format", "12h"))
        tx = cx + 6
        graphics.DrawText(c, tf, tx, cy + tf.baseline, Color(255, 255, 255), tt)
        ex = tx + self.tw(time_font, tt) + 1
        if ap:
            graphics.DrawText(c, sf, ex, cy + tf.baseline, Color(170, 170, 170), ap)
            ex += self.tw(FONT_SMALL, ap) + 3
        else:
            ex += 3
        name_y = cy + tf.baseline
        if mode == "tall":
            ex, name_y = tx, cy + tf.height + nf.baseline
        name = g["name"]
        if "/" in name and ex + self.tw(FONT_NAME, name) > cx + col_w:
            name = name.split("/")[0]
        while name and ex + self.tw(FONT_NAME, name) > cx + col_w:
            name = name[:-1]
        graphics.DrawText(c, nf, ex, name_y, col, name)
        if mode == "one":
            return
        ly = cy + tf.height + (nf.height if mode == "tall" else 0) + sf.baseline
        lx = tx
        zones = self.data.get("zones") or []
        bits = []
        if self.s.get("show_utc", True):
            bits.append((fmt_offset(g["off"]), Color(150, 150, 150)))
        if self.s.get("show_places", True):
            fresh = {self.s["places"].get(zones[z]["id"], "") for z in g["zones"] if z in newset}
            bits += [(p, Color(*rgb) if p in fresh else Color(185, 185, 185)) for p in g["places"]]
        for j, (text, colr) in enumerate(bits):
            tw = self.tw(FONT_SMALL, text)
            if lx + tw > cx + col_w:
                break
            graphics.DrawText(c, sf, lx, ly, colr, text)
            lx += tw + (6 if j == 0 and self.s.get("show_utc", True) else 4)

    def _legend_compact(self, c, x, y, w, h, groups, kind, change, now, test):
        """A narrow strip beside the map (one side): each country's offsets as a colour mark and the time."""
        sf = self.font(FONT_SMALL)
        line = sf.height + 1
        hy = y
        if test is not None:
            self._tag(c, x, hy, str(self.s.get("test_label") or "TEST"), Color(220, 60, 220))
            hy += line + 1
        if kind == "upcoming":
            self._tag(c, x, hy, str(self.s.get("upcoming_label") or "UPCOMING"), Color(255, 210, 40))
            graphics.DrawText(c, sf, x, hy + line + 1 + sf.baseline, Color(255, 210, 40), change_words(change))
            hy += 2 * line + 2
        elif change is not None:
            self._tag(c, x, hy, str(self.s.get("now_label") or "NOW"), Color(200, 200, 200))
            hy += line + 1
        sections = self._sections(groups)
        rows = sum(len(gs) + 1 for _, _, gs in sections)
        step = max(line, min(line + 3, (y + h - hy) // max(1, rows)))
        for label, short, gs in sections:
            graphics.DrawText(c, sf, x, hy + sf.baseline, Color(235, 235, 235), short)
            c.FillRect(x, hy + sf.height, w, 1, Color(90, 90, 90))
            hy += step
            for g in gs:
                if hy + sf.height > y + h:
                    return
                rgb = color_of(self.s, g["off"])
                c.FillRect(x, hy, 3, sf.height - 1, Color(*rgb))
                tt = clock_text(now, g["off"], self.s.get("time_format", "12h"))[0]
                graphics.DrawText(c, sf, x + 5, hy + sf.baseline, Color(255, 255, 255), tt)
                off = fmt_offset(g["off"])[3:]
                ox = x + w - self.tw(FONT_SMALL, off)
                if ox > x + 5 + self.tw(FONT_SMALL, tt) + 2:
                    graphics.DrawText(c, sf, ox, hy + sf.baseline, Color(*rgb), off)
                hy += step

    def _map_labels(self, c, x, y, m, state, groups, now):
        f = self.font(FONT_MAP)
        anchors = (state.get("anchors") or {}).get(m["key"]) or []
        small = self.font(FONT_SMALL)
        boxes = []
        hidden = [f2["box"] for f2 in m["frames"][1:]] if not self.s.get("insets", True) else []
        for _cc, off, ax, ay, room in anchors:
            if any(bx <= ax < bx + bw and by <= ay < by + bh for bx, by, bw, bh in hidden):
                continue
            text = clock_text(now, off, self.s.get("time_format", "12h"))[0]
            # room is the distance to the area's edge, x counted at 0.55 (hub/timezones.py anchors): a label may
            # overlap the edge a little
            for path, font in ((FONT_MAP, f), (FONT_SMALL, small)):
                tw = self.tw(path, text)
                if room >= max(font.height * 0.4, tw * 0.22):
                    by = ay + font.baseline // 2 - 1
                    _outlined(c, font, x + ax - tw // 2, y + by, Color(255, 255, 255), text)
                    boxes.append((ax - tw // 2 - 1, by - font.baseline - 1, tw + 2, font.height + 2))
                    break
        return boxes

    def _cities(self, c, x, y, m, state, now, taken=()):
        cities = [cy for cy in (self.s.get("cities") or []) if cy.get("on")]
        if not cities:
            return
        f = self.font(FONT_SMALL)
        mx, my, mw, mh = m["rect"]
        taken = list(taken) + ([tuple(f2["box"]) for f2 in m["frames"][1:]] if not self.s.get("insets", True) else [])
        idx = m["idx"]
        zones_n = len(state["offs"])
        for cy in cities:
            try:
                p = place(m["frames"], float(cy["lon"]), float(cy["lat"]), mw, mh)
            except (KeyError, TypeError, ValueError):
                continue
            if p is None or (p[2] != "main" and not self.s.get("insets", True)):
                continue
            px, py, _ = p
            text = str(cy.get("name") or "")
            if self.s.get("city_times"):
                z = int(idx[min(max(py, 0), mh - 1), min(max(px, 0), mw - 1)])
                if 0 < z <= zones_n:
                    text += " " + clock_text(now, state["offs"][z - 1], self.s.get("time_format", "12h"))[0]
            tw = self.tw(FONT_SMALL, text)
            spot = None
            for lx in (px + 3, px - 3 - tw):
                box = (lx - 1, py - f.baseline // 2 - 1, tw + 2, f.height + 1)
                if box[0] >= 0 and box[0] + box[2] <= mw and box[1] >= 0 and box[1] + box[3] <= mh \
                        and not any(_hit(box, b) for b in taken):
                    spot = box
                    break
            dot = (px - 1, py - 1, 3, 3)
            if any(_hit(dot, b) for b in taken):
                continue
            c.FillRect(x + px - 1, y + py - 1, 3, 3, Color(0, 0, 0))
            c.SetPixel(x + px, y + py, 255, 255, 255)
            taken.append(dot)
            if spot:
                _outlined(c, f, x + spot[0] + 1, y + spot[1] + 1 + f.baseline, Color(255, 255, 255), text)
                taken.append(spot)


def _hit(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _outlined(c, font, x, y, color, s):
    for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)):
        graphics.DrawText(c, font, x + dx, y + dy, Color(0, 0, 0), s)
    return graphics.DrawText(c, font, x, y, color, s)


def render_preview(data_dir, size, settings_raw=None, as_of=None, page=0, now=None):
    """The wall's pixels for one zone (control panel preview): page ``page`` of what shows at ``as_of``."""

    class _Cfg:
        modules = {"timezones": settings_raw or {}}

    w, h = size
    c = Canvas(w, h)
    p = TzMapPanel(_Cfg(), data_dir)
    p._load(time.time())
    m = p._map_for(w, h)
    now = now or time.time()
    if not p.data.get("states") or m is None:
        graphics.DrawText(c, p.font(FONT_SMALL), 4, 12, Color(110, 110, 110), "Time zones: waiting for the hub...")
        return c.buf.copy()
    t = as_of if as_of is not None else now
    pages = pages_at(p.data, t, p.s)
    kind, state, change = pages[page % len(pages)]
    p.render(c, (0, 0, w, h), m, kind, state, change, now, test=as_of)
    return c.buf.copy()
