"""Live webcam video (control panel → Webcams).

The hub decodes the stream (hub/webcams.py) and sends frames already cropped and scaled to the zone, so the Pi
only copies them to the canvas: no decoding here, ever. A reader thread per playing webcam keeps the newest
frame; it hangs up a few seconds after the screen stops drawing it, and the hub then stops ffmpeg.

On top goes a caption saying what the feed is and where it comes from, styled per webcam (``overlay``): see
display/modules/webcam_caption.py for the sections and layouts.

A rotation entry without a ``cam`` is the Webcams list (``modules.webcams.playlist``, set on the Webcams page): each
turn plays the next ``per_turn`` webcams that are on, each for its own seconds, then ends ("done").

The camera grid (``webcam_grid*`` panels, ``modules.webcams.grid``): tiles filling the whole frame (2 rows by
default, as many columns as fit), filled one camera at a time (every ``interval_ms``, top row left to right, then the
next) until every camera in the grid has appeared, each labelled with its name. The hub fetches the stills and sends
them already cropped to the tile (/api/webcams/grid.bin), all at once.
"""

from __future__ import annotations

import http.client
import json
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
WARM_EVERY = 10.0            # while a webcam is next in the rotation, ask the hub to keep its feed going this often
GIVE_UP_SEC = 25.0           # no picture this long after the play started: skip to the next screen
MAGIC = b"BSWC"
GRID_MAGIC = b"BSG2"
GRID_ROWS = 2                # the camera grid's rows by default; its columns fit the frame
GRID_MAX_ROWS, GRID_MAX_COLS = 4, 10
GRID_VIEW_MS = 1000          # an intersection's tile shows each of its views this long, round and round
GRID_ASPECT = 1.1           # auto columns keep each tile about this much wider than tall (6 across full width)
_list_pos = {"i": -1}        # where the Webcams list is (survives config reloads, which rebuild the panel)


def playlist_of(s: dict) -> list:
    """The Webcams list in order, as [(cam id, seconds)] of the webcams that are on and exist."""
    cams = s.get("cams") or {}
    raw = s.get("playlist")
    if not isinstance(raw, list):                     # no list yet: every webcam, as configured
        raw = [{"cam": k} for k in cams]
    out = []
    for it in raw:
        if isinstance(it, dict) and it.get("on", True) is not False and it.get("cam") in cams:
            try:
                sec = max(5.0, min(3600.0, float(it.get("sec") or 60)))
            except (TypeError, ValueError):
                sec = 60.0
            out.append((it["cam"], sec))
    return out


def stream_query(cam: dict, shape: str, w: int, h: int) -> dict:
    """What the hub needs to make this zone's frames (hub/app.py /api/webcams/frames)."""
    src = cam.get("source") or {}
    q = {"type": src.get("type", "stream"), "url": src.get("url", ""), "fps": int(cam.get("fps", 15)),
         "w": int(w), "h": int(h)}
    if src.get("type") == "drivebc":
        q["id"] = int(src.get("id") or 0)
    try:
        if src.get("refresh_sec"):
            q["refresh"] = int(float(src["refresh_sec"]))
    except (TypeError, ValueError):
        pass
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
        self.query = dict(query)
        self.path = "/api/webcams/frames?" + urlencode(query)
        self.w, self.h = int(query["w"]), int(query["h"])
        self.frame = None
        self.frame_t = 0.0
        self.age = None
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
            age = int.from_bytes(head[8:12], "big") / 1000.0       # a still's image age at send (s)
            if (w, h) != (self.w, self.h):
                raise RuntimeError(f"hub sent {w}x{h}, wanted {self.w}x{self.h}")
            body = r.read(n)
            if len(body) < n:
                raise RuntimeError("stream ended")
            self.frame = np.frombuffer(body, np.uint8).reshape(h, w, 3)
            self.frame_t = time.time()
            self.age = age
            self.error = ""


class WebcamPanel:
    """Draws the ``webcam`` (one side), ``webcam_full`` (full width) and ``webcam_wall`` (whole wall) entries."""

    def __init__(self, cfg, data_dir=None):
        s = cfg.modules.get("webcams") or {}
        self.data_dir = data_dir
        self.weather, self._wx_mtime, self._wx_checked = {}, None, 0.0
        self.cams = s.get("cams") or {}
        self.hub = s.get("hub_url") or DEFAULT_HUB
        self.playlist = playlist_of(s)
        try:
            self.per_turn = max(1, int(s.get("per_turn") or 1))
        except (TypeError, ValueError):
            self.per_turn = 1
        self._turns: dict = {}                                # zone -> the list's turn there: {play, cam, t0, shown}
        self.grid = CameraGrid(self.hub, s.get("grid") or {})
        self.streams: dict = {}
        self.started: dict = {}
        self._warmed: dict = {}                               # source -> when the hub was last asked to warm it
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

    def warm(self, entry, zone=None):
        """The rotation says this webcam is next: have the hub start its feed now (in the background), so its screen
        opens on a picture a second or so behind live instead of "loading". The camera grid fetches all its tiles for
        the area it will play in (zone), so its reveal starts the moment it comes up."""
        if str((entry or {}).get("panel", "")).startswith("webcam_grid"):
            return self.grid.warm(zone)
        cid = (entry or {}).get("cam")
        if not cid and self.playlist:                         # the Webcams list: the one it plays next
            cid = self.playlist[(_list_pos["i"] + 1) % len(self.playlist)][0]
        cam = self.cams.get(cid)
        if not cam:
            return
        q = {k: v for k, v in stream_query(cam, "full", 8, 8).items() if k in ("type", "url", "id", "refresh", "fps")}
        key = tuple(sorted(q.items()))
        now = time.time()
        if now - self._warmed.get(key, 0) < WARM_EVERY:
            return
        if any(st.alive and all(st.query.get(k) == v for k, v in q.items()) for st in self.streams.values()):
            return                                            # already on screen: its stream keeps it going
        self._warmed[key] = now
        hub = urlparse(self.hub)

        def ask():
            try:
                conn = http.client.HTTPConnection(hub.hostname, hub.port or 80, timeout=5)
                conn.request("GET", "/api/webcams/warm?" + urlencode(q))
                conn.getresponse().read()
                conn.close()
            except OSError as e:
                print(f"[webcam] warm {q.get('url') or q.get('id')}: {e}")
        threading.Thread(target=ask, name="webcam-warm", daemon=True).start()

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
        if p == "webcam_tall":
            return "tall"
        return "left" if zone[0] == 0 else "right"

    def draw(self, c, zone, entry, transition):
        if entry.get("cam"):
            return self._draw_cam(c, zone, entry, transition)
        return self._draw_list(c, zone, entry, transition)

    def _draw_list(self, c, zone, entry, transition):
        """The Webcams list: the next webcams that are on, each for its seconds; "done" after per_turn of them."""
        now = time.time()
        if not self.playlist:
            self._centre(c, zone, "No webcams in the list", self.dim)
            return "skip" if now - self.started.setdefault(("list", entry.get("_play")), now) > 4 else None
        where = tuple(zone)                                   # each half of the wall has its own turn
        t = self._turns.get(where)
        if transition or t is None or t["play"] != entry.get("_play"):
            t = self._turns[where] = {"play": entry.get("_play"), "cam": None, "t0": now, "shown": 0, "failed": 0}
        n = len(self.playlist)
        if t["cam"] is None or now - t["t0"] >= self._sec(t["cam"]):
            if t["cam"] is not None:
                t["shown"] += 1
                if t["shown"] >= min(self.per_turn, n):
                    self._turns.pop(where, None)              # another pass of the same play starts afresh
                    return "done"
            _list_pos["i"] = (_list_pos["i"] + 1) % n
            t["cam"], t["t0"], t["new"] = self.playlist[_list_pos["i"]][0], now, True
        new, t["new"] = t.get("new", False), False
        r = self._draw_cam(c, zone, {**entry, "cam": t["cam"]}, new)
        if r == "skip":                                       # this one won't come up: on to the next
            t["failed"] += 1
            t["shown"] -= 1                                   # (it doesn't count as shown)
            t["t0"] = now - self._sec(t["cam"])
            if t["failed"] >= n:
                return "skip"
        return None

    def _sec(self, cid):
        return next((sec for k, sec in self.playlist if k == cid), 60.0)

    def _draw_cam(self, c, zone, entry, transition):
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
        wx = cam.get("_wx") or self._weather(now).get(entry.get("cam"))   # _wx: a preview of an unsaved location
        still = (cam.get("source") or {}).get("type") in ("drivebc", "image")
        extra = {"taken": round(st.frame_t - st.age)} if still and st.age is not None else None   # capture time (s)
        draw_caption(c, zone, cam, now - started, self.font, wx=wx, extra=extra)
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


class CameraGrid:
    """A grid of still cameras filling the whole frame (modules.webcams.grid: {"cams": [{"name", "source"}], "rows",
    "cols", "interval_ms", "labels", "hold_sec", "group_views", "view_ms"}): ``rows`` rows (2) of ``cols`` tiles (or
    "auto": as many as keep the tiles a little wider than square, 6 across full width). One tile changes every interval,
    top row left to right then the next, until every camera has appeared; the screen ends ``hold_sec`` after the last
    one. With ``group_views`` (on) an intersection's views (NB, SB, EB, WB…) share one tile, which loops through them
    every ``view_ms``. Each tile carries its camera's name. The tiles come from the hub in one request, already cropped
    and sized (it fetches the stills); a reader thread loads them so drawing never waits."""

    def __init__(self, hub_url, g):
        self.hub = urlparse(hub_url)
        cams = g.get("cams") if isinstance(g.get("cams"), list) else []
        self.cams = [x for x in cams if isinstance(x, dict) and isinstance(x.get("source"), dict)]
        self.names = [str(x.get("name") or "") for x in self.cams]
        near = g.get("near") if isinstance(g.get("near"), dict) else {}
        self.near = near if near.get("on") else None        # cameras near a point, added by the hub as it fetches
        try:
            self.interval = max(0.05, min(5.0, float(g.get("interval_ms") or 200) / 1000))
            self.hold = max(0.0, min(30.0, float(g.get("hold_sec", 3))))
        except (TypeError, ValueError):
            self.interval, self.hold = 0.2, 3.0
        self.group_views = g.get("group_views", True) is not False
        self.view_sec = _int_in(g.get("view_ms"), GRID_VIEW_MS, 200, 10000) / 1000
        self.rows = _int_in(g.get("rows"), GRID_ROWS, 1, GRID_MAX_ROWS)
        self.cols = None if g.get("cols") in (None, "", "auto") else _int_in(g.get("cols"), 6, 1, GRID_MAX_COLS)
        # each tile's label (display/modules/cam_names.py): the name split into its streets, the chosen parts on each
        # line, at the bottom or top; the direction as a small tag in the opposite corner, in a line, or not at all
        lab = g.get("label") if isinstance(g.get("label"), dict) else {}
        self.labels = bool(g.get("labels", True))
        self.label_lines = lab.get("lines") if isinstance(lab.get("lines"), list) else None
        self.label_short = lab.get("short", True) is not False
        self.label_at = "top" if lab.get("at") == "top" else "bottom"
        self.dir_place = lab.get("dir") if lab.get("dir") in ("tag", "line", "off") else "tag"
        self.dir_style = "full" if lab.get("dir_style") == "full" else "short"
        self.label_size = "small" if lab.get("size") == "small" else "tiny"
        self.tiles: dict = {}                       # (tile w, tile h) -> {"at", "batch": ([array or None], names), "busy"}
        self._plays: dict = {}                      # zone -> {"play", "t0", "ready"}: each half of the wall its own
        self._font = None
        self._labels: dict = {}                     # tile width -> each label worked out (once, not per frame)
        self._up: dict = {}                         # (id(tiles), w, h) -> (tiles, places: [[camera indexes]] of those up)

    def warm(self, zone=None):
        """The rotation says the grid is next: fetch its tiles for that area now (in the background), or refresh the
        sizes it has drawn at."""
        if zone and (self.cams or self.near):
            tw, th, _, _ = self.geometry(zone)
            return self._load((tw, th))
        for size in list(self.tiles):
            self._load(size)

    def _load(self, size):
        got = self.tiles.get(size)
        if got and (got["busy"] or time.time() - got["at"] < 60):
            return
        got = self.tiles.setdefault(size, {"at": 0.0, "tiles": None, "busy": False})
        got["busy"] = True
        threading.Thread(target=self._fetch, args=(size, got), name="webcam-grid", daemon=True).start()

    def _fetch(self, size, got):
        try:
            # the cameras go with the request: the hub crops exactly the grid this config has (a preview's included)
            body = json.dumps({"w": size[0], "h": size[1], "grid": {
                "cams": [{"source": c["source"], "name": c.get("name") or "", **({"crop": c["crop"]} if c.get("crop") else {})}
                         for c in self.cams], "near": self.near}})
            conn = http.client.HTTPConnection(self.hub.hostname, self.hub.port or 80, timeout=90)
            conn.request("POST", "/api/webcams/grid.bin", body=body,
                         headers={"User-Agent": "bigscreen-display", "Content-Type": "application/json"})
            r = conn.getresponse()
            body = r.read()
            conn.close()
            if r.status != 200 or body[:4] != GRID_MAGIC:
                raise RuntimeError(f"hub {r.status}: {body[:200]!r}")
            tiles, names = unpack_grid(body)                    # the names include the nearby cameras
            self._places(tiles, names, size[0], size[1])        # worked out here, not in a frame
            if self.labels:
                self._labels_for(size[0], names)
            got["batch"] = (tiles, names)                       # one swap: a play never pairs new tiles with old names
            got["at"] = time.time()
        except Exception as e:
            print(f"[webcam grid] {e}")
            got["at"] = time.time() - 50                    # try again in ~10 s
        finally:
            got["busy"] = False

    def geometry(self, zone):
        """(tile w, tile h, [tile left edges], [tile top edges]) for the zone: the grid spans it edge to edge, the
        spare pixels going into the 1-2 px gaps between tiles."""
        x, y, w, h = zone
        th = max(8, (h - (self.rows - 1)) // self.rows)
        cols = self.cols or max(1, min(GRID_MAX_COLS, round(w / (th * GRID_ASPECT))))
        tw = max(8, (w - (cols - 1)) // cols)
        xs = [x + (round(k * (w - tw) / (cols - 1)) if cols > 1 else (w - tw) // 2) for k in range(cols)]
        ys = [y + (round(k * (h - th) / (self.rows - 1)) if self.rows > 1 else (h - th) // 2) for k in range(self.rows)]
        return tw, th, xs, ys

    def draw(self, c, zone, entry, transition):
        now = time.time()
        where = tuple(zone)
        p = self._plays.get(where)
        if transition or p is None or p["play"] != entry.get("_play"):
            p = self._plays[where] = {"play": entry.get("_play"), "t0": now, "ready": None}
        if not self.cams and not self.near:
            _centre_text(c, zone, "No cameras in the grid", Color(150, 150, 150))
            return "skip" if now - p["t0"] > 4 else None
        tw, th, xs, ys = self.geometry(zone)
        self._load((tw, th))
        batch = (self.tiles.get((tw, th)) or {}).get("batch")
        if batch is None:                  # only when played before its tiles came (the rotation fetches them well ahead)
            _centre_text(c, zone, "Fetching cameras" + "." * (1 + int(now * 2) % 3), Color(150, 150, 150))
            return "skip" if now - p["t0"] > GIVE_UP_SEC else None
        if p["ready"] is None:
            # the reveal starts once the tiles are here, and plays this batch to its end: the tiles refreshed
            # meanwhile (every minute) are for the next pass, so a newer batch with fewer cameras up can't cut it short
            p["ready"], p["batch"] = now, batch
        r = self.draw_tiles(c, p["batch"][0], (tw, th, xs, ys), now - p["ready"], p["batch"][1])
        if r == "done":
            p["ready"] = None                                  # another pass reveals them all again
        return r

    def draw_tiles(self, c, tiles, geo, elapsed, names=None):
        """The grid ``elapsed`` seconds into the reveal; "done" once every place has appeared, the hold is over and
        each tile on the last screen has shown all its views. A tile is taken over after cells × interval, so a place
        with more views than fit in that at view_ms shows each one for a share of it instead: every view appears.
        Cameras that are down (no picture from the hub) are skipped, not shown as empty tiles; none up: "skip"."""
        tw, th, xs, ys = geo
        names = self.names if names is None else names
        places = self._places(tiles, names, tw, th)
        if not places:
            return "skip"
        cells = len(xs) * len(ys)
        per_view = lambda views: min(self.view_sec, cells * self.interval / len(views))
        shown = min(len(places), int(elapsed / self.interval) + 1)
        for k in range(min(cells, shown)):
            n = k + ((shown - 1 - k) // cells) * cells       # the newest place in tile k (k, k + cells, ...)
            views = places[n]
            i = views[int(max(0.0, elapsed - n * self.interval) / per_view(views)) % len(views)]   # from its first view
            tx, ty = xs[k % len(xs)], ys[k // len(xs)]
            c.Blit(tiles[i], tx, ty)
            if self.labels and tw >= 24 and th >= 16 and i < len(names):
                self._label(c, tx, ty, tw, th, self._labels_for(tw, names)[i])
        if shown < len(places):
            return None
        loops = max((n * self.interval + len(places[n]) * per_view(places[n])
                     for n in range(max(0, len(places) - cells), len(places)) if len(places[n]) > 1), default=0.0)
        if elapsed >= max(len(places) * self.interval + self.hold, loops):
            return "done"
        return None

    def _places(self, tiles, names, tw, th):
        """The cameras that sent a picture, as places in order of first appearance: [[camera index, ...]], an
        intersection's views together when group_views is on. Once per batch and size, not per frame (the lists are
        kept with the result, so a reused id() can't match)."""
        key = (id(tiles), id(names), tw, th)
        hit = self._up.get(key)
        if hit is not None and hit[0] is tiles and hit[1] is names:
            return hit[2]
        up = [i for i, t in enumerate(tiles) if t is not None and t.shape[:2] == (th, tw)]
        if self.group_views:
            from display.modules.cam_names import place_key
            by: dict = {}
            for i in up:
                by.setdefault(place_key(names[i]) if i < len(names) and names[i] else ("#", i), []).append(i)
            places = list(by.values())
        else:
            places = [[i] for i in up]
        if len(self._up) > 4:
            self._up.clear()
        self._up[key] = (tiles, names, places)
        return places

    def _labels_for(self, tw, names=None):
        """Each camera's label for tiles this wide, worked out once per width and list: ([lines cut to fit], tag)."""
        names = self.names if names is None else names
        key = (tw, id(names))
        hit = self._labels.get(key)
        got = hit[1] if hit is not None and hit[0] is names else None   # the list is kept with them: no id reuse
        if got is None:
            from display.modules import cam_names
            from display.modules.webcam_caption import _tw
            f, got = self._label_font(), []
            lines = self.label_lines
            if self.dir_place != "line":                      # the direction is only in a line when asked there
                lines = [[p for p in ln if p != "dir"] for ln in (lines or cam_names.DEFAULT_LINES) if isinstance(ln, list)]
            for name in names:
                tag = cam_names.tag_text(name, self.dir_style, self.label_short) if self.dir_place == "tag" else ""
                tag = tag if _tw(f, tag) + 4 <= tw else ""
                out = []
                for text in cam_names.label_lines(name, lines, self.label_short, self.dir_style)[:3]:
                    while text and _tw(f, text) > tw - 2:
                        text = text[:-1]
                    out.append(text.rstrip())
                got.append((out, tag))
            if len(self._labels) > 8:
                self._labels.clear()
            self._labels[key] = (names, got)
        return got

    def _label_font(self):
        if self._font is None:
            self._font = graphics.Font()
            self._font.LoadFont(FONTS["small" if self.label_size == "small" else "tiny"])
        return self._font

    def _label(self, c, x, y, tw, th, label):
        """The tile's name lines on a shaded strip (bottom or top), and its direction tag in the opposite corner."""
        from display.modules.webcam_caption import _shade, _tw
        lines, tag = label
        f = self._label_font()
        lh = f.height + 1
        lines = lines[:max(0, (th - (lh if tag else 0)) // lh)]       # never more than the tile can hold
        if lines:
            sy = y + th - lh * len(lines) if self.label_at == "bottom" else y
            _shade(c, x, sy, tw, lh * len(lines), 0.55)
            for k, text in enumerate(lines):
                graphics.DrawText(c, f, x + 1, sy + k * lh + f.baseline, Color(235, 235, 235), text)
        if tag:
            w = _tw(f, tag) + 3
            ty = y if self.label_at == "bottom" else y + th - lh
            c.FillRect(x + tw - w, ty, w, lh, Color(0, 0, 0))
            graphics.DrawText(c, f, x + tw - w + 2, ty + f.baseline, Color(255, 200, 40), tag)


def _int_in(v, default, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def pack_grid(tiles, w, h, names=None) -> bytes:
    """The hub's reply: GRID_MAGIC, count u16, tile width u16, tile height u16, the names' JSON length u32 and the
    JSON (a list, one per camera), then per camera a flag byte (1: a picture follows)."""
    nb = json.dumps(list(names or [])).encode()
    out = [GRID_MAGIC, len(tiles).to_bytes(2, "big"), int(w).to_bytes(2, "big"), int(h).to_bytes(2, "big"),
           len(nb).to_bytes(4, "big"), nb]
    for t in tiles:
        out += [b"\x01", np.ascontiguousarray(t, np.uint8).tobytes()] if t is not None else [b"\x00"]
    return b"".join(out)


def unpack_grid(body) -> tuple:
    """(tiles, names) from pack_grid's bytes."""
    n, w, h = (int.from_bytes(body[k:k + 2], "big") for k in (4, 6, 8))
    nl = int.from_bytes(body[10:14], "big")
    names = json.loads(body[14:14 + nl] or b"[]")
    per, off, tiles = w * h * 3, 14 + nl, []
    for _ in range(n):
        ok, off = body[off], off + 1
        if ok:
            tiles.append(np.frombuffer(body[off:off + per], np.uint8).reshape(h, w, 3))
            off += per
        else:
            tiles.append(None)
    return tiles, names


_centre_font = []


def _centre_text(c, zone, text, color):
    if not _centre_font:
        f = graphics.Font()
        f.LoadFont(FONTS["small"])
        _centre_font.append(f)
    f = _centre_font[0]
    x, y, w, h = zone
    tw = sum(max(0, f.CharacterWidth(ord(ch))) for ch in text)
    graphics.DrawText(c, f, x + max(2, (w - tw) // 2), y + h // 2, color, text)


_preview_fonts: dict = {}


def _preview_font(size):
    f = _preview_fonts.get(size)
    if f is None:
        f = _preview_fonts[size] = graphics.Font()
        f.LoadFont(FONTS.get(size, size))
    return f


def caption_dropped(cam: dict, shape_zone, wx=None, extra=None) -> list:
    """Sections turned on that the caption can't fit at this size (the control panel lists them)."""
    from display.modules.webcam_caption import dropped_sections
    return dropped_sections(cam, shape_zone, _preview_font, wx, extra)


def render_preview(cam: dict, frame, shape_zone, elapsed=0.0, wx=None, extra=None) -> np.ndarray:
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

    draw_caption(c, (0, 0, w, h), cam, elapsed, font_for, wx=wx, extra=extra)
    return c.buf.copy()
