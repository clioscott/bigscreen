"""1-bit pixel icons for the alert and EEW screens (assets/icons/pixel_icons.json).

24 px: pixelarticons (MIT), plus composites drawn on the same grid (rain, thunder, freezing rain, fog, tornado).
8 / 16 px: Open Iconic, from the u8g2 icon fonts. Every icon is drawn at its native size, one pixel per LED.
"""

from __future__ import annotations

import json
import os

import numpy as np

from display.gfx import ASSETS_DIR, Color

_ICONS = None
_MASKS = {}
HAZARD = {"rain": "rain", "snow": "snow", "freezing": "freezing", "wind": "wind", "thunder": "thunder", "hail": "hail",
          "tornado": "tornado", "flood": "flood", "heat": "heat", "cold": "cold", "frost": "frost", "fog": "fog",
          "smoke": "smoke", "general": "general"}


def _load():
    global _ICONS
    if _ICONS is None:
        _ICONS = json.load(open(os.path.join(ASSETS_DIR, "icons", "pixel_icons.json")))
    return _ICONS


def mask(name, size=24):
    """Bool array (h, w) for an icon; falls back to the 24-px warning diamond."""
    key = (name, size)
    if key not in _MASKS:
        sets = _load()
        rows = sets.get(str(size), {}).get(name) or sets["24"].get(name) or sets["24"]["general"]
        _MASKS[key] = np.array([[ch == "#" for ch in r] for r in rows], dtype=bool)
    return _MASKS[key]


def hazard(h):
    return HAZARD.get(h, "general")


def draw(c, name, x, y, colour, size=24, plate=None, pad=0):
    """Blit an icon (optionally on a filled plate); returns its width."""
    m = mask(name, size)
    if plate is not None:
        c.FillRect(x - pad, y - pad, m.shape[1] + 2 * pad, m.shape[0] + 2 * pad, Color(*plate))
    c.BlitMask(m, x, y, Color(*colour))
    return m.shape[1]


def outlined(c, name, x, y, colour, outline=(0, 0, 0), size=24):
    """Icon with a 1-px dark outline so it reads on any background (maps, pictures)."""
    m = mask(name, size)
    p = np.pad(m, 1)
    o = (p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:] | m) & ~m
    big = np.zeros((m.shape[0] + 2, m.shape[1] + 2), bool)
    big[1:-1, 1:-1] = o
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        big[1 + dy:m.shape[0] + 1 + dy, 1 + dx:m.shape[1] + 1 + dx] |= o
    c.BlitMask(big & ~np.pad(m, 1), x - 1, y - 1, Color(*outline))
    c.BlitMask(m, x, y, Color(*colour))
    return m.shape[1]
