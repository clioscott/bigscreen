"""Clarity lab: a few seconds of a webcam, looping on the wall, with the picture's settings changed as you watch.

The control panel's Clarity lab page picks a webcam and records one short loop of it (the same seconds at every
resolution the stream has). Two ways to look at it:

- "split": a slice of the picture you choose, twice, side by side: version A on the left, B on the right, a 2 px
  black line between them. Change either side's settings and that half is rendered again from the same loop.
- "full": the webcam's own above-the-clock framing (``crops.tall``), full width, as the wall would play it, with one
  set of settings to tune.

A version is {"height", "fps": 30 | 60, "scaling": "current" | "linear", "sharpen": 0-2, "brightness", "contrast",
"saturation", "dither": bool}:
- scaling "current" is the wall's own path (hub/webcams.py: the video engine cuts the crop and averages it down in
  light, gpu_filter; the swscale chain where that is off); "swscale" is the swscale chain whatever the wall uses
  (it averages coded values, which dims small lights); "linear" averages in linear light here in numpy, exactly;
- sharpen and the three colour settings are the webcam's own (``cams.<id>.colour``), with the wall's maths;
- dither picks each LED's level with a fresh noise pattern every frame, so an area averages to the light wanted
  instead of every pixel being rounded down to the wall's nearest level (levels() below).

The work runs in its own process (``python -m hub.clarity <spec.json>``, started by hub/app.py) at low priority, so
decoding 4K never holds up the hub's live webcams. To make a change show within seconds, a session is kept in
``cache/clarity/session``: the loop's downloaded segments per resolution, and each version's frames scaled to the
zone but not yet sharpened, coloured or dithered (so those three never decode anything again). Progress goes to
``cache/clarity_state.json``; the clip to ``data/clips/clarity.bin|.json`` (display/modules/clip.py loops it).
"""

import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image

from hub import paths
from hub import webcams as wc
from hub.util import atomic_write_json

NAME = "clarity"
GAP = 2                       # black columns between the two halves
SECONDS = (5, 10)
FPS = (30, 60)
SCALINGS = ("current", "swscale", "linear")
MODES = ("split", "full")
BUILD_TIMEOUT = 300.0         # a build that takes longer than this is stopped (hub/app.py)
MAX_SLICE_PX = 9_000_000      # a "linear" crop is averaged here frame by frame (a whole 4K frame is 8.3 M pixels)
KEEP_SCALED = 4               # scaled-frame caches kept per session (12-70 MB each as a rule; a 10 s, 60 fps,
                              # linear full frame is 280 MB)
STATE = paths.CACHE_DIR / "clarity_state.json"
WORK = paths.CACHE_DIR / "clarity"
SESSION = WORK / "session"
CLIPS = paths.DATA_DIR / "clips"

_LIN8 = None


def half_width(zone_w):
    return (int(zone_w) - GAP) // 2


def lin8():
    """sRGB code (0-255) -> linear light (float32 0-1)."""
    global _LIN8
    if _LIN8 is None:
        a = np.arange(256, dtype=np.float64) / 255.0
        _LIN8 = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4).astype(np.float32)
    return _LIN8


def to_srgb(a):
    a = np.clip(a, 0.0, 1.0)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055).astype(np.float32)


def linear_box(frame, out_w, out_h):
    """An (h, w, 3) uint8 picture averaged down to out_w x out_h in linear light: float32 sRGB-coded 0-1. A whole
    number of source pixels per LED is a plain block average; otherwise each LED takes its exact (fractional) box.
    A crop smaller than the output is enlarged smoothly instead."""
    h, w = frame.shape[:2]
    lin = lin8()[frame]
    k = w // out_w if out_w else 0
    if k >= 1 and w == k * out_w and h == k * out_h:
        return to_srgb(lin.reshape(out_h, k, out_w, k, 3).mean(axis=(1, 3)))
    how = Image.BOX if (w >= out_w and h >= out_h) else Image.BILINEAR
    chans = [np.asarray(Image.fromarray(np.ascontiguousarray(lin[..., c]), "F").resize((out_w, out_h), how))
             for c in range(3)]
    return to_srgb(np.stack(chans, -1))


def sharpen(a, amount):
    """The wall's unsharp mask (hub/webcams.py _sharpen_kernel) on a float picture."""
    if not amount:
        return a
    p = np.pad(a, ((1, 1), (1, 1), (0, 0)), mode="edge")
    blur = (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:] + 2 * p[1:-1, :-2] + 4 * p[1:-1, 1:-1] + 2 * p[1:-1, 2:]
            + p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) / 16.0
    return np.clip(a + float(amount) * (a - blur), 0.0, 1.0)


def adjust(a, brightness=1.0, contrast=1.0, saturation=1.0):
    """The wall's colour settings (hub/webcams.py adjust: CSS filter maths) on a float picture."""
    if brightness == 1.0 and contrast == 1.0 and saturation == 1.0:
        return a
    a = a * np.float32(brightness)
    if contrast != 1.0:
        a = (a - 0.5) * np.float32(contrast) + 0.5
    if saturation != 1.0:
        s = saturation
        m = np.array([[0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s],
                      [0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s],
                      [0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s]], np.float32)
        a = a @ m.T
    return np.clip(a, 0.0, 1.0)


# --- the wall's levels ------------------------------------------------------------------------------------------
def levels(brightness):
    """What the LED library shows for each 8-bit value at this brightness: (level per code, the levels that some
    code gives, lowest first, and the lowest code for each of those). It maps a code through the CIE curve to 11
    bits and shows the top pwm_bits (8), so values are rounded down, the darkest codes all come out black, and
    near the top some levels can't be reached at all."""
    c = np.arange(256, dtype=np.float64)
    level = (np.round(2047 * _curve(c * float(brightness) / 255.0)).astype(int)) >> 3
    avail, codes = np.unique(level, return_index=True)
    return level, avail.astype(np.float64), codes.astype(np.uint8)


def _curve(v):
    return np.where(v <= 8, v / 902.3, ((v + 16) / 116.0) ** 3)


def dither(a, brightness, rng, table=None):
    """A float picture (0-1) as 8-bit codes whose wall levels average to the light wanted: each LED takes the
    level the wall can show below or above its target, more often the nearer one."""
    _, avail, codes = table or levels(brightness)
    target = np.clip(2047 * _curve(np.clip(a, 0, 1) * float(brightness)) / 8.0, avail[0], avail[-1])
    i = np.clip(np.searchsorted(avail, target, side="right") - 1, 0, len(avail) - 2)
    lo, hi = avail[i], avail[i + 1]
    noise = rng.random(a.shape[:2], dtype=np.float32)[..., None]         # one pattern for R, G and B
    return codes[i + ((target - lo) / (hi - lo) > noise)]


def finish(a, v, brightness, rng, table=None):
    """One scaled frame (float 0-1) as the bytes the wall gets for version v: sharpened, coloured, then dithered or
    rounded (the wall's own order: hub/webcams.py _finish)."""
    a = sharpen(a, v.get("sharpen") or 0)
    a = adjust(a, v.get("brightness", 1.0), v.get("contrast", 1.0), v.get("saturation", 1.0))
    if v.get("dither"):
        return dither(a, brightness, rng, table)
    return (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)


# --- versions and formats ---------------------------------------------------------------------------------------
def _num(v, key, default, lo, hi):
    try:
        x = float(v.get(key, default) if v.get(key) is not None else default)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    return round(max(lo, min(hi, x)), 3)


def clean_version(v, heights):
    """A version from the panel, checked: its height is one the stream has."""
    v = v if isinstance(v, dict) else {}
    h = v.get("height")
    if h in (None, "best"):
        h = max(heights)
    if h not in heights:
        raise ValueError(f"the stream has no {h}p (it has {', '.join(str(x) + 'p' for x in sorted(heights))})")
    fps = int(v.get("fps") or 30)
    if fps not in FPS:
        raise ValueError("fps must be 30 or 60")
    scaling = v.get("scaling") or "current"
    if scaling not in SCALINGS:
        raise ValueError("scaling must be current, swscale or linear")
    return {"height": int(h), "fps": fps, "scaling": scaling, "sharpen": _num(v, "sharpen", 0.0, 0.0, 2.0),
            "brightness": _num(v, "brightness", 1.0, 0.4, 2.0), "contrast": _num(v, "contrast", 1.0, 0.5, 2.0),
            "saturation": _num(v, "saturation", 1.0, 0.0, 2.0), "dither": bool(v.get("dither"))}


def format_for(formats, height, fps):
    """The stream's format at this height: the one whose frame rate is nearest the version's (60 needs a 60 fps one)."""
    cands = [f for f in formats if f["h"] == height]
    if not cands:
        raise ValueError(f"no {height}p format")
    return min(cands, key=lambda f: (abs(f["fps"] - fps), f.get("audio", False)))


def describe(v):
    bits = [f"{v['height']}p", f"{v['fps']} fps",
            {"linear": "linear light (exact)", "swscale": "old scaling"}.get(v["scaling"], "current scaling")]
    if v.get("sharpen"):
        bits.append(f"sharpen {v['sharpen']:g}")
    for key in ("brightness", "contrast", "saturation"):
        if v.get(key, 1.0) != 1.0:
            bits.append(f"{key} {round(v[key] * 100)}%")
    if v.get("dither"):
        bits.append("dithered")
    return " · ".join(bits)


def compose(a, b, fps_a, fps_b, zone_w):
    """Two versions' frames ((n, h, hw, 3) uint8 each, at their own frame rates, the same seconds) side by side at
    the faster rate: a slower version's frames each show twice."""
    fps = max(fps_a, fps_b)
    secs = min(len(a) / fps_a, len(b) / fps_b)
    n = max(1, int(secs * fps + 1e-6))
    h, hw = a.shape[1], a.shape[2]
    out = np.zeros((n, h, zone_w, 3), np.uint8)
    ia = np.minimum(np.arange(n) * fps_a // fps, len(a) - 1)
    ib = np.minimum(np.arange(n) * fps_b // fps, len(b) - 1)
    out[:, :, :hw] = a[ia]
    out[:, :, zone_w - hw:] = b[ib]
    return out, fps


# --- state ------------------------------------------------------------------------------------------------------
def write_state(**kw):
    st = read_state()
    st.update(kw, at=time.time())
    atomic_write_json(STATE, st)
    return st


def read_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# --- the session: one recorded loop, kept while it is being looked at -----------------------------------------------
def fmt_key(f):
    return f"{f['h']}p{round(f['fps'])}{'d' if f.get('dash') else 'h'}"


def read_session():
    try:
        with open(SESSION / "session.json", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def clear_session():
    shutil.rmtree(SESSION, ignore_errors=True)


def session_for(spec):
    """The session this build belongs to: the one on disk when it is the same webcam and length (and no new loop
    was asked for), else a new one with freshly picked segments."""
    sess = read_session()
    if spec.get("new_loop") or sess.get("url") != spec["url"] or sess.get("seconds") != int(spec["seconds"]) \
            or not sess.get("seqs"):
        seqs = pick_segments(spec["numbering"], int(spec["seconds"]))     # (first: if it fails, the old loop stays)
        clear_session()
        SESSION.mkdir(parents=True, exist_ok=True)
        sess = {"url": spec["url"], "cam": spec.get("cam"), "seconds": int(spec["seconds"]),
                "seqs": seqs, "files": {}, "recorded": time.time()}
        atomic_write_json(SESSION / "session.json", sess)
    return sess


def segments(fmt, seqs):
    """The stream's segments ``seqs`` in this format, joined: (bytes, container)."""
    if fmt.get("dash"):
        return b"".join(wc.seg_get(fmt["url"], n) for n in seqs), "matroska"
    by_seq = {s.seq: s for s in wc.get_playlist(fmt["url"])}
    missing = [n for n in seqs if n not in by_seq]
    if missing:
        raise RuntimeError(f"the {fmt['h']}p stream no longer lists segment {missing[0]}")
    return b"".join(wc.seg_get(by_seq[n].url) for n in seqs), "mpegts"


def pick_segments(fmt, seconds):
    """Segment numbers covering ``seconds``, a little behind the newest (so every resolution has them already)."""
    segs = wc.get_playlist(fmt["url"])
    if len(segs) < 4:
        raise RuntimeError("the stream's playlist is too short")
    dur = segs[-3].dur or 5.0
    k = max(1, math.ceil(seconds / dur))
    last = len(segs) - 3
    first = max(0, last - k + 1)
    return [s.seq for s in segs[first:last + 1]]


def session_file(sess, fmt):
    """The loop in this format, downloaded once per session: (path, container)."""
    key = fmt_key(fmt)
    hit = sess["files"].get(key)
    if hit and (SESSION / hit[0]).is_file():
        return str(SESSION / hit[0]), hit[1]
    try:
        data, container = segments(fmt, sess["seqs"])
    except Exception as e:
        raise RuntimeError(f"this loop is no longer on YouTube at {fmt['h']}p ({e}): record a new loop")
    name = f"seg_{key}.{'webm' if container == 'matroska' else 'ts'}"
    with open(SESSION / name, "wb") as f:
        f.write(data)
    sess["files"][key] = [name, container]
    atomic_write_json(SESSION / "session.json", sess)
    return str(SESSION / name), container


def _prune_scaled():
    files = sorted(SESSION.glob("scaled_*.npy"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[KEEP_SCALED:]:
        p.unlink(missing_ok=True)


def gpu_scaling():
    """Does the wall scale YouTube video on the video engine here (hub/webcams.py gpu_filter)? Then "current" does too."""
    return bool(getattr(wc, "GPU_SCALE", False)) and hasattr(wc, "gpu_filter") and wc.hw_decode()


def _decode(args, fw, fh, n, out, linear, progress):
    """Run ffmpeg and read up to n raw RGB frames of fw x fh into ``out``: (frames read, the end of its messages)."""
    errlog = open(WORK / "ffmpeg.log", "w+b")                # (a file: a pipe nobody reads would block ffmpeg)
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errlog)
    size, got = fw * fh * 3, 0
    out_h, out_w = out.shape[1], out.shape[2]
    try:
        while got < n:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(fh, fw, 3)
            out[got] = linear_box(frame, out_w, out_h) if linear else frame
            got += 1
            if progress and got % 15 == 0:
                progress(got / n)
    finally:
        proc.stdout.close()
        if proc.poll() is None and got < n:
            proc.kill()
        proc.wait()
        errlog.seek(0)
        err = errlog.read()[-2000:].decode("utf-8", "replace").strip()
        errlog.close()
    return got, err


def scaled(path, container, fmt, v, crop, out_w, out_h, n, progress=None):
    """Version v's n frames of the crop, scaled to the zone but not yet sharpened, coloured or dithered:
    (n, out_h, out_w, 3), uint8, or float16 (0-1) for "linear". Kept in the session, so a change to sharpening,
    colour or dithering decodes nothing.

    "current" is whatever the wall does for a YouTube stream on this hub: cut and averaged in light on the video
    engine (webcams.gpu_filter) where that is on, else the swscale chain; a GPU run that gives nothing falls back to
    swscale, as a feed does. "swscale" is always the swscale chain (the wall's before the video engine took over)."""
    l, t, r, b = wc.crop_box(fmt["w"], fmt["h"], out_w, out_h, crop)
    cw, ch = r - l, b - t
    linear = v["scaling"] == "linear"
    # (the wall only cuts and scales in one step when the crop has at least the zone's pixels, webcams.plan: "never
    # scaled up". A smaller crop it enlarges on the CPU, so that is what "current" does for one too.)
    how = "linear" if linear else \
        "gpu" if (v["scaling"] == "current" and gpu_scaling() and cw >= out_w and ch >= out_h) else "sws"

    def key_for(how):
        return hashlib.sha1(json.dumps([fmt_key(fmt), v["fps"], how, [l, t, r, b], out_w, out_h, n]).encode()).hexdigest()[:16]
    key = key_for(how)
    cache = SESSION / f"scaled_{key}.npy"
    if cache.is_file():
        try:
            arr = np.load(cache)
            os.utime(cache)
            if arr.shape == (n, out_h, out_w, 3):
                return arr
        except Exception:                                 # (a cut-short file: EOFError, ValueError…) render it again
            pass
    if linear and cw * ch > MAX_SLICE_PX:
        raise RuntimeError("that view is too big for linear-light scaling at this resolution: pick a smaller source resolution")
    head = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error"]
    tail = ["-an", "-sn", "-dn", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    out = np.zeros((n, out_h, out_w, 3), np.float16 if linear else np.uint8)
    got, err = 0, ""
    if how == "gpu":
        vf = f"fps={v['fps']},hwmap=derive_device=vulkan," + wc.gpu_filter(out_w, out_h, (l, t, cw, ch))
        got, err = _decode(head + wc.hw_input(gpu=True) + ["-f", container, "-i", path, "-vf", vf] + tail,
                           out_w, out_h, n, out, False, progress)
        if got == 0:                                      # the video engine's scaler wouldn't run: the CPU's instead
            why = err.splitlines()[-1][:200] if err else "no output"
            # said on the page (or "As now" beside "Old" would look the same and read as "no difference"), and kept
            # under the swscale key, so the next build tries the video engine again
            write_state(note=f"The video engine's scaler didn't run ({why}), so \"As now\" is showing the old method.")
            how = "sws"
            key = key_for(how)
            cache = SESSION / f"scaled_{key}.npy"
    if how != "gpu":
        cutf = wc.cut(l, t, cw, ch)
        if linear:
            vf = f"fps={v['fps']},{cutf}scale=iw:ih:flags=bicubic+full_chroma_int+accurate_rnd,format=rgb24"
            fw, fh = cw, ch
        else:
            flags = wc.SWS if (cw >= out_w and ch >= out_h) else "flags=bilinear+full_chroma_int+accurate_rnd"
            vf = f"fps={v['fps']},{cutf}scale={out_w}:{out_h}:{flags},format=rgb24"
            fw, fh = out_w, out_h
        hw = (wc.hw_input() if hasattr(wc, "hw_input") else ["-hwaccel", "vaapi", "-hwaccel_device", wc.HW_DEVICE]) \
            if wc.hw_decode() else []
        got, err = _decode(head + hw + ["-f", container, "-i", path, "-vf", vf] + tail, fw, fh, n, out, linear, progress)
    if got == 0:
        raise RuntimeError(f"ffmpeg decoded nothing from the {fmt['h']}p stream: {err.splitlines()[-1][:200] if err else 'no output'}")
    if got < n:                                           # a segment a few frames short: hold the last one
        out[got:] = out[got - 1]
    try:                                                  # (the cache only saves time: never fail a build over it)
        tmp = SESSION / f".scaled_{key}.tmp.npy"
        np.save(tmp, out)
        os.replace(tmp, cache)
        _prune_scaled()
    except OSError:
        pass
    return out


def render(frames, v, brightness, seed):
    """Scaled frames finished for version v: (n, h, w, 3) uint8."""
    table = levels(brightness)
    out = np.empty(frames.shape, np.uint8)
    as_float = frames.dtype != np.uint8
    for i in range(len(frames)):
        a = frames[i].astype(np.float32) if as_float else frames[i].astype(np.float32) / 255.0
        out[i] = finish(a, v, brightness, np.random.default_rng(seed + i), table)
    return out


def build(spec):
    """spec: {"mode": "split" | "full", "url", "formats": {"a": format, "b": format}, "numbering": an HLS format,
    "a", "b" (cleaned versions; "full" uses only a), "crop", "seconds", "zone": [w, h], "brightness", "blind",
    "new_loop", "cam", "label"}. Writes the clip; returns its meta."""
    zone_w, zone_h = spec["zone"]
    full = spec.get("mode") == "full"
    out_w = zone_w if full else half_width(zone_w)
    seconds = int(spec["seconds"])
    sides = ["a"] if full else ["a", "b"]
    WORK.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)

    write_state(stage="Finding the loop", pct=2)
    sess = session_for(spec)
    rendered = []
    for i, k in enumerate(sides):
        v, fmt = spec[k], spec["formats"][k]
        base = 5 + 85 * i / len(sides)
        span = 85 / len(sides)
        write_state(stage=f"Getting the loop at {fmt['h']}p", pct=base)
        path, container = session_file(sess, fmt)
        write_state(stage=f"Rendering {'the picture' if full else 'side ' + k.upper()}: {describe(v)}", pct=base + span * 0.15)
        frames = scaled(path, container, fmt, v, spec.get("crop"), out_w, zone_h, seconds * v["fps"],
                        progress=lambda p, base=base, span=span: write_state(pct=base + span * (0.15 + 0.6 * p)))
        write_state(pct=base + span * 0.8)
        rendered.append(render(frames, v, spec["brightness"], 1000 * (i + 1)))

    meta = {"mode": "full" if full else "split", "built_at": time.time(), "cam": spec.get("cam"),
            "label": spec.get("label"), "seconds": seconds, "recorded": sess.get("recorded"),
            "a": describe(spec["a"]), "blind": False,
            # what the page set, so it can carry on tuning this loop after a reload
            "spec": {"mode": "full" if full else "split", "crop": spec.get("crop"), "a": spec["a"], "b": spec["b"]}}
    if full:
        frames, fps = rendered[0], spec["a"]["fps"]
        meta.update(left="A", right="A")
    else:
        swap = False
        if spec.get("blind"):                             # drawn once per loop: a tweak never reshuffles the sides
            if "swap" not in sess:
                sess["swap"] = random.random() < 0.5
                atomic_write_json(SESSION / "session.json", sess)
            swap = bool(sess["swap"])
        left, right = (1, 0) if swap else (0, 1)
        frames, fps = compose(rendered[left], rendered[right], spec[sides[left]]["fps"], spec[sides[right]]["fps"], zone_w)
        meta.update(left="AB"[left], right="AB"[right], b=describe(spec["b"]), blind=bool(spec.get("blind")))
    meta.update(n=int(frames.shape[0]), h=int(frames.shape[1]), w=int(frames.shape[2]), fps=fps)
    write_state(stage="Writing the clip", pct=95)
    tmp = CLIPS / f".{NAME}.bin.tmp"
    try:
        with open(tmp, "wb") as f:
            frames.tofile(f)
        os.replace(tmp, CLIPS / f"{NAME}.bin")
    finally:
        tmp.unlink(missing_ok=True)
    atomic_write_json(CLIPS / f"{NAME}.json", meta)
    return meta


def main(spec_path):
    try:
        os.nice(10)                                       # the hub's live webcams come first
    except (AttributeError, OSError):
        pass
    paths.ensure_dirs()
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    write_state(running=True, done=False, error="", note="", stage="Starting", pct=0, started=time.time())
    try:
        meta = build(spec)
    except Exception as e:
        write_state(running=False, done=False, error=f"{type(e).__name__}: {e}"[:400], stage="Failed")
        raise
    write_state(running=False, done=True, error="", stage="Ready", pct=100, meta=meta)


if __name__ == "__main__":
    main(sys.argv[1])
