"""Font audit: sample text in any BDF under assets/fonts, on the wall (control panel → Fonts → "Show on
the wall") and in the hub's previews. Handles the JIS-encoded v1 fonts by mapping text to JIS codes."""

from __future__ import annotations

import os
import re

from display.gfx import ASSETS_DIR, Color
from display.modules import weather_alert
from display.modules.weather_alert import FI

W, H = 448, 192
FONT_ROOT = os.path.join(ASSETS_DIR, "fonts")
_CHARSET = {}
_FI = {}
_MINE = set()                          # parsed here (not by a theme): safe to free


def font(rel):
    """FI for a path relative to assets/fonts (cached)."""
    if rel not in _FI:
        if len(_FI) > 40:                         # the audit walks hundreds of fonts: don't keep them all
            for r in _MINE:
                weather_alert._FONTS.pop(r, None)     # (Japanese faces are ~4 MB each once parsed)
            _MINE.clear()
            _FI.clear()
        if rel not in weather_alert._FONTS:
            _MINE.add(rel)
        f = FI(rel)
        if f.h <= 1 and codec(rel):                # JIS-only fonts have no ASCII to measure: use kana/kanji
            mask, top, _ = f.f.text_mask(to_font(rel, "漢字あア"))
            f.asc, f.h = -top, mask.shape[0]
            f.line = f.h + 1
        mask, top, _ = f.f.text_mask("0123456789")
        if mask.shape[0] > f.h:                     # digit-only fonts (seven-segment): measure the digits
            f.asc, f.h = -top, mask.shape[0]
            f.line = f.h + 1
        _FI[rel] = f
    return _FI[rel]


def codec(rel):
    """None for Unicode fonts, else the codec that maps text to the font's JIS code points."""
    if rel not in _CHARSET:
        head = open(os.path.join(FONT_ROOT, rel), encoding="latin-1").read(4096)
        m = re.search(r'CHARSET_REGISTRY\s+"?([^"\n]+)', head)
        reg = (m.group(1) if m else "").upper()
        _CHARSET[rel] = "euc_jis_2004" if "0213" in reg else "euc_jp" if "JISX0208" in reg else None
    return _CHARSET[rel]


def to_font(rel, s):
    """Text in the font's own code space (JIS fonts store kanji at their JIS row/cell, e.g. 0x3021)."""
    cd = codec(rel)
    if not cd:
        return s
    out = []
    for ch in s:
        try:
            b = ch.encode(cd)
        except UnicodeEncodeError:
            out.append(ch)
            continue
        out.append(chr(((b[0] & 0x7F) << 8) | (b[1] & 0x7F)) if len(b) == 2 and b[0] >= 0xA1 else ch)
    return "".join(out)


def has_glyphs(rel, s):
    f = font(rel)
    t = to_font(rel, s)
    chars = [ch for ch in t if not ch.isspace()]
    have = sum(1 for ch in chars if f.f.CharacterWidth(ord(ch)) > 0)
    return bool(chars) and have >= 0.9 * len(chars)          # a missing ° or — shouldn't hide the whole sample


def wrap(rel, text, width):
    """Lines that fit ``width`` px: break at spaces, or anywhere inside CJK text."""
    f = font(rel)
    lines = []
    for para in text.split("\n"):
        cur = ""
        tokens = re.findall(r"\s+|[　-鿿＀-￯]|[^\s　-鿿＀-￯]+", para)
        for tok in tokens:
            cand = cur + tok
            if cur and f.width(to_font(rel, cand.rstrip())) > width:
                lines.append(cur.rstrip())
                cur = tok.lstrip()
            else:
                cur = cand
        lines.append(cur.rstrip())
    return lines


def draw_text(c, rel, x, y, colour, s):
    return font(rel).draw(c, x, y, colour, to_font(rel, s))


def render(c, fonts, latin, jp, fg=(255, 255, 255), x0=0, y0=0, width=W, height=H, label=True):
    """One font: its name, then the samples wrapped to fill the screen. Several: one band each."""
    tiny = font("alert/4x6.bdf")
    if len(fonts) == 1:
        rel = fonts[0]
        f = font(rel)
        y = y0 + 1
        if label:
            tiny.draw(c, x0 + 2, y, (110, 110, 130), f"{rel}  ·  {f.h} px")
            y += 8
        samples = [t for t in (latin, jp) if t and has_glyphs(rel, t.replace("\n", ""))] or [latin or jp]
        for t in samples:
            for ln in wrap(rel, t, width - 4):
                if y + f.h > y0 + height:
                    return y
                draw_text(c, rel, x0 + 2, y, fg, ln)
                y += f.line
            y += 2
        return y
    band = max(10, height // len(fonts))
    for i, rel in enumerate(fonts):
        y = y0 + i * band
        c.FillRect(x0, y, width, 1, Color(40, 40, 52))
        if label:
            tiny.draw(c, x0 + 2, y + 2, (110, 110, 130), os.path.basename(rel)[:-4][:16])
        t = jp if jp and has_glyphs(rel, jp) else latin
        lx = x0 + (68 if label else 2)
        line = wrap(rel, t, width - lx - 2)[0] if t else ""
        below = c.buf[y + band:y0 + height].copy()               # clip each band to its own height
        draw_text(c, rel, lx, y + 2, fg, line)
        c.buf[y + band:y0 + height] = below
    return y0 + height


class FontSpecimen:
    """Takeover panel ``font_specimen``: entry fields ``fonts`` (paths under assets/fonts), ``text``, ``jp``, ``fg``."""

    def draw(self, c, zone, entry, transition, now=None):
        c.FillRect(0, 0, W, H, Color(0, 0, 0))
        fonts = [f for f in (entry.get("fonts") or []) if os.path.exists(os.path.join(FONT_ROOT, f))][:12]
        if not fonts:
            FI("alert/5x8.bdf").draw(c, 4, 4, (200, 200, 200), "No font selected.")
            return None
        fg = entry.get("fg") or (255, 255, 255)
        try:
            render(c, fonts, entry.get("text") or "", entry.get("jp") or "", tuple(fg))
        except Exception as e:                                  # a broken candidate font must not take the wall down
            FI("alert/5x8.bdf").draw(c, 4, 4, (255, 120, 120), f"Font error: {str(e)[:70]}")
        return None
