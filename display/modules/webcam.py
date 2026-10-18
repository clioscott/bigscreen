"""Live webcam video (control panel → Webcams).

The hub decodes the stream (hub/webcams.py) and sends frames already cropped and scaled to the zone, so the Pi
only copies them to the canvas: no decoding here, ever. A reader thread per playing webcam keeps the newest
frame; it hangs up a few seconds after the screen stops drawing it, and the hub then stops ffmpeg.

On top goes a caption saying what the feed is and where it comes from, styled per webcam (``overlay``): see
display/modules/webcam_caption.py for the sections and layouts.
"""

from __future__ import annotations

import http.client
import os
import socket
import threading
import time
from urllib.parse import urlencode, urlparse

import numpy as np

from display.gfx import Color, graphics
from display.modules.webcam_caption import (DEFAULT_OVERLAY, FONTS, INTRO_SEC, LAYOUTS, STYLES,  # noqa: F401
                                            _outlined, _shade, _tw, caption_lines, draw_caption,
                                            fit_detail, overlay_of)

DEFAULT_HUB = "http://192.168.1.111:8800"
IDLE_SEC = 3.0               # hang up when the screen hasn't drawn this webcam for this long
GIVE_UP_SEC = 25.0           # no picture this long after the play started: skip to the next screen
MAGIC = b"BSWC"


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
        self._sock = None                      # our own reference: http.client drops conn.sock once a response owns it
        threading.Thread(target=self._run, name="webcam-reader", daemon=True).start()

    def used(self):
        self.last_use = time.time()

    def close(self):
        """Safe from the render thread: never waits for the reader. Closing the connection would block on the
        reader's buffered read (up to the socket timeout), so shut the socket down instead; the read then fails
        at once and the reader thread closes the rest itself."""
        self.alive = False
        sock = self._sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
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
            finally:
                conn, self._conn, self._sock = self._conn, None, None
                if conn is not None:
                    try:
                        conn.close()                   # on the reader's own thread: nothing else waits on it
                    except Exception:
                        pass
            if self.alive and time.time() - self.last_use < IDLE_SEC:
                time.sleep(2.0)
        self.alive = False

    def _read(self):
        conn = http.client.HTTPConnection(self.hub.hostname, self.hub.port or 80, timeout=30)
        self._conn = conn
        conn.connect()
        self._sock = conn.sock
        if not self.alive:                     # closed while connecting
            return
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

    def __init__(self, cfg, data_dir=None):
        s = cfg.modules.get("webcams") or {}
        self.data_dir = data_dir
        self.weather, self._wx_mtime, self._wx_checked = {}, None, 0.0
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
        """A font by size name (tiny…large) or by path, cached."""
        f = self.fonts.get(size)
        if f is None:
            f = graphics.Font()
            try:
                f.LoadFont(FONTS.get(size, size))
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
            if now - max(started, st.frame_t) > GIVE_UP_SEC:      # a hiccup mid-play doesn't end a held webcam
                return "skip"
        draw_caption(c, zone, cam, now - started, self.font, wx=self._weather(now).get(entry.get("cam")))
        return None

    def _weather(self, now):
        """data/webcam_weather.json (hub/jobs/webcam_weather.py), re-read when it changes (checked every 5 s)."""
        if self.data_dir and now - self._wx_checked > 5:
            self._wx_checked = now
            path = os.path.join(self.data_dir, "webcam_weather.json")
            try:
                mt = os.stat(path).st_mtime
                if mt != self._wx_mtime:
                    import json
                    with open(path, encoding="utf-8") as f:
                        self.weather = json.load(f) or {}
                    self._wx_mtime = mt
            except (OSError, ValueError):
                pass
        return self.weather

    def _centre(self, c, zone, text, color):
        x, y, w, h = zone
        f = self.font("small")
        tw = sum(max(0, f.CharacterWidth(ord(ch))) for ch in text)
        graphics.DrawText(c, f, x + max(2, (w - tw) // 2), y + h // 2, color, text)


_preview_fonts: dict = {}


def _preview_font(size):
    f = _preview_fonts.get(size)
    if f is None:
        f = _preview_fonts[size] = graphics.Font()
        f.LoadFont(FONTS.get(size, size))
    return f


def caption_dropped(cam: dict, shape_zone, wx=None) -> list:
    """Sections turned on that the caption can't fit at this size (the control panel lists them)."""
    from display.modules.webcam_caption import dropped_sections
    return dropped_sections(cam, shape_zone, _preview_font, wx)


def render_preview(cam: dict, frame, shape_zone, elapsed=0.0, wx=None) -> np.ndarray:
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
            f.LoadFont(FONTS.get(size, size))
        return f

    draw_caption(c, (0, 0, w, h), cam, elapsed, font_for, wx=wx)
    return c.buf.copy()
