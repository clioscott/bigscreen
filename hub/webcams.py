"""Live webcam video for the wall.

The Pi has no CPU to spare for decoding video, so the hub does it: one ffmpeg per source, started when someone
watches (the wall or the control panel) and stopped a little after the last one leaves. ffmpeg hands over raw
frames scaled to WORK_W wide (the "lo" picture: the panel's editor, snapshots, health, a stream's first seconds)
and, when a crop needs more detail than that, a "hi" picture at the resolution the crops need, up to the stream's
own (yt-dlp lists every resolution a YouTube stream has: 720p, 1080p, 4K...). A zoomed crop is never upscaled from a
small picture: at the most, one source pixel per LED. Hi frames are never kept: each consumer (one wall zone, crop
and colour, or a panel preview) gets its frame rendered from them as they're decoded (Feed.consumer), so a 4K stream
costs no more memory than a 480p one. When the whole frame at the detail needed is over HI_BUDGET pixels, the hi
picture is just the region the crops cover.

YouTube's live HLS stops at 1080p; a live stream's 1440p and 4K exist only as DASH segments (VP9 WebM, 5 s each,
fetched by sequence number: Feed._fetch_segments pipes them into ffmpeg). Those are used only when a crop needs more
than the HLS has, and only on the video engine, which also crops and scales them (4K in software would take the
N100's CPU).

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
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

import numpy as np
from PIL import Image

WORK_W = 640                 # the lo picture is this wide; a crop that needs no more detail comes out of it
MIN_SOURCE_H = 720           # YouTube: never a smaller stream than this when it has one (480p is soft and blocky)
OVERSAMPLE = 1.5             # a crop gets this many source pixels per LED pixel when the stream has them (then
                             # scaled down: sharper than 1:1, which shows the encoder's blocks)
HI_BUDGET = 1920 * 1088      # the hi picture is at most this many pixels (a 4K zoom gets just its region)
RAISE_GAP = 15.0             # a running feed restarts for more detail at most this often (a crop being zoomed)
DRAFT_SETTLE = 1.5           # a preview's crop counts towards the detail needed once it's held still this long
HW_DEVICE = "/dev/dri/renderD128"   # the N100's video engine (VAAPI): decodes 4K for almost nothing
DEFAULT_SHARPEN = 0.5        # unsharp mask after scaling down (colour.sharpen; 0 = off): crisper edges on the LEDs
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
LOOKUP_ARGS = ("--extractor-args", "youtube:formats=incomplete")   # a live stream's DASH formats (1440p, 4K) too,
                             # beside its HLS ones (yt-dlp leaves them out by default: it can't download them whole)
SEG_BACK = 1                 # DASH: a new feed starts this many segments before the newest (fills the buffer at once)
SEG_POLL = 1.0               # … and asks for the newest segment number this often once it has caught up
SEG_TIMEOUT = 20.0           # … gives up on one segment request after this long
SCHEMES = {"http", "https", "rtsp", "rtsps", "rtmp", "rtmps"}
PROTOCOLS = "http,https,tcp,tls,crypto,httpproxy,rtsp,rtsps,rtmp,rtmps,rtmpt"     # network only: no file:, data:
NOISE = ("frame=", "  ", "Input #", "Output #", "Stream mapping", "Press [q]")
SIZE_RE = re.compile(r"Stream #(\d+):\d+.*Video: rawvideo.*?, (\d+)x(\d+)")       # an output (0 lo, 1 hi)
SRC_RE = re.compile(r"Stream #0:\d+.*Video: (?!rawvideo)(\w+).*?, (\d{2,5})x(\d{2,5})")  # the input (native size)
FPS_RE = re.compile(r"(\d+(?:\.\d+)?) fps")


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
_resolved: dict = {}         # youtube url -> lookup()
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


def lookup(url, fresh=False, timeout=60) -> dict:
    """A YouTube stream's title, live status and every video resolution it has: {"title", "status", "formats":
    [{"url", "w", "h", "fps", "vcodec", "tbr", "audio"}], "at"}, smallest first. One yt-dlp lookup (what -g costs),
    kept RESOLVE_TTL."""
    with _resolve_lock:
        hit = _resolved.get(url)
        if hit and not fresh and time.time() - hit["at"] < RESOLVE_TTL:
            return hit
        failed = _lookup_failed.get(url)
        if failed and not fresh and time.time() - failed[0] < LOOKUP_RETRY:
            raise SourceError(failed[1])
    with _lookup_lock(url):
        with _resolve_lock:                             # another lookup of it may have just finished
            hit = _resolved.get(url)
            if hit and time.time() - hit["at"] < RESOLVE_TTL and (not fresh or time.time() - hit["at"] < 5):
                return hit
        try:
            return _lookup(url, timeout)
        except SourceError as e:
            if not isinstance(e, BotCheck):
                with _resolve_lock:
                    _lookup_failed[url] = (time.time(), str(e))
            raise


LOOKUP_RETRY = 60.0          # a stream whose lookup failed isn't looked up again for this long
_lookup_failed: dict = {}    # youtube url -> (when, why)
_lookup_locks: dict = {}


def _lookup_lock(url):
    with _resolve_lock:
        if len(_lookup_locks) > 200:
            _lookup_locks.clear()
        return _lookup_locks.setdefault(url, threading.Lock())


def cached(url):
    """lookup()'s answer if it's at hand, without asking YouTube (the panel's Picture numbers)."""
    with _resolve_lock:
        hit = _resolved.get(url)
        return hit if hit and time.time() - hit["at"] < RESOLVE_TTL else None


def _lookup(url, timeout):
    wait = _yt_block["until"] - time.time()
    if wait > 0:                                        # throttled: asking again only makes it last longer
        raise BotCheck(f"YouTube is limiting the hub (bot check); trying again in {int(wait // 60) + 1} min")
    try:
        r = _yt_dlp("-J", *LOOKUP_ARGS, url, timeout=timeout)
    except FileNotFoundError:
        raise SourceError("yt-dlp is not installed on the hub")
    except subprocess.TimeoutExpired:
        raise SourceError("YouTube took too long to answer")
    info = None
    if not r.returncode:
        try:
            import json
            info = json.loads(r.stdout)
        except ValueError:
            info = None
    if not isinstance(info, dict):
        err = (r.stderr.strip().splitlines() or ["yt-dlp failed"])[-1]
        if is_bot_check(err):
            _yt_block["until"] = time.time() + BOT_COOL
            print(f"[webcams] YouTube bot check: no lookups for {int(BOT_COOL // 60)} min ({err[:120]})", flush=True)
            raise BotCheck(err.replace("ERROR: ", "")[:300])
        raise SourceError(err.replace("ERROR: ", "")[:300])
    out = {"title": str(info.get("title") or ""), "status": str(info.get("live_status") or "NA"),
           "formats": formats_of(info), "at": time.time()}
    if not out["formats"]:
        raise SourceError("YouTube gave no video stream")
    if out["status"] not in ("is_live", "post_live", "NA", "None"):
        out["title"] = f"{out['title']} (not live: {out['status']})"
    with _resolve_lock:
        if len(_resolved) > 200:
            _resolved.clear()
        _resolved[url] = out
        _lookup_failed.pop(url, None)
    return out


def formats_of(info) -> list:
    """The video streams in yt-dlp's info, smallest first (video-only before muxed, 30 fps before 60, and H.264
    before VP9/AV1 at the same size: cheaper to decode without the video engine). A live stream's DASH formats
    ("dash": True: fetched a segment at a time, _fetch_segments) are kept only where they're bigger than every HLS
    one, and only as WebM (each segment a whole file, so they simply join up)."""
    out, dash = [], []
    live = info.get("live_status") == "is_live"
    for f in info.get("formats") or []:
        try:
            w, h = int(f.get("width") or 0), int(f.get("height") or 0)
            fps = float(f.get("fps") or 0)
            tbr = float(f.get("tbr") or 0)
        except (TypeError, ValueError):
            continue
        vcodec = str(f.get("vcodec") or "none")
        u = str(f.get("url") or "")
        if w < 16 or h < 16 or vcodec == "none" or urlparse(u).scheme.lower() not in SCHEMES:
            continue
        adaptive = live and str(f.get("protocol") or "") in ("http", "https")
        if adaptive and (str(f.get("ext") or "") != "webm" or urlparse(u).scheme.lower() != "https"):
            continue                                       # (an MP4 one: its segments don't join up as they are)
        (dash if adaptive else out).append(
            {"url": u, "w": w, "h": h, "fps": fps, "vcodec": vcodec.split(".")[0], "tbr": tbr,
             "audio": str(f.get("acodec") or "none") != "none", "dash": adaptive})
    top_h = max((f["h"] for f in out), default=0)
    out += [f for f in dash if f["h"] > top_h]
    pref = {"avc1": 0, "h264": 0, "vp09": 1, "vp9": 1, "av01": 2}
    # 30 fps before 60 at the same size: the wall plays 30 (the panels' 60 Hz shows each frame twice), so a 60 fps
    # stream would be decoded only to drop every other frame
    out.sort(key=lambda f: (f["h"], f["audio"], f["fps"] > 31, pref.get(f["vcodec"], 3), -f["fps"], f["tbr"]))
    return out


def playable(formats, hw=None) -> list:
    """The formats this hub can play now: DASH ones only on the video engine (4K VP9 in software would take the
    CPU). ``hw``: hw_decode() when None."""
    if hw is None:
        hw = hw_decode()
    return [f for f in formats if hw or not f.get("dash")]


def native_of(formats):
    """The biggest of a stream's formats (its own resolution), or None."""
    return max(formats, key=lambda f: (f["h"], f["w"], f["fps"], -f.get("audio", False))) if formats else None


def pick_format(formats, need_w=0.0, min_h=MIN_SOURCE_H, snaps=()):
    """The smallest format at least need_w wide (and at least min_h tall when the stream has that), else the
    biggest. With ``snaps`` [(out_w, out_h, crop)] (Whole pixels crops), the smallest of those on which every one is
    still an exact multiple, if any is. None when there are none."""
    if not formats:
        return None
    top = native_of(formats)
    floor = min(min_h, top["h"])
    ok = [f for f in formats if f["w"] >= need_w and f["h"] >= floor]
    exact = [f for f in ok if all(snap_k(c, f["w"], f["h"], ow, oh) for ow, oh, c in snaps)]
    return exact[0] if exact else ok[0] if ok else top


def is_snap(crop) -> bool:
    return isinstance(crop, dict) and bool(crop.get("snap")) and bool(crop.get("w"))


def choose_format(formats, wants, hw=None):
    """The format a feed should decode for these consumers [(out_w, out_h, crop)]: the smallest playable one giving
    each crop OVERSAMPLE pixels per LED, on which every Whole pixels crop is still exact (4K and 1080p both are for
    a crop made on either: 4K is 2 x 2 of 1080p)."""
    fs = playable(formats, hw)
    if not fs:
        return None
    top = native_of(fs)
    need = max([need_of(top["w"], top["h"], ow, oh, {k: v for k, v in c.items() if k != "snap"}
                        if is_snap(c) else c) for ow, oh, c in wants] or [0.0])
    return pick_format(fs, need, snaps=[w for w in wants if is_snap(w[2])])


def resolve(kind, url, fresh=False, timeout=60, need_w=0.0, min_h=MIN_SOURCE_H) -> tuple[str, str]:
    """(media URL for ffmpeg, title). YouTube pages go through lookup(): the smallest stream that's at least need_w
    wide (pick_format), never a DASH one (ffmpeg can't open those by URL)."""
    if kind != "youtube":                               # streams and stills are used as given
        return url, ""
    info = lookup(url, fresh, timeout)
    return pick_format(playable(info["formats"], hw=False), need_w, min_h)["url"], info["title"]


_hw = {"failed": False}


def hw_decode() -> bool:
    """Can ffmpeg use the video engine? (The hub's user must be in the render group.)"""
    return not _hw["failed"] and os.path.exists(HW_DEVICE) and os.access(HW_DEVICE, os.R_OK | os.W_OK)


# --- DASH segments (a live stream's 1440p and 4K) -------------------------------------------------------------
SEG_TRIES = 3                # a segment request is tried this often (1 s, then 2 s apart) before the run gives up


def _retrying(fn, tries=SEG_TRIES):
    """fn() again after a blip: a timeout, a dropped connection, a cut-short read, 429 or 5xx. Other HTTP errors (403:
    the URL expired; 404: no such segment) are answers, raised at once."""
    import http.client
    for i in range(tries):
        try:
            return fn()
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500 or i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead,
                http.client.RemoteDisconnected):
            if i == tries - 1:
                raise
        time.sleep(1.0 + i)


def seg_head(url) -> int:
    """The newest segment's number (YouTube's X-Head-Seqnum, from a HEAD of the format's URL)."""
    def ask():
        req = urllib.request.Request(url, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=SEG_TIMEOUT) as r:
            v = r.headers.get("X-Head-Seqnum")
        try:
            return int(v)
        except (TypeError, ValueError):
            raise SourceError(f"YouTube gave no segment number ({v!r})")
    return _retrying(ask)


def seg_get(url, n) -> bytes:
    """Segment n (a whole WebM file, ~5 s)."""
    sep = "&" if "?" in url else "?"

    def get():
        with urllib.request.urlopen(urllib.request.Request(url + sep + urlencode({"sq": n}), headers=UA),
                                    timeout=SEG_TIMEOUT) as r:
            return r.read()
    return _retrying(get)


def write_all(sink, data) -> None:
    """All of data into a raw (unbuffered) pipe: one write() may take only part of it."""
    view = memoryview(data)
    while view:
        n = sink.write(view)
        if n is None:                                   # (non-blocking only; not for these pipes)
            time.sleep(0.01)
            continue
        view = view[n:]


# --- how much detail a crop needs -------------------------------------------------------------------------------
def crop_frac(src_w, src_h, out_w, out_h, crop=None):
    """The crop as fractions of the frame (x, y, w, h), as the wall cuts it (crop_box)."""
    l, t, r, b = crop_box(src_w, src_h, out_w, out_h, crop)
    return l / src_w, t / src_h, (r - l) / src_w, (b - t) / src_h


def need_of(src_w, src_h, out_w, out_h, crop=None) -> float:
    """How wide the whole frame must be decoded for this crop to get OVERSAMPLE source pixels per LED pixel. A
    "snap" crop needs the stream's own pixels, unscaled (its whole multiples are of those)."""
    if is_snap(crop):
        return 1e6
    return out_w / max(1e-3, crop_frac(src_w, src_h, out_w, out_h, crop)[2]) * OVERSAMPLE


def plan(src_w, src_h, wants) -> dict | None:
    """The hi picture for these consumers [(out_w, out_h, crop)] of a src_w x src_h stream: None when the lo picture
    is enough, else {"full_w": the whole frame's width at this detail, "x", "y", "w", "h": the region in source
    pixels (the whole frame unless that's over HI_BUDGET), "out_w", "out_h": the hi picture's size}. Never more
    pixels than the source has."""
    if src_w < 16 or src_h < 16:
        return None
    wants = [w for w in wants if need_of(src_w, src_h, *w) > WORK_W * 1.1]    # the rest come out of lo
    if not wants:
        return None
    full_w = min(float(src_w), max(need_of(src_w, src_h, *w) for w in wants))
    if full_w <= WORK_W * 1.1:
        return None
    k = full_w / src_w                                   # hi pixels per source pixel (<= 1: never upscaled)
    x0, y0, x1, y1 = 0.0, 0.0, 1.0, 1.0
    if src_w * k * src_h * k > HI_BUDGET:                # the whole frame is too big at this detail: just the crops
        boxes = [crop_frac(src_w, src_h, *w) for w in wants]
        m = 0.04
        x0, y0 = max(0.0, min(b[0] for b in boxes) - m), max(0.0, min(b[1] for b in boxes) - m)
        x1, y1 = min(1.0, max(b[0] + b[2] for b in boxes) + m), min(1.0, max(b[1] + b[3] for b in boxes) + m)
        area = (x1 - x0) * src_w * k * (y1 - y0) * src_h * k
        if area > HI_BUDGET:                             # crops far apart: a little less detail
            k *= (HI_BUDGET / area) ** 0.5

    def even(v):
        return int(v) // 2 * 2

    def even_up(v):
        return -(-int(-(-v // 1)) // 2) * 2
    x, y = even(x0 * src_w), even(y0 * src_h)
    w, h = min(src_w, even_up(x1 * src_w)) - x, min(src_h, even_up(y1 * src_h)) - y
    w, h = even(w), even(h)
    out_w = max(2, min(w, even(w * k + 1)))
    out_h = max(2, min(h, even(h * out_w / w + 1)))
    return {"full_w": round(src_w * out_w / w), "x": x, "y": y, "w": w, "h": h, "out_w": out_w, "out_h": out_h}


def covers(p, src_w, src_h, want) -> bool:
    """Does hi plan p give this consumer [(out_w, out_h, crop)] what it needs: inside the region, enough detail?"""
    if p is None:
        return False
    fx, fy, fw, fh = crop_frac(src_w, src_h, *want)
    inside = fx * src_w >= p["x"] - 1 and fy * src_h >= p["y"] - 1 and (fx + fw) * src_w <= p["x"] + p["w"] + 1 \
        and (fy + fh) * src_h <= p["y"] + p["h"] + 1
    return inside and p["full_w"] >= min(src_w, need_of(src_w, src_h, *want)) * 0.9


config_needs = None          # hub/app.py: (type, url) -> [(out_w, out_h, crop)], every place the saved webcams with
                             # this source play: a feed starts with the detail they need (no restart once on screen)


# --- feeds -----------------------------------------------------------------------------------------------------
class Frame(np.ndarray):
    """A lo frame that knows its place in the decoded order (dseq), so a preview can find the hi render of it."""
    dseq = 0


def _key_of(w, h, crop, colour):
    c = crop if isinstance(crop, dict) and crop.get("w") else None
    return (int(w), int(h), None if c is None else (round(float(c.get("x", 0)), 5), round(float(c.get("y", 0)), 5),
                                                     round(float(c["w"]), 5), bool(c.get("snap"))),
            tuple(sorted((k, round(float(v), 3)) for k, v in (colour or {}).items())))


class Consumer:
    """One thing the feed renders for: a wall zone (fixed size, crop and colour) or a panel preview (its draft changes;
    ``ver`` counts the changes). Its frames are rendered as they're decoded, from the hi picture when there is one:
    ``frames`` holds (dseq, ver, frame) for DECODED_SEC."""

    def __init__(self, w, h, crop=None, colour=None, draft=False, maxlen=200):
        self.w, self.h, self.crop, self.colour = int(w), int(h), crop, colour
        self.draft = draft
        self.ver, self.changed = 0, time.time()
        self.refs = 0
        self.frames = collections.deque(maxlen=maxlen)
        self.lock = threading.Lock()

    def want(self):
        return (self.w, self.h, self.crop)

    def settled(self):
        return not self.draft or time.time() - self.changed >= DRAFT_SETTLE

    def set(self, w, h, crop, colour):
        """A preview's draft changed (its frames from before are re-rendered from lo until new ones come)."""
        if (int(w), int(h), crop, colour) != (self.w, self.h, self.crop, self.colour):
            with self.lock:
                self.w, self.h, self.crop, self.colour = int(w), int(h), crop, colour
                self.ver += 1
                self.changed = time.time()

    def add(self, dseq, ver, frame):
        with self.lock:
            if self.frames and dseq <= self.frames[-1][0]:     # (dseq only grows, across ffmpeg runs too)
                return
            self.frames.append((dseq, ver, frame))

    def _index(self, dseq):
        """Where dseq is (or would go) in frames. Numbers can have gaps (a frame lo never sent, a render that
        failed), so they're searched, not counted."""
        import bisect
        return bisect.bisect_left([f[0] for f in self.frames], dseq)

    def after(self, seq):
        """The first frame after dseq ``seq`` still kept: (dseq, frame), or None."""
        with self.lock:
            if not self.frames or self.frames[-1][0] <= seq:
                return None
            d, _, fr = self.frames[self._index(seq + 1)]
            return d, fr

    def oldest(self):
        with self.lock:
            return self.frames[0][0] - 1 if self.frames else None

    def for_dseq(self, dseq):
        """The render of decoded frame dseq with the current draft, or None (not rendered, or an older draft)."""
        with self.lock:
            if not self.frames:
                return None
            i = self._index(dseq)
            if i < len(self.frames) and self.frames[i][0] == dseq and self.frames[i][1] == self.ver:
                return self.frames[i][2]
        return None


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
        self.consumers = {}              # key -> Consumer (Feed.consumer)
        self.formats = []                # YouTube: every resolution the stream has (lookup)
        self.native = None               # the stream's own resolution: {"w", "h", "fps", "vcodec"}
        self.source = None               # what's being decoded now: {"w", "h", "fps", "vcodec"}
        self.hi = None                   # the hi picture now (plan()), or None: the lo one is enough
        self.hw = False                  # decoding on the video engine
        self._run_t = 0.0                # when the current ffmpeg started
        self._run_hi = False             # the current ffmpeg gives a hi picture (its reader renders the consumers)
        self._hi_broken = False          # a run with a hi picture wouldn't open: this feed sticks to lo
        self._dash_broken = False        # a DASH run wouldn't open: this feed sticks to HLS
        self._fed = False                # this run's fetcher got a segment into ffmpeg
        self._seg_error = ""             # … or why it couldn't
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
        self._kick()

    def _kick(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._restart = True
            proc.kill()

    # consumers (hub/app.py: the wall's stream, the panel's preview) ------------------------------------------
    def consumer(self, w, h, crop=None, colour=None, key=None, draft=False):
        """The consumer for this zone, crop and colour (shared by streams asking the same), its frames seeded from
        the lo frames already decoded so it starts with a backlog. Give it back with release()."""
        key = key or _key_of(w, h, crop, colour)
        with self._lock:
            c = self.consumers.get(key)
            new = c is None
            if new:
                c = self.consumers[key] = Consumer(w, h, crop, colour, draft, maxlen=self.decoded.maxlen or 200)
            c.refs += 1
            backlog = list(self.decoded) if new and not draft else []
        if backlog:                                         # what's already decoded, from lo (a new run renders hi)
            ver, seeded = c.ver, []
            for d, fr in backlog:
                seeded.append((d, ver, render(fr, c.w, c.h, c.crop, c.colour)))
            with c.lock:
                first = c.frames[0][0] if c.frames else None
                for item in reversed(seeded):
                    if first is None or item[0] < first:
                        c.frames.appendleft(item)
        return c

    def release(self, c):
        with self._lock:
            c.refs -= 1
            if c.refs <= 0:
                for k, v in list(self.consumers.items()):
                    if v is c:
                        del self.consumers[k]

    def wants(self):
        """What the hi picture must serve: every saved place this source plays (config_needs) and every consumer
        now (a preview's draft once it's held still)."""
        out = []
        if callable(config_needs):
            try:
                out += list(config_needs(self.kind, self.url) or [])
            except Exception:
                pass
        with self._lock:
            out += [c.want() for c in self.consumers.values() if c.settled()]
        return out

    def _render_consumers(self, frame, dseq, hi=None, run=None):
        """Every consumer's frame for decoded frame dseq: from the hi picture when it covers the consumer, else
        from lo (``frame``; None: lo hasn't come, so only the ones hi covers). ``run``: (plan, source) of the
        ffmpeg run hi came from (a new run may have started meanwhile)."""
        with self._lock:
            todo = list(self.consumers.values())
        p, src = run or (self.hi, self.source)
        src = src or {}
        for c in todo:
            ver, w, h, crop, colour = c.ver, c.w, c.h, c.crop, c.colour
            try:
                if hi is not None and covers(p, src.get("w", 0), src.get("h", 0), (w, h, crop)):
                    out = render_region(hi, p, src["w"], src["h"], w, h, crop, colour)
                elif frame is not None:
                    out = render(frame, w, h, crop, colour)
                else:
                    continue
            except Exception as e:                             # never stop the decoder over one consumer
                self.error = f"render: {type(e).__name__}: {e}"[:300]
                continue
            out.flags.writeable = False
            c.add(dseq, ver, out)

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
                "image_age_sec": round(time.time() - self.image_t) if self.image_t else None,
                "native": self.native, "source": self.source, "hi": self.hi, "hw": self.hw,
                "consumers": len(self.consumers),
                "formats": [{k: f[k] for k in ("w", "h", "fps", "vcodec")} for f in self.formats]}

    # the ffmpeg loop ------------------------------------------------------------------------------------------
    def _playable(self, formats):
        return [f for f in playable(formats) if not (f.get("dash") and self._dash_broken)]

    def _choose(self, fresh):
        """(media URL, title) for the next run, with self.source / self.native / self.hi set for it."""
        wants = self.wants()
        if self.kind == "youtube":
            info = lookup(self.url, fresh)
            self.formats = self._playable(info["formats"])
            top = native_of(self.formats)
            self.native = {k: top[k] for k in ("w", "h", "fps", "vcodec")}
            fmt = choose_format(self.formats, wants, hw=True)          # (already only what this hub can play)
            self.source = {k: fmt[k] for k in ("w", "h", "fps", "vcodec", "dash")}
            media, title = fmt["url"], info["title"]
        else:
            media, title = self.url, ""                      # its size is known once ffmpeg has opened it once
        src = self.source or {}
        self.hi = plan(src.get("w", 0), src.get("h", 0), wants) if src and not self._hi_broken else None
        return media, title

    def _wants_more(self) -> bool:
        """A consumer (or a saved crop) needs more detail, or a region, than the running ffmpeg gives, and the stream
        has it: run again (at most every RAISE_GAP)."""
        if time.time() - self._run_t < RAISE_GAP or not self.source or self._hi_broken:
            return False
        wants = self.wants()
        if not wants:
            return False
        src = self.source
        if self.formats:                                      # a bigger stream would give more
            fmt = choose_format(self.formats, wants, hw=True)
            if fmt and fmt["w"] > src["w"]:
                return True
        lacking = [w for w in wants if need_of(src["w"], src["h"], *w) > WORK_W * 1.1
                   and not covers(self.hi, src["w"], src["h"], w)]
        if not lacking:
            return False
        better = plan(src["w"], src["h"], wants)
        return better is not None and better != self.hi

    def _run(self):
        if self.kind in STILLS:
            return self._run_still()
        backoff = 0
        fresh = False
        while self.wanted():
            try:
                media, title = self._choose(fresh)
                self.title = title or self.title
                if not self.wanted():                  # yt-dlp can take a while; nobody may be left
                    break
                self._restart = False
                with self._lock:
                    self.state = "starting"            # the pacer makes it "live" with the first frame it shows
                got = self._decode(media)
                if self._restart:                      # more frames or more detail wanted: go again now
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

    def _args(self, media, hi_fd=None, hw=False, dash=False):
        """ffmpeg's command line: lo frames on pipe:1 and, with a hi plan, hi frames on hi_fd. ``dash``: the stream
        comes in on stdin (_fetch_segments), decoded, cropped and scaled on the video engine."""
        # -nostats: the progress line ends in \r, which readline would keep growing for hours
        args = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info", "-protocol_whitelist", PROTOCOLS]
        if dash:
            return self._dash_args(args, hi_fd)
        scheme = urlparse(media).scheme.lower()
        if scheme in ("http", "https"):
            args += ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
                     "-user_agent", "BigScreen/2 (home LED wall)"]
            if self.kind == "youtube" or ".m3u8" in media:
                # HLS arrives a segment (2-5 s) at a time and ffmpeg decodes each as soon as it lands: the pacer
                # (_pace) evens that out from a buffer. Starting two segments back fills the buffer at once; a run
                # started for more detail (frames already decoded) starts at the newest segment, which the old run
                # has usually decoded already: a few seconds replay then (only when a crop asks for more detail
                # than the saved ones, i.e. while one is being zoomed in the panel).
                # (Reading with -re instead froze the picture while every next segment downloaded.)
                args += ["-live_start_index", "-1" if self.decoded_t else "-2"]
                self._buffer_sec = BUFFER_SEC
            else:
                self._buffer_sec = BUFFER_SEC_LIVE
        elif scheme.startswith("rtsp"):
            args += ["-rtsp_transport", "tcp"]
            self._buffer_sec = BUFFER_SEC_LIVE
        if hw:
            args += ["-hwaccel", "vaapi", "-hwaccel_device", HW_DEVICE]     # frames come back to memory by themselves
        args += ["-i", media]
        lo = f"scale={WORK_W}:-2:flags=area"
        p = self.hi
        if p is None or hi_fd is None:
            return args + ["-an", "-sn", "-dn", "-vf", f"fps={self.fps},{lo}",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        crop = "" if (p["x"], p["y"], p["w"], p["h"]) == (0, 0, self.source["w"], self.source["h"]) else \
            f"crop=w={p['w']}:h={p['h']}:x={p['x']}:y={p['y']}:exact=1,"
        scale = "" if (p["out_w"], p["out_h"]) == (p["w"], p["h"]) else \
            f"scale={p['out_w']}:{p['out_h']}:flags=area,"
        graph = f"[0:v:0]fps={self.fps},split=2[a][b];[a]{lo}[lo];[b]{crop}{scale}null[hi]"
        return args + ["-filter_complex", graph,
                       "-map", "[lo]", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                       "-map", "[hi]", "-f", "rawvideo", "-pix_fmt", "rgb24", f"pipe:{hi_fd}"]

    def _dash_args(self, args, hi_fd):
        """A DASH (4K) run: WebM segments on stdin; the video engine decodes, crops and scales, and only the two
        pictures come back to memory (as NV12: its own RGB is a few levels off). Scaling a 4K frame on the CPU would
        cost more than a core; pulling a cropped frame back without scale_vaapi, several."""
        self._buffer_sec = BUFFER_SEC                       # 5 s segments, like the HLS
        args = args[:-1] + [PROTOCOLS + ",pipe"]
        args += ["-hwaccel", "vaapi", "-hwaccel_device", HW_DEVICE, "-hwaccel_output_format", "vaapi",
                 "-f", "matroska", "-i", "pipe:0"]
        src = self.source

        def gpu(w, h):
            return f"scale_vaapi=w={w}:h={h}:mode=hq:format=nv12,hwdownload,format=nv12"
        lo = gpu(WORK_W, max(2, round(WORK_W * src["h"] / src["w"] / 2) * 2))
        p = self.hi
        if p is None or hi_fd is None:
            return args + ["-an", "-sn", "-dn", "-vf", f"fps={self.fps},{lo}",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
        crop = "" if (p["x"], p["y"], p["w"], p["h"]) == (0, 0, src["w"], src["h"]) else \
            f"crop=w={p['w']}:h={p['h']}:x={p['x']}:y={p['y']}:exact=1,"
        graph = f"[0:v:0]fps={self.fps},split=2[a][b];[a]{lo}[lo];[b]{crop}{gpu(p['out_w'], p['out_h'])}[hi]"
        return args + ["-filter_complex", graph,
                       "-map", "[lo]", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                       "-map", "[hi]", "-f", "rawvideo", "-pix_fmt", "rgb24", f"pipe:{hi_fd}"]

    def _fetch_segments(self, proc, url):
        """A DASH run's input: the stream's WebM segments in order, into ffmpeg's stdin (each is a whole file, so
        they simply join up), from SEG_BACK before the newest (the newest only when frames were already decoded: a
        run restarted for more detail) and then each new one as it's published. Closing stdin ends the run (an
        expired URL: _run looks the stream up again)."""
        sink = proc.stdin
        n = head = None
        try:
            while proc.poll() is None:
                if head is None or n > head:
                    if head is not None:
                        time.sleep(SEG_POLL)
                    head = seg_head(url)
                    if n is None:
                        n = max(0, head - (0 if self.decoded_t else SEG_BACK))
                    if n > head:
                        continue
                try:
                    data = seg_get(url, n)
                except urllib.error.HTTPError as e:
                    if e.code == 404 and n < head:                 # fell out of the stream's window: go to the newest
                        n = head
                        continue
                    raise
                try:
                    write_all(sink, data)
                except (BrokenPipeError, ValueError):               # ffmpeg ended (or was stopped)
                    return
                self._fed = True
                n += 1
        except Exception as e:
            self._seg_error = self.error = f"segments: {type(e).__name__}: {e}"[:300]
        finally:
            try:
                sink.close()
            except Exception:
                pass

    def _decode(self, media) -> bool:
        """Run ffmpeg until the stream ends, stalls or nobody wants it. True if any frame arrived."""
        hw = hw_decode()
        dash = self.kind == "youtube" and bool((self.source or {}).get("dash"))
        if dash and not hw:                              # the video engine went away since this run was chosen
            self._restart = True
            return False
        self._fed, self._seg_error = False, ""
        hi_r = hi_w = None
        if self.hi is not None:
            hi_r, hi_w = os.pipe()
        args = self._args(media, hi_w, hw, dash)
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
                                    stdin=subprocess.PIPE if dash else None,
                                    pass_fds=(hi_w,) if hi_w is not None else ())
        except BaseException as e:
            if hi_r is not None:
                os.close(hi_r)
            if isinstance(e, FileNotFoundError):
                raise SourceError("ffmpeg is not installed on the hub")
            raise
        finally:
            if hi_w is not None:
                os.close(hi_w)                           # the child has its own copy
        self._proc, self.hw = proc, hw
        self._run_hi = hi_r is not None
        self._run_t = time.time()
        self._new_run = True                       # the pacer restarts its clock (the fps may have changed)
        with self._buf_lock:
            self._buf.clear()                      # frames from a previous run would play out of order
        size_box, tail, src_box = {}, [], []
        threading.Thread(target=self._stderr, args=(proc, size_box, tail, src_box), daemon=True).start()
        threading.Thread(target=self._watchdog, args=(proc,), daemon=True).start()
        if dash:
            threading.Thread(target=self._fetch_segments, args=(proc, media), daemon=True,
                             name="webcam-segments").start()
        got = False
        base = self.dseq                           # this run's frame n is dseq base + n, in both pictures
        try:
            t0 = time.time()
            while 0 not in size_box or (hi_r is not None and 1 not in size_box):   # sizes: in ffmpeg's banner
                if proc.poll() is not None or time.time() - t0 > 45:
                    msg = (tail[-1] if tail else "ffmpeg could not open the stream")[:300]
                    if self._restart:                      # stopped on purpose (more frames or detail wanted)
                        return False
                    if dash and not self._fed:             # no segment reached ffmpeg: the fetching failed (a
                        self.error = self._seg_error or msg   # network blip, an expired URL), not the DASH run
                        return False
                    if dash:                               # the DASH run didn't open: HLS (up to 1080p) from now on
                        self._dash_broken = True           # (first: its errors name vaapi, but the HLS run will
                        self._restart = True               # tell whether the video engine itself works)
                        print(f"[webcams] DASH run failed ({msg[:120]}); HLS for {self.url[:80]}", flush=True)
                    elif hw and any(s in " ".join(tail).lower() for s in ("vaapi", "hwaccel", "hardware device")):
                        _hw["failed"] = True               # the video engine won't open: decode in software
                        print(f"[webcams] no hardware decoding ({msg[:120]}); software from now on", flush=True)
                        self._restart = True
                    elif hi_r is not None or self._run_hi:  # the two-picture run didn't open: lo only next time
                        self._hi_broken = True
                        self._restart = True
                    self.error = msg
                    return False
                time.sleep(0.05)
            if src_box and self.kind != "youtube":     # a plain stream: its size comes from ffmpeg
                codec, sw, sh = src_box[0]
                self.native = self.source = {"w": sw, "h": sh, "fps": self.native and self.native.get("fps") or 0,
                                             "vcodec": codec}
            w, h = size_box[0]
            self.size = [w, h]
            if hi_r is not None:
                hw_, hh = size_box[1]
                threading.Thread(target=self._read_hi, args=(proc, hi_r, hw_, hh, base), daemon=True,
                                 name="webcam-hi").start()
                hi_r = None                            # the hi reader closes it
            n = w * h * 3
            k = 0
            while True:
                frame = np.empty((h, w, 3), np.uint8).view(Frame)   # read straight into the frame: no copies
                view = memoryview(frame.view(np.ndarray)).cast("B")
                got_n = 0
                while got_n < n:
                    r = proc.stdout.readinto(view[got_n:])
                    if not r:
                        if tail and not got:
                            self.error = tail[-1][:300]
                        return got
                    got_n += r
                k += 1
                frame.dseq = base + k
                frame.flags.writeable = False             # shared by every reader once published
                with self._buf_lock:
                    self._buf.append(frame)
                    while len(self._buf) > self._cap():          # bounded memory, even while re-buffering
                        self._buf.popleft()
                with self._cond:                           # the wall's stream takes it now (not paced)
                    self.dseq = base + k
                    if self.decoded.maxlen != int(DECODED_SEC * max(1, self.fps)) + 1:
                        self.decoded = collections.deque(self.decoded, maxlen=int(DECODED_SEC * max(1, self.fps)) + 1)
                    self.decoded.append((self.dseq, frame))
                    self._cond.notify_all()
                if not self._run_hi:
                    self._render_consumers(frame, base + k)    # no hi picture: consumers from lo
                with self._cond:
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
            if hi_r is not None:
                os.close(hi_r)
            if proc.poll() is None:
                proc.kill()
            proc.wait()

    def _read_hi(self, proc, fd, w, h, base):
        """The hi picture's reader: every consumer's frame rendered from it as it's decoded. One buffer, reused:
        hi frames are never kept."""
        run = (self.hi, self.source)
        n = w * h * 3
        buf = np.empty((h, w, 3), np.uint8)
        view = memoryview(buf).cast("B")
        k = 0
        try:
            with os.fdopen(fd, "rb", buffering=0) as f:
                while True:
                    got = 0
                    while got < n:
                        r = f.readinto(view[got:])
                        if not r:
                            return
                        got += r
                    k += 1
                    self._render_consumers(self._lo_twin(base + k), base + k, hi=buf, run=run)
        except Exception as e:
            self.error = f"hi: {type(e).__name__}: {e}"[:300]

    def _lo_twin(self, dseq, timeout=0.5):
        """The lo frame dseq (the same picture: both come from one split in ffmpeg), waiting a little for it."""
        end = time.time() + timeout
        with self._cond:
            while True:
                if self.decoded and self.decoded[-1][0] >= dseq:
                    i = dseq - self.decoded[0][0]
                    return self.decoded[i][1] if 0 <= i < len(self.decoded) else None
                left = end - time.time()
                if left <= 0:
                    return None
                self._cond.wait(min(0.05, left))

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

    def _stderr(self, proc, size_box, tail, src_box=None):
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            m = SIZE_RE.search(line)
            if m and int(m.group(1)) not in size_box:
                size_box[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
            elif src_box is not None and not src_box:
                m = SRC_RE.search(line)
                if m:
                    src_box.append((m.group(1), int(m.group(2)), int(m.group(3))))
            if line and not line.startswith(NOISE) and "Opening '" not in line:
                tail[:] = (tail + [line])[-8:]

    def _watchdog(self, proc):
        """Stop ffmpeg when nobody wants the feed any more, or when frames stopped coming (or never came:
        a stream that opens but sends nothing would otherwise block forever). Restart it when a consumer needs more
        detail than it gives (_wants_more)."""
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
            elif had_frame:
                try:
                    more = self._wants_more()
                except Exception:
                    more = False
                if more:
                    print(f"[webcams] more detail wanted: restarting {self.url[:80]}", flush=True)
                    self._kick()


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
    media, _ = resolve(kind, url, timeout=timeout, need_w=480, min_h=0)   # one small picture
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


GRID_DARK, GRID_FLAT = 12.0, 4.0     # mean level below this: black; spread below this: one flat colour (a placeholder)
GRID_FROZEN_SEC = 2 * 3600          # a still that hasn't changed for this long is a broken camera


def grid_usable(frame, taken=None, now=None):
    """Is this still worth a grid tile? Not when it's black, one flat colour (an "offline" card), or frozen for hours.
    Night pictures are dark but not black: street lights and headlights keep them well above GRID_DARK."""
    if frame is None:
        return False
    if taken is not None and (now or time.time()) - taken > GRID_FROZEN_SEC:
        return False
    sample = frame[::6, ::6].astype(np.float32)
    return float(sample.mean()) >= GRID_DARK and float(sample.std()) >= GRID_FLAT


def grid_tile_work(src, crop, w, h):
    """One camera grid tile, start to finish (fetch, check, crop and scale): run in a worker process (hub/app.py
    _grid_pool), so the grid's thousands of stills never hold up the hub's webcam video. None: down or unusable."""
    try:
        kind, url = check_source(src)
    except SourceError:
        return None
    if kind not in STILLS:
        return None
    frame, taken = fetch_still(kind, url, meta=False)
    return render(frame, w, h, crop) if grid_usable(frame, taken) else None


def grid_worker_init():
    """Grid workers run at a lower priority than the hub: video first."""
    try:
        os.nice(10)
    except (AttributeError, OSError):
        pass


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
    media, _ = resolve(kind, url, need_w=480, min_h=0)
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
    """Pixel box (l, t, r, b) for a crop dict, clamped inside the frame, keeping the output's shape. A crop with
    "snap" (Whole pixels in the panel) that comes out within a pixel or two of k times the output is made exactly that
    (k x k stream pixels per LED, on whole pixels), so it scales down by plain averaging; from a smaller picture (the
    640-wide one) it's cut as usual."""
    c = crop if isinstance(crop, dict) and crop.get("w") else fit_crop(src_w, src_h, out_w, out_h)
    aspect = out_w / out_h
    w = max(8.0, min(float(c["w"]) * src_w, float(src_w), src_h * aspect))
    k = snap_k(c, src_w, src_h, out_w, out_h)
    if k:
        w, h = float(k * out_w), float(k * out_h)
        x = min(max(0, round(float(c.get("x", 0)) * src_w)), src_w - k * out_w)
        y = min(max(0, round(float(c.get("y", 0)) * src_h)), src_h - k * out_h)
        return int(x), int(y), int(x + w), int(y + h)
    h = w / aspect
    x = min(max(0.0, float(c.get("x", 0)) * src_w), src_w - w)
    y = min(max(0.0, float(c.get("y", 0)) * src_h), src_h - h)
    return int(round(x)), int(round(y)), int(round(x + w)), int(round(y + h))


def snap_k(crop, src_w, src_h, out_w, out_h) -> int:
    """For a "snap" crop: the whole number of stream pixels per LED it was set to on this picture, or 0 (not a snap
    crop, or this picture isn't the one it was set on: the 640-wide one, another resolution)."""
    if not (isinstance(crop, dict) and crop.get("snap") and crop.get("w")):
        return 0
    want = float(crop["w"]) * src_w
    k = round(want / out_w)
    if k < 1 or abs(k * out_w - want) > max(2.0, 0.004 * want) or k * out_w > src_w or k * out_h > src_h:
        return 0
    return k


def _resample(box, out_w, out_h):
    """Plain averaging (BOX) for a box exactly k times the output on whole pixels; bilinear otherwise."""
    bw, bh = box[2] - box[0], box[3] - box[1]
    k = round(bw / out_w)
    whole = all(abs(v - round(v)) < 1e-6 for v in box)
    if k >= 1 and whole and abs(bw - k * out_w) < 1e-6 and abs(bh - k * out_h) < 1e-6:
        return Image.BOX
    return Image.BILINEAR


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


def sharpen_of(colour) -> float:
    """How much to sharpen (0 off .. 2): colour.sharpen, DEFAULT_SHARPEN for video with no setting of its own
    (colour given), none for the camera grid's stills (colour None)."""
    if colour is None:
        return 0.0
    try:
        return max(0.0, min(2.0, float(colour.get("sharpen", DEFAULT_SHARPEN))))
    except (TypeError, ValueError):
        return DEFAULT_SHARPEN


def _finish(img, colour) -> np.ndarray:
    """Sharpen (an unsharp mask at the LEDs' own scale: edges stay crisp after scaling down) and colour."""
    amount = sharpen_of(colour)
    if amount > 0:
        from PIL import ImageFilter
        img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=int(amount * 100), threshold=3))
    c = colour or {}
    return adjust(np.asarray(img), float(c.get("brightness", 1)), float(c.get("contrast", 1)),
                  float(c.get("saturation", 1)))


def render(frame, out_w, out_h, crop=None, colour=None) -> np.ndarray:
    """One output frame: (out_h, out_w, 3) uint8."""
    h, w = frame.shape[:2]
    box = crop_box(w, h, out_w, out_h, crop)
    img = Image.fromarray(np.asarray(frame)).resize((out_w, out_h), _resample(box, out_w, out_h), box=box,
                                                    reducing_gap=None if _resample(box, out_w, out_h) == Image.BOX
                                                    else 2.0)
    # (Lanczos: twice the time, no visible difference at the wall's size; this runs for every frame of every webcam)
    return _finish(img, colour)


def render_region(hi, p, src_w, src_h, out_w, out_h, crop=None, colour=None) -> np.ndarray:
    """One output frame from a hi picture: ``hi`` is plan ``p``'s region of a src_w x src_h stream. The crop is cut
    exactly as render() would from the whole frame; at one source pixel per LED it's a straight copy (no blur)."""
    l, t, r, b = crop_box(src_w, src_h, out_w, out_h, crop)
    hh, hw = hi.shape[:2]
    sx, sy = hw / p["w"], hh / p["h"]
    box = (max(0.0, (l - p["x"]) * sx), max(0.0, (t - p["y"]) * sy),
           min(float(hw), (r - p["x"]) * sx), min(float(hh), (b - p["y"]) * sy))
    x0, y0 = max(0, int(box[0]) - 2), max(0, int(box[1]) - 2)        # just the box (and the filter's margin)
    x1, y1 = min(hw, int(box[2]) + 3), min(hh, int(box[3]) + 3)
    part = np.ascontiguousarray(hi[y0:y1, x0:x1])
    box = (box[0] - x0, box[1] - y0, box[2] - x0, box[3] - y0)
    rs = _resample(box, out_w, out_h)
    img = Image.fromarray(part).resize((out_w, out_h), rs, box=box, reducing_gap=None if rs == Image.BOX else 2.0)
    return _finish(img, colour)


# --- wire format to the Pi -------------------------------------------------------------------------------------
MAGIC = b"BSWC"
HEADER = 12                  # MAGIC, width u16, height u16, frame age in ms u32 (big-endian)
PLACEHOLDER_AGE = 0xFFFFFFFF  # the age of the still sent while a feed starts: a placeholder, not part of the video


def pack(arr, age_ms=0) -> bytes:
    h, w = arr.shape[:2]
    return MAGIC + w.to_bytes(2, "big") + h.to_bytes(2, "big") + min(int(age_ms), 2**32 - 1).to_bytes(4, "big") \
        + arr.tobytes()
