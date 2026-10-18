"""Live webcam video (control panel → Webcams).

The hub decodes the stream (hub/webcams.py) and sends frames already cropped and scaled to the zone, so the Pi
only copies them to the canvas: no decoding here, ever. A reader thread per playing webcam keeps the newest
frame; it hangs up a few seconds after the screen stops drawing it, and the hub then stops ffmpeg.

On top goes a caption saying what the feed is and where it comes from, styled per webcam (``overlay``):
    style    tag | bar | outline | dot | none
    corner   tl | tr | bl | br                (bar: top or bottom edge)
    show     name, place, time, live, credit  (booleans)
    when     always | intro                   (intro: the first INTRO_SEC of each play)
    font     tiny | small | medium | large
    accent, text                              "#rrggbb"
"""

from __future__ import annotations

import http.client
import os
import threading
import time
from datetime import datetime
from urllib.parse import urlencode, urlparse

import numpy as np

from display.gfx import Color, graphics

DEFAULT_HUB = "http://192.168.1.111:8800"
IDLE_SEC = 3.0               # hang up when the screen hasn't drawn this webcam for this long
GIVE_UP_SEC = 25.0           # no picture this long after the play started: skip to the next screen
INTRO_SEC = 10.0
MAGIC = b"BSWC"

FONTS = {"tiny": "fonts/alert/tom-thumb.bdf", "small": "fonts/alert/5x8.bdf", "medium": "fonts/alert/6x10.bdf",
         "large": "fonts/alert/7x13B.bdf"}
STYLES = ("tag", "bar", "outline", "dot", "none")
DEFAULT_OVERLAY = {"style": "tag", "corner": "bl", "when": "always", "font": "small", "accent": "#e8202a",
                   "text": "#ffffff", "show": {"name": True, "place": True, "time": True, "live": True, "credit": False}}


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


def stream_query(cam: dict, shape: str, w: int, h: int) -> dict:
    """What the hub needs to make this zone's frames (hub/app.py /api/webcams/frames)."""
    src = cam.get("source") or {}
    q = {"type": src.get("type", "stream"), "url": src.get("url", ""), "fps": int(cam.get("fps", 15)),
         "w": int(w), "h": int(h)}
    crop = (cam.get("crops") or {}).get(shape)
    if isinstance(crop, dict) and crop.get("w"):
        q.update(x=round(float(crop.get("x", 0)), 5), y=round(float(crop.get("y", 0)), 5),
                 cw=round(float(crop["w"]), 5))
    col = cam.get("colour") or {}
    for k, key in (("b", "brightness"), ("c", "contrast"), ("s", "saturation")):
        if col.get(key) not in (None, 1, 1.0):
            q[k] = float(col[key])
    return q


class Stream:
    """Reads one frame stream from the hub on its own thread; ``frame`` is the newest (h, w, 3) uint8."""

    def __init__(self, hub_url: str, query: dict):
        self.hub = urlparse(hub_url)
        self.path = "/api/webcams/frames?" + urlencode(query)
        self.w, self.h = int(query["w"]), int(query["h"])
        self.frame = None
        self.frame_t = 0.0
        self.error = ""
        self.last_use = time.time()
        self.alive = True
        self._conn = None
        threading.Thread(target=self._run, name="webcam-reader", daemon=True).start()

    def used(self):
        self.last_use = time.time()

    def close(self):
        self.alive = False
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:
            pass

    def _run(self):
        try:
            os.sched_setaffinity(0, {0, 1})        # off the render core (2) and rgbmatrix's (3)
        except (AttributeError, OSError):
            pass
        while self.alive and time.time() - self.last_use < IDLE_SEC:
            try:
                self._read()
            except Exception as e:
                self.error = str(e)[:200] or type(e).__name__
            if self.alive and time.time() - self.last_use < IDLE_SEC:
                time.sleep(2.0)
        self.alive = False
        self.close()

    def _read(self):
        conn = http.client.HTTPConnection(self.hub.hostname, self.hub.port or 80, timeout=30)
        self._conn = conn
        conn.request("GET", self.path, headers={"User-Agent": "bigscreen-display"})
        r = conn.getresponse()
        if r.status != 200:
            body = r.read(400).decode("utf-8", "replace")
            raise RuntimeError(f"hub {r.status}: {body}")
        n = self.w * self.h * 3
        while self.alive and time.time() - self.last_use < IDLE_SEC:
            head = r.read(12)
            if len(head) < 12 or head[:4] != MAGIC:
                raise RuntimeError("stream ended")
            w, h = int.from_bytes(head[4:6], "big"), int.from_bytes(head[6:8], "big")
            if (w, h) != (self.w, self.h):
                raise RuntimeError(f"hub sent {w}x{h}, wanted {self.w}x{self.h}")
            body = r.read(n)
            if len(body) < n:
                raise RuntimeError("stream ended")
            self.frame = np.frombuffer(body, np.uint8).reshape(h, w, 3)
            self.frame_t = time.time()
            self.error = ""


class WebcamPanel:
    """Draws the ``webcam`` (one side), ``webcam_full`` (full width) and ``webcam_wall`` (whole wall) entries."""

    def __init__(self, cfg):
        s = cfg.modules.get("webcams") or {}
        self.cams = s.get("cams") or {}
        self.hub = s.get("hub_url") or DEFAULT_HUB
        self.streams: dict = {}
        self.started: dict = {}
        self.fonts: dict = {}
        self.white = Color(255, 255, 255)
        self.dim = Color(150, 150, 150)

    # streams ----------------------------------------------------------------------------------------------
    def _stream(self, cam, shape, w, h):
        q = stream_query(cam, shape, w, h)
        key = tuple(sorted(q.items()))
        st = self.streams.get(key)
        if st is None or not st.alive:
            st = self.streams[key] = Stream(self.hub, q)
        st.used()
        for k, other in list(self.streams.items()):          # hang up on ones nobody drew lately
            if not other.alive or time.time() - other.last_use > IDLE_SEC:
                other.close()
                self.streams.pop(k, None)
        return st

    def font(self, size):
        f = self.fonts.get(size)
        if f is None:
            f = graphics.Font()
            try:
                f.LoadFont(FONTS.get(size, FONTS["small"]))
            except Exception:
                f.LoadFont(FONTS["small"])
            self.fonts[size] = f
        return f

    # drawing -----------------------------------------------------------------------------------------------
    @staticmethod
    def shape_for(entry, zone):
        p = entry.get("panel", "webcam")
        if p == "webcam_wall":
            return "wall"
        if p == "webcam_full":
            return "full"
        return "left" if zone[0] == 0 else "right"

    def draw(self, c, zone, entry, transition):
        x, y, w, h = zone
        now = time.time()
        key = (entry.get("panel"), entry.get("cam"))
        if transition or key not in self.started:
            self.started[key] = now
        started = self.started[key]
        cam = self.cams.get(entry.get("cam"))
        if not cam:
            self._centre(c, zone, f"No webcam '{entry.get('cam')}'", self.dim)
            return "skip" if now - started > 4 else None
        st = self._stream(cam, self.shape_for(entry, zone), w, h)
        if st.frame is not None and now - st.frame_t < 10:
            c.Blit(st.frame, x, y)
        else:
            c.FillRect(x, y, w, h, Color(0, 0, 0))
            label = cam.get("label") or entry.get("cam")
            self._centre(c, zone, f"Tuning in to {label}" + "." * (1 + int(now * 2) % 3), self.dim)
            if st.error:
                self._centre(c, (x, y + 12, w, h), st.error[: max(8, w // 5)], Color(120, 60, 60))
            if now - started > GIVE_UP_SEC:
                return "skip"
        draw_caption(c, zone, cam, now - started, self.font)
        return None

    def _centre(self, c, zone, text, color):
        x, y, w, h = zone
        f = self.font("small")
        tw = sum(max(0, f.CharacterWidth(ord(ch))) for ch in text)
        graphics.DrawText(c, f, x + max(2, (w - tw) // 2), y + h // 2, color, text)


# --- caption ----------------------------------------------------------------------------------------------------
def _tw(font, s):
    return sum(max(0, font.CharacterWidth(ord(ch))) for ch in s)


def _shade(c, x, y, w, h, k):
    """Darken a box of the frame (the caption's see-through background)."""
    x0, y0, x1, y1 = max(0, x), max(0, y), min(c.width, x + w), min(c.height, y + h)
    if x1 > x0 and y1 > y0:
        box = c.buf[y0:y1, x0:x1]
        box[:] = (box.astype(np.uint16) * int(k * 256) >> 8).astype(np.uint8)


def _outlined(c, font, x, y, color, s):
    black = Color(0, 0, 0)
    for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1), (0, -1)):
        graphics.DrawText(c, font, x + dx, y + dy, black, s)
    return graphics.DrawText(c, font, x, y, color, s)


def caption_lines(cam, ov, now_local=None):
    """(title, detail bits): 'Shibuya Crossing' and [('place', 'Tokyo, Japan'), ('time', '04:12'), ('credit', 'FNN')]."""
    sh = ov["show"]
    title = (cam.get("label") or "") if sh.get("name") else ""
    bits = []
    if sh.get("place") and cam.get("place"):
        bits.append(("place", cam["place"]))
    if sh.get("time"):
        t = now_local or _local_time(cam.get("tz"))
        bits.append(("time", t.strftime("%H:%M")))
    if sh.get("credit") and cam.get("credit"):
        bits.append(("credit", cam["credit"]))
    return title, bits


def _clip(font, s, width):
    while s and _tw(font, s) > width:
        s = s[:-1]
    return s.rstrip(" ,·-")


def fit_detail(font, bits, width):
    """The detail line within ``width`` px: drops the credit, then the place, before cutting text."""
    sep = " · " if 0xB7 in getattr(font, "glyphs", {0xB7: 1}) else " - "
    bits = list(bits)
    line = sep.join(v for _, v in bits)
    for drop in ("credit", "place"):
        if _tw(font, line) <= width:
            break
        bits = [b for b in bits if b[0] != drop] or bits
        line = sep.join(v for _, v in bits)
    return _clip(font, line, width)


def draw_caption(c, zone, cam, elapsed, font_for, now_local=None):
    ov = overlay_of(cam)
    style = ov.get("style", "tag")
    if style == "none" or (ov.get("when") == "intro" and elapsed > INTRO_SEC):
        return
    x, y, w, h = zone
    font = font_for(ov.get("font", "small"))
    small = font_for("tiny") if ov.get("font") in ("medium", "large") else font
    accent = Color(*_hex(ov.get("accent"), (232, 32, 42)))
    text = Color(*_hex(ov.get("text"), (255, 255, 255)))
    corner = ov.get("corner", "bl")
    top, left = corner[0] == "t", corner[1] == "l"
    live = ov["show"].get("live")
    title, bits = caption_lines(cam, ov, now_local)
    blink = int(time.time() * 1.25) % 2 == 0

    if style == "dot":
        r = 2
        cx = x + 4 if left else x + w - 5
        cy = y + 4 if top else y + h - 5
        if blink:
            c.FillRect(cx - r + 1, cy - r, 2 * r - 1, 2 * r + 1, accent)
            c.FillRect(cx - r, cy - r + 1, 2 * r + 1, 2 * r - 1, accent)
        if live:
            lw = _tw(small, "LIVE")
            lx = cx + 5 if left else cx - 5 - lw
            _outlined(c, small, lx, cy - small.height // 2 + small.baseline, text, "LIVE")
        return

    pad = 2
    lh = font.height
    badge = "LIVE" if live else ""
    bw = _tw(small, badge) + 4 if badge else 0
    room = w - 2 * pad - 3 - (bw + 3 if bw else 0)
    title = _clip(font, title, room)
    detail = fit_detail(small, bits, room)
    dh = small.height if detail else 0
    gap = 1 if (title and detail) else 0
    tw_ = max(_tw(font, title), _tw(small, detail))
    box_w = min(w, pad * 2 + (bw + 3 if bw else 0) + tw_)
    box_h = pad * 2 + (lh if title else 0) + gap + dh
    if not title and not detail and not badge:
        return

    if style == "bar":
        bx, bw_full = x, w
        by = y if top else y + h - box_h
        _shade(c, bx, by, bw_full, box_h, 0.3)
        c.FillRect(bx, by + box_h - 1 if top else by, bw_full, 1, accent)
        cx = bx + pad
        if badge:
            c.FillRect(cx, by + pad, bw, small.height + 1, accent)
            graphics.DrawText(c, small, cx + 2, by + pad + small.baseline, Color(255, 255, 255), badge)
            cx += bw + 3
        ty = by + pad
        if title:
            graphics.DrawText(c, font, cx, ty + font.baseline, text, title)
            ty += lh + gap
        if detail:
            graphics.DrawText(c, small, cx, ty + small.baseline, text, detail)
        return

    bx = x + 1 if left else x + w - box_w - 1
    by = y + 1 if top else y + h - box_h - 1
    if style == "tag":
        _shade(c, bx, by, box_w, box_h, 0.25)
        c.FillRect(bx if left else bx + box_w - 1, by, 1, box_h, accent)
    cx = bx + pad + (1 if style == "tag" and left else 0)
    ty = by + pad
    put = _outlined if style == "outline" else graphics.DrawText
    if badge:
        c.FillRect(cx, ty, bw, small.height + 1, accent)
        graphics.DrawText(c, small, cx + 2, ty + small.baseline, Color(255, 255, 255), badge)
        cx += bw + 3
    if title:
        put(c, font, cx, ty + font.baseline, text, title)
        ty += lh + gap
    if detail:
        put(c, small, cx, ty + small.baseline, text, detail)


def render_preview(cam: dict, frame, shape_zone, elapsed=0.0) -> np.ndarray:
    """The wall's pixels for one zone: a frame already cropped to the zone, plus the caption (control panel)."""
    from display.gfx import Canvas
    w, h = shape_zone
    c = Canvas(w, h)
    if frame is not None:
        c.Blit(frame, 0, 0)
    fonts = {}

    def font_for(size):
        f = fonts.get(size)
        if f is None:
            f = fonts[size] = graphics.Font()
            f.LoadFont(FONTS.get(size, FONTS["small"]))
        return f

    draw_caption(c, (0, 0, w, h), cam, elapsed, font_for)
    return c.buf.copy()
