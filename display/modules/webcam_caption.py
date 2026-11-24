"""Webcam captions: what the overlay says (sections) and how it's laid out, per webcam.

The caption is a Tag: a box in a corner with an accent edge, holding ``rows`` of pieces (see "the caption: rows of
pieces" below). The other styles (bar, ticker, custom…) went with rows (2026-11); a caption set to one draws as tag.

``overlays`` (optional): {side | full | tall | wall: overlay}, a caption of its own for that size on the wall (the
two sides share "side"); a size without one uses ``overlay``. for_shape() picks it.

``overlay`` in a webcam's config (all optional; DEFAULT_OVERLAY fills the gaps):
    rows        [{"items": [piece, …], "wrap": bool}, …] (up to MAX_ROWS): each row's pieces in order; a piece is
                {"k": a section, "name:2".."name:4" (the name's further lines), "live", "shield" or "sep", "size"?
                (SIZES; none: the name's size for the name, the details' for the rest), "s" (a separator's kind,
                SEP_KINDS), "pad" (spaces round a separator), "spill" (moves to the next row when its own is full)}.
                Being in a row is what shows a section. No rows: legacy_rows makes them from the keys below
                (headline, show, order, lines, separator, place_beside), which only captions from before rows use.
    style       tag | none
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

Weather and sunrise/sunset come from data/webcam_weather.json (hub/jobs/webcam_weather.py, for webcams with a lat/lon);
so does the local time's zone when the webcam has no ``tz`` of its own.
Everything is drawn with approved bitmap fonts at their native size. Long content drops sections in DROP order before
any text is cut.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np

from display.gfx import Color, graphics
from display.modules import caption_flags

INTRO_SEC = 10.0
FADE_SEC = 0.6
FONTS = {"tiny": "fonts/alert/tom-thumb.bdf", "small": "fonts/alert/5x8.bdf", "medium": "fonts/alert/6x10.bdf",
         "large": "fonts/alert/7x13B.bdf", "xlarge": "fonts/alert/9x15B.bdf"}
BIG = {"tiny": "fonts/alert/6x10.bdf", "small": "fonts/alert/7x13B.bdf", "medium": "fonts/alert/7x13B.bdf",
       "large": "fonts/alert/9x15B.bdf", "xlarge": "fonts/alert/10x20.bdf"}     # clocks and the postcard title
DETAIL = {"tiny": "fonts/alert/tom-thumb.bdf", "xlarge": "fonts/alert/6x10.bdf"}   # the details' font (else 5x8)
LAYOUTS = ("tag", "none")      # the other styles went with rows (2026-11); a caption set to one of them draws as tag
STYLES = LAYOUTS                             # older name
# weather extras (from data/webcam_weather.json): feels-like, wind, humidity, today's high/low, the conditions in words
WX_EXTRAS = ("feels", "wind", "humidity", "hilo", "conditions")
# a webcam in space (cams.<id>.space, display/modules/space.py): what it's over (in the place's slot), its next orbital
# sunrise or sunset, the local time below, who's aboard, the mission
SPACE = ("coords", "orbit", "below", "crew", "mission")
SECTIONS = ("name", "place", "time", "date", "offset", "daynight", "weather", "sun", "age", "live", "credit", "custom",
            "shield", "over") + WX_EXTRAS + SPACE
ORDER = ("name", "place", "time", "age", "weather", "conditions", "feels", "hilo", "wind", "humidity", "date", "offset",
         "sun", "custom", "credit") + SPACE    # default order
DROP = ("credit", "custom", "mission", "humidity", "wind", "hilo", "feels", "conditions", "sun", "offset", "date", "coords",
        "below", "crew", "weather", "age", "orbit", "place", "shield", "name")    # dropped first → last
TEXT_ALIGNS = ("auto", "left", "center", "right")
SEPARATORS = {"dot": " · ", "slash": " / ", "pipe": " | ", "dash": " - ", "space": "  "}   # space: just a gap
DEFAULT_OVERLAY = {"style": "tag", "corner": "bl", "when": "always", "font": "small", "accent": "#e8202a",
                   "text": "#ffffff", "bg": "#000000", "opacity": 0.7, "time_format": "24h", "fade": True,
                   "show_sec": 12, "every_min": 3, "custom_text": "", "headline": "name", "lines": "auto",
                   "separator": "space", "uppercase": False, "order": list(ORDER), "jp_font": "auto",
                   "jp_face": "auto", "place_beside": False, "wx_beside": False, "wx_beside_box": False, "temp_icon": False,
                   "jp_caption": {"on": False, "text": "", "face": "k8x12", "size": 12, "corner": "auto"},
                   "show": {"name": True, "place": True, "time": True, "date": False, "offset": False,
                            "daynight": False, "weather": True, "sun": False, "age": False, "live": True,
                            "credit": False, "custom": False, "shield": False, "over": True,
                            **{k: False for k in WX_EXTRAS}, **{k: k in ("orbit", "below", "crew") for k in SPACE}},
                   "crew_flags": True, "mission_days": False,
                   "flags": dict(caption_flags.DEFAULT_FLAGS)}   # (caption_flags.py)
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


# 15×15 icons for the weather beside the caption (wx_beside), drawn with a 1 px black outline; same palette
ICONS15 = {
    "sun": [
        "...............", ".......y.......", ".......y.......", "...y.......y...", "......yyy......",
        ".....yyyyy.....", "....yyyyyyy....", ".yy.yyyyyyy.yy.", "....yyyyyyy....", ".....yyyyy.....",
        "......yyy......", "...y.......y...", ".......y.......", ".......y.......", "...............",
    ],
    "moon": [
        "...............", "...............", ".....m.........", "....mm.........", "...mm..........",
        "..mmm..........", "..mmmm.........", "..mmmm.........", "..mmmmm........", "..mmmmmm.......",
        "...mmmmmmmmm...", "....mmmmmmm....", ".....mmmmm.....", "...............", "...............",
    ],
    "partly": [
        ".....y.........", ".....y.........", "..y.....y......", "....yyy........", "...yyyyy.......",
        "yy.yyyyy.yy....", "...yyyywwww....", "....yywwwwww...", "..ywwwwwwwwww..", "..wwwwwwwwwwww.",
        "..wwwwwwwwwwww.", "..wwwwwwwwwwww.", "...gggggggggg..", "...............", "...............",
    ],
    "partly_night": [
        "...............", "....m..........", "...m...........", "..mm...........", ".mmm...........",
        ".mmmm..........", ".mmmm..wwww....", "..mmmmwwwwww...", "...wwwwwwwwww..", "..wwwwwwwwwwww.",
        "..wwwwwwwwwwww.", "..wwwwwwwwwwww.", "...gggggggggg..", "...............", "...............",
    ],
    "cloud": [
        "...............", "...............", "...............", "...............", ".......wwww....",
        "......wwwwww...", "...wwwwwwwwww..", "..wwwwwwwwwwww.", "..wwwwwwwwwwww.", "..wwwwwwwwwwww.",
        "...gggggggggg..", "...............", "...............", "...............", "...............",
    ],
    "fog": [
        "...............", "...............", ".ggggggggggg...", "...............", "...............",
        "...............", "...ggggggggggg.", "...............", "...............", "...............",
        ".gggggggggggg..", "...............", "...............", "...............", "....gggggggggg.",
    ],
    "rain": [
        "...............", ".......wwww....", "......wwwwww...", "...wwwwwwwwww..", "..wwwwwwwwwwww.",
        "..wwwwwwwwwwww.", "..wwwwwwwwwwww.", "...gggggggggg..", "...............", "...............",
        "....b...b...b..", "...b...b...b...", "...............", "..b...b...b....", ".b...b...b.....",
    ],
    "snow": [
        "...............", ".......wwww....", "......wwwwww...", "...wwwwwwwwww..", "..wwwwwwwwwwww.",
        "..wwwwwwwwwwww.", "..wwwwwwwwwwww.", "...gggggggggg..", "...............", "...............",
        "...c....c....c.", "...............", "...............", ".....c....c....", "...............",
    ],
    "storm": [
        ".......gggg....", "......gggggg...", "...gggggggggg..", "..gggggggggggg.", "..gggggggggggg.",
        "..gggggggggggg.", "...gggggggggg..", "........y......", ".......y.......", "......yyyy.....",
        "........y......", ".......y.......", "......y........", "......y........", "...............",
    ],
}


def draw_icon_beside(c, x, y, name):
    """A 15×15 weather icon at (x, y) with a 1 px black outline round it (the outline goes outside the 15×15)."""
    rows = ICONS15.get(name)
    if not rows:
        return
    on = lambda i, j: 0 <= i < 15 and 0 <= j < 15 and rows[j][i] in PALETTE
    for j in range(-1, 16):
        for i in range(-1, 16):
            if on(i, j):
                c.SetPixel(x + i, y + j, *PALETTE[rows[j][i]])
            elif any(on(i + di, j + dj) for di in (-1, 0, 1) for dj in (-1, 0, 1)):
                c.SetPixel(x + i, y + j, 0, 0, 0)


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


CONDITIONS = [((0,), "Clear"), ((1,), "Mostly clear"), ((2,), "Partly cloudy"), ((3,), "Cloudy"), ((45, 48), "Fog"),
              ((51, 53, 55, 56, 57), "Drizzle"), ((61, 63, 66), "Rain"), ((65, 67), "Heavy rain"),
              ((71, 73, 77), "Snow"), ((75,), "Heavy snow"), ((80, 81), "Showers"), ((82,), "Heavy showers"),
              ((85, 86), "Snow showers"), ((95, 96, 99), "Thunderstorm")]
COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")


def conditions_text(code):
    """WMO weather code → a word or two ("Partly cloudy")."""
    try:
        code = int(code)
    except (TypeError, ValueError):
        return ""
    return next((w for codes, w in CONDITIONS if code in codes), "")


# A webcam may give each size on the wall a caption of its own: ``overlays`` = {size: overlay}, the sizes being the
# sides (left and right share one), full width, above the clock and the whole wall. A size without one uses
# ``overlay``. for_shape() gives the webcam as that size sees it (cached, so the caption's own caches still hit).
SIZE_OF_SHAPE = {"left": "side", "right": "side", "full": "full", "tall": "tall", "wall": "wall"}
SIZES_ON_WALL = ("side", "full", "tall", "wall")
_shaped: dict = {}


def shape_of_panel(panel, zone=None):
    """The shape a webcam (or ISS map) entry is drawn at: wall, full, tall, or the side its zone is on."""
    p = str(panel or "")
    for suffix, shape in (("_wall", "wall"), ("_full", "full"), ("_tall", "tall")):
        if p.endswith(suffix):
            return shape
    return "left" if zone is not None and zone[0] == 0 else "right"


def for_shape(cam: dict, shape) -> dict:
    """The webcam with the caption settings of the size ``shape`` (left | right | full | tall | wall) is drawn at,
    and that size in ``_size`` (flags show only at some sizes: caption_flags.FLAG_SIZES_ON_WALL)."""
    size = SIZE_OF_SHAPE.get(shape)
    if not isinstance(cam, dict) or size is None:
        return cam
    per = cam.get("overlays")
    own = per.get(size) if isinstance(per, dict) and isinstance(per.get(size), dict) else None
    key = (id(cam), size)
    hit = _shaped.get(key)
    if hit is not None and hit[0] is cam and hit[1] is own:
        return hit[2]
    if len(_shaped) > 256:
        _shaped.clear()
    view = {**cam, "_size": size, **({"overlay": own} if own is not None else {})}
    _shaped[key] = (cam, own, view)
    return view


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
    """(the bits kept, the line). A line with icon_kind in it has 9 px less room (its icon goes in front of it)."""
    sp = _sep(font, sep)
    kept = list(bits)

    def room(ks):
        return width - (9 if icon_kind and any(k == icon_kind for k, _ in ks) else 0)

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
    stacked → one per section. Lines with icon_kind in them (the weather with its icon) leave room for it."""
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
        room = width - (9 if icon_kind and any(k == icon_kind for k, _ in cur + [b]) else 0)
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
        room = width - (9 if icon_kind and any(k == icon_kind for k, _ in cur + [b]) else 0)
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
        if isinstance(ov.get("rows"), list) and ov["rows"]:   # rows: a section shows by being in a row
            sh = {**sh, **{k: True for k in SECTIONS if k != "daynight"}}
        up = (lambda s: s.upper()) if ov.get("uppercase") else (lambda s: s)
        t = now_local or _local_time(cam.get("tz") or (wx or {}).get("tz"))   # no zone set: the location's own
        self.tz = t.tzinfo
        twelve = ov.get("time_format") == "12h"
        sec = {}
        # the name may have several lines (the panel's Name box): the first is the headline, the rest go under it
        name_lines = [s.strip() for s in str(cam.get("label") or "").splitlines() if s.strip()] if sh.get("name") else []
        sec["name"] = name_lines[0] if name_lines else ""
        self.name_more = name_lines[1:]
        sec["place"] = (cam.get("place") or "") if sh.get("place") else ""
        sec["credit"] = (cam.get("credit") or "") if sh.get("credit") else ""
        sec["custom"] = (ov.get("custom_text") or "").strip() if sh.get("custom") else ""
        shield = ov.get("shield") if isinstance(ov.get("shield"), dict) else {}
        route = str(shield.get("route") or "").strip()[:8]
        from display.modules.shields import has_number      # as the panel: a route needs a number to be a section
        sec["shield"] = route if sh.get("shield") and has_number(route) else ""
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
            if ov.get("temp_icon"):                       # the small icon in front of the temperature: an option
                self.icon = weather_icon(wx.get("code"), bool(wx.get("is_day", 1)))
        # the bigger icon beside the caption box (needs the weather, not the temperature section)
        self.side_icon = weather_icon(wx.get("code"), bool(wx.get("is_day", 1))) if ov.get("wx_beside") else None
        sec["sun"] = self._sun(wx, t) if sh.get("sun") else ""
        sec.update(self._extras(wx, sh))
        taken = (extra or {}).get("taken")                  # when a still image was taken (epoch), from the hub
        sec["age"] = taken_text(taken, t.tzinfo, ov.get("taken_format"), twelve) if sh.get("age") and taken else ""
        for k in SPACE:
            sec[k] = ""
        if cam.get("space"):                                # in space: the ISS's sections instead of the Earth's
            from display.modules.space import sections as space_sections
            sp = space_sections((extra or {}).get("space"), ov, {**sh, "over": sh.get("over", True)})
            sec["place"] = sp["over"]
            sec.update({k: sp[k] for k in SPACE})
            for k in ("time", "date", "offset", "weather", "sun", "age") + WX_EXTRAS:
                sec[k] = ""
            self.icon = self.side_icon = None
            self.ampm = ""
        self.night = None
        if sh.get("daynight"):
            self.night = (not wx["is_day"]) if "is_day" in wx else (t.hour < 6 or t.hour >= 19)
        self.live = bool(sh.get("live"))
        head = ov.get("headline", "name")
        if head not in ("name", "place", "custom", "time") or not sec.get(head):
            head = "name" if sec["name"] else ("place" if sec["place"] else "name")
        if head != "name" or ov.get("style") == "custom":   # the name as one detail (or one custom slot): one line
            sec["name"] = " ".join(name_lines)
            self.name_more = []
        self.name_more = [up(s) for s in self.name_more]
        self.name_lines = [up(s) for s in str(cam.get("label") or "").splitlines() if s.strip()][:NAME_LINES]
        self.space = bool(cam.get("space"))
        from display.modules import caption_flags         # the row of flags (display/modules/caption_flags.py)
        self.flag_ids = tuple(caption_flags.ids_for(cam, ov))
        self.headline_kind = head
        self.title = up(sec[head] + (" " + self.ampm if head == "time" and self.ampm else ""))
        order = [k for k in (ov.get("order") or []) if k in ORDER] + [k for k in ORDER if k not in (ov.get("order") or [])]
        self.sections = {k: up(v) for k, v in sec.items()}
        self.order = [k for k in order if k != head]
        self.time = sec["time"]
        self.place = self.sections["place"]
        self.date, self.offset = self.sections["date"], self.sections["offset"]

    @staticmethod
    def _extras(wx, sh):
        """The weather extras that are on and have data (empty strings otherwise)."""
        out = {k: "" for k in WX_EXTRAS}
        num = lambda k: wx.get(k) if isinstance(wx.get(k), (int, float)) and not isinstance(wx.get(k), bool) else None
        if sh.get("feels") and num("feels") is not None:
            out["feels"] = f"feels {round(wx['feels'])}°"
        if sh.get("wind") and num("wind") is not None:
            d = num("wind_dir")
            word = (COMPASS[round(d / 45) % 8] + " ") if d is not None and wx["wind"] >= 1 else ""
            out["wind"] = f"{word}{round(wx['wind'])} km/h" if wx["wind"] >= 1 else "calm"
            if wx["wind"] >= 1 and num("gust") is not None and wx["gust"] >= wx["wind"] + 15:
                out["wind"] += f" gusts {round(wx['gust'])}"
        if sh.get("humidity") and num("humidity") is not None:
            out["humidity"] = f"{round(wx['humidity'])}% RH"
        if sh.get("hilo") and num("high") is not None and num("low") is not None:
            out["hilo"] = f"H {round(wx['high'])}° L {round(wx['low'])}°"
        if sh.get("conditions"):
            out["conditions"] = conditions_text(wx.get("code"))
        return out

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
        """The detail sections, in the chosen order (not the headline); the name's further lines first ("name2")."""
        out = [("name2", s) for s in self.name_more]
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
    def __init__(self, c, zone, ov, font_for, cjk=False):
        self.c, self.ov, self.fl = c, ov, font_for
        self.x, self.y, self.w, self.h = zone
        size = ov.get("font") if ov.get("font") in FONTS else "small"
        self.size = size
        self.T = font_for(FONTS.get(size, FONTS["small"]))            # the headline
        self.S = font_for(DETAIL.get(size, FONTS["small"]))           # details
        self.B = font_for(BIG.get(size, BIG["small"]))                # big clock / postcard
        jp = ov.get("jp_font") if ov.get("jp_font") in FONTS else size
        face = ov.get("jp_face") if ov.get("jp_face") in JP_FACES else "auto"
        if cjk and (jp != size or face != "auto"):        # Japanese at its own size or in its own font; Latin stays
            self.T = jp_sized(self.T, font_for(FONTS[jp]).height, face)
            self.S = jp_sized(self.S, font_for(DETAIL.get(jp, FONTS["small"])).height, face)
            self.B = jp_sized(self.B, font_for(BIG[jp]).height, face)
        self.accent = Color(*_hex(ov.get("accent"), (232, 32, 42)))
        self.text = Color(*_hex(ov.get("text"), (255, 255, 255)))
        self.bg = _hex(ov.get("bg"), (0, 0, 0))
        self.op = _num(ov.get("opacity"), 0.7, 0.0, 1.0)
        self.sep = ov.get("separator") if ov.get("separator") in SEPARATORS else "space"
        self.lines = ov.get("lines") if ov.get("lines") in ("auto", "wrap", "stacked", "inline") else "auto"
        corner = ov.get("corner") if ov.get("corner") in ("tl", "tc", "tr", "bl", "bc", "br") else "bl"
        self.top = corner[0] == "t"
        self.align = {"l": "left", "c": "center", "r": "right"}[corner[1]]
        self.left = self.align == "left"
        # the label's size and spacing: margin from the zone's edges, padding inside the box, the box's width
        # ("auto": as wide as its text; "full": the zone's width; a number: at least that many pixels) and how the
        # text sits in it ("auto": on the side of the position)
        self.margin = int(_num(ov.get("margin"), -1, -1, 32))           # -1: each layout's own
        self.pad = int(_num(ov.get("pad"), 2, 0, 10))
        bw = ov.get("box_w", "auto")
        self.box_w = "full" if bw == "full" else int(_num(bw, 0, 0, 448)) if bw not in (None, "", "auto") else 0
        ta = ov.get("text_align") if ov.get("text_align") in TEXT_ALIGNS else "auto"
        self.text_align = self.align if ta == "auto" else ta

    def font_at(self, path):
        """The font at a path (a piece's own size), able to draw Japanese."""
        return self.fl(path)

    def place(self, bw, bh, margin=1):
        """Top-left of a bw×bh box in this caption's position."""
        margin = margin if self.margin < 0 else self.margin
        bx = {"left": self.x + margin, "center": self.x + (self.w - bw) // 2,
              "right": self.x + self.w - bw - margin}[self.align]
        by = self.y + margin if self.top else self.y + self.h - bh - margin
        return bx, by

    def sized(self, content_w):
        """A box's width for content this wide (plus padding), with the label's width setting and the zone's edges."""
        room = self.w - 2 * max(0, self.margin)
        if self.box_w == "full":
            return room
        return min(room, max(content_w, self.box_w))

    def line_x(self, x0, inner, w):
        """Where a line w wide starts in a box whose text area is inner wide, from x0."""
        return x0 + {"left": 0, "center": max(0, (inner - w) // 2), "right": max(0, inner - w)}[self.text_align]

    def badge(self, x, y, text="LIVE", font=None, inverse=False):
        f = font or self.S
        w = _tw(f, text) + 4
        _box(self.c, x, y, w, f.height + 1, (255, 255, 255) if inverse else _rgb(self.accent), 1.0, round_=True)
        graphics.DrawText(self.c, f, x + 2, y + f.baseline, self.accent if inverse else WHITE, text)
        return w

    def details(self, ct, width, with_time=True, with_place=True, max_lines=None):
        """[(first kind, text)] detail lines for this caption's line mode (stacked: up to 4, fit: up to FIT_LINES)."""
        n = max_lines or (4 if self.lines == "stacked" else FIT_LINES)
        bits = ct.bits(with_time, with_place)
        more = [] if getattr(self, "title_multi", False) else [(kd, _clip(self.S, v, width)) for kd, v in bits if kd == "name2"]
        rest = [b for b in bits if b[0] != "name2"]
        return more + detail_lines(self.S, rest, width, self.lines, self.sep, max_lines=max(1, n - len(more)),
                                   icon_kind="weather" if ct.icon else None)

    def text_with_icon(self, f, x, y_top, ct, kind, text, color=None, put=None):
        """Draw a detail line; the weather icon goes in front of the weather, wherever it is in the line."""
        put = put or graphics.DrawText
        color = color or self.text
        at = _icon_at(ct, kind, text)
        if at is None:
            return put(self.c, f, x, y_top + f.baseline, color, text)
        if at:
            put(self.c, f, x, y_top + f.baseline, color, text[:at])
            x += _tw(f, text[:at])
        draw_icon(self.c, x, y_top + (f.height - 7) // 2, ct.icon)
        return put(self.c, f, x + 9, y_top + f.baseline, color, text[at:])


def _icon_at(ct, kind, text):
    """Where in a detail line the weather icon goes (its index in text), or None when the line has no weather."""
    if not ct.icon:
        return None
    if kind == "weather":
        return 0
    w = ct.sections.get("weather") or ""
    i = text.find(w) if w else -1
    return i if i > 0 else None


def _icon_w(ct, kind, text):
    """The room a detail line's weather icon takes (9 px), or 0."""
    return 9 if _icon_at(ct, kind, text) is not None else 0

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
    """Draw the webcam's caption over the frame in zone. font_for(path or size) -> a loaded Font. Returns its box in
    the zone (x0, y0, x1, y1), or None when nothing is drawn (the small ISS map keeps clear of it).

    The caption is rendered once into a transparent layer (cached until its text, settings or blink phase change,
    about once a minute) and each frame only blends that layer over the video in the caption's box. The ticker,
    which scrolls, draws directly."""
    ov = overlay_of(cam)
    style = ov.get("style", "tag") if ov.get("style") in LAYOUT_FUNCS else "tag"
    a = visibility(ov, elapsed)
    if a <= 0:
        return None
    x, y, w, h = zone
    if now_local is None:              # the same layer for 0.4 s: skip rebuilding the key
        mk = (id(cam), id(font_for), w, h, int(time.time() * 2.5), (wx or {}).get("at"), round((extra or {}).get("taken") or 0))
        parts = _memo.get(mk)
        if parts is None:
            if len(_memo) > 32:
                _memo.clear()
            parts = _memo[mk] = _layer(style, ov, Content(cam, ov, None, wx=wx, extra=extra), w, h, font_for) or ()
    else:
        parts = _layer(style, ov, Content(cam, ov, now_local, wx=wx, extra=extra), w, h, font_for) or ()
    _composite(c, x, y, parts, a)
    return _box_of(parts)


def _box_of(parts):
    """The caption's box in the zone (x0, y0, x1, y1) round all its parts, or None."""
    if not parts:
        return None
    bs = [b for b, _a, _p in parts]
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


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
    style = ov.get("style", "tag") if ov.get("style") in LAYOUT_FUNCS else "tag"
    w, h = zone_size
    _rec.dropped = found = set()
    try:
        ct = Content(cam, ov, wx=wx, extra=extra)
        LAYOUT_FUNCS[style](Ctx(_Zone(Canvas(w, h), (0, 0, w, h)), (0, 0, w, h), ov, _font_loader(font_for), has_cjk(ct)), ct)
    finally:
        _rec.dropped = None
    return sorted(found)


_layers: dict = {}           # key -> (bbox, alpha, premultiplied colour); a few per webcam


def _layer(style, ov, ct, w, h, font_for):
    """The caption drawn onto black and onto white: the difference gives each pixel's opacity, the black one its
    premultiplied colour. Returns parts [(bbox, alpha (h,w,1) uint16, premult*255 (h,w,3) uint16)], one per run of
    rows that has something in it (a badge in the far corner doesn't make the whole zone a blend), or None."""
    import json
    from display.gfx import Canvas
    blink = 0
    key = (style, json.dumps(ov, sort_keys=True, default=str), ct.title, tuple(ct.bits()), tuple(ct.name_lines), ct.icon, ct.night, ct.live,
           ct.ampm, ct.clock(), w, h, blink, id(font_for), ct.side_icon, ct.flag_ids, _flag_set_stamp())
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
        LAYOUT_FUNCS[style](Ctx(zc, (0, 0, w, h), ov, fl, has_cjk(ct)), ct)
        main = zc.bbox                                     # the caption's own box (before the icon and the Japanese)
        if ct.side_icon and main:
            _icon_beside(zc, main, w, h, ov, ct.side_icon)
        beside = zc.bbox                                   # the caption with its weather icon: flags go beside this
        jp = _jp_caption(zc, main, w, h, ov, fl)
        if ct.flag_ids:                                    # the flags: on the caption (and its Japanese) or a corner
            anchor = main
            if jp and main and jp_caption_of(ov).get("corner") not in ("tl", "tc", "tr", "bl", "bc", "br"):
                anchor = (min(main[0], jp[0]), min(main[1], jp[1]), max(main[2], jp[2]), max(main[3], jp[3]))
                if beside:                                 # the row, taller than the caption, reaches its band
                    beside = (min(beside[0], jp[0]), beside[1], max(beside[2], jp[2]), beside[3])
            from display.modules import caption_flags
            caption_flags.draw(zc, anchor, w, h, ov, ct.flag_ids, _box,
                               lambda corner, bw, bh: Ctx(zc, (0, 0, w, h), {**ov, "corner": corner}, fl).place(bw, bh, 2),
                               main=main, beside=beside)
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


JP_CAPTION = DEFAULT_OVERLAY["jp_caption"]
JP_CAPTION_TEXT_MAX = 400


def jp_caption_of(ov) -> dict:
    """The separate Japanese caption's settings (overlay "jp_caption"), defaults filled in."""
    got = ov.get("jp_caption") if isinstance(ov.get("jp_caption"), dict) else {}
    return {**JP_CAPTION, **got}


def _jp_caption(zc, main, w, h, ov, font_for):
    """The separate Japanese caption: its own lines of text in its own Japanese font and size (JP_FACES), on the
    caption's background (colour, strength, padding) and in its text colour. "auto": stacked against the main caption
    (above it in a bottom corner, below it in a top one, lined up with its outer edge); else its own corner. Returns
    its box (x0, y0, x1, y1), or None when there is none."""
    jc = jp_caption_of(ov)
    lines = [s.rstrip() for s in str(jc.get("text") or "")[:JP_CAPTION_TEXT_MAX].splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    if not jc.get("on") or not lines:
        return
    face = jc.get("face") if jc.get("face") in JP_FACES else JP_CAPTION["face"]
    sizes = JP_FACES[face][1]
    size = int(_num(jc.get("size"), sizes[0][0], 1, 64))
    path = min(sizes, key=lambda s: abs(s[0] - size))[1]
    f = _jp_fonts.get(path)
    if f is None:
        f = graphics.Font()
        try:
            f.LoadFont(path)
        except Exception:
            return
        _jp_fonts[path] = f
    if 0x41 not in f.glyphs:                              # a font without Latin: the caption's own for those
        f.fallback = f.fallback or with_japanese(font_for(FONTS["small"]))
    pad = int(_num(ov.get("pad"), 2, 0, 10))
    lh = f.height + 1
    room = w - 2 * pad - 2
    lines = [_clip(f, s, room) if s else "" for s in lines]
    bw = 2 * pad + max(_tw(f, s) for s in lines)
    bh = 2 * pad + lh * len(lines) - 1
    corner = jc.get("corner")
    if corner not in ("tl", "tc", "tr", "bl", "bc", "br"):
        corner = None
    own = str(ov.get("corner") or "bl")
    if corner is None and main:                           # next to the caption
        x0, y0, x1, y1 = main
        side = own[1:] if own[1:] in ("l", "c", "r") else "l"
        bx = x0 if side == "l" else x1 - bw if side == "r" else (x0 + x1 - bw) // 2
        by = y0 - 1 - bh if own[:1] != "t" else y1 + 1
    else:                                                 # its own corner (or the caption's, with no caption shown)
        k = Ctx(zc, (0, 0, w, h), {**ov, "corner": corner or own}, font_for)
        bx, by = k.place(bw, bh, 2)
    bx, by = max(0, min(bx, w - bw)), max(0, min(by, h - bh))
    _box(zc, bx, by, bw, bh, _hex(ov.get("bg"), (0, 0, 0)), _num(ov.get("opacity"), 0.7, 0.0, 1.0))
    color = Color(*_hex(ov.get("text"), (255, 255, 255)))
    right = (corner or own)[1:] == "r"
    for i, s in enumerate(lines):
        if s:
            x = bx + bw - pad - _tw(f, s) if right else bx + pad
            graphics.DrawText(zc, f, x, by + pad + i * lh + f.baseline, color, s)
    return bx, by, bx + bw, by + bh


def _flag_set_stamp():
    """Changes when the flag set on disk changes (a cached caption layer is redrawn with the new flags)."""
    from display.modules import caption_flags
    caption_flags.index()
    return caption_flags._index["mtime"]


def _icon_beside(zc, box, w, h, ov, name):
    """The weather icon beside the caption box: to its right, or to its left when the caption sits on the right;
    centred on the box's height. Outside the box (1 px gap, then its outline), or with ``wx_beside_box`` (the Tag
    layout) inside it: the box grows sideways by the icon and the padding, in the box's own colour and strength.
    Left out when there's no room."""
    x0, y0, x1, y1 = box
    right_side = str(ov.get("corner") or "bl")[1:] != "r"
    y = min(max(1, (y0 + y1) // 2 - 7), h - 16)
    if ov.get("wx_beside_box") and ov.get("style", "tag") == "tag":
        pad = int(_num(ov.get("pad"), 2, 0, 10))
        grow = 17 + pad                                    # outline, icon, outline, then the box's padding
        px = x1 if right_side else x0 - grow
        if px < 0 or px + grow > w:
            return
        _box(zc, px, y0, grow, y1 - y0, _hex(ov.get("bg"), (0, 0, 0)), _num(ov.get("opacity"), 0.7, 0.0, 1.0))
        x = px + 1 if right_side else px + pad + 1
    else:
        x = x1 + 2 if right_side else x0 - 17
    if x - 1 >= 0 and x + 16 <= w and y >= 1:
        draw_icon_beside(zc, x, y, name)


def _font_loader(font_for):
    """Accept the older font_for(size) callables as well as font_for(path). Every font comes back able to draw
    Japanese (with_japanese)."""
    def get(path_or_size):
        try:
            f = font_for(path_or_size)
        except Exception:
            size = next((k for k, v in FONTS.items() if v == path_or_size), "small")
            f = font_for(size)
        return with_japanese(f)
    return get


# Japanese (kana and kanji) for captions: the caption fonts are Latin only, so each gets an approved Japanese bitmap
# font of about its height (Fonts tab: approved; all with the full JIS X 0208 set) for the characters it lacks, drawn
# at native size on the same baseline. By the Latin font's height:
JP_FALLBACK = ((9, "fonts/alert/jp-misaki_gothic.bdf"),       # tom-thumb, 5x8 (8 px)
               (11, "fonts/alert/jp-mplus10r.bdf"),           # 6x10 (11 px)
               (13, "fonts/candidates/jp-kaname12b.bdf"),     # 7x13B (12 px, bold)
               (17, "fonts/alert/jp-shnm16b.bdf"),            # 9x15B (16 px, bold)
               (99, "fonts/alert/jp-kappa20b.bdf"))           # 10x20 (20 px, bold)
_jp_fonts: dict = {}
# The Japanese fonts a caption can choose (overlay "jp_face"): every approved Japanese bitmap font on the Fonts tab
# whose box height is its real size (the proportional and Galmuri cuts aren't), by family: [(height px, path)].
# The size used is the family's largest that fits the text size (else its smallest). "auto": JP_FALLBACK.
_J = "fonts/alert/jp-"
_K = "fonts/candidates/jp-"
JP_FACES = {
    "misaki": ("Misaki Gothic", [(8, _J + "misaki_gothic.bdf")]),
    "misaki_2nd": ("Misaki Gothic 2nd", [(8, _J + "misaki_gothic_2nd.bdf")]),
    "misaki_mincho": ("Misaki Mincho", [(8, _J + "misaki_mincho.bdf")]),
    "fusion": ("Fusion Pixel", [(8, _K + "fusion8-mono.bdf"), (10, _K + "fusion10-mono.bdf"),
                                (12, _K + "fusion12-mono.bdf")]),
    "ark": ("Ark Pixel", [(10, _K + "ark10-mono.bdf"), (12, _K + "ark12-mono.bdf")]),
    "mplus": ("M+", [(11, _J + "mplus10r.bdf"), (13, _K + "mplus12r.bdf")]),
    "mplus_bold": ("M+ bold", [(11, _K + "mplus10b.bdf")]),
    "k8x12": ("k8x12", [(12, _J + "k8x12.bdf")]),
    "k8x12l": ("k8x12 L", [(12, _J + "k8x12L.bdf")]),
    "k12": ("K12", [(12, _K + "k12.bdf")]),
    "kaname": ("Kaname", [(12, _K + "kaname12.bdf")]),
    "kaname_bold": ("Kaname bold", [(12, _K + "kaname12b.bdf")]),
    "shnm": ("Shinonome Gothic", [(12, _K + "shnm12.bdf"), (14, _K + "shnm14.bdf"), (16, _K + "shnm16.bdf")]),
    "shnm_bold": ("Shinonome Gothic bold", [(16, _J + "shnm16b.bdf")]),
    "shnm_maru": ("Shinonome Maru", [(12, _J + "shnm12maru.bdf")]),
    "shnm_maru_bold": ("Shinonome Maru bold", [(12, _K + "shnm12marub.bdf")]),
    "shnm_mincho": ("Shinonome Mincho", [(12, _J + "shnm12min.bdf"), (16, _J + "shnm16min.bdf")]),
    "shnm_mincho_bold": ("Shinonome Mincho bold", [(16, _K + "shnm16minb.bdf")]),
    "u8": ("U8 Gothic", [(12, _K + "u8-b12.bdf"), (14, _K + "u8-b14.bdf"), (16, _K + "u8-b16.bdf")]),
    "u8_bold": ("U8 Gothic bold", [(12, _K + "u8-b12-b.bdf"), (14, _K + "u8-b14-b.bdf"), (16, _K + "u8-b16-b.bdf")]),
    "jiskan": ("JIS Kan", [(16, _J + "jiskan16.bdf"), (24, _K + "jiskan24.bdf")]),
    "maru": ("Maru", [(16, _K + "maru16.bdf"), (18, _K + "maru18.bdf")]),
    "ayu": ("Ayu", [(18, _J + "ayu18.bdf"), (20, _J + "ayu20.bdf")]),
    "j83": ("J83", [(18, _K + "j83-18.bdf")]),
    "j83_bold": ("J83 bold", [(18, _K + "j83-18b.bdf")]),
    "kappa": ("Kappa bold", [(20, _J + "kappa20b.bdf")]),
    "kappa_medium": ("Kappa", [(20, _K + "kappa20m.bdf")]),
}


def _jp_path(height, face="auto"):
    """The Japanese font file for text about ``height`` px tall, in a family of JP_FACES or (auto) JP_FALLBACK."""
    if face in JP_FACES:
        sizes = JP_FACES[face][1]
        fits = [s for s in sizes if s[0] <= height + 1]
        return (fits[-1] if fits else sizes[0])[1]
    return next(p for h, p in JP_FALLBACK if height <= h)


def _jp_font(height, face="auto"):
    """The approved Japanese font for text about ``height`` px tall (loaded once), or None."""
    path = _jp_path(height, face)
    jp = _jp_fonts.get(path)
    if jp is None:
        jp = graphics.Font()
        try:
            jp.LoadFont(path)
        except Exception:
            return None
        _jp_fonts[path] = jp
    return jp


def with_japanese(f):
    """The font with a Japanese fallback of its own height attached (once; a font that has kana itself is left
    alone)."""
    if f is None or getattr(f, "fallback", None) is not None or 0x3042 in getattr(f, "glyphs", {0x3042: 1}):
        return f
    jp = _jp_font(f.height)
    if jp is not None:
        f.fallback = jp
        f._text_cache.clear()
    return f


_jp_sized: dict = {}


def jp_sized(f, height, face="auto"):
    """The Latin font f with Japanese the size of a ``height`` px Latin font (the caption's "Japanese text size"), in
    ``face`` (its "Japanese font"): a copy sharing f's glyphs, its lines tall enough for both (so only captions with
    Japanese use it)."""
    jp = _jp_font(height, face)
    if jp is None or jp is getattr(f, "fallback", None):
        return f
    key = (id(f), id(jp))
    hit = _jp_sized.get(key)
    if hit is not None and hit[0] is f:
        return hit[1]
    v = graphics.Font()
    v.glyphs, v.fallback = f.glyphs, jp
    asc, desc = max(f.baseline, jp.baseline), max(f.height - f.baseline, jp.height - jp.baseline)
    v.height, v.baseline = asc + desc, asc
    if len(_jp_sized) > 64:
        _jp_sized.clear()
    _jp_sized[key] = (f, v)
    return v


def has_cjk(ct) -> bool:
    """Whether the caption has any Japanese (or other CJK) text in it."""
    return any(ord(ch) >= 0x2E80 for s in (ct.title, *ct.sections.values(), *ct.name_more) for ch in (s or ""))


# --- the caption: rows of pieces ------------------------------------------------------------------------------------
# A caption is a box of rows; each row an ordered list of pieces: a section (the webcam's name line by line, the
# place, the time, the weather…, the LIVE badge) or a separator (overlay "rows"). Every piece can have its own size.
# A section with nothing to say right now drops out with the separator before it; a row too wide for the zone drops
# its last pieces (or, with "wrap", continues on further lines). Captions saved before rows existed get rows that
# draw what they drew (legacy_rows).
SIZES = {"tiny": "fonts/alert/tom-thumb.bdf", "small": "fonts/alert/5x8.bdf", "medium": "fonts/alert/6x10.bdf",
         "large": "fonts/alert/7x13B.bdf", "xlarge": "fonts/alert/9x15B.bdf", "huge": "fonts/alert/10x20.bdf"}
SEP_KINDS = {"space": "  ", "dot": "·", "dash": "-", "en": "–", "em": "—", "pipe": "|", "slash": "/"}
NAME_LINES = 4               # a webcam's name has up to this many lines, each its own piece ("name", "name:2", …)
MAX_ROWS = 8


def name_kind(i):
    return "name" if i <= 1 else f"name:{i}"


def legacy_rows(ov, ct):
    """The rows that draw what a caption saved before rows drew: LIVE and the headline (the name's first line, or the
    section chosen as headline) with the place beside it if asked, the name's further lines, then the details in
    their order joined by the separator (wrapping), or on the headline's row (inline), or one per row (stacked)."""
    show = ov.get("show") or {}
    sep_name = ov.get("separator") if ov.get("separator") in SEPARATORS else "space"
    sep = {"k": "sep", "s": sep_name, "pad": sep_name != "space"}
    mode = ov.get("lines") if ov.get("lines") in ("auto", "wrap", "stacked", "inline") else "auto"
    head = ct.headline_kind
    size = ov.get("font") if ov.get("font") in FONTS else "small"
    first = ([{"k": "live"}] if show.get("live") else []) + [{"k": head} if head == "name" else {"k": head, "size": size}]
    rows = [{"items": first}]
    beside = bool(ov.get("place_beside")) and mode != "inline" and head != "place" and show.get("place")
    if beside:                                             # (beside only when it fits; else it leads the details)
        first += [dict(sep), {"k": "place", "spill": True}]
    names = len(ct.name_lines)
    details = []
    if head == "name":
        if mode != "inline":
            rows += [{"items": [{"k": name_kind(i)}]} for i in range(2, names + 1)]
        else:
            details += [{"k": name_kind(i)} for i in range(2, names + 1)]
    earth_only = ("time", "date", "offset", "weather", "sun", "age") + WX_EXTRAS
    for k in ct.order:
        if k == "place" and beside or (k in SPACE if not ct.space else k in earth_only):
            continue
        if k == "name":
            if show.get("name"):
                details += [{"k": name_kind(i)} for i in range(1, names + 1)]
            continue
        if show.get(k):
            details.append({"k": k})
    if mode == "inline":
        for d in details:
            first += [dict(sep), {**d, "size": size}]
    elif mode == "stacked":
        rows += [{"items": [d]} for d in details[:4]]
    elif details:
        items = []
        for d in details:
            items += ([dict(sep)] if items else []) + [d]
        rows.append({"items": items, "wrap": True})
    return rows


def rows_of(ov, ct):
    """The caption's rows: its own ("rows"), or those of its older settings."""
    rows = ov.get("rows")
    if isinstance(rows, list) and rows:
        out = []
        for r in rows[:MAX_ROWS]:
            items = r.get("items") if isinstance(r, dict) else r
            if isinstance(items, list):
                out.append({"items": [i for i in items if isinstance(i, dict) and i.get("k")],
                            "wrap": bool(r.get("wrap")) if isinstance(r, dict) else False})
        return out
    return legacy_rows(ov, ct)


def _text(ct, kind):
    """A section's text now ("" when it has none)."""
    if kind == "name" or kind.startswith("name:"):
        i = 1 if kind == "name" else int(kind.split(":")[1])
        return ct.name_lines[i - 1] if 0 < i <= len(ct.name_lines) else ""
    if kind == "time":
        return ct.clock()
    if kind == ct.headline_kind and kind != "name":
        return ct.title
    return ct.sections.get(kind) or ""


def _sep_text(f, item):
    g = SEP_KINDS.get(item.get("s"), "  ")
    if len(g) == 1 and ord(g) not in getattr(f, "glyphs", {ord(g): 1}):
        g = "-"                                            # (a font without the dash: a hyphen)
    return g if g.strip() == "" or not item.get("pad") else f" {g} "


class _Piece:
    __slots__ = ("kind", "text", "f", "w", "badge", "sep", "icon", "spill", "sh")

    def __init__(self, kind, text, f, w, badge=False, sep=False, icon=False, spill=False):
        self.kind, self.text, self.f, self.w, self.badge, self.sep, self.icon = kind, text, f, w, badge, sep, icon
        self.spill = spill                                 # too wide for its row: moves to the start of the next
        self.sh = None                                     # a highway shield: its height (drawn, not text)


def _font_of(k, item, kind):
    """A piece's font: its own size, else the name's size for name lines (and a section chosen as headline), else the
    details' size."""
    size = item.get("size")
    if size in SIZES:
        return k.font_at(SIZES[size])
    if kind == "name" or kind.startswith("name:") or (kind == k.head_kind and kind != "name"):
        return k.T
    return k.S


def _pieces(k, ct, row):
    """The row's pieces now: empty sections left out, with the separator that led to them; no separator first, last
    or twice in a row."""
    out, pending = [], None
    for item in row["items"]:
        kind = str(item.get("k"))
        if kind == "sep":
            if out and pending is None:
                pending = item
            continue
        if kind == "live":
            if not ct.live:
                continue
            f = k.font_at(SIZES[item["size"]]) if item.get("size") in SIZES else k.S
            p = _Piece("live", "LIVE", f, _tw(f, "LIVE") + 4, badge=True)
        elif kind == "shield":                             # the highway's sign, as tall as LIVE (or its own size)
            route = ct.sections.get("shield") or ""
            if not route:
                continue
            f = k.font_at(SIZES[item["size"]]) if item.get("size") in SIZES else k.S
            skind = shield_spec(k.ov)[0]
            h = max(SHIELD_MIN, f.height + 1)
            p = _Piece("shield", route, f, _shield_dims(route, h, skind, shield_style(k.ov))[0], badge=True)
            p.sh = h
        else:
            text = _text(ct, kind)
            if not text:
                continue
            f = _font_of(k, item, kind)
            icon = kind == "weather" and bool(ct.icon)
            p = _Piece(kind, text, f, _tw(f, text) + (9 if icon else 0), icon=icon, spill=bool(item.get("spill")))
        if pending is not None and out:
            sf = k.font_at(SIZES[pending["size"]]) if pending.get("size") in SIZES else k.S
            st = _sep_text(sf, pending)
            out.append(_Piece("sep", st, sf, _tw(sf, st), sep=True))
        pending = None
        out.append(p)
    return out


def _gap(prev):
    return 3 if prev is not None and prev.badge else 0     # LIVE keeps a little room from what follows it


def _width(pieces):
    w, prev = 0, None
    for p in pieces:
        w += _gap(prev) + p.w
        prev = p
    return w


def _fit_row(pieces, room, wrap):
    """[lines]: the row as it fits ``room`` px. Too wide: its last pieces drop (with the separator before them); with
    wrap it continues on up to FIT_LINES lines first (a separator at a break is left out). A single piece too wide is
    cut to fit."""
    if not pieces:
        return []
    lines = []
    if wrap:
        cur = []
        for p in pieces:
            if cur and not p.sep and _width(cur + [p]) > room:
                while cur and cur[-1].sep:
                    cur.pop()
                lines.append(cur)
                cur = []
                if len(lines) >= FIT_LINES:
                    break
            if p.sep and not cur:
                continue
            cur.append(p)
        else:
            if cur:
                lines.append(cur)
    else:
        lines = [list(pieces)]
    out = []
    for ln in lines[:FIT_LINES if wrap else 1]:
        ln = _shrink(ln, room)
        if ln:
            out.append(ln)
    return out


def _shrink(ln, room):
    """The line within room: its last pieces drop until one text is left (separators go with what they led to), and
    that one is cut to fit."""
    ln = list(ln)
    while ln and _width(ln) > room:
        texts = [i for i, p in enumerate(ln) if not p.sep and not p.badge]
        if len(texts) > 1 or (not texts and len(ln) > 1) or (texts and texts[-1] < len(ln) - 1):
            ln.pop()
            while ln and ln[-1].sep:
                ln.pop()
            continue
        if not texts:
            break
        i = texts[-1]
        p = ln[i]
        avail = room - _width(ln[:i]) - _gap(ln[i - 1] if i else None) - (9 if p.icon else 0)
        t = _clip(p.f, p.text, max(0, avail))
        ln[i:] = [_Piece(p.kind, t, p.f, _tw(p.f, t) + (9 if p.icon else 0), icon=p.icon)] if t else []
        break
    return ln


def _line_metrics(line):
    """(height, baseline) of a line: its texts share a baseline; the LIVE badge is centred."""
    base = max([p.f.baseline for p in line if not p.badge] or [0])
    h = max([base - p.f.baseline + p.f.height for p in line if not p.badge] +
            [p.sh or p.f.height + 1 for p in line if p.badge])
    return h, base


def _l_tag(k, ct):
    """A box of rows (rows_of); an accent stripe on the inside edge."""
    c, pad = k.c, k.pad
    k.head_kind = ct.headline_kind
    edge = 1
    room = k.sized(10 ** 4) - 2 * pad - edge - 5
    lines, carry, everything = [], [], []
    for row in rows_of(k.ov, ct):
        ps = _pieces(k, ct, row)
        if carry:                                          # what didn't fit the row above (spill) leads this one
            ps = carry + ps if not ps else carry + [carry_sep] + ps if carry_sep else carry + ps
            carry = []
        everything += ps
        got = _fit_row(ps, room, row.get("wrap"))
        kept = {id(p) for ln in got for p in ln}
        for i, p in enumerate(ps):
            if p.spill and id(p) not in kept:
                carry.append(p)
                carry_sep = ps[i - 1] if i and ps[i - 1].sep else None
        lines += got
    if carry:                                              # nothing below: a line of its own
        lines += _fit_row(carry, room, False)
    shown = {p.kind for ln in lines for p in ln}
    _note_dropped([(p.kind, p.text) for p in everything if not p.sep], shown)
    if not lines:
        return
    widths = [_width(ln) for ln in lines]
    metrics = [_line_metrics(ln) for ln in lines]
    box_w = k.sized(pad * 2 + max(widths) + edge)
    inner = box_w - 2 * pad - edge
    box_h = pad * 2 + sum(h for h, _ in metrics) + len(lines) - 1
    bx, by = k.place(box_w, box_h)
    _box(c, bx, by, box_w, box_h, k.bg, k.op)
    c.FillRect(bx if k.align != "right" else bx + box_w - 1, by, 1, box_h, k.accent)
    x0 = bx + pad + (edge if k.align != "right" else 0)
    ty = by + pad
    for ln, lw, (lh, base) in zip(lines, widths, metrics):
        x, prev = k.line_x(x0, inner, lw), None
        for p in ln:
            x += _gap(prev)
            if p.sh:
                _put_shield(c, x, ty + (lh - p.sh) // 2, p.text, p.sh, shield_spec(k.ov)[0], shield_style(k.ov))
            elif p.badge:
                k.badge(x, ty + (lh - p.f.height - 1) // 2, font=p.f)
            else:
                tx = x
                if p.icon:
                    draw_icon(c, tx, ty + base - p.f.baseline + (p.f.height - 7) // 2, ct.icon)
                    tx += 9
                graphics.DrawText(c, p.f, tx, ty + base, k.text, p.text)
            x += p.w
            prev = p
        ty += lh + 1


FIELDS = ("shield", "live", "name", "place", "time", "age", "weather", "date", "offset", "sun", "custom", "credit") \
    + WX_EXTRAS
DEFAULT_SLOTS = {"shield": [1, 1], "live": [1, 1], "name": [1, 1], "place": [1, 2], "time": [1, 2], "age": [1, 2], "weather": [1, 3], "date": [1, 3],
                 "offset": [1, 3], "sun": [1, 3], "custom": [1, 3], "credit": [1, 3],
                 "conditions": [1, 3], "feels": [1, 3], "hilo": [1, 3], "wind": [1, 4], "humidity": [1, 4]}
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


SHIELD_STYLES = ("pixel", "real")
# where the hub puts the real signs (hub/shield_signs.py → data/shields/, synced to the Pi); the hub adds its own dir
SIGN_DIRS = [os.path.join(os.environ.get("BIGSCREEN_DATA_DIR", "/dev/shm/bigscreen"), "shields")]


def shield_style(ov):
    """pixel (the LED pixel art, display/modules/shields.py) or real (the sign itself, scaled)."""
    s = ov.get("shield") if isinstance(ov.get("shield"), dict) else {}
    return "real" if s.get("style") == "real" else "pixel"


def real_sign(route, kind, h):
    """The route's real sign h px tall as (h, w, 4) uint8 RGBA, or None until the hub has rendered it."""
    from display.modules import shields
    name = shields.sign_file(route, kind)
    if not name:
        return None
    for d in SIGN_DIRS:
        path = os.path.join(d, shields.sign_slug(name) + ".png")
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            continue
        try:
            return _scaled_sign(path, mtime, max(SHIELD_MIN, min(SHIELD_MAX, int(h))))
        except (OSError, ValueError):
            return None
    return None


@lru_cache(maxsize=64)
def _scaled_sign(path, mtime, h):
    """Scaled once per size (and again if the hub re-renders it); never per frame. Raises on an unreadable file,
    so a failure isn't cached."""
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    w = max(1, round(im.width * h / im.height))
    return np.asarray(im.resize((w, h), Image.LANCZOS), dtype=np.uint8)


def _shield_dims(route, h, kind=None, style="pixel"):
    """(width, height) of the shield drawn for a height of h px."""
    if style == "real":
        sign = real_sign(route, kind, h)
        if sign is not None:
            return sign.shape[1], sign.shape[0]
    from display.modules import shields
    rgb = shields._sprite(kind or shields.kind_for(route), max(SHIELD_MIN, h))[0]
    return rgb.shape[1], rgb.shape[0]


def _put_shield(c, x, y, route, h, kind=None, style="pixel"):
    """Draw the shield: the real sign blended by its alpha (soft edges), else the pixel art. Clipped to the canvas
    (and to a _Zone's box when c is one)."""
    sign = real_sign(route, kind, h) if style == "real" else None
    if sign is None:
        from display.modules import shields
        return shields.draw(c, x, y, route, h=max(SHIELD_MIN, h), kind=kind)
    sh, sw = sign.shape[:2]
    x0, y0 = max(x, getattr(c, "x0", 0)), max(y, getattr(c, "y0", 0))
    x1, y1 = min(x + sw, getattr(c, "x1", c.width)), min(y + sh, getattr(c, "y1", c.height))
    if x1 > x0 and y1 > y0:
        src = sign[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.uint16)
        a = src[..., 3:4]
        dst = c.buf[y0:y1, x0:x1]
        dst[:] = ((src[..., :3] * a + dst.astype(np.uint16) * (255 - a)) // 255).astype(np.uint8)
        if hasattr(c, "mark"):
            c.mark(x0, y0, x1, y1)
    return sw


LAYOUT_FUNCS = {"tag": _l_tag, "none": lambda k, ct: None}    # none: no caption (the Japanese one can still show)
