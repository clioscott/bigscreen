"""The Space Map (the ISS map as its own rotation entry; control panel → Space Map): the full ISS map (display/modules/issmap.py) with
its caption, on one side (``iss_map``), the full width (``iss_map_full``), above the clock (``iss_map_tall``) or the
whole wall (``iss_map_wall``). It plays for the entry's own seconds.

Settings: ``modules.iss_map``, shaped like a webcam in space (``label``, ``overlay`` for the caption, ``map`` with
``fixed`` and ``extras``), so the webcam caption and the map draw it as they do for the ISS webcam. Until it's been
saved once, the first webcam in space lends its caption and map settings."""

from __future__ import annotations

import json
import os
import time

from display.gfx import graphics
from display.modules.webcam_caption import FONTS, draw_caption, for_shape, shape_of_panel

DEFAULT_HUB = "http://192.168.1.111:8800"


def settings_of(modules) -> dict:
    """modules.iss_map as a webcam-shaped dict (space: iss, its map always on), or the first ISS webcam's settings."""
    modules = modules or {}
    s = modules.get("iss_map") if isinstance(modules.get("iss_map"), dict) else None
    if not s:
        cams = ((modules.get("webcams") or {}).get("cams") or {})
        cam = next((c for c in cams.values() if isinstance(c, dict) and c.get("space")), None) or {}
        s = {"label": cam.get("label") or "International Space Station", "overlay": cam.get("overlay") or {},
             "overlays": cam.get("overlays") or {}, "map": cam.get("map") or {}}
    out = {**s, "space": "iss"}
    out["map"] = {**(s.get("map") if isinstance(s.get("map"), dict) else {}), "when_down": True}
    return out


class IssMapPanel:
    def __init__(self, cfg, data_dir=None):
        mods = getattr(cfg, "modules", None) or {}
        self.cam = settings_of(mods)
        self.hub = ((mods.get("webcams") or {}).get("hub_url")) or DEFAULT_HUB
        self.data_dir = data_dir
        if data_dir:
            from display.modules import issmap
            issmap.DATA_DIR = data_dir
        self.fonts, self.started, self.boxes = {}, {}, {}
        self._iss, self._mtime, self._checked = None, None, 0.0
        self.cams = (mods.get("webcams") or {}).get("cams") or {}
        self.stream = None                                    # the small live video's (display/modules/webcam.Stream)

    def font(self, size):
        f = self.fonts.get(size)
        if f is None:
            f = graphics.Font()
            try:
                f.LoadFont(FONTS.get(size, size))
            except Exception:
                f.LoadFont(FONTS["small"])
            self.fonts[size] = f
        return f

    def _space(self, now):
        """data/iss.json, re-read when it changes (checked every 5 s)."""
        if self.data_dir and now - self._checked > 5:
            self._checked = now
            path = os.path.join(self.data_dir, "iss.json")
            try:
                mt = os.stat(path).st_mtime
                if mt != self._mtime:
                    with open(path, encoding="utf-8") as f:
                        self._iss = json.load(f)
                    self._mtime = mt
            except (OSError, ValueError):
                pass
        return self._iss

    def draw(self, c, zone, entry, transition):
        from display.modules import issmap, mappanels, space
        now = time.time()
        key = (entry.get("panel"), tuple(zone))
        if transition or key not in self.started:
            self.started[key] = now
        data = self._space(now)
        shape = shape_of_panel(entry.get("panel"), zone)
        frame = self._video(data, now)
        paths = issmap.draw_full(c, zone, data, now - space.LAG, fixed=bool(self.cam["map"].get("fixed")),
                                 caption_box=self.boxes.get(key), extras=issmap.extras_of(self.cam, space_map=True),
                                 live=issmap.live_from(self.hub, mappanels.history_keys(self.cam["map"])),
                                 mapset={**self.cam["map"], "_video_up": frame is not None},
                                 view=issmap.view_of(self.cam, shape))
        if frame is not None:
            box = issmap.video_box(paths)
            if box:
                x, y = zone[0], zone[1]
                issmap.draw_video(c.buf[max(0, y):y + zone[3], max(0, x):x + zone[2]], box, frame)
        cam = for_shape(self.cam, shape)                                       # the caption settings of its size
        self.boxes[key] = draw_caption(c, zone, cam, now - self.started[key], self.font, extra={"space": data})
        return None


    def video_cam(self):
        return video_cam(self.cam, self.cams)

    def _video(self, data, now):
        """The small live video's frame now (map.video), or None when it's off or has nothing worth showing: the ISS
        in Earth's shadow (with the webcam's daylight_only), still loading, black or the loss-of-signal card, frozen,
        or stalled. Its stream opens shortly before sunrise and closes when it isn't wanted."""
        from display.modules import mappanels, space, webcam
        vid = mappanels.video_of(self.cam["map"])
        cam = self.video_cam() if vid.get("on") else None
        lit = True
        if cam and cam.get("daylight_only", True):
            t = now - space.LAG
            lit = space.in_daylight(data, t, t) is not False
            soon = lit or space.in_daylight(data, t + webcam.STREAM_AHEAD_SEC, t + webcam.STREAM_AHEAD_SEC) is not False
            if not soon:
                cam = None
        if cam is None:
            if self.stream is not None:
                self.stream.close()
                self.stream = None
            return None
        w, h = mappanels.video_size(vid)
        q = webcam.stream_query(cam, "full", w, h)
        if self.stream is None or not self.stream.alive or self.stream.query != q:
            if self.stream is not None:
                self.stream.close()
            self.stream = webcam.Stream(self.hub, q)
        st = self.stream
        st.used()
        if not lit or webcam.WebcamPanel._unusable(st, now):
            return None
        frame = st.current(webcam.CLOCK["tick"] or now)
        if getattr(st, "video", True):
            webcam.CLOCK["video_at"] = now                     # (main.py: the loop runs at the video rate meanwhile)
        return frame


def video_cam(settings, cams):
    """The webcam the Space Map's small video shows: map.video.cam, or the first webcam in space."""
    want = (((settings or {}).get("map") or {}).get("video") or {}).get("cam")
    cams = cams if isinstance(cams, dict) else {}
    if want and isinstance(cams.get(want), dict):
        return cams[want]
    return next((c for c in cams.values() if isinstance(c, dict) and c.get("space")), None)


def render_preview(settings, shape, size, data, live, elapsed=30.0, video=None):
    """The Space Map as the wall draws it at a size (shape: left | right | full | tall | wall; size: (w, h)) for the
    control panel: the map with its caption, the moving data where it is now."""
    from display.gfx import Canvas
    from display.modules import issmap, space
    w, h = size
    cam = for_shape(settings, shape)
    fonts = {}

    def font_for(sz):
        f = fonts.get(sz)
        if f is None:
            f = fonts[sz] = graphics.Font()
            try:
                f.LoadFont(FONTS.get(sz, sz))
            except Exception:
                f.LoadFont(FONTS["small"])
        return f

    box = draw_caption(Canvas(w, h), (0, 0, w, h), cam, elapsed, font_for, extra={"space": data})   # (where it goes)
    c = Canvas(w, h)
    ex = issmap.extras_of(settings, space_map=True)
    now = time.time()
    img, paths = issmap.render(w, h, data, now - space.LAG, True, bool(settings["map"].get("fixed")),
                               issmap.reserve_for(box, h), ex, live, {**settings["map"], "_video_up": video is not None},
                               issmap.view_of(settings, shape), tuple(box) if box else None)
    c.Blit(img, 0, 0)
    issmap.flow(c.buf, paths, now, ex, w >= issmap.BIG_W)
    issmap.draw_video(c.buf, issmap.video_box(paths), video)
    draw_caption(c, (0, 0, w, h), cam, elapsed, font_for, extra={"space": data})
    return c.buf.copy()
