"""Pixel art for the alert screen: hazard icons, small glyphs and background patterns.

Icons are 16x16 multi-colour bitmaps (one character per pixel) designed for the
LED grid; they're drawn at integer scales (1x-4x) so edges stay crisp. Colour
keys map to the theme palette at draw time:

    W  icon colour (palette "icon")      A  accent       K  background (cut-outs)
    B  water blue    C  ice cyan    Y  yellow    O  orange    R  red    G  grey
"""

from __future__ import annotations

import numpy as np

FIXED = {"B": (70, 150, 255), "C": (150, 225, 255), "Y": (255, 215, 0), "O": (255, 130, 0),
         "R": (235, 40, 30), "G": (150, 150, 165)}

ICONS = {
    "rain": [
        "......WWWW......",
        "....WWWWWWWW....",
        "...WWWWWWWWWW...",
        ".WWWWWWWWWWWWWW.",
        "WWWWWWWWWWWWWWWW",
        "WWWWWWWWWWWWWWWW",
        ".WWWWWWWWWWWWWW.",
        "................",
        "...B....B....B..",
        "..B....B....B...",
        "................",
        ".....B....B.....",
        "....B....B....B.",
        "................",
        "..B....B....B...",
        ".B....B....B....",
    ],
    "snow": [
        "................",
        ".......W........",
        "....W..W..W.....",
        ".....W.W.W......",
        "..W...WWW...W...",
        "...W...W...W....",
        "....W..W..W.....",
        ".....W.W.W......",
        "WWWWWWWWWWWWWWW.",
        ".....W.W.W......",
        "....W..W..W.....",
        "...W...W...W....",
        "..W...WWW...W...",
        ".....W.W.W......",
        "....W..W..W.....",
        ".......W........",
    ],
    "wind": [
        "................",
        "..........WWW...",
        ".............W..",
        ".............W..",
        "WWWWWWWWWWWWW...",
        "................",
        "WWWWWWWWWWWWWWW.",
        "...............W",
        "...............W",
        "...........WWWW.",
        "................",
        "WWWWWWWWW.......",
        ".........W......",
        ".........W......",
        ".......WW.......",
        "................",
    ],
    "thunder": [
        "......WWWW......",
        "....WWWWWWWW....",
        "...WWWWWWWWWW...",
        ".WWWWWWWWWWWWWW.",
        "WWWWWWWWWWWWWWWW",
        ".WWWWWWYYWWWWWW.",
        "......YY........",
        ".....YY.........",
        "....YYYYYY......",
        ".......YY.......",
        "......YY........",
        ".....YY.........",
        "....YY..........",
        "...Y............",
        "................",
        "................",
    ],
    "heat": [
        ".......Y........",
        "..Y....Y....Y...",
        "...Y.......Y....",
        "......YYY.......",
        "....YYYYYYY.....",
        "....YYYOYYY.....",
        "YY.YYYOOOYYY.YY.",
        "....YYYOYYY.....",
        "....YYYYYYY.....",
        "......YYY.......",
        "...Y.......Y....",
        "..Y....Y....Y...",
        ".......Y........",
        "................",
        "................",
        "................",
    ],
    "cold": [
        "......WWW.......",
        ".....W...W..C...",
        ".....W.C.W.CCC..",
        ".....W.C.W..C...",
        ".....W.C.W......",
        ".....W.C.W..C...",
        ".....W.C.W.CCC..",
        ".....W.C.W..C...",
        ".....W.C.W......",
        "....W.CCC.W.....",
        "...W.CCCCC.W....",
        "...W.CCCCC.W....",
        "....W.CCC.W.....",
        ".....WWWWW......",
        "................",
        "................",
    ],
    "fog": [
        "................",
        "WWWWWWWWWW......",
        "................",
        "...WWWWWWWWWWWWW",
        "................",
        "WWWWWWWWWWWWW...",
        "................",
        "....WWWWWWWWWWWW",
        "................",
        "WWWWWWWWW.......",
        "................",
        "..WWWWWWWWWWWW..",
        "................",
        "................",
        "................",
        "................",
    ],
    "freezing": [
        "......WWWW......",
        "....WWWWWWWW....",
        "...WWWWWWWWWW...",
        ".WWWWWWWWWWWWWW.",
        "WWWWWWWWWWWWWWWW",
        ".WWWWWWWWWWWWWW.",
        "................",
        "..C....C....C...",
        ".C....C....C....",
        "................",
        "CCCCCCCCCCCCCCCC",
        ".C.C..C.C..C.C..",
        ".C....C....C....",
        "................",
        "................",
        "................",
    ],
    "smoke": [
        "................",
        "........GGG.....",
        "......GGGGGGG...",
        ".....GGGGGGGGG..",
        "...GGGGGGGGGG...",
        "..GGGGGGGGG.....",
        ".GGGGGGGG.......",
        ".GGGGGG.........",
        "..GGGG....GGG...",
        "...GG...GGGGGG..",
        "......GGGGGGG...",
        ".....GGGGGG.....",
        "......GGG.......",
        "................",
        "................",
        "................",
    ],
    "flood": [
        "................",
        "......WWWW......",
        ".....WWWWWW.....",
        "....WWWWWWWW....",
        "....WWKKKKWW....",
        "....WWKKKKWW....",
        "................",
        "..BB.....BB.....",
        ".B..B...B..B...B",
        "B....BBB....BBB.",
        "................",
        "..BB.....BB.....",
        ".B..B...B..B...B",
        "B....BBB....BBB.",
        "................",
        "................",
    ],
    "tornado": [
        "WWWWWWWWWWWWWWWW",
        "................",
        ".WWWWWWWWWWWWW..",
        "................",
        "...WWWWWWWWWW...",
        "................",
        "....WWWWWWWW....",
        "................",
        ".....WWWWWW.....",
        "................",
        "......WWWW......",
        "................",
        ".......WW.......",
        "........W.......",
        "........W.......",
        "................",
    ],
    "frost": [
        "................",
        ".......C........",
        ".....C.C.C......",
        "......CCC.......",
        "..C....C....C...",
        "...C...C...C....",
        "CCCCCCCCCCCCCCC.",
        "...C...C...C....",
        "..C....C....C...",
        "......CCC.......",
        ".....C.C.C......",
        ".......C........",
        "................",
        "GGGGGGGGGGGGGGG.",
        "................",
        "................",
    ],
    "general": [
        ".......YY.......",
        "......YYYY......",
        "......YYYY......",
        ".....YYKKYY.....",
        ".....YYKKYY.....",
        "....YYYKKYYY....",
        "....YYYKKYYY....",
        "...YYYYKKYYYY...",
        "...YYYYKKYYYY...",
        "..YYYYYYYYYYYY..",
        "..YYYYYKKYYYYY..",
        ".YYYYYYKKYYYYYY.",
        ".YYYYYYYYYYYYYY.",
        "YYYYYYYYYYYYYYYY",
        "................",
        "................",
    ],
}
ICONS["hail"] = ICONS["thunder"]

GLYPHS = {   # 7x7 UI glyphs, single colour
    "clock": [".WWWWW.", "W..W..W", "W..W..W", "W..WWWW", "W.....W", "W.....W", ".WWWWW."],
    "pin": ["..WWW..", ".W...W.", ".W.W.W.", ".W...W.", "..W.W..", "..W.W..", "...W..."],
    "warn": ["...W...", "..W.W..", "..WWW..", ".W.W.W.", ".W...W.", "W..W..W", "WWWWWWW"],
    "arrow": ["...W...", "....W..", ".....W.", "WWWWWWW", ".....W.", "....W..", "...W..."],
    "check": [".......", "......W", ".....W.", "W...W..", ".W.W...", "..W....", "......."],
    "eye": [".......", "..WWW..", ".W...W.", "W..W..W", ".W...W.", "..WWW..", "......."],
    "drop": ["...W...", "..WWW..", "..WWW..", ".WWWWW.", ".WWWWW.", ".WWWWW.", "..WWW.."],
}


def _grid(rows):
    return [list(r.ljust(16, "."))[:16] for r in rows]


def icon_rgba(name, icon_rgb, accent_rgb, bg_rgb, scale=1):
    """(h, w, 4) uint8 RGBA for an icon at an integer scale."""
    rows = ICONS.get(name) or ICONS["general"]
    h, w = len(rows), max(len(r) for r in rows)
    out = np.zeros((h, w, 4), dtype=np.uint8)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch == ".":
                continue
            rgb = icon_rgb if ch == "W" else accent_rgb if ch == "A" else bg_rgb if ch == "K" else FIXED.get(ch, icon_rgb)
            out[y, x, :3] = rgb
            out[y, x, 3] = 255
    if scale > 1:
        out = np.repeat(np.repeat(out, scale, axis=0), scale, axis=1)
    return out


def glyph_mask(name, scale=1):
    rows = GLYPHS[name]
    m = np.array([[ch != "." for ch in r] for r in rows], dtype=bool)
    return np.repeat(np.repeat(m, scale, axis=0), scale, axis=1) if scale > 1 else m


def blit_rgba(c, rgba, x, y):
    """Alpha-blit (alpha is 0/255 here) onto a display.gfx Canvas."""
    h, w = rgba.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(c.width, x + w), min(c.height, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    src = rgba[y0 - y:y1 - y, x0 - x:x1 - x]
    m = src[..., 3] > 0
    c.buf[y0:y1, x0:x1][m] = src[..., :3][m]


def pattern(kind, w, h, a_rgb, b_rgb, phase=0):
    """(h, w, 3) background patterns: stripes (hazard tape), chevrons, dots, scan, grid, checker."""
    yy, xx = np.mgrid[0:h, 0:w]
    if kind == "stripes":
        on = ((xx + yy + phase) // 6) % 2 == 0
    elif kind == "chevrons":
        on = ((xx + np.abs((yy % 12) - 6) + phase) // 6) % 2 == 0
    elif kind == "dots":
        on = (xx % 4 == 0) & (yy % 4 == 0)
    elif kind == "scan":
        on = yy % 2 == 0
    elif kind == "grid":
        on = (xx % 8 == 0) | (yy % 8 == 0)
    elif kind == "checker":
        on = ((xx // 4) + (yy // 4)) % 2 == 0
    else:
        on = np.zeros((h, w), dtype=bool)
    out = np.empty((h, w, 3), dtype=np.uint8)
    out[:] = b_rgb
    out[on] = a_rgb
    return out
