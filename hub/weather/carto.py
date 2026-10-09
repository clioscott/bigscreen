"""Scale-aware vector cartography for LED-resolution maps.

The v1 overlays drew every vertex of one fixed dataset at every zoom level, so
detail far below a pixel piled up into thick, jagged lines (Vancouver Island's
coast looked bolder than intended). Here every layer is tailored to the view's
scale (km per LED pixel):

* **Coastlines come from a land/water mask, not lines.** Water polygons are
  rasterised at 4x, downsampled to LED pixels, specks smaller than a few pixels
  are removed, and the coast is the 1-px edge between land and water. It can't
  be thicker than one pixel, and islands too small to see simply vanish.
* **Each layer picks a source resolution for the scale** (OSM simplified vs
  full water polygons; Natural Earth 1:50M vs 1:10M vs OSM boundaries).
* **Lines are clipped and simplified to the pixel grid** before drawing, and
  every layer has a visible scale range (municipal boundaries and minor roads
  only appear when zoomed in far enough to read them).
* **Admin boundaries are masked to land**, so OSM's maritime boundary segments
  don't draw lines across the water.
"""

from __future__ import annotations

import hashlib
import json
import struct
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
from shapely.geometry import LineString, box
from shapely import simplify as shp_simplify

from hub.weather.geo import BASEMAP_CACHE, GEODATA_ROOT, View, _hex, to_merc

SS = 4                      # supersampling factor for the land/water mask
NE_DIR = GEODATA_ROOT / "borders"
NE_VEC = GEODATA_ROOT / "geodata" / "vectors"
OSM_DIR = GEODATA_ROOT / "geodata" / "osm"
BCGW_DIR = GEODATA_ROOT / "geodata" / "bcgw"
WATER_FULL = GEODATA_ROOT / "osm_water_cache" / "water-polygons-split-3857" / "water_polygons.shp"
WATER_SIMPLE = GEODATA_ROOT / "osm_water_cache" / "simplified-water-polygons-split-3857" / "simplified_water_polygons.shp"


# ---------------------------------------------------------------------------
# Layer catalogue. min/max_km_px = the scale range where the layer is drawn
# (km per LED pixel; smaller = more zoomed in). `sources` lists
# (finest scale it is used down to, loader) — the first whose threshold the
# view's scale is >= wins, so coarse data is used for wide views.
# ---------------------------------------------------------------------------
LAYERS = {
    "coastline": {"label": "Coastline (from land/water mask)", "kind": "coast",
                  "color": "#5a8fd6", "opacity": 0.9, "width": 1, "min_km_px": 0.0, "max_km_px": 1e9},
    "lakes":     {"label": "Lake shores", "kind": "lakes",
                  "color": "#4f7fc4", "opacity": 0.8, "width": 1, "min_km_px": 0.0, "max_km_px": 8.0},
    "country":   {"label": "Country borders", "kind": "line",
                  "color": "#c8c0e8", "opacity": 0.9, "width": 1, "min_km_px": 0.0, "max_km_px": 1e9,
                  "sources": [(4.0, ("ne", "ne_50m_admin_0_boundary_lines_land")),
                              (0.4, ("ne", "ne_10m_admin_0_boundary_lines_land")),
                              (0.0, ("osm_admin", "2"))]},
    "province":  {"label": "Province / state borders", "kind": "line",
                  "color": "#8f84b8", "opacity": 0.85, "width": 1, "min_km_px": 0.0, "max_km_px": 40.0,
                  "sources": [(4.0, ("ne", "ne_50m_admin_1_states_provinces_lines")),
                              (0.4, ("ne", "ne_10m_admin_1_states_provinces_lines")),
                              (0.0, ("osm_admin", "4"))]},
    "regional":  {"label": "Regional districts / counties", "kind": "line",
                  "color": "#6f6a92", "opacity": 0.7, "width": 1, "min_km_px": 0.0, "max_km_px": 1.5,
                  "sources": [(0.0, ("osm_admin", "6"))]},
    "municipal": {"label": "Municipal boundaries", "kind": "line",
                  "color": "#5c5878", "opacity": 0.65, "width": 1, "min_km_px": 0.0, "max_km_px": 0.5,
                  "sources": [(0.0, ("osm_admin", "8"))]},
    "rivers":    {"label": "Major rivers", "kind": "line",
                  "color": "#3f6fb8", "opacity": 0.7, "width": 1, "min_km_px": 0.4, "max_km_px": 12.0,
                  "sources": [(0.0, ("ne", "ne_10m_rivers_lake_centerlines"))]},
    "motorways": {"label": "Motorways", "kind": "line", "land_only": False,
                  "color": "#d29a3a", "opacity": 0.75, "width": 1, "min_km_px": 0.0, "max_km_px": 3.0,
                  "sources": [(0.0, ("osm_roads", "motorway"))]},
    "trunk":     {"label": "Trunk roads", "kind": "line", "land_only": False,
                  "color": "#b8873a", "opacity": 0.65, "width": 1, "min_km_px": 0.0, "max_km_px": 1.5,
                  "sources": [(0.0, ("osm_roads", "trunk"))]},
    "primary":   {"label": "Primary roads", "kind": "line", "land_only": False,
                  "color": "#9a8a5a", "opacity": 0.6, "width": 1, "min_km_px": 0.0, "max_km_px": 0.6,
                  "sources": [(0.0, ("osm_roads", "primary"))]},
    "secondary": {"label": "Secondary roads", "kind": "line", "land_only": False,
                  "color": "#7a7050", "opacity": 0.55, "width": 1, "min_km_px": 0.0, "max_km_px": 0.3,
                  "sources": [(0.0, ("osm_roads", "secondary"))]},
}
DEFAULT_LAYER_ORDER = ["lakes", "rivers", "secondary", "primary", "trunk", "motorways",
                       "municipal", "regional", "province", "country", "coastline"]


def layer_settings(overrides: dict | None):
    """Merge per-area overrides ({key: {enabled, color, opacity, width, min_km_px, max_km_px}}) onto defaults."""
    out = []
    overrides = overrides or {}
    order = overrides.get("_order") or DEFAULT_LAYER_ORDER
    for key in order:
        if key not in LAYERS:
            continue
        base = {k: v for k, v in LAYERS[key].items() if k not in ("sources", "label", "kind")}
        base["enabled"] = key in ("coastline", "lakes", "country", "province")
        base.update(overrides.get(key) or {})
        base["key"] = key
        out.append(base)
    return out


def describe_layers(view_km_px: float, overrides=None):
    """For the control panel: each layer, whether it's in range at this scale, and the data source it would use."""
    rows = []
    for s in layer_settings(overrides):
        meta = LAYERS[s["key"]]
        in_range = s["min_km_px"] <= view_km_px <= s["max_km_px"]
        src = _pick_source(meta, view_km_px)
        rows.append({**s, "label": meta["label"], "in_range": in_range,
                     "source": src[1] if src else ("OSM water polygons" if meta["kind"] in ("coast", "lakes") else None)})
    return rows


def _pick_source(meta, km_px):
    for threshold, src in meta.get("sources", []):
        if km_px >= threshold:
            return src
    return None


# ---------------------------------------------------------------------------
# Land / water mask
# ---------------------------------------------------------------------------
@lru_cache(maxsize=4)
def _shp_bbox_index(shp_path: str):
    """Per-record bounding boxes of a (large) shapefile, read straight from the
    .shx/.shp headers and cached as .npy so we only ever parse records near the view."""
    shp = Path(shp_path)
    cache = BASEMAP_CACHE / f"idx_{hashlib.sha1(shp_path.encode()).hexdigest()[:10]}_{int(shp.stat().st_mtime)}.npy"
    if cache.exists():
        return np.load(cache)
    shx = np.fromfile(shp.with_suffix(".shx"), dtype=">i4", offset=100).reshape(-1, 2)
    boxes = np.zeros((len(shx), 4), dtype=np.float64)
    with open(shp, "rb") as f:
        for i, (off, _ln) in enumerate(shx):
            f.seek(off * 2 + 8 + 4)                              # record header + shape type
            boxes[i] = struct.unpack("<4d", f.read(32))
    BASEMAP_CACHE.mkdir(parents=True, exist_ok=True)
    np.save(cache, boxes)
    return boxes


def _water_polys_in(view: View, shp_path: Path):
    """Yield list-of-rings (pixel coords at SS scale) for water polygons touching the view (EPSG:3857 source)."""
    import shapefile
    idx = _shp_bbox_index(str(shp_path))
    x0, y0, x1, y1 = view.merc
    hit = np.flatnonzero((idx[:, 0] <= x1) & (idx[:, 2] >= x0) & (idx[:, 1] <= y1) & (idx[:, 3] >= y0))
    sf = shapefile.Reader(str(shp_path))
    k = SS / view.mpp
    for i in hit:
        shp = sf.shape(int(i))
        pts = np.asarray(shp.points, dtype=np.float64)
        parts = list(shp.parts) + [len(pts)]
        rings = []
        for a, b in zip(parts[:-1], parts[1:]):
            r = pts[a:b]
            if len(r) >= 3:
                rings.append(np.column_stack(((r[:, 0] - x0) * k, (y1 - r[:, 1]) * k)))
        yield rings


def _ring_is_outer(r):
    # shapefile convention: outer rings clockwise (in y-up coords). We flipped y, so clockwise → positive area.
    x, y = r[:, 0], r[:, 1]
    return (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) > 0


def land_mask(view: View, min_feature_px: float = 3.0) -> np.ndarray:
    """bool (h, w): True = land."""
    return water_masks(view, min_feature_px)[0]


def water_masks(view: View, min_feature_px: float = 3.0):
    """(land, ocean, lakes) bool masks at LED resolution. Cached per view."""
    key = hashlib.sha1(json.dumps([view.key, min_feature_px, "v2"]).encode()).hexdigest()[:16]
    cached = BASEMAP_CACHE / f"water_{key}.png"
    if cached.exists():
        a = np.asarray(Image.open(cached).convert("RGB"))
        return a[..., 0] > 0, a[..., 1] > 0, a[..., 2] > 0
    # Simplified OSM water polygons are plenty for wide views; full detail below ~1.5 km/px.
    km_px = float(view.area["km_per_px"])
    src = WATER_SIMPLE if km_px >= 1.5 or not WATER_FULL.exists() else WATER_FULL
    W, H = view.w * SS, view.h * SS
    img = Image.new("L", (W, H), 0)                       # 255 = ocean
    draw = ImageDraw.Draw(img)
    for rings in _water_polys_in(view, src):
        for r in rings:
            if _ring_is_outer(r):
                draw.polygon(r.ravel().tolist(), fill=255)
        for r in rings:
            if not _ring_is_outer(r):
                draw.polygon(r.ravel().tolist(), fill=0)
    # Inland lakes aren't in the OSM ocean polygons: Natural Earth lakes, size-filtered below.
    limg = Image.new("L", (W, H), 0)
    _draw_lakes(ImageDraw.Draw(limg), view)
    ocean = np.asarray(img.resize((view.w, view.h), Image.BOX), dtype=np.float32) >= 127.5
    lakes = (np.asarray(limg.resize((view.w, view.h), Image.BOX), dtype=np.float32) >= 127.5) & ~ocean
    # Islands / lakes / inlets smaller than min_feature_px LED pixels would only be noise at this scale.
    land = ~(ocean | lakes)
    land = _drop_small(land, min_feature_px)
    lakes = _drop_small(lakes & ~land, min_feature_px)
    land = ~(ocean | lakes)
    land = ~_drop_small(~land, min_feature_px)
    ocean &= ~land
    lakes &= ~land
    BASEMAP_CACHE.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.dstack([land, ocean, lakes]).astype(np.uint8) * 255, "RGB").save(cached)
    return land, ocean, lakes


def _drop_small(mask, min_px):
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, index=np.arange(1, n + 1))
    keep = np.zeros(n + 1, dtype=bool)
    keep[1:] = sizes >= min_px
    return keep[lab]


def _draw_lakes(draw, view):
    shp = NE_DIR / "ne_10m_lakes.shp"
    if not shp.exists():
        return
    import shapefile
    sf = shapefile.Reader(str(shp))
    lo0, la0, lo1, la1 = view.lonlat
    for s in sf.iterShapes():
        bx = s.bbox
        if bx[2] < lo0 or bx[0] > lo1 or bx[3] < la0 or bx[1] > la1:
            continue
        pts = np.asarray(s.points, dtype=np.float64)
        parts = list(s.parts) + [len(pts)]
        for j, (a, b) in enumerate(zip(parts[:-1], parts[1:])):
            px, py = view.project_many(pts[a:b, 0], pts[a:b, 1])
            ring = np.column_stack((px * SS, py * SS))
            if len(ring) >= 3:
                draw.polygon(ring.ravel().tolist(), fill=255 if j == 0 else 0)


def edge_of(mask: np.ndarray) -> np.ndarray:
    """1-px boundary on the True side of a bool mask (4-neighbour)."""
    m = mask
    pad = np.pad(m, 1, mode="edge")
    neighbour_false = (~pad[:-2, 1:-1]) | (~pad[2:, 1:-1]) | (~pad[1:-1, :-2]) | (~pad[1:-1, 2:])
    return m & neighbour_false


# ---------------------------------------------------------------------------
# Line layers
# ---------------------------------------------------------------------------
@lru_cache(maxsize=16)
def _ne_lines(name):
    import shapefile
    for d in (NE_DIR, NE_VEC):
        p = d / f"{name}.shp"
        if p.exists():
            break
    else:
        return []
    sf = shapefile.Reader(str(p))
    out = []
    for s in sf.iterShapes():
        pts = np.asarray(s.points, dtype=np.float64)
        parts = list(s.parts) + [len(pts)]
        for a, b in zip(parts[:-1], parts[1:]):
            if b - a >= 2:
                out.append((s.bbox, pts[a:b, :2]))
    return out


@lru_cache(maxsize=16)
def _osm_lines(kind, value):
    """OSM GeoJSON lines (admin boundaries by level, or roads by class), de-duplicated across region files."""
    pattern = "*_boundaries.geojson" if kind == "osm_admin" else "*_roads.geojson"
    want = {value} if kind == "osm_admin" else {value, f"{value}_link"}
    seen, out = set(), []
    for f in sorted(OSM_DIR.glob(pattern)):
        for feat in json.loads(f.read_text()).get("features", []):
            p = feat.get("properties") or {}
            if (p.get("admin_level") if kind == "osm_admin" else p.get("highway")) not in want:
                continue
            g = feat.get("geometry") or {}
            parts = [g["coordinates"]] if g.get("type") == "LineString" else (g.get("coordinates") or [])
            for part in parts:
                if len(part) < 2:
                    continue
                sig = (round(part[0][0], 5), round(part[0][1], 5), round(part[-1][0], 5), round(part[-1][1], 5), len(part))
                if sig in seen:
                    continue
                seen.add(sig)
                a = np.asarray(part, dtype=np.float64)[:, :2]
                out.append(((a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()), a))
    return out


def _lines_for(src):
    kind, value = src
    return _ne_lines(value) if kind == "ne" else _osm_lines(kind, value)


def _draw_lines(view, lines, width):
    """Clip + simplify (0.5 px) + draw. Returns bool (h, w) mask."""
    img = Image.new("1", (view.w, view.h), 0)
    draw = ImageDraw.Draw(img)
    lo0, la0, lo1, la1 = view.lonlat
    clip = box(-2, -2, view.w + 2, view.h + 2)
    for bx, pts in lines:
        if bx[2] < lo0 or bx[0] > lo1 or bx[3] < la0 or bx[1] > la1:
            continue
        px, py = view.project_many(pts[:, 0], pts[:, 1])
        geom = LineString(np.column_stack((px, py))).intersection(clip)
        if geom.is_empty:
            continue
        geom = shp_simplify(geom, 0.5, preserve_topology=False)
        for part in getattr(geom, "geoms", [geom]):
            if part.geom_type == "LineString" and part.length >= 1:
                draw.line([(round(x), round(y)) for x, y in part.coords], fill=1, width=width)
    return np.array(img, dtype=bool)      # writable copy (PIL arrays are read-only)


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------
def render_overlay(view: View, overrides: dict | None = None, min_feature_px: float = 3.0) -> np.ndarray:
    """RGBA (h, w, 4) uint8 with every enabled, in-range layer for this view. Cached."""
    layers = [s for s in layer_settings(overrides) if s["enabled"]]
    km_px = float(view.area["km_per_px"])
    key = hashlib.sha1(json.dumps([view.key, layers, min_feature_px, "v1"], sort_keys=True).encode()).hexdigest()[:16]
    cached = BASEMAP_CACHE / f"carto_{key}.png"
    if cached.exists():
        return np.asarray(Image.open(cached).convert("RGBA"))

    land, ocean, lakes = water_masks(view, min_feature_px)
    shore = edge_of(land)
    out = np.zeros((view.h, view.w, 4), dtype=np.uint8)
    for s in layers:
        if not (s["min_km_px"] <= km_px <= s["max_km_px"]):
            continue
        meta = LAYERS[s["key"]]
        if meta["kind"] == "coast":
            m = shore & ndimage.binary_dilation(ocean)          # land pixels touching the sea
        elif meta["kind"] == "lakes":
            m = shore & ndimage.binary_dilation(lakes) & ~ndimage.binary_dilation(ocean)
        else:
            src = _pick_source(meta, km_px)
            if not src:
                continue
            m = _draw_lines(view, _lines_for(src), int(s.get("width", 1)))
            if meta.get("land_only", True):
                m &= ndimage.binary_dilation(land, iterations=1)     # no boundary lines across open water
        rgb = np.array(_hex(s["color"]), dtype=np.uint8)
        a = int(255 * float(s.get("opacity", 1.0)))
        out[m, :3] = rgb
        out[m, 3] = a
    BASEMAP_CACHE.mkdir(parents=True, exist_ok=True)
    Image.fromarray(out, "RGBA").save(cached)
    return out


def tint_water(base: np.ndarray, view: View, water_rgb=None, land_rgb=None, strength=1.0, min_feature_px=3.0):
    """Optional stylised fill: replace/tint water and land with flat colours using the same mask (crisp shores)."""
    land = land_mask(view, min_feature_px)
    out = base.astype(np.float32)
    for sel, rgb in ((~land, water_rgb), (land, land_rgb)):
        if rgb:
            c = np.array(_hex(rgb), dtype=np.float32)
            out[sel] = out[sel] * (1 - strength) + c * strength
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)
