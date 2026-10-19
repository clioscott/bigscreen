"""Real highway signs for the webcam captions: Wikimedia Commons' SVG for a route, rendered to a PNG the wall scales.

The pixel-art shields (assets/icons/shields, display/modules/shields.py) are redrawn per size for the LEDs; this is
the other choice, the sign itself ("BC-99.svg", "I-5.svg", "US 97.svg", "WA-543.svg", …, all public domain on
Commons). ``ensure(route, kind)`` fetches the SVG once (cache/shields/), renders it RENDER_H px tall with resvg and
writes data/shields/<slug>.png, which the data sync takes to the Pi; the Pi only scales that PNG once per size.
Routes Commons has no file for (BC 3, the Crowsnest) return None and the wall keeps the pixel art.
"""

from __future__ import annotations

import io
import re
import threading
import time

import requests

from display.modules.shields import sign_file as commons_file, sign_slug as slug
from hub import paths

RENDER_H = 192                 # px; the wall scales down from this (the tallest shield setting is 64)
UA = {"User-Agent": "BigScreen/2 (home LED wall; webcam caption highway signs; github.com/clioscott/bigscreen)"}
FILE_URL = "https://commons.wikimedia.org/wiki/Special:FilePath/{}"
RETRY_SEC = 6 * 3600           # a route Commons has no file for is asked about again after this
SIGNS_DIR = paths.DATA_DIR / "shields"
CACHE = paths.CACHE_DIR / "shields"


def png_path(route, kind=None):
    name = commons_file(route, kind)
    return SIGNS_DIR / f"{slug(name)}.png" if name else None


_fetching = threading.Lock()   # one Commons request at a time (the panel asks for a row of presets at once)


def _svg(name: str, timeout=20) -> bytes | None:
    """The SVG from Commons, cached for good (signs don't change). A 404 is remembered for RETRY_SEC; anything else
    (rate limiting, Commons down) raises and is simply tried again next time."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path, miss = CACHE / f"{slug(name)}.svg", CACHE / f"{slug(name)}.missing"
    with _fetching:
        if path.exists():
            return path.read_bytes()
        if miss.exists() and time.time() - miss.stat().st_mtime < RETRY_SEC:
            return None
        r = requests.get(FILE_URL.format(name.replace(" ", "_")), headers=UA, timeout=timeout)
        if r.status_code == 404:
            miss.touch()
            return None
        r.raise_for_status()
        if b"<svg" not in r.content:
            raise ValueError(f"{name}: not an SVG")
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return r.content


def render(svg: bytes, h=RENDER_H) -> bytes:
    """SVG → PNG bytes, h px tall (width from the sign's aspect), transparent around the sign."""
    import resvg_py
    s = svg.decode("utf-8", "replace")
    m = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', s)
    if m:                                                  # size it by height: drop the SVG's own width/height
        vw, vh = float(m.group(1)), float(m.group(2))
        s = re.sub(r'(<svg[^>]*?)\swidth="[^"]*"', r"\1", s, count=1)
        s = re.sub(r'(<svg[^>]*?)\sheight="[^"]*"', r"\1", s, count=1)
        s = s.replace("<svg", f'<svg width="{vw * h / vh:.2f}" height="{h}"', 1)
    return bytes(resvg_py.svg_to_bytes(svg_string=s))


def ensure(route, kind=None) -> "paths.Path | None":
    """data/shields/<slug>.png for the route's real sign (fetched and rendered if needed), or None."""
    out = png_path(route, kind)
    if out is None:
        return None
    if out.exists():
        return out
    svg = _svg(commons_file(route, kind))
    if svg is None:
        return None
    png = render(svg)
    from PIL import Image
    Image.open(io.BytesIO(png)).verify()
    SIGNS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(out)
    return out


def wanted(cfg) -> set:
    """(route, kind) of every webcam caption set to show a real sign."""
    cams = (((cfg.get("modules") or {}).get("webcams") or {}).get("cams") or {})
    out = set()
    for cam in cams.values():
        ov = cam.get("overlay") or {}
        sh = ov.get("shield") or {}
        if (ov.get("show") or {}).get("shield") and sh.get("style") == "real" and str(sh.get("route") or "").strip():
            out.add((str(sh["route"]).strip(), sh.get("kind") if sh.get("kind") not in (None, "auto") else None))
    return out
