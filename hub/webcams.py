"""Live webcam video for the wall.

The Pi has no CPU to spare for decoding video, so the hub does it: one ffmpeg per source, started when someone
watches (the wall or the control panel) and stopped a little after the last one leaves. ffmpeg hands over raw
frames scaled to WORK_W wide; each viewer gets its own crop of them, scaled to its zone and colour-adjusted.

Sources (``modules.webcams.cams.<id>.source``):
    {"type": "youtube", "url": "https://www.youtube.com/watch?v=…"}   live stream; resolved with yt-dlp
    {"type": "stream",  "url": "https://…/index.m3u8"}                HLS, MP4, MJPEG, RTSP… anything ffmpeg opens
    {"type": "drivebc", "id": 18}                                       a DriveBC highway camera (a still, every few min)
    {"type": "image",   "url": "https://…/cam.jpg", "refresh_sec": 60}  any still image, fetched again every refresh_sec
Stills become a feed too: the latest image is published every second (so viewers know it's alive) and fetched again
every refresh_sec; each frame carries the image's age for the caption.

A crop is ``{"x", "y", "w"}`` in fractions of the source frame (left edge, top edge, width); its height follows
from the zone's shape, so a crop never stretches the picture. No crop: the largest centred one that fits.
"""

import collections
import os
import queue
import re
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

import numpy as np
from PIL import Image

WORK_W = 640                 # decoded frames are this wide; crops come out of these
MAX_FEEDS = 6                # ffmpegs at once (the wall's list prepares a few ahead: display/modules/webcam.py LOOKAHEAD)
GRACE_SEC = 15.0             # a feed outlives its last viewer (or warm ask, every 10 s) by this long
STALL_SEC = 20.0             # nothing decoded for this long: restart ffmpeg
BUFFER_SEC = 3.5             # playout buffer for HLS (YouTube): longer than a segment, so fetching the next one never
                             # shows as a freeze
BUFFER_SEC_LIVE = 0.3        # … for sources that already arrive in real time (RTSP, MJPEG)
BUFFER_HEADROOM = 4.0        # frames kept beyond the buffer (about one long segment) before the oldest are dropped
RECENT_FRAMES = 90           # published frames kept per feed (3 s at 30 fps) for viewers catching up
DECODED_SEC = 6.0            # decoded frames kept per feed for the wall's stream, which takes them as decoded (the
                             # Pi evens them out): a new viewer starts with these, a slow one catches up through them
OPEN_SEC = 45.0              # … or no first frame this long after starting it
RESOLVE_TTL = 3 * 3600.0     # YouTube manifest URLs last ~6 h; resolve again after 3 (fewer lookups: see BOT_COOL)
BOT_COOL = 20 * 60.0         # YouTube asked the hub to prove it isn't a bot: no new lookups for this long
SCHEMES = {"http", "https", "rtsp", "rtsps", "rtmp", "rtmps"}
PROTOCOLS = "http,https,tcp,tls,crypto,httpproxy,rtsp,rtsps,rtmp,rtmps,rtmpt"     # network only: no file:, data:
NOISE = ("frame=", "  ", "Input #", "Output #", "Stream mapping", "Press [q]")
SIZE_RE = re.compile(r"Stream #\d+:\d+.*Video: rawvideo.*?, (\d+)x(\d+)")


class SourceError(ValueError):
    pass


STILLS = ("drivebc", "image")
DRIVEBC_IMAGE = "https://www.drivebc.ca/images/{id}.jpg"


def check_source(src) -> tuple[str, str]:
    """(type, url) of a source dict, or SourceError."""
    if not isinstance(src, dict):
        raise SourceError("source must be an object")
    kind = src.get("type", "stream")
    if kind == "drivebc":
        cid = src.get("id")
        if not isinstance(cid, int) or isinstance(cid, bool) or cid <= 0:
            raise SourceError("a DriveBC camera needs its numeric id")
        return kind, DRIVEBC_IMAGE.format(id=cid)
    url = str(src.get("url", "")).strip()
    if kind not in ("youtube", "stream", "image"):
        raise SourceError("source type must be youtube, stream, image or drivebc")
    schemes = {"http", "https"} if kind == "image" else SCHEMES
    if urlparse(url).scheme.lower() not in schemes or not urlparse(url).netloc:
        raise SourceError("an image URL must be http(s)" if kind == "image" else "source URL must be http(s), rtsp or rtmp")
    return kind, url


def refresh_of(src) -> float:
    """How often a still is fetched again (s)."""
    try:
        return max(20.0, min(3600.0, float((src or {}).get("refresh_sec") or 60)))
    except (TypeError, ValueError):
        return 60.0


DRIVEBC_CAMERA = "https://www.drivebc.ca/api/webcams/{id}/"
UA = {"User-Agent": "BigScreen/2 (home LED wall)"}


def drivebc_meta(url):
    """(when the camera's current picture was taken, why it's unavailable or "") from DriveBC's camera API.
    DriveBC's image responses carry no Last-Modified, so the time comes from here."""
    from datetime import datetime

    import requests
    cid = url.rsplit("/", 1)[-1].split(".")[0]
    m = requests.get(DRIVEBC_CAMERA.format(id=cid), timeout=15, headers=UA).json()
    why = "camera offline" if m.get("is_on") is False else "camera stale" if m.get("marked_stale") else ""
    try:
        at = datetime.fromisoformat(m["last_update_modified"]).timestamp()
    except (KeyError, TypeError, ValueError):
        at = None
    return at, why


def fetch_still(kind, url, known_at=None, meta=True):
    """(RGB array WORK_W wide, the image's time) for a still source. DriveBC's caption strip (the bottom ~6 %) goes.
    For DriveBC, when the picture is still the one taken at known_at, nothing is downloaded: (None, known_at).
    meta=False skips DriveBC's camera API (thumbnails): the time is then the fetch time."""
    import io
    from email.utils import parsedate_to_datetime

    import requests
    dbc_at = None
    if kind == "drivebc" and meta:
        try:
            dbc_at, why = drivebc_meta(url)
        except Exception:                                   # the camera API is down: the image may still be fine
            dbc_at, why = None, ""
        if why and known_at is None:
            raise SourceError(why)
        if known_at is not None and dbc_at is not None and dbc_at == known_at:
            return None, known_at
    r = requests.get(url, timeout=15, headers=UA)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    w, h = img.size
    if kind == "drivebc":
        img = img.crop((0, 0, w, int(h * 0.94)))
        w, h = img.size
    img = img.resize((WORK_W, max(2, round(h * WORK_W / w / 2) * 2)), Image.LANCZOS)
    if dbc_at is not None:
        at = dbc_at
    else:
        try:
            at = parsedate_to_datetime(r.headers["Last-Modified"]).timestamp()
        except (KeyError, TypeError, ValueError):
            at = time.time()
    return np.ascontiguousarray(np.asarray(img), dtype=np.uint8), min(at, time.time())


# --- resolving -----------------------------------------------------------------------------------------------
_resolved: dict = {}         # youtube url -> (media url, title, at)
_yt_block = {"until": 0.0}   # no YouTube lookups before this (after a bot check)


class BotCheck(SourceError):
    """YouTube refused the hub as a suspected bot: the hub is throttled, the webcam isn't down."""


def is_bot_check(text) -> bool:
    """YouTube refusing the hub (or the hub's own pause after that): never a sign the webcam is down."""
    t = str(text or "").lower()
    return any(s in t for s in ("not a bot", "sign in to confirm", "http error 429", "bot check", "limiting the hub"))
_resolve_lock = threading.Lock()


def deno_path() -> str:
    """deno for yt-dlp's YouTube challenges (some streams fail without a JS runtime): $BIGSCREEN_DENO, the user-level
    install (~/.deno/bin, not on the service's PATH), or deno on the PATH. "" when there is none."""
    import shutil
    from pathlib import Path
    for cand in (os.environ.get("BIGSCREEN_DENO"), str(Path.home() / ".deno" / "bin" / "deno"), shutil.which("deno")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return ""


def _yt_dlp(*args, timeout=60):
    deno = deno_path()
    js = ["--js-runtimes", f"deno:{deno}"] if deno else []
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist", *js, *args],
                          capture_output=True, text=True, timeout=timeout)


def resolve(kind, url, fresh=False, timeout=60) -> tuple[str, str]:
    """(media URL for ffmpeg, title). YouTube pages go through yt-dlp: video only, 480p is plenty for 448 px."""
    if kind != "youtube":                               # streams and stills are used as given
        return url, ""
    with _resolve_lock:
        hit = _resolved.get(url)
        if hit and not fresh and time.time() - hit[2] < RESOLVE_TTL:
            return hit[0], hit[1]
    wait = _yt_block["until"] - time.time()
    if wait > 0:                                        # throttled: asking again only makes it last longer
        raise BotCheck(f"YouTube is limiting the hub (bot check); trying again in {int(wait // 60) + 1} min")
    try:
        r = _yt_dlp("-f", "bv*[height<=480]/bv*/b", "--print", "%(title)s", "--print", "%(live_status)s",
                    "-g", url, timeout=timeout)
    except FileNotFoundError:
        raise SourceError("yt-dlp is not installed on the hub")
    except subprocess.TimeoutExpired:
        raise SourceError("YouTube took too long to answer")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if r.returncode or len(lines) < 3:
        err = (r.stderr.strip().splitlines() or ["yt-dlp failed"])[-1]
        if is_bot_check(err):
            _yt_block["until"] = time.time() + BOT_COOL
            print(f"[webcams] YouTube bot check: no lookups for {int(BOT_COOL // 60)} min ({err[:120]})", flush=True)
            raise BotCheck(err.replace("ERROR: ", "")[:300])
        raise SourceError(err.replace("ERROR: ", "")[:300])
    title, status, media = lines[0], lines[1], lines[-1]
    if urlparse(media).scheme.lower() not in SCHEMES:
        raise SourceError("YouTube gave an unexpected stream address")
    if status not in ("is_live", "post_live", "NA", "None"):
        title = f"{title} (not live: {status})"
    with _resolve_lock:
        if len(_resolved) > 200:
            _resolved.clear()
        _resolved[url] = (media, title, time.time())
    return media, title


# --- feeds -----------------------------------------------------------------------------------------------------
class Feed:
    """One source decoded by one ffmpeg; the newest frame is always in ``frame`` (h, WORK_W, 3) uint8."""

    def __init__(self, kind, url, fps, refresh=60.0):
        self.kind, self.url, self.fps = kind, url, fps
        self.frame = None
        self.seq = 0
        self.frame_t = 0.0
        self.size = None
        self.title = ""
        self.state = "starting"          # starting | live | retrying | stopped
        self.error = ""
        self.viewers = 0
        self.last_view = time.time()
        self.started = time.time()
        self.restarts = 0
        self._times = []
        self._proc = None
        self._restart = False
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._buf = collections.deque()  # decoded frames waiting for their turn (the pacer publishes them)
        self._buf_lock = threading.Lock()
        self.recent = collections.deque(maxlen=RECENT_FRAMES)
        self.decoded = collections.deque(maxlen=int(DECODED_SEC * max(1, fps)) + 1)   # (dseq, frame), as decoded
        self.dseq = 0   # (seq, frame) just published: a viewer that fell
                                                                # behind (a busy hub) catches up instead of skipping
        self._buffer_sec = BUFFER_SEC
        self._new_run = False
        self.decoded_t = 0.0
        self.underflows = 0
        self._health_t = 0.0             # last time what it shows was noted (note_health)
        self._stop = threading.Event()   # set by _run when the feed ends; the pacer exits on it
        self._waiters = set()            # (event loop, asyncio.Event) of async readers, woken on every frame
        self.image_t = None              # stills: when the current image was taken
        self.refresh = refresh
        self._still = None
        threading.Thread(target=self._run, name=f"webcam-{urlparse(url).netloc}", daemon=True).start()
        if kind not in STILLS:                              # stills publish directly
            threading.Thread(target=self._pace, name="webcam-pacer", daemon=True).start()

    # viewers -------------------------------------------------------------------------------------------------
    def attach(self):
        with self._lock:
            self.viewers += 1
            self.last_view = time.time()

    def detach(self):
        with self._lock:
            self.viewers = max(0, self.viewers - 1)
            self.last_view = time.time()

    def add_waiter(self, loop, event):
        with self._lock:
            self._waiters.add((loop, event))

    def remove_waiter(self, loop, event):
        with self._lock:
            self._waiters.discard((loop, event))

    def _cap(self):
        return int((self._buffer_sec + BUFFER_HEADROOM) * max(1, self.fps))

    def wanted(self):
        return self.viewers > 0 or time.time() - self.last_view < GRACE_SEC

    def raise_fps(self, fps):
        """Decode at least this fast (viewers wanting fewer frames skip some); restarts ffmpeg if it has to."""
        if self.kind in STILLS or fps <= self.fps:
            return
        self.fps = fps
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._restart = True
            proc.kill()

    def after(self, seq):
        """The first frame published after ``seq`` that's still kept: (its seq, frame), or None if there's none yet."""
        with self._cond:
            if self.seq <= seq or not self.recent:
                return None
            first = self.recent[0][0]
            i = max(0, seq + 1 - first)                     # fell further behind than kept: the oldest kept
            return self.recent[i] if i < len(self.recent) else None

    def decoded_after(self, seq):
        """The first decoded frame after ``seq`` still kept: (its dseq, frame), or None if there's none yet."""
        with self._cond:
            if self.dseq <= seq or not self.decoded:
                return None
            first = self.decoded[0][0]
            i = max(0, seq + 1 - first)
            return self.decoded[i] if i < len(self.decoded) else None

    def oldest_decoded(self):
        """The dseq just before the oldest decoded frame still kept (a new viewer starts there), or None."""
        with self._cond:
            return self.decoded[0][0] - 1 if self.decoded else None

    def wait(self, after_seq, timeout):
        """The next frame after ``after_seq``: (seq, frame), or (after_seq, None) on timeout."""
        with self._cond:
            if self.seq <= after_seq:
                self._cond.wait(timeout)
            if self.seq <= after_seq or self.frame is None:
                return after_seq, None
            return self.seq, self.frame

    def measured_fps(self):
        t = self._times
        return round((len(t) - 1) / (t[-1] - t[0]), 1) if len(t) > 2 and t[-1] > t[0] else 0.0

    def describe(self):
        return {"type": self.kind, "url": self.url, "fps": self.fps, "state": self.state, "error": self.error,
                "title": self.title, "size": self.size, "viewers": self.viewers, "measured_fps": self.measured_fps(),
                "age_sec": round(time.time() - self.frame_t, 1) if self.frame_t else None,
                "uptime_sec": round(time.time() - self.started), "restarts": self.restarts,
                "buffered_sec": round(len(self._buf) / max(1, self.fps), 1), "underflows": self.underflows,
                "still": self.kind in STILLS,
                "image_age_sec": round(time.time() - self.image_t) if self.image_t else None}

    # the ffmpeg loop ------------------------------------------------------------------------------------------
    def _run(self):
        if self.kind in STILLS:
            return self._run_still()
        backoff = 0
        fresh = False
        while self.wanted():
            try:
                media, title = resolve(self.kind, self.url, fresh)
                self.title = title or self.title
                if not self.wanted():                  # yt-dlp can take a while; nobody may be left
                    break
                self._restart = False
                with self._lock:
                    self.state = "starting"            # the pacer makes it "live" with the first frame it shows
                got = self._decode(media)
                if self._restart:                      # a viewer wanted a higher frame rate: go again now
                    self.error = ""
                    continue
                if got:
                    backoff = 0
                fresh = self.kind == "youtube"         # a YouTube URL that stopped working may have expired
                if not self.error:
                    self.error = "the stream ended"
            except BotCheck as e:                      # the hub is throttled, not the webcam: not "down"
                self.error = str(e)
            except SourceError as e:
                self.error = str(e)
                note_health(self.kind, self.url, False, str(e))
            except Exception as e:                     # never let a feed thread die silently
                self.error = f"{type(e).__name__}: {e}"
            if not self.wanted():
                break
            with self._lock:
                self.state = "retrying"
            self.restarts += 1
            if self.restarts >= 3 and self.error and not is_bot_check(self.error):
                note_health(self.kind, self.url, False, self.error)     # it keeps failing: down
            delay = (5, 15, 30, 60)[min(backoff, 3)]
            backoff += 1
            end = time.time() + delay
            while time.time() < end and self.wanted():
                time.sleep(0.5)
        with self._lock:
            self.state = "stopped"
        self._stop.set()
        _forget(self)

    def _run_still(self):
        """A still source: fetch the image every refresh_sec, publish the latest one every second."""
        next_fetch, failures = 0.0, 0
        while self.wanted():
            now = time.time()
            if now >= next_fetch:
                try:
                    frame, at = fetch_still(self.kind, self.url, self.image_t if self._still is not None else None)
                    if frame is not None:                    # a new picture (DriveBC: only when it posted one)
                        frame.flags.writeable = False
                        self._still, self.size = frame, [frame.shape[1], frame.shape[0]]
                    self.image_t, self.error, failures = at, "", 0
                    next_fetch = now + self.refresh
                except Exception as e:
                    failures += 1
                    self.error = f"{type(e).__name__}: {e}"[:300]
                    next_fetch = now + min(300, 15 * failures)
                    if self._still is None:
                        with self._lock:
                            self.state = "retrying"
            if self._still is not None:
                with self._cond:
                    self.frame, self.frame_t = self._still, now
                    self.seq += 1
                    if self.state != "live":
                        self.state = "live"
                    waiters = list(self._waiters)
                    self._cond.notify_all()
                for loop, ev in waiters:
                    try:
                        loop.call_soon_threadsafe(ev.set)
                    except RuntimeError:
                        pass
                self._times = (self._times + [now])[-60:]
            self._stop.wait(1.0)
        with self._lock:
            self.state = "stopped"
        self._stop.set()
        _forget(self)

    def _decode(self, media) -> bool:
        """Run ffmpeg until the stream ends, stalls or nobody wants it. True if any frame arrived."""
        # -nostats: the progress line ends in \r, which readline would keep growing for hours
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info", "-protocol_whitelist", PROTOCOLS]
        scheme = urlparse(media).scheme.lower()
        if scheme in ("http", "https"):
            args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                     "-user_agent", "BigScreen/2 (home LED wall)"]
            if self.kind == "youtube" or ".m3u8" in media:
                # HLS arrives a segment (2-5 s) at a time and ffmpeg decodes each as soon as it lands: the pacer
                # (_pace) evens that out from a buffer. Starting two segments back fills the buffer at once.
                # (Reading with -re instead froze the picture while every next segment downloaded.)
                args += ["-live_start_index", "-2"]
                self._buffer_sec = BUFFER_SEC
            else:
                self._buffer_sec = BUFFER_SEC_LIVE
        elif scheme.startswith("rtsp"):
            args += ["-rtsp_transport", "tcp"]
            self._buffer_sec = BUFFER_SEC_LIVE
        args += ["-i", media, "-an", "-sn", "-dn", "-vf", f"fps={self.fps},scale={WORK_W}:-2:flags=area",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        except FileNotFoundError:
            raise SourceError("ffmpeg is not installed on the hub")
        self._proc = proc
        self._new_run = True                       # the pacer restarts its clock (the fps may have changed)
        with self._buf_lock:
            self._buf.clear()                      # frames from a previous run would play out of order
        size_box, tail = [], []
        threading.Thread(target=self._stderr, args=(proc, size_box, tail), daemon=True).start()
        threading.Thread(target=self._watchdog, args=(proc,), daemon=True).start()
        got = False
        try:
            t0 = time.time()
            while not size_box:                        # the output size is in ffmpeg's banner
                if proc.poll() is not None or time.time() - t0 > 45:
                    self.error = (tail[-1] if tail else "ffmpeg could not open the stream")[:300]
                    return False
                time.sleep(0.05)
            w, h = size_box[0]
            self.size = [w, h]
            n = w * h * 3
            while True:
                frame = np.empty((h, w, 3), np.uint8)    # read straight into the frame: no per-frame copies
                view = memoryview(frame).cast("B")
                got_n = 0
                while got_n < n:
                    k = proc.stdout.readinto(view[got_n:])
                    if not k:
                        if tail and not got:
                            self.error = tail[-1][:300]
                        return got
                    got_n += k
                frame.flags.writeable = False             # shared by every reader once published
                with self._buf_lock:
                    self._buf.append(frame)
                    while len(self._buf) > self._cap():          # bounded memory, even while re-buffering
                        self._buf.popleft()
                with self._cond:                           # the wall's stream takes it now (not paced)
                    self.dseq += 1
                    if self.decoded.maxlen != int(DECODED_SEC * max(1, self.fps)) + 1:
                        self.decoded = collections.deque(self.decoded, maxlen=int(DECODED_SEC * max(1, self.fps)) + 1)
                    self.decoded.append((self.dseq, frame))
                    waiters = list(self._waiters)
                for loop, ev in waiters:
                    try:
                        loop.call_soon_threadsafe(ev.set)
                    except RuntimeError:
                        pass
                self.decoded_t = time.time()
                if not got:
                    got, self.error = True, ""
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def _pace(self):
        """Publish buffered frames at an even 1/fps, _buffer_sec behind. (Re)starts once frames have been arriving
        for _buffer_sec, by time rather than count: an HLS segment lands all at once, and starting on its frame count
        alone would leave no margin before the next one (a freeze per segment). Ends when the feed stops."""
        t0, k, first = None, 0, None
        while not self._stop.is_set():
            if self._new_run:
                self._new_run, t0, first = False, None, None
            fps = max(1, self.fps)
            if t0 is None:
                with self._buf_lock:
                    n = len(self._buf)
                now = time.time()
                if not n:
                    first = None
                    self._stop.wait(0.02)
                    continue
                first = first or now
                if now - first < self._buffer_sec and n < 2 * self._buffer_sec * fps:
                    self._stop.wait(0.02)
                    continue
                t0, k = now, 0
            due = t0 + k / fps
            now = time.time()
            if due > now:
                self._stop.wait(min(0.1, due - now))
                continue
            with self._buf_lock:
                frame = self._buf.popleft() if self._buf else None
            if frame is None:                                     # ran dry: hold the picture and re-buffer
                if not self._new_run:                             # (a restart empties it on purpose)
                    self.underflows += 1
                t0 = first = None
                continue
            with self._cond:
                self.frame, self.frame_t = frame, now
                self.seq += 1
                self.recent.append((self.seq, frame))
                if self.state == "starting":
                    self.state = "live"
                waiters = list(self._waiters)
                self._cond.notify_all()
            for loop, ev in waiters:
                try:
                    loop.call_soon_threadsafe(ev.set)
                except RuntimeError:                              # that reader's loop is gone
                    pass
            self._times = (self._times + [now])[-60:]
            if now - self._health_t >= HEALTH_EVERY:          # what it shows: up, or black (down)
                self._health_t = now
                black = is_black(frame)
                note_health(self.kind, self.url, not black, "black" if black else "")
            k += 1
            if now - due > 1.0:                                   # the clock slipped (a long pause): start over
                t0, k = now, 0

    def _stderr(self, proc, size_box, tail):
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not size_box:
                m = SIZE_RE.search(line)
                if m:
                    size_box.append((int(m.group(1)), int(m.group(2))))
            if line and not line.startswith(NOISE) and "Opening '" not in line:
                tail[:] = (tail + [line])[-8:]

    def _watchdog(self, proc):
        """Stop ffmpeg when nobody wants the feed any more, or when frames stopped coming (or never came:
        a stream that opens but sends nothing would otherwise block forever)."""
        run_start = time.time()
        while proc.poll() is None:
            time.sleep(1.0)
            had_frame = self.decoded_t > run_start
            last = self.decoded_t if had_frame else run_start
            limit = STALL_SEC if had_frame else OPEN_SEC
            if not self.wanted():
                proc.kill()
            elif time.time() - last > limit:
                self.error = f"no picture for {int(limit)} s"
                proc.kill()


_feeds: dict = {}            # (type, url) -> Feed
_feeds_lock = threading.Lock()
_last_frames: "collections.OrderedDict" = collections.OrderedDict()   # (type, url) -> a stopped feed's last frame
LAST_FRAMES = 24             # kept (about 0.7 MB each)


def _forget(feed):
    with _feeds_lock:
        for k, f in list(_feeds.items()):
            if f is feed:
                del _feeds[k]
        if feed.frame is not None and feed.kind not in STILLS:
            key = (feed.kind, feed.url)
            _last_frames[key] = feed.frame
            _last_frames.move_to_end(key)
            while len(_last_frames) > LAST_FRAMES:
                _last_frames.popitem(last=False)


def still_for(src):
    """A picture of a live source to show while its feed starts, so a screen never opens on "loading": the last
    frame it decoded (when it last played), else its snapshot (the panel's thumbnail, any age; a stale one is
    refreshed in the background). None when there's neither. Never waits on the network."""
    try:
        kind, url = check_source(src)
    except SourceError:
        return None
    if kind in STILLS:
        return None
    with _feeds_lock:
        f = _feeds.get((kind, url))
        if f is not None and f.frame is not None:
            return f.frame
        hit = _last_frames.get((kind, url))
    if hit is not None:
        return hit
    try:
        jpg, _ = snapshot(src)
    except Exception:
        return None
    if not jpg:
        return None
    import io
    try:
        return np.ascontiguousarray(np.asarray(Image.open(io.BytesIO(jpg)).convert("RGB")), dtype=np.uint8)
    except Exception:
        return None


def feed(src, fps=15) -> Feed:
    """The running feed for a source, started if needed (raises SourceError). One feed per source, decoding at
    the highest frame rate anyone asked for; viewers that want fewer thin it themselves (app.py min_gap)."""
    kind, url = check_source(src)
    fps = 1 if kind in STILLS else max(1, min(30, int(fps or 15)))
    key = (kind, url)
    with _feeds_lock:
        f = _feeds.get(key)
        if f is not None and f.state != "stopped":
            f.last_view = time.time()
            f.raise_fps(fps)
            if kind in STILLS:
                f.refresh = refresh_of(src)
            return f
        live = [x for x in _feeds.values() if x.state != "stopped" and x.kind not in STILLS]
        if kind not in STILLS and len(live) >= MAX_FEEDS:
            idle = [x for x in live if x.viewers == 0]
            if not idle:
                raise SourceError(f"already decoding {MAX_FEEDS} webcams")
            stale = min(idle, key=lambda x: x.last_view)
            stale.last_view = 0                         # its watchdog stops it within a second
            _feeds.pop((stale.kind, stale.url), None)
        f = _feeds[key] = Feed(kind, url, fps, refresh_of(src))
        return f


def describe():
    with _feeds_lock:
        return [f.describe() for f in _feeds.values()]


# --- snapshots (the panel's gallery and list) -----------------------------------------------------------------
# Requests never wait for a grab: they get the cached still (fresh or stale) at once, or None while background
# workers grab it. A source that failed is not tried again for SNAP_RETRY, so dead streams cost nothing per page load.
SNAP_TTL = 3600.0            # a still older than this is refreshed (and served meanwhile)
SNAP_RETRY = 600.0           # after a failed grab
SNAP_KEEP = 500              # stills kept on disk (the gallery has ~100 webcams, plus DriveBC)
SNAP_WORKERS = 4             # grabs at once: each is ~5 s of yt-dlp + ~4 s of ffmpeg, mostly waiting on the network
_snap_queue: "queue.Queue" = queue.Queue()
_snap_busy: set = set()      # keys queued or being grabbed
_snap_failed: dict = {}      # key -> (when, error)
_snap_lock = threading.Lock()
_snap_workers: list = []


def _snap_path(kind, url):
    import hashlib

    from hub import paths
    d = paths.CACHE_DIR / "webcam_snaps"
    d.mkdir(parents=True, exist_ok=True)
    return d / (hashlib.sha1(f"{kind}|{url}".encode()).hexdigest()[:16] + ".jpg")


def snapshot(src):
    """(jpeg bytes or None, status): "fresh", "stale" (a refresh is queued), "pending" (being grabbed; ask again
    in a few seconds) or "failed: <why>" (not retried for SNAP_RETRY). Never blocks on ffmpeg."""
    kind, url = check_source(src)
    key = (kind, url)
    path = _snap_path(kind, url)
    data = age = None
    try:
        age = time.time() - path.stat().st_mtime
        data = path.read_bytes()
    except OSError:
        pass
    if data is not None and age < SNAP_TTL:
        return data, "fresh"
    with _snap_lock:
        failed = _snap_failed.get(key)
        recently_failed = failed and time.time() - failed[0] < SNAP_RETRY
        if not recently_failed and key not in _snap_busy:
            _snap_busy.add(key)
            _snap_queue.put(key)
            while len(_snap_workers) < SNAP_WORKERS:
                t = threading.Thread(target=_snap_worker, name="webcam-snap", daemon=True)
                t.start()
                _snap_workers.append(t)
    if data is not None:
        return data, "stale"
    if recently_failed:
        return None, f"failed: {failed[1]}"
    return None, "pending"


def _snap_worker():
    while True:
        key = _snap_queue.get()
        try:
            _grab(*key)
            with _snap_lock:
                _snap_failed.pop(key, None)
        except Exception as e:
            msg = str(e) if isinstance(e, SourceError) else f"no picture: {e}"
            with _snap_lock:
                _snap_failed[key] = (time.time(), msg[:200])
                if len(_snap_failed) > 500:
                    _snap_failed.clear()
        finally:
            with _snap_lock:
                _snap_busy.discard(key)


BLACK_LEVEL = 24             # a pixel brighter than this (its brightest channel) counts as lit
BLACK_LIT = 0.001            # a picture with fewer lit pixels than this share is black: strict, since a dark night
                             # scene still has a few (lights, sky glow, the encoder's noise); a dead feed has none


def is_black(frame) -> bool:
    """A feed that's down but still sending: an all-black (or nearly) picture. Looks at every 4th pixel."""
    if frame is None or not getattr(frame, "size", 0):
        return False
    sub = np.asarray(frame)[::4, ::4]
    return float((sub.max(axis=-1) > BLACK_LEVEL).mean()) < BLACK_LIT


def grab_frame(kind, url, timeout=40) -> np.ndarray:
    """One current picture from a source, as RGB (480 wide for live streams). SourceError when there's none.
    ``timeout`` bounds each step (yt-dlp, then ffmpeg; ffmpeg also gives up on a network read stalled for 15 s)."""
    if kind in STILLS:
        return fetch_still(kind, url)[0]
    import io
    media, _ = resolve(kind, url, timeout=timeout)
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-protocol_whitelist", PROTOCOLS]
    if urlparse(media).scheme.lower() in ("http", "https"):
        args += ["-rw_timeout", "15000000"]
    if kind == "youtube" or ".m3u8" in media:
        args += ["-live_start_index", "-1"]
    args += ["-i", media, "-frames:v", "1", "-vf", "scale=480:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "pipe:1"]
    try:
        r = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise SourceError("no picture in time")
    if r.returncode or not r.stdout:
        raise SourceError((r.stderr.decode("utf-8", "replace").strip().splitlines() or ["no picture"])[-1][:200])
    return np.asarray(Image.open(io.BytesIO(r.stdout)).convert("RGB"))


_YT_ID = re.compile(r"(?:v=|youtu\.be/|/live/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")


def youtube_id(url) -> str:
    """The 11-character video id in a YouTube URL, or "" (a channel's /live address has none)."""
    m = _YT_ID.search(str(url or ""))
    return m.group(1) if m else ""


def youtube_thumb(vid) -> bytes:
    """A live stream's current thumbnail from i.ytimg.com (refreshed by YouTube every few minutes)."""
    import requests
    for name in ("maxresdefault_live", "hqdefault_live", "mqdefault_live"):
        r = requests.get(f"https://i.ytimg.com/vi/{vid}/{name}.jpg", timeout=10, headers=UA)
        if r.status_code == 200 and len(r.content) > 2000:
            return r.content
    raise SourceError("no thumbnail")


# --- health from what the feeds do (hub/jobs/webcam_health.py leaves YouTube to this) ----------------------------
HEALTH_EVERY = 60.0          # a playing feed's picture is checked for black this often
_health_seen: dict = {}      # (type, url) -> (up, why) last noted
_health_lock = threading.Lock()


def note_health(kind, url, up, why=""):
    """A feed's own verdict: up (a lit picture), or down (black, or failing again and again). Written to
    data/webcam_health.json for every webcam with this source, only when it changes; never blocks the feed."""
    if not up and is_bot_check(why):                  # the hub is throttled, not the webcam
        return
    key, val = (kind, url), (bool(up), "" if up else str(why)[:200])
    with _health_lock:
        if _health_seen.get(key) == val:
            return
        _health_seen[key] = val
    threading.Thread(target=_write_health, args=(kind, url, *val), name="webcam-health", daemon=True).start()


def _write_health(kind, url, up, why):
    try:
        from hub import config, paths, sync
        from hub.util import atomic_write_json, read_json
        cams = ((config.load().get("modules") or {}).get("webcams") or {}).get("cams") or {}
        ids = []
        for cid, cam in cams.items():
            try:
                if isinstance(cam, dict) and check_source(cam.get("source")) == (kind, url):
                    ids.append(cid)
            except SourceError:
                pass
        if not ids:
            return
        path = paths.DATA_DIR / "webcam_health.json"
        now = time.time()
        with _health_lock:
            data = read_json(path, {}) or {}
            for cid in ids:
                old = data.get(cid) if isinstance(data.get(cid), dict) else {}
                since = old.get("since") if old.get("up") == up and old.get("since") else now
                data[cid] = {"up": up, "why": why, "since": since, "checked": now, "by": "feed"}
            atomic_write_json(path, data)
        sync.push_data()
    except Exception as e:
        print(f"[webcams] health not written: {e}", flush=True)


# --- fetch every feed (the panel's "Fetch all feeds") ----------------------------------------------------------
FETCH_GAP = 2.5              # seconds between webcams: one at a time, never a burst (YouTube bot checks)
_fetch_all = {"running": False, "done": 0, "total": 0, "results": {}, "stopped": "", "started": 0.0, "finished": 0.0}
_fetch_lock = threading.Lock()


def fetch_all_status() -> dict:
    with _fetch_lock:
        return {**_fetch_all, "results": dict(_fetch_all["results"])}


def start_fetch_all(cams: dict) -> dict:
    """Fetch every webcam's feed now, one after another: a YouTube lookup (kept RESOLVE_TTL, so the wall then starts
    it with no lookup) and a fresh thumbnail. Stops at a YouTube bot check rather than pushing on. In the background;
    fetch_all_status() follows it."""
    with _fetch_lock:
        if _fetch_all["running"]:
            return {**_fetch_all, "results": dict(_fetch_all["results"])}
        todo = [(cid, cam.get("source")) for cid, cam in cams.items() if isinstance(cam, dict)]
        _fetch_all.update(running=True, done=0, total=len(todo), results={}, stopped="", started=time.time(),
                          finished=0.0)
    threading.Thread(target=_fetch_all_run, args=(todo,), name="webcam-fetch-all", daemon=True).start()
    return fetch_all_status()


def _fetch_all_run(todo):
    try:
        for i, (cid, src) in enumerate(todo):
            wait = _yt_block["until"] - time.time()
            res = {"ok": False, "msg": ""}
            try:
                kind, url = check_source(src)
                if kind == "youtube" and wait > 0:
                    raise BotCheck(f"YouTube is limiting the hub (bot check); try again in {int(wait // 60) + 1} min")
                if kind == "youtube":
                    _, title = resolve(kind, url)
                    res = {"ok": True, "msg": "ready" + (f": {title}"[:120] if "not live" in title else "")}
                    if "not live" not in title:
                        note_health(kind, url, True)        # it came up: no longer "down"
                elif kind in STILLS:
                    _, at = fetch_still(kind, url)
                    res = {"ok": True, "msg": f"still from {int(max(0, time.time() - at) // 60)} min ago"}
                else:
                    res = {"ok": True, "msg": "ready"}
                try:
                    _grab(kind, url)                    # the list's thumbnail (YouTube: from i.ytimg.com)
                except Exception:
                    pass
            except BotCheck as e:
                with _fetch_lock:
                    _fetch_all["results"][cid] = {"ok": False, "msg": str(e)[:200]}
                    _fetch_all["done"] = i + 1
                    _fetch_all["stopped"] = str(e)[:200]
                return
            except Exception as e:
                res = {"ok": False, "msg": (str(e) or type(e).__name__)[:200]}
            with _fetch_lock:
                _fetch_all["results"][cid] = res
                _fetch_all["done"] = i + 1
            if i + 1 < len(todo):
                time.sleep(FETCH_GAP)
    finally:
        with _fetch_lock:
            _fetch_all["running"] = False
            _fetch_all["finished"] = time.time()


def _grab(kind, url):
    import tempfile
    if kind in STILLS:
        import io
        frame, _ = fetch_still(kind, url)
        out = io.BytesIO()
        Image.fromarray(frame).resize((480, round(frame.shape[0] * 480 / frame.shape[1]))).save(out, "JPEG", quality=85)
        path = _snap_path(kind, url)
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as tmp:
            tmp.write(out.getvalue())
        os.replace(tmp.name, path)
        return
    vid = youtube_id(url) if kind == "youtube" else ""
    if vid:                                             # YouTube's own image server: no yt-dlp (no bot checks)
        jpg = youtube_thumb(vid)
        path = _snap_path(kind, url)
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as tmp:
            tmp.write(jpg)
        os.replace(tmp.name, path)
        return
    media, _ = resolve(kind, url)
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error", "-protocol_whitelist", PROTOCOLS]
    if kind == "youtube" or ".m3u8" in media:
        args += ["-live_start_index", "-1"]
    args += ["-i", media, "-frames:v", "1", "-vf", "scale=480:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "pipe:1"]
    r = subprocess.run(args, capture_output=True, timeout=40)
    if r.returncode or not r.stdout:
        raise SourceError((r.stderr.decode("utf-8", "replace").strip().splitlines() or ["no picture"])[-1][:200])
    path = _snap_path(kind, url)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as tmp:
        tmp.write(r.stdout)
    os.replace(tmp.name, path)
    stills = sorted(path.parent.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
    for old in stills[:-SNAP_KEEP]:
        old.unlink(missing_ok=True)


# --- crop, scale, colour --------------------------------------------------------------------------------------
def fit_crop(src_w, src_h, out_w, out_h) -> dict:
    """The largest centred crop with the output's shape."""
    aspect = out_w / out_h
    w = min(1.0, (src_h * aspect) / src_w)
    h = w * src_w / aspect / src_h
    return {"x": round(max(0.0, (1 - w) / 2), 5), "y": round(max(0.0, (1 - h) / 2), 5), "w": round(w, 5)}


def crop_box(src_w, src_h, out_w, out_h, crop=None) -> tuple[int, int, int, int]:
    """Pixel box (l, t, r, b) for a crop dict, clamped inside the frame, keeping the output's shape."""
    c = crop if isinstance(crop, dict) and crop.get("w") else fit_crop(src_w, src_h, out_w, out_h)
    aspect = out_w / out_h
    w = max(8.0, min(float(c["w"]) * src_w, float(src_w), src_h * aspect))
    h = w / aspect
    x = min(max(0.0, float(c.get("x", 0)) * src_w), src_w - w)
    y = min(max(0.0, float(c.get("y", 0)) * src_h), src_h - h)
    return int(round(x)), int(round(y)), int(round(x + w)), int(round(y + h))


def adjust(arr, brightness=1.0, contrast=1.0, saturation=1.0):
    """CSS filter maths (brightness(), contrast(), saturate()), so the panel's preview matches the wall."""
    if brightness == 1.0 and contrast == 1.0 and saturation == 1.0:
        return arr
    a = arr.astype(np.float32) * (brightness / 255.0)
    if contrast != 1.0:
        a = (a - 0.5) * contrast + 0.5
    if saturation != 1.0:
        s = saturation
        m = np.array([[0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
                      [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
                      [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]], np.float32)
        a = a @ m.T
    return (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)


def render(frame, out_w, out_h, crop=None, colour=None) -> np.ndarray:
    """One output frame: (out_h, out_w, 3) uint8."""
    h, w = frame.shape[:2]
    box = crop_box(w, h, out_w, out_h, crop)
    img = Image.fromarray(frame).resize((out_w, out_h), Image.LANCZOS, box=box, reducing_gap=2.0)
    c = colour or {}
    return adjust(np.asarray(img), float(c.get("brightness", 1)), float(c.get("contrast", 1)),
                  float(c.get("saturation", 1)))


# --- wire format to the Pi -------------------------------------------------------------------------------------
MAGIC = b"BSWC"
HEADER = 12                  # MAGIC, width u16, height u16, frame age in ms u32 (big-endian)


def pack(arr, age_ms=0) -> bytes:
    h, w = arr.shape[:2]
    return MAGIC + w.to_bytes(2, "big") + h.to_bytes(2, "big") + min(int(age_ms), 2**32 - 1).to_bytes(4, "big") \
        + arr.tobytes()
