"""Satellites Overhead's basemaps (display/modules/issmap.py OverheadView, overhead_base).

For each size the screen plays at, the hub draws the map's lines for exactly that view with the weather maps'
scale-aware cartography (hub/weather/carto.py): coasts and lake shores from the land/water mask, country and
province lines from the source that suits the scale, each 1 px wide and clipped to land. NASA's Black Marble city
lights are fetched for the same area at the view's own pixels (each pixel the brightest of four samples, so small
towns stay). So zoomed in or out, nothing is a scaled-up raster.

→ data/issmap/<issmap.overhead_name(view)>.bin (b"OVB1", w, h, has_lights, then h×w line classes and h×w lights)
+ .json (what it was drawn for). Drawn again only when the view changes (zoom, home, size) or RENDER_VERSION moves;
the lights are fetched again after LIGHTS_TTL. Run by hub/jobs/sats.py; the control panel's preview draws the
draft's into the cache (preview) so the Pi's copy only ever holds the live settings' maps.

With the date line in view each side is drawn on its own and the two meet exactly. A view that's too wide (past
MAX_KM_PX, or Mercator's ±85° in it) gets none: the Pi draws the Natural Earth 1:110m lines there, which suit that
scale."""

from __future__ import annotations

import io
import math
import os
import threading
import time
from pathlib import Path

import numpy as np

from hub import paths

RENDER_VERSION = 1            # bump to draw every basemap again after a change to the drawing
MAX_KM_PX = 80.0              # wider than this the Pi's Natural Earth 1:110m lines do
MIN_PX = 3.0                  # islands and lakes smaller than this many px are left out (carto)
LIGHTS_WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
LIGHTS_TTL = 90 * 86400
LIGHTS_RETRY = 86400          # a failed lights fetch is tried again after this
MAX_WMS = 2048                # px either way in one lights request
DRAFTS = paths.CACHE_DIR / "overhead"
KEEP_DRAFTS = 3600            # s a preview's basemap is kept
BUDGET = 120.0                # s: no new basemap is started after this long in one run (the sats job has 180)
UA = {"User-Agent": "BigScreen/2 (home LED wall; space map)"}
_lock = threading.Lock()
# (line class: issmap.OV_*) drawn by carto in these colours, read back from its overlay's red channel
CLASSES = {"coastline": 1, "country": 2, "province": 3, "lakes": 4}


def zones(cfg) -> dict:
    """{shape: [w, h]} of each place a screen can play (display/main.py Screen: ticker 44 px + 1, bottom strip 17)."""
    L = (cfg or {}).get("layout") or {}
    w, h = L.get("width", 448), L.get("height", 192)
    main_h = h - 17 - (L.get("top_bar_height", 44) + 1)
    cal_w = int(w * L.get("calendar_split_ratio", 0.66))
    return {"left": [cal_w, main_h], "right": [w - cal_w - 1, main_h], "full": [w, main_h], "tall": [w, h - 17],
            "wall": [w, h]}


def home(settings, cfg):
    """(lat, lon) Satellites Overhead is centred on: its own home, else the weather alerts' home."""
    from hub.bc import common
    h = (settings or {}).get("home")
    if isinstance(h, list) and len(h) == 2:
        return float(h[0]), float(h[1])
    lon, lat = common.home(cfg)
    return round(float(lat), 3), round(float(lon), 3)


def view_for(settings, home_, w, h):
    """The OverheadView the wall draws for these settings (modules.sats_overhead, settings_of'd) at w×h."""
    from display.modules import iss_panel, issmap
    _mp, view = iss_panel.overhead_mapset(settings, home_, w, h)
    return issmap.OverheadView(w, h, **view["_overhead"])


def in_use(cfg) -> bool:
    mods = (cfg or {}).get("modules") or {}
    lc = mods.get("layout_cycle") or {}
    return bool(mods.get("sats_overhead")) or any(
        str((e or {}).get("panel", "")).startswith("sats_overhead")
        for side in ("left", "right") for e in lc.get(side) or [] if isinstance(e, dict))


def live_views(cfg) -> list:
    """The views of the live settings, one per size."""
    from display.modules import iss_panel
    settings = iss_panel.settings_of((cfg or {}).get("modules") or {}, iss_panel.OVERHEAD)
    hm = home(settings, cfg)
    out, seen = [], set()
    for w, h in zones(cfg).values():
        if (w, h) not in seen:
            seen.add((w, h))
            out.append(view_for(settings, hm, w, h))
    return out


def _lat_at(v, y):
    return math.degrees(2 * math.atan(math.exp(v.m0 + ((v.h - 1) / 2 - y) / v.k)) - math.pi / 2)


def parts(v) -> list:
    """The view's columns split where the date line crosses them (one part, or two): [{"i0", "i1" (columns),
    "lon" (the longitude at their middle), "west", "east" (their outer edges)}], each drawable on its own."""
    out, i = [], 0
    while i < v.w:
        turn = (v.cx0 + i) // v.W
        j = min(v.w, (turn + 1) * v.W - v.cx0)             # (the first column round the next turn of the world)
        a, b = v.cx0 + i - turn * v.W, v.cx0 + j - 1 - turn * v.W
        deg = lambda x: x / v.W * 360.0 - 180.0
        out.append({"i0": i, "i1": j, "lon": deg((a + b) / 2), "west": deg(a - 0.5), "east": deg(b + 0.5)})
        i = j
    return out


def edges(v):
    """(west, south, east, north) of the view's outer pixel edges (east past 180 when the date line is in view)."""
    ps = parts(v)
    return ps[0]["west"], _lat_at(v, v.h - 0.5), ps[0]["west"] + v.w / v.W * 360.0, _lat_at(v, -0.5)


def drawable(v) -> bool:
    _w, s, _e, n = edges(v)
    return v.km_per_px <= MAX_KM_PX and -85.0 < s and n < 85.0


def lines(v) -> np.ndarray:
    """(h, w) uint8 line classes (issmap.OV_*: coast, country, province, lake shore; 0 none) for the view: each part
    (parts) drawn as a view of its own on the same pixel grid, so they meet exactly at the date line."""
    from hub.weather import carto
    from hub.weather.geo import View
    ov = {k: {"enabled": False} for k in carto.LAYERS}
    for k, c in CLASSES.items():
        ov[k] = {"enabled": True, "color": f"#{c:02x}0000", "opacity": 1.0}
    out = np.zeros((v.h, v.w), np.uint8)
    for p in parts(v):
        gv = View({"id": "overhead", "center": [v.lat0, p["lon"]], "km_per_px": v.km_per_px}, p["i1"] - p["i0"], v.h)
        rgba = carto.render_overlay(gv, ov, MIN_PX, group="over")
        out[:, p["i0"]:p["i1"]] = np.where(rgba[..., 3] > 0, rgba[..., 0], 0)
    return out


def lights(v, timeout=30) -> np.ndarray:
    """(h, w) uint8 Black Marble brightness at the view's pixels: fetched for each part's area (EPSG:4326) at about
    twice its resolution, each pixel the brightest of four samples inside it."""
    import requests
    from PIL import Image
    _w, south, _e, north = edges(v)
    finest = math.cos(math.radians(max(abs(south), abs(north)))) / v.k     # (the smallest pixel's height, radians)
    H = int(min(MAX_WMS, max(8, math.ceil(math.radians(north - south) / (finest / 2)))))
    out = np.zeros((v.h, v.w), np.uint8)
    for p in parts(v):
        n, west, east = p["i1"] - p["i0"], p["west"], p["east"]
        W = int(min(MAX_WMS, max(8, 2 * n)))
        r = requests.get(LIGHTS_WMS, headers=UA, timeout=timeout, params={
            "SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.3.0", "LAYERS": "VIIRS_Black_Marble", "STYLES": "",
            "CRS": "EPSG:4326", "BBOX": f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}", "WIDTH": W, "HEIGHT": H,
            "FORMAT": "image/png"})
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("image/"):
            raise ValueError(f"not an image: {r.text[:120]}")
        img = np.asarray(Image.open(io.BytesIO(r.content)).convert("L"), np.uint8)
        gh, gw = img.shape
        part = out[:, p["i0"]:p["i1"]]
        for dy in (-0.25, 0.25):
            lat = np.array([_lat_at(v, y + dy) for y in range(v.h)])
            rows = ((north - lat) / (north - south) * gh).astype(int).clip(0, gh - 1)
            for dx in (-0.25, 0.25):
                cols = ((np.arange(n) + 0.5 + dx) / n * gw).astype(int).clip(0, gw - 1)
                np.maximum(part, img[rows][:, cols], out=part)
    return out


def _write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def ensure(v, out_dir, with_lights=True, now=None, lights_timeout=30) -> str:
    """The view's basemap in out_dir, drawn if it's missing or out of date: "kept", "drawn" (with how the lights
    went), "too wide" (none: the Pi's own lines), or raises."""
    from hub.util import atomic_write_json, read_json
    from display.modules import issmap
    now = now or time.time()
    if not drawable(v):
        return "too wide"
    out_dir = Path(out_dir)
    name = issmap.overhead_name(v)
    meta = read_json(out_dir / f"{name}.json", None) or {}
    stale_lights = with_lights and (
        (meta.get("lights_at") and now - meta["lights_at"] > LIGHTS_TTL)
        or (not meta.get("lights_at") and now - float(meta.get("lights_tried") or 0) > LIGHTS_RETRY))
    if meta.get("version") == RENDER_VERSION and (out_dir / f"{name}.bin").exists() and not stale_lights:
        return "kept"
    with _lock:
        ln = lines(v)
        lt, lights_at, err = None, None, None
        if with_lights:
            try:
                lt, lights_at = lights(v, lights_timeout), now
            except Exception as e:                      # (the lines alone: the Pi uses the world's lights there)
                err = str(e)[:160]
        head = b"OVB1" + int(v.w).to_bytes(2, "big") + int(v.h).to_bytes(2, "big") + bytes([1 if lt is not None else 0])
        _write(out_dir / f"{name}.bin", head + ln.tobytes() + (lt.tobytes() if lt is not None else b""))
        atomic_write_json(out_dir / f"{name}.json", {
            "version": RENDER_VERSION, "w": v.w, "h": v.h, "W": v.W, "cx0": v.cx0, "lat": v.lat0,
            "lon": v.centre_lon, "km_per_px": v.km_per_px, "at": now, "lights_at": lights_at,
            "lights_tried": now if with_lights else meta.get("lights_tried"), "lights_error": err})
    return "drawn" + ("" if not with_lights else ", lights" if lt is not None else f", no lights ({err})")


def _prune(d: Path, keep: set, older_than=None):
    now = time.time()
    for p in d.glob("ov_*.*"):
        if p.stem in keep:
            continue
        if older_than is not None and now - p.stat().st_mtime < older_than:
            continue
        try:
            p.unlink()
        except OSError:
            pass


def run(cfg) -> dict:
    """The live settings' basemaps into data/issmap (synced to the Pi); the rest there (an old zoom) go."""
    from display.modules import issmap
    if not in_use(cfg):
        return {"skipped": "Satellites Overhead isn't set up"}
    t0 = time.time()
    d = paths.DATA_DIR / "issmap"
    out, keep = {}, set()
    for v in live_views(cfg):
        keep.add(issmap.overhead_name(v))
        if time.time() - t0 > BUDGET:
            out[f"{v.w}x{v.h}"] = "next run (out of time)"
            continue
        try:
            out[f"{v.w}x{v.h}"] = ensure(v, d)
        except Exception as e:
            out[f"{v.w}x{v.h}"] = f"failed: {str(e)[:160]}"
    _prune(d, keep)
    if DRAFTS.exists():
        _prune(DRAFTS, set(), KEEP_DRAFTS)
    return out


def preview(v) -> str:
    """The basemap for a draft's view (the control panel's preview), drawn into the cache unless the live one is it.
    The hub's renderer looks there too (issmap.OVERHEAD_DIRS)."""
    from display.modules import issmap
    if str(DRAFTS) not in issmap.OVERHEAD_DIRS:
        issmap.OVERHEAD_DIRS.append(str(DRAFTS))
    if (paths.DATA_DIR / "issmap" / f"{issmap.overhead_name(v)}.bin").exists():
        return "live"
    return ensure(v, DRAFTS, lights_timeout=15)
