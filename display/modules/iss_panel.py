"""The Space Map (the ISS map as its own rotation entry; control panel → Space Map): the full ISS map (display/modules/issmap.py) with
its caption, on one side (``iss_map``), the full width (``iss_map_full``), above the clock (``iss_map_tall``) or the
whole wall (``iss_map_wall``). It plays for the entry's own seconds.

Settings: ``modules.iss_map``, shaped like a webcam in space (``label``, ``overlay`` for the caption, ``map`` with
``fixed`` and ``extras``), so the webcam caption and the map draw it as they do for the ISS webcam. Until it's been
saved once, the first webcam in space lends its caption and map settings.

Satellites Overhead (``sats_overhead*``, settings ``modules.sats_overhead``, the same shape plus ``map.overhead``:
issmap.overhead_of) is the same map held still over the wall's home, zoomed out just far enough to show the horizon
of its layer (Starlink by default): what could be seen in the sky from home right now. Home: ``home`` [lat, lon] in
its settings, else data/issmap/overhead.json (hub/jobs/sats.py writes the weather alerts' home there)."""

from __future__ import annotations

import json
import os
import time

from display.gfx import graphics
from display.modules.webcam_caption import FONTS, draw_caption, for_shape, shape_of_panel

DEFAULT_HUB = "http://192.168.1.111:8800"


OVERHEAD = "sats_overhead"
OVERHEAD_LABEL = "Satellites overhead"


def settings_of(modules, key="iss_map") -> dict:
    """modules.iss_map (or modules.sats_overhead) as a webcam-shaped dict (space: iss, its map always on), or the first
    ISS webcam's settings."""
    modules = modules or {}
    s = modules.get(key) if isinstance(modules.get(key), dict) else None
    if not s and key == OVERHEAD:                         # (until it's saved: the Space Map's look, a one-line caption)
        sm = modules.get("iss_map") if isinstance(modules.get("iss_map"), dict) else {}
        s = {"label": OVERHEAD_LABEL, "overlay": {**(sm.get("overlay") or {}), "rows": [{"items": [{"k": "name"}]}]},
             "map": {k: v for k, v in (sm.get("map") or {}).items() if k in ("sats", "links", "labels", "names", "units",
                                                                             "icons", "relays", "ground")}}
    if not s:
        cams = ((modules.get("webcams") or {}).get("cams") or {})
        cam = next((c for c in cams.values() if isinstance(c, dict) and c.get("space")), None) or {}
        s = {"label": cam.get("label") or "International Space Station", "overlay": cam.get("overlay") or {},
             "overlays": cam.get("overlays") or {}, "map": cam.get("map") or {}}
    out = {**s, "space": "iss"}
    out["map"] = {**(s.get("map") if isinstance(s.get("map"), dict) else {}), "when_down": True}
    return out


def home_of(settings, data_dir=None):
    """(lat, lon) Satellites Overhead is centred on: its own ``home``, else the hub's (data/issmap/overhead.json)."""
    h = (settings or {}).get("home")
    if isinstance(h, (list, tuple)) and len(h) == 2:
        try:
            return float(h[0]), float(h[1])
        except (TypeError, ValueError):
            pass
    try:
        with open(os.path.join(data_dir or issmap_dir(), "issmap", "overhead.json"), encoding="utf-8") as f:
            la, lo = json.load(f)["home"]
        return float(la), float(lo)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def issmap_dir():
    from display.modules import issmap
    return issmap.DATA_DIR


def overhead_mapset(settings, home, w, h):
    """(mapset, view) for Satellites Overhead at w×h: the map's settings with home in them, and the view on home."""
    from display.modules import issmap
    mp = {**(settings.get("map") or {}), "_home": home}
    return mp, issmap.overhead_view(home, w, h, issmap.overhead_of(mp))


class IssMapPanel:
    def __init__(self, cfg, data_dir=None, key="iss_map"):
        mods = getattr(cfg, "modules", None) or {}
        self.key = key
        self.cam = settings_of(mods, key)
        self.hub = ((mods.get("webcams") or {}).get("hub_url")) or DEFAULT_HUB
        self.data_dir = data_dir
        if data_dir:
            from display.modules import issmap
            issmap.DATA_DIR = data_dir
        self.fonts, self.started, self.boxes = {}, {}, {}
        self._iss, self._mtime, self._checked = None, None, 0.0
        from common import webcam_globals                    # (the small video plays with the settings for every webcam)
        self.cams = webcam_globals.cams_of(mods.get("webcams"))
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
        mapset = {**self.cam["map"], "_video_up": frame is not None}
        view, fixed = issmap.view_of(self.cam, shape), bool(self.cam["map"].get("fixed"))
        if self.key == OVERHEAD:
            home = self._home(now)
            if home is None:
                return None
            mapset, view = overhead_mapset({**self.cam, "map": mapset}, home, zone[2], zone[3])
            fixed = True
        paths = issmap.draw_full(c, zone, data, now - space.LAG, fixed=fixed,
                                 caption_box=self.boxes.get(key), extras=issmap.extras_of(self.cam, space_map=True),
                                 live=issmap.live_from(self.hub, mappanels.history_keys(self.cam["map"])),
                                 mapset=mapset, view=view)
        if frame is not None:
            box = issmap.video_box(paths)
            if box:
                x, y = zone[0], zone[1]
                issmap.draw_video(c.buf[max(0, y):y + zone[3], max(0, x):x + zone[2]], box, frame)
        cam = for_shape(self.cam, shape)                                       # the caption settings of its size
        self.boxes[key] = draw_caption(c, zone, cam, now - self.started[key], self.font, extra={"space": data})
        return None

    def _home(self, now):
        if now - getattr(self, "_home_at", -1e9) > 60:                # (checked a minute at a time, found or not)
            self._home_at, self._home_v = now, home_of(self.cam, self.data_dir)
        return self._home_v

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


def render_preview(settings, shape, size, data, live, elapsed=30.0, video=None, home=None):
    """The Space Map as the wall draws it at a size (shape: left | right | full | tall | wall; size: (w, h)) for the
    control panel: the map with its caption, the moving data where it is now. home (lat, lon): Satellites Overhead."""
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
    mapset, view = {**settings["map"], "_video_up": video is not None}, issmap.view_of(settings, shape)
    if home is not None:
        mapset, view = overhead_mapset({**settings, "map": mapset}, home, w, h)
    img, paths = issmap.render(w, h, data, now - space.LAG, True, home is not None or bool(settings["map"].get("fixed")),
                               issmap.reserve_for(box, h), ex, live, mapset, view, tuple(box) if box else None)
    c.Blit(img, 0, 0)
    issmap.flow(c.buf, paths, now, ex, w >= issmap.BIG_W)
    issmap.draw_video(c.buf, issmap.video_box(paths), video)
    draw_caption(c, (0, 0, w, h), cam, elapsed, font_for, extra={"space": data})
    return c.buf.copy()
