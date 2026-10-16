"""Shared toolkit for the BC alert screens: Alert Ready (NAAD), DriveBC, earthquakes, tsunami, wildfire.

Each of those screens is a full-wall takeover (like the weather alert and the Japan EEW screens) with its own
look, built from the pieces here:

  * fonts      approved BDF faces only, at native size, by role (``font("h20")``)
  * text       measuring, word wrap, and ``fit`` — the largest font ladder step at which every paragraph fits a
               box, dropping the least important paragraphs only if even the smallest step overflows (never "…")
  * maps       basemaps rendered by the hub (data/bcmaps/<name>.bin + .json) with their projection
  * icons      the shared 1-bit pixel icons plus a few drawn here (flame, cone, barrier, wave, quake…)
  * caching    a theme draws its static layer once per item (``static``) and only animates on top of it,
               so holding a screen costs one array copy per frame on the Pi
  * BCScreen   the base class: items from the hub's JSON, one item per screen for its dwell time, themes,
               samples (labelled SAMPLE on the wall), previews for the control panel

Times on the wall are local (the Pi runs in America/Vancouver).
"""

from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime

import numpy as np

from display.gfx import Color, graphics
from display.modules import pixel_icons as px
from display.modules.weather_alert import FI

W, H = 448, 192
R = 6378137.0
BLACK, WHITE = (0, 0, 0), (255, 255, 255)

# ------------------------------------------------------------------------------------------------ fonts
_C, _A = "candidates/", "alert/"
FONTS = {
    # display faces (bold, proportional)
    "h24": _C + "u8-win-crox4hb.bdf", "h24r": _C + "u8-win-crox4h.bdf",
    "h20": _C + "u8-win-crox3hb.bdf", "h20r": _C + "u8-win-crox3h.bdf",
    "h18": _C + "x11-helvB18.bdf", "h18r": _C + "x11-helvR18.bdf",
    "h16": _C + "u8-win-crox2hb.bdf", "h16r": _C + "u8-win-crox2h.bdf",
    "h14": _C + "x11-helvB14.bdf", "h13": _C + "u8-win-crox1hb.bdf", "h13r": _C + "u8-win-crox1h.bdf",
    "hv12": _C + "x11-helvR12.bdf", "hv10": _C + "x11-helvR10.bdf", "hvb10": _C + "x11-helvB10.bdf",
    "lu12": _C + "x11-luRS12.bdf", "lub12": _C + "x11-luBS12.bdf", "lu14": _C + "x11-luRS14.bdf", "lub14": _C + "x11-luBS14.bdf",
    "lut12": _C + "x11-lutRS12.bdf", "lutb12": _C + "x11-lutBS12.bdf", "lutb18": _C + "x11-lutBS18.bdf",
    "lut10": _C + "x11-lutRS10.bdf", "lutb10": _C + "x11-lutBS10.bdf", "lutb14": _C + "x11-lutBS14.bdf", "lut14": _C + "x11-lutRS14.bdf",
    # monospace text
    "m20b": _C + "jmk-neep-10x20-bold.bdf", "m20": _C + "jmk-neep-10x20.bdf",
    "m16b": _A + "Tamzen8x16b.bdf", "m16": _A + "Tamzen8x16r.bdf",
    "m15b": _A + "9x15B.bdf", "m15": _A + "9x15.bdf",
    "m14b": _A + "7x14B.bdf", "m14": _A + "7x14.bdf", "t14b": _A + "t0-14b-uni.bdf", "t14": _A + "t0-14-uni.bdf",
    "m13b": _A + "7x13B.bdf", "m13": _A + "7x13.bdf", "t13b": _A + "t0-13b-uni.bdf", "t13": _A + "t0-13-uni.bdf",
    "t12b": _A + "t0-12b-uni.bdf", "t12": _A + "t0-12-uni.bdf", "ter12b": _C + "ter-u12b.bdf", "ter12": _C + "ter-u12n.bdf",
    "ter14b": _C + "ter-u14b.bdf", "ter14": _C + "ter-u14n.bdf",
    "c11b": _A + "cherry-11-b.bdf", "c11": _A + "cherry-11-r.bdf", "c13b": _A + "cherry-13-b.bdf", "c13": _A + "cherry-13-r.bdf",
    "m10": _A + "6x10.bdf", "m10b": _A + "ctrld-fixed-10b.bdf", "g10": _A + "gohufont-11.bdf", "g10b": _A + "gohufont-11b.bdf",
    "s8": _A + "5x8.bdf", "s8b": _C + "clean-clR5x8.bdf", "s7": _A + "5x7.bdf",
    "crt12": _A + "cozette.bdf", "pro15": _A + "profont15.bdf", "pro17": _A + "profont17.bdf", "pro22": _A + "profont22.bdf",
    "envy15": _C + "envypn7x15.bdf", "knxt20": _C + "knxt.bdf",
}
_FONT_OBJS = {}


def font(role):
    f = _FONT_OBJS.get(role)
    if f is None:
        f = _FONT_OBJS[role] = FI(FONTS.get(role, role), 1)
    return f


def preload(roles):
    for r in roles:
        try:
            font(r)
        except Exception as e:                                      # a missing font never stops the wall
            print(f"[bc] font {r}: {e}")


# ------------------------------------------------------------------------------------------------ colour
def C(rgb):
    return rgb if isinstance(rgb, Color) else Color(*rgb)


def dim(rgb, k):
    return tuple(max(0, min(255, int(v * k))) for v in rgb)


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def pulse(now, period=1.2, lo=0.45, hi=1.0):
    return lo + (hi - lo) * (0.5 + 0.5 * math.sin(now * 2 * math.pi / period))


def blink(now, period=1.0, duty=0.5):
    return (now % period) < period * duty


# ------------------------------------------------------------------------------------------------ text
def tw(f, s):
    return f.width(s) if s else 0


def text(c, f, x, y, col, s):
    """Draw ``s`` with its top at y; returns the advance."""
    return f.draw(c, int(x), int(y), col, s) if s else 0


def text_c(c, f, cx, y, col, s):
    return text(c, f, int(cx - tw(f, s) / 2), y, col, s)


def text_r(c, f, rx, y, col, s):
    return text(c, f, int(rx - tw(f, s)), y, col, s)


def wrap(f, s, width):
    """Word-wrap into lines no wider than ``width`` (a word longer than a line is split, never cut)."""
    out = []
    for para in str(s or "").split("\n"):
        words, line = para.split(), ""
        for w in words:
            cand = f"{line} {w}" if line else w
            if tw(f, cand) <= width:
                line = cand
                continue
            if line:
                out.append(line)
            while tw(f, w) > width and len(w) > 1:              # a URL or a very long name: break it by width
                k = len(w)
                while k > 1 and tw(f, w[:k]) > width:
                    k -= 1
                out.append(w[:k])
                w = w[k:]
            line = w
        if line:
            out.append(line)
    return out


def fits_one_line(f, s, width):
    return tw(f, s) <= width


def pick(roles, s, width):
    """The first (largest) font role in which ``s`` fits on one line (else the last)."""
    for r in roles:
        if tw(font(r), s) <= width:
            return font(r)
    return font(roles[-1])


class Para:
    """A paragraph for ``fit``: text, a style key (looked up in the ladder step), colour, importance (0 = keep)."""

    __slots__ = ("text", "style", "col", "prio", "gap")

    def __init__(self, text, style="body", col=WHITE, prio=0, gap=None):
        self.text, self.style, self.col, self.prio, self.gap = text, style, col, prio, gap


def fit(paras, w, h, ladder, gap=3, line_gap=0):
    """Lay paragraphs into a w×h box: tries each ladder step ({style: font role}) from largest to smallest; if
    even the smallest overflows, drops the least important paragraph (highest prio) and tries again.
    Returns [(font, colour, dx, dy, text)] and the height used."""
    paras = [p for p in paras if p and p.text]
    while True:
        for step in ladder:
            lines, y = [], 0
            ok = True
            for i, p in enumerate(paras):
                f = font(step.get(p.style) or step.get("body"))
                for ln in wrap(f, p.text, w):
                    if y + f.h > h:
                        ok = False
                        break
                    lines.append((f, p.col, 0, y, ln))
                    y += f.h + line_gap
                if not ok:
                    break
                y += (p.gap if p.gap is not None else gap)
            if ok:
                return lines, max(0, y - gap)
        droppable = [p for p in paras if p.prio > 0]
        if not droppable:                                        # draw what fits at the smallest step
            return lines, h
        worst = max(droppable, key=lambda p: p.prio)
        paras = [p for p in paras if p is not worst]


def draw_fit(c, lines, x, y):
    for f, col, dx, dy, s in lines:
        text(c, f, x + dx, y + dy, col, s)


def fit_text(c, s, x, y, w, h, roles, col, centre=False, line_gap=1):
    """One paragraph in the largest role that fits the box; returns the height used."""
    for r in roles:
        f = font(r)
        lines = wrap(f, s, w)
        if len(lines) * (f.h + line_gap) - line_gap <= h or r == roles[-1]:
            for i, ln in enumerate(lines):
                ty = y + i * (f.h + line_gap)
                if ty + f.h > y + h + 2:
                    break
                (text_c(c, f, x + w // 2, ty, col, ln) if centre else text(c, f, x, ty, col, ln))
            return len(lines) * (f.h + line_gap) - line_gap
    return 0


# ------------------------------------------------------------------------------------------------ shapes
def box(c, x, y, w, h, col):
    c.FillRect(int(x), int(y), int(w), int(h), C(col))


def frame(c, x, y, w, h, col, t=1):
    x, y, w, h = int(x), int(y), int(w), int(h)
    box(c, x, y, w, t, col)
    box(c, x, y + h - t, w, t, col)
    box(c, x, y, t, h, col)
    box(c, x + w - t, y, t, h, col)


def line(c, x0, y0, x1, y1, col):
    graphics.DrawLine(c, int(x0), int(y0), int(x1), int(y1), C(col))


def thick_line(c, x0, y0, x1, y1, col, t=2):
    for d in range(t):
        o = d - t // 2
        if abs(x1 - x0) >= abs(y1 - y0):
            line(c, x0, y0 + o, x1, y1 + o, col)
        else:
            line(c, x0 + o, y0, x1 + o, y1, col)


def circle(c, cx, cy, r, col):
    if r >= 1:
        graphics.DrawCircle(c, int(cx), int(cy), int(r), C(col))


def circle_clip(c, cx, cy, r, col, clip):
    """A 1-px circle drawn only inside clip = (x0, y0, x1, y1)."""
    if r < 1:
        return
    x0, y0, x1, y1 = clip
    n = max(16, int(2 * math.pi * r))
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    xs = np.rint(cx + r * np.cos(a)).astype(int)
    ys = np.rint(cy + r * np.sin(a)).astype(int)
    m = (xs >= max(0, x0)) & (xs < min(W, x1)) & (ys >= max(0, y0)) & (ys < min(H, y1))
    c.buf[ys[m], xs[m]] = col


def disc(c, cx, cy, r, col):
    r = int(r)
    for dy in range(-r, r + 1):
        dx = int(math.sqrt(max(0, r * r - dy * dy)))
        box(c, cx - dx, cy + dy, 2 * dx + 1, 1, col)


def chip(c, f, x, y, s, bg, fg, padx=3, pady=1):
    """A filled label; returns its width."""
    w = tw(f, s) + 2 * padx
    box(c, x, y, w, f.h + 2 * pady, bg)
    text(c, f, x + padx, y + pady, fg, s)
    return w


def hatch(c, x, y, w, h, col, step=4, phase=0):
    """Diagonal stripes (closures, hazard bands)."""
    x, y, w, h = int(x), int(y), int(w), int(h)
    sub = c.buf[y:y + h, x:x + w]
    if sub.size == 0:
        return
    yy, xx = np.mgrid[0:sub.shape[0], 0:sub.shape[1]]
    m = ((xx + yy + int(phase)) % (2 * step)) < step
    sub[m] = col


def shade(c, x, y, w, h, k=0.35):
    x0, y0 = max(0, int(x)), max(0, int(y))
    sub = c.buf[y0:int(y + h), x0:int(x + w)]
    if sub.size:
        sub[:] = (sub.astype(np.uint16) * int(k * 256) >> 8).astype(np.uint8)


def gradient_v(c, x, y, w, h, top, bottom):
    for i in range(int(h)):
        box(c, x, y + i, w, 1, mix(top, bottom, i / max(1, h - 1)))


# ------------------------------------------------------------------------------------------------ icons
# 1-bit art drawn for these screens (16 px class unless noted); '#' = lit.
ART = {
    "flame": ["......#.........", ".....##.........", ".....###........", "....####...#....", "...#####..##....",
              "...######.###...", "..###########...", "..############..", ".#####.#######..", ".####...######..",
              ".###.....#####..", ".###..#..#####..", "..##.###.####...", "..###########...", "...#########....",
              ".....#####......"],
    "cone": [".......##.......", "......####......", "......####......", ".....######.....", ".....#....#.....",
             "....########....", "....########....", "...##########...", "...#........#...", "..############..",
             "..############..", ".##############.", "################", "################"],
    "barrier": ["################", "#..##..##..##..#", "##..##..##..##.#", "#.##..##..##..##", "################",
                "..##........##..", "..##........##..", "################", "#..##..##..##..#", "##..##..##..##.#",
                "#.##..##..##..##", "################", "..##........##..", "..##........##..", ".####......####."],
    "crash": ["......####......", "....########....", "...##......##...", "..##........##..", ".##############.",
              "################", "##..########..##", "################", ".###........###.", ".###........###.",
              "................", "..#..#....#..#..", ".#.#..#..#..#.#.", "#...#..##..#...#"],
    "wave": ["........######..", "......###....##.", ".....##.......#.", "....##..####....", "...##..##..##...",
             "..##..##....#...", "..#..##.........", ".##.##..........", ".#..#........#..", "##.##.......###.",
             "#..#.....#####.#", "#.##...######..#", "####.#######...#", "################", "################"],
    "quake": ["................", "......#.........", "......##........", "......#.#.......", "#.....#.#.....#.",
              "##...#...#...##.", "#.#..#...#..#.#.", "#..#.#....#.#..#", "#...##.....#...#", "#....#.........#",
              "................", "################", "#..#..#..#..#..#", "################"],
    "siren": ["......####......", ".....######.....", "....##....##....", "....#......#....", "...##..##..##...",
              "...#..####..#...", "...#..####..#...", "...#........#...", "..############..", "..############..",
              ".##############.", ".##############."],
    "megaphone": ["............##..", "..........####..", "........##..#...", "#####..##...#...", "#####.#.....#...",
                  "#####.#.....#...", "#####.#.....#...", "#####..##...#...", "..#.....##..#...", "..#.......####..",
                  "..##........##..", "...#............"],
    "child": [".....####.......", "....######......", "....######......", ".....####.......", "......##........",
              "...########.....", "..##.####.##....", ".##..####..##...", ".....####.......", ".....####.......",
              "....##..##......", "....##..##......", "...##....##.....", "...##....##....."],
    "house": [".......##.......", "......####......", ".....######.....", "....########....", "...##########...",
              "..############..", ".##############.", "..##........##..", "..##.###....##..", "..##.###.##.##..",
              "..##.###.##.##..", "..##.###....##..", "..############.."],
    "drop": [".......#........", "......###.......", "......###.......", ".....#####......", "....#######.....",
             "...#########....", "..###########...", "..###########...", ".#############..", ".#############..",
             ".#############..", "..###########...", "...#########....", ".....#####......"],
    "bolt": ["........####....", ".......####.....", "......####......", ".....####.......", "....##########..",
             ".......####.....", "......####......", ".....####.......", "....###.........", "...##...........",
             "..#............."],
    "phone": ["..##########....", "..#........#....", "..#.######.#....", "..#.######.#....", "..#.######.#....",
              "..#.######.#....", "..#.######.#....", "..#........#....", "..#...##...#....", "..##########...."],
    "hazmat": [".......##.......", "......####......", ".....##..##.....", "....##.##.##....", "...##..##..##...",
               "..##...##...##..", ".##....##....##.", "##.....##.....##", "##............##", ".##....##....##.",
               "..##...##...##..", "...##......##...", "....##....##....", ".....##..##.....", "......####......",
               ".......##......."],
    "shield": ["################", "#..............#", "#..............#", "#..............#", "#..............#",
               "#..............#", ".#............#.", ".#............#.", "..#..........#..", "...#........#...",
               "....##....##....", "......####......"],
    "tree": [".......##.......", "......####......", ".....######.....", "......####......", ".....######.....",
             "....########....", ".....######.....", "....########....", "...##########...", "..############..",
             ".......##.......", ".......##.......", "......####......"],
    "arrow_up": ["......##......", ".....####.....", "....######....", "...########...", "..##########..",
                 ".....####.....", ".....####.....", ".....####.....", ".....####.....", ".....####....."],
    "run": [".........##.....", "........####....", "........####....", ".........##.....", "......######....",
            ".....###.##.##..", "....#.####...#..", "......####......", ".....##..##.....", "....##....##....",
            "...##......#....", "..##.......##..."],
}
_ART_MASKS = {}


def art(name):
    m = _ART_MASKS.get(name)
    if m is None:
        rows = ART[name]
        wd = max(len(r) for r in rows)
        m = _ART_MASKS[name] = np.array([[ch == "#" for ch in r.ljust(wd, ".")] for r in rows], dtype=bool)
    return m


def icon(c, name, x, y, col, size=16):
    """Draw an icon from ART or the shared pixel icon set; returns its width."""
    if name in ART:
        m = art(name)
        if size and size != 16 and m.shape[0] != size:           # ART is drawn at 16; 2× for the big plates
            k = max(1, int(round(size / 16)))
            m = np.repeat(np.repeat(m, k, axis=0), k, axis=1)
        c.BlitMask(m, int(x), int(y), C(col))
        return m.shape[1]
    return px.draw(c, name, int(x), int(y), col, size=size)


def icon_size(name, size=16):
    if name in ART:
        m = art(name)
        k = max(1, int(round(size / 16))) if size and size != 16 else 1
        return m.shape[1] * k, m.shape[0] * k
    m = px.mask(name, size)
    return m.shape[1], m.shape[0]


def icon_outlined(c, name, x, y, col, outline=BLACK, size=16):
    w, h = icon_size(name, size)
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        icon(c, name, x + dx, y + dy, outline, size)
    icon(c, name, x, y, col, size)
    return w


# ------------------------------------------------------------------------------------------------ seven-segment
from display.modules.quake import seven_seg  # noqa: E402  (shared digit renderer)


# ------------------------------------------------------------------------------------------------ maps
_MAPS = {}


class Map:
    """A hub-rendered basemap (data/bcmaps/<name>.bin, RGB) with its Web-Mercator projection."""

    def __init__(self, img, meta):
        self.img, self.meta = img, meta
        self.h, self.w = img.shape[:2]
        self.lon0 = float(meta.get("lon0", -180.0))               # longitudes are taken in [lon0, lon0 + 360)

    @staticmethod
    def load(data_dir, name):
        if not data_dir:
            return None
        p = os.path.join(data_dir, "bcmaps", f"{name}.json")
        try:
            mt = os.path.getmtime(p)
        except OSError:
            return None
        hit = _MAPS.get(name)
        if hit and hit[0] == mt:
            return hit[1]
        try:
            meta = json.load(open(p))
            img = np.fromfile(os.path.join(data_dir, "bcmaps", f"{name}.bin"), dtype=np.uint8).reshape(meta["h"], meta["w"], 3)
        except Exception as e:
            print(f"[bc] map {name}: {e}")
            return None
        m = Map(img, meta)
        _MAPS[name] = (mt, m)
        return m

    def project(self, lat, lon):
        lon = float(lon)
        while lon < self.lon0:
            lon += 360.0
        while lon >= self.lon0 + 360.0:
            lon -= 360.0
        x = math.radians(lon) * R
        y = math.log(math.tan(math.pi / 4 + math.radians(max(-85, min(85, float(lat)))) / 2)) * R
        m = self.meta
        return (x - m["merc"][0]) / m["mpp"], (m["merc"][3] - y) / m["mpp"]

    def km_per_px(self):
        return float(self.meta.get("km_per_px") or 1.0)

    def inside(self, lat, lon, pad=0):
        x, y = self.project(lat, lon)
        return -pad <= x < self.w + pad and -pad <= y < self.h + pad

    def blit(self, c, x=0, y=0, sx=0, sy=0, w=None, h=None, k=1.0):
        w = self.w - sx if w is None else w
        h = self.h - sy if h is None else h
        sub = self.img[sy:sy + h, sx:sx + w]
        if k != 1.0:
            sub = (sub.astype(np.uint16) * int(k * 256) >> 8).astype(np.uint8)
        c.Blit(sub, int(x), int(y))

    def polyline(self, c, pts, col, ox=0, oy=0, t=1):
        """pts: [[lon, lat], …] (GeoJSON order)."""
        prev = None
        for lon, lat in pts:
            x, y = self.project(lat, lon)
            x, y = x + ox, y + oy
            if prev is not None:
                (thick_line if t > 1 else line)(c, prev[0], prev[1], x, y, col, *((t,) if t > 1 else ()))
            prev = (x, y)

    def poly_mask(self, rings, w=None, h=None, ox=0, oy=0):
        """Bool mask of polygon rings ([[lon, lat], …]) at map pixels (PIL scanline fill, once per item)."""
        from PIL import Image, ImageDraw
        w, h = w or self.w, h or self.h
        img = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(img)
        for ring in rings:
            pts = [tuple(v + o for v, o in zip(self.project(la, lo), (ox, oy))) for lo, la in ring]
            if len(pts) >= 3:
                d.polygon(pts, fill=255)
            elif pts:
                d.point(pts, fill=255)
        return np.asarray(img) > 0


def edge(mask):
    p = np.pad(mask, 1)
    return mask & ~(p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:])


def tint(c, mask, col, k=0.5, ox=0, oy=0):
    """Blend ``col`` into the canvas where ``mask`` is set (mask placed at ox, oy)."""
    h, w = mask.shape
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(W, ox + w), min(H, oy + h)
    if x1 <= x0 or y1 <= y0:
        return
    sub = c.buf[y0:y1, x0:x1]
    m = mask[y0 - oy:y1 - oy, x0 - ox:x1 - ox]
    sub[m] = (sub[m].astype(np.float32) * (1 - k) + np.array(col, np.float32) * k).astype(np.uint8)


def paint(c, mask, col, ox=0, oy=0):
    c.BlitMask(mask, int(ox), int(oy), C(col))


def haversine(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, a)))


def bearing_word(la1, lo1, la2, lo2):
    """Compass point from 1 to 2 (N, NNE, …)."""
    y = math.sin(math.radians(lo2 - lo1)) * math.cos(math.radians(la2))
    x = (math.cos(math.radians(la1)) * math.sin(math.radians(la2))
         - math.sin(math.radians(la1)) * math.cos(math.radians(la2)) * math.cos(math.radians(lo2 - lo1)))
    b = (math.degrees(math.atan2(y, x)) + 360) % 360
    return ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"][int((b + 11.25) // 22.5) % 16]


# ------------------------------------------------------------------------------------------------ time
def hhmm(ts, sec=False):
    if not ts:
        return ""
    return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S" if sec else "%H:%M")


def day_hhmm(ts):
    if not ts:
        return ""
    d = datetime.fromtimestamp(float(ts))
    today = datetime.now().date()
    if d.date() == today:
        return d.strftime("%H:%M")
    return d.strftime("%a %H:%M")


def ago(ts, now=None):
    if not ts:
        return ""
    s = max(0, (now or time.time()) - float(ts))
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)} min ago"
    if s < 86400:
        h = s / 3600
        return f"{h:.0f} h ago" if h >= 3 else f"{int(h)} h {int(s % 3600 // 60)} min ago"
    return f"{int(s // 86400)} d ago"


def until(ts, now=None):
    if not ts:
        return ""
    s = float(ts) - (now or time.time())
    if s <= 0:
        return "now"
    if s < 3600:
        return f"in {int(s // 60)} min"
    return f"in {s / 3600:.1f} h"


# ------------------------------------------------------------------------------------------------ screens
class StaticLoader:
    """Stands in for display.data_loader.DataLoader in previews (one fixed JSON per name)."""

    def __init__(self, data, data_dir=None):
        self.data, self.data_dir = data or {}, data_dir

    def get_data(self, name):
        return self.data.get(name)

    def get_mtime(self, name):
        return time.time() if name in self.data else None


class BCScreen:
    """Base for the BC alert screens. Subclasses set the class attributes and implement ``live_items``."""

    PANEL = ""            # takeover panel name in the rotation ("naad_alert", …)
    DATA = ""             # data file the hub writes (data/<DATA>.json)
    SETTINGS = ""         # config section (modules.<SETTINGS>)
    THEMES = {}           # id → {"label", "hint", "draw": fn(screen, c, item, now, el)}
    SAMPLES = {}          # id → {"label", "item": {...}}
    DEFAULT_THEME = ""
    FONT_ROLES = ()       # preloaded when the screen is built, not in the first frame of an alert

    def __init__(self, settings=None, loader=None, data_dir=None):
        self.settings = settings or {}
        self.loader = loader
        self.data_dir = data_dir or getattr(loader, "data_dir", None)
        self.queue, self.idx, self.t0 = [], 0, 0.0
        self.theme_id = None
        self._static = {}
        self._tour = 0
        self.state = {}                                  # per-item scratch for themes (animation state)

    # -- data -------------------------------------------------------------------------------------
    def data(self):
        return (self.loader.get_data(self.DATA) if self.loader else None) or {}

    def live_items(self):
        """Items to show now (each a dict with at least "id")."""
        return [i for i in (self.data().get("items") or []) if not i.get("expires") or i["expires"] > time.time()]

    def active(self):
        if self.settings.get("enabled") is False:
            return False
        try:
            return bool(self.live_items())
        except Exception as e:
            print(f"[bc] {self.PANEL}: {e}")
            return False

    def sample(self, key):
        smp = self.SAMPLES.get(key) or next(iter(self.SAMPLES.values()))
        it = json.loads(json.dumps(smp["item"]))
        it.setdefault("id", f"sample-{key}")
        it["sample"] = True
        now = getattr(self, "_clock", None) or time.time()      # the drawing clock (previews replay time)
        for k, v in list(it.items()):                  # sample times are relative ("-300" = five minutes ago)
            if k.endswith("_rel"):
                it[k[:-4]] = now + float(v)
        return it

    # -- themes -----------------------------------------------------------------------------------
    def theme_for(self, item, entry):
        tid = (entry or {}).get("theme") or item.get("theme") or self.settings.get("theme") or self.DEFAULT_THEME
        if tid == "tour":
            ids = list(self.THEMES)
            tid = ids[self._tour % len(ids)]
        return tid if tid in self.THEMES else self.DEFAULT_THEME

    def dwell(self, item):
        base = float(self.settings.get("dwell_sec") or 20)
        words = len(" ".join(str(item.get(k) or "") for k in ("headline", "description", "instruction", "text")).split())
        return max(base, min(90.0, 6.0 + words / 3.2))

    def static(self, c, key, draw):
        """Draw the static layer once per key and paste it on later frames."""
        hit = self._static.get(key)
        if hit is not None:
            c.buf[:] = hit
            return False
        c.buf[:] = 0
        draw()
        if len(self._static) > 12:
            self._static.clear()
        self._static[key] = c.buf.copy()
        return True

    # -- drawing ------------------------------------------------------------------------------------
    def _queue(self, entry):
        if entry.get("sample"):
            return [self.sample(entry["sample"])]
        items = self.live_items()
        if entry.get("item"):
            items = [i for i in items if i.get("id") == entry["item"]] or items
        return items

    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        entry = entry or {}
        self._clock = now
        if transition or not self.queue:
            self.queue, self.idx, self.t0 = self._queue(entry), 0, now
            self.state = {}
        if not self.queue:
            c.Clear()
            text(c, font("m13"), 8, 8, (160, 160, 170), "Nothing to show.")
            return "done"
        item = self.queue[min(self.idx, len(self.queue) - 1)]
        tid = self.theme_for(item, entry)
        el = now - self.t0
        try:
            self.THEMES[tid]["draw"](self, c, item, now, el)
        except Exception:
            import traceback
            traceback.print_exc()
            c.Clear()
            text(c, font("m13"), 8, 8, (255, 80, 80), f"{self.PANEL}: theme {tid} failed")
        if item.get("sample"):
            sample_badge(c, self.THEMES[tid].get("badge"))
        if el >= float(entry.get("dwell_sec") or self.dwell(item)):
            self.idx += 1
            self.t0 = now
            self.state = {}
            if self.idx >= len(self.queue):
                self.idx = 0
                self._tour += 1
                self.queue = self._queue(entry)
                return "done"
        return None


def sample_badge(c, pos=None):
    """Samples are labelled on the wall; each theme says where the badge goes (default bottom-right)."""
    f = font("s8")
    w = tw(f, "SAMPLE") + 6
    x, y = pos if pos else (W - w - 1, H - f.h - 3)
    box(c, x, y, w, f.h + 2, (90, 0, 110))
    text(c, f, x + 3, y + 1, (255, 210, 255), "SAMPLE")


def render_preview(cls, theme, sample=None, at=4.0, settings=None, data=None, data_dir=None, frames=1):
    """A frame of a screen ``at`` seconds into an item (a sample unless ``sample`` is None and real data exist)."""
    from display.gfx import Canvas
    loader = StaticLoader({cls.DATA: data or {}}, data_dir)
    scr = cls(dict(settings or {}, theme=theme), loader, data_dir)
    c = Canvas(W, H)
    entry = {"theme": theme, "dwell_sec": 10 ** 6}
    if sample:
        entry["sample"] = sample
    elif not scr.live_items():
        entry["sample"] = next(iter(cls.SAMPLES))
    now = time.time()
    t0 = now - float(at)
    scr.draw(c, (0, 0, W, H), entry, True, now=t0)
    for k in range(1, frames + 1):                       # a few frames so animated state settles
        scr.draw(c, (0, 0, W, H), entry, False, now=t0 + float(at) * k / frames)
    return c.buf
