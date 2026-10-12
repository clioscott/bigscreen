"""Building blocks for the alert designs: where (region chips, the Lower Mainland tile grid), when (countdown
ring, validity timeline), how bad (the four alert levels). Flat-colour pixel UI at native size; fonts come from
the theme's family. Regions use the wall's own names (alert_brief.REGIONS); the home region gets its own tint.
"""

from __future__ import annotations

import math
import time

import numpy as np

from display.gfx import Color
from display.modules import alert_brief



def _dim(rgb, k):
    return tuple(max(0, min(255, int(v * k))) for v in rgb)


def _mix(a, b, k):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


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
    outlined; the home region in a lighter tint (filled) or a warm outline (clear). Labels shorten to fit.
    Affected tiles take the weather type's main colour; the home tile its second colour."""
    nw, nh = cart_size(font, extent, gap)
    w, h = w or nw, h or nh
    hit = affected(ctx)
    fill = ctx.sev
    home = getattr(ctx, "home_name", "Richmond")
    for name, (tx, ty, tw, th) in tile_rects(extent, x, y, w, h, gap).items():
        on, mine = name in hit, name == home
        if on:
            col = getattr(ctx, "sev2", _mix(fill, (255, 255, 255), 0.45)) if mine else fill
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


ALL_METRO = "Metro Vancouver (All Regions)"


def label_variants(ctx):
    """The affected regions worded from fullest to shortest — ECCC's own text for a single region, then the
    wall's names ("Metro Vancouver (All Regions)", then "Metro Vancouver"), then the short codes. Each variant
    is [(region name, text)], home first."""
    labs = labels(ctx)
    out = []
    regions = ctx.b.get("regions") or []
    if len(regions) == 1 and len(labs) == 1:
        out.append([(labs[0][0], regions[0])])
    if any(n == "Metro Vancouver" for n, _ in labs):
        out.append([(n, ALL_METRO if n == "Metro Vancouver" else n) for n, _ in labs])
    out.append([(n, n) for n, _ in labs])
    out.append([(n, c) for n, c in labs])
    return out


def _chip_rows(texts, font, maxw, pad, gap):
    rows, cx = 1, 0
    for t in texts:
        w = font.width(t) + 2 * pad
        if w > maxw:
            return 999
        if cx and cx + w > maxw:
            rows, cx = rows + 1, 0
        cx += w + gap
    return rows


def chips(c, ctx, x, y, maxw, font, pad=3, gap=4, lines=2):
    """The affected regions as chips — the fullest wording that fits in ``lines`` rows of ``maxw`` — the home
    region filled in the severity colour, others outlined. Never drawn past ``maxw``. Returns the height."""
    upper = getattr(ctx, "upper", False)
    items = None
    for var in label_variants(ctx):
        var = [(n, t.upper() if upper and t != CODES.get(n) else t) for n, t in var]
        if _chip_rows([t for _, t in var], font, maxw, pad, gap) <= lines:
            items = var
            break
    if items is None:                                    # even the codes overflow: as many as fit
        items = [(n, c_) for n, c_ in labels(ctx)]
    hh = font.h + 4
    cx, cy, used = x, y, hh
    for name, text in items:
        w = font.width(text) + 2 * pad
        if cx + w > x + maxw and cx > x:
            if (cy - y) // (hh + 3) + 1 >= lines:
                break
            cx, cy = x, cy + hh + 3
            used = cy - y + hh
        if w > maxw:
            break
        if is_home(ctx, name):
            c.FillRect(cx, cy, w, hh, Color(*ctx.sev))
            font.draw(c, cx + pad, cy + 2, (0, 0, 0), text)
        else:
            for rx, ry, rw, rh in ((cx, cy, w, 1), (cx, cy + hh - 1, w, 1), (cx, cy, 1, hh), (cx + w - 1, cy, 1, hh)):
                c.FillRect(rx, ry, rw, rh, Color(*ctx.sev))
            font.draw(c, cx + pad, cy + 2, ctx.pal["strong"], text)
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
