"""Clarity lab: the same few seconds of a webcam, rendered two ways, looping side by side on the wall.

The control panel's Clarity lab page picks a webcam, a slice of its picture and two versions (source resolution,
frames a second, how it is scaled down, sharpening, dithering). The hub downloads the same segments of the stream at
the resolutions the two versions need, renders each to exactly half the zone, and writes one clip of ready-made
frames (``data/clips/clarity.bin|.json``) that display/modules/clip.py loops: version A on the left, B on the right
(or swapped, for a blind comparison), a 2 px black line between them.

The work runs in its own process (``python -m hub.clarity <spec.json>``, started by hub/app.py) at low priority, so
decoding 4K never holds up the hub's live webcams. Progress goes to ``cache/clarity_state.json``.

A version is {"height": 1080, "fps": 30 | 60, "scaling": "current" | "linear", "sharpen": 0-2, "dither": bool}:
- scaling "current" is the wall's own path (hub/webcams.py: ffmpeg cuts the slice and averages it down in one step);
  "linear" averages in linear light instead (bright points on dark keep their brightness);
- dither picks each LED's level with a fresh noise pattern every frame, so an area averages to the light wanted
  instead of every pixel being rounded down to the wall's nearest level (LEVELS below).
"""

import json
import math
import os
import random
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
SCALINGS = ("current", "linear")
MAX_SLICE_PX = 4_000_000      # a "linear" slice is averaged here frame by frame: more source pixels than this is slow
STATE = paths.CACHE_DIR / "clarity_state.json"
WORK = paths.CACHE_DIR / "clarity"
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
    A slice smaller than the output is enlarged smoothly instead."""
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
    """One rendered frame (float 0-1) as the bytes the wall gets for version v."""
    a = sharpen(a, v.get("sharpen") or 0)
    if v.get("dither"):
        return dither(a, brightness, rng, table)
    return (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)


# --- versions and formats ---------------------------------------------------------------------------------------
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
        raise ValueError("scaling must be current or linear")
    try:
        sh = max(0.0, min(2.0, float(v.get("sharpen") or 0)))
    except (TypeError, ValueError):
        raise ValueError("sharpen must be a number")
    return {"height": int(h), "fps": fps, "scaling": scaling, "sharpen": sh, "dither": bool(v.get("dither"))}


def format_for(formats, height, fps):
    """The stream's format at this height: the one whose frame rate is nearest the version's (60 needs a 60 fps one)."""
    cands = [f for f in formats if f["h"] == height]
    if not cands:
        raise ValueError(f"no {height}p format")
    return min(cands, key=lambda f: (abs(f["fps"] - fps), f.get("audio", False)))


def describe(v):
    bits = [f"{v['height']}p", f"{v['fps']} fps", "linear light" if v["scaling"] == "linear" else "current scaling"]
    if v["sharpen"]:
        bits.append(f"sharpen {v['sharpen']:g}")
    if v["dither"]:
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


# --- the build (its own process) --------------------------------------------------------------------------------
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


def render(path, container, fmt, v, crop, out_w, out_h, n, brightness, seed, progress=None):
    """Version v's n frames of the slice from a downloaded piece of the stream: (n, out_h, out_w, 3) uint8."""
    l, t, r, b = wc.crop_box(fmt["w"], fmt["h"], out_w, out_h, crop)
    cw, ch = r - l, b - t
    cutf = wc.cut(l, t, cw, ch)
    if v["scaling"] == "linear":
        if cw * ch > MAX_SLICE_PX:
            raise RuntimeError("that slice is too big for linear-light scaling at this resolution: zoom in or pick a smaller source")
        vf = f"fps={v['fps']},{cutf}scale=iw:ih:flags=bicubic+full_chroma_int+accurate_rnd,format=rgb24"
        fw, fh = cw, ch
    else:
        flags = wc.SWS if (cw >= out_w and ch >= out_h) else "flags=bilinear+full_chroma_int+accurate_rnd"
        vf = f"fps={v['fps']},{cutf}scale={out_w}:{out_h}:{flags},format=rgb24"
        fw, fh = out_w, out_h
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error"]
    if wc.hw_decode():
        args += ["-hwaccel", "vaapi", "-hwaccel_device", wc.HW_DEVICE]
    args += ["-f", container, "-i", path, "-an", "-sn", "-dn", "-vf", vf, "-frames:v", str(n),
             "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    table = levels(brightness)
    out = np.zeros((n, out_h, out_w, 3), np.uint8)
    size, got = fw * fh * 3, 0
    try:
        while got < n:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            frame = np.frombuffer(raw, np.uint8).reshape(fh, fw, 3)
            a = linear_box(frame, out_w, out_h) if v["scaling"] == "linear" else frame.astype(np.float32) / 255.0
            out[got] = finish(a, v, brightness, np.random.default_rng(seed + got), table)
            got += 1
            if progress and got % 15 == 0:
                progress(got / n)
    finally:
        proc.stdout.close()
        err = proc.stderr.read().decode("utf-8", "replace").strip()
        proc.stderr.close()
        proc.wait()
    if got == 0:
        raise RuntimeError(f"ffmpeg decoded nothing from the {fmt['h']}p stream: {err.splitlines()[-1][:200] if err else 'no output'}")
    if got < n:                                           # a segment a few frames short: hold the last one
        out[got:] = out[got - 1]
    return out


def build(spec):
    """spec: {"formats": {height: format}, "numbering": an HLS format, "a", "b" (cleaned versions), "crop", "seconds", "zone": [w, h],
    "brightness", "blind", "cam", "label"}. Writes the clip; returns its meta."""
    zone_w, zone_h = spec["zone"]
    hw = half_width(zone_w)
    seconds = int(spec["seconds"])
    fmts = {int(k): v for k, v in spec["formats"].items()}
    versions = [spec["a"], spec["b"]]
    WORK.mkdir(parents=True, exist_ok=True)
    CLIPS.mkdir(parents=True, exist_ok=True)

    write_state(stage="Downloading the same seconds at each resolution", pct=2)
    # numbered from an HLS format's playlist (the DASH ones, 1440p and 4K, share the numbers)
    seqs = pick_segments(spec["numbering"], seconds)
    files = {}
    try:
        for i, (h, f) in enumerate(sorted(fmts.items())):
            data, container = segments(f, seqs)
            p = WORK / f"seg_{h}.{'webm' if container == 'matroska' else 'ts'}"
            with open(p, "wb") as fh:
                fh.write(data)
            files[h] = (str(p), container)
            write_state(stage=f"Downloaded {h}p ({len(data) / 1e6:.1f} MB)", pct=5 + 15 * (i + 1) / len(fmts))

        rendered = []
        for i, v in enumerate(versions):
            path, container = files[v["height"]]
            base = 20 + 38 * i
            write_state(stage=f"Rendering version {'AB'[i]}: {describe(v)}", pct=base)
            rendered.append(render(path, container, fmts[v["height"]], v, spec.get("crop"), hw, zone_h,
                                   seconds * v["fps"], spec["brightness"], 1000 * (i + 1),
                                   progress=lambda p, base=base: write_state(pct=base + 38 * p)))
    finally:
        for p, _ in files.values():
            try:
                os.unlink(p)
            except OSError:
                pass

    swap = bool(spec.get("blind")) and random.random() < 0.5
    left, right = (1, 0) if swap else (0, 1)
    frames, fps = compose(rendered[left], rendered[right], versions[left]["fps"], versions[right]["fps"], zone_w)
    meta = {"n": int(frames.shape[0]), "h": int(frames.shape[1]), "w": int(frames.shape[2]), "fps": fps,
            "built_at": time.time(), "cam": spec.get("cam"), "label": spec.get("label"), "seconds": seconds,
            "left": "AB"[left], "right": "AB"[right], "a": describe(versions[0]), "b": describe(versions[1]),
            "blind": bool(spec.get("blind"))}
    write_state(stage="Writing the clip", pct=97)
    tmp = CLIPS / f".{NAME}.bin.tmp"
    with open(tmp, "wb") as f:
        frames.tofile(f)
    os.replace(tmp, CLIPS / f"{NAME}.bin")
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
    write_state(running=True, done=False, error="", stage="Starting", pct=0, meta=None, started=time.time())
    try:
        meta = build(spec)
    except Exception as e:
        write_state(running=False, done=False, error=f"{type(e).__name__}: {e}"[:400], stage="Failed")
        raise
    write_state(running=False, done=True, error="", stage="Ready", pct=100, meta=meta)


if __name__ == "__main__":
    main(sys.argv[1])
