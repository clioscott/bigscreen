"""Numpy frame buffer + a pixel-exact stand-in for rgbmatrix's `graphics` module.

The v1 modules draw with `graphics.DrawText/DrawLine/Color/Font` and
`canvas.SetPixel`. This module reproduces those calls exactly (same BDF parsing
quirks, same fixed-point line algorithm) but draws into a numpy array, so:

  * a whole frame reaches the panels in ONE C++ call (FrameCanvas.SetBuffer),
  * the frame can be mirrored to the control panel as a PNG,
  * the renderer runs on any machine (server, Windows) for previews.

Fast paths the v1 code lacked: FillRect (slice assignment instead of one
DrawLine per row), cached rendered strings, and Blit for icons/images.
"""

from __future__ import annotations

import os
from collections import OrderedDict

import numpy as np

K_MAX_FONT_WIDTH = 196          # rgbmatrix kMaxFontWidth (bitset width)
REPLACEMENT_CP = 0xFFFD


class Color:
    __slots__ = ("red", "green", "blue", "rgb", "arr")

    def __init__(self, r=0, g=0, b=0):
        self.red, self.green, self.blue = int(r) & 255, int(g) & 255, int(b) & 255
        self.rgb = (self.red, self.green, self.blue)
        self.arr = np.array(self.rgb, dtype=np.uint8)     # avoids tuple→array conversion per fill


def _rgb(c):
    return c.arr if isinstance(c, Color) else np.array((int(c[0]), int(c[1]), int(c[2])), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------
class Canvas:
    """Drop-in for rgbmatrix FrameCanvas, backed by an (h, w, 3) uint8 array."""

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.buf = np.zeros((height, width, 3), dtype=np.uint8)

    # rgbmatrix API ------------------------------------------------------
    def SetPixel(self, x, y, r, g, b):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.buf[y, x] = (r, g, b)

    def Clear(self):
        self.buf.fill(0)

    def Fill(self, r, g, b):
        self.buf[:] = (r, g, b)

    def SetImage(self, image, offset_x=0, offset_y=0, unsafe=True):
        """Accepts a PIL image (any mode) like rgbmatrix's SetImage."""
        if image.mode != "RGB":
            image = image.convert("RGB")
        self.Blit(np.asarray(image), offset_x, offset_y)

    # fast paths -----------------------------------------------------------
    def FillRect(self, x, y, w, h, color):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(self.width, x + w), min(self.height, y + h)
        if x1 > x0 and y1 > y0:
            self.buf[y0:y1, x0:x1] = _rgb(color)

    def Blit(self, arr, x, y, mask=None):
        """Copy an (h, w, 3) uint8 array to (x, y), clipped. `mask` (h, w) bool limits the copy."""
        h, w = arr.shape[:2]
        sx0, sy0 = max(0, -x), max(0, -y)
        dx0, dy0 = max(0, x), max(0, y)
        dx1, dy1 = min(self.width, x + w), min(self.height, y + h)
        if dx1 <= dx0 or dy1 <= dy0:
            return
        src = arr[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
        if mask is None:
            self.buf[dy0:dy1, dx0:dx1] = src[..., :3]
        else:
            m = mask[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
            self.buf[dy0:dy1, dx0:dx1][m] = src[..., :3][m]

    def BlitMask(self, mask, x, y, color):
        """Paint `color` wherever the (h, w) bool mask is set, clipped."""
        h, w = mask.shape
        sx0, sy0 = max(0, -x), max(0, -y)
        dx0, dy0 = max(0, x), max(0, y)
        dx1, dy1 = min(self.width, x + w), min(self.height, y + h)
        if dx1 <= dx0 or dy1 <= dy0:
            return
        m = mask[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
        self.buf[dy0:dy1, dx0:dx1][m] = _rgb(color)


# ---------------------------------------------------------------------------
# BDF fonts (mirrors rpi-rgb-led-matrix lib/bdf-font.cc)
# ---------------------------------------------------------------------------
class _Glyph:
    __slots__ = ("device_width", "height", "y_offset", "mask")


def _parse_bitmap_row(line):
    """Hex row → list of set x positions, left-aligned like parseBitmap()."""
    bits = []
    x = 0
    for ch in line.strip():
        try:
            nib = int(ch, 16)
        except ValueError:
            break
        for b in (8, 4, 2, 1):
            if nib & b:
                bits.append(x)
            x += 1
        if x >= K_MAX_FONT_WIDTH:
            break
    return bits, x > 0


class Font:
    """Drop-in for rgbmatrix graphics.Font."""

    _file_cache: dict = {}

    def __init__(self):
        self.glyphs: dict[int, _Glyph] = {}
        self.height = 0
        self.baseline = 0
        self._text_cache: OrderedDict = OrderedDict()

    def LoadFont(self, path):
        path = resolve_asset(path)
        cached = Font._file_cache.get(path)
        if cached:
            self.glyphs, self.height, self.baseline = cached
            return True
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        glyphs = {}
        height = baseline = 0
        cp = 0
        dw = 0
        cur = None   # (width, height, x_off, y_off, rows)
        row = -1
        with open(path, "r", encoding="latin-1") as f:
            for line in f:
                if line.startswith("FONTBOUNDINGBOX"):
                    p = line.split()
                    height = int(p[2])
                    baseline = int(p[4]) + height
                elif line.startswith("ENCODING"):
                    try:
                        cp = int(line.split()[1])
                    except (IndexError, ValueError):
                        pass
                elif line.startswith("DWIDTH"):
                    dw = min(int(line.split()[1]), K_MAX_FONT_WIDTH)
                elif line.startswith("BBX"):
                    p = line.split()
                    w, h, xo, yo = int(p[1]), int(p[2]), int(p[3]), int(p[4])
                    cur = [w, h, xo, yo, []]
                    row = -1
                elif line.startswith("BITMAP"):
                    row = 0
                elif line.startswith("ENDCHAR"):
                    if cur is not None and row == cur[1]:
                        glyphs[cp] = _make_glyph(dw, cur)
                    cur = None
                    row = -1
                elif cur is not None and 0 <= row < cur[1]:
                    xs, ok = _parse_bitmap_row(line)
                    if ok:
                        cur[4].append(xs)
                        row += 1
        self.glyphs, self.height, self.baseline = glyphs, height, baseline
        Font._file_cache[path] = (glyphs, height, baseline)
        return True

    def CharacterWidth(self, cp):
        g = self.glyphs.get(cp)
        return g.device_width if g else -1

    def _glyph(self, cp):
        return self.glyphs.get(cp) or self.glyphs.get(REPLACEMENT_CP)

    def text_mask(self, text, extra_spacing=0):
        """Rendered string as (mask[h, w] bool, top relative to baseline, advance width). Cached."""
        key = (text, extra_spacing)
        hit = self._text_cache.get(key)
        if hit is not None:
            self._text_cache.move_to_end(key)
            return hit
        placed = []
        x = 0
        top, bottom = 0, 0
        for ch in text:
            g = self._glyph(ord(ch))
            if g is None:
                x += extra_spacing
                continue
            gtop = -g.height - g.y_offset
            placed.append((x, gtop, g))
            top = min(top, gtop)
            bottom = max(bottom, gtop + g.height)
            x += g.device_width + extra_spacing
        width = max(x, 1)
        mask = np.zeros((max(bottom - top, 1), width), dtype=bool)
        for gx, gtop, g in placed:
            if g.mask is None:
                continue
            gh, gw = g.mask.shape
            gw = min(gw, width - gx)
            if gw > 0:
                mask[gtop - top:gtop - top + gh, gx:gx + gw] |= g.mask[:, :gw]
        result = (mask, top, x)
        self._text_cache[key] = result
        if len(self._text_cache) > 600:
            self._text_cache.popitem(last=False)
        return result


def _make_glyph(device_width, cur):
    w, h, x_off, y_off, rows = cur
    g = _Glyph()
    g.device_width = device_width
    g.height = h
    g.y_offset = y_off
    if device_width <= 0 or h <= 0:
        g.mask = None
        return g
    m = np.zeros((h, device_width), dtype=bool)
    for y, xs in enumerate(rows):
        # bdf-font.cc: bitmap[row] >>= x_offset. A negative shift converts to a huge size_t and
        # empties the bitset, so such rows render blank on the real panel — keep that behaviour.
        if x_off < 0:
            continue
        for x in xs:
            px = x + x_off
            if px < device_width:          # DrawGlyph only scans x < device_width
                m[y, px] = True
    g.mask = m if m.any() else None
    return g


# ---------------------------------------------------------------------------
# graphics module functions
# ---------------------------------------------------------------------------
def DrawText(canvas, font, x, y, color, text, *args):
    """Same signature/return as rgbmatrix graphics.DrawText (y is the baseline)."""
    if not text:
        return 0
    mask, top, advance = font.text_mask(text)
    canvas.BlitMask(mask, x, y + top, color)
    return advance


def _cdiv(a, b):
    """C integer division (truncates toward zero)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def DrawLine(canvas, x0, y0, x1, y1, color):
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    if y0 == y1:                                   # fast path: horizontal
        a, b = (x0, x1) if x0 <= x1 else (x1, x0)
        canvas.FillRect(a, y0, b - a + 1, 1, color)
        return
    if x0 == x1:                                   # fast path: vertical
        a, b = (y0, y1) if y0 <= y1 else (y1, y0)
        canvas.FillRect(x0, a, 1, b - a + 1, color)
        return
    r, g, b_ = color.rgb if isinstance(color, Color) else color
    dy, dx, shift = y1 - y0, x1 - x0, 16
    if abs(dx) > abs(dy):
        if x1 < x0:
            x0, x1, y0, y1 = x1, x0, y1, y0
        grad = _cdiv(dy << shift, dx)
        y = 0x8000 + (y0 << shift)
        for x in range(x0, x1 + 1):
            canvas.SetPixel(x, y >> shift, r, g, b_)
            y += grad
    else:
        if y1 < y0:
            x0, x1, y0, y1 = x1, x0, y1, y0
        grad = _cdiv(dx << shift, dy)
        x = 0x8000 + (x0 << shift)
        for y in range(y0, y1 + 1):
            canvas.SetPixel(x >> shift, y, r, g, b_)
            x += grad


def DrawCircle(canvas, x0, y0, radius, color):
    """Midpoint circle, as rgbmatrix graphics.cc."""
    r, g, b = color.rgb if isinstance(color, Color) else color
    x, y, err = radius, 0, 1 - radius
    while y <= x:
        for px, py in ((x, y), (y, x), (-y, x), (-x, y), (-x, -y), (-y, -x), (y, -x), (x, -y)):
            canvas.SetPixel(x0 + px, y0 + py, r, g, b)
        y += 1
        if err < 0:
            err += 2 * y + 1
        else:
            x -= 1
            err += 2 * (y - x + 1)


class _GraphicsNamespace:
    """So ported modules can keep writing `graphics.DrawText(...)`."""
    Color = Color
    Font = Font
    DrawText = staticmethod(DrawText)
    DrawLine = staticmethod(DrawLine)
    DrawCircle = staticmethod(DrawCircle)


graphics = _GraphicsNamespace()


# ---------------------------------------------------------------------------
# assets
# ---------------------------------------------------------------------------
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(REPO_DIR, "assets")


def resolve_asset(path):
    """Config paths look like 'fonts/5x7.bdf' (v1 cwd-relative) → repo/assets/fonts/5x7.bdf."""
    if not path or os.path.isabs(path):
        return path
    cand = os.path.join(ASSETS_DIR, path)
    return cand if os.path.exists(cand) else path
