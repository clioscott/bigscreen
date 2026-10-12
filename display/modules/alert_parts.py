"""Graphic building blocks for the alert designs: where (region map, tile cartogram, region chips, home badge),
when (countdown ring, validity timeline), how bad / how much (severity ladder, amount gauge), and animated
hazard scenes. All 1-bit / flat-colour pixel graphics at native size; fonts come from the theme's family.

Regions use the wall's own names (alert_brief.REGIONS); the maps are ECCC's alert-region boundaries
(MSC Geography Package, PubMesoZone) rasterised one pixel per LED (assets/alerts/region_maps.*).
"""

from __future__ import annotations

import json
import math
import os
import re
import time

import numpy as np

from display.gfx import ASSETS_DIR, Color
from display.modules import alert_brief
from display.modules import pixel_icons as px

_MAPS = None
_META = None


def _dim(rgb, k):
    return tuple(max(0, min(255, int(v * k))) for v in rgb)


def _mix(a, b, k):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


HAZARD_COLOURS = {"rain": (40, 120, 255), "snow": (215, 235, 255), "freezing": (175, 95, 255), "wind": (0, 215, 195),
                  "thunder": (255, 215, 0), "tornado": (255, 0, 185), "flood": (0, 80, 255), "heat": (255, 110, 0),
                  "cold": (120, 200, 255), "frost": (150, 255, 255), "fog": (175, 175, 180), "smoke": (195, 125, 60),
                  "hail": (255, 215, 0), "general": (255, 190, 60)}


def hazard_colour(ctx):
    return HAZARD_COLOURS.get(ctx.b["hazard"], ctx.sev)


# ------------------------------------------------------------------------------------------------ where
METRO_PARTS = ("Richmond", "Vancouver", "North Shore", "Tri-Cities", "Surrey")
CODES = dict(alert_brief.REGIONS)


def affected(ctx):
    """Set of the wall's region names this alert covers (all five for "Metro Vancouver")."""
    if not hasattr(ctx, "_affected"):
        out = set()
        for name, code in alert_brief.region_labels(ctx.b.get("regions") or [], getattr(ctx, "home_name", "Richmond")):
            out |= set(METRO_PARTS) if name == "Metro Vancouver" else {name}
        ctx._affected = out
        ctx._labels = alert_brief.region_labels(ctx.b.get("regions") or [], getattr(ctx, "home_name", "Richmond"))
    return ctx._affected


def labels(ctx):
    affected(ctx)
    return ctx._labels


def home_hit(ctx):
    return bool(getattr(ctx, "home", False))


def _maps():
    global _MAPS, _META
    if _MAPS is None:
        z = np.load(os.path.join(ASSETS_DIR, "alerts", "region_maps.npz"))
        _MAPS = {k: z[k] for k in z.files}
        _META = json.load(open(os.path.join(ASSETS_DIR, "alerts", "region_maps.json")))
    return _MAPS, _META


def map_size(extent, w, h=999):
    """(key, w, h) of the largest pre-rendered map of ``extent`` fitting w×h."""
    maps, meta = _maps()
    best = None
    for key, m in meta["maps"].items():
        if m["extent"] == extent and m["w"] <= w and m["h"] <= h and (best is None or m["w"] > best[1]):
            best = (key, m["w"], m["h"])
    return best or (f"{extent}_72", meta["maps"][f"{extent}_72"]["w"], meta["maps"][f"{extent}_72"]["h"])


_MAP_CACHE = {}


def _map_layers(key, names_hit):
    ck = (key, tuple(sorted(names_hit)))
    if ck not in _MAP_CACHE:
        maps, meta = _maps()
        idx = maps[key]
        regions = meta["regions"]
        hit_ids = [regions.index(n) + 1 for n in names_hit if n in regions]
        land = idx > 0
        hit = np.isin(idx, hit_ids)
        pad = np.pad(idx, 1)
        edge = land & ((pad[:-2, 1:-1] != idx) | (pad[2:, 1:-1] != idx) | (pad[1:-1, :-2] != idx) | (pad[1:-1, 2:] != idx))
        if len(_MAP_CACHE) > 64:
            _MAP_CACHE.clear()
        _MAP_CACHE[ck] = (land, hit, edge, meta["maps"][key])
    return _MAP_CACHE[ck]


def region_map(c, ctx, x, y, w, h=999, extent="metro", font=None, labels_on=True, land=None, fill=None, k=1.0):
    """The region map at (x, y): land in a quiet tone, the alert's regions in the hazard colour (``k`` scales
    the fill — the designs pulse it), region borders, codes on the affected regions, home house. Returns (w, h)."""
    key, mw, mh = map_size(extent, w, h)
    landm, hit, edge, meta = _map_layers(key, affected(ctx))
    region = c.buf[y:y + mh, x:x + mw]
    land_col = np.array(land or _mix(ctx.pal["panel"], (90, 96, 110), 0.35), np.uint8)
    fill_col = np.array(_dim(fill or hazard_colour(ctx), k), np.uint8)
    region[landm] = land_col
    region[hit] = fill_col
    region[edge & ~hit] = _dim(land_col, 0.45)
    region[edge & hit] = _mix(tuple(fill_col), (0, 0, 0), 0.55)
    if font is not None and labels_on:
        for name, (ax, ay) in meta["anchors"].items():
            if name in affected(ctx):
                t = CODES.get(name, name.upper())
                tw = font.width(t)
                tx = max(x, min(x + mw - tw, x + ax - tw // 2))
                ty = max(y, min(y + mh - font.h, y + ay - font.h // 2))
                outline_text(c, font, tx, ty, t, (255, 255, 255))
    hx, hy = meta["home"]
    if 0 <= hx < mw and 0 <= hy < mh:
        draw_house(c, x + hx, y + hy, home_hit(ctx), ctx)
    return mw, mh


def outline_text(c, font, x, y, s, col, out=(0, 0, 0)):
    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        font.draw(c, x + dx, y + dy, out, s)
    font.draw(c, x, y, col, s)


HOUSE = ["...#...", "..###..", ".#####.", "#######", ".##.##.", ".##.##.", ".#####."]
_HOUSE = np.array([[ch == "#" for ch in r] for r in HOUSE])


def draw_house(c, cx, cy, lit, ctx):
    """Home marker: bright on the severity colour when the alert covers home, a dim outline when it doesn't."""
    m = _HOUSE
    h, w = m.shape
    ox, oy = int(cx) - w // 2, int(cy) - h // 2
    pad = np.pad(m, 1)
    ring = (pad[:-2, 1:-1] | pad[2:, 1:-1] | pad[1:-1, :-2] | pad[1:-1, 2:] | np.pad(m, 1)[1:-1, 1:-1])
    big = np.zeros((h + 2, w + 2), bool)
    big[1:-1, 1:-1] = ring
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        big[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx] |= ring
    c.BlitMask(big & ~np.pad(m, 1), ox - 1, oy - 1, Color(0, 0, 0))
    c.BlitMask(m, ox, oy, Color(*((255, 255, 255) if lit else (110, 110, 120))))


# tile cartogram: (col, row, colspan) per region
TILES = {
    "metro": {"cols": 2, "rows": 3, "tiles": {"North Shore": (0, 0, 2), "Vancouver": (0, 1, 1), "Tri-Cities": (1, 1, 1),
                                               "Richmond": (0, 2, 1), "Surrey": (1, 2, 1)}},
    "lm": {"cols": 5, "rows": 3, "tiles": {"Sea to Sky": (1, 0, 1), "North Shore": (2, 0, 2), "Sunshine Coast": (0, 1, 1),
                                           "Howe Sound": (1, 1, 1), "Vancouver": (2, 1, 1), "Tri-Cities": (3, 1, 1),
                                           "Abbotsford": (4, 1, 1), "Richmond": (2, 2, 1), "Surrey": (3, 2, 1),
                                           "Chilliwack": (4, 2, 1)}},
}


HOME_TINT = (255, 226, 120)      # the home region's tile outline / label when the alert doesn't cover it
TILE_SHORT = {"Tri-Cities": "TRI-C", "Howe Sound": "HOWE", "Sunshine Coast": "SUN C", "Sea to Sky": "SEA2SKY",
              "North Shore": "N VAN", "Abbotsford": "ABBY", "Chilliwack": "CHWK", "Richmond": "RICH", "Vancouver": "VAN",
              "Surrey": "SURREY"}


def is_home(ctx, name):
    return name == getattr(ctx, "home_name", "Richmond") or (name == "Metro Vancouver" and home_hit(ctx))


def tile_rects(extent, x, y, w, h, gap=1):
    t = TILES[extent]
    cw = (w - gap * (t["cols"] - 1)) // t["cols"]
    ch = (h - gap * (t["rows"] - 1)) // t["rows"]
    return {n: (x + col * (cw + gap), y + row * (ch + gap), cw * span + gap * (span - 1), ch)
            for n, (col, row, span) in t["tiles"].items()}


def cart_size(font, extent="lm", gap=1):
    """Natural size of the tile grid: every single tile wide enough for its code in ``font``."""
    t = TILES[extent]
    tw = max(font.width(CODES.get(n, n.upper())) for n, (_, _, span) in t["tiles"].items() if span == 1) + 6
    th = font.h + 5
    return t["cols"] * tw + gap * (t["cols"] - 1), t["rows"] * th + gap * (t["rows"] - 1)


def cartogram(c, ctx, x, y, font, extent="lm", w=None, h=None, gap=1):
    """The regions as a geographic grid of labelled tiles: affected tiles filled in the hazard colour, others
    outlined; the home region in a lighter tint (filled) or a warm outline (clear). Labels shorten to fit."""
    nw, nh = cart_size(font, extent, gap)
    w, h = w or nw, h or nh
    hit = affected(ctx)
    fill = hazard_colour(ctx)
    home = getattr(ctx, "home_name", "Richmond")
    for name, (tx, ty, tw, th) in tile_rects(extent, x, y, w, h, gap).items():
        on, mine = name in hit, name == home
        if on:
            col = _mix(fill, (255, 255, 255), 0.45) if mine else fill
            c.FillRect(tx, ty, tw, th, Color(*col))
            ink = (0, 0, 0) if sum(col) > 300 else (255, 255, 255)
        else:
            edge = HOME_TINT if mine else _dim(ctx.pal["dim"], 0.7)
            c.FillRect(tx, ty, tw, th, Color(*ctx.pal["panel"]))
            for rx, ry, rw, rh in ((tx, ty, tw, 1), (tx, ty + th - 1, tw, 1), (tx, ty, 1, th), (tx + tw - 1, ty, 1, th)):
                c.FillRect(rx, ry, rw, rh, Color(*edge))
            ink = HOME_TINT if mine else ctx.pal["dim"]
        code = CODES.get(name, name.upper())
        for cand in (code, TILE_SHORT.get(name, code)):
            if font.width(cand) <= tw - 2:
                code = cand
                break
        else:
            while code and font.width(code) > tw - 2:
                code = code[:-1]
        font.draw(c, tx + (tw - font.width(code)) // 2, ty + (th - font.h) // 2, ink, code)
    return w, h


def chips(c, ctx, x, y, maxw, font, pad=3, gap=4, lines=2):
    """Region codes as chips: the home region filled in the severity colour, others outlined. Wraps up to
    ``lines`` rows; a chip that would not fit is left off (never drawn past ``maxw``). Returns the height."""
    hh = font.h + 4
    cx, cy, used = x, y, hh
    for name, code in labels(ctx):
        w = font.width(code) + 2 * pad
        if cx + w > x + maxw and cx > x:
            if (cy - y) // (hh + 3) + 1 >= lines:
                break
            cx, cy = x, cy + hh + 3
            used = cy - y + hh
        if w > maxw:
            break
        if is_home(ctx, name):
            c.FillRect(cx, cy, w, hh, Color(*ctx.sev))
            font.draw(c, cx + pad, cy + 2, (0, 0, 0), code)
        else:
            for rx, ry, rw, rh in ((cx, cy, w, 1), (cx, cy + hh - 1, w, 1), (cx, cy, 1, hh), (cx + w - 1, cy, 1, hh)):
                c.FillRect(rx, ry, rw, rh, Color(*ctx.sev))
            font.draw(c, cx + pad, cy + 2, ctx.pal["strong"], code)
        cx += w + gap
    return used


# ------------------------------------------------------------------------------------------------ when
def times(ctx):
    return ctx.b.get("_issued_ts"), ctx.b.get("_expires_ts")


def remaining_text(ctx, now=None):
    i0, i1 = times(ctx)
    if not i1:
        return ""
    left = max(0, i1 - (now or time.time()))
    h = int(left // 3600)
    return f"{h}h" if h >= 2 else f"{h}h{int(left % 3600 // 60):02d}" if h else f"{int(left // 60)}m"


_RINGS = {}


def countdown_ring(c, ctx, cx, cy, r, font, small, now=None):
    """How much of the alert's lifetime is left, as a ring that empties; the hours left in the middle."""
    if r not in _RINGS:
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        d = np.hypot(xx, yy)
        ang = (np.arctan2(xx, -yy) % (2 * np.pi)) / (2 * np.pi)
        _RINGS[r] = ((d <= r + 0.3) & (d >= r - 3.2), ang)
    ring, ang = _RINGS[r]
    i0, i1 = times(ctx)
    now = now or time.time()
    frac = 1.0 if not (i0 and i1 and i1 > i0) else max(0.0, min(1.0, (i1 - now) / (i1 - i0)))
    box = c.buf[cy - r:cy + r + 1, cx - r:cx + r + 1]
    if box.shape[:2] != ring.shape:
        return
    box[ring] = _dim(ctx.sev, 0.22)
    box[ring & (ang <= frac)] = ctx.sev
    t = remaining_text(ctx, now) or "-"
    if font.width(t) > 2 * r - 6:
        font = small
    font.draw(c, cx - font.width(t) // 2, cy - font.h // 2 - 2, ctx.pal["strong"], t)
    lab = "LEFT"
    small.draw(c, cx - small.width(lab) // 2, cy + font.h // 2, ctx.pal["dim"], lab)


def timeline(c, ctx, x, y, w, font, now=None):
    """issued ─── NOW ─── until: the alert's validity as a bar with a live marker. Returns height."""
    i0, i1 = times(ctx)
    b = ctx.b
    now = now or time.time()
    frac = (now - i0) / (i1 - i0) if i0 and i1 and i1 > i0 else 0.5
    frac = max(0.0, min(1.0, frac))
    a = ("ISSUED " + b["issued"]).upper() if b.get("issued") else ""
    z = ("UNTIL " + b["until"]).upper() if b.get("until") else ""
    if font.width(a) + font.width(z) + 8 > w:          # no room for both: the end time matters more
        a = ""
    while z and font.width(z) > w:
        z = z[:-1]
    font.draw(c, x, y, ctx.pal["dim"], a)
    font.draw(c, x + w - font.width(z), y, ctx.pal["strong"], z)
    by = y + font.line + 2
    c.FillRect(x, by, w, 3, Color(*_dim(ctx.sev, 0.25)))
    c.FillRect(x, by, int(w * frac), 3, Color(*ctx.sev))
    mx = x + int(w * frac)
    c.FillRect(mx - 1, by - 2, 3, 7, Color(255, 255, 255))
    return font.line + 8


# ------------------------------------------------------------------------------------------------ how bad / how much
SHORT_LEVEL = {"warning": "WARN", "watch": "WATCH", "advisory": "ADVSY", "statement": "STMT"}
LEVELS = [("warning", "WARNING"), ("watch", "WATCH"), ("advisory", "ADVISORY"), ("statement", "STATEMENT")]


def severity_ladder(c, ctx, x, y, w, h, font, t=0.0, vertical=True):
    """The four ECCC alert levels with this alert's lit (and blinking gently)."""
    from display.modules.alert_themes import SEVERITY
    n = len(LEVELS)
    for i, (kind, lab) in enumerate(LEVELS):
        on = kind == ctx.b["kind"]
        col = SEVERITY[kind]
        if vertical:
            rh = h // n
            rx, ry, rw, rhh = x, y + i * rh, w, rh - 2
        else:
            cw = w // n
            rx, ry, rw, rhh = x + i * cw, y, cw - 2, h
        glow = 0.75 + 0.25 * math.sin(t * 4) if on else 0.16
        c.FillRect(rx, ry, rw, rhh, Color(*_dim(col, glow)))
        tc = (0, 0, 0) if on else _dim(col, 0.7)
        if font.width(lab) > rw - 2:
            lab = SHORT_LEVEL[kind] if font.width(SHORT_LEVEL[kind]) <= rw - 2 else lab[0]
        font.draw(c, rx + (rw - font.width(lab)) // 2, ry + (rhh - font.h) // 2, tc, lab)


AMOUNT_RE = re.compile(r"(?:(\d+(?:\.\d+)?)\s*(?:to|-|–)\s*)?(?:up to\s*)?(\d+(?:\.\d+)?)\s*(mm|cm|km/h|°c|degrees|c\b)", re.I)
SCALES = {"mm": (0, 150, "mm"), "cm": (0, 60, "cm"), "km/h": (0, 150, "km/h"), "°c": (20, 45, "°C"), "degrees": (20, 45, "°C"),
          "c": (20, 45, "°C")}


def parse_amount(ctx):
    """(lo, hi, unit, scale_max, scale_min) from the alert's key amount, or None."""
    for src in (ctx.b.get("amount") or "", " ".join([ctx.b.get("summary") or ""] + list(ctx.b.get("details") or []))):
        from display.modules.alert_brief import is_motion
        m = next((m for m in AMOUNT_RE.finditer(src) if not is_motion(src, m.start())), None)
        if m:
            lo = float(m.group(1)) if m.group(1) else None
            hi = float(m.group(2))
            unit = m.group(3).lower()
            smin, smax, ulab = SCALES.get(unit, (0, max(10.0, hi * 1.4), unit))
            if unit in ("°c", "degrees", "c") and ctx.b["hazard"] == "cold":
                smin, smax = -50, 0
                hi = -abs(hi)
                lo = -abs(lo) if lo is not None else None
            smax = max(smax, hi * 1.1)
            return (lo if lo is not None else smin, hi, ulab, smax, smin)
    return None


def amount_gauge(c, ctx, x, y, w, h, font, small):
    """The forecast amount as a range on a scale (0–150 mm, 0–60 cm, 0–150 km/h, 20–45 °C). False if none."""
    a = parse_amount(ctx)
    if not a:
        return False
    lo, hi, unit, smax, smin = a
    val = f"{lo:g}-{hi:g} {unit}" if lo != smin else f"up to {hi:g} {unit}"
    if font.width(val) > w:
        font = small
    font.draw(c, x, y, ctx.pal["amount"], val)
    by = y + font.line + 1
    bh = max(3, h - font.line - small.line - 2)
    c.FillRect(x, by, w, bh, Color(*_dim(ctx.pal["panel"], 1.4)))
    sx = lambda v: x + int((v - smin) / (smax - smin) * (w - 1))
    c.FillRect(sx(lo), by, max(2, sx(hi) - sx(lo)), bh, Color(*hazard_colour(ctx)))
    c.FillRect(sx(hi), by - 1, 1, bh + 2, Color(255, 255, 255))
    for q in (0, 0.5, 1.0):
        v = smin + (smax - smin) * q
        lab = f"{v:g}"
        lx = max(x, min(x + w - small.width(lab), sx(v) - small.width(lab) // 2))
        small.draw(c, lx, by + bh + 1, ctx.pal["dim"], lab)
    return True


# ------------------------------------------------------------------------------------------------ hazard scenes
_RNG = np.random.default_rng(7)
_SEEDS = _RNG.random((160, 3))


def scene(c, ctx, x, y, w, h, t, icon=True):
    """A small animated weather scene for the hazard: falling rain, snow, wind streaks, lightning, rising water,
    heat shimmer, fog banks, smoke, a spinning funnel. Drawn every frame (cheap: ≤160 particles)."""
    hz = ctx.b["hazard"]
    col = hazard_colour(ctx)
    region = c.buf[y:y + h, x:x + w]
    top = np.array(_dim(ctx.pal["bg"], 1.0), np.float32)
    region[:] = np.linspace(top, np.array(_mix(ctx.pal["bg"], col, 0.18), np.float32), h)[:, None, :].astype(np.uint8)
    S = _SEEDS
    if hz in ("rain", "thunder", "freezing", "hail", "flood"):
        n = 90 if hz != "flood" else 40
        for i in range(n):
            sx, sy, sp = S[i]
            yy = (sy * h + t * (60 + sp * 60)) % (h + 8) - 4
            xx = (sx * (w + 20) - yy * 0.35) % w
            c.FillRect(x + int(xx), y + int(yy), 1, 3, Color(*_mix(col, (255, 255, 255), 0.3 * sp)))
        if hz == "freezing":
            for i in range(0, w, 3):
                c.FillRect(x + i, y + h - 2 - int(1.5 + 1.5 * math.sin(i * 0.7)), 2, 2, Color(200, 230, 255))
        if hz in ("thunder", "hail") and int(t * 1.3) % 4 == 0 and (t * 1.3) % 1 < 0.35:
            region[:] = np.clip(region.astype(np.int16) + 70, 0, 255).astype(np.uint8)
            bx = x + w // 2 + int(8 * math.sin(int(t * 1.3) * 3.1))
            yy = y
            for k in range(6):
                nx = bx + (4 if k % 2 else -4)
                ny = yy + h // 6
                for s in range(h // 6):
                    c.FillRect(int(bx + (nx - bx) * s / max(1, h // 6)), yy + s, 2, 1, Color(255, 255, 220))
                bx, yy = nx, ny
        if hz == "flood":
            lvl = h * (0.55 + 0.08 * math.sin(t * 0.8))
            for i in range(w):
                wy = int(lvl + 2 * math.sin(i * 0.25 + t * 3))
                c.FillRect(x + i, y + wy, 1, h - wy, Color(*_dim(col, 0.75)))
                c.FillRect(x + i, y + wy, 1, 1, Color(160, 200, 255))
    elif hz in ("snow", "cold", "frost"):
        for i in range(110 if hz == "snow" else 50):
            sx, sy, sp = S[i]
            yy = (sy * h + t * (10 + sp * 14)) % h
            xx = (sx * w + 3 * math.sin(t * (0.8 + sp) + i)) % w
            s = 2 if sp > 0.7 else 1
            c.FillRect(x + int(xx), y + int(yy), s, s, Color(*_mix((230, 240, 255), col, 0.3)))
        if hz == "frost":
            for i in range(12):
                sx, sy, sp = S[100 + i]
                if (t * 2 + sp * 7) % 3 < 0.4:
                    xx, yy = x + int(sx * w), y + int(sy * h)
                    c.FillRect(xx - 2, yy, 5, 1, Color(255, 255, 255))
                    c.FillRect(xx, yy - 2, 1, 5, Color(255, 255, 255))
    elif hz == "wind":
        for i in range(36):
            sx, sy, sp = S[i]
            ln = int(6 + sp * 16)
            xx = (sx * (w + ln) + t * (90 + sp * 120)) % (w + ln) - ln
            yy = int(sy * h + 2 * math.sin(t * 2 + i))
            x0, x1 = max(0, int(xx)), min(w, int(xx) + ln)
            if x1 > x0 and 0 <= yy < h:
                c.FillRect(x + x0, y + yy, x1 - x0, 1, Color(*_mix(col, (255, 255, 255), 0.4 * sp)))
    elif hz == "tornado":
        cx = x + w // 2 + int(6 * math.sin(t * 0.7))
        for k in range(h):
            rw = int(2 + (h - k) * 0.35 * (w / 100))
            ph = t * 6 + k * 0.4
            off = int(rw * math.sin(ph))
            c.FillRect(cx + off - 1, y + k, 3, 1, Color(*_mix(col, (255, 255, 255), 0.3)))
            c.FillRect(cx - off, y + k, 1, 1, Color(*_dim(col, 0.6)))
    elif hz == "heat":
        for k in range(0, h, 2):
            sh = int(2 * math.sin(k * 0.4 + t * 5))
            region[k] = np.roll(region[k], sh, axis=0)
        r = int(min(w, h) * 0.22)
        cx, cy = x + w // 2, y + int(h * 0.4)
        yy, xx = np.mgrid[-r - 6:r + 7, -r - 6:r + 7]
        d = np.hypot(xx, yy)
        rays = (d > r + 2) & (d < r + 6) & ((np.arctan2(yy, xx) * 6 / math.pi + t * 2) % 2 < 0.8)
        disk = d <= r
        sub = c.buf[cy - r - 6:cy + r + 7, cx - r - 6:cx + r + 7]
        if sub.shape[:2] == d.shape:
            sub[disk] = (255, 200, 60)
            sub[rays] = (255, 140, 30)
    elif hz in ("fog", "smoke"):
        for i in range(6):
            sx, sy, sp = S[i]
            by = int(sy * h)
            bw = int(w * (0.4 + sp * 0.5))
            bx = int((sx * w + t * (6 + sp * 8)) % (w + bw)) - bw
            x0, x1 = max(0, bx), min(w, bx + bw)
            if x1 > x0:
                band = region[max(0, by - 2):by + 3, x0:x1]
                band[:] = np.clip(band.astype(np.int16) * 0.5 + np.array(col) * 0.35, 0, 255).astype(np.uint8)
        if hz == "smoke":
            for i in range(40):
                sx, sy, sp = S[i]
                yy = h - (sy * h + t * (8 + sp * 10)) % h
                xx = sx * w + 4 * math.sin(t + i)
                c.FillRect(x + int(xx) % w, y + int(yy), 2, 2, Color(*_mix((255, 140, 40), (120, 110, 100), sy)))
    else:                                                       # general: expanding warning rings
        cx, cy = x + w // 2, y + h // 2
        for k in range(3):
            r = ((t * 14 + k * 14) % 42)
            for a in range(0, 360, 6):
                xx = cx + int(r * math.cos(math.radians(a)))
                yy = cy + int(r * 0.7 * math.sin(math.radians(a)))
                if x <= xx < x + w and y <= yy < y + h:
                    c.FillRect(xx, yy, 1, 1, Color(*_dim(col, 1 - r / 42)))
    if icon:
        name = px.hazard(ctx.b["hazard"])
        px.outlined(c, name, x + (w - 24) // 2, y + (h - 24) // 2, ctx.pal["icon"])


# ------------------------------------------------------------------------------------------------ paged text
def paged_flow(layout_fn, ctx, paras, box, fonts, gap, max_pages):
    """Flow the text through ``max_pages`` copies of one box; tags each line with its page. Overflows (so the
    engine tries a smaller font) only if even that many pages aren't enough."""
    lines, over = layout_fn(paras, [box] * max_pages, fonts, ctx.pal, ctx.upper, gap=gap, home=ctx.home_name)
    page, last_y = 0, -1
    for ln in lines:
        if ln["y"] < last_y:
            page += 1
        ln["page"] = page
        last_y = ln["y"]
    return lines, over


def page_now(seg, ctx):
    """Which page to show: page 0 through the reveal, then the hold split evenly between pages."""
    pages = 1 + max((ln.get("page", 0) for ln in seg["lines"]), default=0)
    local = ctx.t - seg["start"] - seg["reveal"]
    if pages <= 1 or local < 0:
        return 0, pages
    per = max(4.0, seg["hold"] / pages)
    return min(pages - 1, int(local // per)), pages


def page_dots(c, x, y, page, pages, col, dim):
    for i in range(pages):
        c.FillRect(x + i * 6, y, 4, 4, Color(*(col if i == page else dim)))
