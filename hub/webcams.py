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
STALL_SEC = 20.0             # no frame for this long: restart ffmpeg
RESOLVE_TTL = 3600.0         # YouTube manifest URLs last ~6 h; resolve again after an hour
SCHEMES = {"http", "https", "rtsp", "rtsps", "rtmp", "rtmps"}
PROTOCOLS = "file,http,https,tcp,tls,crypto,hls,httpproxy,rtp,udp,rtsp,rtmp,rtmps,rtmpt,data"
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


def _yt_dlp(*args, timeout=60):
    return subprocess.run([sys.executable, "-m", "yt_dlp", "--no-warnings", "--no-playlist", *args],
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
    if status not in ("is_live", "post_live", "NA", "None"):
        title = f"{title} (not live: {status})"
    with _resolve_lock:
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
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        threading.Thread(target=self._run, name=f"webcam-{urlparse(url).netloc}", daemon=True).start()

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
                "uptime_sec": round(time.time() - self.started), "restarts": self.restarts}

    # the ffmpeg loop ------------------------------------------------------------------------------------------
    def _run(self):
        backoff = 0
        fresh = False
        while self.wanted():
            try:
                media, title = resolve(self.kind, self.url, fresh)
                self.title = title or self.title
                got = self._decode(media)
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
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "info", "-protocol_whitelist", PROTOCOLS]
        scheme = urlparse(media).scheme.lower()
        if scheme in ("http", "https"):
            args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                     "-user_agent", "BigScreen/2 (home LED wall)"]
            if self.kind == "youtube" or ".m3u8" in media:
                # start at the live edge, and read at real-time speed: HLS comes in multi-second segments that
                # ffmpeg would otherwise decode in a burst, then wait (the wall would see a few frames per segment)
                args += ["-live_start_index", "-1", "-re"]
        elif scheme.startswith("rtsp"):
            args += ["-rtsp_transport", "tcp"]
        args += ["-i", media, "-an", "-sn", "-dn", "-vf", f"fps={self.fps},scale={WORK_W}:-2:flags=area",
                 "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        except FileNotFoundError:
            raise SourceError("ffmpeg is not installed on the hub")
        self._proc = proc
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
                now = time.time()
                with self._cond:
                    self.frame, self.frame_t = frame, now
                    self.seq += 1
                    self._cond.notify_all()
                self._times = (self._times + [now])[-60:]
                if not got:
                    got, self.state, self.error = True, "live", ""
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def _stderr(self, proc, size_box, tail):
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if not size_box:
                m = SIZE_RE.search(line)
                if m:
                    size_box.append((int(m.group(1)), int(m.group(2))))
            if line and not line.startswith(("frame=", "  ", "Input #", "Output #", "Stream mapping", "Press [q]")):
                tail[:] = (tail + [line])[-8:]

    def _watchdog(self, proc):
        """Stop ffmpeg when nobody wants the feed any more, or when frames stopped coming."""
        while proc.poll() is None:
            time.sleep(1.0)
            last = self.frame_t or self.started
            if not self.wanted():
                proc.kill()
            elif self.state == "live" and time.time() - last > STALL_SEC:
                self.error = f"no picture for {int(STALL_SEC)} s"
                proc.kill()


_feeds: dict = {}            # (type, url, fps) -> Feed
_feeds_lock = threading.Lock()


def _forget(feed):
    with _feeds_lock:
        for k, f in list(_feeds.items()):
            if f is feed:
                del _feeds[k]


def feed(src, fps=15) -> Feed:
    """The running feed for a source, started if needed (raises SourceError)."""
    kind, url = check_source(src)
    fps = max(1, min(30, int(fps or 15)))
    key = (kind, url, fps)
    with _feeds_lock:
        f = _feeds.get(key)
        if f is not None and f.state != "stopped":
            f.last_view = time.time()
            return f
        live = [x for x in _feeds.values() if x.state != "stopped"]
        if len(live) >= MAX_FEEDS:
            idle = [x for x in live if x.viewers == 0]
            if not idle:
                raise SourceError(f"already decoding {MAX_FEEDS} webcams")
            stale = min(idle, key=lambda x: x.last_view)
            stale.last_view = 0                         # its watchdog stops it within a second
            _feeds.pop((stale.kind, stale.url, stale.fps), None)
        f = _feeds[key] = Feed(kind, url, fps)
        return f


def describe():
    with _feeds_lock:
        return [f.describe() for f in _feeds.values()]


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
