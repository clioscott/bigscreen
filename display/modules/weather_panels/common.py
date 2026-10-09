"""Shared helpers for the weather_panels package.

This file contains:
  * Drawing primitives (hline, vline, dotted, dither, frame styles, arrows)
  * Color palettes (12+ distinct visual languages)
  * Text helpers (truncate, centered, right-aligned)
  * Icon loader (cached PIL load + frame extraction)
  * Sample data fixture (Richmond BC, all 6 screen types populated)

Every primitive accepts an ``rgbmatrix.graphics`` Color or a 3-tuple. The
zone size convention is (224, 138) for half panels and (448, 138) for
full panels; designs MUST stay within that.
"""

from __future__ import annotations

import functools
import json as _json
import logging
import math
import os
import threading
from threading import Lock as _Lock

import numpy as np
from typing import Tuple

from display.gfx import graphics

from display.modules.weather_icon_map import gif_for_icon_code

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HALF_W, HALF_H = 224, 138
FULL_W, FULL_H = 448, 138

# How long each screen is displayed before auto-rotating to the next.
# Static screens (current/forecast/hourly/minutely/warning) use this
# baseline. Radar/animation screens use a longer dwell that's scaled
# to play exactly N full loops of the animation at the current FPS.
SCREEN_DWELL_SEC = 5.0
ANIM_LOOPS_PER_SCREEN = 2     # play 2 full loops on each radar screen
_STATIC_SCREENS = [
    # (screen_name, method_name, track, screen_order)
    ("current",   "screen_current",  None, 1),
    ("hourly",    "screen_hourly",   None, 2),
    ("forecast",  "screen_forecast", None, 3),
    ("warning",   "screen_warning",  None, 99),
]

# Legacy fallback labels for the control panel's screen selector.
_STATIC_SCREEN_LABELS = {
    "current":   "Current",
    "forecast":  "Forecast (5-day)",
    "hourly":    "Hourly chart",
    "minutely":  "Minutely chart",
    "warning":   "Warning / advisory",
}


def _build_screen_dispatch() -> tuple[list[str], list[tuple], dict[str, str]]:
    """Build SCREEN_NAMES, SCREEN_DISPATCH, and SCREEN_LABELS dynamically.

    Static screens (current/forecast/hourly/minutely/warning) are always
    present. Radar/map screens are generated from ``animation_tracks`` in
    ``config/basemap_presets.json``: every track with a non-null
    ``screen_order`` becomes a screen that calls ``screen_radar`` with
    ``track=<track_name>``. Stitch-screen entries (``is_stitch_screen``)
    also become screens — the Pi-side stitching logic handles combining
    the frames from the group's past + future sub-tracks.

    Returns (SCREEN_NAMES, SCREEN_DISPATCH, SCREEN_LABELS).
    """
    import json as _j
    _presets_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "config", "basemap_presets.json")

    # Collect all entries: (screen_order, screen_name, method, track, label)
    entries = []
    for sname, method, track, order in _STATIC_SCREENS:
        label = _STATIC_SCREEN_LABELS.get(sname, sname)
        entries.append((order, sname, method, track, label))

    # Add tracks from config
    try:
        with open(_presets_path) as f:
            cfg = _j.load(f)
        tracks = cfg.get("animation_tracks", {})
        for tname, tcfg in tracks.items():
            if tname.startswith("_"):
                continue
            if not isinstance(tcfg, dict):
                continue
            order = tcfg.get("screen_order")
            if order is None:
                continue  # hidden from cycle
            display = tcfg.get("display_name", tname)
            screen_name = f"map_{tname}"
            entries.append((order, screen_name, "screen_radar", tname, display))
    except Exception as exc:
        logger.warning("Failed to load animation_tracks for screen dispatch: %s", exc)

    # Sort by screen_order
    entries.sort(key=lambda e: (e[0], e[1]))

    names = [e[1] for e in entries]
    dispatch = [(e[1], e[2], e[3]) for e in entries]
    labels = {e[1]: f"{i+1}. {e[4]}" for i, e in enumerate(entries)}
    return names, dispatch, labels


SCREEN_NAMES, SCREEN_DISPATCH, SCREEN_LABELS = _build_screen_dispatch()
NUM_SCREENS = len(SCREEN_NAMES)

# Indices into SCREEN_DISPATCH for the *auto-cycle* (free-cycle when no
# cycle-entry pin is active).  Only static text screens are included here.
# Animation tracks (GOES, radar, etc.) are intentionally excluded so they
# can never appear by surprise in the middle of the cycle — they must be
# explicitly configured as cycle entries with a weather_screen pin.
AUTO_CYCLE_IDXS: list[int] = [
    i for i, (_n, _m, track) in enumerate(SCREEN_DISPATCH)
    if track is None
]


def rebuild_screen_dispatch() -> None:
    """Re-read config and rebuild the screen dispatch tables.

    Called when the control panel pushes a config change so the Pi
    picks up new/removed tracks without a full restart.
    """
    global SCREEN_NAMES, SCREEN_DISPATCH, SCREEN_LABELS, NUM_SCREENS, AUTO_CYCLE_IDXS
    SCREEN_NAMES, SCREEN_DISPATCH, SCREEN_LABELS = _build_screen_dispatch()
    NUM_SCREENS = len(SCREEN_NAMES)
    AUTO_CYCLE_IDXS = [
        i for i, (_n, _m, track) in enumerate(SCREEN_DISPATCH)
        if track is None
    ]

# Richmond BC reference coordinates (used by daylight bar solar math
# and radar map projection).
RICHMOND_LAT = 49.1666
RICHMOND_LON = -123.1336

# Alpha threshold for "is this pixel lit?" after BOX averaging when we
# rasterize a GIF frame down to a small icon size.
_ALPHA_THRESHOLD = 96


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=512)
def _C(r: int, g: int, b: int):
    """Construct a graphics.Color clamped to 0..255.

    Cached so repeated calls with the same (r, g, b) — which is the
    overwhelmingly common case in palette-driven rendering — return the
    same Cython object instead of allocating a new one every frame.
    """
    return graphics.Color(max(0, min(255, int(r))),
                          max(0, min(255, int(g))),
                          max(0, min(255, int(b))))


def color_to_rgb(c) -> Tuple[int, int, int]:
    """Extract (r, g, b) from a graphics.Color or 3-tuple."""
    if isinstance(c, tuple):
        return (int(c[0]), int(c[1]), int(c[2]))
    return (int(c.red), int(c.green), int(c.blue))


def lerp_color(a, b, t: float):
    """Linearly interpolate between two colors. ``t`` in [0, 1]."""
    t = max(0.0, min(1.0, float(t)))
    ar, ag, ab = color_to_rgb(a)
    br, bg, bb = color_to_rgb(b)
    return _C(ar + (br - ar) * t,
              ag + (bg - ag) * t,
              ab + (bb - ab) * t)


def shade(c, factor: float):
    """Darken (factor < 1) or brighten (factor > 1) a color."""
    r, g, b = color_to_rgb(c)
    return _C(r * factor, g * factor, b * factor)


# ---------------------------------------------------------------------------
# Color palettes
# ---------------------------------------------------------------------------

PALETTES: dict[str, dict] = {
    # 1. Cool: blue-leaning, default modern
    "cool": {
        "fg":      _C(232, 237, 242),
        "label":   _C(122, 130, 144),
        "accent":  _C( 95, 198, 255),
        "warm":    _C(255, 184,  77),
        "cool":    _C(158, 200, 255),
        "good":    _C(136, 240, 140),
        "bad":     _C(255,  90,  80),
        "muted":   _C( 90,  96, 106),
        "frame":   _C( 70,  90, 110),
        "bg_tile": _C( 22,  28,  36),
    },
    # 2. Warm sunset
    "sunset": {
        "fg":      _C(255, 232, 200),
        "label":   _C(180, 140, 110),
        "accent":  _C(255, 150,  60),
        "warm":    _C(255,  90,  70),
        "cool":    _C(180, 200, 240),
        "good":    _C(220, 230, 110),
        "bad":     _C(255,  70,  70),
        "muted":   _C(120,  80,  60),
        "frame":   _C(200, 110,  60),
        "bg_tile": _C( 36,  20,  10),
    },
    # 3. Mono LCD
    "mono": {
        "fg":      _C(220, 230, 220),
        "label":   _C(120, 130, 120),
        "accent":  _C(180, 220, 180),
        "warm":    _C(220, 230, 220),
        "cool":    _C(180, 200, 180),
        "good":    _C(200, 220, 200),
        "bad":     _C(240, 200, 200),
        "muted":   _C( 70,  80,  70),
        "frame":   _C( 90, 110,  90),
        "bg_tile": _C( 14,  20,  14),
    },
    # 4. Terminal green (CRT)
    "term": {
        "fg":      _C(110, 250, 130),
        "label":   _C( 80, 160,  90),
        "accent":  _C(160, 255, 200),
        "warm":    _C(240, 230, 100),
        "cool":    _C( 90, 200, 240),
        "good":    _C(110, 250, 130),
        "bad":     _C(255,  90,  80),
        "muted":   _C( 30,  90,  40),
        "frame":   _C( 50, 150,  70),
        "bg_tile": _C(  8,  24,  10),
    },
    # 5. Amber phosphor
    "amber": {
        "fg":      _C(255, 178,  77),
        "label":   _C(170, 110,  40),
        "accent":  _C(255, 220, 120),
        "warm":    _C(255, 130,  40),
        "cool":    _C(190, 160,  90),
        "good":    _C(220, 200, 110),
        "bad":     _C(255,  90,  60),
        "muted":   _C( 90,  60,  20),
        "frame":   _C(180, 110,  30),
        "bg_tile": _C( 30,  18,   6),
    },
    # 6. Cyber neon (pink + cyan)
    "cyber": {
        "fg":      _C(240, 240, 240),
        "label":   _C(200, 130, 220),
        "accent":  _C( 60, 230, 240),
        "warm":    _C(255,  80, 200),
        "cool":    _C( 60, 230, 240),
        "good":    _C(140, 240, 160),
        "bad":     _C(255,  80, 130),
        "muted":   _C( 80,  60, 120),
        "frame":   _C(180,  60, 200),
        "bg_tile": _C( 28,  12,  40),
    },
    # 7. Pastel
    "pastel": {
        "fg":      _C(220, 230, 240),
        "label":   _C(150, 160, 200),
        "accent":  _C(180, 210, 255),
        "warm":    _C(255, 195, 175),
        "cool":    _C(170, 215, 255),
        "good":    _C(180, 240, 200),
        "bad":     _C(255, 150, 170),
        "muted":   _C( 90,  90, 130),
        "frame":   _C(120, 130, 180),
        "bg_tile": _C( 24,  28,  44),
    },
    # 8. NES retro
    "nes": {
        "fg":      _C(252, 252, 252),
        "label":   _C(200, 200, 200),
        "accent":  _C( 60, 188, 252),
        "warm":    _C(248, 152,  24),
        "cool":    _C(  0, 124, 240),
        "good":    _C(  0, 168,   0),
        "bad":     _C(232,   0,   0),
        "muted":   _C(116, 116, 116),
        "frame":   _C(252, 252, 252),
        "bg_tile": _C( 24,  24,  24),
    },
    # 9. Frost (icy blues)
    "ice": {
        "fg":      _C(240, 250, 255),
        "label":   _C(150, 180, 210),
        "accent":  _C(150, 210, 255),
        "warm":    _C(255, 200, 150),
        "cool":    _C(180, 230, 255),
        "good":    _C(180, 240, 220),
        "bad":     _C(255, 130, 130),
        "muted":   _C( 80, 110, 140),
        "frame":   _C(120, 170, 210),
        "bg_tile": _C( 14,  22,  34),
    },
    # 10. Tropic (deep teal + lime)
    "tropic": {
        "fg":      _C(244, 252, 240),
        "label":   _C(180, 220, 170),
        "accent":  _C( 80, 220, 180),
        "warm":    _C(255, 200,  90),
        "cool":    _C( 90, 220, 200),
        "good":    _C(140, 240, 160),
        "bad":     _C(255, 100, 110),
        "muted":   _C( 50, 110,  90),
        "frame":   _C( 70, 170, 140),
        "bg_tile": _C( 14,  30,  26),
    },
    # 11. Newsprint (high-contrast paper-like)
    "paper": {
        "fg":      _C(248, 244, 232),
        "label":   _C(120, 110,  95),
        "accent":  _C(220, 100,  60),
        "warm":    _C(220, 130,  70),
        "cool":    _C( 80, 150, 200),
        "good":    _C(140, 180,  90),
        "bad":     _C(220,  60,  60),
        "muted":   _C( 80,  72,  62),
        "frame":   _C(180, 165, 140),
        "bg_tile": _C( 30,  26,  18),
    },
    # 12. Storm (deep blue + lightning yellow)
    "storm": {
        "fg":      _C(232, 240, 255),
        "label":   _C(120, 140, 180),
        "accent":  _C(255, 230,  80),
        "warm":    _C(255, 180,  70),
        "cool":    _C( 90, 130, 220),
        "good":    _C(130, 220, 200),
        "bad":     _C(255,  90, 110),
        "muted":   _C( 60,  72, 110),
        "frame":   _C( 80, 110, 170),
        "bg_tile": _C( 10,  16,  32),
    },
}


# ---------------------------------------------------------------------------
# Pixel primitives
# ---------------------------------------------------------------------------

def hline(canvas, x: int, y: int, w: int, color) -> None:
    if w <= 0:
        return
    graphics.DrawLine(canvas, x, y, x + w - 1, y, color)


def vline(canvas, x: int, y: int, h: int, color) -> None:
    if h <= 0:
        return
    graphics.DrawLine(canvas, x, y, x, y + h - 1, color)


def rect_outline(canvas, x: int, y: int, w: int, h: int, color) -> None:
    if w <= 0 or h <= 0:
        return
    hline(canvas, x, y, w, color)
    hline(canvas, x, y + h - 1, w, color)
    vline(canvas, x, y, h, color)
    vline(canvas, x + w - 1, y, h, color)


def rect_filled(canvas, x: int, y: int, w: int, h: int, color) -> None:
    if w <= 0 or h <= 0:
        return
    for yy in range(y, y + h):
        graphics.DrawLine(canvas, x, yy, x + w - 1, yy, color)


def hline_dotted(canvas, x: int, y: int, w: int, color, on: int = 1, off: int = 1) -> None:
    period = on + off
    for i in range(w):
        if (i % period) < on:
            canvas.SetPixel(x + i, y, color.red, color.green, color.blue)


def vline_dotted(canvas, x: int, y: int, h: int, color, on: int = 1, off: int = 1) -> None:
    period = on + off
    for i in range(h):
        if (i % period) < on:
            canvas.SetPixel(x, y + i, color.red, color.green, color.blue)


def rect_dotted(canvas, x: int, y: int, w: int, h: int, color, on: int = 1, off: int = 1) -> None:
    hline_dotted(canvas, x, y, w, color, on, off)
    hline_dotted(canvas, x, y + h - 1, w, color, on, off)
    vline_dotted(canvas, x, y, h, color, on, off)
    vline_dotted(canvas, x + w - 1, y, h, color, on, off)


def corner_brackets(canvas, x: int, y: int, w: int, h: int, color, leg: int = 4) -> None:
    if w < leg * 2 + 1 or h < leg * 2 + 1:
        rect_outline(canvas, x, y, w, h, color)
        return
    hline(canvas, x, y, leg, color)
    vline(canvas, x, y, leg, color)
    hline(canvas, x + w - leg, y, leg, color)
    vline(canvas, x + w - 1, y, leg, color)
    hline(canvas, x, y + h - 1, leg, color)
    vline(canvas, x, y + h - leg, leg, color)
    hline(canvas, x + w - leg, y + h - 1, leg, color)
    vline(canvas, x + w - 1, y + h - leg, leg, color)


def chunky_window(canvas, x: int, y: int, w: int, h: int, color, title_h: int = 0) -> None:
    """Two-pixel-thick double border with optional title divider."""
    rect_outline(canvas, x, y, w, h, color)
    if w >= 6 and h >= 6:
        rect_outline(canvas, x + 2, y + 2, w - 4, h - 4, color)
    if title_h > 0 and y + title_h < y + h - 2:
        hline(canvas, x + 2, y + title_h, w - 4, color)


def double_border(canvas, x: int, y: int, w: int, h: int, color) -> None:
    rect_outline(canvas, x, y, w, h, color)
    if w >= 4 and h >= 4:
        rect_outline(canvas, x + 2, y + 2, w - 4, h - 4, color)


def scanline_header(canvas, x: int, y: int, w: int, color) -> None:
    hline(canvas, x, y, w, color)
    hline(canvas, x, y + 2, w, color)


def progress_bar(canvas, x: int, y: int, w: int, h: int, pct: float, fg, bg) -> None:
    if w <= 0 or h <= 0:
        return
    pct = max(0.0, min(100.0, float(pct)))
    fill_w = int(round(w * pct / 100.0))
    rect_outline(canvas, x, y, w, h, bg)
    if fill_w > 2 and h > 2:
        rect_filled(canvas, x + 1, y + 1, fill_w - 2, h - 2, fg)


def vertical_bar(canvas, x: int, y: int, w: int, h: int, pct: float, fg, bg) -> None:
    if w <= 0 or h <= 0:
        return
    pct = max(0.0, min(100.0, float(pct)))
    fill_h = int(round(h * pct / 100.0))
    rect_outline(canvas, x, y, w, h, bg)
    if fill_h > 1 and w > 2:
        rect_filled(canvas, x + 1, y + h - fill_h, w - 2, fill_h, fg)


def dither_fill(canvas, x: int, y: int, w: int, h: int, color, density: int = 2) -> None:
    period = max(2, density)
    for yy in range(h):
        offset = yy % 2
        for xx in range(w):
            if ((xx + offset) % period) == 0:
                canvas.SetPixel(x + xx, y + yy, color.red, color.green, color.blue)


def checker_fill(canvas, x: int, y: int, w: int, h: int, color, square: int = 2) -> None:
    """Checkerboard fill with squares of the given size."""
    for yy in range(h):
        for xx in range(w):
            if ((xx // square) + (yy // square)) % 2 == 0:
                canvas.SetPixel(x + xx, y + yy, color.red, color.green, color.blue)


def arrow_dir(canvas, cx: int, cy: int, deg: float, color, length: int = 5) -> None:
    """Draw a directional arrow at (cx, cy) pointing TO ``deg`` (compass degrees)."""
    rad = math.radians(deg)
    dx = math.sin(rad)
    dy = -math.cos(rad)
    tx = int(round(cx - dx * length))
    ty = int(round(cy - dy * length))
    graphics.DrawLine(canvas, tx, ty, cx, cy, color)
    for spread in (-30, 30):
        rad2 = math.radians(deg - 180 + spread)
        bx = int(round(cx + math.sin(rad2) * 2.5))
        by = int(round(cy - math.cos(rad2) * 2.5))
        graphics.DrawLine(canvas, cx, cy, bx, by, color)


def pixel_circle(canvas, cx: int, cy: int, r: int, color) -> None:
    """Bresenham-style hollow circle."""
    x, y = r, 0
    err = 0
    while x >= y:
        for px, py in (
            (cx + x, cy + y), (cx + y, cy + x), (cx - y, cy + x), (cx - x, cy + y),
            (cx - x, cy - y), (cx - y, cy - x), (cx + y, cy - x), (cx + x, cy - y),
        ):
            canvas.SetPixel(px, py, color.red, color.green, color.blue)
        y += 1
        if err <= 0:
            err += 2 * y + 1
        if err > 0:
            x -= 1
            err -= 2 * x + 1


def sparkline(canvas, x: int, y: int, w: int, h: int,
              values: list, vmin, vmax, color) -> None:
    """Plot ``values`` as a connected line. ``vmin/vmax`` set the y-axis."""
    n = len(values)
    if n < 2 or w < 2 or h < 2:
        return
    span = max(vmax - vmin, 0.001)
    pts = []
    for i, v in enumerate(values):
        px = x + int(round(i * (w - 1) / (n - 1)))
        py = y + h - 1 - int(round((v - vmin) * (h - 1) / span))
        pts.append((px, py))
    for i in range(n - 1):
        graphics.DrawLine(canvas, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], color)


def histogram(canvas, x: int, y: int, w: int, h: int,
              values: list, vmin, vmax, color, bg=None) -> None:
    """Plot ``values`` as vertical bars filling the bottom of (x,y,w,h)."""
    n = len(values)
    if n < 1 or w < n or h < 2:
        return
    span = max(vmax - vmin, 0.001)
    bar_w = max(1, w // n)
    for i, v in enumerate(values):
        bx = x + i * bar_w
        bh = max(0, int(round((v - vmin) * (h - 1) / span)))
        if bg is not None:
            for k in range(bar_w - 1 if bar_w > 1 else 1):
                vline(canvas, bx + k, y, h, bg)
        for k in range(bar_w - 1 if bar_w > 1 else 1):
            if bh > 0:
                vline(canvas, bx + k, y + h - bh, bh, color)


def chart_history_forecast(canvas, x: int, y: int, w: int, h: int,
                           *,
                           history: list, forecast: list,
                           vmin: float, vmax: float,
                           hist_color, fcst_color,
                           now_color=None,
                           fill_below: bool = False,
                           hatch_forecast: bool = True) -> None:
    """Continuous chart spanning ``-len(history)`` ... NOW ... ``+len(forecast)``.

    ``history`` is observation values (left of NOW). ``forecast`` is
    forecast values (right of NOW). They share a single y-axis (vmin/vmax).
    The vertical NOW line is drawn at the boundary in red unless ``now_color``
    is supplied.

    History is rendered as a SOLID line. Forecast can be rendered as
    a HATCHED line (alternating pixels) when ``hatch_forecast=True`` so
    the reader can tell them apart at a glance.

    If ``fill_below=True``, area below each line is filled in a dim
    version of its color (rain accumulation style).
    """
    if w <= 2 or h <= 2:
        return
    nh = len(history)
    nf = len(forecast)
    n = nh + nf
    if n < 2:
        return
    span = max(vmax - vmin, 0.001)

    def to_xy(i: int, v: float) -> tuple[int, int]:
        px = x + int(round(i * (w - 1) / (n - 1)))
        py = y + h - 1 - int(round((v - vmin) * (h - 1) / span))
        return px, py

    # History line (solid)
    for i in range(nh - 1):
        x1, y1 = to_xy(i, history[i])
        x2, y2 = to_xy(i + 1, history[i + 1])
        graphics.DrawLine(canvas, x1, y1, x2, y2, hist_color)
        if fill_below:
            for px in range(x1, x2 + 1):
                t = 0 if x2 == x1 else (px - x1) / (x2 - x1)
                py = int(round(y1 + t * (y2 - y1)))
                col_dim = _C(hist_color.red // 3, hist_color.green // 3, hist_color.blue // 3)
                vline(canvas, px, py + 1, y + h - py - 1, col_dim)

    # Bridge from last history point to first forecast point
    if nh > 0 and nf > 0:
        x1, y1 = to_xy(nh - 1, history[-1])
        x2, y2 = to_xy(nh, forecast[0])
        graphics.DrawLine(canvas, x1, y1, x2, y2, fcst_color)

    # Forecast line: optionally hatched
    for i in range(nh, n - 1):
        v0 = forecast[i - nh]
        v1 = forecast[i - nh + 1]
        x1, y1 = to_xy(i, v0)
        x2, y2 = to_xy(i + 1, v1)
        if hatch_forecast:
            steps = max(abs(x2 - x1), abs(y2 - y1))
            if steps == 0:
                canvas.SetPixel(x1, y1, fcst_color.red, fcst_color.green, fcst_color.blue)
            else:
                for k in range(steps + 1):
                    if k & 1:    # every other pixel only
                        continue
                    t = k / steps
                    px = int(round(x1 + t * (x2 - x1)))
                    py = int(round(y1 + t * (y2 - y1)))
                    canvas.SetPixel(px, py, fcst_color.red, fcst_color.green, fcst_color.blue)
        else:
            graphics.DrawLine(canvas, x1, y1, x2, y2, fcst_color)
        if fill_below:
            for px in range(x1, x2 + 1):
                t = 0 if x2 == x1 else (px - x1) / (x2 - x1)
                py = int(round(y1 + t * (y2 - y1)))
                col_dim = _C(fcst_color.red // 4, fcst_color.green // 4, fcst_color.blue // 4)
                # hatched area fill: skip every other column
                if (px - x1) & 1:
                    continue
                vline(canvas, px, py + 1, y + h - py - 1, col_dim)

    # NOW vertical line (red by default) at the history/forecast boundary
    nc = now_color if now_color is not None else _C(255, 40, 40)
    if nh > 0:
        nx = x + int(round((nh - 1) * (w - 1) / (n - 1)))
        for py in range(y, y + h):
            canvas.SetPixel(nx, py, nc.red, nc.green, nc.blue)


def chart_history_forecast_bars(canvas, x: int, y: int, w: int, h: int,
                                *,
                                history: list, forecast: list,
                                vmin: float, vmax: float,
                                hist_color, fcst_color,
                                now_color=None) -> None:
    """Like ``chart_history_forecast`` but draws each value as a vertical
    bar (good for precip rate). History bars are solid; forecast bars are
    1-px-spaced (hatched) so they're visually distinct."""
    if w <= 2 or h <= 2:
        return
    nh = len(history)
    nf = len(forecast)
    n = nh + nf
    if n < 1:
        return
    span = max(vmax - vmin, 0.001)
    bar_w = w / float(n)

    for i in range(n):
        v = history[i] if i < nh else forecast[i - nh]
        if v <= vmin:
            continue
        bh = int(round((v - vmin) / span * (h - 1)))
        if bh <= 0:
            continue
        bx = x + int(round(i * bar_w))
        bw = max(1, int(round(bar_w)))
        if i < nh:
            # Solid history bar
            rect_filled(canvas, bx, y + h - bh, max(1, bw - 1), bh, hist_color)
        else:
            # Hatched forecast bar (alternating columns)
            for k in range(0, max(1, bw - 1), 2):
                vline(canvas, bx + k, y + h - bh, bh, fcst_color)

    # NOW line in red
    nc = now_color if now_color is not None else _C(255, 40, 40)
    if nh > 0:
        nx = x + int(round(nh * bar_w))
        for py in range(y, y + h):
            canvas.SetPixel(nx, py, nc.red, nc.green, nc.blue)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _char_width(font, ch: str) -> int:
    try:
        w = font.CharacterWidth(ord(ch))
        if w > 0:
            return w
    except Exception:
        pass
    return 4


def text_width(font, s: str) -> int:
    return sum(_char_width(font, c) for c in s)


def draw_text(canvas, font, x: int, y: int, color, s: str) -> int:
    graphics.DrawText(canvas, font, x, y, color, s)
    return text_width(font, s)


def draw_text_centered(canvas, font, cx: int, y: int, color, s: str, in_w: int | None = None) -> None:
    w = text_width(font, s)
    if in_w is not None and w > in_w:
        while s and text_width(font, s + ".") > in_w:
            s = s[:-1]
        s = s + "." if s else ""
        w = text_width(font, s)
    graphics.DrawText(canvas, font, cx - w // 2, y, color, s)


def draw_text_right(canvas, font, x_right: int, y: int, color, s: str) -> None:
    w = text_width(font, s)
    graphics.DrawText(canvas, font, x_right - w, y, color, s)


def truncate_to_width(font, s: str, max_w: int) -> str:
    if text_width(font, s) <= max_w:
        return s
    while s and text_width(font, s + ".") > max_w:
        s = s[:-1]
    return s + "." if s else ""


def wrap_to_width(font, s: str, max_w: int, max_lines: int = 4) -> list[str]:
    """Greedy word-wrap. Returns up to ``max_lines`` lines."""
    if not s:
        return []
    words = s.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip()
        if text_width(font, candidate) <= max_w:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
                if len(lines) >= max_lines:
                    return lines
            # Word longer than max_w -- truncate hard
            if text_width(font, w) > max_w:
                cur = truncate_to_width(font, w, max_w)
            else:
                cur = w
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Icon loader
# ---------------------------------------------------------------------------

_ICON_CACHE: dict[tuple[str, int], dict | None] = {}
from display.gfx import ASSETS_DIR as _ASSETS_DIR
_ICON_DIR = os.path.join(_ASSETS_DIR, "icons", "weather", "animatednew")
_ICON_LOCK = threading.Lock()


def _load_icon_anim(basename: str, size: int) -> dict | None:
    key = (basename, size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    with _ICON_LOCK:
        if key in _ICON_CACHE:
            return _ICON_CACHE[key]
        try:
            from PIL import Image
        except ImportError:
            _ICON_CACHE[key] = None
            return None

        path = os.path.join(_ICON_DIR, f"{basename}.gif")
        if not os.path.exists(path):
            _ICON_CACHE[key] = None
            return None

        try:
            im = Image.open(path)
            n = getattr(im, "n_frames", 1)
            frames: list[dict] = []
            cum_ms = 0
            try:
                from PIL.Image import Resampling
                resampler = Resampling.BOX
            except ImportError:
                resampler = Image.BOX  # type: ignore[attr-defined]
            for i in range(n):
                im.seek(i)
                fr = im.convert("RGBA")
                if fr.size != (size, size):
                    fr = fr.resize((size, size), resampler)
                a = np.asarray(fr)
                pixels = (np.ascontiguousarray(a[..., :3]), a[..., 3] >= _ALPHA_THRESHOLD)   # (rgb, mask)
                dur = int(im.info.get("duration") or 100)
                cum_ms += dur
                frames.append({"pixels": pixels, "end_ms": cum_ms})
            entry = {"frames": frames, "total_ms": cum_ms or 1}
        except Exception:
            _ICON_CACHE[key] = None
            return None
        _ICON_CACHE[key] = entry
        return entry


def _icon_frame_pixels(anim: dict, now: float) -> list:
    elapsed_ms = int(now * 1000) % anim["total_ms"]
    for f in anim["frames"]:
        if elapsed_ms < f["end_ms"]:
            return f["pixels"]
    return anim["frames"][-1]["pixels"]


def draw_icon(canvas, code: int, x: int, y: int, size: int, now: float, is_night: bool = False) -> bool:
    name = gif_for_icon_code(code, is_night=is_night)
    anim = _load_icon_anim(name, size)
    if not anim:
        return False
    rgb, mask = _icon_frame_pixels(anim, now)
    canvas.Blit(rgb, x, y, mask)
    return True


def draw_icon_basename(canvas, basename: str, x: int, y: int, size: int, now: float) -> bool:
    anim = _load_icon_anim(basename, size)
    if not anim:
        return False
    rgb, mask = _icon_frame_pixels(anim, now)
    canvas.Blit(rgb, x, y, mask)
    return True


# ---------------------------------------------------------------------------
# Number formatting helpers
# ---------------------------------------------------------------------------

def fmt_temp(t, unit: str = "°") -> str:
    if t is None:
        return "--" + unit
    return f"{int(round(t))}{unit}"


def fmt_temp_decimal(t) -> str:
    if t is None:
        return "--.-°"
    return f"{t:.1f}°"


def fmt_temp_precise(t, unit: str = "°") -> str:
    """Format temperature with 1 decimal place, or 2 if the data has meaningful
    hundredths precision.  Rounds to 2 places first to suppress floating-point
    noise (e.g. 14.300000001 → '14.3°'), then strips the trailing zero when the
    hundredths digit is zero.

    Examples:
        14.0  → '14.0°'
        14.3  → '14.3°'
        14.27 → '14.27°'
        -5.1  → '-5.1°'
    """
    if t is None:
        return "--" + unit
    s = f"{round(t, 2):.2f}"  # e.g. "14.30", "-5.10", "14.27"
    if s.endswith("0"):
        s = s[:-1]              # strip trailing zero → "14.3", "-5.1"
    return s + unit


def temp_to_color(t, palette: dict):
    if t is None:
        return palette["label"]
    if t >= 22:
        return palette["warm"]
    if t <= 2:
        return palette["cool"]
    return palette["fg"]


def pop_to_color(p, palette: dict):
    if p is None:
        return palette["label"]
    if p >= 60:
        return palette["accent"]
    if p >= 30:
        return palette["fg"]
    return palette["muted"]


# ---------------------------------------------------------------------------
# Sample data fixture
# ---------------------------------------------------------------------------
# Plausible Richmond, BC weather data covering all 6 screen types. A real
# data feed (DataLoader pickle from the server) will swap this out at
# render time later; for the preview module we want fields populated so
# every screen has something to show.

SAMPLE: dict = {
    "current": {
        "location":        "Richmond",
        "station":         "CYVR",
        "icon_code":       4,
        "is_night":        False,
        "weather_text":    "Mostly cloudy",
        # Longer 2-3 sentence forecast we can render on space-rich designs
        "weather_long":    "Mostly cloudy with a 60% chance of showers this afternoon. Wind west 25 km/h gusting to 40. High 14, falling to 10 by evening.",
        "temp_c":          12.4,
        "feels_c":         10.8,
        "wind_chill_c":    None,
        "humidex_c":       None,
        "rh_pct":          78,
        "dew_point_c":     8.5,
        "wind_kmh":        18,
        "wind_gust_kmh":   32,
        "wind_dir_deg":    270,
        "wind_dir_text":   "W",
        "pressure_kpa":    101.3,
        "pressure_trend":  "rising",     # rising | falling | steady
        "pressure_change": 0.04,         # hPa or kPa over last 3h
        "visibility_km":   24,
        "uv_index":        3,
        "uv_category":     "Moderate",
        "aqhi":            2,
        "aqhi_category":   "Low Risk",
        "cloud_cover_pct": 65,
        "obs_local":       "10:34",
        "obs_utc":         "17:34Z",
        # Today's astronomical times -- used by daylight bar
        "sunrise":         "06:14",
        "sunset":          "20:32",
        # Day's expected normal high/low from regionalNormals
        "normal_high":     14,
        "normal_low":       7,
    },
    "hourly": [
        # 24 entries, hour-aligned, starting at the current observation hour
        {"hour": "11A", "ts_h": 11, "temp_c": 12, "icon_code":  4, "pop_pct": 20, "wind_kmh": 18, "wind_dir": "W",  "lop": "Slight"},
        {"hour": "12P", "ts_h": 12, "temp_c": 13, "icon_code":  4, "pop_pct": 20, "wind_kmh": 18, "wind_dir": "W",  "lop": "Slight"},
        {"hour":  "1P", "ts_h": 13, "temp_c": 13, "icon_code": 30, "pop_pct": 40, "wind_kmh": 22, "wind_dir": "W",  "lop": "Moderate"},
        {"hour":  "2P", "ts_h": 14, "temp_c": 14, "icon_code": 31, "pop_pct": 60, "wind_kmh": 24, "wind_dir": "W",  "lop": "Moderate"},
        {"hour":  "3P", "ts_h": 15, "temp_c": 14, "icon_code":  9, "pop_pct": 70, "wind_kmh": 26, "wind_dir": "W",  "lop": "High"},
        {"hour":  "4P", "ts_h": 16, "temp_c": 13, "icon_code":  9, "pop_pct": 70, "wind_kmh": 26, "wind_dir": "SW", "lop": "High"},
        {"hour":  "5P", "ts_h": 17, "temp_c": 12, "icon_code": 30, "pop_pct": 40, "wind_kmh": 22, "wind_dir": "SW", "lop": "Moderate"},
        {"hour":  "6P", "ts_h": 18, "temp_c": 11, "icon_code":  4, "pop_pct": 20, "wind_kmh": 18, "wind_dir": "SW", "lop": "Slight"},
        {"hour":  "7P", "ts_h": 19, "temp_c": 10, "icon_code":  4, "pop_pct": 10, "wind_kmh": 14, "wind_dir": "S",  "lop": "Nil"},
        {"hour":  "8P", "ts_h": 20, "temp_c":  9, "icon_code":  2, "pop_pct": 10, "wind_kmh": 12, "wind_dir": "S",  "lop": "Nil"},
        {"hour":  "9P", "ts_h": 21, "temp_c":  9, "icon_code": 32, "pop_pct": 10, "wind_kmh": 10, "wind_dir": "S",  "lop": "Nil"},
        {"hour": "10P", "ts_h": 22, "temp_c":  8, "icon_code": 32, "pop_pct": 10, "wind_kmh":  8, "wind_dir": "SE", "lop": "Nil"},
        {"hour": "11P", "ts_h": 23, "temp_c":  7, "icon_code": 33, "pop_pct": 20, "wind_kmh":  8, "wind_dir": "SE", "lop": "Slight"},
        {"hour": "12A", "ts_h":  0, "temp_c":  7, "icon_code": 33, "pop_pct": 30, "wind_kmh": 10, "wind_dir": "SE", "lop": "Slight"},
        {"hour":  "1A", "ts_h":  1, "temp_c":  7, "icon_code": 36, "pop_pct": 40, "wind_kmh": 12, "wind_dir": "SE", "lop": "Moderate"},
        {"hour":  "2A", "ts_h":  2, "temp_c":  7, "icon_code": 36, "pop_pct": 50, "wind_kmh": 14, "wind_dir": "E",  "lop": "Moderate"},
        {"hour":  "3A", "ts_h":  3, "temp_c":  6, "icon_code": 36, "pop_pct": 50, "wind_kmh": 14, "wind_dir": "E",  "lop": "Moderate"},
        {"hour":  "4A", "ts_h":  4, "temp_c":  6, "icon_code": 33, "pop_pct": 40, "wind_kmh": 12, "wind_dir": "E",  "lop": "Moderate"},
        {"hour":  "5A", "ts_h":  5, "temp_c":  6, "icon_code": 33, "pop_pct": 30, "wind_kmh": 10, "wind_dir": "NE", "lop": "Slight"},
        {"hour":  "6A", "ts_h":  6, "temp_c":  6, "icon_code":  3, "pop_pct": 20, "wind_kmh":  8, "wind_dir": "NE", "lop": "Slight"},
        {"hour":  "7A", "ts_h":  7, "temp_c":  7, "icon_code":  2, "pop_pct": 10, "wind_kmh":  8, "wind_dir": "N",  "lop": "Nil"},
        {"hour":  "8A", "ts_h":  8, "temp_c":  9, "icon_code":  2, "pop_pct": 10, "wind_kmh": 10, "wind_dir": "N",  "lop": "Nil"},
        {"hour":  "9A", "ts_h":  9, "temp_c": 11, "icon_code":  1, "pop_pct": 10, "wind_kmh": 12, "wind_dir": "NW", "lop": "Nil"},
        {"hour": "10A", "ts_h": 10, "temp_c": 13, "icon_code":  0, "pop_pct": 10, "wind_kmh": 14, "wind_dir": "NW", "lop": "Nil"},
    ],
    "daily": [
        {"name": "Today", "short": "Mon", "dow_idx": 1, "hi": 14, "lo":  8, "icon_code":  4, "text": "Mostly cloudy", "pop_pct": 20, "wind_kmh": 18, "wind_dir": "W",  "uv": 3, "snow_cm": 0, "rain_mm": 0,
         "long": "Mostly cloudy with a 60 percent chance of showers this afternoon. Wind west 25 km/h gusting to 40. High 14."},
        {"name": "Tue",   "short": "Tue", "dow_idx": 2, "hi": 16, "lo":  9, "icon_code": 30, "text": "Light rain",    "pop_pct": 70, "wind_kmh": 22, "wind_dir": "SW", "uv": 2, "snow_cm": 0, "rain_mm": 4,
         "long": "Periods of rain. Amount 5 to 10 mm. Wind southwest 30 km/h. High 16."},
        {"name": "Wed",   "short": "Wed", "dow_idx": 3, "hi": 18, "lo": 11, "icon_code":  0, "text": "Sunny",         "pop_pct": 10, "wind_kmh": 12, "wind_dir": "W",  "uv": 6, "snow_cm": 0, "rain_mm": 0,
         "long": "Sunny. Wind west 15 km/h. High 18. UV index 6 high."},
        {"name": "Thu",   "short": "Thu", "dow_idx": 4, "hi": 17, "lo": 10, "icon_code":  2, "text": "Few clouds",    "pop_pct": 10, "wind_kmh": 10, "wind_dir": "NW", "uv": 5, "snow_cm": 0, "rain_mm": 0,
         "long": "A mix of sun and cloud. Wind northwest 10 km/h. High 17."},
        {"name": "Fri",   "short": "Fri", "dow_idx": 5, "hi": 15, "lo":  9, "icon_code":  4, "text": "Partly cloudy", "pop_pct": 30, "wind_kmh": 16, "wind_dir": "W",  "uv": 4, "snow_cm": 0, "rain_mm": 1,
         "long": "Partly cloudy with a 30 percent chance of showers. Wind west 15 km/h. High 15."},
        {"name": "Sat",   "short": "Sat", "dow_idx": 6, "hi": 13, "lo":  8, "icon_code":  9, "text": "Showers",       "pop_pct": 80, "wind_kmh": 28, "wind_dir": "S",  "uv": 2, "snow_cm": 0, "rain_mm": 8,
         "long": "Periods of rain at times heavy. Amount 10 to 15 mm. Wind south 30 km/h. High 13."},
        {"name": "Sun",   "short": "Sun", "dow_idx": 7, "hi": 14, "lo":  7, "icon_code":  4, "text": "Mostly cloudy", "pop_pct": 30, "wind_kmh": 18, "wind_dir": "SW", "uv": 3, "snow_cm": 0, "rain_mm": 1,
         "long": "Mostly cloudy with a 30 percent chance of showers. Wind southwest 20 km/h. High 14."},
    ],
    # Per-minute precipitation for the next 60 min, in mm/h. Synthetic
    # but realistic: a small front moves in around minute 12, peaks
    # around minute 28, tails off by minute 50.
    "minutely": [
        # mm/h forecast for next 60 minutes
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.1, 0.2, 0.3,
        0.5, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8, 3.2, 3.8, 4.4,
        4.8, 5.0, 5.2, 5.4, 5.5, 5.4, 5.2, 4.8, 4.4, 3.8,
        3.2, 2.8, 2.4, 2.0, 1.6, 1.4, 1.2, 1.0, 0.8, 0.6,
        0.5, 0.4, 0.3, 0.2, 0.2, 0.1, 0.1, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    ],
    # Per-minute precipitation for the LAST 60 min, in mm/h. SWOB-realtime
    # provides observation-grade per-minute precip rate at CYVR. This
    # sample reflects a light rain event passing through ~30 min ago
    # that has already cleared.
    # Index 0 = 60 min ago, index 59 = 1 min ago.
    "minutely_history": [
        0.0, 0.0, 0.0, 0.1, 0.2, 0.4, 0.7, 1.1, 1.6, 2.2,
        2.6, 2.9, 3.0, 2.8, 2.4, 2.0, 1.6, 1.3, 1.0, 0.8,
        0.6, 0.5, 0.4, 0.3, 0.3, 0.2, 0.2, 0.1, 0.1, 0.1,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    ],
    # Hourly OBSERVATIONS for the past 24 hours (CYVR climate-hourly /
    # SWOB roll-ups). Used by the hourly chart screens to show the
    # left half (history) of the chart. The future side comes from
    # citypage hourlyForecastGroup (the 'hourly' list above).
    # ts_h is hour-of-day local time (0-23). Order: oldest first.
    "hourly_history": [
        {"hour": "11A", "ts_h": 11, "temp_c": 11, "rh_pct": 82, "wind_kmh": 14, "wind_dir": "W",  "precip_mm": 0.0, "pressure_kpa": 101.5},
        {"hour": "12P", "ts_h": 12, "temp_c": 12, "rh_pct": 78, "wind_kmh": 16, "wind_dir": "W",  "precip_mm": 0.0, "pressure_kpa": 101.4},
        {"hour":  "1P", "ts_h": 13, "temp_c": 13, "rh_pct": 74, "wind_kmh": 18, "wind_dir": "W",  "precip_mm": 0.2, "pressure_kpa": 101.3},
        {"hour":  "2P", "ts_h": 14, "temp_c": 13, "rh_pct": 76, "wind_kmh": 20, "wind_dir": "W",  "precip_mm": 0.6, "pressure_kpa": 101.2},
        {"hour":  "3P", "ts_h": 15, "temp_c": 14, "rh_pct": 72, "wind_kmh": 22, "wind_dir": "W",  "precip_mm": 0.4, "pressure_kpa": 101.1},
        {"hour":  "4P", "ts_h": 16, "temp_c": 14, "rh_pct": 70, "wind_kmh": 24, "wind_dir": "SW", "precip_mm": 0.1, "pressure_kpa": 101.0},
        {"hour":  "5P", "ts_h": 17, "temp_c": 13, "rh_pct": 73, "wind_kmh": 22, "wind_dir": "SW", "precip_mm": 0.0, "pressure_kpa": 100.9},
        {"hour":  "6P", "ts_h": 18, "temp_c": 12, "rh_pct": 76, "wind_kmh": 18, "wind_dir": "SW", "precip_mm": 0.0, "pressure_kpa": 100.9},
        {"hour":  "7P", "ts_h": 19, "temp_c": 11, "rh_pct": 80, "wind_kmh": 14, "wind_dir": "S",  "precip_mm": 0.0, "pressure_kpa": 100.9},
        {"hour":  "8P", "ts_h": 20, "temp_c": 10, "rh_pct": 84, "wind_kmh": 12, "wind_dir": "S",  "precip_mm": 0.0, "pressure_kpa": 101.0},
        {"hour":  "9P", "ts_h": 21, "temp_c": 10, "rh_pct": 86, "wind_kmh": 10, "wind_dir": "S",  "precip_mm": 0.0, "pressure_kpa": 101.0},
        {"hour": "10P", "ts_h": 22, "temp_c":  9, "rh_pct": 88, "wind_kmh":  8, "wind_dir": "SE", "precip_mm": 0.0, "pressure_kpa": 101.1},
        {"hour": "11P", "ts_h": 23, "temp_c":  9, "rh_pct": 90, "wind_kmh":  8, "wind_dir": "SE", "precip_mm": 0.0, "pressure_kpa": 101.1},
        {"hour": "12A", "ts_h":  0, "temp_c":  8, "rh_pct": 92, "wind_kmh":  6, "wind_dir": "SE", "precip_mm": 0.2, "pressure_kpa": 101.2},
        {"hour":  "1A", "ts_h":  1, "temp_c":  8, "rh_pct": 94, "wind_kmh":  6, "wind_dir": "E",  "precip_mm": 0.5, "pressure_kpa": 101.2},
        {"hour":  "2A", "ts_h":  2, "temp_c":  8, "rh_pct": 95, "wind_kmh":  6, "wind_dir": "E",  "precip_mm": 1.2, "pressure_kpa": 101.2},
        {"hour":  "3A", "ts_h":  3, "temp_c":  8, "rh_pct": 96, "wind_kmh":  6, "wind_dir": "E",  "precip_mm": 1.8, "pressure_kpa": 101.2},
        {"hour":  "4A", "ts_h":  4, "temp_c":  8, "rh_pct": 95, "wind_kmh":  8, "wind_dir": "E",  "precip_mm": 1.4, "pressure_kpa": 101.3},
        {"hour":  "5A", "ts_h":  5, "temp_c":  8, "rh_pct": 92, "wind_kmh": 10, "wind_dir": "NE", "precip_mm": 0.6, "pressure_kpa": 101.3},
        {"hour":  "6A", "ts_h":  6, "temp_c":  8, "rh_pct": 88, "wind_kmh": 10, "wind_dir": "NE", "precip_mm": 0.2, "pressure_kpa": 101.4},
        {"hour":  "7A", "ts_h":  7, "temp_c":  9, "rh_pct": 84, "wind_kmh": 10, "wind_dir": "N",  "precip_mm": 0.0, "pressure_kpa": 101.4},
        {"hour":  "8A", "ts_h":  8, "temp_c": 10, "rh_pct": 78, "wind_kmh": 12, "wind_dir": "N",  "precip_mm": 0.0, "pressure_kpa": 101.4},
        {"hour":  "9A", "ts_h":  9, "temp_c": 11, "rh_pct": 72, "wind_kmh": 14, "wind_dir": "NW", "precip_mm": 0.0, "pressure_kpa": 101.4},
        {"hour": "10A", "ts_h": 10, "temp_c": 12, "rh_pct": 66, "wind_kmh": 14, "wind_dir": "NW", "precip_mm": 0.0, "pressure_kpa": 101.4},
    ],
    "alerts": [
        {
            "alert_code":     "wind.statement",
            "alert_name_en":  "Wind Warning",
            "alert_short_en": "Wind",
            "alert_type":     "warning",
            "status_en":      "in effect",
            "risk_colour_en": "#FF8C00",   # EC's orange for warnings
            "issued":         "Mon 09:25",
            "expires":        "Tue 12:00",
            "feature_name":   "Metro Vancouver - central, including the City of Vancouver",
            "impact_en":      "Damaging winds may toss loose objects or cause tree branches to break.",
            "alert_text_en":  ("Strong southwesterly winds are expected to develop this afternoon "
                               "and persist into the overnight period. Peak gusts of 80 to 100 km/h "
                               "are likely along exposed coastal sections and over higher terrain. "
                               "Travel may be difficult; loose objects should be secured. The wind "
                               "will ease Tuesday morning as the frontal system moves inland."),
            "confidence_en":  "High",
        },
    ],
    # Cached radar metadata (rendered from EC PNG by radar.py at runtime;
    # for the preview we include synthetic precip blob coordinates so
    # designs don't have to wait for a network fetch).
    "radar": {
        "frames_local_minutes_ago": [40, 30, 20, 10, 0],
        "scale_label_en":           "0-10 mm/h",
        "wide": {
            # SW BC bbox (approx): minLon -125.5 maxLon -120.0 minLat 48.5 maxLat 50.5
            "bbox": [-125.5, 48.5, -120.0, 50.5],
            "vancouver_pin": [-123.1, 49.27],
            # Synthetic precip blobs (lon, lat, radius_deg, intensity 0-3)
            "blobs": [
                (-123.5, 49.4, 0.4, 2),
                (-122.7, 49.2, 0.3, 1),
                (-124.6, 49.6, 0.5, 3),
                (-122.0, 50.1, 0.4, 2),
            ],
        },
        "local": {
            # Metro Vancouver bbox: roughly the Lower Mainland strip
            "bbox": [-123.4, 49.0, -121.9, 49.4],
            "vancouver_pin": [-123.1, 49.27],
            "blobs": [
                (-123.2, 49.25, 0.10, 2),
                (-122.85, 49.18, 0.07, 1),
                (-122.5, 49.20, 0.12, 3),
                (-122.10, 49.22, 0.09, 2),
            ],
        },
    },
}


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------

def hm_to_minutes(s: str) -> int:
    """Convert ``HH:MM`` (24h) to minutes-of-day. Returns 0 on parse fail."""
    try:
        hh, mm = s.split(":")
        return int(hh) * 60 + int(mm)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Animation track registry + settings persistence
# ---------------------------------------------------------------------------
# Each animated radar/cloud/forecast track is a sequence of pre-rendered
# PNG frames produced by ``scripts/build_radar_anim.py`` on the server.
# Frames live at ``data/radar_anim/{track_name}/frame_NN.png``. The Pi
# loads them at startup and cycles at the configured FPS.

# Track configuration. Each track maps to a basemap preset (used for
# bbox + dimensions) plus a description of what gets composited and
# at what cadence. Half-zone presets are used for Pi-side radar screens
# unless the design overrides; full-zone designs use the _full variants.

def _load_animation_tracks() -> dict:
    """Load animation tracks from config, resolving viz_mode references and
    converting preset_keys to preset_half / preset_full.

    Tracks with a ``viz_mode`` field inherit layers, direction, frame_count,
    cadence_min, and history_hours from the matching visualization_modes
    entry, unless the track overrides them explicitly.

    Falls back to an empty dict if the config is missing or malformed.
    """
    import json as _j
    _presets_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "..", "config", "basemap_presets.json")
    try:
        with open(_presets_path) as f:
            cfg = _j.load(f)
        raw = cfg.get("animation_tracks", {})
        viz_modes = cfg.get("visualization_modes", {})
    except Exception as exc:
        logger.warning("Failed to load animation_tracks: %s", exc)
        return {}

    result = {}
    for tname, tcfg in raw.items():
        if not isinstance(tcfg, dict):
            continue
        # Skip _comment separator entries (string values)
        if tname.startswith("_comment"):
            continue
        # Skip pure stitch-screen markers (they have no layers of their own)
        if tcfg.get("is_stitch_screen"):
            # Still register them so get_stitch_group() works
            entry = dict(tcfg)
            keys = tcfg.get("preset_keys", [])
            if keys:
                entry["preset_half"] = keys[0]
                entry["preset_full"] = keys[1] if len(keys) > 1 else keys[0]
            result[tname] = entry
            continue

        # Resolve viz_mode: inherit layers/timing from visualization_modes
        entry = dict(tcfg)
        mode_name = tcfg.get("viz_mode")
        if mode_name and mode_name in viz_modes:
            mode = viz_modes[mode_name]
            # Inherit from mode, track overrides take precedence
            for field in ("layers", "direction", "frame_count", "cadence_min",
                          "history_hours", "description", "accumulate"):
                if field not in entry and field in mode:
                    entry[field] = mode[field]
            # Store the resolved mode name for the control panel
            entry["viz_mode"] = mode_name
            entry.setdefault("description", mode.get("description", ""))

        # Convert preset_keys list to preset_half / preset_full
        keys = tcfg.get("preset_keys", [])
        if keys:
            entry["preset_half"] = keys[0]
            entry["preset_full"] = keys[1] if len(keys) > 1 else keys[0]
        entry.setdefault("frame_count", 30)
        entry.setdefault("cadence_min", 6)
        entry.setdefault("direction", "past")
        entry.setdefault("description", entry.get("display_name", tname))
        result[tname] = entry
    return result


ANIMATION_TRACKS = _load_animation_tracks()


def get_animation_track(name: str) -> dict | None:
    """Return the config dict for an animation track, or None."""
    return ANIMATION_TRACKS.get(name)


def get_stitch_group(group_name: str) -> dict:
    """Return {'past': track_name, 'future': track_name} for a stitch group.

    Scans ANIMATION_TRACKS for tracks whose ``stitch_group`` matches and
    pairs them by direction. Returns an empty dict if no valid pair found.
    """
    past_track = None
    future_track = None
    for tname, tcfg in ANIMATION_TRACKS.items():
        if not isinstance(tcfg, dict):
            continue
        if tcfg.get("stitch_group") != group_name:
            continue
        if tcfg.get("is_stitch_screen"):
            continue  # the marker entry, not a real track
        direction = tcfg.get("direction", "past")
        if direction == "past" and past_track is None:
            past_track = tname
        elif direction == "future" and future_track is None:
            future_track = tname
    result = {}
    if past_track:
        result["past"] = past_track
    if future_track:
        result["future"] = future_track
    return result


def reload_animation_tracks() -> None:
    """Re-read config and rebuild the ANIMATION_TRACKS dict."""
    global ANIMATION_TRACKS
    ANIMATION_TRACKS = _load_animation_tracks()


# --- Settings persistence (FPS, per-track speed overrides, etc.) ---

_SETTINGS_LOCK = _Lock()
_SETTINGS_CACHE: dict | None = None

DEFAULT_RADAR_FPS = 6


def _settings_file_path() -> str:
    # BIGSCREEN_CONFIG_DIR is a *persistent* directory (e.g. /home/scott/bigscreen-calendar/config/).
    # Falls back to project config/ if unset (works on both server and dev).
    # Do NOT use BIGSCREEN_DATA_DIR here — that is tmpfs on the Pi and lost on reboot.
    base = os.environ.get("BIGSCREEN_CONFIG_DIR")
    if not base:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "config",
        )
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return os.path.join(base, "panel_settings.json")


def load_settings() -> dict:
    """Load (and cache) the panel-settings JSON blob."""
    global _SETTINGS_CACHE
    if _SETTINGS_CACHE is not None:
        return _SETTINGS_CACHE
    with _SETTINGS_LOCK:
        if _SETTINGS_CACHE is not None:
            return _SETTINGS_CACHE
        try:
            with open(_settings_file_path()) as f:
                _SETTINGS_CACHE = _json.load(f)
                if not isinstance(_SETTINGS_CACHE, dict):
                    _SETTINGS_CACHE = {}
        except (FileNotFoundError, _json.JSONDecodeError, OSError):
            _SETTINGS_CACHE = {}
    return _SETTINGS_CACHE


def save_settings(settings: dict) -> None:
    """Atomic-write the settings dict to disk."""
    global _SETTINGS_CACHE
    path = _settings_file_path()
    tmp = path + ".tmp"
    with _SETTINGS_LOCK:
        try:
            with open(tmp, "w") as f:
                _json.dump(settings, f, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Failed to persist panel settings to %s: %s", path, exc)
            return
        _SETTINGS_CACHE = settings


def get_radar_fps() -> int:
    """Frames-per-second for radar/cloud/forecast animations."""
    s = load_settings()
    val = s.get("radar_anim_fps", DEFAULT_RADAR_FPS)
    try:
        n = int(val)
        return max(1, min(60, n))
    except (TypeError, ValueError):
        return DEFAULT_RADAR_FPS


def full_frame_count(track_cfg: dict) -> int:
    """Return the expected total frame count for a fully-warmed track.

    ``frame_count`` in config is how many frames are *fetched per cron run*,
    not the animation length.  Over repeated runs frames accumulate on disk up
    to ``retention_hours``.  For accumulate-mode tracks the pack is always
    exactly ``frame_count`` frames; for all others use the full retention math.

    Priority for the time-window used in the calculation:
      1. ``history_hours``  — explicit display window (how far back to show)
      2. ``retention_hours`` — storage retention (may be longer than display)
      3. 72h default (conservative fallback)

    ``history_hours`` takes precedence because it directly bounds the number
    of frames that end up in the pack; using a larger ``retention_hours`` as
    the estimate causes the computed dwell to be much longer than one full
    animation loop, which makes the animation play many more loops than
    configured.

    This is the value that should be used when computing dwell times so
    animations show one complete time-span per loop (e.g. the full 72h radar
    history) rather than a tiny slice of it.
    """
    if track_cfg.get("is_stitch_screen"):
        # Stitch screens splice a past sub-track and a future sub-track together.
        # Neither sub-track has history_hours/retention_hours on the stitch entry
        # itself, so the generic math below would fall through to the 72h default
        # and compute a wildly inflated frame count (720 frames at 6-min cadence).
        # Instead, sum the real frame counts from the constituent sub-tracks.
        group = get_stitch_group(track_cfg.get("stitch_group", ""))
        total = 0
        for sub_name in (group.get("past"), group.get("future")):
            if sub_name:
                sub = ANIMATION_TRACKS.get(sub_name, {})
                total += full_frame_count(sub)
        return max(1, total)
    if track_cfg.get("accumulate"):
        return int(track_cfg.get("frame_count", 30))
    # Use history_hours if present (it bounds the actual pack size); fall back
    # to retention_hours, then 72h.
    h = (track_cfg.get("history_hours")
         or track_cfg.get("retention_hours")
         or 72)
    cadence = int(track_cfg.get("cadence_min", 6))
    return max(1, int(int(h) * 60 / cadence))


def get_screen_dwells() -> list[float]:
    """Compute per-screen dwell times in seconds.

    Static screens use ``SCREEN_DWELL_SEC`` as a baseline. Animated
    radar screens dwell long enough to play ``ANIM_LOOPS_PER_SCREEN``
    full loops at the current FPS, with a 0.25s safety margin so the
    very last frame is fully visible before the screen advances.

    Recomputed each render so changing FPS via the control panel
    immediately reshapes the cycle.
    """
    fps = max(1, get_radar_fps())
    dwells: list[float] = []
    for _name, _method, track in SCREEN_DISPATCH:
        if track is None:
            dwells.append(float(SCREEN_DWELL_SEC))
        else:
            cfg = ANIMATION_TRACKS.get(track, {})
            if cfg.get("is_stitch_screen"):
                # Stitched screen: sum full frame counts from both sub-tracks
                group = get_stitch_group(cfg.get("stitch_group", ""))
                total_frames = 0
                for sub_name in (group.get("past"), group.get("future")):
                    if sub_name:
                        sub = ANIMATION_TRACKS.get(sub_name, {})
                        total_frames += full_frame_count(sub)
                one_loop_sec = total_frames / float(fps)
                dwells.append(one_loop_sec * ANIM_LOOPS_PER_SCREEN + 0.5)
            else:
                n_frames = full_frame_count(cfg)
                one_loop_sec = n_frames / float(fps)
                dwells.append(one_loop_sec * ANIM_LOOPS_PER_SCREEN + 0.25)
    return dwells


def screen_idx_at(elapsed: float, dwells: list[float] | None = None) -> tuple[int, float]:
    """Given elapsed seconds since the cycle anchor, return the
    ``(screen_idx, total_cycle_sec)`` for the current auto-cycle position.

    Returns a SCREEN_DISPATCH index. The auto-cycle only covers static
    text screens (AUTO_CYCLE_IDXS); animation tracks never appear in the
    free-cycle — they only show when explicitly pinned by a cycle entry.

    ``dwells`` is ignored (kept for API compatibility); static screens all
    use SCREEN_DWELL_SEC.
    """
    auto = AUTO_CYCLE_IDXS
    if not auto:
        return 0, float(SCREEN_DWELL_SEC)
    dwell = float(SCREEN_DWELL_SEC)
    n = len(auto)
    total = n * dwell
    if total <= 0:
        return auto[0], dwell
    pos = elapsed % total
    i = min(int(pos / dwell), n - 1)
    return auto[i], total


def set_radar_fps(fps: int) -> int:
    """Persist a new radar FPS value, clamped to 1..60."""
    fps = max(1, min(60, int(fps)))
    s = dict(load_settings())
    s["radar_anim_fps"] = fps
    save_settings(s)
    return fps


# ---------------------------------------------------------------------------
# Element placer (live pixel-nudging support)
# ---------------------------------------------------------------------------
#
# Each design replaces hardcoded (x, y) coordinates with a call to a
# placer object that:
#   1. Registers the element so the control panel can list it,
#   2. Looks up any saved offset, and
#   3. Returns the absolute (x, y) to draw at.
#
# Offsets are stored in ``data/panel_offsets.json`` keyed by
# (mode, design, screen, element). The file is rewritten on every
# nudge / reset via the override HTTP server so the values persist
# across client restarts.
#
# Design code looks like:
#
#     place = ElementPlacer("panels_half", "01-atlas", "current", x, y, w, h)
#     px, py = place("header_title", 5, 11)
#     draw_text(canvas, fonts["small"], px, py, palette["fg"], cur["location"])
#
# That single line registers the element, applies any saved nudge,
# and returns the absolute draw position.

# (_json and _Lock are imported at the top of the module so the
# settings/animation helpers above can use them too.)

_OFFSETS_LOCK = _Lock()
_OFFSETS_CACHE: dict | None = None
_REGISTERED_ELEMENTS: dict = {}     # (mode, design, screen) -> [(name, dflt_dx, dflt_dy), ...]
_REGISTERED_LOCK = _Lock()


def _offsets_file_path() -> str:
    """Return the absolute path of the panel-offsets JSON file.

    Lives under BIGSCREEN_CONFIG_DIR (persistent, survives reboots),
    NOT in BIGSCREEN_DATA_DIR which is tmpfs on the Pi.
    """
    base = os.environ.get("BIGSCREEN_CONFIG_DIR")
    if not base:
        base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
            "config",
        )
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        pass
    return os.path.join(base, "panel_offsets.json")


def load_offsets() -> dict:
    """Load offsets from disk (cached). Returns a nested dict of
    ``{mode: {design: {screen: {element: [dx, dy]}}}}``."""
    global _OFFSETS_CACHE
    if _OFFSETS_CACHE is not None:
        return _OFFSETS_CACHE
    with _OFFSETS_LOCK:
        if _OFFSETS_CACHE is not None:
            return _OFFSETS_CACHE
        try:
            with open(_offsets_file_path()) as f:
                _OFFSETS_CACHE = _json.load(f)
                if not isinstance(_OFFSETS_CACHE, dict):
                    _OFFSETS_CACHE = {}
        except (FileNotFoundError, _json.JSONDecodeError, OSError):
            _OFFSETS_CACHE = {}
    return _OFFSETS_CACHE


def save_offsets(offsets: dict) -> None:
    """Atomic-write the offsets dict to disk and update the cache."""
    global _OFFSETS_CACHE
    path = _offsets_file_path()
    tmp = path + ".tmp"
    with _OFFSETS_LOCK:
        try:
            with open(tmp, "w") as f:
                _json.dump(offsets, f, indent=2, sort_keys=True)
            os.replace(tmp, path)
        except OSError as exc:
            logger.warning("Failed to persist panel offsets to %s: %s", path, exc)
            return
        _OFFSETS_CACHE = offsets


def get_offset(mode: str, design: str, screen: str, element: str) -> tuple[int, int]:
    """Return the saved (dx, dy) offset for an element, or (0, 0)."""
    o = load_offsets()
    raw = (o.get(mode, {})
            .get(design, {})
            .get(screen, {})
            .get(element, [0, 0]))
    if isinstance(raw, list) and len(raw) >= 2:
        try:
            return int(raw[0]), int(raw[1])
        except (TypeError, ValueError):
            pass
    return 0, 0


def set_offset(mode: str, design: str, screen: str, element: str,
               dx: int, dy: int) -> tuple[int, int]:
    """Replace the stored offset for an element; persists to disk."""
    o = dict(load_offsets())   # shallow copy; we mutate per-level
    o.setdefault(mode, {}).setdefault(design, {}).setdefault(screen, {})[element] = [int(dx), int(dy)]
    save_offsets(o)
    return int(dx), int(dy)


def nudge_offset(mode: str, design: str, screen: str, element: str,
                 ddx: int, ddy: int) -> tuple[int, int]:
    """Add (ddx, ddy) to the existing offset and persist."""
    cdx, cdy = get_offset(mode, design, screen, element)
    return set_offset(mode, design, screen, element, cdx + int(ddx), cdy + int(ddy))


def reset_offset(mode: str, design: str, screen: str, element: str) -> None:
    """Remove the stored offset for one element."""
    o = dict(load_offsets())
    try:
        del o[mode][design][screen][element]
        save_offsets(o)
    except KeyError:
        pass


def reset_all_offsets(mode: str | None = None,
                      design: str | None = None,
                      screen: str | None = None) -> None:
    """Remove offsets at the given scope (all if all args None)."""
    o = dict(load_offsets())
    if mode is None:
        save_offsets({})
        return
    if design is None:
        o.pop(mode, None)
    elif screen is None:
        o.get(mode, {}).pop(design, None)
    else:
        o.get(mode, {}).get(design, {}).pop(screen, None)
    save_offsets(o)


def _register_element(mode: str, design: str, screen: str,
                      element: str, default_dx: int, default_dy: int) -> None:
    """Track an element so the control panel can list it."""
    key = (mode, design, screen)
    with _REGISTERED_LOCK:
        lst = _REGISTERED_ELEMENTS.setdefault(key, [])
        for i, e in enumerate(lst):
            if e[0] == element:
                lst[i] = (element, int(default_dx), int(default_dy))
                return
        lst.append((element, int(default_dx), int(default_dy)))


def _discover_elements_from_source(mode: str, design: str, screen: str) -> list[tuple]:
    """Static discovery: inspect the design's screen method source for
    ``place("element_name", dx, dy)`` calls. Returns a list of
    ``(name, default_dx, default_dy)`` tuples without needing a render pass.
    """
    import inspect, re as _re
    # Map mode -> design list
    if "half" in mode:
        from .designs_half import HALF_DESIGNS as designs
    else:
        from .designs_full import FULL_DESIGNS as designs

    # Find the design class by name
    cls = None
    for d in designs:
        if getattr(d, "name", None) == design:
            cls = d
            break
    if cls is None:
        return []

    # Map screen name to method name via SCREEN_DISPATCH
    method_name = None
    for sname, mname, _track in SCREEN_DISPATCH:
        if sname == screen:
            method_name = mname
            break
    if method_name is None:
        return []

    method = getattr(cls, method_name, None)
    if method is None:
        return []

    try:
        source = inspect.getsource(method)
    except (OSError, TypeError):
        return []

    # Match place("element_name", dx, dy) and place.right/place.bottom variants
    elements = []
    seen = set()
    for m in _re.finditer(r'place(?:\.(?:right|bottom))?\(\s*"([^"]+)"\s*,\s*(-?\d+)\s*,\s*(-?\d+)', source):
        name, dx, dy = m.group(1), int(m.group(2)), int(m.group(3))
        if name not in seen:
            elements.append((name, dx, dy))
            seen.add(name)
    return elements


def list_elements(mode: str, design: str, screen: str) -> list[dict]:
    """Return a list of element descriptors for the given screen.

    Each entry is ``{"name": str, "default": [dx, dy], "offset": [dx, dy]}``.
    Used by the override server's GET /weather_panels/elements endpoint.

    If the screen hasn't been rendered yet (elements not in the dynamic
    registry), falls back to static source inspection of the design code.
    """
    key = (mode, design, screen)
    with _REGISTERED_LOCK:
        registered = list(_REGISTERED_ELEMENTS.get(key, []))

    # Fall back to source inspection if nothing registered yet
    if not registered:
        registered = _discover_elements_from_source(mode, design, screen)

    out = []
    for name, dflt_dx, dflt_dy in registered:
        odx, ody = get_offset(mode, design, screen, name)
        out.append({
            "name":    name,
            "default": [dflt_dx, dflt_dy],
            "offset":  [odx, ody],
        })
    return out


class ElementPlacer:
    """Per-screen helper that converts (default_dx, default_dy) into an
    absolute (x, y) using the saved nudge offset for that element. Also
    registers the element so the control panel knows it exists."""

    __slots__ = ("mode", "design", "screen", "x_zone", "y_zone", "w_zone", "h_zone")

    def __init__(self, mode: str, design: str, screen: str,
                 x_zone: int, y_zone: int, w_zone: int, h_zone: int):
        self.mode = mode
        self.design = design
        self.screen = screen
        self.x_zone = int(x_zone)
        self.y_zone = int(y_zone)
        self.w_zone = int(w_zone)
        self.h_zone = int(h_zone)

    def __call__(self, element: str, default_dx: int, default_dy: int) -> tuple[int, int]:
        _register_element(self.mode, self.design, self.screen,
                          element, default_dx, default_dy)
        odx, ody = get_offset(self.mode, self.design, self.screen, element)
        return self.x_zone + int(default_dx) + odx, self.y_zone + int(default_dy) + ody

    # Convenience accessors for designs that want to reason about
    # the bottom-right edge, etc., relative to default offsets.
    def right(self, element: str, default_dx_from_right: int, default_dy: int) -> tuple[int, int]:
        """Place anchored from the RIGHT edge; default_dx_from_right is
        the inset from the right side of the zone."""
        return self.__call__(element, self.w_zone - int(default_dx_from_right), default_dy)

    def bottom(self, element: str, default_dx: int, default_dy_from_bottom: int) -> tuple[int, int]:
        return self.__call__(element, default_dx, self.h_zone - int(default_dy_from_bottom))
