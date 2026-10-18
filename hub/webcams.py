"""Live webcam video for the wall.

The Pi has no CPU to spare for decoding video, so the hub does it: one ffmpeg per source, started when someone
watches (the wall or the control panel) and stopped a little after the last one leaves. ffmpeg hands over raw
frames scaled to WORK_W wide; each viewer gets its own crop of them, scaled to its zone and colour-adjusted.

Sources (``modules.webcams.cams.<id>.source``):
    {"type": "youtube", "url": "https://www.youtube.com/watch?v=…"}   live stream; resolved with yt-dlp
    {"type": "stream",  "url": "https://…/index.m3u8"}                HLS, MP4, MJPEG, RTSP… anything ffmpeg opens

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
MAX_FEEDS = 4                # ffmpegs at once
GRACE_SEC = 30.0             # a feed outlives its last viewer by this long (the next play starts instantly)
STALL_SEC = 20.0             # nothing decoded for this long: restart ffmpeg
BUFFER_SEC = 3.5             # playout buffer for HLS (YouTube): longer than a segment, so fetching the next one never
                             # shows as a freeze
BUFFER_SEC_LIVE = 0.3        # … for sources that already arrive in real time (RTSP, MJPEG)
OPEN_SEC = 45.0              # … or no first frame this long after starting it
RESOLVE_TTL = 3600.0         # YouTube manifest URLs last ~6 h; resolve again after an hour
SCHEMES = {"http", "https", "rtsp", "rtsps", "rtmp", "rtmps"}
PROTOCOLS = "http,https,tcp,tls,crypto,httpproxy,rtsp,rtsps,rtmp,rtmps,rtmpt"     # network only: no file:, data:
NOISE = ("frame=", "  ", "Input #", "Output #", "Stream mapping", "Press [q]")
SIZE_RE = re.compile(r"Stream #\d+:\d+.*Video: rawvideo.*?, (\d+)x(\d+)")


class SourceError(ValueError):
    pass


def check_source(src) -> tuple[str, str]:
    """(type, url) of a source dict, or SourceError."""
    if not isinstance(src, dict):
        raise SourceError("source must be an object")
    kind, url = src.get("type", "stream"), str(src.get("url", "")).strip()
    if kind not in ("youtube", "stream"):
        raise SourceError("source type must be youtube or stream")
    if urlparse(url).scheme.lower() not in SCHEMES or not urlparse(url).netloc:
        raise SourceError("source URL must be http(s), rtsp or rtmp")
    return kind, url


# --- resolving -----------------------------------------------------------------------------------------------
_resolved: dict = {}         # youtube url -> (media url, title, at)
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


def resolve(kind, url, fresh=False) -> tuple[str, str]:
    """(media URL for ffmpeg, title). YouTube pages go through yt-dlp: video only, 480p is plenty for 448 px."""
    if kind != "youtube":
        return url, ""
    with _resolve_lock:
        hit = _resolved.get(url)
        if hit and not fresh and time.time() - hit[2] < RESOLVE_TTL:
            return hit[0], hit[1]
    try:
        r = _yt_dlp("-f", "bv*[height<=480]/bv*/b", "--print", "%(title)s", "--print", "%(live_status)s",
                    "-g", url)
    except FileNotFoundError:
        raise SourceError("yt-dlp is not installed on the hub")
    except subprocess.TimeoutExpired:
        raise SourceError("YouTube took too long to answer")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    if r.returncode or len(lines) < 3:
        err = (r.stderr.strip().splitlines() or ["yt-dlp failed"])[-1]
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

    def __init__(self, kind, url, fps):
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
        self._buffer_sec = BUFFER_SEC
        self._new_run = False
        self.decoded_t = 0.0
        self.underflows = 0
        threading.Thread(target=self._run, name=f"webcam-{urlparse(url).netloc}", daemon=True).start()
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

    def wanted(self):
        return self.viewers > 0 or time.time() - self.last_view < GRACE_SEC

    def raise_fps(self, fps):
        """Decode at least this fast (viewers wanting fewer frames skip some); restarts ffmpeg if it has to."""
        if fps <= self.fps:
            return
        self.fps = fps
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._restart = True
            proc.kill()

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
                "buffered_sec": round(len(self._buf) / max(1, self.fps), 1), "underflows": self.underflows}

    # the ffmpeg loop ------------------------------------------------------------------------------------------
    def _run(self):
        backoff = 0
        fresh = False
        while self.wanted():
            try:
                media, title = resolve(self.kind, self.url, fresh)
                self.title = title or self.title
                if not self.wanted():                  # yt-dlp can take a while; nobody may be left
                    break
                self._restart = False
                got = self._decode(media)
                if self._restart:                      # a viewer wanted a higher frame rate: go again now
                    self.error = ""
                    continue
                if got:
                    backoff = 0
                fresh = self.kind == "youtube"         # a YouTube URL that stopped working may have expired
                if not self.error:
                    self.error = "the stream ended"
            except SourceError as e:
                self.error = str(e)
            except Exception as e:                     # never let a feed thread die silently
                self.error = f"{type(e).__name__}: {e}"
            if not self.wanted():
                break
            self.state = "retrying"
            self.restarts += 1
            delay = (5, 15, 30, 60)[min(backoff, 3)]
            backoff += 1
            end = time.time() + delay
            while time.time() < end and self.wanted():
                time.sleep(0.5)
        self.state = "stopped"
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
            buf = bytearray(n)
            view = memoryview(buf)
            while True:
                got_n = 0
                while got_n < n:
                    k = proc.stdout.readinto(view[got_n:])
                    if not k:
                        if tail and not got:
                            self.error = tail[-1][:300]
                        return got
                    got_n += k
                frame = np.frombuffer(bytes(buf), np.uint8).reshape(h, w, 3)
                with self._buf_lock:
                    self._buf.append(frame)
                self.decoded_t = time.time()
                if not got:
                    got, self.error = True, ""
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def _pace(self):
        """Publish buffered frames at an even 1/fps. Waits until the buffer holds _buffer_sec before starting (and
        again whenever it runs dry); drops the oldest frames if the source runs ahead of the clock."""
        t0, k = None, 0
        while self.state != "stopped":
            if self._new_run:
                self._new_run, t0 = False, None
            fps = max(1, self.fps)
            want = max(1, int(self._buffer_sec * fps))
            if t0 is None:
                with self._buf_lock:
                    n = len(self._buf)
                if n < want:
                    time.sleep(0.02)
                    continue
                t0, k = time.time(), 0
            due = t0 + k / fps
            now = time.time()
            if due > now:
                time.sleep(min(0.1, due - now))
                continue
            with self._buf_lock:
                if not self._buf:
                    frame = None
                else:
                    frame = self._buf.popleft()
                    for _ in range(len(self._buf) - 2 * want):     # running ahead: catch up
                        self._buf.popleft()
            if frame is None:                                     # ran dry: hold the picture and re-buffer
                self.underflows += 1
                t0 = None
                continue
            with self._cond:
                self.frame, self.frame_t = frame, now
                self.seq += 1
                self._cond.notify_all()
            self._times = (self._times + [now])[-60:]
            if self.state != "live":
                self.state = "live"
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


def _forget(feed):
    with _feeds_lock:
        for k, f in list(_feeds.items()):
            if f is feed:
                del _feeds[k]


def feed(src, fps=15) -> Feed:
    """The running feed for a source, started if needed (raises SourceError). One feed per source, decoding at
    the highest frame rate anyone asked for; viewers that want fewer thin it themselves (app.py min_gap)."""
    kind, url = check_source(src)
    fps = max(1, min(30, int(fps or 15)))
    key = (kind, url)
    with _feeds_lock:
        f = _feeds.get(key)
        if f is not None and f.state != "stopped":
            f.last_view = time.time()
            f.raise_fps(fps)
            return f
        live = [x for x in _feeds.values() if x.state != "stopped"]
        if len(live) >= MAX_FEEDS:
            idle = [x for x in live if x.viewers == 0]
            if not idle:
                raise SourceError(f"already decoding {MAX_FEEDS} webcams")
            stale = min(idle, key=lambda x: x.last_view)
            stale.last_view = 0                         # its watchdog stops it within a second
            _feeds.pop((stale.kind, stale.url), None)
        f = _feeds[key] = Feed(kind, url, fps)
        return f


def describe():
    with _feeds_lock:
        return [f.describe() for f in _feeds.values()]


# --- snapshots (the panel's gallery and list) -----------------------------------------------------------------
# Requests never wait for a grab: they get the cached still (fresh or stale) at once, or None while background
# workers grab it. A source that failed is not tried again for SNAP_RETRY, so dead streams cost nothing per page load.
SNAP_TTL = 3600.0            # a still older than this is refreshed (and served meanwhile)
SNAP_RETRY = 600.0           # after a failed grab
SNAP_KEEP = 200              # stills kept on disk
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


def _grab(kind, url):
    import tempfile
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
