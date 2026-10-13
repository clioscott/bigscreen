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


HOME_TINT = (255, 226, 120)      # the home region's tile outline / label
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


# The Where grid, in three sizes. Each is columns west to east, each column split evenly (no holes, no one box
# singled out by size). A box is (name, wall region it belongs to, label - "|" marks a two-line break, short label).
# The grid picks the biggest size whose boxes all fit their full names. Regions off the main grids (Sunshine
# Coast, Howe Sound, Sea to Sky) only appear, as a column down the left, when the alert covers them.
def _b(name, region, label, short=None):
    return (name, region, label, short or label.replace("|", " "))


WHERE_TIERS = {
    "small": [[_b("North Shore", "North Shore", "NORTH SHORE", "N VAN"), _b("Vancouver", "Vancouver", "VANCOUVER", "VAN"),
               _b("Richmond", "Richmond", "RICHMOND", "RICH")],
              [_b("Tri-Cities", "Tri-Cities", "TRI-CITIES", "TRI-C"), _b("Surrey", "Surrey", "SURREY")],
              [_b("Abbotsford", "Abbotsford", "ABBOTSFORD", "ABBY"), _b("Chilliwack", "Chilliwack", "CHILLIWACK", "CHWK")]],
    "medium": [[_b("West Vancouver", "North Shore", "WEST|VANCOUVER", "W VAN"), _b("Vancouver", "Vancouver", "VANCOUVER", "VAN"),
                _b("Richmond", "Richmond", "RICHMOND", "RICH")],
               [_b("North Vancouver", "North Shore", "NORTH|VANCOUVER", "N VAN"), _b("Burnaby", "Vancouver", "BURNABY"),
                _b("Delta", "Richmond", "DELTA")],
               [_b("Tri-Cities", "Tri-Cities", "TRI-CITIES", "TRI-C"), _b("Surrey", "Surrey", "SURREY|WHITE ROCK", "SURREY")],
               [_b("Maple Ridge", "Tri-Cities", "MAPLE RIDGE|PITT MEADOWS", "MAPLE R"), _b("Langley", "Surrey", "LANGLEY")]],
    "large": [[_b("West Vancouver", "North Shore", "WEST|VANCOUVER", "W VAN"), _b("Vancouver", "Vancouver", "VANCOUVER", "VAN"),
               _b("Richmond", "Richmond", "RICHMOND", "RICH")],
              [_b("North Vancouver", "North Shore", "NORTH|VANCOUVER", "N VAN"), _b("Burnaby", "Vancouver", "BURNABY"),
               _b("Delta", "Richmond", "DELTA")],
              [_b("Port Moody", "Tri-Cities", "PORT|MOODY"), _b("New Westminster", "Vancouver", "NEW|WESTMINSTER", "NEW WEST"),
               _b("Surrey", "Surrey", "SURREY|WHITE ROCK", "SURREY")],
              [_b("Coquitlam", "Tri-Cities", "COQUITLAM"), _b("Port Coquitlam", "Tri-Cities", "PORT|COQUITLAM", "POCO"),
               _b("Langley", "Surrey", "LANGLEY")],
              [_b("Maple Ridge", "Tri-Cities", "MAPLE RIDGE|PITT MEADOWS", "MAPLE R"), _b("Abbotsford", "Abbotsford", "ABBOTSFORD", "ABBY")],
              [_b("Mission", "Abbotsford", "MISSION"), _b("Chilliwack", "Chilliwack", "CHILLIWACK", "CHWK")]],
}
WHERE_EXTRA = [_b("Sea to Sky", "Sea to Sky", "SEA TO|SKY", "S2S"), _b("Howe Sound", "Howe Sound", "HOWE|SOUND", "HOWE"),
               _b("Sunshine Coast", "Sunshine Coast", "SUNSHINE|COAST", "SUN C")]


def _layout(ctx, tier, x, y, w, h, gap):
    hit = affected(ctx)
    extra = [bx for bx in WHERE_EXTRA if bx[1] in hit]
    cols = ([extra] if extra else []) + WHERE_TIERS[tier]
    cw = (w - gap * (len(cols) - 1)) // len(cols)
    x += (w - (len(cols) * cw + gap * (len(cols) - 1))) // 2
    out = []
    for k, col in enumerate(cols):
        n = len(col)
        for r, bx in enumerate(col):
            ty0 = y + (h + gap) * r // n
            ty1 = y + (h + gap) * (r + 1) // n - gap
            out.append((bx, (x + k * (cw + gap), ty0, cw, ty1 - ty0)))
    return out


def _label_lines(font, bx, tw, th):
    """The lines to write in a box: the full name on one line, else on two, else the short label; None if
    even that doesn't fit."""
    full = bx[2]
    if font.width(full.replace("|", " ")) <= tw - 4 and th >= font.h + 3:
        return [full.replace("|", " ")], True
    if "|" in full and th >= 2 * font.h + 5:
        ls = full.split("|")
        if all(font.width(l_) <= tw - 4 for l_ in ls):
            return ls, True
    if font.width(bx[3]) <= tw - 2 and th >= font.h + 2:
        return [bx[3]], False
    return None, False


def where_tier(ctx, font, w, h, gap=1):
    """The biggest Where grid whose boxes all show their full names at this size."""
    for tier in ("large", "medium"):
        if all(_label_lines(font, bx, r[2], r[3])[1] for bx, r in _layout(ctx, tier, 0, 0, w, h, gap)):
            return tier
    return "small"


def cart_size(font, extent="lm", gap=1):
    """Natural size of the small Where grid: three boxes wide enough for the full region names in ``font``."""
    tw = max(font.width(bx[2].split("|")[0]) for col in WHERE_TIERS["small"] for bx in col) + 8
    th = font.h + 5
    return 3 * tw + gap * 2, 3 * th + gap * 2


def cartogram(c, ctx, x, y, font, extent="lm", w=None, h=None, gap=1, tier=None):
    """The Where grid (WHERE_TIERS): boxes in the alert's regions filled in the weather type's colour, the rest
    outlined; the home box in the second colour (outlined in its tint), or just the tint outline when not covered.
    Names are written in full and only fall back to short labels when a box is too small."""
    nw, nh = cart_size(font, extent, gap)
    w, h = w or nw, h or nh
    tier = tier or where_tier(ctx, font, w, h, gap)
    hit = affected(ctx)
    fill = ctx.sev
    home = getattr(ctx, "home_name", "Richmond")
    for bx, (tx, ty, tw, th) in _layout(ctx, tier, x, y, w, h, gap):
        on, mine = bx[1] in hit, bx[0] == home
        if on:
            col = getattr(ctx, "sev2", _mix(fill, (255, 255, 255), 0.45)) if mine else fill
            c.FillRect(tx, ty, tw, th, Color(*col))
            ink = (0, 0, 0) if sum(col) > 300 else (255, 255, 255)
            if mine:
                for rx, ry, rw, rr in ((tx, ty, tw, 1), (tx, ty + th - 1, tw, 1), (tx, ty, 1, th), (tx + tw - 1, ty, 1, th)):
                    c.FillRect(rx, ry, rw, rr, Color(*HOME_TINT))
        else:
            edge = HOME_TINT if mine else _dim(ctx.pal["dim"], 0.7)
            c.FillRect(tx, ty, tw, th, Color(*ctx.pal["panel"]))
            for rx, ry, rw, rh in ((tx, ty, tw, 1), (tx, ty + th - 1, tw, 1), (tx, ty, 1, th), (tx + tw - 1, ty, 1, th)):
                c.FillRect(rx, ry, rw, rh, Color(*edge))
            ink = HOME_TINT if mine else ctx.pal["dim"]
        lines, _ = _label_lines(font, bx, tw, th)
        if not lines:
            lab = bx[3]
            while lab and font.width(lab) > tw - 2:
                lab = lab[:-1]
            lines = [lab]
        y0 = ty + (th - len(lines) * font.h - (len(lines) - 1) * 2) // 2
        for i, l_ in enumerate(lines):
            font.draw(c, tx + (tw - font.width(l_)) // 2, y0 + i * (font.h + 2), ink, l_)
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


# ------------------------------------------------------------------------------------------------ city grid
# One box per community, on a rough west→east / north→south grid (not to scale), each tied to the ECCC forecast
# zone it's in so an alert lights exactly the towns its zones cover: (name, short label, zone prefix, col, row).
CITY_COLS, CITY_ROWS = 12, 8
CITY_TILES = [
    ("Powell River", "POWELL R", "Sunshine Coast - Saltery Bay", 0, 0),
    ("Pemberton", "PEMBRTN", "Whistler", 6, 0),
    ("Whistler", "WHISTLER", "Whistler", 6, 1),
    ("Squamish", "SQUAMISH", "Howe Sound", 5, 2),
    ("Sechelt", "SECHELT", "Sunshine Coast - Gibsons", 0, 3),
    ("Gibsons", "GIBSONS", "Sunshine Coast - Gibsons", 1, 3),
    ("Bowen Island", "BOWEN", "Howe Sound", 3, 3),
    ("Lions Bay", "LIONS BY", "Howe Sound", 4, 3),
    ("West Vancouver", "W VAN", "Metro Vancouver - North Shore", 3, 4),
    ("North Vancouver", "N VAN", "Metro Vancouver - North Shore", 4, 4),
    ("Port Moody", "PT MOODY", "Metro Vancouver - northeast", 6, 4),
    ("Coquitlam", "COQUITLM", "Metro Vancouver - northeast", 7, 4),
    ("Pitt Meadows", "PITT MDW", "Metro Vancouver - northeast", 8, 4),
    ("Maple Ridge", "MAPLE RG", "Metro Vancouver - northeast", 9, 4),
    ("Mission", "MISSION", "Fraser Valley - west", 10, 4),
    ("Harrison", "HARRISON", "Fraser Valley - central", 11, 4),
    ("Vancouver", "VAN", "Metro Vancouver - central", 3, 5),
    ("Burnaby", "BURNABY", "Metro Vancouver - central", 4, 5),
    ("New Westminster", "NEW WEST", "Metro Vancouver - central", 5, 5),
    ("Port Coquitlam", "PT COQ", "Metro Vancouver - northeast", 7, 5),
    ("Hope", "HOPE", "Fraser Valley - east", 11, 5),
    ("Richmond", "RICHMOND", "Metro Vancouver - southwest", 3, 6),
    ("Delta", "DELTA", "Metro Vancouver - southwest", 4, 6),
    ("Surrey", "SURREY", "Metro Vancouver - southeast", 5, 6),
    ("Langley", "LANGLEY", "Metro Vancouver - southeast", 7, 6),
    ("Abbotsford", "ABBOTSFD", "Fraser Valley - west", 9, 6),
    ("Chilliwack", "CHILLWK", "Fraser Valley - central", 10, 6),
    ("Tsawwassen", "TSAWWSN", "Metro Vancouver - southwest", 3, 7),
    ("White Rock", "WHT ROCK", "Metro Vancouver - southeast", 5, 7),
]


def city_hit(ctx, zone):
    return any(r.startswith(zone) for r in ctx.b.get("regions") or [])


def city_grid_size(font, gap=1):
    """Smallest (w, h) where every short label fits its box."""
    cw = max(font.width(s) for _, s, _, _, _ in CITY_TILES) + 4
    ch = font.h + 4
    return CITY_COLS * cw + gap * (CITY_COLS - 1), CITY_ROWS * ch + gap * (CITY_ROWS - 1)


def city_grid(c, ctx, x, y, w, h, font, gap=1):
    """Every community as a labelled box on the rough map grid: the alert's towns filled in the weather colour
    (home in the second colour), the rest outlined. The full name is used when it fits the box. Columns or
    rows with no towns shrink to nothing, so the grid uses the space it's given."""
    used_c = sorted({t[3] for t in CITY_TILES})
    used_r = sorted({t[4] for t in CITY_TILES})
    cw = (w - gap * (len(used_c) - 1)) // len(used_c)
    rh = (h - gap * (len(used_r) - 1)) // len(used_r)
    home = (getattr(ctx, "home_name", "") or "").lower()
    for name, short, zone, col, row in CITY_TILES:
        tx = x + used_c.index(col) * (cw + gap)
        ty = y + used_r.index(row) * (rh + gap)
        on, mine = city_hit(ctx, zone), name.lower() == home
        if on:
            fill = getattr(ctx, "sev2", ctx.sev) if mine else ctx.sev
            c.FillRect(tx, ty, cw, rh, Color(*fill))
            ink = (0, 0, 0) if sum(fill) > 300 else (255, 255, 255)
        else:
            edge = HOME_TINT if mine else _dim(ctx.pal["dim"], 0.6)
            c.FillRect(tx, ty, cw, rh, Color(*ctx.pal["panel"]))
            for rx, ry, rw, rhh in ((tx, ty, cw, 1), (tx, ty + rh - 1, cw, 1), (tx, ty, 1, rh), (tx + cw - 1, ty, 1, rh)):
                c.FillRect(rx, ry, rw, rhh, Color(*edge))
            ink = HOME_TINT if mine else ctx.pal["dim"]
        lab = name.upper() if font.width(name.upper()) <= cw - 3 else short
        while lab and font.width(lab) > cw - 2:
            lab = lab[:-1]
        font.draw(c, tx + (cw - font.width(lab)) // 2, ty + (rh - font.h) // 2, ink, lab)


# ------------------------------------------------------------------------------------------------ any-level grids
_GRIDS = {}


def grid_def(grid_id):
    """A tile grid built by tools/build_grids.py (assets/grids/<id>.json), or None."""
    if grid_id not in _GRIDS:
        import json
        from display.gfx import resolve_asset
        try:
            with open(resolve_asset(f"grids/{grid_id}.json"), encoding="utf-8") as f:
                _GRIDS[grid_id] = json.load(f)
        except (OSError, ValueError):
            _GRIDS[grid_id] = None
    return _GRIDS[grid_id]


def grid_cover(ctx, grid_id):
    """(full ids, partly covered ids, home id) for an alert on a grid, from the hub's coverage."""
    cov = getattr(ctx, "coverage", None) or {}
    full = set(map(str, (cov.get("full") or {}).get(grid_id) or []))
    part = set(map(str, (cov.get("part") or {}).get(grid_id) or [])) - full
    home = (getattr(ctx, "home_units", None) or {}).get(grid_id)
    return full, part, (str(home) if home is not None else None)


def _wrap2(f, name, w):
    """The name on one line, else split over two (at the best word break); None if neither fits ``w``."""
    if f.width(name) <= w:
        return [name]
    words = name.split()
    best = None
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        if f.width(a) <= w and f.width(b) <= w:
            score = max(f.width(a), f.width(b))
            if best is None or score < best[0]:
                best = (score, [a, b])
    return best[1] if best else None


def grid_units(ctx, grid_id):
    """The grid's units to draw: optional ones (Vancouver Island on a mainland grid) only when the alert covers
    them; columns and rows left empty close up."""
    g = grid_def(grid_id)
    full, part, _ = grid_cover(ctx, grid_id)
    units = [u for u in g["units"] if not u.get("optional") or str(u["id"]) in full or str(u["id"]) in part]
    cols = sorted({u["col"] for u in units})
    rows = sorted({u["row"] for u in units})
    return [dict(u, col=cols.index(u["col"]), row=rows.index(u["row"])) for u in units], len(cols), len(rows)


def unit_grid(c, ctx, grid_id, x, y, w, h, fonts, values=None, colour=None):
    """Draw a tile grid: one box per unit at its place on the coarse map grid, the alert's units filled in the
    weather colour, partly covered ones half-filled, home in the second colour (or a warm outline), the rest
    outlined. Labels are the unit's full name — one line, else two — in the largest of ``fonts`` that fits that
    box; an abbreviation only when even the smallest font can't hold the name.
    ``values`` {unit id: text} adds a value line; ``colour`` {unit id: rgb} fills by value instead."""
    g = grid_def(grid_id)
    if not g:
        return False
    full, part, home = grid_cover(ctx, grid_id)
    units, cols, rows = grid_units(ctx, grid_id)
    gap = 1
    cw = (w - gap * (cols - 1)) // cols
    rh = (h - gap * (rows - 1)) // rows
    for u in units:
        uid = str(u["id"])
        tx, ty = x + u["col"] * (cw + gap), y + u["row"] * (rh + gap)
        mine = uid == home
        fill = None
        if colour and uid in colour:
            fill = colour[uid]
        elif uid in full:
            fill = getattr(ctx, "sev2", ctx.sev) if mine else ctx.sev
        elif uid in part:
            fill = _dim(ctx.sev, 0.42)
        if fill:
            c.FillRect(tx, ty, cw, rh, Color(*fill))
            ink = (0, 0, 0) if sum(fill) > 300 else (255, 255, 255)
            if mine and (uid in full or uid in part):
                for rx, ry, rw, rr in ((tx, ty, cw, 1), (tx, ty + rh - 1, cw, 1), (tx, ty, 1, rh), (tx + cw - 1, ty, 1, rh)):
                    c.FillRect(rx, ry, rw, rr, Color(*HOME_TINT))
            if colour and uid in full:                         # value-coloured boxes: the alert's units outlined
                for rx, ry, rw, rr in ((tx, ty, cw, 1), (tx, ty + rh - 1, cw, 1), (tx, ty, 1, rh), (tx + cw - 1, ty, 1, rh)):
                    c.FillRect(rx, ry, rw, rr, Color(*(HOME_TINT if mine else (255, 255, 255))))
        else:
            edge = HOME_TINT if mine else _dim(ctx.pal["dim"], 0.55)
            c.FillRect(tx, ty, cw, rh, Color(*ctx.pal["panel"]))
            for rx, ry, rw, rr in ((tx, ty, cw, 1), (tx, ty + rh - 1, cw, 1), (tx, ty, 1, rh), (tx + cw - 1, ty, 1, rh)):
                c.FillRect(rx, ry, rw, rr, Color(*edge))
            ink = HOME_TINT if mine else ctx.pal["dim"]
        val = (values or {}).get(uid)
        name = u["labels"][0]
        lines, font = None, fonts[-1]
        for f in fonts:
            room = rh - 2 - ((f.h + 1) if val else 0)
            ls = _wrap2(f, name, cw - 3)
            if ls and len(ls) * (f.h + 1) - 1 <= room:
                lines, font = ls, f
                break
        if lines is None:                                       # last resort: the shortest variant that fits
            f = fonts[-1]
            room = rh - 2 - ((f.h + 1) if val else 0)
            for lab in u["labels"]:
                ls = _wrap2(f, lab, cw - 3)
                if ls and len(ls) * (f.h + 1) - 1 <= room:
                    lines = ls
                    break
            lines = lines or [u["labels"][-1]]
        n = len(lines) + (1 if val else 0)
        th = n * (font.h + 1) - 1
        yy = ty + (rh - th) // 2
        for s_ in lines:
            font.draw(c, tx + (cw - font.width(s_)) // 2, yy, ink, s_)
            yy += font.h + 1
        if val:
            vf = font
            font.draw(c, tx + (cw - vf.width(val)) // 2, yy, ink, val)
    return True


def pick_grid(ctx, prefer=None):
    """The level that tells this alert best: the finest grid where it lights a handful-to-dozens of units."""
    if prefer and grid_def(prefer):
        return prefer
    count = lambda gid: len(grid_cover(ctx, gid)[0])
    if 0 < count("municipalities_lm") <= 3 and count("communities_metro") > 0:
        return "communities_metro"
    if count("municipalities_lm") > 0:
        return "municipalities_lm"
    if count("regional_districts") or grid_cover(ctx, "regional_districts")[1]:
        return "regional_districts"
    return "provinces"
