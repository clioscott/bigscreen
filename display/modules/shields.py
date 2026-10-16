"""Highway shields as LED pixel art (assets/icons/shields/shields.json, built by tools/build_shields.py).

    shields.draw(c, x, y, "1", h=20)          # Trans-Canada 1, 20 px tall; returns the width drawn
    shields.kind_for("16") → "yellowhead"

Each sign type (BC, Trans-Canada, Yellowhead, Crowsnest, Washington, US Highway, Interstate) has a hinted
sprite per size and a number box; the route number is drawn in Overpass Bold digits (Highway Gothic's open
descendant) at the largest height that fits the box, switching to the narrow cut for three-character routes.
"""

from __future__ import annotations

import json
import os

import numpy as np

from display.gfx import ASSETS_DIR, Color

_DATA = None
_SPRITES = {}
KINDS_BY_ROUTE = {"1": "tch", "16": "yellowhead", "5": "yellowhead", "3": "crowsnest"}
_PLACED = {}


def _data():
    global _DATA
    if _DATA is None:
        _DATA = json.load(open(os.path.join(ASSETS_DIR, "icons", "shields", "shields.json"), encoding="utf8"))
    return _DATA


def kind_for(route, network="bc"):
    """BC routes by number (1 Trans-Canada, 16/5 Yellowhead, 3 Crowsnest, else BC); "I-5", "US 97", "SR 543"."""
    r = str(route).upper().replace(" ", "")
    if r.startswith("I-") or r.startswith("I5") and len(r) <= 3:
        return "interstate"
    if r.startswith("US"):
        return "us"
    if r.startswith(("SR", "WA")):
        return "wa"
    return KINDS_BY_ROUTE.get(r, network)


def _route_text(route):
    r = str(route).upper().replace(" ", "")
    for p in ("I-", "US-", "US", "SR-", "SR", "WA-", "WA"):
        if r.startswith(p):
            return r[len(p):]
    return r


def _kind(kind):
    d = _data()
    kind = d.get("aliases", {}).get(kind, kind)
    return kind, d["kinds"][kind]


def sizes(kind="bc"):
    return sorted(int(h) for h in _kind(kind)[1]["sizes"])


def _sprite(kind, h):
    key = (kind, h)
    if key not in _SPRITES:
        kind, spec = _kind(kind)
        avail = sorted(int(s) for s in spec["sizes"])
        hh = max([s for s in avail if s <= h] or [avail[0]])
        rows = spec["sizes"][str(hh)]
        idx = np.array([[".abcdefghij".index(ch) for ch in r] for r in rows], dtype=np.int8)
        pal = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in spec["palette"]]
        rgb = np.zeros(idx.shape + (3,), np.uint8)
        for i, col in enumerate(pal):
            rgb[idx == i + 1] = col
        m = (spec.get("size_meta") or {}).get(str(hh)) or {}
        spec = dict(spec, number=m.get("number", spec["number"]), box=m.get("box", spec["box"]), centre=m.get("centre"),
                    allow=np.array([[ch == "#" for ch in r] for r in m["allow"]], bool) if m.get("allow") else None)
        _SPRITES[key] = (rgb, idx > 0, spec, pal)
    return _SPRITES[key]


def _glyph(cut, hd, ch):
    rows = _data()["digits"][cut][str(hd)].get(ch)
    return np.array([[c == "#" for c in r] for r in rows], bool) if rows else None


# below this digit height Highway Gothic stops reading: small routes use small bitmap digits instead
MIN_HG = 8
SMALL_FONTS = ["alert/ctrld-fixed-10b.bdf", "alert/gohufont-11b.bdf", "alert/u8g2_squeezed_bold_7.bdf", "alert/tom-thumb.bdf",
               "candidates/micro.bdf"]
_SMALL = {}


def _small_glyph(path, ch):
    key = (path, ch)
    if key not in _SMALL:
        from display.modules.bc_kit import FI
        f = FI(path, 1)
        mask, _top, _adv = f.f.text_mask(ch)
        ys, xs = np.nonzero(mask)
        _SMALL[key] = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(ys) else None
    return _SMALL[key]


def _number_layout(text, bw, bh):
    """(cut, digit height, glyphs) — the largest that fits the box (narrow cut for long routes), falling back to
    small bitmap digits where Highway Gothic would be too small to read."""
    heights = sorted((int(k) for k in _data()["digits"]["bold"]), reverse=True)
    for cut in ("bold", "narrow"):
        for hd in heights:
            if hd > bh or hd < MIN_HG:
                continue
            gl = [_glyph(cut, hd, ch) for ch in text]
            if any(g is None for g in gl):
                continue
            w = sum(g.shape[1] for g in gl) + max(0, len(gl) - 1) * (1 if hd < 12 else 2)
            if w <= bw:
                return cut, hd, gl, w
    for path in SMALL_FONTS:
        gl = [_small_glyph(path, ch) for ch in text]
        if any(g is None for g in gl):
            continue
        hd = max(g.shape[0] for g in gl)
        w = sum(g.shape[1] for g in gl) + len(gl) - 1
        if hd <= bh and w <= bw:
            return path, hd, gl, w
    gl = [g for g in (_small_glyph(SMALL_FONTS[-1], ch) for ch in text) if g is not None]
    return SMALL_FONTS[-1], max(g.shape[0] for g in gl), gl, sum(g.shape[1] for g in gl) + len(gl) - 1


def _candidates(text):
    """Every way to set the route, largest first: Highway Gothic bold, then narrow, then small bitmap digits."""
    heights = sorted((int(k) for k in _data()["digits"]["bold"]), reverse=True)
    for hd in heights:
        if hd < MIN_HG:
            break
        for cut in ("bold", "narrow"):
            gl = [_glyph(cut, hd, ch) for ch in text]
            if all(g is not None for g in gl):
                yield gl, (2 if hd >= 12 else 1), hd
    for path in SMALL_FONTS:
        gl = [_small_glyph(path, ch) for ch in text]
        if all(g is not None for g in gl):
            yield gl, 1, max(g.shape[0] for g in gl)


def _text_mask(gl, gap, hd):
    w = sum(g.shape[1] for g in gl) + gap * (len(gl) - 1)
    m = np.zeros((hd, w), bool)
    x = 0
    for g in gl:
        m[hd - g.shape[0]:, x:x + g.shape[1]] = g
        x += g.shape[1] + gap
    return m


def _place(allow, centre, text, tol=1.0):
    """(mask, ox, oy): the largest setting of ``text`` whose every pixel lies in ``allow`` and whose middle is
    within ``tol`` px of the centre (a smaller setting, centred, beats a bigger one pushed aside)."""
    ah, aw = allow.shape
    cx, cy = centre
    for gl, gap, hd in _candidates(text):
        m = _text_mask(gl, gap, hd)
        th, tw_ = m.shape
        if th > ah or tw_ > aw:
            continue
        best = None
        for oy in range(0, ah - th + 1):
            for ox in range(0, aw - tw_ + 1):
                if not allow[oy:oy + th, ox:ox + tw_][m].all():
                    continue
                d = (ox + tw_ / 2 - cx) ** 2 + (oy + th / 2 - cy) ** 2
                if best is None or d < best[0]:
                    best = (d, ox, oy)
        if best and best[0] <= 2 * (tol + 0.5) ** 2:
            return m, best[1], best[2]
    gl, gap, hd = list(_candidates(text))[-1]
    m = _text_mask(gl, gap, hd)
    return m, int(cx - m.shape[1] / 2), int(cy - m.shape[0] / 2)


def draw(c, x, y, route, h=20, kind=None):
    """Draw a route shield with its number; returns the width."""
    kind = kind or kind_for(route)
    rgb, mask, spec, pal = _sprite(kind, h)
    sh, sw = mask.shape
    c.Blit(rgb, int(x), int(y), mask)
    text = _route_text(route)
    if spec.get("allow") is not None:
        key = (kind, h, text)
        if key not in _PLACED:
            _PLACED[key] = _place(spec["allow"], spec["centre"], text)
        m, ox, oy = _PLACED[key]
        c.BlitMask(m, int(x) + ox, int(y) + oy, Color(*pal[spec["number"]]))
        return sw
    bx0, by0, bx1, by1 = spec["box"]
    by = int(round(by0 * sh))
    bh = max(1, int(round(by1 * sh)) - by)
    if abs(bx0 + bx1 - 1.0) < 0.02:                       # a centred box: exactly symmetric in pixels
        bx = int(round(bx0 * sw))
        bw = max(1, sw - 2 * bx)
    else:
        bx = int(round(bx0 * sw))
        bw = max(1, int(round(bx1 * sw)) - bx)
    cut, hd, gl, tw = _number_layout(text, bw, bh)
    col = Color(*pal[spec["number"]])
    gap = 1 if hd < 12 or cut not in ("bold", "narrow") else 2
    nx = int(x) + bx + (bw - tw) // 2 + ((bw - tw) % 2 if sw % 2 == 0 else 0)
    ny = int(y) + by + (bh - hd) // 2
    for g in gl:
        c.BlitMask(g, nx, ny + (hd - g.shape[0]), col)     # glyphs share a baseline
        nx += g.shape[1] + gap
    return sw


def width(route, h=20, kind=None):
    return _sprite(kind or kind_for(route), h)[1].shape[1]
