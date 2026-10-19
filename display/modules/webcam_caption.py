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
    taken_format time (default: "taken 17:05:42") | relative ("3 min ago") | both — the "age" section of a still
    custom layout only:
    shield      {route: "99", kind: auto | bc | tch | yellowhead | crowsnest | interstate | us | wa,
                 place: inside | left | right | line, size: box | 12-64}: a highway shield (display/modules/shields.py)
                shown with show.shield. inside: in front of its column's text, the box growing to fit it; left /
                right: beside the box, outside it (taller than the box, it lines up with the box's outer edge);
                line: a piece of its line, the line's height. size: the box's height, or a height in px
    slots       {section: [column 1-3, line 1-4, "right"?]}; sections sharing a line follow `order`; "right" puts
                the section at the line's right end (e.g. the time in the box's bottom-right corner)
    line_sizes  {"column.line": headline | detail | big}
    columns     [{align: left | center | right}, …]; divider: true draws a line between columns
    container   box | strip | outline | board
    fade        true: fade in and out instead of popping

Weather and sunrise/sunset come from data/webcam_weather.json (hub/jobs/webcam_weather.py, for webcams with a lat/lon).
Everything is drawn with approved bitmap fonts at their native size. Long content drops sections in DROP order before
any text is cut.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

from display.gfx import Color, graphics

INTRO_SEC = 10.0
FADE_SEC = 0.6
FONTS = {"tiny": "fonts/alert/tom-thumb.bdf", "small": "fonts/alert/5x8.bdf", "medium": "fonts/alert/6x10.bdf",
         "large": "fonts/alert/7x13B.bdf"}
BIG = {"tiny": "fonts/alert/6x10.bdf", "small": "fonts/alert/7x13B.bdf", "medium": "fonts/alert/7x13B.bdf",
       "large": "fonts/alert/9x15B.bdf"}     # clocks and the postcard title
LAYOUTS = ("tag", "bar", "outline", "dot", "lower_third", "split", "postcard", "chips", "clock", "signage", "ticker",
           "stack", "custom", "none")
STYLES = LAYOUTS                             # older name
SECTIONS = ("name", "place", "time", "date", "offset", "daynight", "weather", "sun", "age", "live", "credit", "custom",
            "shield")
ORDER = ("name", "place", "time", "age", "weather", "date", "offset", "sun", "custom", "credit")    # default order
DROP = ("credit", "custom", "sun", "offset", "date", "weather", "age", "place", "shield", "name")    # dropped first → last
SEPARATORS = {"dot": " · ", "slash": " / ", "pipe": " | ", "dash": " - ", "space": "   "}
DEFAULT_OVERLAY = {"style": "tag", "corner": "bl", "when": "always", "font": "small", "accent": "#e8202a",
                   "text": "#ffffff", "bg": "#000000", "opacity": 0.7, "time_format": "24h", "fade": True,
                   "show_sec": 12, "every_min": 3, "custom_text": "", "headline": "name", "lines": "auto",
                   "separator": "dot", "uppercase": False, "order": list(ORDER),
                   "show": {"name": True, "place": True, "time": True, "date": False, "offset": False,
                            "daynight": False, "weather": False, "sun": False, "age": False, "live": True,
                            "credit": False, "custom": False, "shield": False}}
WHITE, BLACK = Color(255, 255, 255), Color(0, 0, 0)
WX_MAX_AGE = 2 * 3600       # weather older than this isn't shown
FIT_LINES = 3               # "fit" wraps onto up to this many lines before dropping sections
_rec = threading.local()    # .dropped: a set while dropped_sections() records (per thread: the hub renders in many)
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


@lru_cache(maxsize=4096)
def _tw(font, s):
    """Text width in px (memoised: layouts measure the same strings every frame)."""
    return sum(max(0, font.CharacterWidth(ord(ch))) for ch in s)


def _shade(c, x, y, w, h, k):
    """Darken a box of the frame (keep k of it)."""
    x0, y0, x1, y1 = max(0, x), max(0, y), min(c.width, x + w), min(c.height, y + h)
    if x1 > x0 and y1 > y0:
        box = c.buf[y0:y1, x0:x1]
        box[:] = (box.astype(np.uint16) * int(k * 256) >> 8).astype(np.uint8)


def _box(c, x, y, w, h, rgb, opacity, round_=False):
    """A background box: the frame blended toward rgb by opacity; round_ trims the corner pixels. Clipped to the
    caption's zone when drawing through _Zone."""
    x0, y0 = max(x, getattr(c, "x0", 0), 0), max(y, getattr(c, "y0", 0), 0)
    x1, y1 = min(x + w, getattr(c, "x1", c.width), c.width), min(y + h, getattr(c, "y1", c.height), c.height)
    if x1 <= x0 or y1 <= y0:
        return
    if hasattr(c, "mark"):
        c.mark(x0, y0, x1, y1)
    a = int(max(0.0, min(1.0, opacity)) * 256)
    buf = c.buf
    box = buf[y0:y1, x0:x1].astype(np.uint16)
    out = ((box * (256 - a) + np.array(rgb, np.uint16) * a) >> 8).astype(np.uint8)
    if round_ and w > 4 and h > 4 and (x0, y0, x1, y1) == (x, y, x + w, y + h):
        keep = buf[y0:y1, x0:x1]
        for yy, xx in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
            out[yy, xx] = keep[yy, xx]
    buf[y0:y1, x0:x1] = out


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


@lru_cache(maxsize=4096)
def _clip(font, s, width):
    """s cut to fit width px (memoised; a binary search over the prefix widths)."""
    if _tw(font, s) <= width:
        return s
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _tw(font, s[:mid]) <= width:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo].rstrip(" ,·-/|")


def _sep(font, name="dot"):
    s = SEPARATORS.get(name, " · ")
    return s if "·" not in s or 0xB7 in getattr(font, "glyphs", {0xB7: 1}) else " - "


def fit_detail(font, bits, width, drop=("credit", "place"), sep="dot"):
    """Join (kind, text) bits within width px, dropping kinds in `drop` order before cutting text."""
    return _fit(font, tuple(tuple(b) for b in bits), width, tuple(drop), sep)[1]


@lru_cache(maxsize=2048)
def _fit(font, bits, width, drop, sep, icon_kind=None):
    """(the bits kept, the line). A line that starts with icon_kind has 9 px less room (its icon goes in front)."""
    sp = _sep(font, sep)
    kept = list(bits)

    def room(ks):
        return width - (9 if icon_kind and ks and ks[0][0] == icon_kind else 0)

    line = sp.join(v for _, v in kept)
    for kind in drop:
        if _tw(font, line) <= room(kept):
            break
        kept = [b for b in kept if b[0] != kind] or kept
        line = sp.join(v for _, v in kept)
    return tuple(kept), _clip(font, line, max(0, room(kept)))


def _note_dropped(bits, kept_kinds):
    rec = getattr(_rec, "dropped", None)
    if rec is not None:
        rec.update(k for k, _ in bits if k not in kept_kinds)


def detail_lines(font, bits, width, mode="auto", sep="dot", max_lines=FIT_LINES, icon_kind=None):
    """The details as (first section's kind, text) lines for the `lines` setting:
    auto / wrap → one line if everything fits, else wrapped onto up to max_lines (sections drop only after that);
    stacked → one per section. Lines starting with icon_kind (the weather with its icon) leave room for it."""
    bits = tuple(tuple(b) for b in bits)
    if not bits:
        return []
    if mode == "stacked":
        _note_dropped(bits, {k for k, _ in bits[:max_lines]})
        return [(kd, _clip(font, v, width - (9 if kd == icon_kind else 0))) for kd, v in bits[:max_lines]]
    kept, text = _fit(font, bits, width, DROP, sep, icon_kind)
    if len(kept) == len(bits) or max_lines <= 1:          # it all fits on one line (or one line is all there is)
        _note_dropped(bits, {k for k, _ in kept})
        return [(kept[0][0] if kept else None, text)] if text else []
    sp = _sep(font, sep)
    lines, cur = [], []
    for b in bits:                                         # wrap: fill each line, then start the next
        trial = sp.join(v for _, v in cur + [b])
        room = width - (9 if icon_kind and (cur or [b])[0][0] == icon_kind else 0)
        if cur and _tw(font, trial) > room:
            lines.append(cur)
            cur = [b]
        else:
            cur.append(b)
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:                             # still too much: the lowest-priority sections go
        keep = list(bits)
        for kind in DROP:
            if len(_wrap_count(font, keep, width, sp, icon_kind)) <= max_lines:
                break
            keep = [b for b in keep if b[0] != kind] or keep
        lines = _wrap_count(font, keep, width, sp, icon_kind)[:max_lines]
    out, shown = [], set()
    for ln in lines:
        k2, t2 = _fit(font, tuple(ln), width, DROP, sep, icon_kind)
        if t2:
            out.append((k2[0][0] if k2 else None, t2))
            shown.update(k for k, _ in k2)
    _note_dropped(bits, shown)
    return out


def _wrap_count(font, bits, width, sp, icon_kind):
    lines, cur = [], []
    for b in bits:
        trial = sp.join(v for _, v in cur + [b])
        room = width - (9 if icon_kind and (cur or [b])[0][0] == icon_kind else 0)
        if cur and _tw(font, trial) > room:
            lines.append(cur)
            cur = [b]
        else:
            cur.append(b)
    if cur:
        lines.append(cur)
    return lines


# --- content ------------------------------------------------------------------------------------------------------
class Content:
    """The caption's sections for one moment, already formatted (wx: this webcam's weather, if any)."""

    def __init__(self, cam, ov, now_local=None, here=None, wx=None, extra=None):
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
        shield = ov.get("shield") if isinstance(ov.get("shield"), dict) else {}
        sec["shield"] = str(shield.get("route") or "").strip()[:8] if sh.get("shield") else ""
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
        if wx and time.time() - float(wx.get("at") or 0) > WX_MAX_AGE:
            wx = {}                                         # the weather job hasn't run for hours: don't show old data
        self.icon = None
        sec["weather"] = ""
        if sh.get("weather") and wx.get("temp") is not None:
            sec["weather"] = f"{round(wx['temp'])}°C"
            self.icon = weather_icon(wx.get("code"), bool(wx.get("is_day", 1)))
        sec["sun"] = self._sun(wx, t) if sh.get("sun") else ""
        taken = (extra or {}).get("taken")                  # when a still image was taken (epoch), from the hub
        sec["age"] = taken_text(taken, t.tzinfo, ov.get("taken_format"), twelve) if sh.get("age") and taken else ""
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


def taken_text(taken, tz, fmt="time", twelve=False):
    """A still's capture time: "taken 17:05:42" (exact, in the camera's time zone), "3 min ago", or both."""
    at = datetime.fromtimestamp(taken, timezone.utc).astimezone(tz) if tz else datetime.fromtimestamp(taken)
    exact = "taken " + (at.strftime("%I:%M:%S %p").lstrip("0") if twelve else at.strftime("%H:%M:%S"))
    rel = age_text(time.time() - taken)
    return rel if fmt == "relative" else f"{exact} · {rel}" if fmt == "both" else exact


def age_text(sec):
    """How old a still image is: 'just now', '4 min ago', '1 h 20 min ago'."""
    sec = max(0, int(sec))
    if sec < 90:
        return "just now"
    m = sec // 60
    return f"{m} min ago" if m < 60 else f"{m // 60} h {m % 60} min ago" if m % 60 else f"{m // 60} h ago"


def caption_lines(cam, ov, now_local=None):
    """(title, detail bits) — kept for callers of the older API."""
    c = Content(cam, ov, now_local)
    return c.title, c.bits()


# --- visibility ---------------------------------------------------------------------------------------------------
def _num(v, default, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def visibility(ov, elapsed):
    """0-1: how visible the caption is `elapsed` s into a play."""
    when = ov.get("when", "always")
    fade = bool(ov.get("fade", True))
    if when not in ("intro", "periodic"):
        return 1.0
    if when == "intro":
        start, length = 0.0, INTRO_SEC
    else:                                                   # periodic: the intro, then show_sec every every_min
        period = _num(ov.get("every_min"), 3, 0.5, 120) * 60
        length = _num(ov.get("show_sec"), 12, 3, 600)
        if length + FADE_SEC >= period:                     # shown longer than the gap: just stay up
            return 1.0
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
        size = ov.get("font") if ov.get("font") in FONTS else "small"
        self.size = size
        self.T = font_for(FONTS.get(size, FONTS["small"]))            # the headline
        self.S = font_for(FONTS["tiny"] if size == "tiny" else FONTS["small"])   # details
        self.B = font_for(BIG.get(size, BIG["small"]))                # big clock / postcard
        self.accent = Color(*_hex(ov.get("accent"), (232, 32, 42)))
        self.text = Color(*_hex(ov.get("text"), (255, 255, 255)))
        self.bg = _hex(ov.get("bg"), (0, 0, 0))
        self.op = _num(ov.get("opacity"), 0.7, 0.0, 1.0)
        self.sep = ov.get("separator") if ov.get("separator") in SEPARATORS else "dot"
        self.lines = ov.get("lines") if ov.get("lines") in ("auto", "wrap", "stacked", "inline") else "auto"
        corner = ov.get("corner") if ov.get("corner") in ("tl", "tc", "tr", "bl", "bc", "br") else "bl"
        self.top = corner[0] == "t"
        self.align = {"l": "left", "c": "center", "r": "right"}[corner[1]]
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

    def details(self, ct, width, with_time=True, with_place=True, max_lines=None):
        """[(first kind, text)] detail lines for this caption's line mode (stacked: up to 4, fit: up to FIT_LINES)."""
        n = max_lines or (4 if self.lines == "stacked" else FIT_LINES)
        return detail_lines(self.S, ct.bits(with_time, with_place), width, self.lines, self.sep, max_lines=n,
                            icon_kind="weather" if ct.icon else None)

    def text_with_icon(self, f, x, y_top, ct, kind, text, color=None, put=None):
        """Draw a detail line; the weather icon goes in front when the line starts with the weather."""
        put = put or graphics.DrawText
        color = color or self.text
        if kind == "weather" and ct.icon:
            draw_icon(self.c, x, y_top + (f.height - 7) // 2, ct.icon)
            x += 9
        return put(self.c, f, x, y_top + f.baseline, color, text)

class _Zone:
    """The canvas as layouts see it: every draw is clipped to the caption's zone (a scrolling ticker can't spill into
    the other half of the wall) and the area drawn is recorded (a fade blends only that box)."""

    def __init__(self, c, zone):
        self.c, self.buf, self.width, self.height = c, c.buf, c.width, c.height
        self.x0, self.y0 = max(0, zone[0]), max(0, zone[1])
        self.x1, self.y1 = min(c.width, zone[0] + zone[2]), min(c.height, zone[1] + zone[3])
        self.bbox = None

    def mark(self, x0, y0, x1, y1):
        b = self.bbox
        self.bbox = (x0, y0, x1, y1) if b is None else (min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1))

    def BlitMask(self, mask, x, y, color):
        h, w = mask.shape
        sx0, sy0 = max(0, self.x0 - x), max(0, self.y0 - y)
        ex, ey = min(w, self.x1 - x), min(h, self.y1 - y)
        if ex <= sx0 or ey <= sy0:
            return
        self.c.BlitMask(mask[sy0:ey, sx0:ex], x + sx0, y + sy0, color)
        self.mark(x + sx0, y + sy0, x + ex, y + ey)

    def FillRect(self, x, y, w, h, color):
        x0, y0, x1, y1 = max(x, self.x0), max(y, self.y0), min(x + w, self.x1), min(y + h, self.y1)
        if x1 > x0 and y1 > y0:
            self.c.FillRect(x0, y0, x1 - x0, y1 - y0, color)
            self.mark(x0, y0, x1, y1)

    def Blit(self, arr, x, y, mask=None):
        h, w = arr.shape[:2]
        sx0, sy0 = max(0, self.x0 - x), max(0, self.y0 - y)
        ex, ey = min(w, self.x1 - x), min(h, self.y1 - y)
        if ex <= sx0 or ey <= sy0:
            return
        self.c.Blit(arr[sy0:ey, sx0:ex], x + sx0, y + sy0, None if mask is None else mask[sy0:ey, sx0:ex])
        self.mark(x + sx0, y + sy0, x + ex, y + ey)

    def SetPixel(self, x, y, r, g, b):
        if self.x0 <= x < self.x1 and self.y0 <= y < self.y1:
            self.c.SetPixel(x, y, r, g, b)
            self.mark(x, y, x + 1, y + 1)


def draw_caption(c, zone, cam, elapsed, font_for, now_local=None, wx=None, extra=None):
    """Draw the webcam's caption over the frame in zone. font_for(path or size) -> a loaded Font.

    The caption is rendered once into a transparent layer (cached until its text, settings or blink phase change,
    about once a minute) and each frame only blends that layer over the video in the caption's box. The ticker,
    which scrolls, draws directly."""
    ov = overlay_of(cam)
    style = ov.get("style", "tag")
    if style not in LAYOUT_FUNCS:
        return
    a = visibility(ov, elapsed)
    if a <= 0:
        return
    x, y, w, h = zone
    if style != "ticker" and now_local is None:              # the same layer for 0.4 s: skip rebuilding the key
        mk = (id(cam), id(font_for), w, h, int(time.time() * 2.5), (wx or {}).get("at"), round((extra or {}).get("taken") or 0))
        parts = _memo.get(mk)
        if parts is None:
            if len(_memo) > 32:
                _memo.clear()
            parts = _memo[mk] = _layer(style, ov, Content(cam, ov, None, wx=wx, extra=extra), w, h, font_for) or ()
        _composite(c, x, y, parts, a)
        return
    content = Content(cam, ov, now_local, wx=wx, extra=extra)
    if style == "ticker":
        zc = _Zone(c, zone)
        before = c.buf[zc.y0:zc.y1, zc.x0:zc.x1].copy() if a < 1 else None
        LAYOUT_FUNCS[style](Ctx(zc, zone, ov, _font_loader(font_for)), content)
        if before is not None and zc.bbox:
            _blend_back(c, zc, before, a)
        return
    _composite(c, x, y, _layer(style, ov, content, w, h, font_for) or (), a)


_memo: dict = {}


def _composite(c, x, y, parts, a):
    """Blend the cached layer parts over the frame at (x, y); a < 1 while fading."""
    for (bx0, by0, bx1, by1), alpha, prem in parts:
        X0, Y0, X1, Y1 = max(0, x + bx0), max(0, y + by0), min(c.width, x + bx1), min(c.height, y + by1)
        if X1 <= X0 or Y1 <= Y0:
            continue
        sl = (slice(Y0 - y - by0, Y1 - y - by0), slice(X0 - x - bx0, X1 - x - bx0))
        al, pm = alpha[sl], prem[sl]
        region = c.buf[Y0:Y1, X0:X1]
        if a >= 1:
            region[:] = ((region.astype(np.uint16) * (255 - al) + pm) // 255).astype(np.uint8)
        else:                                               # fading: the layer at a fraction of its strength
            k = int(a * 256)
            al2 = (al.astype(np.uint32) * k) >> 8
            region[:] = ((region.astype(np.uint32) * (255 - al2) + ((pm.astype(np.uint32) * k) >> 8)) // 255
                         ).astype(np.uint8)


def _blend_back(c, zc, before, a):
    x0, y0, x1, y1 = zc.bbox
    region = c.buf[y0:y1, x0:x1]
    old = before[y0 - zc.y0:y1 - zc.y0, x0 - zc.x0:x1 - zc.x0]
    k = int(a * 256)
    region[:] = ((old.astype(np.uint16) * (256 - k) + region.astype(np.uint16) * k) >> 8).astype(np.uint8)


def dropped_sections(cam, zone_size, font_for, wx=None, extra=None):
    """The sections turned on that this caption can't show at this size (the panel lists them)."""
    from display.gfx import Canvas
    ov = overlay_of(cam)
    style = ov.get("style", "tag")
    if style not in LAYOUT_FUNCS:
        return []
    w, h = zone_size
    _rec.dropped = found = set()
    try:
        ct = Content(cam, ov, wx=wx, extra=extra)
        LAYOUT_FUNCS[style](Ctx(_Zone(Canvas(w, h), (0, 0, w, h)), (0, 0, w, h), ov, _font_loader(font_for)), ct)
    finally:
        _rec.dropped = None
    return sorted(found)


_layers: dict = {}           # key -> (bbox, alpha, premultiplied colour); a few per webcam
BLINKING = ("dot", "clock")


def _layer(style, ov, ct, w, h, font_for):
    """The caption drawn onto black and onto white: the difference gives each pixel's opacity, the black one its
    premultiplied colour. Returns parts [(bbox, alpha (h,w,1) uint16, premult*255 (h,w,3) uint16)], one per run of
    rows that has something in it (a badge in the far corner doesn't make the whole zone a blend), or None."""
    import json
    from display.gfx import Canvas
    blink = int(time.time() * 1.25) % 2 if style in BLINKING else 0
    key = (style, json.dumps(ov, sort_keys=True, default=str), ct.title, tuple(ct.bits()), ct.icon, ct.night, ct.live,
           ct.ampm, ct.clock(), w, h, blink, id(font_for))
    hit = _layers.get(key)
    if hit is not None:
        return hit
    fl = _font_loader(font_for)
    planes = []
    bbox = None
    for bgv in (0, 255):
        cv = Canvas(w, h)
        cv.buf[:] = bgv
        zc = _Zone(cv, (0, 0, w, h))
        LAYOUT_FUNCS[style](Ctx(zc, (0, 0, w, h), ov, fl), ct)
        planes.append(cv.buf.astype(np.int32))
        if zc.bbox:
            bbox = zc.bbox if bbox is None else (min(bbox[0], zc.bbox[0]), min(bbox[1], zc.bbox[1]),
                                                 max(bbox[2], zc.bbox[2]), max(bbox[3], zc.bbox[3]))
    if bbox is None:
        out = None
    else:
        x0, y0, x1, y1 = bbox
        A, B = planes[0][y0:y1, x0:x1], planes[1][y0:y1, x0:x1]
        alpha = (255 - (B - A).max(axis=2, keepdims=True)).clip(0, 255)        # B - A = 255 × (1 − opacity)
        rows = np.flatnonzero(alpha[..., 0].any(axis=1))
        out = []
        if rows.size:                                       # split into runs of rows with gaps of 3+ empty rows
            breaks = np.flatnonzero(np.diff(rows) > 3)
            for r0, r1 in zip(np.r_[rows[0], rows[breaks + 1]], np.r_[rows[breaks], rows[-1]] + 1):
                cols = np.flatnonzero(alpha[r0:r1, :, 0].any(axis=0))
                c0, c1 = cols[0], cols[-1] + 1
                out.append(((x0 + c0, y0 + r0, x0 + c1, y0 + r1), alpha[r0:r1, c0:c1].astype(np.uint16),
                            (A[r0:r1, c0:c1] * 255).clip(0, 65025).astype(np.uint16)))
        out = out or None
    if len(_layers) > 64:
        _layers.clear()
    _layers[key] = out
    return out


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
    """(headline text, [(kind, text)] detail lines), honouring lines=inline (details on the headline row)."""
    T = T or k.T
    if k.lines == "inline":                                # whole sections drop before anything is cut
        sp = _sep(T, k.sep)
        title = _clip(T, ct.title, room)
        bits = tuple(ct.bits())
        if bits and room - _tw(T, title + sp) > 20:
            kept, rest = _fit(T, bits, room - _tw(T, title + sp), DROP, k.sep)
            _note_dropped(bits, {kd for kd, _ in kept} if rest else set())
            return (title + sp + rest) if rest else title, []
        _note_dropped(bits, set())
        return title, []
    return _clip(T, ct.title, room), k.details(ct, room)


def _l_tag(k, ct, outline=False):
    """A box: LIVE + headline, details underneath; an accent stripe on the inside edge."""
    c, T, S, pad = k.c, k.T, k.S, 2
    bw = _tw(S, "LIVE") + 4 if ct.live else 0
    room = k.w - 2 * pad - 6
    title, lines = _headline_and_lines(k, ct, room - (bw + 3 if bw else 0))
    if not (title or lines or bw):
        return
    tw = max([(bw + 3 if bw else 0) + _tw(T, title)] +
             [_tw(S, ln) + (9 if kd == "weather" and ct.icon else 0) for kd, ln in lines])
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
    for kd, ln in lines:
        k.text_with_icon(S, x0, ty, ct, kd, ln, put=put)
        ty += S.height + 1


def _l_outline(k, ct):
    _l_tag(k, ct, outline=True)


def _l_bar(k, ct):
    """A full-width strip along the top or bottom: LIVE, headline over details, the time at the right."""
    c, T, S, pad = k.c, k.T, k.S, 2
    right_time = ct.time and ct.headline_kind != "time"
    room_guess = k.w - 2 * pad - 40
    title, lines = _headline_and_lines(k, ct, room_guess)
    lines = lines[:2]                                      # (height only; the drawn lines come from details() below)
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
    for kd, ln in k.details(ct, right - (k.x + pad), with_time=not right_time, max_lines=2) if k.lines != "inline" else []:
        k.text_with_icon(S, k.x + pad, ty, ct, kd, ln)
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
    dw = max([_tw(S, ln) + 2 * padx + (9 if kd == "weather" and ct.icon else 0) for kd, ln in lines], default=0)
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
        for kd, ln in lines:
            k.text_with_icon(S, dx + padx, ty, ct, kd, ln)
            ty += S.height + 1


def _l_split(k, ct):
    """A full-width strip in two sections: headline and details on the left, a big local clock on the right."""
    c, T, S, B = k.c, k.T, k.S, k.B
    pad = 2
    show_clock = bool(ct.time) and ct.headline_kind != "time"
    lines = [] if k.lines == "inline" else k.details(ct, k.w // 2, with_time=not show_clock, max_lines=2)
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
    for kd, ln in detail_lines(S, bits, right - (k.x + pad + 1), k.lines if k.lines != "inline" else "auto", k.sep,
                               max_lines=2):
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
    for kd, ln in lines:
        w_ = _tw(S, ln) + (9 if kd == "weather" and ct.icon else 0)
        k.text_with_icon(S, k.x + (k.w - w_) // 2, ty, ct, kd, ln, put=_outlined)
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
    """A world clock: the local time big, the name or place under it, then the other details; LIVE dot."""
    c, S, B = k.c, k.S, k.B
    pad = 3
    big = ct.time or "--:--"
    icon = (ct.icon if ct.icon else ("moon" if ct.night else "sun") if ct.night is not None else None)
    if ct.headline_kind == "time":                          # the clock is the headline: name (or place) under it
        head_kind = "name" if ct.sections.get("name") else "place"
    else:
        head_kind = ct.headline_kind
    head = _clip(S, ct.sections.get(head_kind) or "", k.w - 2 * pad - 2)
    bits = [b for b in ct.bits(with_time=False) if b[0] != head_kind]
    lines = detail_lines(S, bits, k.w - 2 * pad - 6, k.lines if k.lines != "inline" else "auto", k.sep)[:3]
    bw_ = (9 if icon else 0) + _tw(B, big) + (_tw(S, ct.ampm) + 1 if ct.ampm else 0)
    box_w = min(k.w - 2, max([bw_, _tw(S, head)] + [_tw(S, s) for _, s in lines]) + 2 * pad + 6)
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
    for s_ in ([head] if head else []) + [t for _, t in lines]:
        graphics.DrawText(c, S, bx + pad, ty + S.baseline, k.text, s_)
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
    maxw = k.w - 2 - 2 * pad
    rows = [(f, l, _clip(f, r, max(0, maxw - min(_tw(f, l), maxw // 2) - 6))) for f, l, r in rows]
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


# --- custom: your own columns and lines ----------------------------------------------------------------------------
FIELDS = ("shield", "live", "name", "place", "time", "age", "weather", "date", "offset", "sun", "custom", "credit")
DEFAULT_SLOTS = {"shield": [1, 1], "live": [1, 1], "name": [1, 1], "place": [1, 2], "time": [1, 2], "age": [1, 2], "weather": [1, 3], "date": [1, 3],
                 "offset": [1, 3], "sun": [1, 3], "custom": [1, 3], "credit": [1, 3]}
LINE_SIZES = ("headline", "detail", "big")
SHIELD_KINDS = ("auto", "bc", "tch", "yellowhead", "crowsnest", "interstate", "us", "wa")
SHIELD_MIN, SHIELD_MAX = 12, 64       # the sprite sizes shields.json has (12-64 px tall)


SHIELD_PLACES = ("inside", "left", "right", "line")


def shield_spec(ov):
    """(kind or None for auto, place, size): place is inside | left | right (outside the box) | line; size is "box"
    (the box's height) or a height in px."""
    s = ov.get("shield") if isinstance(ov.get("shield"), dict) else {}
    kind = s.get("kind") if s.get("kind") in SHIELD_KINDS[1:] else None
    place = s.get("place") if s.get("place") in SHIELD_PLACES else "inside"
    size = s.get("size", "box")
    if not (size == "box" or (isinstance(size, int) and not isinstance(size, bool) and SHIELD_MIN <= size <= SHIELD_MAX)):
        size = "box"
    return kind, place, size


def _shield_dims(route, h, kind=None):
    """(width, height) of the shield drawn for a height of h px."""
    from display.modules import shields
    rgb = shields._sprite(kind or shields.kind_for(route), max(SHIELD_MIN, h))[0]
    return rgb.shape[1], rgb.shape[0]


def custom_grid(ct, ov):
    """{column: {line: [kind, …]}} for the sections that are on and have text, in the chosen order."""
    slots = ov.get("slots") if isinstance(ov.get("slots"), dict) else {}
    order = [k for k in (ov.get("order") or []) if k in FIELDS]
    if "live" not in order:                                # older orders don't list LIVE: it leads, as on the page
        order.insert(0, "live")
    if "shield" not in order:                              # nor the shield, which leads its line
        order.insert(0, "shield")
    order += [k for k in FIELDS if k not in order]
    grid = {}
    for kind in order:
        if kind == "live":
            if not ct.live:
                continue
        elif not (ct.clock() if kind == "time" else ct.sections.get(kind)):
            continue
        slot = slots.get(kind, DEFAULT_SLOTS.get(kind, [1, 3]))
        try:
            col, line = max(1, min(3, int(slot[0]))), max(1, min(4, int(slot[1])))
        except (TypeError, ValueError, IndexError):
            col, line = DEFAULT_SLOTS.get(kind, [1, 3])
        grid.setdefault(col, {}).setdefault(line, []).append(kind)
    return grid


def right_kinds(ov):
    """The sections pushed to the right end of their line (a slot's optional third value, "right")."""
    slots = ov.get("slots") if isinstance(ov.get("slots"), dict) else {}
    return {k for k, v in slots.items() if isinstance(v, list) and len(v) > 2 and v[2] == "right"}


def _l_custom(k, ct):
    """Your arrangement: up to 3 columns × 4 lines, sections in the order you set; each line's size and each column's
    alignment chosen per webcam. Container: box (in the chosen position), strip (full width), outline, or board."""
    c, ov = k.c, k.ov
    grid = custom_grid(ct, ov)
    if not grid:
        return
    sizes = ov.get("line_sizes") if isinstance(ov.get("line_sizes"), dict) else {}
    cols_cfg = ov.get("columns") if isinstance(ov.get("columns"), list) else []
    container = ov.get("container") if ov.get("container") in ("box", "strip", "outline", "board") else "box"
    divider = bool(ov.get("divider", True))
    board = container == "board"
    color = Color(*AMBER) if board and _rgb(k.accent) == _hex(DEFAULT_OVERLAY["accent"], None) else \
        (k.accent if board else k.text)
    put = _outlined if container == "outline" else graphics.DrawText
    pad, gap = 3, 9
    sp_name = k.sep
    pushed = right_kinds(ov)
    split_gap = 6                                          # the least space between a line's left and right parts

    def sides(kinds):
        return [kd for kd in kinds if kd not in pushed], [kd for kd in kinds if kd in pushed]

    # the shield: a piece of its line ("line"); inside the box in front of its column's text, the box growing to fit
    # ("inside"); or beside the box, outside it ("left" / "right"; a strip spans the zone, so it goes inside)
    sh_kind, sh_place, sh_size = shield_spec(ov)
    if container == "strip" and sh_place in ("left", "right"):
        sh_place = "inside"
    route = ct.sections.get("shield", "")
    emblem = outside = None                                # the column an inside shield leads; "left"/"right"
    if route and sh_place != "line":
        for col in list(grid):
            for ln in list(grid[col]):
                if "shield" in grid[col][ln]:
                    if sh_place == "inside":
                        emblem = col
                    else:
                        outside = sh_place
                    grid[col][ln].remove("shield")
                    if not grid[col][ln]:
                        del grid[col][ln]
            if not grid[col] and col != emblem:
                del grid[col]
        if emblem is not None:
            grid.setdefault(emblem, {})
        if not grid:
            grid[1] = {}                                   # a shield alone: an empty box beside it

    def shield_h(f):
        return max(SHIELD_MIN, f.height)

    def line_h(f, kinds):
        return max(f.height, _shield_dims(route, shield_h(f), sh_kind)[1]) if "shield" in kinds else f.height

    def font_of(col, line):
        sz = sizes.get(f"{col}.{line}") or ("headline" if (col, line) == (min(grid), 1) else "detail")
        return {"headline": k.T, "big": k.B}.get(sz, k.S)

    def piece_width(f, kind):
        if kind == "live":
            return _tw(k.S, "LIVE") + 4
        if kind == "shield":
            return _shield_dims(route, shield_h(f), sh_kind)[0]
        w_ = _tw(f, _text(ct, kind))
        return w_ + (9 if kind == "weather" and ct.icon else 0)

    def run_width(f, kinds):
        return sum(piece_width(f, kd) for kd in kinds) + sum(_joint(f, a, b) for a, b in zip(kinds, kinds[1:]))

    def line_width(f, kinds):
        left, right = sides(kinds)
        return run_width(f, left) + run_width(f, right) + (split_gap if left and right else 0)

    def _joint(f, a, b):                                   # a separator between sections; a gap beside LIVE or a shield
        return 3 if {"live", "shield"} & {a, b} else _tw(f, _sep(f, sp_name))

    # the box's height comes from the lines; an emblem is sized from it (or set in px)
    cols = sorted(grid)
    text_h = max([sum(line_h(font_of(col, ln), grid[col][ln]) + 1 for ln in grid[col]) - 1 for col in cols if grid[col]]
                 or [k.S.height])
    ew, sw, eh, want = {}, 0, 0, 0                         # inside shield's width (with its gap) per column; its size
    if emblem is not None or outside:
        want = (text_h + 2 * pad - (2 if emblem is not None else 0)) if sh_size == "box" else sh_size
        sw, eh = _shield_dims(route, want, sh_kind)
        if emblem is not None:
            ew[emblem] = sw + (4 if grid[emblem] else 0)
    out_w = sw + 2 if outside else 0                       # an outside shield and its gap to the box

    # measure: every column at its natural width; if they don't all fit, the widest give way first
    avail = k.w - 2 * pad - 2 - gap * (len(cols) - 1) - sum(ew.values()) - out_w
    natural = {col: max([line_width(font_of(col, ln), grid[col][ln]) for ln in grid[col]] or [0]) for col in cols}
    room = dict(natural)
    while sum(room.values()) > avail:                      # the widest gives way, down to the next widest…
        widest = max(room, key=room.get)
        excess = sum(room.values()) - avail
        second = max([v for c_, v in room.items() if c_ != widest], default=0)
        target = max(second, room[widest] - excess)
        if target >= room[widest]:                         # …and when they're level, all share the space evenly
            share = max(1, avail // len(room))
            room = {c_: min(v, share) for c_, v in room.items()}
            break
        room[widest] = target
    layout = []                                            # [(col, [(font, [kinds], width)], col_width)]
    for col in cols:
        lines = []
        for ln in sorted(grid[col]):
            f = font_of(col, ln)
            kinds = list(grid[col][ln])
            while kinds and line_width(f, kinds) > room[col]:
                drop = next((d for d in DROP if d in kinds), None)
                if drop is None or len(kinds) == 1:
                    break
                kinds.remove(drop)
                _note_dropped([(drop, "")], set())
            lines.append((f, kinds, min(line_width(f, kinds), room[col])))
        layout.append((col, lines, max([w_ for _, _, w_ in lines] or [0])))
    total_w = sum(cw + ew.get(col, 0) for col, _, cw in layout) + gap * (len(layout) - 1)
    box_w, box_h = total_w + 2 * pad + (1 if container == "box" else 0), text_h + 2 * pad
    if emblem is not None:
        box_h = max(box_h, eh + 2)                         # inside: the box grows to the shield (text centred in it)

    if container == "strip":
        bx, by = k.x, (k.y if k.top else k.y + k.h - box_h)
        _box(c, bx, by, k.w, box_h, k.bg, k.op)
        c.FillRect(k.x, by + box_h - 1 if k.top else by, k.w, 1, k.accent)
        # left-aligned columns flow from the left edge, right-aligned ones from the right
        xs, left_x, right_x = {}, bx + pad + 1, bx + k.w - pad - 1
        for i, (col, lines, cw) in enumerate(layout):
            if _col_align(cols_cfg, col, i, len(layout)) == "right":
                continue
            xs[col] = left_x
            left_x += cw + ew.get(col, 0) + gap
        for i, (col, lines, cw) in reversed(list(enumerate(layout))):
            if col in xs:
                continue
            right_x -= cw + ew.get(col, 0)
            xs[col] = max(right_x, left_x)                 # never over a left-flowing column
            right_x -= gap
    else:
        bx, by = k.place(min(box_w, k.w - 2 - out_w) + out_w, box_h, margin=2)     # the box and an outside shield
        if outside:
            from display.modules import shields
            bw_ = min(box_w, k.w - 2 - out_w)
            sx = bx if outside == "left" else bx + bw_ + 2
            if outside == "left":
                bx += out_w
            sy = by + (box_h - eh) // 2 if eh <= box_h else (by if k.top else by + box_h - eh)
            shields.draw(c, sx, sy, route, h=max(SHIELD_MIN, want), kind=sh_kind)
        box_w = min(box_w, k.w - 2 - out_w)
        if container == "box":
            _box(c, bx, by, min(box_w, k.w - 2), box_h, k.bg, k.op)
            c.FillRect(bx if k.align != "right" else bx + min(box_w, k.w - 2) - 1, by, 1, box_h, k.accent)
        elif board:
            _box(c, bx, by, min(box_w, k.w - 2), box_h, k.bg, max(k.op, 0.85))
            dim = Color(*(v // 3 for v in _rgb(color)))
            c.FillRect(bx, by, min(box_w, k.w - 2), 1, dim)
            c.FillRect(bx, by + box_h - 1, min(box_w, k.w - 2), 1, dim)
        xs, x = {}, bx + pad + (1 if container == "box" and k.align != "right" else 0)
        for col, lines, cw in layout:
            xs[col] = x
            x += cw + ew.get(col, 0) + gap

    for i, (col, lines, cw) in enumerate(layout):
        x0 = xs[col]
        if col == emblem:                                  # centred in the box, which grew to fit it
            from display.modules import shields
            shields.draw(c, x0, by + (box_h - eh) // 2, route, h=max(SHIELD_MIN, want), kind=sh_kind)
            x0 += ew[col]
        if divider and i > 0 and container != "outline":
            c.FillRect(x0 - (gap + 1) // 2, by + 2, 1, box_h - 4, k.accent if not board else Color(*(v // 3 for v in _rgb(color))))
        align = _col_align(cols_cfg, col, i, len(layout))
        ty = by + (box_h - text_h) // 2                    # = by + pad unless a shield made the box taller
        for f, kinds, w_ in lines:
            lh = line_h(f, kinds)
            left, right = sides(kinds)
            if right:                                      # a split line spans the column: right part flush right
                rw = min(run_width(f, right), cw)
                if left:
                    _draw_line(k, ct, f, x0, ty, left, max(0, cw - rw - split_gap), color, put, lh, sh_kind)
                _draw_line(k, ct, f, x0 + cw - rw, ty, right, rw, color, put, lh, sh_kind)
            else:
                x = x0 + (cw - w_ if align == "right" else (cw - w_) // 2 if align == "center" else 0)
                _draw_line(k, ct, f, x, ty, kinds, w_, color, put, lh, sh_kind)
            ty += lh + 1


def _col_align(cols_cfg, col, index, count):
    try:
        a = cols_cfg[col - 1].get("align")
    except (IndexError, AttributeError, TypeError):
        a = None
    if a in ("left", "center", "right"):
        return a
    return "right" if count > 1 and index == count - 1 else "left"   # a last column hugs the right by default


def _text(ct, kind):
    return ct.clock() if kind == "time" else ct.sections.get(kind, "")


def _draw_line(k, ct, f, x, y_top, kinds, room, color, put, lh=None, sh_kind=None):
    """One line of pieces: LIVE badges, shields, a weather icon before the weather, separators between; clipped to
    room. lh: the line's height when a shield makes it taller than the font (text is centred in it)."""
    end = x + room
    sp = _sep(f, k.sep)
    lh = lh or f.height
    y_top += (lh - f.height) // 2
    for i, kind in enumerate(kinds):
        if i:
            if {"live", "shield"} & {kind, kinds[i - 1]}:
                x += 3
            elif x + _tw(f, sp) + 4 <= end:                # no separator unless some of the next piece fits after it
                x += put(k.c, f, x, y_top + f.baseline, color, sp)
            else:
                break
        if x >= end:
            break
        if kind == "live":
            x += k.badge(x, y_top + max(0, (f.height - k.S.height - 1) // 2))
            continue
        if kind == "shield":
            from display.modules import shields
            route = ct.sections.get("shield", "")
            sw, sh = _shield_dims(route, max(SHIELD_MIN, f.height), sh_kind)
            if x + sw <= end:
                shields.draw(k.c, x, y_top + (f.height - sh) // 2, route, h=max(SHIELD_MIN, f.height), kind=sh_kind)
            x += sw
            continue
        if kind == "weather" and ct.icon:
            draw_icon(k.c, x, y_top + (f.height - 7) // 2, ct.icon)
            x += 9
        x += put(k.c, f, x, y_top + f.baseline, color, _clip(f, _text(ct, kind), max(0, end - x)))


LAYOUT_FUNCS = {"custom": _l_custom, "tag": _l_tag, "outline": _l_outline, "bar": _l_bar, "dot": _l_dot, "lower_third": _l_lower_third,
                "split": _l_split, "postcard": _l_postcard, "chips": _l_chips, "clock": _l_clock,
                "signage": _l_signage, "ticker": _l_ticker, "stack": _l_stack}
