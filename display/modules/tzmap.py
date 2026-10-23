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
                     country or both, over ``names`` (the panel's Clock changes card)
    legend_rows      offset (a row per UTC offset, like the map: one time per country) | traditional (a row per
                     zone as it was, e.g. Pacific: BC beside the US west coast, its places split by rule and tagged);
                     the columns are Canada and the US, so their times line up
    row_names        {"-480": "PACIFIC"}: traditional rows' names, by the zone's old standard offset
    list_hide        zones shown on the map but left out of the list (Newfoundland, Atlantic, Alaska: four rows)
    label_style      name (Mountain, from ``names``) | abbr (MST): what each time in the list is called
    rule_labels      {"dst": "DST", "fixed": "NO DST"}: traditional rows only: the tag on each time, whether its
                     places still change their clocks (a row's places that keep different rules are separate times)
    country_labels, country_short   {"CA": "CANADA", "US": "UNITED STATES"}: legend section headings (short: narrow)
    canada_boost     how much brighter Canada's areas are than the US's (1 = the same)
    places           {"America/Vancouver": "BC", …}: the short place each zone adds to its row ("" = none)
    show_places, show_utc   the legend's second line
    show_list        the list of times beside the map (off: the map fills its zone, and its labels carry the times)
    map_labels       on | off | auto (only without the list): each zone's time written in the middle of its biggest
                     area, once for each offset in Canada and once in the US (the panel's toggle sets on/off)
    map_label_hide   ["CA:-240", …]: those zone times left off the map (country and offset in minutes)
    home_zone        the zone the others are compared with (America/Vancouver)
    highlight_home   its time in the list on a box in its colour
    map_label_offsets  under each zone time on the map, how far it is from home ("+1h", "-1h", "+4:30"; home's own
                     label: "home"; the other country's at the same time: "same")
    cities           [{name, lat, lon, on}], drawn in order where there's room (all off by default)
    city_times       off | after (each city's time after its name) | below (on a line of its own under the name);
                     true/false from older configs mean after/off
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
FONT_TAG = "fonts/alert/tom-thumb.bdf"         # the list's DST / NO DST tags (narrow, so they fit beside the time)
BIG_ENTRY_H = 34                               # a roomy list time: 9x15 clock, 6x10 name, 5x7 places

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
    "America/Inuvik": "", "America/Cambridge_Bay": "", "America/Regina": "SK", "America/Swift_Current": "",
    "America/Chicago": "", "America/Indiana/Knox": "", "America/Indiana/Tell_City": "",
    "America/Menominee": "", "America/North_Dakota/Center": "", "America/North_Dakota/New_Salem": "",
    "America/North_Dakota/Beulah": "", "America/Winnipeg": "MB", "America/Rankin_Inlet": "", "America/Resolute": "",
    "America/Atikokan": "", "America/Toronto": "ON QC", "America/Iqaluit": "", "America/New_York": "",
    "America/Detroit": "", "America/Kentucky/Louisville": "", "America/Kentucky/Monticello": "",
    "America/Indiana/Indianapolis": "", "America/Indiana/Marengo": "", "America/Indiana/Petersburg": "",
    "America/Indiana/Vevay": "", "America/Indiana/Vincennes": "", "America/Indiana/Winamac": "",
    "America/Halifax": "NS PE", "America/Glace_Bay": "", "America/Moncton": "NB", "America/Goose_Bay": "",
    "America/Blanc-Sablon": "", "America/St_Johns": "NL",
}

DEFAULT_NAMES = {"HST": "Hawaii", "HDT": "Aleutian", "AKST": "Alaska", "AKDT": "Alaska", "PST": "Pacific",
                 "PDT": "Pacific", "MST": "Mountain", "MDT": "Mountain", "CST": "Central", "CDT": "Central",
                 "EST": "Eastern", "EDT": "Eastern", "AST": "Atlantic", "ADT": "Atlantic", "NST": "Nfld",
                 "NDT": "Nfld"}

DEFAULT_COLORS = {"-600": "#a77bff", "-540": "#ff78d2", "-480": "#ff5f5f", "-420": "#ff9d3a", "-360": "#f5e04a",
                  "-300": "#5fdc6e", "-240": "#3ccfe0", "-210": "#5b8cff", "-180": "#8f9bff", "-150": "#3fe0b0"}

COUNTRIES = ("CA", "US")                      # legend columns, in this order
# the legend's rows by default: the zones as they were (a zone's standard offset in 2019, hub/jobs/timezones.py)
HOME_NAMES = {"-600": "HAWAII", "-540": "ALASKA", "-480": "PACIFIC", "-420": "MOUNTAIN", "-360": "CENTRAL",
              "-300": "EASTERN", "-240": "ATLANTIC", "-210": "NFLD"}
# on the map but not in the list, so it has the four main zones: Newfoundland, Atlantic and Alaska (its corner
# of the map, in its own colour; Hawaii isn't on either: hub/timezones.py MAP_DROP)
DEFAULT_LIST_HIDE = ["America/St_Johns", "America/Halifax", "America/Glace_Bay", "America/Moncton",
                     "America/Goose_Bay", "America/Blanc-Sablon", "America/Anchorage", "America/Juneau",
                     "America/Sitka", "America/Metlakatla", "America/Yakutat", "America/Nome"]
TERRITORIES = ("YT", "NT", "NU")
NAME_SHARE = 0.15         # a second zone name for a time needs this share of its area
MIN_SHARE = 0.25          # a time in a traditional zone's cell with no place named needs this share of its area
                          # (Blanc-Sablon's and Creston's year-round time stay off the list)

DEFAULTS = {
    "title": "TIME ZONES", "upcoming_label": "UPCOMING", "now_label": "NOW", "test_label": "TEST",
    "country_labels": {"CA": "CANADA", "US": "UNITED STATES"}, "country_short": {"CA": "CA", "US": "US"},
    "legend_rows": "offset", "row_names": {}, "list_hide": DEFAULT_LIST_HIDE,
    "rule_labels": {"dst": "DST", "fixed": "NO DST"}, "label_style": "name",
    "home_zone": "America/Vancouver", "highlight_home": True, "map_label_offsets": False,
    "warn_days": 14, "warn_pages": "both", "page_sec": 12, "time_format": "12h",
    "names": DEFAULT_NAMES, "offset_names": {}, "places": {}, "show_places": True, "show_utc": True,
    "map_labels": "on", "show_list": False, "cities": DEFAULT_CITIES, "city_times": False,
    "colors": DEFAULT_COLORS, "fill": 0.45, "canada_boost": 1.4, "highlight_new": True,
}


def settings(raw):
    """modules.timezones over DEFAULTS (names, places and colours merge key by key)."""
    s = dict(DEFAULTS)
    raw = raw if isinstance(raw, dict) else {}
    for k, v in raw.items():
        if k in ("names", "colors", "places", "offset_names", "country_labels", "country_short",
                 "row_names", "rule_labels") and isinstance(v, dict):
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


# main map (lon0, lat1, lat2, lat0) and the places that must fit: Alaska's Gulf coast to Prince William Sound (the
# rest of Alaska is cut off), BC's corner at 60°N 139°W, Haida Gwaii, the
# Florida Keys, Newfoundland and the tip of Quebec (the territories show where the frame's top cuts them)
PROJ_MAIN = (-96.0, 35.0, 55.0, 45.0)
EXTENT_MAIN = [(-148.0, 60.6), (-139.1, 60.0), (-133.2, 54.0), (-124.8, 48.4), (-124.4, 40.4), (-117.1, 32.5), (-97.1, 25.8),
               (-81.8, 24.5), (-52.6, 47.6), (-55.6, 52.0), (-64.5, 60.4), (-73.0, 62.6)]
LEGEND_MIN_W = 56          # narrower than LEGEND_FULL_W: the compact legend (times only)
LEGEND_FULL_W = 128
HEADER_BESIDE_W = 95       # the map's side margin that fits the title and tags beside it


def _extent(proj, pts):
    xs, ys = lcc(*proj)([p[0] for p in pts], [p[1] for p in pts])
    return float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())


_MAIN_BOX = _extent(PROJ_MAIN, EXTENT_MAIN)
ASPECT = (_MAIN_BOX[2] - _MAIN_BOX[0]) / (_MAIN_BOX[3] - _MAIN_BOX[1])


def layout(w, h, legend=False):
    """Where the map (and, with ``legend``, the list) go in a w×h zone: {"map": (x, y, w, h), "legend": (x, y, w, h) |
    None}. The map is the same size either way (the hub rasterises it once): centred alone, or at the left with the
    list beside it when there's room."""
    mw = min(w, int(round(h * ASPECT)))
    if legend and w - mw - 2 >= LEGEND_MIN_W:
        return {"map": (0, 0, mw, h), "legend": (mw + 2, 0, w - mw - 2, h)}
    mh = h if mw < w else min(h, int(round(w / ASPECT)))
    return {"map": ((w - mw) // 2, (h - mh) // 2, mw, mh), "legend": None}


def frames(mw, mh):
    """The projection of a mw×mh map (a list: the hub stores it as the map's frames)."""
    x0, y0, x1, y1 = _MAIN_BOX
    k = min(mw / (x1 - x0), mh / (y1 - y0))
    out = [{"name": "main", "proj": list(PROJ_MAIN), "k": k, "ox": (mw - k * (x1 - x0)) / 2 - k * x0,
            "oy": (mh - k * (y1 - y0)) / 2 + k * y1}]
    return out


def to_px(frame, lon, lat):
    x, y = lcc(*frame["proj"])(lon, lat)
    return frame["ox"] + frame["k"] * x, frame["oy"] - frame["k"] * y


def place(frames_, lon, lat, mw, mh):
    """Pixel (x, y) of a point on the map, or None when it's off the map."""
    x, y = to_px(frames_[0], lon, lat)
    x, y = int(round(float(x))), int(round(float(y)))
    return (x, y) if 0 <= x < mw and 0 <= y < mh else None


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


def home_offset(data, state, s):
    """(offset, country) of the home zone in a state, or (None, None) when it isn't on the map."""
    for i, z in enumerate((data.get("zones") or [])[:len(state["offs"])]):
        if z["id"] == s.get("home_zone"):
            return state["offs"][i], z.get("cc", "")
    return None, None


def from_home(off, home, own=True):
    """How far an offset is from home: "+1h", "-1h", "+4:30"; no difference: "home" for home's own label (``own``),
    "same" for another country's."""
    d = off - home
    if d == 0:
        return "home" if own else "same"
    h, m = divmod(abs(d), 60)
    return ("+" if d > 0 else "-") + (f"{h}:{m:02d}" if m else f"{h}h")


def fmt_offset(m):
    sign = "+" if m >= 0 else "-"
    h, mm = divmod(abs(int(m)), 60)
    return f"UTC{sign}{h}" + (f":{mm:02d}" if mm else "")


def names_of(zones, idx, state, s, cc, off):
    """What zones idx (one country, one offset) are called, biggest first: from their tz abbreviations, which
    follow each country's own naming (after Nov 1, 2026 BC is MST: "Mountain", as Wikipedia's Time in Canada has
    it), or a fixed name from offset_names. label_style "abbr" gives the abbreviations themselves (MST, PDT)."""
    fixed = s["offset_names"].get(f"{cc}:{off}") or s["offset_names"].get(str(off))
    if fixed:
        return [fixed]
    # by area, so Atikokan's EST doesn't make the Central zones "Eastern"
    weight = {}
    for i in idx:
        a = state["abbrs"][i]
        weight[a] = weight.get(a, 0) + float(zones[i].get("area") or 1)
    out = []
    total = sum(weight.values()) or 1
    for a in sorted(weight, key=lambda a: (-weight[a], a)):
        if out and weight[a] < NAME_SHARE * total:     # a sliver (Atikokan's EST among Manitoba's CDT) adds no name
            break
        n = a if s.get("label_style") == "abbr" else (s["names"].get(a) or a)
        if n not in out:
            out.append(n)
    return out


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
        named = "/".join(names_of(zones, idx, state, s, cc, off)[:2])
        places = []
        for i in idx:
            p = s["places"].get(zones[i]["id"], "")
            if p and p not in places:
                places.append(p)
        out.append({"cc": cc, "off": off, "zones": idx, "name": named, "places": places})
    return out


def table_of(data, state, s):
    """The legend: rows of Canada / US cells, each a list of times ``{off, dst, zones, places}`` (west to east; a
    time per offset and rule: places that still change clocks apart from those that don't). Rows are traditional
    zones (by each zone's 2019 standard offset) or UTC offsets (``legend_rows``)."""
    zones = data.get("zones") or []
    hide = set(s.get("list_hide") or [])
    by_offset = s.get("legend_rows") == "offset"
    rows = {}
    for i, z in enumerate(zones[:len(state["offs"])]):
        if z["id"] in hide or z.get("cc", "") not in COUNTRIES:      # Mexico is on the map, not in the list
            continue
        off = state["offs"][i]
        key = off if by_offset else z.get("home", off)
        cell = rows.setdefault(key, {}).setdefault(z.get("cc", ""), {})
        dst = None if by_offset else bool(z.get("dst", True))       # by offset: one time per country, untagged
        e = cell.setdefault((off, dst), {"off": off, "dst": dst, "zones": [], "places": [], "area": 0.0})
        e["zones"].append(i)
        e["area"] += float(z.get("area") or 1)
        p = s["places"].get(z["id"], "")
        if p:
            e.setdefault("_rank", {})
            # provinces and states before territories, then the biggest first (what's cut when space runs out)
            terr = any(t in p.split() for t in TERRITORIES)
            e["_rank"][p] = min(e["_rank"].get(p, (9, 0)), (int(terr), -float(z.get("area") or 1)))
    for cells_ in rows.values():
        for entries in cells_.values():
            for e in entries.values():
                rank = e.pop("_rank", {})
                e["places"] = sorted(rank, key=lambda p: rank[p])
    out = []
    for cells_ in rows.values():
        for cc, entries in cells_.items():
            for e in entries.values():
                e["names"] = names_of(zones, e["zones"], state, s, cc, e["off"])
                e["abbrs"] = names_of(zones, e["zones"], state, dict(s, label_style="abbr"), cc, e["off"])
    for key in sorted(rows):
        cells = {}
        for cc, entries in rows[key].items():
            total = sum(e["area"] for e in entries.values())
            keep = list(entries.values()) if by_offset else [e for e in entries.values()
                                                              if e["places"] or e["area"] >= MIN_SHARE * total]
            cells[cc] = sorted(keep or list(entries.values()), key=lambda e: (e["off"], bool(e["dst"])))
        label = fmt_offset(key) if by_offset else (s["row_names"].get(str(key)) or HOME_NAMES.get(str(key))
                                                     or fmt_offset(key))
        out.append({"key": key, "label": label, "cells": cells})
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
                for kind in ("idx", "lines", "water"):
                    if kind not in m:                          # rasters from before the water layer
                        continue
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
        # the lines follow the zones over their water too (lakes, straits, territorial waters), so they carry on
        # through the Great Lakes instead of stopping at each shore
        wz = m.get("water")
        zg = np.minimum(np.where(land, idx, wz), n) if wz is not None else g
        zoned = zg > 0

        def edges(v):
            """Zoned pixels (land or water) whose right or lower neighbour is zoned with another value."""
            e = np.zeros_like(zoned)
            e[:, :-1] |= zoned[:, :-1] & zoned[:, 1:] & (v[:, :-1] != v[:, 1:])
            e[:-1, :] |= zoned[:-1, :] & zoned[1:, :] & (v[:-1, :] != v[1:, :])
            return e
        # offset boundaries: 1 px white
        img[edges(np.where(zoned, offs[zg], -99999))] = (255, 255, 255)
        # the Canada/US border (from the zones' countries, so it lines up with the colours): a 2 px line with a
        # dark pixel either side on land
        cc = np.array([{"CA": 1, "US": 2}.get(c, 3) for c in ccs], np.int8)[zg]
        b = edges(np.where(zoned, cc, 0))
        b2 = b.copy()
        b2[1:, :] |= b[:-1, :]
        b2[:, 1:] |= b[:, :-1]
        halo = np.zeros_like(b2)
        halo[1:, :] |= b2[:-1, :]
        halo[:-1, :] |= b2[1:, :]
        halo[:, 1:] |= b2[:, :-1]
        halo[:, :-1] |= b2[:, 1:]
        img[halo & land & ~b2] = 0
        img[b2 & zoned] = (235, 235, 235)
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
        lay = layout(*m["zone"], legend=bool(self.s.get("show_list")))
        mx, my = lay["map"][:2]                  # the raster's size from the hub, placed for this layout
        mw = m["rect"][2]
        c.Blit(self._background(m, state, new, groups), x + mx, y + my)
        mode = self.s.get("map_labels", "on")
        taken = []                               # label boxes (map pixels) the city names keep clear of
        if mode == "on" or (mode == "auto" and lay["legend"] is None):
            # labels may use the panel's space beside the map, but not the list's
            zw, zh = m["zone"]
            right = m["rect"][2] if lay["legend"] is not None else zw - mx
            taken = self._map_labels(c, x + mx, y + my, m, state, groups, now, bounds=(-mx, -my, right, zh - my))
        self._cities(c, x + mx, y + my, m, state, now, taken)
        if lay["legend"] is not None:
            lx, ly, lw, lh = lay["legend"]
            self._legend(c, x + lx, y + ly, lw, lh, state, kind, change, now, new, test)
        elif mx >= HEADER_BESIDE_W:                    # room beside the map (full width, whole wall): the header there
            self._header_beside(c, x + 3, y + 2, mx - 6, kind, change, test)
        else:
            self._header_on_map(c, x + mx, y + my, mw, m["rect"][3], kind, change, test)

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

    def _header_beside(self, c, x, y, w, kind, change, test):
        """The title and the UPCOMING / NOW / TEST tags, one under another, in the space beside a centred map (each
        line only if it fits, so nothing runs into the map)."""
        nf, f = self.font(FONT_NAME), self.font(FONT_SMALL)
        title = str(self.s.get("title") or "")
        if title and self.tw(FONT_NAME, title) <= w:
            graphics.DrawText(c, nf, x, y + nf.baseline, Color(235, 235, 235), title)
            y += nf.height + 2
        for kind_, text, colr in self._header_lines(kind, change, test):
            width = self.tw(FONT_SMALL, text) + (4 if kind_ == "tag" else 0)
            if width > w:
                continue
            if kind_ == "tag":
                self._tag(c, x, y, text, colr)
            else:
                graphics.DrawText(c, f, x, y + f.baseline + 1, colr, text)
            y += f.height + 2

    def _header_lines(self, kind, change, test):
        """The header as [("tag" | "text", words, colour)]: TEST, then UPCOMING + the date or NOW + CHANGES …"""
        out = []
        if test is not None:
            out.append(("tag", str(self.s.get("test_label") or "TEST"), Color(220, 60, 220)))
        if kind == "upcoming":
            out += [("tag", str(self.s.get("upcoming_label") or "UPCOMING"), Color(255, 210, 40)),
                    ("text", change_words(change), Color(255, 210, 40))]
        elif change is not None:
            out += [("tag", str(self.s.get("now_label") or "NOW"), Color(200, 200, 200)),
                    ("text", "CHANGES " + change_words(change), Color(150, 150, 150))]
        elif test is not None:
            d = datetime.fromtimestamp(test, timezone.utc) - timedelta(hours=8)
            out.append(("text", d.strftime("%b ").upper() + str(d.day), Color(220, 60, 220)))
        return out

    def _header_on_map(self, c, x, y, w, h, kind, change, test):
        """The tags on one line over the map's bottom-left corner (open Pacific, clear of Alaska's label)."""
        if kind == "upcoming" or change is not None or test is not None:
            f = self.font(FONT_SMALL)
            words = 12 + len(self.s.get("upcoming_label") or "") + len(self.s.get("test_label") or "")
            top = y + h - f.height - 3
            c.FillRect(x, top, min(w, words * 5 + 30), f.height + 3, Color(0, 0, 0))
            self._header_tags(c, x + 1, top + 1, w, kind, change, test)

    def _legend(self, c, x, y, w, h, state, kind, change, now, new, test):
        if w < LEGEND_FULL_W:
            return self._legend_compact(c, x, y, w, h, state, kind, change, now, test, set(new))
        nf, sf = self.font(FONT_NAME), self.font(FONT_SMALL)
        title = str(self.s.get("title") or "")
        hy = y
        if title:
            graphics.DrawText(c, nf, x, hy + nf.baseline, Color(235, 235, 235), title)
            hy += nf.height + 1
        hy += self._header_tags(c, x, hy, w, kind, change, test)
        table = table_of(self.data, state, self.s)
        if not table:
            return
        # columns: the row's name, then Canada and the US side by side, so each row's times line up
        lw = min(48, max(self.tw(FONT_SMALL, r["label"]) for r in table) + 4)
        cw_ca = (w - lw - 4) // 2
        widths = {"CA": cw_ca, "US": w - lw - 4 - cw_ca}
        cols = [("CA", x + lw), ("US", x + lw + cw_ca + 4)]
        for cc, cx in cols:
            cw = widths[cc]
            label = str(self.s["country_labels"].get(cc, cc))
            if self.tw(FONT_SMALL, label) > cw:
                label = str(self.s["country_short"].get(cc, cc))
            graphics.DrawText(c, sf, cx, hy + sf.baseline, Color(235, 235, 235), label)
        hy += sf.height + 2
        c.FillRect(x, hy - 1, w, 1, Color(90, 90, 90))
        need = [max([len(r["cells"].get(cc, [])) for cc in COUNTRIES] + [1]) for r in table]
        # roomy (the whole wall, four rows): a big clock with the zone's name and places under it
        big = (self.s.get("legend_rows") == "offset" and min(widths.values()) >= 80
               and (y + h - hy) >= sum(need) * (BIG_ENTRY_H + 4))
        entry_h = BIG_ENTRY_H if big else 14
        spare = max(0, (y + h - hy) - sum(n * entry_h for n in need)) // len(table)
        newset = set(new)
        for r, n in zip(table, need):
            row_h = n * entry_h + spare
            ey = hy + max(0, (row_h - n * entry_h) // 2)
            label_y = ey + (self.font(FONT_TIME_BIG).baseline - 1 if big else 2 + sf.baseline)
            graphics.DrawText(c, sf, x, label_y, self._row_colour(r), r["label"][:max(1, lw // 5)])
            for cc, cx in cols:
                entries = r["cells"].get(cc) or []
                if not entries:
                    graphics.DrawText(c, sf, cx + 2, label_y, Color(70, 70, 70), "-")
                cw = widths[cc]
                for j, e in enumerate(entries):
                    if j:                                     # a line between times that follow different rules
                        c.FillRect(cx + 8, ey + j * entry_h - 1, cw - 8, 1, Color(55, 55, 55))
                    draw = self._entry_big if big else self._entry
                    draw(c, cx, ey + j * entry_h, cw, e, now, newset)
            hy += row_h
            if hy < y + h:
                c.FillRect(x, hy - 1, w, 1, Color(35, 35, 35))

    def _is_home(self, e):
        """Whether a list time is home's (BC's): its time is highlighted."""
        if not self.s.get("highlight_home", True):
            return False
        zones = self.data.get("zones") or []
        return any(zones[z]["id"] == self.s.get("home_zone") for z in e["zones"] if z < len(zones))

    def _time(self, c, font, path, x, y, text, rgb, home):
        """A clock in the list (y = its top): white, or home's white outlined in black on a box in its colour."""
        if home:
            _boxed(c, font, x, y + font.baseline, self.tw(path, text), rgb, text)
        else:
            graphics.DrawText(c, font, x, y + font.baseline, Color(255, 255, 255), text)

    def _entry_big(self, c, cx, cy, cw, e, now, newset):
        """A roomy time: the clock (9x15 bold) and AM/PM, the zone's name (6x10, in its colour) under it, then the
        places (places with new rules this year in the colour too)."""
        tf, nf, sf = self.font(FONT_TIME_BIG), self.font(FONT_NAME), self.font(FONT_SMALL)
        rgb = color_of(self.s, e["off"])
        tt, ap = clock_text(now, e["off"], self.s.get("time_format", "12h"))
        self._time(c, tf, FONT_TIME_BIG, cx, cy, tt, rgb, self._is_home(e))
        if ap:
            graphics.DrawText(c, sf, cx + self.tw(FONT_TIME_BIG, tt) + 3, cy + tf.baseline, Color(170, 170, 170), ap)
        ny = cy + tf.height + 1
        names = e.get("names") or []
        name = "/".join(names[:2])
        if self.tw(FONT_NAME, name) > cw and len(names) > 1:
            name = "/".join((e.get("abbrs") or names)[:2])
        while name and self.tw(FONT_NAME, name) > cw:
            name = name[:-1]
        graphics.DrawText(c, nf, cx, ny + nf.baseline, Color(*rgb), name)
        if not self.s.get("show_places", True):
            return
        zones = self.data.get("zones") or []
        fresh = {self.s["places"].get(zones[z]["id"], "") for z in e["zones"] if z in newset}
        px, py = cx, ny + nf.height + sf.baseline
        for p in sorted(e["places"], key=lambda p: p not in fresh):
            w = self.tw(FONT_SMALL, p)
            if px + w > cx + cw:
                break
            graphics.DrawText(c, sf, px, py, Color(*rgb) if p in fresh else Color(185, 185, 185), p)
            px += w + 5

    def _entry(self, c, cx, cy, cw, e, now, newset):
        """One time in a cell: the clock (7x13 bold), then the zone's name over AM/PM and the places
        (rows by offset);
        old-zone rows: a colour mark (hatched like the map when its zones are) and a rule tag (NO DST highlighted)
        over the offset and places."""
        tf, sf, rf = self.font(FONT_TIME), self.font(FONT_SMALL), self.font(FONT_TAG)
        rgb = color_of(self.s, e["off"])
        tx = cx
        if self.s.get("legend_rows") != "offset":      # rows by offset: the row's name has the colour instead
            hatched = bool(newset & set(e["zones"])) and self.s.get("highlight_new", True)
            self._swatch(c, cx, cy, 6, tf.height - 1, rgb, hatched)
            tx = cx + 8
        tt, ap = clock_text(now, e["off"], self.s.get("time_format", "12h"))
        self._time(c, tf, FONT_TIME, tx, cy, tt, rgb, self._is_home(e))
        sx = tx + self.tw(FONT_TIME, tt) + 3
        rx = sx
        by_offset = self.s.get("legend_rows") == "offset"
        ly = cy + sf.height + sf.baseline
        px = sx
        if ap and by_offset:                          # rows by offset: AM/PM leads the places line
            graphics.DrawText(c, sf, px, ly, Color(170, 170, 170), ap)
            px += self.tw(FONT_SMALL, ap) + 4
        elif ap and not by_offset:
            graphics.DrawText(c, sf, sx, cy + sf.baseline, Color(170, 170, 170), ap)
            rx += self.tw(FONT_SMALL, ap) + 3
        if by_offset and e.get("names"):
            # the zone's name in the row's colour; a time two zones share gets both ("Pacific/Mountain", or
            # "PDT/MST" when that's too long), never just one of them
            room = cx + cw - rx
            name = "/".join(e["names"][:2])
            if self.tw(FONT_SMALL, name) > room and len(e["names"]) > 1:
                name = "/".join((e.get("abbrs") or e["names"])[:2])
            while name and self.tw(FONT_SMALL, name) > room:
                name = name[:-1]
            graphics.DrawText(c, sf, rx, cy + sf.baseline, Color(*rgb), name)
        labels = self.s.get("rule_labels") or {}
        tag = "" if e.get("dst") is None else str(labels.get("dst" if e["dst"] else "fixed", "") or "")
        tw = self.tw(FONT_TAG, tag)
        if tag and rx + tw + 1 <= cx + cw:
            if e["dst"]:
                graphics.DrawText(c, rf, rx, cy + rf.baseline, Color(130, 130, 130), tag)
            else:
                c.FillRect(rx - 1, cy - 1, tw + 2, rf.height + 1, Color(225, 225, 225))
                graphics.DrawText(c, rf, rx, cy + rf.baseline, Color(0, 0, 0), tag)
        if self.s.get("show_utc", True) and not by_offset:
            off = fmt_offset(e["off"])[3:]
            graphics.DrawText(c, sf, px, ly, Color(*rgb), off)
            px += self.tw(FONT_SMALL, off) + 4
        if not self.s.get("show_places", True):
            return
        zones = self.data.get("zones") or []
        fresh = {self.s["places"].get(zones[z]["id"], "") for z in e["zones"] if z in newset}
        for p in sorted(e["places"], key=lambda p: p not in fresh):
            w = self.tw(FONT_SMALL, p)
            if px + w > cx + cw:
                break
            graphics.DrawText(c, sf, px, ly, Color(*rgb) if p in fresh else Color(185, 185, 185), p)
            px += w + 4

    def _row_colour(self, r):
        """A row's name: in its offset's colour when rows are offsets, grey for old-zone rows."""
        return Color(*color_of(self.s, r["key"])) if self.s.get("legend_rows") == "offset" else Color(150, 150, 150)

    def _swatch(self, c, x, y, w, h, rgb, hatched):
        """A time's colour mark, drawn like its areas on the map: solid, or (zones with new rules on the upcoming
        map) the dim fill with bright diagonal lines."""
        if not hatched:
            c.FillRect(x, y, w, h, Color(*rgb))
            return
        fill = min(1.0, max(0.15, float(self.s.get("fill") or 0.45)))
        c.FillRect(x, y, w, h, Color(*(int(v * fill) for v in rgb)))
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                if (xx + yy) % 4 == 0:
                    c.SetPixel(xx, yy, *rgb)

    def _legend_compact(self, c, x, y, w, h, state, kind, change, now, test, newset=frozenset()):
        """A narrow strip beside the map (one side): each row's name, then the Canada and US times beside each other."""
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
        half = w // 2
        for k, cc in enumerate(COUNTRIES):
            graphics.DrawText(c, sf, x + k * half, hy + sf.baseline, Color(235, 235, 235),
                              str(self.s["country_short"].get(cc, cc)))
        c.FillRect(x, hy + sf.height, w, 1, Color(90, 90, 90))
        hy += line + 1
        table = table_of(self.data, state, self.s)
        need = [max([len(r["cells"].get(cc, [])) for cc in COUNTRIES] + [1]) + 1 for r in table]
        gap = min(10, max(0, (y + h - hy) - sum(need) * line) // max(1, len(table)))   # rows spread over the height
        for r, lines in zip(table, need):
            n = lines - 1
            if hy + line * lines > y + h:
                return
            hy += gap // 2
            graphics.DrawText(c, sf, x, hy + sf.baseline, self._row_colour(r), r["label"][:max(1, w // 5)])
            hy += line
            for k, cc in enumerate(COUNTRIES):
                for j, e in enumerate(r["cells"].get(cc) or []):
                    ex, ey = x + k * half, hy + j * line
                    tx = ex
                    if self.s.get("legend_rows") != "offset":
                        self._swatch(c, ex, ey, 3, sf.height - 1, color_of(self.s, e["off"]),
                                     bool(newset & set(e["zones"])) and self.s.get("highlight_new", True))
                        tx = ex + 5                         # a gap before a highlighted time's box
                    self._time(c, sf, FONT_SMALL, tx, ey, clock_text(now, e["off"], self.s.get("time_format", "12h"))[0],
                               color_of(self.s, e["off"]), self._is_home(e))
            hy += n * line + gap - gap // 2

    def _map_labels(self, c, x, y, m, state, groups, now, bounds=None):
        """Each zone's time in the middle of its biggest area, once per offset in Canada and in the US. The main
        zones (the ones in the list) all get the full-size font, spilling past their area (and the map, within
        ``bounds``: the panel, in map pixels) if they must; only zones left off the list (Alaska, the Atlantic,
        Newfoundland) drop to smaller fonts to fit."""
        f = self.font(FONT_MAP)
        mw, mh = m["rect"][2], m["rect"][3]
        x0, y0, x1, y1 = bounds or (0, 0, mw, mh)
        zones = self.data.get("zones") or []
        listed = set(self.s.get("list_hide") or [])
        main = {(zones[i].get("cc", ""), o) for i, o in enumerate(state["offs"])
                if i < len(zones) and zones[i]["id"] not in listed}
        anchors = (state.get("anchors") or {}).get(m["key"]) or []
        small = self.font(FONT_SMALL)
        boxes = []
        hide = set(self.s.get("map_label_hide") or [])
        home, home_cc = home_offset(self.data, state, self.s)
        offsets_on = bool(self.s.get("map_label_offsets")) and home is not None
        # roomiest first, so a label on a thin strip (US Pacific's coast) is the one that moves aside
        for cc, off, ax, ay, room in sorted(anchors, key=lambda a: -a[4]):
            # once per offset in Canada and in the US (not Mexico), unless that one is switched off
            if cc not in COUNTRIES or f"{cc}:{off}" in hide:
                continue
            text = clock_text(now, off, self.s.get("time_format", "12h"))[0]
            # room is the distance to the area's edge, x counted at 0.55 (hub/timezones.py anchors): a label may
            # overlap the edge a little
            # a main zone: always full size; another (Newfoundland, Alaska) the biggest font that fits, down to the
            # narrow one a little over its coast
            if (cc, off) in main:
                fonts = ((FONT_MAP, f, 0.0),)
            else:
                fonts = ((FONT_MAP, f, None), (FONT_SMALL, small, None), (FONT_TAG, self.font(FONT_TAG), 2.0))
            for path, font, least in fonts:
                tw = self.tw(path, text)
                if room >= (least if least is not None else max(font.height * 0.4, tw * 0.22)):
                    # the offset from home on a second line, in the small font (the narrow one under a narrow time)
                    sub_text, sub_font, sub_path = "", None, None
                    if offsets_on:
                        sub_path = FONT_TAG if path == FONT_TAG else FONT_SMALL
                        sub_font, sub_text = self.font(sub_path), from_home(off, home, own=cc == home_cc)
                    sub_h = sub_font.height if sub_text else 0
                    bw = max(tw, self.tw(sub_path, sub_text) if sub_text else 0)
                    # centred on the anchor, but kept inside the panel (Alaska's corner would spill off it)
                    clamp_x = lambda v: min(max(v, x0 + 1), x1 - bw - 1)
                    clamp_y = lambda v: min(max(v, y0 + font.baseline + 1), y1 - (font.height - font.baseline) - sub_h - 1)
                    lx = clamp_x(ax - bw // 2)
                    by = clamp_y(ay + font.baseline // 2 - 1 - sub_h // 2)
                    is_home = self.s.get("highlight_home", True) and cc == home_cc and off == home
                    pad = 1 if is_home else 0                  # home's box is a pixel bigger than the outline
                    lx, by = _clear_spot(lx, by, bw + 2, font.height + sub_h + 2, font.baseline, boxes, clamp_x, clamp_y,
                                         pad=pad)
                    tx = x + lx + (bw - tw) // 2
                    if is_home:
                        # home's time (BC's): white, outlined in black, on a box in its colour
                        _boxed(c, font, tx, y + by, tw, color_of(self.s, off), text)
                    else:
                        _outlined(c, font, tx, y + by, Color(255, 255, 255), text)
                    if sub_text:
                        sw = self.tw(sub_path, sub_text)
                        _outlined(c, sub_font, x + lx + (bw - sw) // 2, y + by + (font.height - font.baseline)
                                  + sub_font.baseline, Color(*color_of(self.s, off)), sub_text)
                    boxes.append((lx - 1 - pad, by - font.baseline - 1 - pad, bw + 2 + 2 * pad, font.height + sub_h + 2 + pad))
                    break
        return boxes

    def _cities(self, c, x, y, m, state, now, taken=()):
        cities = [cy for cy in (self.s.get("cities") or []) if cy.get("on")]
        if not cities:
            return
        f = self.font(FONT_SMALL)
        mx, my, mw, mh = m["rect"]
        taken = list(taken)
        zones_n = len(state["offs"])
        for cy in cities:
            try:
                p = place(m["frames"], float(cy["lon"]), float(cy["lat"]), mw, mh)
            except (KeyError, TypeError, ValueError):
                continue
            if p is None:
                continue
            px, py = p
            lines = [str(cy.get("name") or "")]
            mode = {True: "after", False: "off", None: "off"}.get(self.s.get("city_times"), self.s.get("city_times"))
            if mode in ("after", "below"):
                z = _zone_at(m, px, py)
                if 0 < z <= zones_n:
                    t = clock_text(now, state["offs"][z - 1], self.s.get("time_format", "12h"))[0]
                    if mode == "below":
                        lines.append(t)
                    else:
                        lines[0] += " " + t
            tw = max(self.tw(FONT_SMALL, s) for s in lines)
            spot = None
            for lx in (px + 3, px - 3 - tw):
                box = (lx - 1, py - f.baseline // 2 - 1, tw + 2, f.height * len(lines) + 1)
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
                for k, s in enumerate(lines):                 # left of the dot: lines end at the dot's side
                    sx = spot[0] + 1 + (tw - self.tw(FONT_SMALL, s) if spot[0] < px else 0)
                    _outlined(c, f, x + sx, y + spot[1] + 1 + f.baseline + k * f.height,
                              Color(255, 255, 255) if k == 0 else Color(200, 200, 200), s)
                taken.append(spot)


def _zone_at(m, px, py):
    """The zone at a map pixel: the land's, else its water's (a city on a lakeshore: Toronto, Chicago), else the
    nearest land within 2 px; 0 if none."""
    idx, water = m["idx"], m.get("water")
    h, w = idx.shape
    px, py = min(max(px, 0), w - 1), min(max(py, 0), h - 1)
    if idx[py, px]:
        return int(idx[py, px])
    if water is not None and water[py, px]:
        return int(water[py, px])
    for r in (1, 2):
        box = idx[max(0, py - r):py + r + 1, max(0, px - r):px + r + 1]
        if box.any():
            vals, counts = np.unique(box[box > 0], return_counts=True)
            return int(vals[np.argmax(counts)])
    return 0


def _clear_spot(lx, by, w, h, baseline, boxes, clamp_x, clamp_y, reach=16, pad=0):
    """A label's place clear of the boxes already placed: as it is if free, else the nearest free spot within
    ``reach`` px (left first, then right, down, up and diagonals; kept in bounds), else where it was. ``pad`` grows
    the label's box on the sides and top (home's coloured box)."""
    box = lambda x_, y_: (x_ - 1 - pad, y_ - baseline - 1 - pad, w + 2 * pad, h + pad)
    if not any(_hit(box(lx, by), b) for b in boxes):
        return lx, by
    tries = sorted({(dx, dy) for dx in range(-reach, reach + 1, 2) for dy in range(-reach, reach + 1, 3)},
                   key=lambda d: (abs(d[0]) + abs(d[1]) * 1.5, d[0] > 0, d[1] < 0))
    for dx, dy in tries:
        nx, ny = clamp_x(lx + dx), clamp_y(by + dy)
        if not any(_hit(box(nx, ny), b) for b in boxes):
            return nx, ny
    return lx, by


def _hit(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def _boxed(c, font, x, y, tw, rgb, s):
    """White text outlined in black on a box in ``rgb`` (y = the baseline); the box leaves room for the outline."""
    c.FillRect(x - 2, y - font.baseline - 2, tw + 4, font.height + 3, Color(*rgb))
    _outlined(c, font, x, y, Color(255, 255, 255), s)


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
