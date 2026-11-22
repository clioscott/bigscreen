"""The ISS maps (a webcam in space): the full map, shown in the ISS webcam's turn while its video is unusable (the
night side, the loss-of-signal card, the feed down), and the small map in a corner of the video.

Both are a world map with day and night (the weather maps' shading: darker where the Sun is down, smooth through
civil twilight), the ISS's last TRAIL_SEC of orbit as a line fading into the past, and the ISS. They stay centred on
the ISS from side to side (the world scrolls under it; it only moves up and down), so nothing is ever cut at the
date line; the full map can instead hold the world still (``cams.<id>.map.fixed``) and let the ISS cross it, its
lines then carrying on from the other edge.

The full map also has grey country borders, the ground stations that talk to the ISS and the TDRS relays, with a
red line from the ISS to whatever it's talking to: straight down to the nearest station that can see it, or else up
to a TDRS relay, with a cyan line from the relay down to its ground terminal. (Simulated from public positions: the
real schedule isn't published.) The small map has only coastlines, the trail and a tiny ISS.

Everything is for the moment the video shows (space.LAG behind). The coastlines and borders are Natural Earth's
1:110m (assets/maps/world_110m.json, tools/build_world_lines.py), drawn once per size; the night shading is
recomputed every SHADE_EVERY and the map every COMPOSE_EVERY (the ISS moves about one pixel in ten seconds), and
each frame just copies it."""

from __future__ import annotations

import json
import math
import os

import numpy as np

from common.orbit import gmst, julian, sun_dir

ASSET = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "maps", "world_110m.json")
OCEAN = (8, 18, 40)
OUTSIDE = (3, 7, 16)          # above and below the map when the zone is taller than the world
BORDER = (90, 90, 95)
COAST = (90, 170, 200)
TRAIL = (255, 255, 255)
LINK = (255, 40, 40)          # the ISS to a ground station or a relay
RELAY_LINK = (60, 220, 255)   # a relay down to its ground terminal
FRAME = (0, 0, 0)             # round the small map
NIGHT = 0.45                  # how bright the night side is
TWILIGHT = 6.0                # degrees of Sun below the horizon over which day fades to night
TRAIL_SEC = 90 * 60
SHADE_EVERY = 30.0
COMPOSE_EVERY = 1.0
CORNERS = ("tl", "tr", "bl", "br")

# public positions of stations that talk to the ISS (lat, lon)
GROUND = {
    "White Sands": (32.5, -106.6), "Guam": (13.6, 144.9), "Wallops": (37.9, -75.5), "Moscow": (55.9, 37.8),
    "Ussuriysk": (43.8, 131.9), "Kamchatka": (53.0, 158.6), "Kiruna": (67.9, 21.1), "Svalbard": (78.2, 15.4),
    "Kourou": (5.2, -52.8), "Santiago": (-33.1, -70.7), "Hartebeesthoek": (-25.9, 27.7), "Dongara": (-29.0, 115.3),
    "McMurdo": (-77.8, 166.7), "Tsukuba": (36.1, 140.1), "Redu": (50.0, 5.1), "Alice Springs": (-23.8, 133.9),
}
TDRS = {"TDRS East": -41.0, "TDRS West": -171.0, "TDRS Z": 85.0}    # geostationary, over the equator (longitude)
TDRS_GROUND = ("White Sands", "Guam")                              # where the relays come down
ALT_KM = 420.0
MIN_EL = 5.0
R_KM = 6371.0

ISS_ART = ["AA.AA...AA.AA", "AA.AA...AA.AA", "WWWWWWWWWWWWW", "AA.AA.G.AA.AA", "AA.AA.W.AA.AA"]
RELAY_ART = ["BB.W.BB", "BBWWWBB", "BB.W.BB"]
DISH_ART = ["D.....", ".DD...", ".D.D..", "..DDS.", "...S..", ".KKKK.", ".KLKL."]   # a big dish on a building
SMALL_ART = ["A.A", "AWA", "A.A"]                                                  # the small map's ISS
PAL = {"A": (220, 150, 40), "W": (235, 235, 235), "G": (240, 200, 60), "B": (70, 130, 255), "D": (225, 225, 235),
       "S": (150, 150, 160), "K": (110, 110, 125), "L": (255, 210, 90)}


# --- links -----------------------------------------------------------------------------------------------------
def ground_range_km(alt_km=ALT_KM, min_el=MIN_EL):
    """How far along the ground a station sees the ISS above min_el degrees (~2,300 km)."""
    e = math.radians(min_el)
    return R_KM * (math.acos(R_KM / (R_KM + alt_km) * math.cos(e)) - e)


REACH_KM = ground_range_km()


def gc_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    c = math.sin(la1) * math.sin(la2) + math.cos(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return R_KM * math.acos(max(-1.0, min(1.0, c)))


def _dlon(a, b):
    """b - a in degrees, the short way round (-180..180)."""
    return (b - a + 180.0) % 360.0 - 180.0


def geo_visible(lon_sat, pt, min_el=MIN_EL):
    """Can a geostationary satellite at lon_sat see the point above min_el?"""
    la, lo = map(math.radians, pt)
    c = math.cos(la) * math.cos(lo - math.radians(lon_sat))
    el = math.degrees(math.atan2(c - 6378.0 / 42164.0, math.sqrt(max(0.0, 1 - c * c))))
    return el >= min_el


def link(pos):
    """Who the ISS at pos (lat, lon) talks to: ("ground", station) when one can see it (the nearest), else
    ("relay", relay, terminal or None): the TDRS nearest in longitude, and the ground terminal that relay sees."""
    name, d = min(((n, gc_km(pos, p)) for n, p in GROUND.items()), key=lambda x: x[1])
    if d <= REACH_KM:
        return ("ground", name)
    relay = min(TDRS, key=lambda n: abs(_dlon(pos[1], TDRS[n])))
    down = [g for g in TDRS_GROUND if geo_visible(TDRS[relay], GROUND[g])]
    term = min(down, key=lambda g: abs(_dlon(TDRS[relay], GROUND[g][1]))) if down else None
    return ("relay", relay, term)


# --- projection ------------------------------------------------------------------------------------------------
class View:
    """Where things go on a map w×h: an equirectangular world W px round (the zone's width, or wider when following
    the ISS in a tall narrow zone, so the map keeps filling it), lat top..bot over mh rows from y0."""

    def __init__(self, w, h, follow=True, small=False):
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
        self.top, self.bot = top, bot
        self.key = (self.W, self.mh, round(top, 3), round(bot, 3))

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


def base(v, full):
    """The world W×mh: ocean, coastlines and, for the full map, borders, ground stations and relays (they never move);
    uint8, cached per size. Returns (image, mask of the icons' pixels: the trail goes under them)."""
    key = (v.key, full)
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
            for la, lo in GROUND.values():
                _sprite(icons, DISH_ART, float(v.wx(lo)), float(v.y(la)) - v.y0, v, 0)
            for lo in TDRS.values():
                _sprite(icons, RELAY_ART, float(v.wx(lo)), float(v.y(0.0)) - v.y0, v, 0)
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


_shaded: dict = {}


def shaded(v, full, t):
    """The world with day and night (the icons stay lit), recomputed every SHADE_EVERY: (image, icon mask)."""
    slot = int(t // SHADE_EVERY)
    key = (v.key, full)
    hit = _shaded.get(key)
    if hit is None or hit[0] != slot:
        if len(_shaded) > 8:
            _shaded.clear()
        img, mask = base(v, full)
        lit = (img * daylight(v, slot * SHADE_EVERY) + 0.5).astype(np.uint8)
        lit[mask] = img[mask]
        hit = _shaded[key] = (slot, (lit, mask))
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
    """The track as an (n, 4) float array, made once per track."""
    hit = _arrays.get(id(track))
    if hit is None or hit[0] is not track:
        _arrays.clear()
        hit = _arrays[id(track)] = (track, np.asarray(track, float).reshape(-1, 4))
    return hit[1]


def _trail(img, v, left, track, t, pos):
    """The last TRAIL_SEC of the track up to pos, fading into the past (newest drawn last, on top)."""
    if not track:
        return
    arr = _array(track)
    i, j = np.searchsorted(arr[:, 0], [t - TRAIL_SEC, t], side="right")
    a = np.vstack([arr[i:j, :3], [[t, pos[0], pos[1]]]])
    if len(a) < 2:
        return
    age = (t - a[1:, 0]) / TRAIL_SEC
    alpha = np.maximum(0.08, 0.9 * (1 - age))
    x, y = v.wx(a[:, 2]), v.y(a[:, 1])
    _seg_line(img, v, left, x[:-1], y[:-1], x[1:], y[1:], TRAIL, alpha)


def render(w, h, data, t, full=True, fixed=False):
    """The map at time t (as filmed) as (h, w, 3) uint8. full: the full map (borders, stations, relays, links,
    the detailed ISS); else the small map (coastlines, trail, the tiny ISS). fixed (full map only): the world held
    still with the ISS crossing it; else centred on the ISS."""
    from display.modules.space import position
    if w < 8 or h < 4:
        return np.zeros((max(0, h), max(0, w), 3), np.uint8)
    v = View(w, h, follow=not (full and fixed), small=not full)
    track = (data or {}).get("track") if isinstance(data, dict) else None
    pos = position(track, t) if track else None
    left = v.left(pos[1] if pos else None)
    img = np.empty((h, w, 3), np.uint8)
    if v.mh < h:
        img[:] = OUTSIDE
    world, mask = shaded(v, full, t)
    band = img[v.y0:v.y0 + v.mh]
    _window(band, world, left, v.W)                        # the world from ``left``, round the date line
    if pos is not None:
        _trail(img, v, left, track, t, pos)
        if full:                                           # the stations and relays back over the trail
            m = np.empty(band.shape[:2] + (1,), bool)
            _window(m, mask[..., None], left, v.W)
            src = np.empty_like(band)
            _window(src, world, left, v.W)
            np.copyto(band, src, where=m)
        ix, iy = float(v.wx(pos[1])), float(v.y(pos[0]))
        if full:
            got = link(pos)
            if got[0] == "ground":
                la, lo = GROUND[got[1]]
                _seg_line(img, v, left, ix, iy, v.wx(lo), v.y(la), LINK)
            else:
                rx, ry = float(v.wx(TDRS[got[1]])), float(v.y(0.0))
                _seg_line(img, v, left, ix, iy, rx, ry, LINK)
                if got[2]:
                    la, lo = GROUND[got[2]]
                    _seg_line(img, v, left, rx, ry, v.wx(lo), v.y(la), RELAY_LINK)
        _sprite(img, ISS_ART if full else SMALL_ART, ix, iy, v, left, clamp_y=True)
    return img


_cache: dict = {}


def cached(w, h, data, t, full=True, fixed=False):
    """render(), redone every COMPOSE_EVERY (and when the data or the settings change)."""
    key = (w, h, full, fixed)
    slot = (int(t // COMPOSE_EVERY), id(data))
    hit = _cache.get(key)
    if hit is None or hit[0] != slot:
        if len(_cache) > 8:
            _cache.clear()
        hit = _cache[key] = (slot, render(w, h, data, t, full, fixed))
    return hit[1]


def draw_full(c, zone, data, t, fixed=False):
    """The full map over the whole zone."""
    x, y, w, h = zone
    c.Blit(cached(w, h, data, t, True, fixed), x, y)


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
    c.Blit(cached(sw, sh, data, t, False), zx + x, zy + y)
