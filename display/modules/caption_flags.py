"""Flags on a webcam's caption: a row of pixel flags in any corner, stacked on the caption when they share its corner.

The flag set itself isn't in the repo (it includes artwork whose licence doesn't allow publishing it): it lives in the
hub's runtime data, ``<data>/flags/`` (synced to the Pi like the other data), as ``index.json`` and one PNG per flag
per size, ``<id>_<size>.png``. index.json: {"version": 1, "flags": {id: {name, group, country, region, default,
files: {"18": file, "23": file}}}}. Countries are drawn at 23 px by default and everything else at 18 px.

``overlay.flags`` in a webcam's config (DEFAULT_FLAGS fills the gaps):
    on       show flags
    ids      the flags, left to right; null: automatic (the webcam's country, then its province / state / prefecture,
             common/flagplaces.py), so a webcam with a known place shows its flags without being set up
    corner   auto (the caption's corner) | tl tc tr / bl bc br. In the caption's corner the row sits right against
             the caption: above it in a bottom corner, below it in a top one, lined up with its outer edge
    size     auto (each flag's own default) | 18 | 23
"""

from __future__ import annotations

import json
import os

import numpy as np

from common import flagplaces

FLAG_DIRS = [os.path.join(os.environ.get("BIGSCREEN_DATA_DIR", "/dev/shm/bigscreen"), "flags")]
DEFAULT_FLAGS = {"on": True, "ids": None, "corner": "auto", "size": "auto"}
CORNERS = ("tl", "tc", "tr", "bl", "bc", "br")
SIZES = (18, 23)
GAP = 2                       # between flags (each has a 1 px outline in the caption's background colour)
MAX_FLAGS = 6

_index = {"path": None, "mtime": None, "flags": {}}      # mtime: the index's and its folder's (a replaced PNG)
_images: dict = {}


def flags_of(ov) -> dict:
    got = ov.get("flags") if isinstance(ov.get("flags"), dict) else {}
    return {**DEFAULT_FLAGS, **got}


def index() -> dict:
    """{id: entry} from the first flag folder that has an index.json (re-read when it changes); {} when there is none."""
    for d in FLAG_DIRS:
        p = os.path.join(d, "index.json")
        try:
            mt = (os.path.getmtime(p), os.path.getmtime(d))   # the folder changes when rsync puts a new PNG in place
        except OSError:
            continue
        if _index["path"] != p or _index["mtime"] != mt:
            try:
                flags = json.load(open(p, encoding="utf-8")).get("flags") or {}
            except (OSError, ValueError):
                flags = {}
            _index.update(path=p, mtime=mt, flags=flags if isinstance(flags, dict) else {})
            _images.clear()
        return _index["flags"]
    return {}


def ids_for(cam: dict, ov: dict) -> list[str]:
    """The flags this webcam's caption shows, left to right (none when they're off or the set isn't there)."""
    fs = flags_of(ov)
    if not fs.get("on"):
        return []
    known = index()
    if not known:
        return []
    ids = fs.get("ids")
    if isinstance(ids, list):
        out = [str(i) for i in ids if str(i) in known]
    else:
        out = flagplaces.default_ids(cam, known)
    return out[:MAX_FLAGS]


def image(fid: str, size) -> np.ndarray | None:
    """The flag as an (h, w, 4) uint8 array at ``size`` (18, 23, or "auto": its own default), the other size when that
    one is missing; None when it can't be loaded."""
    e = index().get(fid)
    if not e:
        return None
    files = e.get("files") or {}
    want = int(e.get("default") or 18) if size not in SIZES else int(size)
    order = [str(want)] + [str(s) for s in SIZES if s != want]
    for s in order:
        fn = files.get(s)
        if not fn:
            continue
        key = (fid, s)
        if key not in _images:
            arr = None
            path = os.path.join(os.path.dirname(_index["path"] or ""), fn)
            try:
                from PIL import Image
                with Image.open(path) as im:
                    arr = np.asarray(im.convert("RGBA")).copy()
            except Exception:
                arr = None
            _images[key] = arr
        if _images[key] is not None:
            return _images[key]
    return None


def row_size(imgs) -> tuple[int, int]:
    """(width, height) of a row of flags, outlines included."""
    if not imgs:
        return 0, 0
    w = sum(a.shape[1] + 2 for a in imgs) + GAP * (len(imgs) - 1)
    h = max(a.shape[0] for a in imgs) + 2
    return w, h


def draw(zc, anchor, w, h, ov, ids, box, place):
    """Draw the row of flags ``ids`` in the zone canvas zc (w × h). ``anchor``: the caption's box (x0, y0, x1, y1),
    with the Japanese caption when that sits on it, or None. ``box(zc, x, y, w, h, rgb, opacity)`` draws a background
    box, ``place(corner, bw, bh)`` gives a box's top-left in a corner. Returns the row's box, or None."""
    if not ids:
        return None
    fs = flags_of(ov)
    imgs = [a for a in (image(i, fs.get("size")) for i in ids) if a is not None]
    if not imgs:
        return None
    rw, rh = row_size(imgs)
    if rw > w or rh > h:
        return None
    own = str(ov.get("corner") or "bl")
    corner = fs.get("corner") if fs.get("corner") in CORNERS else own
    if corner == own and anchor:                           # on the caption: above it (bottom) or below it (top)
        x0, y0, x1, y1 = anchor
        side = own[1:] if own[1:] in ("l", "c", "r") else "l"
        bx = x0 if side == "l" else x1 - rw if side == "r" else (x0 + x1 - rw) // 2
        by = y0 - 1 - rh if own[:1] != "t" else y1 + 1
    else:
        bx, by = place(corner, rw, rh)
    bx, by = max(0, min(bx, w - rw)), max(0, min(by, h - rh))
    bg = ov.get("bg")
    rgb = tuple(int(bg[i:i + 2], 16) for i in (1, 3, 5)) if isinstance(bg, str) and len(bg) == 7 else (0, 0, 0)
    op = ov.get("opacity", 0.7)
    try:
        op = min(1.0, max(0.0, float(op)))
    except (TypeError, ValueError):
        op = 0.7
    x = bx
    for a in imgs:                                         # bottoms lined up: a 23 px country beside 18 px regions
        fh, fw = a.shape[:2]
        y = by + rh - 2 - fh
        box(zc, x, y, fw + 2, fh + 2, rgb, op)              # 1 px outline in the caption's background
        m = a[..., 3] > 127
        zc.Blit(a[..., :3], x + 1, y + 1, m)
        x += fw + 2 + GAP
    return bx, by, bx + rw, by + rh
