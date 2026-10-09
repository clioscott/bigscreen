"""Map geometry, raster basemaps and vector overlays.

A map *area* is a centre point plus a scale (km per LED pixel). Rendering an
area at a size (w, h) gives a Web-Mercator bbox, so half- and full-width
versions share a scale and the full-width one just shows more.

Vector cartography (coastlines, borders, roads) lives in hub/weather/carto.py.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageEnhance

from hub import paths

R = 6378137.0
MAX_LAT = 85.05112878
UA = "BigScreen/2.0 (personal LED weather display)"

# v1 downloaded ~3 GB of geodata into ~/bigscreen-server/data; reuse it in place.
GEODATA_ROOT = Path(os.environ.get("BIGSCREEN_GEODATA", Path.home() / "bigscreen-server" / "data"))
TILE_CACHE = paths.CACHE_DIR / "tiles"
BASEMAP_CACHE = paths.CACHE_DIR / "basemaps"


# ---------------------------------------------------------------------------
# projection
# ---------------------------------------------------------------------------
def to_merc(lon, lat):
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    return math.radians(lon) * R, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R


def from_merc(x, y):
    return math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


class View:
    """An area rendered at a pixel size: mercator bbox + lon/lat bbox + pixel projection."""

    def __init__(self, area: dict, width: int, height: int):
        self.area = area
        self.w, self.h = int(width), int(height)
        lat, lon = float(area["center"][0]), float(area["center"][1])
        km_per_px = float(area["km_per_px"])
        mpp = km_per_px * 1000.0 / math.cos(math.radians(lat))      # projected metres per pixel
        cx, cy = to_merc(lon, lat)
        self.mpp = mpp
        self.merc = (cx - self.w / 2 * mpp, cy - self.h / 2 * mpp, cx + self.w / 2 * mpp, cy + self.h / 2 * mpp)
        lo0, la0 = from_merc(self.merc[0], self.merc[1])
        lo1, la1 = from_merc(self.merc[2], self.merc[3])
        self.lonlat = (lo0, la0, lo1, la1)

    @property
    def key(self):
        a = self.area
        return f"{a['id']}_{self.w}x{self.h}_{float(a['km_per_px']):g}_{a['center'][0]:.4f}_{a['center'][1]:.4f}"

    def project(self, lon, lat):
        x, y = to_merc(lon, lat)
        return ((x - self.merc[0]) / self.mpp, (self.merc[3] - y) / self.mpp)

    def project_many(self, lons, lats):
        lats = np.clip(np.asarray(lats, dtype=np.float64), -MAX_LAT, MAX_LAT)
        x = np.radians(np.asarray(lons, dtype=np.float64)) * R
        y = np.log(np.tan(np.pi / 4 + np.radians(lats) / 2)) * R
        return (x - self.merc[0]) / self.mpp, (self.merc[3] - y) / self.mpp


# ---------------------------------------------------------------------------
# raster basemaps (XYZ tiles)
# ---------------------------------------------------------------------------
TILE_SOURCES = {
    "esri_imagery":  ("Satellite imagery (Esri)",
                      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"),
    "esri_dark":     ("Dark grey (Esri)",
                      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}"),
    "esri_relief":   ("Shaded relief (Esri)",
                      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Shaded_Relief/MapServer/tile/{z}/{y}/{x}"),
    "esri_terrain":  ("Terrain (Esri)",
                      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Terrain_Base/MapServer/tile/{z}/{y}/{x}"),
    "carto_dark":    ("Dark, no labels (CARTO)",
                      "https://a.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png"),
    "carto_light":   ("Light, no labels (CARTO)",
                      "https://a.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png"),
    "black":         ("Solid black", None),
}
_session = requests.Session()
_session.headers["User-Agent"] = UA


def _tile(source, z, x, y):
    path = TILE_CACHE / source / str(z) / str(x) / f"{y}.png"
    if path.exists():
        try:
            return Image.open(path).convert("RGB")
        except Exception:
            path.unlink(missing_ok=True)
    url = TILE_SOURCES[source][1].format(z=z, x=x, y=y)
    r = _session.get(url, timeout=20)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return img


def render_basemap(view: View, style: dict) -> np.ndarray:
    """(h, w, 3) uint8 basemap for this view. Cached on disk by view + style."""
    style = {"source": "esri_imagery", "brightness": 0.6, "contrast": 1.0, "saturation": 1.0, **(style or {})}
    key = hashlib.sha1(json.dumps([view.key, style], sort_keys=True).encode()).hexdigest()[:16]
    cached = BASEMAP_CACHE / f"{key}.png"
    if cached.exists():
        return np.asarray(Image.open(cached).convert("RGB"))

    if style["source"] == "black" or style["source"] not in TILE_SOURCES:
        img = Image.new("RGB", (view.w, view.h), (0, 0, 0))
    else:
        world = 2 * math.pi * R
        # finest zoom where a tile pixel is <= half an output pixel (2x supersampling), within limits
        z = max(1, min(13, math.ceil(math.log2(world / (256 * view.mpp / 2)))))
        while True:
            tpx = world / (256 * 2 ** z)                               # metres per tile pixel
            tx0 = int((view.merc[0] + world / 2) / (256 * tpx))
            tx1 = int((view.merc[2] + world / 2) / (256 * tpx))
            ty0 = int((world / 2 - view.merc[3]) / (256 * tpx))
            ty1 = int((world / 2 - view.merc[1]) / (256 * tpx))
            if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) <= 120 or z <= 1:
                break
            z -= 1
        n = 2 ** z
        mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * 256, (ty1 - ty0 + 1) * 256))
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                if 0 <= ty < n:
                    mosaic.paste(_tile(style["source"], z, tx % n, ty), ((tx - tx0) * 256, (ty - ty0) * 256))
        # crop the exact bbox out of the mosaic, then resample to the LED size
        ox = (view.merc[0] + world / 2) / tpx - tx0 * 256
        oy = (world / 2 - view.merc[3]) / tpx - ty0 * 256
        box = (ox, oy, ox + (view.merc[2] - view.merc[0]) / tpx, oy + (view.merc[3] - view.merc[1]) / tpx)
        img = mosaic.resize((view.w, view.h), Image.LANCZOS, box=box)
        if style["saturation"] != 1.0:
            img = ImageEnhance.Color(img).enhance(style["saturation"])
        if style["contrast"] != 1.0:
            img = ImageEnhance.Contrast(img).enhance(style["contrast"])
        if style["brightness"] != 1.0:
            img = ImageEnhance.Brightness(img).enhance(style["brightness"])
    BASEMAP_CACHE.mkdir(parents=True, exist_ok=True)
    img.save(cached)
    return np.asarray(img)


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def alpha_over(base: np.ndarray, rgba: np.ndarray, opacity: float = 1.0) -> np.ndarray:
    """Composite an RGBA layer over an RGB base (both uint8). Returns uint8 RGB."""
    a = rgba[..., 3:4].astype(np.float32) * (opacity / 255.0)
    out = base.astype(np.float32) * (1 - a) + rgba[..., :3].astype(np.float32) * a
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def prune_cache(max_age_days=30):
    """Drop cached basemaps/overlays not used for a month (they're keyed by settings hash)."""
    cutoff = time.time() - max_age_days * 86400
    for p in BASEMAP_CACHE.glob("*.png"):
        if p.stat().st_atime < cutoff and p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)
