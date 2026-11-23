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
FALLBACK_RELAYS = [{"name": "TDRS", "net": "tdrs", "lat": 0.0, "lon": lo} for lo in (-41.0, -62.0, -171.0, 85.0)]
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
CSS_ART = ["R.R", "RWR", "R.R"]
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
CSS_BIG = ["RRR.RRR", "RRRWRRR", "...W...", "..WWW.."]
PAL = {"A": (220, 150, 40), "W": (235, 235, 235), "G": (240, 200, 60), "B": (70, 130, 255), "D": (225, 225, 235),
       "S": (150, 150, 160), "K": (110, 110, 125), "L": (255, 210, 90), "T": (60, 200, 170), "E": (250, 190, 140),
       "R": (230, 80, 60), "H": (160, 105, 30)}
RU = lambda art: [r.replace("D", "E") for r in art]                               # a Russian station's dish
ICONS = {False: {"iss": ISS_ART, "tdrs": RELAY_ART, "luch": LUCH_ART, "nasa": DISH_ART, "ru": RU(DISH_ART),
                 "css": CSS_ART},
         True: {"iss": ISS_BIG, "tdrs": RELAY_BIG, "luch": LUCH_BIG, "nasa": DISH_BIG, "ru": RU(DISH_BIG),
                "css": CSS_BIG}}

# --- the extras: each on or off, with its settings (cams.<id>.map.extras) ---------------------------------------
TELEMETRY_ROWS = ("cabin_p", "cabin_t", "o2", "co2", "gyros", "ku", "water", "arrays")
EXTRAS = {
    "lights": {"on": True, "strength": 0.8},
    "aurora": {"on": True, "strength": 0.8},
    "ahead": {"on": True, "minutes": 45},
    "sunlit": {"on": True, "shadow": "blue"},
    "tiangong": {"on": True, "trail": True, "label": True},
    "signal": {"on": True, "tag": True},
    "russian": {"on": True},
    "flow": {"on": True, "speed": "normal", "direction": "both"},
    "telemetry": {"on": True, "corner": "tr", "rows": ["cabin_p", "cabin_t", "o2", "co2", "gyros", "water"]},
    "docked": {"on": True, "corner": "tr", "days": True},
}
FLOW_SPEED = {"slow": 6.0, "normal": 12.0, "fast": 24.0}   # px a second
FLOW_GAP = 8                                                 # px between the dots


def extras_of(cam) -> dict:
    """The map's extras for a webcam: EXTRAS with its own settings over them."""
    got = ((cam or {}).get("map") or {}).get("extras") if isinstance((cam or {}).get("map"), dict) else None
    got = got if isinstance(got, dict) else {}
    return {k: {**d, **(got.get(k) if isinstance(got.get(k), dict) else {})} for k, d in EXTRAS.items()}


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


def links(pos, relays=None, aos=None, russian=True):
    """The ISS's links at pos (lat, lon): [{"net", "pts": [(lat, lon), …] from the ISS, "cols": [colour per leg],
    "via": name}]. NASA: unless the signal is known lost (aos False), through the TDRS that both the ISS and a ground
    terminal can see (the newest; then the nearest the ISS), down to White Sands (or else Guam). Russia: down to a Russian station
    that sees the ISS above MIN_EL (the highest), else through a Luch-5 that the ISS and Shchyolkovo can see."""
    relays = relays or FALLBACK_RELAYS
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
                        "cols": [LINK, RELAY_LINK], "via": r.get("name")})
    if russian:
        seen = [(elevation(p, iss), n) for n, p in RU_GROUND.items()]
        el, n = max(seen)
        if el >= MIN_EL:
            out.append({"net": "ru", "pts": [pos, RU_GROUND[n]], "cols": [RU_LINK], "via": n})
        else:
            got = via("luch", {LUCH_GROUND: RU_GROUND[LUCH_GROUND]})
            if got:
                _d, r, g = got
                out.append({"net": "ru", "pts": [pos, (r.get("lat") or 0.0, r["lon"]), RU_GROUND[g]],
                            "cols": [RU_LINK, RU_RELAY_LINK], "via": r.get("name")})
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
_live = {"data": None, "used": 0.0, "hub": None, "thread": None}


def live_from(hub):
    """The newest /api/iss/live from the hub (polled in the background every LIVE_EVERY while asked for), or None."""
    _live["used"], _live["hub"] = time.time(), hub
    th = _live["thread"]
    if hub and (th is None or not th.is_alive()):
        _live["thread"] = threading.Thread(target=_poll_live, name="iss-live", daemon=True)
        _live["thread"].start()
    return _live["data"]


def _poll_live():
    import urllib.request
    while time.time() - _live["used"] < 15:
        try:
            with urllib.request.urlopen(_live["hub"].rstrip("/") + "/api/iss/live", timeout=4) as r:
                _live["data"] = json.loads(r.read())
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
        arr = np.frombuffer(raw[8:8 + w * h], np.uint8).reshape(h, w)
    except (OSError, ValueError):
        mt, arr = None, None
    _grids[name] = (now, mt, arr)
    return arr


# --- projection ------------------------------------------------------------------------------------------------
class View:
    """Where things go on a map w×h: an equirectangular world W px round (the zone's width, or wider when following
    the ISS in a tall narrow zone, so the map keeps filling it), lat top..bot over mh rows from y0.

    reserve (top px, bottom px): rows the caption covers. The ISS's whole band of latitudes (and so its trail) is
    kept clear of them: the map is moved, and squashed from top to bottom only when the band doesn't otherwise fit.
    The map still runs on under the caption."""

    def __init__(self, w, h, follow=True, small=False, reserve=None):
        self.w, self.h, self.follow, self.small = w, h, follow, small
        if small:
            s, span = w / 360.0, 180.0
        else:
            s = w / 360.0 if not follow else max(w / 360.0, h / 180.0)
            span = min(180.0, h / s)
        self.W = max(8, int(round(360.0 * s)))
        self.mh = min(h, max(4, int(round(span * s))))
        self.y0 = (h - self.mh) // 2
        # a short map is the ISS's band (it never goes past 51.6°); a taller one reaches up to the Arctic coasts
        c = 7.0 * min(1.0, max(0.0, (span - 104.0) / 50.0))
        top = min(90.0, c + span / 2)
        bot = top - span
        if bot < -90.0:
            bot, top = -90.0, -90.0 + span
        rt, rb = reserve or (0, 0)
        avail = h - rt - rb - 2 * BAND_MARGIN
        if not small and (rt or rb) and avail >= 12:
            ys = min(self.mh / span, avail / (2 * ISS_LAT))         # px per degree of latitude
            top = ISS_LAT + (rt + BAND_MARGIN) / ys                  # (past the pole: space above the map)
            self.y0 = max(0, int(math.ceil((top - 90.0) * ys)))
            top = min(90.0, top)
            bot = max(-90.0, top - (h - self.y0) / ys)
            self.mh = min(h - self.y0, max(4, int(round((top - bot) * ys))))
        self.top, self.bot = top, bot
        self.key = (self.W, self.mh, self.y0, round(top, 3), round(bot, 3))

    def wx(self, lon):
        """World x (px, 0..W) of a longitude: arrays too."""
        return (np.asarray(lon, float) + 180.0) / 360.0 * self.W

    def y(self, lat):
        return self.y0 + (self.top - np.asarray(lat, float)) / (self.top - self.bot) * self.mh

    def left(self, iss_lon):
        """The world x at the map's left edge: the ISS in the middle, or the whole world from the date line."""
        if not self.follow or iss_lon is None:
            return 0
        return int(math.floor(float(self.wx(iss_lon)) - self.w / 2 + 0.5))


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
    if alpha is None:
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


def _sprite_arrays(art):
    hit = _sprites.get(id(art))
    if hit is None:
        hh, ww = len(art), len(art[0])
        rgb = np.zeros((hh, ww, 3), np.uint8)
        mask = np.zeros((hh, ww), bool)
        for j, row in enumerate(art):
            for i, ch in enumerate(row):
                if ch != ".":
                    rgb[j, i], mask[j, i] = PAL[ch], True
        hit = _sprites[id(art)] = (rgb, mask)
    return hit


def _sprite(img, art, wx, y, v, left, clamp_y=False):
    """An icon centred on world x wx, row y (drawn again a world further along when it straddles the map's edge)."""
    rgb, mask = _sprite_arrays(art)
    hh, ww = mask.shape
    h, w = img.shape[:2]
    y0 = int(round(y - hh / 2))
    if clamp_y:
        y0 = max(0, min(h - hh, y0))
    x = int(round((wx - left) % v.W - ww / 2))
    for xx in (x, x - v.W, x + v.W):
        X0, X1, Y0, Y1 = max(0, xx), min(w, xx + ww), max(0, y0), min(h, y0 + hh)
        if X1 <= X0 or Y1 <= Y0:
            continue
        m = mask[Y0 - y0:Y1 - y0, X0 - xx:X1 - xx]
        img[Y0:Y1, X0:X1][m] = rgb[Y0 - y0:Y1 - y0, X0 - xx:X1 - xx][m]


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


_bases: dict = {}


def base(v, full, big=False, russian=True):
    """The world W×mh: ocean, coastlines and, for the full map, borders and the ground stations (they never move);
    uint8, cached per size. Returns (image, mask of the icons' pixels: the trail goes under them)."""
    key = (v.key, full, big, russian)
    hit = _bases.get(key)
    if hit is None:
        img = np.empty((v.mh, v.W, 3), np.uint8)
        img[:] = OCEAN
        L = world_lines()
        if full:
            _polylines(img, v, L["borders"], BORDER)
        _polylines(img, v, L["coast"], COAST)
        icons = np.zeros((v.mh, v.W, 3), np.uint8)
        if full:
            art = ICONS[big]
            for la, lo in NASA_GROUND.values():
                _sprite(icons, art["nasa"], float(v.wx(lo)), float(v.y(la)) - v.y0, v, 0)
            if russian:
                for la, lo in RU_GROUND.values():
                    _sprite(icons, art["ru"], float(v.wx(lo)), float(v.y(la)) - v.y0, v, 0)
        mask = icons.any(axis=2)
        img[mask] = icons[mask]
        if len(_bases) > 8:
            _bases.clear()
        hit = _bases[key] = (img, mask)
    return hit


def daylight(v, t):
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
    return (NIGHT + (1.0 - NIGHT) * f)[..., None]


def _sample(v, g, north_first):
    """A lat/lon grid (rows of latitude, columns of longitude from -180, or from 0 for the aurora) at each world
    pixel: (mh, W) float32 0..1."""
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


def shaded(v, full, t, big=False, ex=None):
    """The world with day and night (and on the full map the city lights and the aurora on the night side; the icons
    stay lit), recomputed every SHADE_EVERY: (image, icon mask)."""
    slot = int(t // SHADE_EVERY)
    russian = bool(full and _on(ex, "russian"))
    lights = grid("lights") if full and _on(ex, "lights") else None
    aurora = grid("aurora") if full and _on(ex, "aurora") else None
    key = (v.key, full, big, russian)
    tag = (slot, id(lights), id(aurora), json.dumps([(ex or {}).get("lights"), (ex or {}).get("aurora")], default=str))
    hit = _shaded.get(key)
    if hit is None or hit[0] != tag:
        if len(_shaded) > 8:
            _shaded.clear()
        img, mask = base(v, full, big, russian)
        day = daylight(v, slot * SHADE_EVERY)
        lit = img * day
        dark = ((1.0 - day[..., 0]) / (1.0 - NIGHT)).clip(0, 1)
        for g, north, k, rgb, f in ((lights, True, "lights", LIGHTS, lambda x: np.clip((x - 0.18) * 1.6, 0, 1)),
                                    (aurora, False, "aurora", AURORA, lambda x: np.clip((x * 255 - 4) / 30, 0, .75))):
            if g is None:
                continue
            strength = float((ex.get(k) or {}).get("strength", 0.8))
            vals = _sample(v, g, north) / 255.0
            a = (f(vals) * (dark if north else np.clip(dark * 1.5, 0, 1)) * strength)[..., None]
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


# --- text panels -----------------------------------------------------------------------------------------------
_font = {}


def _tiny():
    f = _font.get("tiny")
    if f is None:
        from display.gfx import graphics
        from display.modules.webcam_caption import FONTS
        f = _font["tiny"] = graphics.Font()
        f.LoadFont(FONTS["tiny"])
    return f


def _canvas(img):
    from display.gfx import Canvas
    cv = Canvas.__new__(Canvas)
    cv.width, cv.height, cv.buf = img.shape[1], img.shape[0], img
    return cv


def _tw(s):
    f = _tiny()
    return sum(max(0, f.CharacterWidth(ord(ch))) for ch in s)


def _text(img, x, y, s, rgb):
    """Text with its top-left at x, y."""
    from display.gfx import Color, graphics
    f = _tiny()
    graphics.DrawText(_canvas(img), f, x, y + f.baseline, Color(*rgb), s)


def _num(live, k, d=0):
    try:
        return float(((live or {}).get("values") or {})[k]["v"]), True
    except (KeyError, TypeError, ValueError):
        return 0.0, False


def telemetry_lines(live, rows):
    """[(label, value)] for the telemetry panel (NASA ISS Live readings; -- when the hub has none)."""
    out = []
    for k in rows:
        if k == "arrays":
            p, ok1 = _num(live, "sarj_p")
            s, ok2 = _num(live, "sarj_s")
            out.append(("ARRAYS", f"P {p:.0f}  S {s:.0f}" if ok1 and ok2 else "--"))
            continue
        if k == "ku":
            cal = ((((live or {}).get("values") or {}).get("ku_tx") or {}).get("cal") or "")
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
        n = str(d.get("name") or "").upper()
        fc = d.get("flag") or ""
        f = flag_char(fc) if fc else ""
        dd = f"  {int((now - float(d['docked'])) // 86400)}D" if days and d.get("docked") else ""
        out.append(f"{f}{n}{dd}")
    return out


def _panel(img, corner, y, lines, head=None):
    """A see-through dark panel of tiny text in a top corner from row y; returns the row under it."""
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


def render(w, h, data, t, full=True, fixed=False, reserve=None, extras=None, live=None):
    """The map at time t (as filmed): (image (h, w, 3) uint8, link paths for the moving data, or []). full: the full
    map (borders, stations, relays, links, the detailed ISS, the extras); else the small map (coastlines, trail, the
    tiny ISS). fixed (full map only): the world held still with the ISS crossing it; else centred on the ISS.
    reserve: the caption's rows (see View). extras: extras_of(cam). live: the hub's /api/iss/live."""
    from display.modules.space import position
    if w < 8 or h < 4:
        return np.zeros((max(0, h), max(0, w), 3), np.uint8), []
    ex = (extras if extras is not None else extras_of(None)) if full else {}
    big = bool(full and w >= BIG_W)
    art = ICONS[big]
    v = View(w, h, follow=not (full and fixed), small=not full, reserve=reserve if full else None)
    track = (data or {}).get("track") if isinstance(data, dict) else None
    pos = position(track, t) if track else None
    left = v.left(pos[1] if pos else None)
    img = np.empty((h, w, 3), np.uint8)
    if v.mh < h:
        img[:] = OUTSIDE
    world, mask = shaded(v, full, t, big, ex)
    band = img[v.y0:v.y0 + v.mh]
    _window(band, world, left, v.W)                        # the world from ``left``, round the date line
    paths = []
    if pos is None:
        return img, paths
    if full and _on(ex, "ahead"):
        _ahead(img, v, left, track, t, pos, ex["ahead"].get("minutes", 45))
    _trail(img, v, left, track, t, pos, sunlit=(ex["sunlit"].get("shadow", "blue") if _on(ex, "sunlit") else None))
    if full:                                               # the stations back over the trail
        m = np.empty(band.shape[:2] + (1,), bool)
        _window(m, mask[..., None], left, v.W)
        src = np.empty_like(band)
        _window(src, world, left, v.W)
        np.copyto(band, src, where=m)
    ix, iy = float(v.wx(pos[1])), float(v.y(pos[0]))
    if full:
        css = (data or {}).get("css")
        if _on(ex, "tiangong") and css:
            cpos = position(css, t)
            if cpos:
                if ex["tiangong"].get("trail", True):
                    _trail(img, v, left, css, t, cpos, colour=CSS, peak=0.5, floor=0.05)
                cx, cy = float(v.wx(cpos[1])), float(v.y(cpos[0]))
                _sprite(img, art["css"], cx, cy, v, left)
                if ex["tiangong"].get("label", True) and big:
                    X = int(round((cx - left) % v.W))
                    lab = "TIANGONG"
                    _text(img, min(w - _tw(lab) - 1, X + 6), max(0, int(cy) - 9), lab, (230, 110, 90))
        aos = signal_at(live, t) if _on(ex, "signal") else None
        relays = (data or {}).get("relays") or FALLBACK_RELAYS
        for lk in links(pos, relays, aos, russian=_on(ex, "russian")):
            xs, ys = [ix], [iy]
            for la, lo in lk["pts"][1:]:
                xs.append(float(_unwrap(np.array([xs[-1]]), np.array([float(v.wx(lo))]), v.W)[0]))
                ys.append(float(v.y(la)))
            for k, col in enumerate(lk["cols"]):
                _seg_line(img, v, left, xs[k], ys[k], xs[k + 1], ys[k + 1], col)
            live_ok = aos is True if lk["net"] == "nasa" else True
            if live_ok:
                paths.append({"xs": xs, "ys": ys, "cols": lk["cols"], "left": left, "W": v.W})
        for r in relays:
            if r.get("net") == "luch" and not _on(ex, "russian"):
                continue
            _sprite(img, art["luch" if r.get("net") == "luch" else "tdrs"], float(v.wx(r["lon"])),
                    float(v.y(r.get("lat") or 0.0)), v, left)
        if w >= 280:                                        # the panels, in the top corners
            ys_ = {"tl": 0, "tr": 0}
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
        if _on(ex, "signal") and ex["signal"].get("tag", True) and aos is not None:
            lab = "AOS" if aos else "LOS"
            X = int(round((ix - left) % v.W)) + len(art["iss"][0]) // 2 + 3
            Y = max(0, min(h - 8, int(iy) - 3))
            img[Y:Y + 7, X - 1:X + _tw(lab) + 1] = (img[Y:Y + 7, X - 1:X + _tw(lab) + 1] * 0.3).astype(np.uint8)
            _text(img, X, Y + 1, lab, AOS_C if aos else LOS_C)
    _sprite(img, art["iss"] if full else SMALL_ART, ix, iy, v, left, clamp_y=True)
    return img, paths


def flow(buf, paths, now, ex, big=False):
    """The data moving along the links (drawn every frame over the map): dots travelling down (ISS to ground) and/or
    up, the link's own colour lightened, kept off the icons at the ends and the relay."""
    if not paths or not _on(ex, "flow"):
        return
    speed = FLOW_SPEED.get((ex["flow"] or {}).get("speed"), FLOW_SPEED["normal"])
    direction = (ex["flow"] or {}).get("direction", "both")
    gap = FLOW_GAP + (2 if direction == "both" else 0)
    clear = 7 if big else 4
    for p in paths:
        xs, ys = np.asarray(p["xs"], float), np.asarray(p["ys"], float)
        seg = np.hypot(np.diff(xs), np.diff(ys))
        cum = np.r_[0, np.cumsum(seg)]
        total = cum[-1]
        if total < 2 * clear + 2:
            continue
        runs = []
        if direction in ("down", "both"):
            runs.append(((now * speed) % gap + np.arange(0, total, gap), 0.75))
        if direction in ("up", "both"):
            runs.append((total - ((now * speed + gap / 2) % gap + np.arange(0, total, gap)), 0.45))
        for s, lift in runs:
            s = s[(s > 0) & (s < total)]
            s = s[np.min(np.abs(s[:, None] - cum[None, :]), axis=1) > clear]
            if not s.size:
                continue
            k = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(seg) - 1)
            f = (s - cum[k]) / np.maximum(seg[k], 1e-6)
            px, py = xs[k] + (xs[k + 1] - xs[k]) * f, ys[k] + (ys[k + 1] - ys[k]) * f
            for leg in range(len(seg)):
                sel = k == leg
                if sel.any():
                    col = np.asarray(p["cols"][leg], float) * (1 - lift) + 255 * lift
                    _plot(buf, px[sel], py[sel], col, None, p["left"], p["W"])


_cache: dict = {}


def cached(w, h, data, t, full=True, fixed=False, reserve=None, extras=None, live=None):
    """render(), redone every COMPOSE_EVERY (and when the data, the live feed or the settings change)."""
    ek = json.dumps(extras, sort_keys=True, default=str) if extras else ""
    key = (w, h, full, fixed, tuple(reserve or ()), ek)
    slot = (int(t // COMPOSE_EVERY), id(data), id(live))
    hit = _cache.get(key)
    if hit is None or hit[0] != slot:
        if len(_cache) > 8:
            _cache.clear()
        hit = _cache[key] = (slot, render(w, h, data, t, full, fixed, reserve, extras, live))
    return hit[1]


def draw_full(c, zone, data, t, fixed=False, caption_box=None, extras=None, live=None, now=None):
    """The full map over the whole zone, the ISS's band clear of the caption (its box in the zone, if any), with the
    data moving along its links."""
    x, y, w, h = zone
    ex = extras if extras is not None else extras_of(None)
    img, paths = cached(w, h, data, t, True, fixed, reserve_for(caption_box, h), ex, live)
    c.Blit(img, x, y)
    if paths:
        flow(c.buf[max(0, y):y + h, max(0, x):x + w], paths, time.time() if now is None else now, ex, w >= BIG_W)


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
