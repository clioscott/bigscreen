"""The ISS maps (a webcam in space): the full map, shown in the ISS webcam's turn while its video is unusable (the
night side, the loss-of-signal card, the feed down), and the small map in a corner of the video.

Both are a world map with day and night (the weather maps' shading: darker where the Sun is down, smooth through
civil twilight), the ISS's last TRAIL_SEC of orbit as a line fading into the past, and the ISS. They stay centred on
the ISS from side to side (the world scrolls under it; it only moves up and down), so nothing is ever cut at the
date line; the full map can instead hold the world still (``cams.<id>.map.fixed``) and let the ISS cross it, its
lines then carrying on from the other edge. The ISS's band of latitudes stays clear of the caption.

The full map also has grey country borders and the ISS's real communications network:
  * NASA's side: the ISS talks to the ground through a TDRS relay (real positions: hub/jobs/iss.py from CelesTrak),
    which sends it down to White Sands, or Guam for the relay over the Indian Ocean. Drawn red (up to the relay)
    and cyan (down to the ground) while NASA's own telemetry says it has the signal (AOS; hub/iss_live.py); nothing
    at LOS. Which relay is in use isn't published: the one that can see both the ISS and a terminal, nearest the ISS.
  * Russia's side (``extras.russian``): straight down to a Russian ground station that can see the ISS, else up to
    a Luch-5 relay and down to Shchyolkovo near Moscow. Inferred from geometry (no public Russian telemetry); orange.
The extras (``cams.<id>.map.extras``, each on/off with its own settings, EXTRAS): city lights on the night side
(NASA Black Marble), the aurora forecast (NOAA OVATION), the track ahead, the trail in sunlight and Earth's shadow,
Tiangong, the AOS/LOS tag, data moving along the links (the one animation: drawn per frame over the cached map),
station telemetry (NASA ISS Live) and the docked spacecraft. Zones the full width of the wall get the bigger icons.
The small map has only coastlines, the trail and a tiny ISS.

Everything is for the moment the video shows (space.LAG behind). The coastlines and borders are Natural Earth's
1:110m (assets/maps/world_110m.json, tools/build_world_lines.py), drawn once per size; the night shading (with the
lights and aurora) is recomputed every SHADE_EVERY and the map every COMPOSE_EVERY (the ISS moves about one pixel in
ten seconds), and each frame just copies it."""

from __future__ import annotations

import json
import math
import os
import threading
import time

import numpy as np

from common.orbit import gmst, julian, sun_dir

ASSET = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "maps", "world_110m.json")
DATA_DIR = os.environ.get("BIGSCREEN_DATA_DIR", "/dev/shm/bigscreen")   # (set by the webcam panel / the hub)
OCEAN = (8, 18, 40)
OUTSIDE = (3, 7, 16)          # above and below the map when the zone is taller than the world
BORDER = (90, 90, 95)
COAST = (90, 170, 200)
TRAIL = (255, 255, 255)
LINK = (255, 40, 40)          # NASA: the ISS up to a TDRS relay
RELAY_LINK = (60, 220, 255)   # NASA: the relay down to its ground terminal
RU_LINK = (255, 140, 40)      # Russia: the ISS to a ground station or up to a Luch relay
RU_RELAY_LINK = (255, 210, 110)   # Russia: the Luch relay down to the ground
LIGHTS = (255, 200, 110)
AURORA = (60, 255, 140)
SHADOW = {"blue": (90, 130, 255), "dim": (150, 150, 160)}
SUNLIT = (255, 235, 170)
AHEAD = (255, 230, 150)
CSS = (230, 90, 70)
TXT, DIM_TXT, AOS_C, LOS_C = (215, 220, 228), (110, 135, 160), (80, 225, 120), (130, 130, 140)
FRAME = (0, 0, 0)             # round the small map
NIGHT = 0.45                  # how bright the night side is
TWILIGHT = 6.0                # degrees of Sun below the horizon over which day fades to night
TRAIL_SEC = 90 * 60
ISS_LAT = 52.0                # the ISS never goes further north or south than this (51.6° orbit)
BAND_MARGIN = 4               # px kept between the ISS's band and the caption (the icon is 5 px tall)
SHADE_EVERY = 30.0
COMPOSE_EVERY = 1.0
CORNERS = ("tl", "tr", "bl", "br")
BIG_W = 400                   # zones at least this wide (the full width above the clock, the whole wall): big icons

# ground stations (lat, lon; city-level): NASA's TDRS ground terminals, and Russian ground stations
NASA_GROUND = {"White Sands": (32.50, -106.61), "Guam": (13.59, 144.84)}
RU_GROUND = {"Shchyolkovo": (55.92, 38.00), "Ulan-Ude": (51.83, 107.60), "Ussuriysk": (43.80, 131.95),
             "Yelizovo": (53.19, 158.39)}
LUCH_GROUND = "Shchyolkovo"
CN_GROUND = {"Beijing": (40.07, 116.27), "Kashgar": (39.50, 76.00), "Jiamusi": (46.80, 130.32), "Sanya": (18.25, 109.50)}
GROUND_FLAG = {**{n: "us" for n in NASA_GROUND}, **{n: "ru" for n in RU_GROUND}, **{n: "cn" for n in CN_GROUND}}
RELAY_FLAG = {"tdrs": "us", "luch": "ru", "tianlian": "cn"}
FALLBACK_RELAYS = [{"name": "TDRS", "net": "tdrs", "lat": 0.0, "lon": lo} for lo in (-41.0, -62.0, -171.0, 85.0)]
# the links' look per network (``map.links.<net>``): the leg up to a relay (or straight down), the leg down from it,
# how the data moves along them (the animation drawn every frame) and how bright the line under it is
NETS = {"nasa": "NASA and partners (US, Canada, Europe, Japan)", "ru": "Russia", "cn": "China (Tiangong)"}
LINK_DEFAULTS = {
    "nasa": {"up": "#ff2828", "down": "#3cdcff", "style": "dots", "speed": "normal", "direction": "both", "line": 1.0},
    "ru": {"up": "#ff8c28", "down": "#ffd26e", "style": "dots", "speed": "normal", "direction": "both", "line": 1.0},
    "cn": {"up": "#ff4646", "down": "#ffd23c", "style": "dots", "speed": "normal", "direction": "both", "line": 1.0},
}
LINK_STYLES = ("dots", "dashes", "comet", "pulse", "still")
RELAY_SHOW = ("always", "linked")
ALT_KM = 420.0
MIN_EL = 5.0
R_KM = 6371.0
GEO_KM = 42164.0
GRAZE_KM = 100.0              # a line of sight passes at least this far above the ground (the atmosphere)

# --- icons: small (half-width zones and the small map) and big (the full width) ---------------------------------
ISS_ART = ["AA.AA...AA.AA", "AA.AA...AA.AA", "WWWWWWWWWWWWW", "AA.AA.G.AA.AA", "AA.AA.W.AA.AA"]
RELAY_ART = ["BB.W.BB", "BBWWWBB", "BB.W.BB"]
LUCH_ART = ["TT.W.TT", "TTWWWTT", "TT.W.TT"]
DISH_ART = ["D.....", ".DD...", ".D.D..", "..DDS.", "...S..", ".KKKK.", ".KLKL."]   # a big dish on a building
SMALL_ART = ["A.A", "AWA", "A.A"]                                                  # the small map's ISS
# Tiangong from above, as big as the ISS: the two lab modules (Wentian, Mengtian) across the node with their big
# wings at the ends, the core module (Tianhe) back from it with its own smaller wings
CSS_ART = ["RR...W...RR",
           "RRWWWWWWWRR",
           "RR...W...RR",
           "....QWQ....",
           ".....W....."]
ISS_BIG = ["AAA.AAA.....AAA.AAA",
           "HHH.HHH..W..HHH.HHH",
           "AAA.AAA.WWW.AAA.AAA",
           "WWWWWWWWWWWWWWWWWWW",
           "AAA.AAA.WGW.AAA.AAA",
           "HHH.HHH..W..HHH.HHH",
           "AAA.AAA..G..AAA.AAA"]
RELAY_BIG = ["BBB.D.D.BBB",
             "BBB.DWD.BBB",
             "BBBWWWWWBBB",
             "BBB.DWD.BBB",
             "BBB.D.D.BBB"]
LUCH_BIG = ["TTT..D..TTT",
            "TTT.WWW.TTT",
            "TTTWWWWWTTT",
            "TTT.WWW.TTT",
            "TTT..D..TTT"]
DISH_BIG = ["D....S...D",
            "DD...S..DD",
            ".DDD.SDDD.",
            "..DDDDDD..",
            "....SS....",
            "....SS....",
            ".KKKKKKKK.",
            ".KLKLKLKK.",
            ".KKKKKKKK."]
CSS_BIG = ["QQQQ.........QQQQ",
           "RRRR....W....RRRR",
           "RRRRWWWWWWWWWRRRR",
           "QQQQ....W....QQQQ",
           "......RRWRR......",
           "......QQWQQ......",
           "........W........"]
# the AOS/LOS tag as an icon (``extras.signal.style``): signal bars, green with the signal, grey and crossed out without
AOS_ART = ["......V", "....V.V", "..V.V.V", "V.V.V.V"]
LOS_ART = ["X.X...Z", ".X..Z.Z", "X.X.Z.Z", "Z.Z.Z.Z"]
PAL = {"A": (220, 150, 40), "W": (235, 235, 235), "G": (240, 200, 60), "B": (70, 130, 255), "D": (225, 225, 235),
       "S": (150, 150, 160), "K": (110, 110, 125), "L": (255, 210, 90), "T": (60, 200, 170), "E": (250, 190, 140),
       "R": (230, 80, 60), "H": (160, 105, 30), "Y": (255, 214, 90), "M": (220, 222, 235), "P": (255, 150, 100),
       "O": (255, 225, 120), "Q": (150, 45, 35), "V": (80, 225, 120), "X": (235, 60, 60), "Z": (130, 130, 140)}
RU = lambda art: [r.replace("D", "E") for r in art]                               # a Russian station's dish
CN = lambda art: [r.replace("D", "R") for r in art]                               # a Chinese station's dish
TL_ART = ["RR.W.RR", "RRWWWRR", "RR.W.RR"]
TL_BIG = [r.replace("T", "R") for r in LUCH_BIG]
SUN_ART = [".Y.Y.", "YYYYY", ".YYY.", "YYYYY", ".Y.Y."]
MOON_ART = [".MM.", "MM..", "MM..", ".MM."]
PAD_ART = ["..P..", ".PPP.", ".PPP.", "PPPPP", "P.P.P"]
HOME_ART = ["...O...", "..OOO..", ".OOOOO.", "OOOOOOO", ".OO.OO.", ".OO.OO."]    # Satellites Overhead: the wall's home
HORIZON = (120, 200, 255)     # its ring: where ov's layer is min_el above the horizon
ICONS = {False: {"iss": ISS_ART, "tdrs": RELAY_ART, "luch": LUCH_ART, "tianlian": TL_ART, "nasa": DISH_ART,
                 "ru": RU(DISH_ART), "cn": CN(DISH_ART), "css": CSS_ART},
         True: {"iss": ISS_BIG, "tdrs": RELAY_BIG, "luch": LUCH_BIG, "tianlian": TL_BIG, "nasa": DISH_BIG,
                "ru": RU(DISH_BIG), "cn": CN(DISH_BIG), "css": CSS_BIG}}
GROUND_ART = {"nasa": "nasa", "ru": "ru", "cn": "cn"}

# --- the extras: each on or off, with its settings (cams.<id>.map.extras) ---------------------------------------
TELEMETRY_ROWS = ("cabin_p", "cabin_t", "o2", "co2", "gyros", "ku", "water", "arrays")
EXTRAS = {
    "lights": {"on": True, "strength": 0.8},
    "aurora": {"on": True, "strength": 0.8},
    "ahead": {"on": True, "minutes": 45},
    "sunlit": {"on": True, "shadow": "blue"},
    "tiangong": {"on": True, "trail": True, "label": True},
    "signal": {"on": True, "tag": True, "place": "auto", "style": "text"},
    "russian": {"on": True},
    "flow": {"on": True, "speed": "normal", "direction": "both"},
    "telemetry": {"on": True, "corner": "tr", "rows": ["cabin_p", "cabin_t", "o2", "co2", "gyros", "water"]},
    "docked": {"on": True, "corner": "tl", "days": True},
    # (the Space Map's own: the webcam's map keeps to the ones above)
    "trail": {"on": True, "minutes": 90},
    "chinese": {"on": True},
    "borders": {"on": True},
    "night": {"on": True, "darkness": 0.55},
    "grid": {"on": False, "step": 30, "lines": "grid"},
    "sunmoon": {"on": False, "lines": False},
    "footprint": {"on": False, "elevation": 10, "tiangong": False},
    "terminator": {"on": False},
    "launches": {"on": False, "count": 3},
}
SPACE_ONLY = ("trail", "chinese", "borders", "night", "grid", "sunmoon", "footprint", "terminator", "launches")
FLOW_SPEED = {"slow": 6.0, "normal": 12.0, "fast": 24.0}   # px a second
FLOW_GAP = 8                                                 # px between the dots


def extras_of(cam, space_map=False) -> dict:
    """The map's extras for a webcam (or the Space Map: space_map): EXTRAS with its own settings over them. The
    webcam's map has none of the Space Map's own (SPACE_ONLY) but the trail, borders and night as they always were."""
    got = ((cam or {}).get("map") or {}).get("extras") if isinstance((cam or {}).get("map"), dict) else None
    got = got if isinstance(got, dict) else {}
    out = {k: {**d, **(got.get(k) if isinstance(got.get(k), dict) else {})} for k, d in EXTRAS.items()}
    if not space_map:
        for k in SPACE_ONLY:
            out[k] = {**EXTRAS[k], "on": k in ("trail", "borders", "night")}
    return out


def _on(ex, k):
    return bool(ex and (ex.get(k) or {}).get("on"))


# --- geometry of the links ------------------------------------------------------------------------------------
def _xyz(lat, lon, r):
    la, lo = math.radians(lat), math.radians(lon)
    return (r * math.cos(la) * math.cos(lo), r * math.cos(la) * math.sin(lo), r * math.sin(la))


def _clear(a, b, graze=GRAZE_KM):
    """The straight line between points a and b (km, Earth-centred) passes above the ground (and the air)."""
    d = [b[i] - a[i] for i in range(3)]
    dd = sum(x * x for x in d) or 1e-9
    f = max(0.0, min(1.0, -sum(a[i] * d[i] for i in range(3)) / dd))
    p = [a[i] + f * d[i] for i in range(3)]
    return math.sqrt(sum(x * x for x in p)) >= R_KM + graze


def elevation(site, target):
    """Degrees above the horizon of target (Earth-centred km) seen from a ground site (lat, lon)."""
    o = _xyz(site[0], site[1], R_KM)
    d = [target[i] - o[i] for i in range(3)]
    n = math.sqrt(sum(x * x for x in d)) or 1e-9
    up = [x / R_KM for x in o]
    return math.degrees(math.asin(max(-1.0, min(1.0, sum(d[i] * up[i] for i in range(3)) / n))))


def gc_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    c = math.sin(la1) * math.sin(la2) + math.cos(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return R_KM * math.acos(max(-1.0, min(1.0, c)))


def _generation(name):
    """How new a relay is, from its name (TDRS 13 > TDRS 3; Luch-5V > 5B > 5A): the newest one that can see the ISS is
    taken (the old ones are spares)."""
    import re
    n = str(name or "")
    m = re.search(r"(\d+)\s*$", n)
    if m:
        return int(m.group(1))
    m = re.search(r"-5([A-Z])", n.upper())
    return ord(m.group(1)) if m else 0


def link_style(mapset, net) -> dict:
    """A network's link look: LINK_DEFAULTS with ``map.links.<net>`` over it, colours as RGB."""
    got = ((mapset or {}).get("links") or {}).get(net) if isinstance((mapset or {}).get("links"), dict) else None
    st = {**LINK_DEFAULTS[net], **(got if isinstance(got, dict) else {})}
    from display.modules.mapsats import hex_rgb
    st["rgb"] = [hex_rgb(st["up"], hex_rgb(LINK_DEFAULTS[net]["up"])), hex_rgb(st["down"], hex_rgb(LINK_DEFAULTS[net]["down"]))]
    return st


def cn_links(pos, relays=None):
    """Tiangong's link at pos: straight down to a Chinese ground station that sees it above MIN_EL (the highest), else
    through the Tianlian relay that sees it and a Chinese station (inferred from geometry, as for Russia)."""
    css = _xyz(pos[0], pos[1], R_KM + 390.0)
    el, n = max((elevation(p, css), n) for n, p in CN_GROUND.items())
    if el >= MIN_EL:
        return [{"net": "cn", "pts": [pos, CN_GROUND[n]], "legs": [0], "via": n}]
    best = None
    for r in valid_relays(relays):
        if r.get("net") != "tianlian":
            continue
        sat = _xyz(r["lat"], r["lon"], GEO_KM)
        if not _clear(css, sat):
            continue
        g = max(CN_GROUND, key=lambda k: elevation(CN_GROUND[k], sat))
        if elevation(CN_GROUND[g], sat) < MIN_EL:
            continue
        rank = (-_generation(r.get("name")), gc_km(pos, (r["lat"], r["lon"])))
        if best is None or rank < best[0]:
            best = (rank, r, g)
    if not best:
        return []
    _d, r, g = best
    return [{"net": "cn", "pts": [pos, (r["lat"], r["lon"]), CN_GROUND[g]], "legs": [0, 1], "via": r.get("name")}]


def links(pos, relays=None, aos=None, russian=True):
    """The ISS's links at pos (lat, lon): [{"net", "pts": [(lat, lon), …] from the ISS, "cols": [colour per leg],
    "via": name}]. NASA: unless the signal is known lost (aos False), through the TDRS that both the ISS and a ground
    terminal can see (the newest; then the nearest the ISS), down to White Sands (or else Guam). Russia: down to a Russian station
    that sees the ISS above MIN_EL (the highest), else through a Luch-5 that the ISS and Shchyolkovo can see."""
    relays = valid_relays(relays) or FALLBACK_RELAYS
    iss = _xyz(pos[0], pos[1], R_KM + ALT_KM)
    out = []

    def via(net, grounds):
        best = None
        for r in relays:
            if r.get("net") != net:
                continue
            sat = _xyz(float(r.get("lat") or 0.0), float(r["lon"]), GEO_KM)
            if not _clear(iss, sat):
                continue
            g = next((n for n, p in grounds.items() if elevation(p, sat) >= MIN_EL), None)
            if g is None:
                continue
            d = gc_km(pos, (float(r.get("lat") or 0.0), float(r["lon"])))
            rank = (-_generation(r.get("name")), d)
            if best is None or rank < best[0]:
                best = (rank, r, g)
        return best

    if aos is not False:
        got = via("tdrs", NASA_GROUND)
        if got:
            _d, r, g = got
            out.append({"net": "nasa", "pts": [pos, (r.get("lat") or 0.0, r["lon"]), NASA_GROUND[g]],
                        "cols": [LINK, RELAY_LINK], "legs": [0, 1], "via": r.get("name")})
    if russian:
        seen = [(elevation(p, iss), n) for n, p in RU_GROUND.items()]
        el, n = max(seen)
        if el >= MIN_EL:
            out.append({"net": "ru", "pts": [pos, RU_GROUND[n]], "cols": [RU_LINK], "legs": [0], "via": n})
        else:
            got = via("luch", {LUCH_GROUND: RU_GROUND[LUCH_GROUND]})
            if got:
                _d, r, g = got
                out.append({"net": "ru", "pts": [pos, (r.get("lat") or 0.0, r["lon"]), RU_GROUND[g]],
                            "cols": [RU_LINK, RU_RELAY_LINK], "legs": [0, 1], "via": r.get("name")})
    return out


def valid_relays(relays):
    """The relays with a usable position (the data comes from the hub; anything odd is left out)."""
    out = []
    for r in relays if isinstance(relays, list) else []:
        try:
            if isinstance(r, dict) and r.get("net") in ("tdrs", "luch", "tianlian"):
                out.append({**r, "lat": float(r.get("lat") or 0.0), "lon": float(r["lon"])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def signal_at(live, t):
    """NASA's signal at time t from the hub's live feed: True (AOS), False (LOS), None (not known)."""
    if not isinstance(live, dict):
        return None
    hist = live.get("signal") or []
    if not hist or (not live.get("connected") and t > (live.get("at") or 0) - 60):
        return None
    got = None
    for ts, a in hist:
        if ts <= t:
            got = bool(a)
        else:
            break
    return got


# --- the hub's live feed (NASA ISS Live: hub/iss_live.py), polled while a map is up -----------------------------
LIVE_EVERY = 3.0
LIVE_STALE = 15.0             # a snapshot older than this (the hub down, restarting) isn't used
_live = {"data": None, "got": 0.0, "used": 0.0, "hub": None, "thread": None, "keys": None}


HISTORY_EVERY = 60.0          # the readings' past (/api/iss/history: the Space Map's sparklines), this often


def live_from(hub, history_keys=None):
    """The newest /api/iss/live from the hub (polled in the background every LIVE_EVERY while asked for, with the
    readings in ``history_keys``: their past from /api/iss/history every HISTORY_EVERY, as its "history"), or None when
    there's no fresh one."""
    now = time.time()
    _live["used"], _live["hub"] = now, hub
    _live["keys"] = sorted(history_keys) if history_keys else None
    th = _live["thread"]
    if hub and (th is None or not th.is_alive()):
        _live["thread"] = threading.Thread(target=_poll_live, name="iss-live", daemon=True)
        _live["thread"].start()
    return _live["data"] if now - _live["got"] < LIVE_STALE else None


def _poll_live():
    import urllib.request
    hist, hist_at = None, 0.0
    while time.time() - _live["used"] < 15:
        base = _live["hub"].rstrip("/")
        try:
            keys = _live["keys"]
            if not keys:
                hist = None
            elif time.time() - hist_at > HISTORY_EVERY or (hist or {}).get("keys") != keys:
                hist_at = time.time()
                with urllib.request.urlopen(base + "/api/iss/history?keys=" + ",".join(keys), timeout=6) as r:
                    h = json.loads(r.read())
                hist = {**h, "keys": keys} if isinstance(h, dict) else hist
        except Exception:
            pass
        try:
            with urllib.request.urlopen(base + "/api/iss/live", timeout=4) as r:
                d = json.loads(r.read())
            if isinstance(d, dict):
                if hist:
                    d["history"] = hist
                _live["data"], _live["got"] = d, time.time()
        except Exception:
            pass
        time.sleep(LIVE_EVERY)


# --- rasters from the hub (data/issmap: city lights, aurora) ------------------------------------------------------
_grids: dict = {}


def grid(name):
    """data/issmap/<name>.bin as a uint8 array (rows from the north for lights, from the south for aurora), re-read
    when it changes (checked every minute); None when missing."""
    hit = _grids.get(name)
    now = time.time()
    if hit and now - hit[0] < 60:
        return hit[2]
    path = os.path.join(DATA_DIR, "issmap", name + ".bin")
    try:
        mt = os.stat(path).st_mtime
        if hit and hit[1] == mt:
            _grids[name] = (now, mt, hit[2])
            return hit[2]
        raw = open(path, "rb").read()
        w, h = int.from_bytes(raw[4:6], "big"), int.from_bytes(raw[6:8], "big")
        if w < 2 or h < 2:
            raise ValueError("empty grid")
        arr = np.frombuffer(raw[8:8 + w * h], np.uint8).reshape(h, w)
    except (OSError, ValueError):
        mt, arr = None, None
    _grids[name] = (now, mt, arr)
    return arr


# --- projection ------------------------------------------------------------------------------------------------
VIEW = {"zoom": 1.0, "stretch": 1.0, "lat": None, "lon": 0.0, "clear": True}
VIEW_RANGES = {"zoom": (1.0, 4.0), "stretch": (0.5, 2.0), "lat": (-80.0, 80.0), "lon": (-180.0, 180.0)}
TOP_MIN = 66.0                # an automatic view reaches at least this far north (Moscow, Scandinavia, the Russian
                              # ground stations), squashing the map from top to bottom when the zone is too short


OVERHEAD_ZOOM_MAX = 10.0      # (a world this many zone widths round is shaded every SHADE_EVERY on the Pi)
OVERHEAD_DEFAULTS = {"min_el": 10, "layer": "starlink", "ring": True, "home": True, "outside": 0.3, "margin": 1.08}


def overhead_of(mapset) -> dict:
    """map.overhead (Satellites Overhead): min_el (degrees above the horizon that count as seen), layer (whose height
    sizes the view and its ring: the horizon for those satellites), ring, home (the marker), outside (how bright the
    satellites past the horizon are, 0-1), margin (the ring's diameter times this fills the zone's height)."""
    got = (mapset or {}).get("overhead")
    return {**OVERHEAD_DEFAULTS, **(got if isinstance(got, dict) else {})}


def overhead_alt(ov) -> float:
    from common.satlayers import LAYERS
    return float((LAYERS.get(ov.get("layer")) or LAYERS["starlink"])["alt_km"])


def overhead_view(home, w, h, ov) -> dict:
    """The view (View opts) for Satellites Overhead: the home in the middle, the stretch that keeps a circle round
    near it (1/cos lat, as far as the projection allows), zoomed so the horizon ring of ov's layer fits the height."""
    lat, lon = float(home[0]), float(home[1])
    rho = footprint_deg(overhead_alt(ov), float(ov.get("min_el", 10)))
    stretch = max(VIEW_RANGES["stretch"][0], min(VIEW_RANGES["stretch"][1], 1.0 / max(0.2, math.cos(math.radians(lat)))))
    span = 2.0 * rho * max(1.0, float(ov.get("margin", 1.08)))      # the ring's diameter (degrees), with room round it
    zoom = min(w, h) / span / stretch * 360.0 / max(1, w)           # (round: as many px across as up and down)
    if zoom < 1.0:                                                  # (high orbits: the world's width is the least
        stretch = max(VIEW_RANGES["stretch"][0], stretch * zoom)    # zoom, so it's flattened to fit the height)
        zoom = 1.0
    return {**VIEW, "lat": max(-80.0, min(80.0, lat)), "lon": lon, "stretch": stretch, "clear": False, "_zoom": zoom}


def view_of(cam, shape) -> dict:
    """The map's view for a size (``cams.<id>.map.views.<side|full|tall|wall>``) over VIEW."""
    from display.modules.webcam_caption import SIZE_OF_SHAPE
    mp = (cam or {}).get("map") if isinstance((cam or {}).get("map"), dict) else {}
    views = mp.get("views") if isinstance(mp.get("views"), dict) else {}
    got = views.get(SIZE_OF_SHAPE.get(shape, shape))
    return {**VIEW, **(got if isinstance(got, dict) else {})}


def _num_in(v, k):
    lo, hi = VIEW_RANGES[k]
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = VIEW[k] if VIEW[k] is not None else 0.0
    return max(lo, min(hi, x))


class View:
    """Where things go on a map w×h: an equirectangular world W px round (the zone's width times the zoom, or wider
    when following the ISS in a tall narrow zone, so the map keeps filling it). The world is drawn once as a raster
    of latitudes top..bot (mh rows; ``key`` names it for the caches), put on the screen with its first row at y0
    (which can be above the zone, when the view is zoomed in on part of it).

    opts (VIEW, per size): zoom (1 = the world's width fits the zone), stretch (rows per degree of latitude against
    columns per degree of longitude), lat (the latitude at the middle of the zone, or None: automatic), lon (degrees
    the view's middle is east of the ISS, or of 0° with a fixed world), clear (automatic only: keep the ISS's band
    off the caption).

    Automatic: the ISS's whole band of latitudes (and so its trail) stays clear of reserve (top px, bottom px: the
    rows the caption covers), the rest of the zone showing as far towards the other pole as it reaches (at least
    TOP_MIN north: the map is squashed from top to bottom only when it has to be). Zoomed in too far for the band to
    fit, the view follows the ISS up and down instead. The map still runs on under the caption."""

    def __init__(self, w, h, follow=True, small=False, reserve=None, opts=None, iss_lat=None):
        self.w, self.h, self.follow, self.small = w, h, follow, small
        o = {**VIEW, **(opts or {})} if not small else dict(VIEW)
        zoom, stretch = _num_in(o["zoom"], "zoom"), _num_in(o["stretch"], "stretch")
        if o.get("_zoom") is not None:                              # (Satellites Overhead: worked out, not a setting)
            zoom = max(1.0, min(OVERHEAD_ZOOM_MAX, float(o["_zoom"])))
        self.lon_off = _num_in(o["lon"], "lon")
        if small:
            s = w / 360.0
        else:
            s = (w / 360.0 if not follow else max(w / 360.0, h / 180.0)) * zoom
        self.W = max(8, int(round(360.0 * s)))
        ys = s * stretch                                            # px per degree of latitude
        rt, rb = (reserve or (0, 0)) if not small else (0, 0)
        lat = o.get("lat")
        if small:
            ys = h / 180.0
            top = 90.0
        elif lat is not None:                                       # a latitude in the middle of the zone
            top = _num_in(lat, "lat") + (h / 2) / ys
        else:
            clear = bool(o.get("clear", True)) and bool(rt or rb)
            y_lo, y_hi = (rt + BAND_MARGIN, h - rb - BAND_MARGIN) if clear else (0, h)
            avail = y_hi - y_lo
            need = ISS_LAT + max(ISS_LAT, TOP_MIN if not rt else ISS_LAT)   # (the band, and the north it should reach)
            if zoom <= 1.2 and avail >= 12:
                ys = min(ys, avail / need, avail / (2 * ISS_LAT))
                if rb or not rt:                                    # the band's south edge just above the caption
                    top = -ISS_LAT + y_hi / ys
                else:                                               # (a caption at the top: its north edge under it)
                    top = ISS_LAT + y_lo / ys
                top = max(ISS_LAT + y_lo / ys,                      # (a zone taller than the world: centred,
                          min(top, 90.0 + max(0.0, (h / ys - 180.0) / 2)))   # as far as the band stays clear)
            elif zoom > 1.2 and iss_lat is not None:                # zoomed in: up and down with the ISS
                top = float(iss_lat) + (y_lo + avail / 2) / ys
            else:
                span = h / ys
                c = 7.0 * min(1.0, max(0.0, (span - 104.0) / 50.0))   # a short map is the ISS's band; taller: north
                top = min(90.0 + max(0.0, (span - 180.0) / 2), c + span / 2)
        if not small and (lat is not None or zoom > 1.2):          # (no further than the poles allow)
            span = h / ys
            if span <= 180.0:
                top = max(-90.0 + span, min(90.0, top))
        self.ys = ys
        # the raster: the whole world when the view moves up and down, else just the latitudes it shows
        moving = not small and lat is None and zoom > 1.2
        r_top = 90.0 if moving else min(90.0, top)
        r_bot = -90.0 if moving else max(-90.0, top - h / ys)
        self.top, self.bot = r_top, r_bot
        self.mh = max(4, int(round((r_top - r_bot) * ys)))
        self.y0 = int(round((top - r_top) * ys))                    # the raster's first row on the screen
        self.key = (self.W, self.mh, round(r_top, 3), round(r_bot, 3))

    def rows(self):
        """(screen rows, raster rows) the world covers: two slices of equal length (maybe empty)."""
        a, b = max(0, self.y0), min(self.h, self.y0 + self.mh)
        if b <= a:
            return slice(0, 0), slice(0, 0)
        return slice(a, b), slice(a - self.y0, b - self.y0)

    def wx(self, lon):
        """World x (px, 0..W) of a longitude: arrays too."""
        return (np.asarray(lon, float) + 180.0) / 360.0 * self.W

    def y(self, lat):
        return self.y0 + (self.top - np.asarray(lat, float)) / (self.top - self.bot) * self.mh

    def left(self, iss_lon):
        """The world x at the map's left edge: the ISS in the middle (moved by the view's lon), or with a fixed world
        0° (plus lon): the whole world from the date line when the map isn't zoomed."""
        if self.follow and iss_lon is None:
            return 0
        c = (float(iss_lon) if self.follow else 0.0) + self.lon_off
        return int(math.floor(float(self.wx(c)) - self.w / 2 + 0.5))


# --- drawing primitives ----------------------------------------------------------------------------------------
def _samples(X0, Y0, X1, Y1):
    """Pixel samples along segments (arrays of endpoints): (xs, ys, segment index), every pixel step."""
    X0, Y0, X1, Y1 = (np.asarray(a, float) for a in (X0, Y0, X1, Y1))
    n = np.maximum(np.abs(X1 - X0), np.abs(Y1 - Y0)).astype(np.int64) + 1
    if not n.size:
        return np.zeros(0), np.zeros(0), np.zeros(0, np.int64)
    idx = np.repeat(np.arange(n.size), n + 1)
    start = np.repeat(np.cumsum(n + 1) - (n + 1), n + 1)
    f = (np.arange(idx.size) - start) / np.repeat(n, n + 1)
    return X0[idx] + (X1 - X0)[idx] * f, Y0[idx] + (Y1 - Y0)[idx] * f, idx


def _plot(img, xs, ys, rgb, alpha=None, left=None, period=None):
    """Set the pixels at (xs, ys), blended by alpha (one per sample, or None: solid). With left/period the xs are
    world x, wrapped round the world onto a map starting at world x ``left``."""
    xi = np.rint(xs).astype(np.int64)
    yi = np.rint(ys).astype(np.int64)
    if period is not None:
        xi = (xi - (left or 0)) % period
    h, w = img.shape[:2]
    ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
    xi, yi = xi[ok], yi[ok]
    col = np.asarray(rgb, np.float32)
    if alpha is None and xi.size > 4 * h * w // 8:         # (very many: each pixel once, through a mask)
        m = np.zeros(h * w, bool)
        m[yi * w + xi] = True
        img.reshape(-1, img.shape[2])[m] = col
    elif alpha is None:
        img[yi, xi] = col
    else:                                                  # (blended in float just at these pixels)
        a = np.asarray(alpha, np.float32)[ok][:, None]
        img[yi, xi] = (img[yi, xi] * (1 - a) + col * a + 0.5).astype(img.dtype)


def _unwrap(x0, x1, period):
    """x1 moved by whole turns of the world to be the short way from x0 (a line never goes the long way round)."""
    return x0 + (x1 - x0 + period / 2) % period - period / 2


def _seg_line(img, v, left, x0, y0, x1, y1, rgb, alpha=None, per_sample=False):
    """Segments between world points (arrays), each the short way round, onto the map."""
    x0 = np.atleast_1d(np.asarray(x0, float))
    x1 = _unwrap(x0, np.atleast_1d(np.asarray(x1, float)), v.W)
    xs, ys, idx = _samples(x0, np.atleast_1d(y0), x1, np.atleast_1d(y1))
    a = None if alpha is None else np.atleast_1d(np.asarray(alpha, np.float32))[idx]
    _plot(img, xs, ys, rgb, a, left, v.W)


_sprites: dict = {}


def _sprite_arrays(art, outline=False):
    """(rgb, mask) of an icon; with outline, a pixel bigger all round with a black edge (8-way) round the ink."""
    key = (id(art), outline)
    hit = _sprites.get(key)
    if hit is None:
        hh, ww = len(art), len(art[0])
        rgb = np.zeros((hh, ww, 3), np.uint8)
        mask = np.zeros((hh, ww), bool)
        for j, row in enumerate(art):
            for i, ch in enumerate(row):
                if ch != ".":
                    rgb[j, i], mask[j, i] = PAL[ch], True
        if outline:
            m = np.pad(mask, 1)
            grown = m.copy()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    grown |= np.roll(np.roll(m, dy, 0), dx, 1)
            rgb = np.pad(rgb, ((1, 1), (1, 1), (0, 0)))          # (the edge is black: rgb 0 where there's no ink)
            mask = grown
        hit = _sprites[key] = (rgb, mask)
    return hit


TAG_PLACES = ("auto", "right", "left", "above", "below", "above_right", "above_left", "below_right", "below_left")


TAG_STYLES = ("text", "icon")
FLAG_W, FLAG_H = 9, 6                                      # (display/modules/flags.py)


def _stamp(img, rgb, mask, x0, y0):
    """rgb where mask, with its top-left at x0, y0 (clipped to img)."""
    h, w = img.shape[:2]
    hh, ww = mask.shape
    X0, Y0, X1, Y1 = max(0, x0), max(0, y0), min(w, x0 + ww), min(h, y0 + hh)
    if X1 <= X0 or Y1 <= Y0:
        return
    m = mask[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
    img[Y0:Y1, X0:X1][m] = rgb[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0][m]


def iss_flags(L):
    """The ISS label's flags (``labels.iss``): ([(code, side)] beside the icon, the flags in the label (characters),
    {side: px they take}). flag "globe": a globe flag in the label; "partners": the US and Russian flags, each in
    the label or by the icon on its own side (us_at, ru_at); "none"."""
    from display.modules.flags import flag_char
    mode, beside, inline = L.get("flag"), [], ""
    if mode == "globe":
        inline = flag_char("world")
    elif mode == "partners":
        for c in ("us", "ru"):
            at = L.get(f"{c}_at", "label")
            if at in ("right", "left", "above", "below"):
                beside.append((c, at))
            else:
                inline += flag_char(c)
    pad = {}
    for side in ("right", "left", "above", "below"):
        n = sum(1 for _c, s in beside if s == side)
        if n:
            pad[side] = n * (FLAG_W + 2) if side in ("right", "left") else FLAG_H + 2
    return beside, inline, pad


def _flag_boxes(beside, IX, IY, art):
    """[(code, x, y)]: each flag's top-left beside an icon centred on IX, IY, a pixel clear of its outline; flags on
    the same side sit side by side (centred above or below it)."""
    aw, ah = len(art[0]), len(art)
    L, T = IX - aw // 2, IY - ah // 2
    R, B = L + aw - 1, T + ah - 1
    out = []
    for side in ("right", "left", "above", "below"):
        codes = [c for c, s in beside if s == side]
        total = len(codes) * FLAG_W + (len(codes) - 1) * 2
        for i, c in enumerate(codes):
            if side == "right":
                out.append((c, R + 3 + i * (FLAG_W + 2), IY - FLAG_H // 2))
            elif side == "left":
                out.append((c, L - 2 - FLAG_W - i * (FLAG_W + 2), IY - FLAG_H // 2))
            else:
                out.append((c, IX - total // 2 + i * (FLAG_W + 2), T - 2 - FLAG_H if side == "above" else B + 3))
    return out


def iss_text(text, data, n_crew):
    """The ISS label's own text (``labels.iss.text``), with {exp} the expedition's number and {crew} how many are
    aboard (left out when not known)."""
    s = str(text or "ISS")
    name = str(((data or {}).get("mission") or {}).get("name") or "")
    exp = name.split()[-1] if name and name.split()[-1].isdigit() else ""
    return s.replace("{exp}", exp).replace("{crew}", str(n_crew or "")).strip() or "ISS"


def tag_xy(place, IX, IY, art, tw, w, h, pad=None):
    """Where the AOS/LOS tag's text goes (its left column and the top row of its 7-row backdrop) for an ISS icon
    centred on IX, IY: ``place`` one of TAG_PLACES (beside, above or below the icon, or off a corner), its
    backdrop just outside the icon's black outline. Where that side runs off the map it goes to the other side
    (never pushed back over the ISS); then it's kept on the map. tw: the text's width (_tw, ending in a 1 px gap).
    pad: {side: px} already taken next to the icon on that side (its flags), which the tag goes beyond."""
    place = place if place in TAG_PLACES else "right"
    aw, ah = len(art[0]), len(art)
    L, T = IX - aw // 2, IY - ah // 2                      # the icon's first column and row
    R, B = L + aw - 1, T + ah - 1                          # its last (the outline is one further out)
    p = pad or {}
    L, R, T, B = L - p.get("left", 0), R + p.get("right", 0), T - p.get("above", 0), B + p.get("below", 0)
    vert, _, side = place.partition("_") if place not in ("left", "right") else ("", "", place)
    xs = {"right": R + 3, "left": L - 1 - tw}
    ys = {"above": T - 8, "below": B + 2}
    if side == "right" and xs["right"] + tw > w:
        side = "left"
    elif side == "left" and xs["left"] - 1 < 0:
        side = "right"
    if vert == "above" and ys["above"] < 0:
        vert = "below"
    elif vert == "below" and ys["below"] + 7 > h:
        vert = "above"
    X = xs.get(side, IX - (tw - 1) // 2)
    Y = ys.get(vert, IY - 3)
    return max(1, min(w - tw, X)), max(0, min(h - 8, Y))


def _sprite(img, art, wx, y, v, left, clamp_y=False, outline=True, drawn=None):
    """An icon centred on world x wx, row y (drawn again a world further along when it straddles the map's edge).
    outline: a 1 px black edge round it, so it stays clear on a busy map; clamp_y keeps the icon itself in.
    drawn: a bool mask the size of img, set where the icon (with its edge) went."""
    rgb, mask = _sprite_arrays(art, outline)
    hh, ww = mask.shape
    pad = 1 if outline else 0
    h, w = img.shape[:2]
    y0 = int(round(y - (hh - 2 * pad) / 2)) - pad
    if clamp_y:
        y0 = max(-pad, min(h - hh + pad, y0))
    x = int(round((wx - left) % v.W - (ww - 2 * pad) / 2)) - pad
    for xx in (x, x - v.W, x + v.W):
        X0, X1, Y0, Y1 = max(0, xx), min(w, xx + ww), max(0, y0), min(h, y0 + hh)
        if X1 <= X0 or Y1 <= Y0:
            continue
        m = mask[Y0 - y0:Y1 - y0, X0 - xx:X1 - xx]
        img[Y0:Y1, X0:X1][m] = rgb[Y0 - y0:Y1 - y0, X0 - xx:X1 - xx][m]
        if drawn is not None:
            drawn[Y0:Y1, X0:X1] |= m


# --- the world: lines once per size, night every SHADE_EVERY --------------------------------------------------
_world = {"lines": None}


def world_lines():
    """{"coast": [flat lon/lat list, …], "borders": [...]} (assets/maps/world_110m.json), read once."""
    if _world["lines"] is None:
        try:
            with open(ASSET, encoding="utf-8") as f:
                d = json.load(f)
            _world["lines"] = {k: [np.asarray(l, float).reshape(-1, 2) for l in d.get(k) or []]
                               for k in ("coast", "borders")}
        except (OSError, ValueError):
            _world["lines"] = {"coast": [], "borders": []}
    return _world["lines"]


def _polylines(img, v, lines, rgb):
    """Lines of lon/lat points onto the world raster (rows 0..mh, x round the world)."""
    segs = [np.c_[p[:-1], p[1:]] for p in lines if len(p) > 1]
    if not segs:
        return
    s = np.concatenate(segs)
    s = s[np.abs(s[:, 2] - s[:, 0]) <= 180]              # (the data's own date-line breaks)
    x0, x1 = v.wx(s[:, 0]), v.wx(s[:, 2])
    y0, y1 = v.y(s[:, 1]) - v.y0, v.y(s[:, 3]) - v.y0
    xs, ys, _ = _samples(x0, y0, x1, y1)
    _plot(img, xs, ys, rgb, None, 0, v.W)


def _graticule(img, v, grid):
    """Lines of latitude and longitude every ``step`` degrees (or only the equator and tropics: lines "special"),
    faint, the equator a little brighter."""
    step = max(10, min(90, int(grid.get("step", 30))))
    special = {0.0: (60, 80, 110), 23.44: (45, 60, 85), -23.44: (45, 60, 85), 66.56: (40, 55, 80), -66.56: (40, 55, 80)}
    lats = {} if grid.get("lines") == "special" else {float(a): (32, 44, 66) for a in range(-90 + step, 90, step)}
    lats.update(special if grid.get("lines") in ("special", "both") else {0.0: (60, 80, 110)})
    for la, rgb in lats.items():
        y = int(round(float(v.y(la)) - v.y0))
        if 0 <= y < v.mh:
            img[y, ::2] = rgb
    if grid.get("lines") != "special":
        for lo in range(-180, 180, step):
            x = int(round(float(v.wx(lo)))) % v.W
            img[::2, x] = (32, 44, 66)


_bases: dict = {}


def ground_sites(russian=True, chinese=False):
    """[(name, (lat, lon), net)] of the ground stations on the full map."""
    out = [(n, p, "nasa") for n, p in NASA_GROUND.items()]
    if russian:
        out += [(n, p, "ru") for n, p in RU_GROUND.items()]
    if chinese:
        out += [(n, p, "cn") for n, p in CN_GROUND.items()]
    return out


def base(v, full, big=False, russian=True, chinese=False, stations=True, borders=True, grid=None):
    """The world W×mh: ocean, coastlines and, for the full map, borders (and a grid of latitudes and longitudes) and
    the ground stations (they never move); uint8, cached per size. Returns (image, mask of the icons' pixels: the
    trail goes under them)."""
    key = (v.key, full, big, russian, chinese, stations, borders, json.dumps(grid))
    hit = _bases.get(key)
    if hit is None:
        img = np.empty((v.mh, v.W, 3), np.uint8)
        img[:] = OCEAN
        L = world_lines()
        if full and grid:
            _graticule(img, v, grid)
        if full and borders:
            _polylines(img, v, L["borders"], BORDER)
        _polylines(img, v, L["coast"], COAST)
        icons = np.zeros((v.mh, v.W, 3), np.uint8)
        mask = np.zeros((v.mh, v.W), bool)
        if full and stations:
            art = ICONS[big]
            for _n, (la, lo), net in ground_sites(russian, chinese):
                _sprite(icons, art[GROUND_ART[net]], float(v.wx(lo)), float(v.y(la)) - v.y0, v, 0, drawn=mask)
        img[mask] = icons[mask]
        if len(_bases) > 8:
            _bases.clear()
        hit = _bases[key] = (img, mask)
    return hit


def daylight(v, t, night=NIGHT):
    """How bright each pixel of the world is at time t: 1 in daylight, NIGHT at night, smooth through twilight (as the
    weather maps' shade_day_night). (mh, W, 1) float32."""
    jd = julian(t)
    s = sun_dir(jd)
    dec = math.asin(max(-1.0, min(1.0, s[2])))
    ra = math.atan2(s[1], s[0])
    lon = np.radians((np.arange(v.W) + 0.5) / v.W * 360.0 - 180.0)
    lat = np.radians(v.top - (np.arange(v.mh) + 0.5) / v.mh * (v.top - v.bot))
    ha = gmst(jd) + lon - ra
    # the sine of the Sun's elevation (linear in it through twilight: no arcsin needed)
    se = (np.sin(lat)[:, None] * math.sin(dec)).astype(np.float32) \
        + (np.cos(lat)[:, None] * math.cos(dec)).astype(np.float32) * np.cos(ha).astype(np.float32)[None, :]
    lo = math.sin(math.radians(-TWILIGHT))
    f = np.clip((se - lo) / -lo, 0.0, 1.0)
    return (night + (1.0 - night) * f)[..., None]


_samples_memo: dict = {}


def _sample(v, g, north_first):
    """A lat/lon grid (rows of latitude, columns of longitude from -180, or from 0 for the aurora) at each world
    pixel: (mh, W) float32 0..255, kept per view and grid (only the night side changes every SHADE_EVERY)."""
    key = (v.key, id(g), north_first)
    hit = _samples_memo.get(key)
    if hit is not None and hit[0] is g:
        return hit[1]
    if len(_samples_memo) > 8:
        _samples_memo.clear()
    out = _sample_now(v, g, north_first)
    _samples_memo[key] = (g, out)
    return out


def _sample_now(v, g, north_first):
    gh, gw = g.shape
    lons = (np.arange(v.W) + 0.5) / v.W * 360.0 - 180.0
    lats = v.top - (np.arange(v.mh) + 0.5) / v.mh * (v.top - v.bot)
    if north_first:                                       # lights: lat 90 at row 0, lon -180 at column 0
        rows = ((90.0 - lats) / 180.0 * gh).astype(int).clip(0, gh - 1)
        cols = ((lons + 180.0) / 360.0 * gw).astype(int).clip(0, gw - 1)
    else:                                                 # aurora: lat -90 at row 0, lon 0 at column 0
        rows = (np.round(lats) + 90).astype(int).clip(0, gh - 1)
        cols = (np.floor(lons) % 360).astype(int) % gw
    return g[rows][:, cols].astype(np.float32)


_shaded: dict = {}


def shaded(v, full, t, big=False, ex=None, stations=True):
    """The world with day and night (and on the full map the city lights and the aurora on the night side; the icons
    stay lit), recomputed every SHADE_EVERY: (image, icon mask)."""
    slot = int(t // SHADE_EVERY)
    russian = bool(full and _on(ex, "russian"))
    chinese = bool(full and _on(ex, "chinese") and _on(ex, "tiangong"))
    lights = grid("lights") if full and _on(ex, "lights") else None
    aurora = grid("aurora") if full and _on(ex, "aurora") else None
    night = 1.0 - float((ex.get("night") or {}).get("darkness", 1 - NIGHT)) if full and ex and "night" in ex else NIGHT
    if full and ex and "night" in ex and not _on(ex, "night"):
        night = 1.0
    night = max(0.05, min(1.0, night))
    gr = dict(ex["grid"]) if full and _on(ex, "grid") else None
    borders = not (full and ex and "borders" in ex and not _on(ex, "borders"))
    key = (v.key, full, big, russian, chinese, stations, borders, json.dumps(gr))
    tag = (slot, id(lights), id(aurora), night,
           json.dumps([(ex or {}).get("lights"), (ex or {}).get("aurora")], default=str))
    hit = _shaded.get(key)
    if hit is None or hit[0] != tag:
        if len(_shaded) > 8:
            _shaded.clear()
        img, mask = base(v, full, big, russian, chinese, stations, borders, gr)
        day = daylight(v, slot * SHADE_EVERY, night)
        lit = img * day
        dark = ((1.0 - day[..., 0]) / max(1e-6, 1.0 - night)).clip(0, 1) if night < 1.0 else np.zeros(day.shape[:2], np.float32)
        for g, north, k, rgb, f in ((lights, True, "lights", LIGHTS, lambda x: np.clip((x - 0.18) * 1.6, 0, 1)),
                                    (aurora, False, "aurora", AURORA, lambda x: np.clip((x * 255 - 4) / 30, 0, .75))):
            if g is None:
                continue
            strength = float((ex.get(k) or {}).get("strength", 0.8))
            fk = (v.key, id(g), k)
            hit = _samples_memo.get(fk)
            if hit is None or hit[0] is not g:                   # the brightness of each pixel's lights / aurora
                hit = _samples_memo[fk] = (g, f(_sample(v, g, north) / 255.0).astype(np.float32))
            a = (hit[1] * (dark if north else np.clip(dark * 1.5, 0, 1)) * strength)[..., None]
            lit = lit * (1 - a) + np.asarray(rgb, np.float32) * a
        lit = (lit + 0.5).astype(np.uint8)
        lit[mask] = img[mask]
        hit = _shaded[key] = (tag, (lit, mask))
    return hit[1]


def _window(dst, src, left, W):
    """Copy src's columns from world x ``left`` round the world into dst (as wide as the map): slices, no copies of
    the whole world."""
    w = dst.shape[1]
    x, start = 0, left % W
    while x < w:
        n = min(w - x, W - start)
        dst[:, x:x + n] = src[:, start:start + n]
        x, start = x + n, 0


# --- the maps --------------------------------------------------------------------------------------------------
_arrays: dict = {}


def _array(track):
    """A track as an (n, 4) float array, made once per track."""
    hit = _arrays.get(id(track))
    if hit is None or hit[0] is not track:
        if len(_arrays) > 4:
            _arrays.clear()
        hit = _arrays[id(track)] = (track, np.asarray(track, float).reshape(-1, 4))
    return hit[1]


def _trail(img, v, left, track, t, pos, sunlit=None, colour=TRAIL, span=TRAIL_SEC, peak=0.9, floor=0.08):
    """The last ``span`` of a track up to pos, fading into the past (newest drawn last, on top). sunlit: the shadow's
    colour name (the trail warm in sunlight, that colour in Earth's shadow); None: all ``colour``."""
    if not track:
        return
    arr = _array(track)
    i, j = np.searchsorted(arr[:, 0], [t - span, t], side="right")
    a = np.vstack([arr[i:j], [[t, pos[0], pos[1], arr[j - 1, 3] if j else 1]]])
    if len(a) < 2:
        return
    age = (t - a[1:, 0]) / span
    alpha = np.maximum(floor, peak * (1 - age))
    x, y = v.wx(a[:, 2]), v.y(a[:, 1])
    if sunlit is None:
        _seg_line(img, v, left, x[:-1], y[:-1], x[1:], y[1:], colour, alpha)
        return
    lit = a[1:, 3] > 0
    for sel, rgb in ((lit, SUNLIT), (~lit, SHADOW.get(sunlit, SHADOW["blue"]))):
        if sel.any():
            _seg_line(img, v, left, x[:-1][sel], y[:-1][sel], x[1:][sel], y[1:][sel], rgb, alpha[sel])


def _ahead(img, v, left, track, t, pos, minutes):
    """The track ahead, dashed, fading out towards ``minutes`` from now."""
    if not track:
        return
    arr = _array(track)
    span = max(5.0, float(minutes)) * 60
    i, j = np.searchsorted(arr[:, 0], [t, t + span], side="right")
    a = np.vstack([[[t, pos[0], pos[1], 1]], arr[i:j]])
    if len(a) < 2:
        return
    x0 = np.asarray(v.wx(a[:-1, 2]), float)
    x1 = _unwrap(x0, np.asarray(v.wx(a[1:, 2]), float), v.W)
    xs, ys, idx = _samples(x0, v.y(a[:-1, 1]), x1, v.y(a[1:, 1]))
    keep = (np.arange(xs.size) // 2) % 2 == 0
    al = np.maximum(0.12, 0.55 * (1 - (a[1:, 0] - t) / span))[idx]
    _plot(img, xs[keep], ys[keep], AHEAD, al[keep], left, v.W)


def reserve_for(box, h):
    """The rows a caption box (x0, y0, x1, y1) takes from a zone h tall: (top, bottom), from the side it's on."""
    if not box:
        return None
    x0, y0, x1, y1 = box
    return (int(y1) + 1, 0) if y0 < h - y1 else (0, h - int(y0) + 1)


# --- text ------------------------------------------------------------------------------------------------------
def _tw(s):
    from display.modules.mappanels import text_w
    return text_w(s)


def _text(img, x, y, s, rgb):
    """Text with its top-left at x, y."""
    from display.modules.mappanels import draw_text
    draw_text(img, x, y, s, rgb)


def _num(live, k, d=0):
    try:
        return float(live["values"][k]["v"]), True
    except (KeyError, TypeError, ValueError, IndexError):
        return 0.0, False


def telemetry_lines(live, rows):
    """[(label, value)] for the old telemetry panel (NASA ISS Live readings; -- when the hub has none)."""
    out = []
    for k in rows:
        if k == "arrays":
            p, ok1 = _num(live, "sarj_p")
            s, ok2 = _num(live, "sarj_s")
            out.append(("ARRAYS", f"P {p:.0f}  S {s:.0f}" if ok1 and ok2 else "--"))
            continue
        if k == "ku":
            try:
                cal = str(live["values"]["ku_tx"]["cal"] or "")
            except (KeyError, TypeError):
                cal = ""
            out.append(("KU-BAND", "TX" if cal.upper() == "YES" else "OFF" if cal else "--"))
            continue
        v, ok = _num(live, k)
        label, fmt = {"cabin_p": ("CABIN", "{:.0f} MMHG"), "cabin_t": ("TEMP", "{:.1f} C"),
                      "o2": ("O2", "{:.0f} MMHG"), "co2": ("CO2", "{:.1f} MMHG"), "gyros": ("GYROS", "{:.0f}/4 ONLINE"),
                      "water": ("WATER", "{:.0f}% CLEAN")}.get(k, (k.upper(), "{}"))
        out.append((label, fmt.format(v) if ok else "--"))
    return out


def docked_lines(data, now, days=True):
    from display.modules.flags import flag_char
    out = []
    for d in (data or {}).get("docked") or []:
        if not isinstance(d, dict):
            continue
        n = str(d.get("name") or "").upper()
        fc = str(d.get("flag") or "")
        f = flag_char(fc) if fc else ""
        try:
            dd = f"  {int((now - float(d['docked'])) // 86400)}D" if days and d.get("docked") else ""
        except (TypeError, ValueError):
            dd = ""
        out.append(f"{f}{n}{dd}")
    return out


def _panel(img, corner, y, lines, head=None):
    """The webcam map's see-through dark panel of tiny text in a top corner from row y; returns the row under it."""
    h, w = img.shape[:2]
    rows = ([(head, None)] if head else []) + lines
    lw = max((_tw(a) for a, b in rows if b is not None), default=0)
    width = max(_tw(a) + (lw - _tw(a) + 3 + _tw(b) if b is not None else 0) for a, b in rows) + 4
    width = min(width, w - 2)
    bh = 7 * len(rows) + 2
    x0 = 0 if corner == "tl" else w - width
    img[y:y + bh, x0:x0 + width] = (img[y:y + bh, x0:x0 + width] * 0.28).astype(np.uint8)
    for i, (a, b) in enumerate(rows):
        yy = y + 1 + 7 * i
        if b is None:
            _text(img, x0 + 2, yy, a, DIM_TXT if (head and i == 0) else TXT)
        else:
            _text(img, x0 + 2, yy, a, DIM_TXT)
            _text(img, x0 + 2 + lw + 3, yy, b, TXT)
    return y + bh + 2


# --- the Space Map's own layers --------------------------------------------------------------------------------
def _circle(lat, lon, rho, n=120):
    """Points (lats, lons) round the small circle rho degrees from (lat, lon)."""
    p, l0, r = math.radians(lat), math.radians(lon), math.radians(rho)
    th = np.linspace(0, 2 * math.pi, n)
    la = np.arcsin(np.sin(p) * math.cos(r) + np.cos(p) * math.sin(r) * np.cos(th))
    lo = l0 + np.arctan2(np.sin(th) * math.sin(r) * math.cos(p), math.cos(r) - math.sin(p) * np.sin(la))
    return np.degrees(la), (np.degrees(lo) + 540.0) % 360.0 - 180.0


def footprint_deg(alt_km, el_deg):
    """How far (degrees of arc) from the point below a satellite at alt_km it is seen el_deg above the horizon."""
    e = math.radians(el_deg)
    return math.degrees(math.acos(R_KM / (R_KM + alt_km) * math.cos(e)) - e)


def _ring(img, v, left, lat, lon, rho, rgb, alpha=0.55):
    la, lo = _circle(lat, lon, rho)
    x, y = v.wx(lo), v.y(la)
    _seg_line(img, v, left, x[:-1], y[:-1], x[1:], y[1:], rgb, np.full(len(x) - 1, alpha, np.float32))


def _terminator(img, v, left, t):
    from common.orbit import sub_point
    jd = julian(t)
    la, lo = sub_point(sun_dir(jd), jd)
    _ring(img, v, left, la, lo, 90.0, (255, 190, 90), 0.5)


def _screen(v, left, wx_, y_):
    return int(round((wx_ - left) % v.W)), int(round(y_))


# --- the maps --------------------------------------------------------------------------------------------------
def render(w, h, data, t, full=True, fixed=False, reserve=None, extras=None, live=None, mapset=None, view=None,
           avoid=None):
    """The map at time t (as filmed): (image (h, w, 3) uint8, paths for the moving data, or []). full: the full map
    (borders, stations, relays, links, the detailed ISS, the extras); else the small map (coastlines, trail, the
    tiny ISS). fixed (full map only): the world held still with the ISS crossing it; else centred on the ISS.
    reserve: the caption's rows (see View); avoid: its box. extras: extras_of(cam). live: the hub's /api/iss/live.
    mapset: the Space Map's ``map`` settings (links, relays, labels, names, satellites, panels, units; None: the
    webcam's map, drawn as it always was). view: its view for this size (view_of)."""
    from display.modules import mappanels, maplabels, mapsats
    from display.modules.space import position
    if w < 8 or h < 4:
        return np.zeros((max(0, h), max(0, w), 3), np.uint8), []
    smap = mapset is not None
    ms = mapset if isinstance(mapset, dict) else {}
    ex = (extras if extras is not None else extras_of(None, smap)) if full else {}
    size = ms.get("icons") if ms.get("icons") in ("small", "big") else "auto"
    big = bool(full and (size == "big" or (size == "auto" and w >= BIG_W)))
    art = ICONS[big]
    track = (data or {}).get("track") if isinstance(data, dict) else None
    pos = position(track, t) if track else None
    v = View(w, h, follow=not (full and fixed), small=not full, reserve=reserve if full else None,
             opts=view if full else None, iss_lat=pos[0] if pos else None)
    left = v.left(pos[1] if pos else None)
    img = np.empty((h, w, 3), np.uint8)
    img[:] = OUTSIDE
    ground_mode = ms.get("ground") if ms.get("ground") in ("always", "linked", "hidden") else "always"
    world, mask = shaded(v, full, t, big, ex, stations=ground_mode == "always")
    sr, rr = v.rows()
    band = img[sr]
    _window(band, world[rr], left, v.W)                    # the world from ``left``, round the date line
    paths = []
    no_iss = pos is None and full and bool(ms.get("_home"))  # (Satellites Overhead draws on without the ISS's track)
    if pos is None and not no_iss:
        return img, paths
    if no_iss:                                             # (somewhere never in view: home's antipode)
        pos = (-float(ms["_home"][0]), (float(ms["_home"][1]) + 360.0) % 360.0 - 180.0)
    if full and _on(ex, "terminator"):
        _terminator(img, v, left, t)
    if full and _on(ex, "ahead") and not no_iss:
        _ahead(img, v, left, track, t, pos, ex["ahead"].get("minutes", 45))
    if (not full or _on(ex, "trail")) and not no_iss:
        span = max(5.0, float((ex.get("trail") or {}).get("minutes", 90))) * 60 if full else TRAIL_SEC
        _trail(img, v, left, track, t, pos, span=span,
               sunlit=(ex["sunlit"].get("shadow", "blue") if _on(ex, "sunlit") else None))
    if full:                                               # the stations back over the trail
        m = np.empty(band.shape[:2] + (1,), bool)
        _window(m, mask[rr][..., None], left, v.W)
        src = np.empty_like(band)
        _window(src, world[rr], left, v.W)
        np.copyto(band, src, where=m)
    ix, iy = float(v.wx(pos[1])), float(v.y(pos[0]))
    if not full:
        _sprite(img, SMALL_ART, ix, iy, v, left, clamp_y=True)
        return img, paths
    IX, IY = _screen(v, left, ix, iy)
    hw = lambda a: (len(a[0]) // 2 + 1, len(a) // 2 + 1)
    icons, label_items = [], []
    lab = {k: maplabels.labels_of(ms, k) for k in maplabels.KINDS}
    rgb_of = lambda k: mapsats.hex_rgb(lab[k]["colour"], (200, 200, 200))
    flag = lambda k, code: (__import__("display.modules.flags", fromlist=["x"]).flag_char(code) if lab[k]["flag"] else "")
    people = [p for p in (data or {}).get("people") or [] if isinstance(p, dict)]

    def label(kind, key, default, anchor, half, code=None, prio=5, extra="", optional=False, pad=None, flags=None):
        if not smap:
            return
        L = lab[kind]
        name = maplabels.name_for(ms, key, default).upper() if L["name"] else ""
        text = (flags if flags is not None else (flag(kind, code) if code else "")) + name + extra
        if text.strip():
            label_items.append({"text": text, "anchor": anchor, "half": half, "pos": L["pos"], "prio": prio,
                                "rgb": rgb_of(kind), "edge": L.get("edge", "outline"), "optional": optional,
                                "pad": pad})

    # satellite layers (under everything else: there can be thousands)
    counts, up = {}, {}
    home = ms.get("_home") if smap else None              # (Satellites Overhead: the wall's home in the middle)
    ov = overhead_of(ms) if home else None
    observer = (float(home[0]), float(home[1]), float(ov["min_el"]), float(ov["outside"])) if home else None
    if smap and isinstance(ms.get("sats"), dict):
        counts = mapsats.draw(img, v, left, t, ms, DATA_DIR, pos, (ix, iy), paths, label_items, lab["sats"],
                              rgb_of("sats"), icons, observer=observer, up=up)
        m = np.empty(band.shape[:2] + (1,), bool)          # (the ground stations stay on top)
        _window(m, mask[rr][..., None], left, v.W)
        src = np.empty_like(band)
        _window(src, world[rr], left, v.W)
        np.copyto(band, src, where=m)
    if home:                                               # the horizon for ov's layer, and home
        hx, hy = float(v.wx(home[1])), float(v.y(home[0]))
        if ov.get("ring", True):
            _ring(img, v, left, home[0], home[1], footprint_deg(overhead_alt(ov), float(ov["min_el"])), HORIZON, 0.6)
        if ov.get("home", True):
            _sprite(img, HOME_ART, hx, hy, v, left)
            HX, HY = _screen(v, left, hx, hy)
            icons.append((HX - 3, HY - 3, HX + 3, HY + 3))
    # Tiangong and its link
    css = (data or {}).get("css")
    cpos = position(css, t) if (css and _on(ex, "tiangong")) else None
    if cpos:
        if ex["tiangong"].get("trail", True):
            _trail(img, v, left, css, t, cpos, colour=CSS, peak=0.5, floor=0.05)
        cx, cy = float(v.wx(cpos[1])), float(v.y(cpos[0]))
        _sprite(img, art["css"], cx, cy, v, left)
        CX, CY = _screen(v, left, cx, cy)
        icons.append((CX - hw(art["css"])[0], CY - hw(art["css"])[1], CX + hw(art["css"])[0], CY + hw(art["css"])[1]))
        if smap:
            tg = [p for p in people if not p.get("iss") and "shenzhou" in str(p.get("craft", "")).lower()]
            crew = f" {len(tg)} CREW" if lab["tiangong"].get("crew") and tg else ""
            label("tiangong", "Tiangong", "Tiangong", (CX, CY), hw(art["css"]), "cn", 1, crew)
        elif ex["tiangong"].get("label", True) and big:    # (the webcam's map: its name, as it always was)
            X = int(round((cx - left) % v.W))
            mappanels.draw_text_outlined(img, min(w - _tw("TIANGONG") - 1, X + 10), max(0, int(cy) - 11), "TIANGONG",
                                         (230, 110, 90))
        if _on(ex, "footprint") and ex["footprint"].get("tiangong"):
            _ring(img, v, left, cpos[0], cpos[1], footprint_deg(390.0, float(ex["footprint"].get("elevation", 10))),
                  CSS, 0.45)
    # the links
    aos = signal_at(live, t) if _on(ex, "signal") else None
    relays = valid_relays((data or {}).get("relays")) or FALLBACK_RELAYS
    lks = links(pos, relays, aos, russian=_on(ex, "russian")) if not no_iss else []
    if cpos and _on(ex, "chinese"):
        lks += cn_links(cpos, relays)
    used = set()
    for lk in lks:
        st = link_style(ms, lk["net"])
        start = (cx, cy) if lk["net"] == "cn" else (ix, iy)
        xs, ys = [start[0]], [start[1]]
        for la, lo in lk["pts"][1:]:
            xs.append(float(_unwrap(np.array([xs[-1]]), np.array([float(v.wx(lo))]), v.W)[0]))
            ys.append(float(v.y(la)))
        legs = lk.get("legs") or list(range(len(xs) - 1))
        cols = [st["rgb"][min(1, g)] for g in legs]
        line = max(0.0, min(1.0, float(st.get("line", 1.0))))
        for k, col in enumerate(cols):
            if line > 0:
                _seg_line(img, v, left, xs[k], ys[k], xs[k + 1], ys[k + 1], col, None if line >= 1 else [line])
        lk["ground"] = next((n for n, p in {**NASA_GROUND, **RU_GROUND, **CN_GROUND}.items() if p == lk["pts"][-1]), None)
        lk["relay"] = len(lk["pts"]) > 2
        used.add(lk.get("via"))
        used.add(lk["ground"])
        live_ok = aos is True if lk["net"] == "nasa" else True
        if live_ok and _on(ex, "flow") and st.get("style") != "still":
            paths.append({"kind": "link", "xs": xs, "ys": ys, "cols": cols, "left": left, "W": v.W, "style": st["style"],
                          "speed": st.get("speed", ex["flow"].get("speed")), "direction": st.get("direction", ex["flow"].get("direction"))})
    # the relays and ground stations (their icons, flags and names)
    relay_mode = ms.get("relays") if ms.get("relays") in RELAY_SHOW else "always"
    for r in relays:
        if (r.get("net") == "luch" and not _on(ex, "russian")) or (r.get("net") == "tianlian" and not (cpos and _on(ex, "chinese"))):
            continue
        if relay_mode == "linked" and r.get("name") not in used:
            continue
        a = art[r.get("net")] if r.get("net") in art else art["tdrs"]
        rx, ry = float(v.wx(r["lon"])), float(v.y(r.get("lat") or 0.0))
        _sprite(img, a, rx, ry, v, left)
        RX, RY = _screen(v, left, rx, ry)
        icons.append((RX - hw(a)[0], RY - hw(a)[1], RX + hw(a)[0], RY + hw(a)[1]))
        label("relays", r.get("name"), r.get("name") or "", (RX, RY), hw(a), RELAY_FLAG.get(r.get("net")), 3, optional=True)
    for n, (la, lo), net in ground_sites(_on(ex, "russian"), bool(cpos and _on(ex, "chinese"))):
        if ground_mode == "hidden" or (ground_mode == "linked" and n not in used):
            continue
        a = art[GROUND_ART[net]]
        gx, gy = float(v.wx(lo)), float(v.y(la))
        if ground_mode == "linked":
            _sprite(img, a, gx, gy, v, left)
        GX, GY = _screen(v, left, gx, gy)
        icons.append((GX - hw(a)[0], GY - hw(a)[1], GX + hw(a)[0], GY + hw(a)[1]))
        label("ground", n, n, (GX, GY), hw(a), GROUND_FLAG.get(n), 4, optional=True)
    # the Sun and Moon, the footprint, launch pads
    if _on(ex, "sunmoon"):
        from common.orbit import moon_dir, sub_point
        jd = julian(t)
        for vec, a in ((sun_dir(jd), SUN_ART), (moon_dir(jd), MOON_ART)):
            la, lo = sub_point(vec, jd)
            sx, sy = float(v.wx(lo)), float(v.y(la))
            if ex["sunmoon"].get("lines") and not no_iss:
                _seg_line(img, v, left, ix, iy, sx, sy, PAL["Y" if a is SUN_ART else "M"], [0.25])
            _sprite(img, a, sx, sy, v, left)
            SX, SY = _screen(v, left, sx, sy)
            icons.append((SX - 3, SY - 3, SX + 3, SY + 3))
    if _on(ex, "footprint") and not no_iss:
        _ring(img, v, left, pos[0], pos[1], footprint_deg(ALT_KM, float(ex["footprint"].get("elevation", 10))),
              (255, 214, 120), 0.6)
    if _on(ex, "launches"):
        for x in [x for x in (data or {}).get("launches") or [] if isinstance(x, dict) and x.get("lat") is not None
                  and x.get("net", 0) > t - 1800][:max(1, int(ex["launches"].get("count", 3)))]:
            px_, py_ = float(v.wx(x["lon"])), float(v.y(x["lat"]))
            _sprite(img, PAD_ART, px_, py_, v, left)
            PX, PY = _screen(v, left, px_, py_)
            icons.append((PX - 3, PY - 3, PX + 3, PY + 3))
            dt = x["net"] - t
            from display.modules.mappanels import _countdown
            label("launch", x.get("name"), str(x.get("rocket") or x.get("name") or "").split()[0] if lab["launch"]["name"] else "",
                  (PX, PY), (3, 3), x.get("flag"), 2, (" T-" + _countdown(dt)) if dt > 0 else "")
            paths.append({"kind": "pulse", "x": PX, "y": PY, "rgb": PAL["P"]})
    # the panels (where they go first: the labels keep off them)
    top, bottom = (reserve or (0, 0))
    placed = []
    if smap:
        ctx = {"data": data, "live": live, "t": t, "units": mappanels.units_of(ms), "pos": pos, "aos": aos,
               "links": lks, "css_pos": cpos, "sat_counts": counts, "sats_up": up if home else None}
        vid = mappanels.video_of(ms)
        placed = mappanels.layout(mappanels.panels_of(ms, ex), ctx, w, h, top, bottom, avoid,
                                  video=vid if vid.get("on") and ms.get("_video_up") else None)
        for p, m, x, y in placed:
            if m.get("video"):                             # where the live video goes (iss_panel draws it)
                paths.append({"kind": "video", "x": x, "y": y, "w": m["w"], "h": m["h"],
                              "border": bool(p.get("border", True)), "tag": bool(p.get("tag", True))})
    elif w >= 280:                                         # the webcam's map: its two panels, as they always were
        ys_ = {"tl": top, "tr": top}
        if _on(ex, "telemetry"):
            c = ex["telemetry"].get("corner", "tr") if ex["telemetry"].get("corner") in ys_ else "tr"
            rows = [r for r in ex["telemetry"].get("rows") or [] if r in TELEMETRY_ROWS]
            if rows:
                ys_[c] = _panel(img, c, ys_[c], telemetry_lines(live, rows))
        if _on(ex, "docked"):
            lines = docked_lines(data, t, ex["docked"].get("days", True))
            if lines:
                c = ex["docked"].get("corner", "tr") if ex["docked"].get("corner") in ys_ else "tr"
                ys_[c] = _panel(img, c, ys_[c], [(s, None) for s in lines], head="DOCKED")
    pboxes = mappanels.boxes(placed)
    # the ISS's own label, then all the labels where they cover least
    iss_half = hw(art["iss"])
    iss_up = not home or (-iss_half[0] < IX < w + iss_half[0] and -iss_half[1] < IY < h + iss_half[1])
    if not iss_up:                                         # (Satellites Overhead: the ISS only while it's in view)
        aos = None
    else:
        icons.append((IX - iss_half[0], IY - iss_half[1], IX + iss_half[0], IY + iss_half[1]))
    crew = [p for p in people if p.get("iss")]
    n_crew = len(crew) or ((data or {}).get("crew") or {}).get("count")
    side_flags, in_label, pad = iss_flags(lab["iss"]) if smap else ([], "", {})
    flag_boxes = []
    if iss_up and side_flags:                              # the US and Russian flags beside the icon
        flag_boxes = _flag_boxes(side_flags, IX, IY, art["iss"])
        icons.extend((x - 1, y - 1, x + FLAG_W + 1, y + FLAG_H + 1) for _c, x, y in flag_boxes)
    tag = _on(ex, "signal") and ex["signal"].get("tag", True) and aos is not None
    if tag:                                                # (the AOS/LOS tag's room)
        icon_tag = ex["signal"].get("style") == "icon"
        lab_ = "AOS" if aos else "LOS"
        tw_ = len(AOS_ART[0]) + 1 if icon_tag else _tw(lab_)
        tside = ex["signal"].get("place") or "auto"
        if tside == "auto":                                # (the Space Map's ISS label is on the right)
            tside = "below" if smap and lab["iss"].get("name") else "right"
        X, Y = tag_xy(tside, IX, IY, art["iss"], tw_, w, h, pad)
        icons.append((X - 1, Y, X + tw_, Y + 7))
        if tside in ("right", "left", "above", "below"):  # (a label on that side goes beyond it)
            pad = {**pad, tside: pad.get(tside, 0) + (tw_ + 2 if tside in ("right", "left") else 9)}
    if iss_up:
        L_ = lab["iss"]
        text = iss_text(L_.get("text"), data, n_crew)
        label("iss", "ISS", text, (IX, IY), iss_half, None, 0,
              f" {n_crew} CREW" if L_.get("crew") and n_crew else "", pad=pad, flags=in_label)
    if smap:
        obstacles = icons + pboxes + ([tuple(avoid)] if avoid else [])
        maplabels.draw(img, maplabels.place(label_items, obstacles, w, h))
        mappanels.draw(img, placed)
    if tag:
        if icon_tag:
            a = AOS_ART if aos else LOS_ART
            rgb, m = _sprite_arrays(a, True)
            _stamp(img, rgb, m, X - 1, Y + 1)
        else:
            mappanels.draw_text_outlined(img, X, Y + 1, lab_, AOS_C if aos else LOS_C)
    from display.modules import flags as flagmod
    for code, x, y in flag_boxes:
        pic = flagmod.picture(code)
        _stamp(img, np.pad(pic, ((1, 1), (1, 1), (0, 0))), mappanels.grow(np.pad(np.ones(pic.shape[:2], bool), 1)),
               x - 1, y - 1)
    if iss_up:
        _sprite(img, art["iss"], ix, iy, v, left, clamp_y=not home)   # (last: nothing ever covers the ISS)
    if pboxes:
        solid = [b for (q, m, _x, _y), b in zip(placed, pboxes) if m.get("video")]
        seen = [(*b, 1 - max(0.0, min(1.0, float(q.get("opacity", 0.72))))) for (q, m, _x, _y), b in zip(placed, pboxes)
                if not m.get("video")]
        for p in paths:
            if p.get("kind") == "pulse":
                p["skip"] = pboxes
            elif p.get("kind") != "video":                 # the data runs on under see-through panels, as dim as the
                p["skip"], p["under"] = solid, seen        # line shows there (_put)
    return img, paths


# --- the moving data (drawn every frame over the cached map) ----------------------------------------------------
def _keep(px, py, skip):
    ok = np.ones(px.shape, bool)
    for x0, y0, x1, y1 in skip or ():
        ok &= ~((px >= x0) & (px < x1) & (py >= y0) & (py < y1))
    return ok


def _along(xs, ys, s, cum, seg):
    k = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(seg) - 1)
    f = (s - cum[k]) / np.maximum(seg[k], 1e-6)
    return xs[k] + (xs[k + 1] - xs[k]) * f, ys[k] + (ys[k + 1] - ys[k]) * f, k


PANEL_INK = 100                 # a pixel this bright under a panel is its text (the map there is darkened)


def _put(buf, p, px, py, col, alpha=None):
    """Points of the moving data onto the map: kept off the video, and under a see-through panel dimmed by its
    opacity and kept off its text."""
    sx, sy = np.rint((px - p["left"]) % p["W"]), np.rint(py)
    ok = _keep(sx, sy, p.get("skip"))
    under = p.get("under")
    if under:
        a = np.broadcast_to(np.float32(1.0) if alpha is None else np.asarray(alpha, np.float32), px.shape).copy()
        h, w = buf.shape[:2]
        dimmed = False
        for x0, y0, x1, y1, k in under:
            inb = ok & (sx >= x0) & (sx < x1) & (sy >= y0) & (sy < y1)
            if inb.any():
                xi, yi = sx[inb].astype(np.int64), sy[inb].astype(np.int64)
                on = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
                ink = np.zeros(xi.shape, bool)
                ink[on] = buf[yi[on], xi[on]].max(axis=1) > PANEL_INK
                idx = np.flatnonzero(inb)
                ok[idx[ink]] = False
                a[idx] *= k
                dimmed = True
        alpha = a if (dimmed or alpha is not None) else None
    if alpha is not None:
        alpha = np.asarray(alpha, np.float32)[ok]
    if ok.any():
        _plot(buf, px[ok], py[ok], col, alpha, p["left"], p["W"])


def flow(buf, paths, now, ex, big=False):
    """The data moving along the links (drawn every frame over the map), in each network's style: dots, dashes,
    comets (a bright head with a fading tail) or a pulse of light running along the line; down (ISS to ground), up
    or both ways; kept off the icons at the ends and the relay and off the video, and dimmed under see-through panels
    (_put)."""
    if not paths or not _on(ex, "flow"):
        return
    clear = 7 if big else 4
    for p in paths:
        kind = p.get("kind", "link")
        if kind == "video":
            continue
        if kind == "pulse":                                  # a launch pad: a ring that grows and fades
            r = (now * 4) % 8
            a = max(0.0, 1 - r / 8)
            th = np.linspace(0, 2 * math.pi, 24, endpoint=False)
            px, py = p["x"] + (r + 3) * np.cos(th), p["y"] + (r + 3) * np.sin(th)
            q = {"left": 0, "W": buf.shape[1] * 4, "skip": p.get("skip")}
            ok = _keep(px, py, q["skip"])
            if ok.any():
                _plot(buf, px[ok], py[ok], p["rgb"], np.full(int(ok.sum()), a * 0.8, np.float32))
            continue
        if kind == "segs":                                   # many short links (a trellis): one dot running each
            x0, y0, x1, y1 = (np.asarray(p[k], float) for k in ("x0", "y0", "x1", "y1"))
            if not x0.size:
                continue
            L = np.maximum(np.hypot(x1 - x0, y1 - y0), 1.0)
            sp = FLOW_SPEED.get(p.get("speed"), FLOW_SPEED["normal"]) * 0.6
            f = (now * sp / L + p["phase"]) % 1.0
            if p.get("direction") == "up":
                f = 1 - f
            px, py = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
            col = np.asarray(p["rgb"], float) * 0.4 + 255 * 0.6
            _put(buf, p, px, py, col)
            continue
        xs, ys = np.asarray(p["xs"], float), np.asarray(p["ys"], float)
        seg = np.hypot(np.diff(xs), np.diff(ys))
        cum = np.r_[0, np.cumsum(seg)]
        total = cum[-1]
        if total < 2 * clear + 2:
            continue
        style = p.get("style", "dots")
        speed = FLOW_SPEED.get(p.get("speed"), FLOW_SPEED["normal"])
        direction = p.get("direction", "both")
        cols = [np.asarray(c, float) for c in p["cols"]]
        lighten = lambda c, lift: c * (1 - lift) + 255 * lift
        if style == "pulse":                                 # light running along the whole line
            s = np.arange(clear, total - clear, 1.0)
            ph = (now * speed) if direction != "up" else -(now * speed)
            a = np.clip(np.sin((s - ph) / 14.0 * math.pi) * 1.4 - 0.4, 0, 1) * 0.8
            px, py, k = _along(xs, ys, s, cum, seg)
            for leg in range(len(seg)):
                sel = (k == leg) & (a > 0.02)
                if sel.any():
                    _put(buf, p, px[sel], py[sel], lighten(cols[leg], 0.6), a[sel])
            continue
        gap = {"dots": FLOW_GAP, "dashes": 10, "comet": 26}.get(style, FLOW_GAP) + (2 if direction == "both" else 0)
        runs = []
        if direction in ("down", "both"):
            runs.append(((now * speed) % gap + np.arange(0, total, gap), 0.75, 1))
        if direction in ("up", "both"):
            runs.append((total - ((now * speed + gap / 2) % gap + np.arange(0, total, gap)), 0.45, -1))
        for heads, lift, sgn in runs:
            if style == "dashes":
                s = (heads[:, None] - sgn * np.arange(4)[None, :]).ravel()
                a = None
            elif style == "comet":
                tail = 7
                s = (heads[:, None] - sgn * np.arange(tail)[None, :]).ravel()
                a = np.tile(1.0 - np.arange(tail) / tail, heads.size)
            else:
                s, a = heads, None
            keep = (s > 0) & (s < total)
            keep &= np.min(np.abs(s[:, None] - cum[None, :]), axis=1) > clear
            s = s[keep]
            a = a[keep] if a is not None else None
            if not s.size:
                continue
            px, py, k = _along(xs, ys, s, cum, seg)
            for leg in range(len(seg)):
                sel = k == leg
                if sel.any():
                    _put(buf, p, px[sel], py[sel], lighten(cols[leg], lift), None if a is None else a[sel])


_cache: dict = {}


def cached(w, h, data, t, full=True, fixed=False, reserve=None, extras=None, live=None, mapset=None, view=None,
           avoid=None):
    """render(), redone every COMPOSE_EVERY (and when the data, the live feed or the settings change)."""
    ek = json.dumps([extras, mapset, view], sort_keys=True, default=str) if (extras or mapset or view) else ""
    key = (w, h, full, fixed, tuple(reserve or ()), tuple(avoid or ()), ek)
    slot = (int(t // COMPOSE_EVERY), id(data), id(live))
    hit = _cache.get(key)
    if hit is None or hit[0] != slot:
        if len(_cache) > 8:
            _cache.clear()
        hit = _cache[key] = (slot, render(w, h, data, t, full, fixed, reserve, extras, live, mapset, view, avoid))
    return hit[1]


def draw_full(c, zone, data, t, fixed=False, caption_box=None, extras=None, live=None, now=None, mapset=None,
              view=None):
    """The full map over the whole zone, the ISS's band clear of the caption (its box in the zone, if any), with the
    data moving along its links."""
    x, y, w, h = zone
    ex = extras if extras is not None else extras_of(None, mapset is not None)
    img, paths = cached(w, h, data, t, True, fixed, reserve_for(caption_box, h), ex, live, mapset, view,
                        tuple(caption_box) if caption_box and mapset is not None else None)
    c.Blit(img, x, y)
    if paths:
        flow(c.buf[max(0, y):y + h, max(0, x):x + w], paths, time.time() if now is None else now, ex, w >= BIG_W)
    return paths


def video_box(paths):
    """Where the Space Map left room for its live video ({x, y, w, h, border, tag}, zone-relative), or None."""
    return next((p for p in paths or [] if p.get("kind") == "video"), None)


def draw_video(buf, box, frame):
    """The live video into its box (a 1 px dark frame round it, and a small LIVE tag)."""
    if box is None or frame is None:
        return
    x, y, w, h = box["x"], box["y"], box["w"], box["h"]
    H, W = buf.shape[:2]
    if box.get("border", True):
        buf[max(0, y):min(H, y + h), max(0, x):min(W, x + w)] = (40, 46, 60)
    fh, fw = frame.shape[:2]
    X0, Y0 = x + 1, y + 1
    X1, Y1 = min(W, X0 + min(fw, w - 2)), min(H, Y0 + min(fh, h - 2))
    if X1 > X0 and Y1 > Y0:
        buf[Y0:Y1, X0:X1] = frame[:Y1 - Y0, :X1 - X0]
    if box.get("tag", True):
        tw = _tw("LIVE") + 3
        buf[Y0:Y0 + 7, X0:X0 + tw] = (200, 30, 40)
        _text(buf, X0 + 2, Y0 + 1, "LIVE", (255, 255, 255))


def small_size(w, h):
    """The small map's size in a zone w×h: about a fifth of its width (96×48 on the whole wall), twice as wide as tall,
    at least 48 wide where that fits; (0, 0) when the zone is too small for one."""
    sw = min(max(48, round(w * 0.214)), 128, w - 10, (h - 10) * 2) // 2 * 2
    return (sw, sw // 2) if sw >= 24 else (0, 0)


def small_box(w, h, corner="tr", avoid=None, margin=3):
    """Where the small map goes in a zone w×h (zone-relative x, y, sw, sh; its 1 px frame is round that): in its
    corner, or, when that would cover the caption (avoid: its box x0, y0, x1, y1), above it (bottom corners) or below
    it (top corners)."""
    sw, sh = small_size(w, h)
    corner = corner if corner in CORNERS else "tr"
    x = margin + 1 if corner[1] == "l" else w - sw - margin - 1
    y = margin + 1 if corner[0] == "t" else h - sh - margin - 1

    def hits(yy):
        x0, y0, x1, y1 = avoid
        return x < x1 + 1 and x + sw + 1 > x0 - 1 and yy < y1 + 1 and yy + sh + 1 > y0 - 1

    if avoid and hits(y):
        moved = avoid[1] - 2 - sh if corner[0] == "b" else avoid[3] + 2
        if 1 <= moved <= h - sh - 1:                       # (no room past it: it stays in its corner)
            y = moved
    return x, y, sw, sh


def draw_small(c, zone, data, t, corner="tr", avoid=None):
    """The small map in a corner of the zone, with a dark 1 px frame (off the caption: see small_box)."""
    zx, zy, w, h = zone
    x, y, sw, sh = small_box(w, h, corner, avoid)
    if not sw:
        return
    buf = c.buf
    X0, Y0 = zx + x - 1, zy + y - 1
    buf[max(0, Y0):Y0 + sh + 2, max(0, X0):X0 + sw + 2] = FRAME
    c.Blit(cached(sw, sh, data, t, False)[0], zx + x, zy + y)
