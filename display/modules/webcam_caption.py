"""Webcam captions: what the overlay says (sections) and how it's laid out (layouts), per webcam.

``overlay`` in a webcam's config (all optional; DEFAULT_OVERLAY fills the gaps):
    style       a layout from LAYOUTS
    corner      where it sits: tl tc tr / bl bc br (top or bottom; left, centre or right)
    font        tiny | small | medium | large: the headline's size (details use the small font)
    headline    name | place | custom | time: which section is the big line (the others become details)
    show        {name, place, time, date, offset, daynight, weather, sun, live, credit, custom}
    order       the detail sections in the order they're written (missing ones follow in ORDER)
    lines       auto (one detail line) | wrap (up to two) | stacked (one per section) | inline (beside the headline)
    separator   dot | slash | pipe | dash | space
    uppercase   true: everything in capitals
    custom_text a line of your own
    time_format 24h | 12h
    accent, text, bg  "#rrggbb"; opacity 0-1: how solid the background boxes are
    when        always | intro (the first INTRO_SEC of a play) | periodic (show_sec every every_min minutes)
    fade        true: fade in and out instead of popping

Weather and sunrise/sunset come from data/webcam_weather.json (hub/jobs/webcam_weather.py, for webcams with a lat/lon).
Everything is drawn with approved bitmap fonts at their native size. Long content drops sections in DROP order before
any text is cut.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np

from display.gfx import Color, graphics

INTRO_SEC = 10.0
FADE_SEC = 0.6
FONTS = {"tiny": "fonts/alert/tom-thumb.bdf", "small": "fonts/alert/5x8.bdf", "medium": "fonts/alert/6x10.bdf",
         "large": "fonts/alert/7x13B.bdf"}
BIG = {"tiny": "fonts/alert/6x10.bdf", "small": "fonts/alert/7x13B.bdf", "medium": "fonts/alert/7x13B.bdf",
       "large": "fonts/alert/9x15B.bdf"}     # clocks and the postcard title
LAYOUTS = ("tag", "bar", "outline", "dot", "lower_third", "split", "postcard", "chips", "clock", "signage", "ticker",
           "stack", "none")
STYLES = LAYOUTS                             # older name
SECTIONS = ("name", "place", "time", "date", "offset", "daynight", "weather", "sun", "live", "credit", "custom")
ORDER = ("name", "place", "time", "weather", "date", "offset", "sun", "custom", "credit")    # default detail order
DROP = ("credit", "custom", "sun", "offset", "date", "weather", "place", "name")    # dropped first → last
SEPARATORS = {"dot": " · ", "slash": " / ", "pipe": " | ", "dash": " - ", "space": "   "}
DEFAULT_OVERLAY = {"style": "tag", "corner": "bl", "when": "always", "font": "small", "accent": "#e8202a",
                   "text": "#ffffff", "bg": "#000000", "opacity": 0.7, "time_format": "24h", "fade": True,
                   "show_sec": 12, "every_min": 3, "custom_text": "", "headline": "name", "lines": "auto",
                   "separator": "dot", "uppercase": False, "order": list(ORDER),
                   "show": {"name": True, "place": True, "time": True, "date": False, "offset": False,
                            "daynight": False, "weather": False, "sun": False, "live": True, "credit": False,
                            "custom": False}}
WHITE, BLACK = Color(255, 255, 255), Color(0, 0, 0)
AMBER = (255, 170, 0)

# 7×7 pixel icons; letters pick the colour
PALETTE = {"y": (255, 200, 40), "w": (225, 230, 240), "g": (150, 160, 175), "b": (90, 160, 255), "c": (200, 235, 255),
           "m": (200, 210, 255)}
ICONS = {
    "sun": ["...y...", ".y...y.", "..yyy..", "y.yyy.y", "..yyy..", ".y...y.", "...y..."],
    "moon": ["..mmm..", ".mm....", "mm.....", "mm.....", "mm.....", ".mm....", "..mmm.."],
    "partly": ["y.y....", ".yyy...", "yyww...", ".wwwww.", "wwwwwww", ".wwwww.", "......."],
    "partly_night": ["m......", "mm.....", "mmww...", ".wwwww.", "wwwwwww", ".wwwww.", "......."],
    "cloud": [".......", "..ww...", ".wwwww.", "wwwwwww", "wwwwwww", ".wwwww.", "......."],
    "fog": ["gggggg.", ".......", ".gggggg", ".......", "gggggg.", ".......", ".gggggg"],
    "rain": [".ggg...", "ggggg..", "ggggggg", ".......", "b.b.b..", ".b.b.b.", "b.b.b.."],
    "snow": [".ggg...", "ggggg..", "ggggggg", ".......", "c.c.c..", ".......", ".c.c.c."],
    "storm": [".ggg...", "ggggg..", "ggggggg", "...y...", "..yy...", "...y...", "..y...."],
}


def weather_icon(code, is_day=True):
    """WMO weather code → icon name."""
    if code is None:
        return None
    code = int(code)
    if code in (0, 1):
        return "sun" if is_day else "moon"
    if code == 2:
        return "partly" if is_day else "partly_night"
    if code == 3:
        return "cloud"
    if code in (45, 48):
        return "fog"
    if code in (71, 73, 75, 77, 85, 86):
        return "snow"
    if code >= 95:
        return "storm"
    return "rain"


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
    out = ((box * (256 - a) + np.array(rgb, np.uint16) * a) >> 8).astype(np.uint8)
    if round_ and w > 4 and h > 4:
        keep = c.buf[y0:y1, x0:x1]
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


def _rgb(col):
    return (col.red, col.green, col.blue) if hasattr(col, "red") else tuple(col)


def draw_icon(c, x, y, name):
    rows = ICONS.get(name)
    if not rows:
        return 0
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch in PALETTE:
                c.SetPixel(x + i, y + j, *PALETTE[ch])
    return 8


def _clip(font, s, width):
    while s and _tw(font, s) > width:
        s = s[:-1]
    return s.rstrip(" ,·-/|")


def _sep(font, name="dot"):
    s = SEPARATORS.get(name, " · ")
    return s if "·" not in s or 0xB7 in getattr(font, "glyphs", {0xB7: 1}) else " - "


def fit_detail(font, bits, width, drop=("credit", "place"), sep="dot"):
    """Join (kind, text) bits within width px, dropping kinds in `drop` order before cutting text."""
    sp = _sep(font, sep)
    bits = list(bits)
    line = sp.join(v for _, v in bits)
    for kind in drop:
        if _tw(font, line) <= width:
            break
        bits = [b for b in bits if b[0] != kind] or bits
        line = sp.join(v for _, v in bits)
    return _clip(font, line, width)


def detail_lines(font, bits, width, mode="auto", sep="dot", max_lines=4):
    """The details as lines for the `lines` setting: auto → 1, wrap → up to 2, stacked → one per section."""
    if not bits:
        return []
    if mode == "stacked":
        return [_clip(font, v, width) for _, v in bits[:max_lines]]
    if mode == "wrap":
        sp = _sep(font, sep)
        lines, cur = [], []
        for b in bits:
            trial = sp.join(v for _, v in cur + [b])
            if cur and _tw(font, trial) > width:
                lines.append(cur)
                cur = [b]
                if len(lines) == 2:
                    break
            else:
                cur.append(b)
        if cur and len(lines) < 2:
            lines.append(cur)
        return [fit_detail(font, ln, width, DROP, sep) for ln in lines[:2]]
    line = fit_detail(font, bits, width, DROP, sep)
    return [line] if line else []


# --- content ------------------------------------------------------------------------------------------------------
class Content:
    """The caption's sections for one moment, already formatted (wx: this webcam's weather, if any)."""

    def __init__(self, cam, ov, now_local=None, here=None, wx=None):
        sh = ov["show"]
        up = (lambda s: s.upper()) if ov.get("uppercase") else (lambda s: s)
        t = now_local or _local_time(cam.get("tz"))
        self.tz = t.tzinfo
        twelve = ov.get("time_format") == "12h"
        sec = {}
        sec["name"] = (cam.get("label") or "") if sh.get("name") else ""
        sec["place"] = (cam.get("place") or "") if sh.get("place") else ""
        sec["credit"] = (cam.get("credit") or "") if sh.get("credit") else ""
        sec["custom"] = (ov.get("custom_text") or "").strip() if sh.get("custom") else ""
        self.ampm = up(t.strftime("%p")) if twelve else ""
        sec["time"] = ((t.strftime("%I:%M").lstrip("0") if twelve else t.strftime("%H:%M")) if sh.get("time") else "")
        sec["date"] = f"{t:%a} {t.day} {t:%b}".upper() if sh.get("date") else ""
        sec["offset"] = ""
        if sh.get("offset") and t.utcoffset() is not None:
            mine = (here or datetime.now().astimezone()).utcoffset()
            mins = int((t.utcoffset() - mine).total_seconds() // 60)
            if mins:
                h, m = divmod(abs(mins), 60)
                sec["offset"] = f"{'+' if mins > 0 else '-'}{h}h" + (f"{m:02d}" if m else "")
        wx = wx or {}
        self.icon = None
        sec["weather"] = ""
        if sh.get("weather") and wx.get("temp") is not None:
            sec["weather"] = f"{round(wx['temp'])}°C"
            self.icon = weather_icon(wx.get("code"), bool(wx.get("is_day", 1)))
        sec["sun"] = self._sun(wx, t) if sh.get("sun") else ""
        self.night = None
        if sh.get("daynight"):
            self.night = (not wx["is_day"]) if "is_day" in wx else (t.hour < 6 or t.hour >= 19)
        self.live = bool(sh.get("live"))
        head = ov.get("headline", "name")
        if head not in ("name", "place", "custom", "time") or not sec.get(head):
            head = "name" if sec["name"] else ("place" if sec["place"] else "name")
        self.headline_kind = head
        self.title = up(sec[head] + (" " + self.ampm if head == "time" and self.ampm else ""))
        order = [k for k in (ov.get("order") or []) if k in ORDER] + [k for k in ORDER if k not in (ov.get("order") or [])]
        self.sections = {k: up(v) for k, v in sec.items()}
        self.order = [k for k in order if k != head]
        self.time = sec["time"]
        self.place = self.sections["place"]
        self.date, self.offset = self.sections["date"], self.sections["offset"]

    def _sun(self, wx, t):
        events = sorted([("sunrise", s) for s in wx.get("sunrise", [])] + [("sunset", s) for s in wx.get("sunset", [])],
                        key=lambda e: e[1])
        now = time.time()
        for name, ts in events:
            if ts > now:
                at = datetime.fromtimestamp(ts, timezone.utc).astimezone(t.tzinfo)
                return f"{name} {at:%H:%M}"
        return ""

    def clock(self):
        return self.time + (" " + self.ampm if self.ampm else "")

    def bits(self, with_time=True, with_place=True):
        """The detail sections, in the chosen order (not the headline)."""
        out = []
        for k in self.order:
            if (k == "time" and not with_time) or (k == "place" and not with_place):
                continue
            v = self.clock() if k == "time" and self.time else self.sections.get(k)
            if v:
                out.append((k, v))
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
        self.size = size
        self.T = font_for(FONTS.get(size, FONTS["small"]))            # the headline
        self.S = font_for(FONTS["tiny"] if size == "tiny" else FONTS["small"])   # details
        self.B = font_for(BIG.get(size, BIG["small"]))                # big clock / postcard
        self.accent = Color(*_hex(ov.get("accent"), (232, 32, 42)))
        self.text = Color(*_hex(ov.get("text"), (255, 255, 255)))
        self.bg = _hex(ov.get("bg"), (0, 0, 0))
        self.op = float(ov.get("opacity", 0.7))
        self.sep = ov.get("separator", "dot")
        self.lines = ov.get("lines", "auto")
        corner = ov.get("corner", "bl")
        self.top = corner[0] == "t"
        self.align = {"l": "left", "c": "center", "r": "right"}.get(corner[1:2], "left")
        self.left = self.align == "left"

    def place(self, bw, bh, margin=1):
        """Top-left of a bw×bh box in this caption's position."""
        bx = {"left": self.x + margin, "center": self.x + (self.w - bw) // 2,
              "right": self.x + self.w - bw - margin}[self.align]
        by = self.y + margin if self.top else self.y + self.h - bh - margin
        return bx, by

    def badge(self, x, y, text="LIVE", font=None, inverse=False):
        f = font or self.S
        w = _tw(f, text) + 4
        _box(self.c, x, y, w, f.height + 1, (255, 255, 255) if inverse else _rgb(self.accent), 1.0, round_=True)
        graphics.DrawText(self.c, f, x + 2, y + f.baseline, self.accent if inverse else WHITE, text)
        return w

    def details(self, ct, width, with_time=True, with_place=True):
        return detail_lines(self.S, ct.bits(with_time, with_place), width, self.lines, self.sep)

    def text_with_icon(self, f, x, y_top, ct, kind, text, color=None, put=None):
        """Draw a detail line; a weather icon sits in front of the weather section when it starts the line."""
        put = put or graphics.DrawText
        color = color or self.text
        if kind == "weather" and ct.icon:
            draw_icon(self.c, x, y_top + (f.height - 7) // 2, ct.icon)
            x += 9
        return put(self.c, f, x, y_top + f.baseline, color, text)


def draw_caption(c, zone, cam, elapsed, font_for, now_local=None, wx=None):
    """Draw the webcam's caption over the frame in zone. font_for(path or size) -> a loaded Font."""
    ov = overlay_of(cam)
    style = ov.get("style", "tag")
    if style not in LAYOUT_FUNCS:
        return
    a = visibility(ov, elapsed)
    if a <= 0:
        return
    x, y, w, h = zone
    before = c.buf[max(0, y):y + h, max(0, x):x + w].copy() if a < 1 else None
    content = Content(cam, ov, now_local, wx=wx)
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
def _headline_and_lines(k, ct, room, T=None):
    """(headline text, detail lines) honouring lines=inline (details on the headline row)."""
    T = T or k.T
    if k.lines == "inline":                                # whole sections drop before anything is cut
        sp = _sep(T, k.sep)
        title = _clip(T, ct.title, room)
        rest = fit_detail(T, ct.bits(), room - _tw(T, title + sp), DROP, k.sep) if ct.bits() else ""
        return (title + sp + rest) if rest and room - _tw(T, title + sp) > 20 else title, []
    return _clip(T, ct.title, room), k.details(ct, room)


def _l_tag(k, ct, outline=False):
    """A box: LIVE + headline, details underneath; an accent stripe on the inside edge."""
    c, T, S, pad = k.c, k.T, k.S, 2
    bw = _tw(S, "LIVE") + 4 if ct.live else 0
    room = k.w - 2 * pad - 6
    title, lines = _headline_and_lines(k, ct, room - (bw + 3 if bw else 0))
    if not (title or lines or bw):
        return
    tw = max([(bw + 3 if bw else 0) + _tw(T, title)] + [_tw(S, ln) + (9 if ct.icon else 0) for ln in lines])
    box_w = min(k.w, pad * 2 + tw + (1 if not outline else 0))
    head_h = max(T.height if title else 0, S.height + 1 if bw else 0)
    box_h = pad * 2 + head_h + sum(S.height + 1 for _ in lines)
    bx, by = k.place(box_w, box_h)
    put = _outlined if outline else graphics.DrawText
    if not outline:
        _box(c, bx, by, box_w, box_h, k.bg, k.op)
        c.FillRect(bx if k.align != "right" else bx + box_w - 1, by, 1, box_h, k.accent)
    x0 = bx + pad + (1 if not outline and k.align != "right" else 0)
    cx, ty = x0, by + pad
    if bw:
        k.badge(cx, ty)
        cx += bw + 3
    if title:
        put(c, T, cx, ty + T.baseline, k.text, title)
    ty += head_h + 1
    kinds = [b[0] for b in ct.bits()]
    for i, ln in enumerate(lines):
        first = kinds[i] if k.lines == "stacked" and i < len(kinds) else (kinds[0] if kinds and i == 0 else None)
        k.text_with_icon(S, x0, ty, ct, first, ln, put=put)
        ty += S.height + 1


def _l_outline(k, ct):
    _l_tag(k, ct, outline=True)


def _l_bar(k, ct):
    """A full-width strip along the top or bottom: LIVE, headline over details, the time at the right."""
    c, T, S, pad = k.c, k.T, k.S, 2
    right_time = ct.time and ct.headline_kind != "time"
    room_guess = k.w - 2 * pad - 40
    title, lines = _headline_and_lines(k, ct, room_guess)
    lines = lines[:2]
    head_h = max(T.height if title else 0, S.height + 1 if ct.live else 0)
    bh = pad * 2 + head_h + sum(S.height + 1 for _ in lines)
    by = k.y if k.top else k.y + k.h - bh
    _box(c, k.x, by, k.w, bh, k.bg, k.op)
    c.FillRect(k.x, by + bh - 1 if k.top else by, k.w, 1, k.accent)
    cx = k.x + pad
    if ct.live:
        cx += k.badge(cx, by + pad) + 3
    right = k.x + k.w - pad
    if right_time:
        tt = ct.clock()
        right -= _tw(T, tt)
        graphics.DrawText(c, T, right, by + pad + T.baseline, k.text, tt)
        right -= 6
    if title:
        graphics.DrawText(c, T, cx, by + pad + T.baseline, k.text, _clip(T, title, right - cx))
    ty = by + pad + head_h + 1
    for ln in k.details(ct, right - (k.x + pad), with_time=not right_time)[:2] if k.lines != "inline" else []:
        k.text_with_icon(S, k.x + pad, ty, ct, None, ln)
        ty += S.height + 1


def _l_dot(k, ct):
    """Just a blinking LIVE dot (and the time) in a corner."""
    c, S = k.c, k.S
    label = " ".join(s for s in ("LIVE" if ct.live else "", ct.clock()) if s)
    lw = _tw(S, label) + 7 if label else 5
    bx, by = k.place(lw + 4, 9, margin=1)
    cx, cy = bx + 3, by + 4
    if int(time.time() * 1.25) % 2 == 0:
        c.FillRect(cx - 1, cy - 2, 3, 5, k.accent)
        c.FillRect(cx - 2, cy - 1, 5, 3, k.accent)
    if label:
        _outlined(c, S, cx + 5, cy - S.height // 2 + S.baseline, k.text, label)


def _l_lower_third(k, ct):
    """TV news: the headline on an accent block, a darker strip of details tucked under it, LIVE on the block's end."""
    c, T, S = k.c, k.T, k.S
    padx = 4
    bw = _tw(S, "LIVE") + 4 if ct.live else 0
    title = _clip(T, ct.title, k.w - 2 * padx - 6 - (bw + 4 if bw else 0))
    lines = k.details(ct, k.w - 2 * padx - 6)[:3] if k.lines != "inline" else []
    th = T.height + 4 if title else 0
    dh = (S.height + 1) * len(lines) + 2 if lines else 0
    if not (th or dh):
        return
    tw = (_tw(T, title) + 2 * padx + (bw + 4 if bw else 0)) if title else 0
    dw = max([_tw(S, ln) + 2 * padx + (9 if ct.icon else 0) for ln in lines], default=0)
    W = max(tw, dw + (3 if title else 0))
    bx, by = k.place(W, th + dh, margin=3)
    tx = bx if k.align != "right" else bx + W - tw
    if title:
        _box(c, tx, by, tw, th, _rgb(k.accent), min(1.0, k.op + 0.2))
        graphics.DrawText(c, T, tx + padx, by + 2 + T.baseline, k.text, title)
        if bw:
            k.badge(tx + tw - padx - bw, by + (th - S.height - 1) // 2, inverse=True)
    if lines:
        dx = (bx + (3 if title else 0)) if k.align != "right" else bx + W - dw - (3 if title else 0)
        _box(c, dx, by + th, dw, dh, k.bg, k.op)
        ty = by + th + 1
        kinds = [b[0] for b in ct.bits()]
        for i, ln in enumerate(lines):
            first = kinds[i] if k.lines == "stacked" and i < len(kinds) else (kinds[0] if kinds and i == 0 else None)
            k.text_with_icon(S, dx + padx, ty, ct, first, ln)
            ty += S.height + 1


def _l_split(k, ct):
    """A full-width strip in two sections: headline and details on the left, a big local clock on the right."""
    c, T, S, B = k.c, k.T, k.S, k.B
    pad = 2
    show_clock = bool(ct.time) and ct.headline_kind != "time"
    lines = [] if k.lines == "inline" else k.details(ct, k.w // 2, with_time=not show_clock)
    lines = [ln for ln in lines][:2]
    left_h = (T.height + 1 if ct.title else 0) + sum(S.height + 1 for _ in lines)
    bh = pad * 2 + max(left_h, B.height if show_clock else 0, S.height + 1)
    by = k.y if k.top else k.y + k.h - bh
    _box(c, k.x, by, k.w, bh, k.bg, k.op)
    c.FillRect(k.x, by + bh - 1 if k.top else by, k.w, 1, k.accent)
    right = k.x + k.w - pad - 1
    if show_clock:
        if ct.ampm:
            right -= _tw(S, ct.ampm)
            graphics.DrawText(c, S, right, by + pad + S.baseline, k.text, ct.ampm)
            right -= 1
        right -= _tw(B, ct.time)
        graphics.DrawText(c, B, right, by + (bh - B.height) // 2 + B.baseline, k.text, ct.time)
        icon = ct.icon if ct.icon and ct.sections.get("weather") else ("moon" if ct.night else "sun") if ct.night is not None else None
        stack = [s for s in (ct.sections["weather"], ct.date, ct.offset) if s][:2]
        if stack:
            sw = max(_tw(S, s) for s in stack)
            right -= sw + 4
            for i, s in enumerate(stack):
                graphics.DrawText(c, S, right + sw - _tw(S, s), by + pad + i * (S.height + 1) + S.baseline, k.text, s)
        if icon:
            right -= 9
            draw_icon(c, right, by + (bh - 7) // 2, icon)
        right -= 4
        c.FillRect(right, by + 2, 1, bh - 4, k.accent)
        right -= 4
    cx, ty = k.x + pad + 1, by + pad
    if ct.live:
        cx += k.badge(cx, ty) + 3
    if ct.title:
        graphics.DrawText(c, T, cx, ty + T.baseline, k.text, _clip(T, ct.title, right - cx))
        ty += max(T.height, S.height + 1) + 1
    used = {"weather", "date", "offset"} if show_clock else set()
    bits = [b for b in ct.bits(with_time=not show_clock) if b[0] not in used]
    for ln in detail_lines(S, bits, right - (k.x + pad + 1), k.lines if k.lines != "inline" else "auto", k.sep)[:2]:
        graphics.DrawText(c, S, k.x + pad + 1, ty + S.baseline, k.text, ln)
        ty += S.height + 1


def _l_postcard(k, ct):
    """A travel-postcard title: the headline centred in big type with a shadow, the details under it."""
    c, S = k.c, k.S
    T = k.T if k.size == "tiny" else k.B
    title = _clip(T, ct.title, k.w - 8)
    lines = k.details(ct, k.w - 8)[:3] if k.lines != "inline" else []
    block = (T.height + 2 if title else 0) + sum(S.height + 1 for _ in lines)
    ty = k.y + 4 if k.top else k.y + k.h - block - 5
    if title:
        _shadowed(c, T, k.x + (k.w - _tw(T, title)) // 2, ty + T.baseline, k.text, title)
        ty += T.height + 2
    for ln in lines:
        _outlined(c, S, k.x + (k.w - _tw(S, ln)) // 2, ty + S.baseline, k.text, ln)
        ty += S.height + 1
    if ct.live:                                             # LIVE in a top corner away from the text
        lw = _tw(S, "LIVE") + 4
        k.badge(k.x + k.w - lw - 2, k.y + 2 if not k.top else k.y + k.h - S.height - 3)


def _l_chips(k, ct):
    """Separate rounded chips in a row (wrapping), starting in the corner: LIVE, headline, then each section."""
    c, T, S = k.c, k.T, k.S
    chips = []
    if ct.live:
        chips.append(("live", "LIVE", S))
    if ct.title:
        chips.append(("name", ct.title, T))
    for kind, text in ct.bits():
        chips.append((kind, text, S))
    rows, row, roww = [], [], 0
    maxw = k.w - 4
    for kind, text, f in chips:
        extra = 9 if (kind == "weather" and ct.icon) or (kind == "time" and ct.night is not None) else 0
        cw = min(maxw, _tw(f, text) + 6 + extra)
        if row and roww + cw + 2 > maxw:
            rows.append(row)
            row, roww = [], 0
        row.append((kind, _clip(f, text, cw - 6 - extra), f, cw, extra))
        roww += cw + 2
    if row:
        rows.append(row)
    rows = rows[:3]
    heights = [max(f.height for _, _, f, _, _ in r) + 3 for r in rows]
    total = sum(heights) + 2 * (len(rows) - 1)
    _, yy = k.place(10, total, margin=2)
    for r, rh in zip(rows, heights):
        width = sum(cw for _, _, _, cw, _ in r) + 2 * (len(r) - 1)
        xx, _ = k.place(width, rh, margin=2)
        for kind, text, f, cw, extra in r:
            live = kind == "live"
            _box(c, xx, yy, cw, rh, _rgb(k.accent) if live else k.bg, 1.0 if live else k.op, round_=True)
            if extra:
                draw_icon(c, xx + 3, yy + (rh - 7) // 2,
                          ct.icon if kind == "weather" else ("moon" if ct.night else "sun"))
            graphics.DrawText(c, f, xx + 3 + extra, yy + (rh - f.height) // 2 + f.baseline, WHITE if live else k.text, text)
            xx += cw + 2
        yy += rh + 2


def _l_clock(k, ct):
    """A world clock: the local time big, the place (or headline) under it, then the other details; LIVE dot."""
    c, S, B = k.c, k.S, k.B
    pad = 3
    big = ct.time or "--:--"
    icon = (ct.icon if ct.icon else ("moon" if ct.night else "sun") if ct.night is not None else None)
    head = ct.place if ct.headline_kind == "name" and ct.place else ct.title
    head = _clip(S, head, k.w - 2 * pad - 2)
    bits = [b for b in ct.bits(with_time=False) if not (b[0] == "place" and head == ct.place)]
    lines = detail_lines(S, bits, k.w - 2 * pad - 6, k.lines if k.lines != "inline" else "auto", k.sep)[:3]
    bw_ = (9 if icon else 0) + _tw(B, big) + (_tw(S, ct.ampm) + 1 if ct.ampm else 0)
    box_w = min(k.w - 2, max([bw_, _tw(S, head)] + [_tw(S, s) for s in lines]) + 2 * pad + 6)
    box_h = pad * 2 + B.height + (S.height + 1 if head else 0) + len(lines) * (S.height + 1)
    bx, by = k.place(box_w, box_h, margin=2)
    _box(c, bx, by, box_w, box_h, k.bg, k.op, round_=True)
    cx, ty = bx + pad, by + pad
    if icon:
        draw_icon(c, cx, ty + (B.height - 7) // 2, icon)
        cx += 9
    graphics.DrawText(c, B, cx, ty + B.baseline, k.text, big)
    if ct.ampm:
        graphics.DrawText(c, S, cx + _tw(B, big) + 1, ty + S.baseline, k.text, ct.ampm)
    if ct.live:                                             # a LIVE dot, pulsing
        on = int(time.time() * 1.25) % 2 == 0
        c.FillRect(bx + box_w - 6, by + 3, 3, 3, k.accent if on else Color(*(v // 3 for v in _rgb(k.accent))))
    ty += B.height + 1
    for s in ([head] if head else []) + lines:
        graphics.DrawText(c, S, bx + pad, ty + S.baseline, k.text, s)
        ty += S.height + 1


def _l_signage(k, ct):
    """A departure board: a dark box with a thin frame, lines in amber (or the accent), values right-aligned."""
    c, T, S = k.c, k.T, k.S
    col = Color(*AMBER) if _rgb(k.accent) == _hex(DEFAULT_OVERLAY["accent"], None) else k.accent
    pad = 3
    rows = [(T, ct.title.upper(), ct.clock().upper() if ct.headline_kind != "time" else "")]
    bits = [b for b in ct.bits(with_time=False)]
    left = [v.upper() for kd, v in bits if kd in ("place", "custom", "credit")]
    right = [v.upper() for kd, v in bits if kd not in ("place", "custom", "credit")]
    for i in range(max(len(left), len(right) and 1, 1 if ct.live else 0)):
        l = left[i] if i < len(left) else ("LIVE" if i == 0 and ct.live and not left else "")
        r = " ".join(right) if i == 0 else ""
        if l or r:
            rows.append((S, l, r))
    rows = rows[:4]
    box_w = min(k.w - 2, max(_tw(f, l) + _tw(f, r) + 10 for f, l, r in rows) + 2 * pad)
    box_h = pad * 2 + sum(f.height + 1 for f, _, _ in rows) - 1
    bx, by = k.place(box_w, box_h, margin=2)
    _box(c, bx, by, box_w, box_h, k.bg, max(k.op, 0.85))
    dim = Color(*(v // 3 for v in _rgb(col)))
    c.FillRect(bx, by, box_w, 1, dim)
    c.FillRect(bx, by + box_h - 1, box_w, 1, dim)
    ty = by + pad
    for f, l, r in rows:
        rw = _tw(f, r)
        graphics.DrawText(c, f, bx + pad, ty + f.baseline, col, _clip(f, l, box_w - 2 * pad - rw - 6))
        if r:
            graphics.DrawText(c, f, bx + box_w - pad - rw, ty + f.baseline, col, r)
        ty += f.height + 1


def _l_ticker(k, ct):
    """A news ticker: LIVE + headline in an accent tab at one end, the details scrolling along a strip."""
    c, T, S = k.c, k.T, k.S
    pad = 2
    h = max(T.height, S.height + 1) + 2 * pad
    by = k.y if k.top else k.y + k.h - h
    tab_text = ct.title
    tab_w = min(k.w // 2, _tw(T, tab_text) + 2 * pad + (_tw(S, "LIVE") + 7 if ct.live else 0)) if tab_text or ct.live else 0
    _box(c, k.x, by, k.w, h, k.bg, k.op)
    sp = _sep(S, k.sep)
    line = sp.join(v for _, v in ct.bits())
    if line:
        line += sp
        lw = _tw(S, line)
        speed = 22.0                                        # px/s
        off = int(time.time() * speed) % max(1, lw)
        x = k.x + tab_w + 2 - off
        while x < k.x + k.w:
            graphics.DrawText(c, S, x, by + (h - S.height) // 2 + S.baseline, k.text, line)
            x += lw
    if tab_w:
        _box(c, k.x, by, tab_w, h, _rgb(k.accent), 1.0)
        cx = k.x + pad
        if ct.live:
            cx += k.badge(cx, by + (h - S.height - 1) // 2, inverse=True) + 3
        graphics.DrawText(c, T, cx, by + (h - T.height) // 2 + T.baseline, WHITE, _clip(T, tab_text, tab_w - (cx - k.x) - pad))


def _l_stack(k, ct):
    """Every section on its own line, headline on top, outlined over the picture (no box)."""
    c, T, S = k.c, k.T, k.S
    lines = [(T, ct.title, None)] if ct.title else []
    lines += [(S, v, kd) for kd, v in ct.bits()][:5]
    if ct.live:
        lines.insert(0, (S, "LIVE", "live"))
    total = sum(f.height + 1 for f, _, _ in lines)
    widths = [(_tw(f, t) + (9 if kd == "weather" and ct.icon else 0)) for f, t, kd in lines]
    bx, by = k.place(max(widths, default=10), total, margin=3)
    ty = by
    for (f, t, kd), w_ in zip(lines, widths):
        x = {"left": bx, "center": k.x + (k.w - w_) // 2, "right": bx + max(widths) - w_}[k.align]
        if kd == "live":
            k.badge(x, ty)
        else:
            k.text_with_icon(f, x, ty, ct, kd, _clip(f, t, k.w - 8), put=_outlined)
        ty += f.height + 1


LAYOUT_FUNCS = {"tag": _l_tag, "outline": _l_outline, "bar": _l_bar, "dot": _l_dot, "lower_third": _l_lower_third,
                "split": _l_split, "postcard": _l_postcard, "chips": _l_chips, "clock": _l_clock,
                "signage": _l_signage, "ticker": _l_ticker, "stack": _l_stack}
