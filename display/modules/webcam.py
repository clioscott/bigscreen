"""Live webcam video (control panel → Webcams).

The hub decodes the stream (hub/webcams.py) and sends frames already cropped and scaled to the zone, so the Pi
only copies them to the canvas: no decoding here, ever. A reader thread per playing webcam keeps the newest
frame; it hangs up a few seconds after the screen stops drawing it, and the hub then stops ffmpeg.

Smooth first: video plays from a deep buffer here (PLAY_BUFFER_SEC) at exactly the feed's rate by the draw clock, so a
busy hub or a slow network never shows as a freeze or a catch-up; how far behind live that is, nobody can tell. Only
webcams set to play in step (``sync``: cameras looking at the same street, so a train crossing from one view to the
next lines up when the wall cuts between them) show each frame at a fixed delay after it was filmed instead, at the
cost of a freeze when a frame comes late. While video is on screen display/main.py draws at 30 Hz (one draw per frame).

On top goes a caption saying what the feed is and where it comes from, styled per webcam (``overlay``): see
display/modules/webcam_caption.py for the sections and layouts.

A rotation entry without a ``cam`` is the Webcams list (``modules.webcams.playlist``, set on the Webcams page): each
turn plays ``per_turn`` webcams that are on, each for its own seconds, then ends ("done"). ``per_turn: "all"``: every
webcam that's on and up, once. ``from_top`` (on): each turn starts at the top of the list; off: it carries on from
where the last turn stopped.

No "loading" between webcams: the hub starts the next one's feed PREROLL_SEC before it's due (the list's next webcam,
or the rotation's next screen through warm) and the Pi opens its stream STREAM_AHEAD_SEC before, so its frames are
already arriving when it comes up; if they aren't yet,
the current webcam stays up a little longer (HOLD_MAX_SEC) rather than cutting to black. Webcams that are down are
skipped: the hub's checks (data/webcam_health.json, hub/jobs/webcam_health.py) and, while playing or prerolling, a
stream that sends no picture or a black one (BLACK_SEC), which then sits out for BAD_SEC.

The camera grid (``webcam_grid*`` panels, ``modules.webcams.grid``): tiles filling the whole frame (2 rows by
default, as many columns as fit), filled one camera at a time (every ``interval_ms``, top row left to right, then the
next) until every camera in the grid has appeared, each labelled with its name. The hub fetches the stills and sends
them already cropped to the tile (/api/webcams/grid.bin), all at once.
"""

from __future__ import annotations

import collections
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
PLAY_START_SEC = 4.0         # after running dry, video plays again once this much is buffered here; the first start
                             # waits for the full PLAY_BUFFER_SEC ("Loading webcam feeds" meanwhile, unless prerolled)
PLAY_BUFFER_SEC = 12.0       # … and settles at this depth, played at exactly the feed's rate by this clock: a busy hub
                             # (Plex) and the network never show. YouTube's frames come 5 s at a time (a segment), so it
                             # swings 7-12 s: a hub ~7 s late is still unseen (seconds behind live: nobody minds)
PLAY_MAX_SEC = 20.0          # kept at most (only a far worse stall than seen drops anything)
PREROLL_KEEP_SEC = 2.0       # a stream not playing yet (prerolled) keeps at most this much beyond PLAY_BUFFER_SEC (memory)
FILL_AVG_SEC = 20.0          # the buffer's depth is averaged over this long; while that's within DRIFT_BAND of
DRIFT_BAND = 0.45            # PLAY_BUFFER_SEC the video plays at exactly 1.0 (every frame for the same time); beyond it,
DRIFT_MAX = 0.01             # 1 % faster or slower until it's back within DRIFT_SETTLE (one frame in 100: unseen). Wide:
DRIFT_SETTLE = 0.25          # 5 s segments keep the average ~20 % under the target, and a stall's dip refills by itself
WARM_EVERY = 10.0            # while a webcam is next in the rotation, ask the hub to keep its feed going this often
WEBCAM_SEC = 40.0            # how long a webcam in the list shows when it doesn't say (the panel's default for a new one)
GIVE_UP_SEC = 25.0           # no picture this long after the play started: skip to the next screen
PREROLL_SEC = 30.0           # the hub starts the next webcam's feed this long before it's due (~5-10 s from cold)
STREAM_AHEAD_SEC = 16.0      # … and the Pi opens its own stream this long before: its buffer is full when it's due
HOLD_MAX_SEC = 15.0          # the current webcam stays up at most this much longer while the next one isn't buffered
LOOKAHEAD = 3                # the list prepares this many webcams ahead, each by how soon its turn is (short turns:
PREROLL_STREAMS = 2          # several at once); at most this many of them also have the Pi's stream open (buffering)
STALL_SEC = 4.0              # a mapped webcam (the ISS) whose feed sends nothing new this long shows its map
BLACK_SEC = 4.0              # a picture black for this long: the feed is down
BAD_SEC = 600.0              # a webcam found down while playing sits out this long (the hub's checks may clear it)
RETRY_DOWN_SEC = 1800.0      # a feed's "down" this old: try it again (only a play can say it's back)
MAGIC = b"BSWC"
MAGIC_DUE = b"BSW2"          # hub/webcams.py pack: a frame in step, its header followed by when it's due (epoch ms u64)
HOLD_LATE_SEC = 0.5          # in step: frames due longer ago than this are let go (one is always kept to show)
LATE_SHIFT_MAX = 10.0        # in step: a webcam whose frames start coming late plays up to this much later instead of
                             # freezing (out of step with the rest until it's shown again)
PLACEHOLDER_AGE = 0xFFFFFFFF  # hub/webcams.py: a frame with this age is the still sent while a feed starts
GRID_MAGIC = b"BSG2"
GRID_ROWS = 2                # the camera grid's rows by default; its columns fit the frame
GRID_MAX_ROWS, GRID_MAX_COLS = 4, 10
GRID_VIEW_MS = 1000          # an intersection's tile shows each of its views this long, round and round
GRID_ASPECT = 1.1           # auto columns keep each tile about this much wider than tall (6 across full width)
_list_pos = {"i": -1}        # where the Webcams list is (survives config reloads, which rebuild the panel)
CLOCK = {"tick": None}       # when the frame being drawn is due (display/main.py's fixed grid): video goes by this, so
                             # each frame shows for the same number of draws whatever the drawing costs. "video_at":
                             # when video was last drawn (main.py then runs at system.display.video_fps, 30 by default:
                             # one draw per frame with time to spare, where 60 Hz overran and showed frames unevenly)


def is_black(frame) -> bool:
    """A black picture: a feed that's down but still sending. As hub/webcams.py is_black (strict: a dark night scene,
    even a cropped one of sky or water, has some pixels above the level)."""
    sub = frame[::4, ::4]
    return float((sub.max(axis=-1) > 24).mean()) < 0.001


def is_flat(frame) -> bool:
    """A plain screen: nearly all one colour (NASA's blue "loss of signal" slate, a camera's "offline" card). Only for
    webcams that ask (a foggy harbour can be nearly flat too): ``skip_plain``, on for webcams in space."""
    sub = frame[::4, ::4].reshape(-1, 3).astype(np.int16)
    med = np.median(sub, axis=0)
    return float((np.abs(sub - med).max(axis=1) <= 14).mean()) >= 0.9


def is_frozen(sub, last) -> bool:
    """Two samples (every 4th pixel) a second apart with almost nothing changed: a slate standing in for the video
    (NASA's "temporarily suspended … loss-of-signal" card is a still with text). Live video from the ISS always moves."""
    if last is None or sub.shape != last.shape:
        return False
    return float((np.abs(sub - last).max(axis=-1) > 6).mean()) < 0.002


def skips_plain(cam: dict) -> bool:
    return bool((cam or {}).get("skip_plain", bool((cam or {}).get("space"))))


def _minutes(v):
    """"HH:MM" -> minutes after midnight, or None."""
    try:
        h, m = str(v).strip().split(":")
        h, m = int(h), int(m)
    except (ValueError, AttributeError):
        return None
    return h * 60 + m if 0 <= h <= 24 and 0 <= m < 60 and h * 60 + m <= 1440 else None


def cam_tz(cam: dict):
    """The webcam's own time zone: its tz, its weather city's, or None (the Pi's)."""
    tz = (cam or {}).get("tz") or ((cam or {}).get("wx_city") or {}).get("tz")
    if not tz:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(str(tz))
    except Exception:
        return None


def in_hours(cam: dict, now=None) -> bool:
    """Is it within the webcam's hours (``hours: {"from": "HH:MM", "to": "HH:MM"}`` in its own local time; "to"
    before "from" runs past midnight, e.g. 16:00-08:00 for the night)? No hours: always."""
    hours = (cam or {}).get("hours")
    if not isinstance(hours, dict):
        return True
    a, b = _minutes(hours.get("from")), _minutes(hours.get("to"))
    if a is None or b is None or a == b:
        return True
    from datetime import datetime
    t = datetime.fromtimestamp(now or time.time(), cam_tz(cam))
    m = t.hour * 60 + t.minute
    return a <= m < b if a < b else (m >= a or m < b)


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
                sec = max(5.0, min(3600.0, float(it.get("sec") or WEBCAM_SEC)))
            except (TypeError, ValueError):
                sec = 60.0
            out.append((it["cam"], sec))
    return out


FULL_ASPECT = 448 / 134            # the full-width zone (the shape a tall crop borrows from when it has none)
SOURCE_ASPECT = 16 / 9             # webcams are nearly all 16:9 (the hub clamps a crop to the real frame anyway)


def crop_for(cam: dict, shape: str, w: int, h: int):
    """The webcam's crop for this shape. "tall" (above the clock) with none of its own borrows the full-width one:
    the same width and centre, just taller, so the framing chosen for full width carries over."""
    crops = cam.get("crops") or {}
    crop = crops.get(shape)
    if isinstance(crop, dict) and crop.get("w"):
        return crop
    full = crops.get("full")
    if shape != "tall" or not (isinstance(full, dict) and full.get("w")):
        return None
    try:
        cw, x, y = float(full["w"]), float(full.get("x", 0)), float(full.get("y", 0))
    except (TypeError, ValueError):
        return None
    h_full = cw * SOURCE_ASPECT / FULL_ASPECT                # the crops' heights as fractions of the frame
    h_tall = cw * SOURCE_ASPECT / (w / max(1, h))
    if h_tall > 1.0:                                       # taller than the frame: narrow it to fit, same centre
        cw *= 1.0 / h_tall
        x += (float(full["w"]) - cw) / 2
        h_tall = 1.0
    y = min(max(0.0, y + h_full / 2 - h_tall / 2), 1.0 - h_tall)
    return {"x": round(max(0.0, x), 5), "y": round(y, 5), "w": round(cw, 5)}


def in_step(cam: dict) -> bool:
    """Does this webcam play in step with the others (``sync``, off by default)? In step, every frame shows a fixed
    delay after it was filmed, the same for every webcam, so cameras side by side line up when the wall cuts between
    them (a train leaving one view enters the next); but a frame that comes late freezes the picture and the video
    then skips to catch up. Off, the video plays from a deep buffer at its own steady rate and nothing the hub or the
    network does shows. Only worth it for cameras looking at the same place."""
    return bool((cam or {}).get("sync"))


def stream_query(cam: dict, shape: str, w: int, h: int) -> dict:
    """What the hub needs to make this zone's frames (hub/app.py /api/webcams/frames)."""
    src = cam.get("source") or {}
    q = {"type": src.get("type", "stream"), "url": src.get("url", ""), "fps": int(cam.get("fps", 15)),
         "w": int(w), "h": int(h)}
    if src.get("type") == "drivebc":
        q["id"] = int(src.get("id") or 0)
    if src.get("type") == "youtube" and in_step(cam):
        q["sync"] = 1                                 # in step: frames say when they're due (hub/webcams.py)
    if skips_plain(cam):
        q["flat"] = 1                                  # a plain screen (a "loss of signal" slate) counts as down
    try:
        if src.get("refresh_sec"):
            q["refresh"] = int(float(src["refresh_sec"]))
    except (TypeError, ValueError):
        pass
    crop = crop_for(cam, shape, w, h)
    if isinstance(crop, dict) and crop.get("w"):
        q.update(x=round(float(crop.get("x", 0)), 5), y=round(float(crop.get("y", 0)), 5),
                 cw=round(float(crop["w"]), 5))
        if crop.get("snap"):                           # Whole pixels: k x k stream pixels per LED, averaged
            q["px"] = 1
    col = cam.get("colour") or {}
    for k, key in (("b", "brightness"), ("c", "contrast"), ("s", "saturation")):
        if col.get(key) not in (None, 1, 1.0):
            q[k] = float(col[key])
    if col.get("sharpen") is not None:                 # none: the hub's default (hub/webcams.py DEFAULT_SHARPEN)
        q["sh"] = float(col["sharpen"])
    return q


class Stream:
    """Reads one frame stream from the hub on its own thread; ``frame`` is the newest (h, w, 3) uint8."""
    dry_runs = 0                               # times playback ran out of frames (a freeze while it refills)
    skipped = 0                                # frames passed over (two due in one draw: a late frame)
    shown = 0
    in_step = False                            # (set per stream by its first frame in step)
    late = 0
    _holding = False
    _shown_due = None
    shift = 0.0                                # in step: how much later than its due times this stream plays
    _on_time = False                           # in step: a frame has come well before it was due
    _drift = 0.0

    def __init__(self, hub_url: str, query: dict):
        self.hub = urlparse(hub_url)
        self.query = dict(query)
        self.path = "/api/webcams/frames?" + urlencode(query)
        self.w, self.h = int(query["w"]), int(query["h"])
        self.frame = None
        self.frame_t = 0.0
        self.age = None
        self.error = ""
        self.started = time.time()
        self.fps = max(1, int(query.get("fps") or 15))
        self.video = query.get("type") not in ("drivebc", "image")   # stills: just the newest
        self._q = collections.deque(maxlen=int(self.fps * PLAY_MAX_SEC) + 4)   # frames waiting to be shown
        self._play_t0 = None                   # playback clock: when frame _play_k was due (None: buffering)
        self._play_k, self._play_pos, self._play_last = 0, 0.0, 0.0
        self._shown = None
        self._played = False                   # started playing at least once (the first start waits for a full buffer)
        self._fill_avg = None                  # the buffer's depth averaged over ~FILL_AVG_SEC (steers the drift)
        self._drift = 0.0                      # the speed's correction now: 0, or ±DRIFT_MAX while the depth is far off
        self._buffer_since = time.time()       # when it last began buffering (the panel gives up on one that never fills)
        # in step (hub/webcams.py: YouTube): each frame says when it's due on the wall, the same delay behind real
        # time for every webcam; it's shown then, whatever the network or the hub did on the way
        self.in_step = False
        self._tq = collections.deque()         # (due, frame), oldest first
        self._tlock = threading.Lock()
        self._shown_due = None
        self._holding = False                  # the newest frame is overdue: holding it until more come
        self.late = 0                          # frames that came after they were due (passed over)
        self.black_since = None                # when the picture went black, or plain (None: it isn't)
        self._marks = collections.deque(maxlen=64)   # in step: (due, blank) once a second, for what's on screen
        self.skip_plain = bool(self.query.get("flat"))   # a plain screen counts as black (skips_plain)
        self._black_checked = 0.0
        self.last_use = time.time()
        self.alive = True
        self._conn = None
        self._sock = None                      # our own reference: http.client drops conn.sock once a response owns it
        threading.Thread(target=self._run, name="webcam-reader", daemon=True).start()

    def used(self, hold=0.0):
        """Keep the stream open: drawn now, or (hold) wanted for the next ``hold`` seconds more (a preroll)."""
        self.last_use = max(self.last_use, time.time() + hold)

    def current(self, now=None):
        """The frame to show now. Video plays from the buffer by this clock at the feed's own speed, never skipping:
        the first start waits for a full PLAY_BUFFER_SEC, later ones (after running dry) for PLAY_START_SEC. The
        speed is exactly 1.0 while the buffer's averaged depth is near PLAY_BUFFER_SEC (DRIFT_BAND); only when it
        wanders further is it eased back at DRIFT_MAX (1 %), so a backlog after a stall drains over minutes instead of
        being thrown away, and a steady feed never has a frame doubled or dropped. Stills: the newest."""
        if not self.video:
            return self.frame
        now = time.time() if now is None else now
        if self.in_step:
            return self._current_in_step(now)
        q = self._q
        target = max(1, int(self.fps * PLAY_BUFFER_SEC))
        start = max(1, int(self.fps * PLAY_START_SEC)) if self._played else target   # first start: a full buffer
        if self._shown is None and q:
            self._shown = q[0]                                 # a picture at once (the hub's still, or the first)
        if self._play_t0 is None:
            if len(q) < start:
                return self._shown if self._shown is not None else self.frame
            while len(q) > target:                             # a prerolled backlog: start at the buffer's depth
                q.popleft()
            # from the frame it carries on from (the still shown while buffering was the oldest, which then jumped
            # seconds ahead); a quarter frame in: on the draw grid the frame changes between two draws, never on one
            self._shown = q.popleft()
            self.shown += 1
            self._play_t0, self._play_k, self._play_pos, self._play_last = now, 0, 0.25, now
            self._played = True
            self._fill_avg, self._drift = None, 0.0
        dt = min(0.25, max(0.0, now - self._play_last))
        self._play_last = now
        # the speed is the feed's own: exactly 1.0, every frame for the same time. YouTube's segments (5 s at a
        # time) swing the depth by seconds, so it's averaged over ~20 s, and only a depth well off the target
        # (DRIFT_BAND) is eased back, at 1 %, until it's close again (DRIFT_SETTLE). Steering by the depth all the
        # time doubled or dropped a frame every second or two: a steady judder.
        k = min(1.0, dt / FILL_AVG_SEC)
        self._fill_avg = len(q) if self._fill_avg is None else self._fill_avg + (len(q) - self._fill_avg) * k
        dev = self._fill_avg / target - 1.0
        if abs(dev) >= DRIFT_BAND:
            self._drift = DRIFT_MAX if dev > 0 else -DRIFT_MAX
        elif abs(dev) <= DRIFT_SETTLE:
            self._drift = 0.0
        rate = 1.0 + self._drift
        self._play_pos += dt * self.fps * rate
        due = int(self._play_pos)
        step = 0
        while self._play_k < due and q:
            self._shown = q.popleft()
            self._play_k += 1
            step += 1
        self.shown += step
        self.skipped += max(0, step - 1)
        if self._play_k < due:                                 # ran dry: hold the picture, fill up again
            self._play_t0 = None
            self._buffer_since = now
            self.dry_runs += 1
        return self._shown

    # in step ------------------------------------------------------------------------------------------------
    def _add_due(self, due, frame, now):
        """The reader's side: queue a frame by when it's due. A frame due no later than ones already queued comes
        from a restarted run on the hub (more detail, a new URL): it replaces them from there on."""
        self.in_step = True
        if due >= now + 1.0:
            self._on_time = True
        elif self._on_time and due + self.shift < now - 0.05:
            # it was coming on time and now isn't (a busy hub): play it later, smoothly, rather than freeze and skip.
            # (The first frames of a stream are always a little late: those don't count.)
            self.shift = min(LATE_SHIFT_MAX, max(self.shift, now - due + 1.0))
        due += self.shift
        with self._tlock:
            q = self._tq
            while q and q[-1][0] >= due:
                q.pop()
            q.append((due, frame))
            if due < now:
                self.late += 1
            while len(q) > 1 and q[1][0] <= now - HOLD_LATE_SEC:   # long gone (a prerolled stream isn't drawn)
                q.popleft()
            while len(q) > self._q.maxlen:
                q.popleft()

    def _current_in_step(self, tick):
        """The frame due at this draw. Draws come on a fixed 1/60 s grid and frames every 1/30 s; each frame's time
        is shifted (by under one draw, the same for all of a feed's frames) to fall halfway between two draws, so
        a little jitter never shows one a draw early or late: every frame shows for exactly two draws."""
        period = CLOCK.get("period") or 1 / 60
        with self._tlock:
            q = self._tq
            if not q:
                return self._shown if self._shown is not None else self.frame
            t = tick + (q[0][0] - tick - period / 2) % period
            popped = 0
            while len(q) > 1 and q[1][0] <= t:
                q.popleft()
                popped += 1
            due, frame = q[0]
            if due <= t:
                if due != self._shown_due:
                    self._shown, self._shown_due = frame, due
                    self.shown += 1
                    self.skipped += max(0, popped - 1)
                hold = len(q) == 1 and t - due > 2.5 / self.fps      # nothing after it yet: holding the picture
                if hold and not self._holding:
                    self.dry_runs += 1
                self._holding = hold
            elif self._shown is None:
                self._shown = frame                          # the first one early: a still until it's due
            return self._shown

    def overdue(self, now=None):
        """In step: how long the newest frame has been due with nothing after it (0 while frames are coming)."""
        now = now or time.time()
        with self._tlock:
            if not self._tq:
                return 0.0 if self._shown is None else now - (self._shown_due or now)
            return max(0.0, now - self._tq[-1][0])

    def ahead(self, now=None):
        """In step: how many seconds of video are here beyond now."""
        now = now or time.time()
        with self._tlock:
            return max(0.0, self._tq[-1][0] - now) if self._tq else 0.0

    def showable(self, now=None):
        """There's a picture to draw: in step, any frame at all (a late one is held); else a recent frame."""
        now = now or time.time()
        if self.in_step:
            return self._shown is not None or bool(self._tq)
        return self.frame is not None and now - self.frame_t < 10

    _last_sub = None

    def check_blank(self, frame, t, due=None):
        """Note whether the video is down while still sending (blank), from when; in step also by when that frame is
        on screen (due)."""
        b = self.blank(frame)
        if due is not None:
            self._marks.append((due, b))
        if not b:
            self.black_since = None
        elif self.black_since is None:
            self.black_since = t

    def blank_on_screen(self, now=None, hold=BLACK_SEC):
        """The picture on screen now has been blank (black; plain or frozen for skip_plain) for ``hold`` seconds. In
        step, by when the checked frames are due (they arrive seconds early); else since they came."""
        now = now or time.time()
        if not self.in_step or not self._marks:
            return self.black_since is not None and now - self.black_since >= hold
        since = None
        for due, b in list(self._marks):
            if due > now:
                break
            since = (due if since is None else since) if b else None
        return since is not None and now - since >= hold

    def blank(self, frame) -> bool:
        """Down while still sending: black, or for a webcam that asks (skip_plain) a plain screen or a frozen picture.
        Checked once a second; BLACK_SEC of it and the webcam is passed over (or left, mid-turn)."""
        if is_black(frame):
            return True
        if not self.skip_plain:
            return False
        sub = frame[::4, ::4].astype(np.int16)
        frozen = is_frozen(sub, self._last_sub)
        self._last_sub = sub
        return frozen or is_flat(frame)

    def describe(self):
        d = {"w": self.w, "h": self.h, "fps": self.fps, "url": self.query.get("url") or self.query.get("id"),
             "playing": self._play_t0 is not None, "buffered_sec": round(len(self._q) / self.fps, 1),
             "shown": self.shown, "skipped": self.skipped, "dry_runs": self.dry_runs, "error": self.error,
             "in_step": self.in_step, "drift": self._drift}
        if self.in_step:
            d.update(playing=not self._holding, buffered_sec=round(self.ahead(), 1), late=self.late,
                     shift=round(self.shift, 1))
        return d

    def buffering(self):
        """Video that isn't playing yet (filling its buffer, or filling again after running dry). In step: only
        before the first frame (it's then held while late, never "loading")."""
        if self.in_step:
            return self._shown is None and not self._tq
        return self.video and self._play_t0 is None

    def buffered(self):
        """Ready to play smoothly from the start: playing, or a full buffer waiting (a prerolled stream). In step:
        the next second is here."""
        if not self.video:
            return True
        if self.in_step:
            return self.ahead() >= 1.0
        return self._play_t0 is not None or len(self._q) >= max(1, int(self.fps * PLAY_BUFFER_SEC))

    def ready(self):
        """Ready to come up: a recent frame that isn't black and, for video, a full buffer (so it plays smoothly)."""
        return self.frame is not None and time.time() - self.frame_t < 10 and self.black_since is None \
            and self.buffered()

    def down(self, now=None):
        """Down beyond doubt: black for BLACK_SEC, or no picture GIVE_UP_SEC after opening (a hiccup mid-play isn't);
        in step, also nothing new for GIVE_UP_SEC past the last frame."""
        now = now or time.time()
        if self.black_since is not None and now - self.black_since >= BLACK_SEC:
            return True
        if self.in_step and self.overdue(now) > GIVE_UP_SEC:
            return True
        return self.frame is None and now - self.started > GIVE_UP_SEC

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
            if len(head) < 12 or head[:4] not in (MAGIC, MAGIC_DUE):
                raise RuntimeError("stream ended")
            due = None
            if head[:4] == MAGIC_DUE:                           # a frame in step: shown when it's due
                more = r.read(8)
                if len(more) < 8:
                    raise RuntimeError("stream ended")
                due = int.from_bytes(more, "big") / 1000.0
            w, h = int.from_bytes(head[4:6], "big"), int.from_bytes(head[6:8], "big")
            raw_age = int.from_bytes(head[8:12], "big")
            placeholder = raw_age == PLACEHOLDER_AGE                # the hub's still while the feed starts
            age = None if placeholder else raw_age / 1000.0         # a still's image age at send (s)
            if (w, h) != (self.w, self.h):
                raise RuntimeError(f"hub sent {w}x{h}, wanted {self.w}x{self.h}")
            body = r.read(n)
            if len(body) < n:
                raise RuntimeError("stream ended")
            self.frame = np.frombuffer(body, np.uint8).reshape(h, w, 3)
            self.frame_t = time.time()
            if due is not None and not placeholder:
                self._add_due(due, self.frame, self.frame_t)
            elif self.video and not placeholder:                # the placeholder shows while loading, never in the video
                self._q.append(self.frame)
                if self._play_t0 is None:                       # prerolled (not playing yet): only what the start keeps
                    keep = int(self.fps * (PLAY_BUFFER_SEC + PREROLL_KEEP_SEC))
                    while len(self._q) > keep:
                        self._q.popleft()
            if not placeholder and self.frame_t - self._black_checked >= 1.0:   # on the reader's core, once a second
                self._black_checked = self.frame_t              # (the hub's still while a feed starts says nothing
                self.check_blank(self.frame, self.frame_t,      # about the video: it isn't checked)
                                 None if due is None else due + self.shift)
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
        if s.get("per_turn") == "all":
            self.per_turn = None                              # the whole list
        else:
            try:
                self.per_turn = max(1, int(s.get("per_turn") or 1))
            except (TypeError, ValueError):
                self.per_turn = 1
        self.from_top = s.get("from_top", True) is not False
        self._turns: dict = {}                                # zone -> the list's turn there: {play, cam, t0, shown}
        self.grid = CameraGrid(self.hub, s.get("grid") or {})
        self.streams: dict = {}
        self.started: dict = {}
        self._warmed: dict = {}                               # source -> when the hub was last asked to warm it
        self.health, self._health_mtime, self._health_checked = {}, None, 0.0
        self._bad: dict = {}                                  # webcam -> until when it sits out (found down here)
        self.fonts: dict = {}
        self.white = Color(255, 255, 255)
        self.dim = Color(150, 150, 150)

    # streams ----------------------------------------------------------------------------------------------
    def describe(self):
        """The open streams and how their playback is going (the display's /status)."""
        return [st.describe() for st in list(self.streams.values())]

    def _stream(self, cam, shape, w, h, hold=0.0):
        q = stream_query(cam, shape, w, h)
        key = tuple(sorted(q.items()))
        st = self.streams.get(key)
        if st is None or not st.alive:
            st = self.streams[key] = Stream(self.hub, q)
        st.used(hold)
        for k, other in list(self.streams.items()):          # hang up on ones nobody drew (or prerolled) lately
            if not other.alive or time.time() - other.last_use > IDLE_SEC:
                other.close()
                self.streams.pop(k, None)
        return st

    def _preroll(self, cid, shape, zone):
        """Open this webcam's stream for the zone now, so its frames are already here when it comes up (kept open a few
        seconds past each call). The stream starts the hub's feed too. Returns the stream, or None."""
        cam = self.cams.get(cid)
        if not cam:
            return None
        return self._stream(cam, shape, zone[2], zone[3], hold=IDLE_SEC)

    def warm(self, entry, zone=None, soon=False):
        """The rotation says this webcam is next: have the hub start its feed now (decoding and buffering take ~5-10 s),
        and once it's ``soon`` (under STREAM_AHEAD_SEC to go) open the Pi's own stream for the zone too, so the screen
        opens on a picture (a few seconds behind live) instead of "loading". The camera grid fetches all its tiles for
        the area it will play in (zone), so its reveal starts the moment it comes up."""
        if str((entry or {}).get("panel", "")).startswith("webcam_grid"):
            return self.grid.warm(zone)
        cid = (entry or {}).get("cam")
        if not cid and self.playlist:                         # the Webcams list: the first few it will play
            first, taken = [], self._taken()
            n = len(self.playlist)
            pos = -1 if self.from_top and not self._other_turns(None) else _list_pos["i"]
            for step in range(1, n + 1):
                k = self.playlist[(pos + step) % n][0]
                if k not in taken and not self.is_down(k) and k not in first:
                    first.append(k)
                if len(first) >= LOOKAHEAD:
                    break
            shape = self.shape_for(entry, zone) if zone is not None else None
            for i, k in enumerate(first):                     # the first ones' streams too (short turns), the rest
                if soon and zone is not None and i < PREROLL_STREAMS:    # at the hub
                    self._preroll(k, shape, zone)
                else:
                    self._warm_hub(k, shape, zone)
            return
        shape = self.shape_for(entry, zone) if zone is not None else None
        if soon and zone is not None and cid in self.cams:
            self._preroll(cid, shape, zone)
        else:
            self._warm_hub(cid, shape, zone)

    def _warm_hub(self, cid, shape=None, zone=None):
        """Ask the hub to start (or keep up) this webcam's feed, at most every WARM_EVERY; in the background. With the
        zone it will play in, the hub decodes the detail that zone's crop needs from the start: without it the feed
        started 640 wide and restarted for more detail just as the webcam came up."""
        cam = self.cams.get(cid)
        if not cam:
            return
        source = ("type", "url", "id", "refresh", "fps")
        if zone is not None and shape:
            q = {k: v for k, v in stream_query(cam, shape, zone[2], zone[3]).items()
                 if k in source + ("w", "h", "x", "y", "cw", "px")}
        else:
            q = {k: v for k, v in stream_query(cam, "full", 8, 8).items() if k in source}
        key = tuple(sorted(q.items()))
        now = time.time()
        if now - self._warmed.get(key, 0) < WARM_EVERY:
            return
        if any(st.alive and all(st.query.get(k) == q[k] for k in source if k in q) for st in self.streams.values()):
            return                                            # already open here: that stream keeps it going
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

    # which webcams are down ---------------------------------------------------------------------------------
    def is_down(self, cid, now=None):
        """Down by the hub's last check, or found down here while playing (for BAD_SEC), or outside its hours (the
        list passes it over like a webcam that's down). A verdict from a feed (only a play can clear it) is tried again
        once it's RETRY_DOWN_SEC old, so a webcam that's back gets played; a YouTube bot check is the hub being
        throttled, never the webcam down."""
        now = now or time.time()
        if not in_hours(self.cams.get(cid), now):
            return True
        if self._mapped(cid):                                  # the ISS: its map plays when the video can't
            return False
        if not self._daylit(cid, now):                         # in space, on the night side: nothing to see
            return True
        if now < self._bad.get(cid, 0):
            return True
        h = self._health(now).get(cid)
        if not (isinstance(h, dict) and h.get("up") is False):
            return False
        why = str(h.get("why") or "").lower()
        if any(s in why for s in ("not a bot", "sign in to confirm", "bot check", "limiting the hub", "http error 429")):
            return False
        return not (h.get("by") == "feed" and now - float(h.get("checked") or 0) > RETRY_DOWN_SEC)

    def _daylit(self, cid, now):
        """A webcam in space with daylight_only (on unless turned off): the ISS is in sunlight for its whole next turn,
        as the video shows it (LAG behind). No orbit data: true (never hide it on a hub hiccup)."""
        cam = self.cams.get(cid) or {}
        if not cam.get("space") or not cam.get("daylight_only", True):
            return True
        from display.modules import space
        t0 = now - space.LAG
        lit = space.in_daylight(self._space(now), t0, t0 + self._sec(cid))
        return lit is not False

    def _mapped(self, cid):
        """A webcam in space whose map plays while its video is unusable (``map.when_down``, on unless turned off):
        never down, never skipped."""
        cam = self.cams.get(cid) or {}
        return bool(cam.get("space")) and (cam.get("map") or {}).get("when_down", True) is not False

    @staticmethod
    def _unusable(st, now):
        """A mapped webcam's video can't be shown now: no picture yet, loading, blank on screen (black, the
        loss-of-signal card, frozen), or the feed stalled (nothing new for STALL_SEC)."""
        if not st.showable(now) or st.buffering() or st.blank_on_screen(now):
            return True
        if st.in_step:
            return st.overdue(now) > STALL_SEC
        return now - st.frame_t > STALL_SEC

    def _mark_bad(self, cid, why=""):
        if self._mapped(cid):                                 # (never down: its map plays instead)
            return
        self._bad[cid] = time.time() + BAD_SEC
        print(f"[webcam] {cid} is down{': ' + why if why else ''}; skipping it for {int(BAD_SEC // 60)} min")

    def _next_up(self, after, avoid=()):
        """The list's next webcam after position ``after`` that isn't down and isn't in ``avoid`` (what the other half of
        the wall is showing or about to); the next one that isn't down when every other is taken; the next one at all
        when every one is down."""
        n = len(self.playlist)
        order = [self.playlist[(after + step) % n][0] for step in range(1, n + 1)]
        up = [cid for cid in order if not self.is_down(cid)]
        return next((cid for cid in up if cid not in avoid), up[0] if up else (order[0] if n else None))

    def _other_turns(self, t):
        """The other half of the wall's list turns that are playing."""
        return [o for o in self._turns.values() if o is not t and o.get("cam")]

    def _taken(self, t=None):
        """The webcams the other turns (the other half of the wall) are showing or have lined up next."""
        return {x for other in self._turns.values() if other is not t for x in (other.get("cam"), other.get("next")) if x}

    def _health(self, now):
        """data/webcam_health.json (hub/jobs/webcam_health.py), re-read when it changes (checked every 5 s)."""
        if self.data_dir and now - self._health_checked > 5:
            self._health_checked = now
            path = os.path.join(self.data_dir, "webcam_health.json")
            try:
                mt = os.stat(path).st_mtime
                if mt != self._health_mtime:
                    with open(path, encoding="utf-8") as f:
                        got = json.load(f)
                    self.health = got if isinstance(got, dict) else {}
                    self._health_mtime = mt
            except (OSError, ValueError):
                pass
        return self.health

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
        """The Webcams list: the next webcams that are on and up, each for its seconds; "done" after per_turn of them.
        The next one is prerolled near the end of each, and only cut to once it has a picture (or HOLD_MAX_SEC on)."""
        now = time.time()
        if not self.playlist:
            self._centre(c, zone, "No webcams in the list", self.dim)
            return "skip" if now - self.started.setdefault(("list", entry.get("_play")), now) > 4 else None
        where = tuple(zone)                                   # each half of the wall has its own turn
        shape = self.shape_for(entry, zone)
        t = self._turns.get(where)
        if transition or t is None or t["play"] != entry.get("_play"):
            t = self._turns[where] = {"play": entry.get("_play"), "cam": None, "t0": now, "shown": 0, "failed": 0,
                                      "next": None}
            if self.from_top and not self._other_turns(t):    # a new turn starts at the top (unless the other half
                _list_pos["i"] = -1                           # of the wall is part-way through the list)
            up = sum(1 for cid, _ in self.playlist if not self.is_down(cid, now))
            t["limit"] = max(1, up) if self.per_turn is None else self.per_turn
        n = len(self.playlist)
        if t["cam"] is None:                                  # the turn's first webcam (prerolled by warm)
            if all(self.is_down(cid, now) for cid, _ in self.playlist):
                self._centre(c, zone, "Every webcam is down", self.dim)
                return "skip" if now - t["t0"] > 4 else None
            self._switch(t, self._next_up(_list_pos["i"], self._taken(t)), now)
        due = t["t0"] + self._sec(t["cam"])
        last = t["shown"] + 1 >= min(t.get("limit") or 1, n)  # nothing after this one in this turn
        if last and now >= due:
            self._turns.pop(where, None)                      # another pass of the same play starts afresh
            return "done"
        if not last and now >= due - PREROLL_SEC:
            nxt = self._lookahead(t, shape, zone, now, due)
            if nxt is None and now >= due:                    # nothing else is up: this one carries on (a slot all
                t["shown"] += 1                               # the same, so the turn still ends)
                t["t0"], t["wait0"] = now, None
            elif nxt is not None and now >= due:
                st = self._preroll(nxt, shape, zone)
                if not t.get("wait0"):
                    t["wait0"] = now                          # when this one became due to come up
                wait0 = t["wait0"]
                if st is not None and (st.ready() or self._mapped(nxt)):
                    t["shown"] += 1
                    self._switch(t, nxt, now)
                elif now - wait0 >= HOLD_MAX_SEC:
                    if st is not None and st.frame is not None:   # a picture, just late: cut to it
                        t["shown"] += 1
                        self._switch(t, nxt, now)
                    else:                                     # no picture at all: pass it over, the current one stays
                        self._mark_bad(nxt, (st.error if st is not None else "") or "no picture")
                        t["next"], t["wait0"] = None, now
        new, t["new"] = t.get("new", False), False
        r = self._draw_cam(c, zone, {**entry, "cam": t["cam"]}, new, black_skips=True)
        if r == "skip":                                       # this one won't come up: on to the next, uncounted
            t["failed"] += 1
            if t["failed"] >= n:
                self._turns.pop(where, None)
                return "skip"
            self._switch(t, self._next_up(_list_pos["i"], self._taken(t)), now)
        return None

    def _ahead(self, t, now, due):
        """The next LOOKAHEAD webcams of the list after the one on screen, with when each comes up: [(cam, start)]. The
        other half's webcams and ones that are down are passed over."""
        n = len(self.playlist)
        skip = self._taken(t) | {t["cam"]}
        out, start = [], due
        for step in range(1, n + 1):
            cid = self.playlist[(_list_pos["i"] + step) % n][0]
            if cid in skip or self.is_down(cid, now) or any(cid == c for c, _ in out):
                continue
            out.append((cid, start))
            start += self._sec(cid)
            if len(out) >= LOOKAHEAD:
                break
        return out

    def _lookahead(self, t, shape, zone, now, due):
        """The webcam that comes after the current one. The next LOOKAHEAD are prepared by how soon each comes up: the
        hub's feed from PREROLL_SEC before, the Pi's stream (buffering) from STREAM_AHEAD_SEC before, for at most
        PREROLL_STREAMS at once; with short turns several are getting ready together. The next one found down by then
        is passed over for the one after it, never shown."""
        for _ in range(len(self.playlist)):
            ahead = self._ahead(t, now, due)
            if not ahead:
                t["next"] = None
                return None                                   # the only one up is on screen now
            streams, bad = 0, None
            for i, (cid, start) in enumerate(ahead):
                lead = start - now
                if lead <= STREAM_AHEAD_SEC and streams < PREROLL_STREAMS:
                    st = self._preroll(cid, shape, zone)
                    streams += 1
                    if i == 0 and st is not None and st.down(now) and not self._mapped(cid):
                        bad = (cid, st)
                        break
                elif lead <= PREROLL_SEC:
                    self._warm_hub(cid, shape, zone)
            if bad is None:
                t["next"] = ahead[0][0]
                return t["next"]
            cid, st = bad
            self._mark_bad(cid, "black" if st.black_since is not None else (st.error or "no picture"))
        return None

    def _switch(self, t, cid, now):
        """Put ``cid`` on screen in this turn (the list carries on after it next time)."""
        _list_pos["i"] = next((i for i, (k, _) in enumerate(self.playlist) if k == cid), _list_pos["i"] + 1) \
            % len(self.playlist)
        t["cam"], t["t0"], t["new"], t["next"], t["wait0"] = cid, now, True, None, None

    def _sec(self, cid):
        return next((sec for k, sec in self.playlist if k == cid), WEBCAM_SEC)

    def _draw_cam(self, c, zone, entry, transition, black_skips=False):
        """One webcam. black_skips (the list): a picture gone black ends it; a webcam played on its own stays."""
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
        mapped = self._mapped(entry.get("cam"))
        if mapped:                                            # the ISS: its map while the video is unusable
            from display.modules import issmap, space
            data, t = self._space(now), now - space.LAG
            dark = cam.get("daylight_only", True) and (lambda a: space.in_daylight(data, a, a) is False)
            night = bool(dark) and dark(t)
            st = None
            if not (night and dark(t + STREAM_AHEAD_SEC)):    # the stream only from shortly before sunrise
                st = self._stream(cam, self.shape_for(entry, zone), w, h)
            if night or st is None or self._unusable(st, now):
                boxes = self.__dict__.setdefault("_map_caption", {})   # the caption's box last frame: the map keeps
                bk = (entry.get("cam"), w, h)                         # the ISS clear of it
                issmap.draw_full(c, zone, data, t, fixed=bool((cam.get("map") or {}).get("fixed")),
                                 caption_box=boxes.get(bk))
                boxes[bk] = draw_caption(c, zone, cam, now - started, self.font, extra={"space": data})
                return None
        else:
            st = self._stream(cam, self.shape_for(entry, zone), w, h)
        if not mapped and black_skips and st.black_since is not None and now - st.black_since >= BLACK_SEC:
            self._mark_bad(entry.get("cam"), "black")         # down but still sending: on to the next screen
            return "skip"
        if not mapped and st.in_step and st.overdue(now) > GIVE_UP_SEC:   # in step, nothing new this long: gone
            self._mark_bad(entry.get("cam"), st.error or "stopped")
            return "skip"
        if st.showable(now):
            c.Blit(st.current(CLOCK["tick"] or now), x, y)
            if getattr(st, "video", True):
                CLOCK["video_at"] = now                        # main.py: the loop runs at the video rate meanwhile
            if st.buffering():                                 # not prepared ahead (played by hand): load, then play
                _shade(c, x, y, w, h, 0.6)
                self._centre(c, zone, "Loading webcam feeds" + "." * (1 + int(now * 2) % 3), Color(225, 225, 225))
                if now - st._buffer_since > GIVE_UP_SEC:       # it never fills: on to the next screen
                    self._mark_bad(entry.get("cam"), st.error or "too slow to buffer")
                    return "skip"
                return None
        else:
            c.FillRect(x, y, w, h, Color(0, 0, 0))
            label = " ".join(str(cam.get("label") or entry.get("cam")).split())      # (a name may have lines)
            self._centre(c, zone, f"Tuning in to {label}" + "." * (1 + int(now * 2) % 3), self.dim)
            if st.error:
                self._centre(c, (x, y + 12, w, h), st.error[: max(8, w // 5)], Color(120, 60, 60))
            if now - max(started, st.frame_t) > GIVE_UP_SEC:      # a hiccup mid-play doesn't end a held webcam
                self._mark_bad(entry.get("cam"), st.error or "no picture")
                return "skip"
        wx = cam.get("_wx") or self._weather(now).get(entry.get("cam"))   # _wx: a preview of an unsaved location
        still = (cam.get("source") or {}).get("type") in ("drivebc", "image")
        extra = {"taken": round(st.frame_t - st.age)} if still and st.age is not None else None   # capture time (s)
        if cam.get("space"):                                  # in space: the ISS's orbit, crew and mission
            extra = {**(extra or {}), "space": self._space(now)}
        box = draw_caption(c, zone, cam, now - started, self.font, wx=wx, extra=extra)
        draw_small_map(c, zone, cam, (extra or {}).get("space"), now, box)
        return None

    def _space(self, now):
        """data/iss.json (hub/jobs/iss.py: the ISS's track, sunrises and sunsets, crew, mission), re-read when it
        changes (checked every 5 s)."""
        if self.data_dir and now - getattr(self, "_iss_checked", 0) > 5:
            self._iss_checked = now
            path = os.path.join(self.data_dir, "iss.json")
            try:
                mt = os.stat(path).st_mtime
                if mt != getattr(self, "_iss_mtime", None):
                    with open(path, encoding="utf-8") as f:
                        self._iss = json.load(f)
                    self._iss_mtime = mt
            except (OSError, ValueError):
                pass
        return getattr(self, "_iss", None)

    def _weather(self, now):
        """data/webcam_weather.json (hub/jobs/webcam_weather.py), re-read when it changes (checked every 5 s)."""
        if self.data_dir and now - self._wx_checked > 5:
            self._wx_checked = now
            path = os.path.join(self.data_dir, "webcam_weather.json")
            try:
                mt = os.stat(path).st_mtime
                if mt != self._wx_mtime:
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


def draw_small_map(c, zone, cam, data, now, caption_box=None):
    """The small ISS map in its corner of a webcam in space (``overlay.minimap``: on, corner), off the caption."""
    mm = overlay_of(cam).get("minimap")
    if not cam.get("space") or not isinstance(mm, dict) or not mm.get("on"):
        return
    from display.modules import issmap, space
    issmap.draw_small(c, zone, data, now - space.LAG, mm.get("corner") or "tr", caption_box)


def render_preview(cam: dict, frame, shape_zone, elapsed=0.0, wx=None, extra=None, show_map=False) -> np.ndarray:
    """The wall's pixels for one zone: a frame already cropped to the zone, plus the caption (control panel). A webcam
    in space: its small map too, or with show_map (or no frame) the full map its turn shows when the video can't."""
    from display.gfx import Canvas
    w, h = shape_zone
    c = Canvas(w, h)
    fonts = {}

    def font_for(size):
        f = fonts.get(size)
        if f is None:
            f = fonts[size] = graphics.Font()
            f.LoadFont(FONTS.get(size, size))
        return f

    space_data = (extra or {}).get("space")
    full_map = bool(cam.get("space")) and (show_map or frame is None) \
        and (cam.get("map") or {}).get("when_down", True) is not False
    if full_map:
        from display.modules import issmap, space
        box = draw_caption(Canvas(w, h), (0, 0, w, h), cam, elapsed, font_for, wx=wx, extra=extra)   # (where it goes)
        c.Blit(issmap.render(w, h, space_data, time.time() - space.LAG, True,
                             bool((cam.get("map") or {}).get("fixed")), issmap.reserve_for(box, h)), 0, 0)
    elif frame is not None:
        c.Blit(frame, 0, 0)
    box = draw_caption(c, (0, 0, w, h), cam, elapsed, font_for, wx=wx, extra=extra)
    if not full_map:
        draw_small_map(c, (0, 0, w, h), cam, space_data, time.time(), box)
    return c.buf.copy()
