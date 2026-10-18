"""Webcam captions: what the overlay says (sections) and how it's laid out (layouts), per webcam.

``overlay`` in a webcam's config (all optional; DEFAULT_OVERLAY fills the gaps):
    style       a layout from LAYOUTS (tag, bar, outline, dot, lower_third, split, postcard, chips, clock, signage, none)
    corner      tl | tr | bl | br   (bar-like layouts use only its top/bottom half)
    font        tiny | small | medium | large: the name's size (details use the small font)
    show        {name, place, time, date, offset, daynight, live, credit, custom}: which sections appear
    custom_text a line of your own ("Scramble Crossing", "Webcam by the harbour")
    time_format 24h | 12h
    accent, text, bg  "#rrggbb"; opacity 0-1: how solid the background boxes are
    when        always | intro (the first INTRO_SEC of a play) | periodic (show_sec every every_min minutes)
    fade        true: fade in and out instead of popping

Everything is drawn with approved bitmap fonts at their native size. Long content drops sections in DROP order
(credit first, place last) before any text is cut.
"""

from __future__ import annotations

import time
from datetime import datetime

import numpy as np

from display.gfx import Color, graphics

INTRO_SEC = 10.0
FADE_SEC = 0.6
FONTS = {"tiny": "fonts/alert/tom-thumb.bdf", "small": "fonts/alert/5x8.bdf", "medium": "fonts/alert/6x10.bdf",
         "large": "fonts/alert/7x13B.bdf"}
BIG = {"tiny": "fonts/alert/6x10.bdf", "small": "fonts/alert/7x13B.bdf", "medium": "fonts/alert/7x13B.bdf",
       "large": "fonts/alert/9x15B.bdf"}     # the clock in split / clock layouts
LAYOUTS = ("tag", "bar", "outline", "dot", "lower_third", "split", "postcard", "chips", "clock", "signage", "none")
STYLES = LAYOUTS                             # older name
SECTIONS = ("name", "place", "time", "date", "offset", "daynight", "live", "credit", "custom")
DROP = ("credit", "custom", "offset", "date", "place")      # dropped in this order when space runs out
DEFAULT_OVERLAY = {"style": "tag", "corner": "bl", "when": "always", "font": "small", "accent": "#e8202a",
                   "text": "#ffffff", "bg": "#000000", "opacity": 0.7, "time_format": "24h", "fade": True,
                   "show_sec": 12, "every_min": 3, "custom_text": "",
                   "show": {"name": True, "place": True, "time": True, "date": False, "offset": False,
                            "daynight": False, "live": True, "credit": False, "custom": False}}
WHITE, BLACK = Color(255, 255, 255), Color(0, 0, 0)

SUN = ["...#...", ".#...#.", "..###..", "#.###.#", "..###..", ".#...#.", "...#..."]
MOON = ["..###..", ".##....", "##.....", "##.....", "##.....", ".##....", "..###.."]


def overlay_of(cam: dict) -> dict:
    o = {**DEFAULT_OVERLAY, **(cam.get("overlay") or {})}
    o["show"] = {**DEFAULT_OVERLAY["show"], **((cam.get("overlay") or {}).get("show") or {})}
    return o


def _hex(s, default):
    try:
        s = str(s).lstrip("#")
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except (ValueError, IndexError):
        return default


def _local_time(tz):
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz)) if tz else datetime.now()
    except Exception:
        return datetime.now()


def _tw(font, s):
    return sum(max(0, font.CharacterWidth(ord(ch))) for ch in s)


def _shade(c, x, y, w, h, k):
    """Darken a box of the frame (keep k of it)."""
    x0, y0, x1, y1 = max(0, x), max(0, y), min(c.width, x + w), min(c.height, y + h)
    if x1 > x0 and y1 > y0:
        box = c.buf[y0:y1, x0:x1]
        box[:] = (box.astype(np.uint16) * int(k * 256) >> 8).astype(np.uint8)


def _box(c, x, y, w, h, rgb, opacity, round_=False):
    """A background box: the frame blended toward rgb by opacity; round_ trims the corner pixels."""
    x0, y0, x1, y1 = max(0, x), max(0, y), min(c.width, x + w), min(c.height, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    a = int(max(0.0, min(1.0, opacity)) * 256)
    box = c.buf[y0:y1, x0:x1].astype(np.uint16)
    col = np.array(rgb, np.uint16)
    out = ((box * (256 - a) + col * a) >> 8).astype(np.uint8)
    if round_ and w > 4 and h > 4:
        keep = c.buf[y0:y1, x0:x1].copy()
        for yy, xx in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
            out[yy, xx] = keep[yy, xx]
    c.buf[y0:y1, x0:x1] = out


def _outlined(c, font, x, y, color, s):
    for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)):
        graphics.DrawText(c, font, x + dx, y + dy, BLACK, s)
    return graphics.DrawText(c, font, x, y, color, s)


def _shadowed(c, font, x, y, color, s):
    graphics.DrawText(c, font, x + 1, y + 1, BLACK, s)
    return graphics.DrawText(c, font, x, y, color, s)


def _icon(c, x, y, rows, color):
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch == "#":
                c.SetPixel(x + i, y + j, *_rgb(color))


def _rgb(col):
    return (col.red, col.green, col.blue) if hasattr(col, "red") else tuple(col)


def _clip(font, s, width):
    while s and _tw(font, s) > width:
        s = s[:-1]
    return s.rstrip(" ,·-")


def _sep(font):
    return " · " if 0xB7 in getattr(font, "glyphs", {0xB7: 1}) else " - "


def fit_detail(font, bits, width, drop=("credit", "place")):
    """Join (kind, text) bits within width px, dropping kinds in `drop` order before cutting text."""
    sep = _sep(font)
    bits = list(bits)
    line = sep.join(v for _, v in bits)
    for kind in drop:
        if _tw(font, line) <= width:
            break
        bits = [b for b in bits if b[0] != kind] or bits
        line = sep.join(v for _, v in bits)
    return _clip(font, line, width)


# --- content ------------------------------------------------------------------------------------------------------
class Content:
    """The caption's sections for one moment, already formatted."""

    def __init__(self, cam, ov, now_local=None, here=None):
        sh = ov["show"]
        t = now_local or _local_time(cam.get("tz"))
        self.title = (cam.get("label") or "") if sh.get("name") else ""
        self.place = (cam.get("place") or "") if sh.get("place") else ""
        self.credit = (cam.get("credit") or "") if sh.get("credit") else ""
        self.custom = (ov.get("custom_text") or "").strip() if sh.get("custom") else ""
        self.live = bool(sh.get("live"))
        twelve = ov.get("time_format") == "12h"
        self.time = (t.strftime("%I:%M").lstrip("0") if twelve else t.strftime("%H:%M")) if sh.get("time") else ""
        self.ampm = t.strftime("%p") if (twelve and self.time) else ""
        self.date = f"{t:%a} {t.day} {t:%b}".upper() if sh.get("date") else ""
        self.offset = ""
        if sh.get("offset") and t.utcoffset() is not None:
            mine = (here or datetime.now().astimezone()).utcoffset()
            mins = int((t.utcoffset() - mine).total_seconds() // 60)
            if mins:
                h, m = divmod(abs(mins), 60)
                self.offset = f"{'+' if mins > 0 else '-'}{h}h" + (f"{m:02d}" if m else "")
        self.night = (t.hour < 6 or t.hour >= 19) if sh.get("daynight") else None

    def clock(self):
        return self.time + (" " + self.ampm if self.ampm else "")

    def bits(self, with_time=True, with_place=True):
        out = []
        if with_place and self.place:
            out.append(("place", self.place))
        if with_time and self.time:
            out.append(("time", self.clock()))
        for kind in ("date", "offset", "custom", "credit"):
            if getattr(self, kind):
                out.append((kind, getattr(self, kind)))
        return out


def caption_lines(cam, ov, now_local=None):
    """(title, detail bits) — kept for callers of the older API."""
    c = Content(cam, ov, now_local)
    return c.title, c.bits()


# --- visibility ---------------------------------------------------------------------------------------------------
def visibility(ov, elapsed):
    """0-1: how visible the caption is `elapsed` s into a play."""
    when = ov.get("when", "always")
    fade = ov.get("fade", True)
    if when == "always":
        return 1.0
    if when == "intro":
        start, length = 0.0, INTRO_SEC
    else:                                                   # periodic: the intro, then show_sec every every_min
        period = max(30.0, float(ov.get("every_min", 3)) * 60)
        length = max(3.0, float(ov.get("show_sec", 12)))
        start = (elapsed // period) * period if elapsed >= length else 0.0
    t = elapsed - start
    if t < 0 or t > length + (FADE_SEC if fade else 0):
        return 0.0
    if not fade:
        return 1.0 if t <= length else 0.0
    if start > 0 and t < FADE_SEC:
        return t / FADE_SEC
    if t > length:
        return max(0.0, 1 - (t - length) / FADE_SEC)
    return 1.0


# --- drawing ------------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, c, zone, ov, font_for):
        self.c, self.ov = c, ov
        self.x, self.y, self.w, self.h = zone
        size = ov.get("font", "small")
        self.T = font_for(FONTS.get(size, FONTS["small"]))            # the name
        self.S = font_for(FONTS["tiny"] if size == "tiny" else FONTS["small"])   # details
        self.B = font_for(BIG.get(size, BIG["small"]))                # big clock
        self.accent = Color(*_hex(ov.get("accent"), (232, 32, 42)))
        self.text = Color(*_hex(ov.get("text"), (255, 255, 255)))
        self.bg = _hex(ov.get("bg"), (0, 0, 0))
        self.op = float(ov.get("opacity", 0.7))
        corner = ov.get("corner", "bl")
        self.top, self.left = corner[0] == "t", corner[1] == "l"

    def badge(self, x, y, text="LIVE", font=None):
        """A LIVE badge in the accent colour; returns its width."""
        f = font or self.S
        w = _tw(f, text) + 4
        _box(self.c, x, y, w, f.height + 1, _rgb(self.accent), 1.0, round_=True)
        graphics.DrawText(self.c, f, x + 2, y + f.baseline, WHITE, text)
        return w

    def daynight(self, x, y, night):
        _icon(self.c, x, y, MOON if night else SUN, Color(200, 210, 255) if night else Color(255, 200, 40))
        return 8


def draw_caption(c, zone, cam, elapsed, font_for, now_local=None):
    """Draw the webcam's caption over the frame in zone. font_for(path or size) -> a loaded Font."""
    ov = overlay_of(cam)
    style = ov.get("style", "tag")
    if style not in LAYOUTS or style == "none":
        return
    a = visibility(ov, elapsed)
    if a <= 0:
        return
    x, y, w, h = zone
    before = c.buf[max(0, y):y + h, max(0, x):x + w].copy() if a < 1 else None
    content = Content(cam, ov, now_local)
    LAYOUT_FUNCS[style](Ctx(c, zone, ov, _font_loader(font_for)), content)
    if before is not None:                                  # fading: blend the caption in
        region = c.buf[max(0, y):y + h, max(0, x):x + w]
        k = int(a * 256)
        region[:] = ((before.astype(np.uint16) * (256 - k) + region.astype(np.uint16) * k) >> 8).astype(np.uint8)


def _font_loader(font_for):
    """Accept the older font_for(size) callables as well as font_for(path)."""
    def get(path_or_size):
        try:
            return font_for(path_or_size)
        except Exception:
            size = next((k for k, v in FONTS.items() if v == path_or_size), "small")
            return font_for(size)
    return get


# --- layouts ------------------------------------------------------------------------------------------------------
def _l_tag(k, ct, outline=False):
    """A box in a corner: LIVE + name, details underneath; an accent stripe on the inside edge."""
    c, T, S, pad = k.c, k.T, k.S, 2
    bw = _tw(S, "LIVE") + 4 if ct.live else 0
    room = k.w - 2 * pad - 4 - (bw + 3 if bw else 0)
    title = _clip(T, ct.title, room)
    detail = fit_detail(S, ct.bits(), room, DROP)
    if not (title or detail or bw):
        return
    tw = max(_tw(T, title), _tw(S, detail))
    box_w = min(k.w, pad * 2 + (bw + 3 if bw else 0) + tw + (1 if not outline else 0))
    rows = (T.height if title else 0) + (1 if title and detail else 0) + (S.height if detail else 0)
    box_h = pad * 2 + max(rows, S.height + 1 if bw else 0)
    bx = k.x + 1 if k.left else k.x + k.w - box_w - 1
    by = k.y + 1 if k.top else k.y + k.h - box_h - 1
    put = _outlined if outline else graphics.DrawText
    if not outline:
        _box(c, bx, by, box_w, box_h, k.bg, k.op)
        c.FillRect(bx if k.left else bx + box_w - 1, by, 1, box_h, k.accent)
    cx, ty = bx + pad + (1 if not outline and k.left else 0), by + pad
    if bw:
        k.badge(cx, ty)
        cx += bw + 3
    if title:
        put(c, T, cx, ty + T.baseline, k.text, title)
        ty += T.height + 1
    if detail:
        put(c, S, cx, ty + S.baseline, k.text, detail)


def _l_outline(k, ct):
    _l_tag(k, ct, outline=True)


def _l_bar(k, ct):
    """A full-width strip along the top or bottom: LIVE, the name over the details, the time at the right."""
    c, T, S, pad = k.c, k.T, k.S, 2
    rows = (T.height if ct.title else 0) + (1 if ct.title else 0) + S.height
    bh = pad * 2 + rows
    by = k.y if k.top else k.y + k.h - bh
    _box(c, k.x, by, k.w, bh, k.bg, k.op)
    c.FillRect(k.x, by + bh - 1 if k.top else by, k.w, 1, k.accent)
    cx = k.x + pad
    if ct.live:
        cx += k.badge(cx, by + pad) + 3
    right = k.x + k.w - pad
    if ct.time:
        tt = ct.clock()
        right -= _tw(T, tt)
        graphics.DrawText(c, T, right, by + pad + T.baseline, k.text, tt)
        right -= 6
    room = right - cx
    ty = by + pad
    if ct.title:
        graphics.DrawText(c, T, cx, ty + T.baseline, k.text, _clip(T, ct.title, room))
        ty += T.height + 1
    detail = fit_detail(S, ct.bits(with_time=False), room, DROP)
    if detail:
        graphics.DrawText(c, S, cx, ty + S.baseline, k.text, detail)


def _l_dot(k, ct):
    """Just a blinking LIVE dot (and the time) in a corner."""
    c, S = k.c, k.S
    cx = k.x + 4 if k.left else k.x + k.w - 5
    cy = k.y + 4 if k.top else k.y + k.h - 5
    if int(time.time() * 1.25) % 2 == 0:
        c.FillRect(cx - 1, cy - 2, 3, 5, k.accent)
        c.FillRect(cx - 2, cy - 1, 5, 3, k.accent)
    label = " ".join(s for s in ("LIVE" if ct.live else "", ct.clock()) if s)
    if label:
        lw = _tw(S, label)
        lx = cx + 5 if k.left else cx - 5 - lw
        _outlined(c, S, lx, cy - S.height // 2 + S.baseline, k.text, label)


def _l_lower_third(k, ct):
    """TV news: the name on an accent block, a darker strip of details tucked under it, LIVE on the block's end."""
    c, T, S = k.c, k.T, k.S
    padx = 4
    bw = _tw(S, "LIVE") + 4 if ct.live else 0
    room = k.w - 2 * padx - 6 - (bw + 4 if bw else 0)
    title = _clip(T, ct.title or ct.place, room)
    detail = fit_detail(S, ct.bits(with_place=bool(ct.title)), k.w - 2 * padx - 6, DROP)
    th = T.height + 4 if title else 0
    dh = S.height + 3 if detail else 0
    if not (th or dh):
        return
    tw = (_tw(T, title) + 2 * padx + (bw + 4 if bw else 0)) if title else 0
    dw = _tw(S, detail) + 2 * padx if detail else 0
    total = th + dh
    by = k.y + 3 if k.top else k.y + k.h - total - 3
    bx = k.x + 3 if k.left else k.x + k.w - max(tw, dw) - 3
    if title:
        tx = bx if k.left else bx + max(tw, dw) - tw
        _box(c, tx, by, tw, th, _rgb(k.accent), min(1.0, k.op + 0.2))
        graphics.DrawText(c, T, tx + padx, by + 2 + T.baseline, k.text, title)
        if bw:
            bx2 = tx + tw - padx - bw
            _box(c, bx2, by + (th - S.height - 1) // 2, bw, S.height + 1, (255, 255, 255), 1.0, round_=True)
            graphics.DrawText(c, S, bx2 + 2, by + (th - S.height - 1) // 2 + S.baseline, k.accent, "LIVE")
    if detail:
        dx = bx + (3 if k.left else 0) if title else bx
        dx = dx if k.left else bx + max(tw, dw) - dw - (3 if title else 0)
        _box(c, dx, by + th, dw, dh, k.bg, k.op)
        graphics.DrawText(c, S, dx + padx, by + th + 1 + S.baseline, k.text, detail)


def _l_split(k, ct):
    """A full-width strip in two sections: name and details on the left, a big local clock on the right."""
    c, T, S, B = k.c, k.T, k.S, k.B
    pad = 2
    left_h = (T.height if ct.title else 0) + (1 if ct.title else 0) + S.height
    bh = pad * 2 + max(left_h, B.height if ct.time else 0)
    by = k.y if k.top else k.y + k.h - bh
    _box(c, k.x, by, k.w, bh, k.bg, k.op)
    c.FillRect(k.x, by + bh - 1 if k.top else by, k.w, 1, k.accent)
    right = k.x + k.w - pad - 1
    if ct.time:                                             # the clock section
        if ct.ampm:
            right -= _tw(S, ct.ampm)
            graphics.DrawText(c, S, right, by + pad + S.baseline, k.text, ct.ampm)
            right -= 1
        right -= _tw(B, ct.time)
        graphics.DrawText(c, B, right, by + (bh - B.height) // 2 + B.baseline, k.text, ct.time)
        stack = [s for s in (ct.date, ct.offset) if s]      # date / offset stacked to its left
        if stack:
            sw = max(_tw(S, s) for s in stack)
            right -= sw + 4
            for i, s in enumerate(stack[:2]):
                graphics.DrawText(c, S, right + sw - _tw(S, s), by + pad + i * (S.height + 1) + S.baseline,
                                  k.text, s)
        if ct.night is not None:
            right -= 9
            k.daynight(right, by + (bh - 7) // 2, ct.night)
        right -= 4
        c.FillRect(right, by + 2, 1, bh - 4, k.accent)      # the section divider
        right -= 4
    cx = k.x + pad + 1
    ty = by + pad
    if ct.live:
        cx += k.badge(cx, ty) + 3
    room = right - cx
    if ct.title:
        graphics.DrawText(c, T, cx, ty + T.baseline, k.text, _clip(T, ct.title, room))
        ty += T.height + 1
    bits = [b for b in ct.bits(with_time=False) if b[0] not in ("date", "offset")] if ct.time else ct.bits()
    detail = fit_detail(S, bits, right - (k.x + pad + 1), DROP)
    if detail:
        graphics.DrawText(c, S, k.x + pad + 1, ty + S.baseline, k.text, detail)


def _l_postcard(k, ct):
    """A travel-postcard title: the name centred in big type with a shadow, the details under it."""
    c, S = k.c, k.S
    T = k.B if k.ov.get("font") in ("medium", "large") else k.T
    title = _clip(T, ct.title, k.w - 8)
    detail = fit_detail(S, ct.bits(), k.w - 8, DROP)
    block = (T.height + 2 if title else 0) + (S.height if detail else 0)
    ty = k.y + 4 if k.top else k.y + k.h - block - 5
    if title:
        _shadowed(c, T, k.x + (k.w - _tw(T, title)) // 2, ty + T.baseline, k.text, title)
        ty += T.height + 2
    if detail:
        _outlined(c, S, k.x + (k.w - _tw(S, detail)) // 2, ty + S.baseline, k.text, detail)
    if ct.live:                                             # LIVE in the opposite top corner
        lw = _tw(S, "LIVE") + 4
        k.badge(k.x + k.w - lw - 2 if k.left else k.x + 2, k.y + 2 if not k.top else k.y + k.h - S.height - 3)


def _l_chips(k, ct):
    """Separate rounded chips in a row (wrapping), starting in the corner: LIVE, name, place, time…"""
    c, T, S = k.c, k.T, k.S
    chips = []
    if ct.live:
        chips.append(("live", "LIVE", S))
    if ct.title:
        chips.append(("name", ct.title, T))
    for kind, text in ct.bits():
        chips.append((kind, text, S))
    if ct.night is not None and ct.time:
        chips = [(kd, ("  " + tx) if kd == "time" else tx, f) for kd, tx, f in chips]
    rows, row, roww = [], [], 0
    maxw = k.w - 4
    for kind, text, f in chips:
        cw = min(maxw, _tw(f, text) + 6)
        if row and roww + cw + 2 > maxw:
            rows.append(row)
            row, roww = [], 0
        row.append((kind, _clip(f, text, cw - 6), f, cw))
        roww += cw + 2
    if row:
        rows.append(row)
    rows = rows[:3]
    heights = [max(f.height for _, _, f, _ in r) + 3 for r in rows]
    total = sum(heights) + 2 * (len(rows) - 1)
    yy = k.y + 2 if k.top else k.y + k.h - total - 2
    for r, rh in zip(rows, heights):
        width = sum(cw for *_, cw in r) + 2 * (len(r) - 1)
        xx = k.x + 2 if k.left else k.x + k.w - width - 2
        for kind, text, f, cw in r:
            live = kind == "live"
            _box(c, xx, yy, cw, rh, _rgb(k.accent) if live else k.bg, 1.0 if live else k.op, round_=True)
            graphics.DrawText(c, f, xx + 3, yy + (rh - f.height) // 2 + f.baseline, WHITE if live else k.text, text)
            if kind == "time" and ct.night is not None:
                k.daynight(xx + 2, yy + (rh - 7) // 2, ct.night)
            xx += cw + 2
        yy += rh + 2


def _l_clock(k, ct):
    """A world clock: the local time big, the place (or name) under it, date and offset below; LIVE dot."""
    c, S, B = k.c, k.S, k.B
    pad = 3
    head = ct.place or ct.title
    lines = [s for s in (fit_detail(S, [(k_, v) for k_, v in (("date", ct.date), ("offset", ct.offset)) if v],
                                    k.w - 10, DROP),) if s]
    big = ct.time or "--:--"
    icon = 9 if ct.night is not None else 0
    bw_ = icon + _tw(B, big) + (_tw(S, ct.ampm) + 1 if ct.ampm else 0)
    head = _clip(S, head, k.w - 2 * pad - 2)
    box_w = min(k.w - 2, max(bw_, _tw(S, head), *(_tw(S, s) for s in lines)) + 2 * pad + 6)
    box_h = pad * 2 + B.height + (S.height + 1 if head else 0) + len(lines) * (S.height + 1)
    bx = k.x + 2 if k.left else k.x + k.w - box_w - 2
    by = k.y + 2 if k.top else k.y + k.h - box_h - 2
    _box(c, bx, by, box_w, box_h, k.bg, k.op, round_=True)
    cx, ty = bx + pad, by + pad
    if icon:
        k.daynight(cx, ty + (B.height - 7) // 2, ct.night)
        cx += icon
    graphics.DrawText(c, B, cx, ty + B.baseline, k.text, big)
    if ct.ampm:
        graphics.DrawText(c, S, cx + _tw(B, big) + 1, ty + S.baseline, k.text, ct.ampm)
    if ct.live and int(time.time() * 1.25) % 2 == 0:
        c.FillRect(bx + box_w - 5, by + 3, 2, 2, k.accent)
    ty += B.height + 1
    for s in ([head] if head else []) + lines:
        graphics.DrawText(c, S, bx + pad, ty + S.baseline, k.text, s)
        ty += S.height + 1


def _l_signage(k, ct):
    """A departure board: a dark box with a thin frame, lines in the accent colour, values right-aligned."""
    c, S = k.c, k.S
    T = k.T if k.ov.get("font") != "large" else k.T
    pad = 3
    rows = []
    name = (ct.title or ct.place).upper()
    rows.append((T, name, ct.clock()))
    second = (ct.place.upper() if ct.title else "")
    right2 = " ".join(s for s in (ct.date, ct.offset) if s)
    if second or right2 or ct.live:
        rows.append((S, second or ("LIVE" if ct.live else ""), right2))
    box_w = min(k.w - 2, max(_tw(f, l) + _tw(f, r) + 10 for f, l, r in rows) + 2 * pad)
    box_h = pad * 2 + sum(f.height + 1 for f, _, _ in rows) - 1
    bx = k.x + 2 if k.left else k.x + k.w - box_w - 2
    by = k.y + 2 if k.top else k.y + k.h - box_h - 2
    _box(c, bx, by, box_w, box_h, k.bg, max(k.op, 0.85))
    dim = Color(*(v // 3 for v in _rgb(k.accent)))
    c.FillRect(bx, by, box_w, 1, dim)
    c.FillRect(bx, by + box_h - 1, box_w, 1, dim)
    ty = by + pad
    for f, l, r in rows:
        rw = _tw(f, r)
        graphics.DrawText(c, f, bx + pad, ty + f.baseline, k.accent, _clip(f, l, box_w - 2 * pad - rw - 6))
        if r:
            graphics.DrawText(c, f, bx + box_w - pad - rw, ty + f.baseline, k.accent, r)
        ty += f.height + 1


LAYOUT_FUNCS = {"tag": _l_tag, "outline": _l_outline, "bar": _l_bar, "dot": _l_dot, "lower_third": _l_lower_third,
                "split": _l_split, "postcard": _l_postcard, "chips": _l_chips, "clock": _l_clock,
                "signage": _l_signage}
