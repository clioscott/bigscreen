"""Scale-aware vector cartography for LED-resolution maps.

The v1 overlays drew every vertex of one fixed dataset at every zoom level, so
detail far below a pixel piled up into thick, jagged lines (Vancouver Island's
coast looked bolder than intended). Here every layer is tailored to the view's
scale (km per LED pixel):

* **Shorelines come from a land/water mask, not lines.** Ocean (OSM coastline
  water polygons), lakes and river areas (OSM inland water polygons, Natural
  Earth lakes) are rasterised at 4x and downsampled to LED pixels. Features
  smaller than a few pixels are removed, and each shoreline — coast, lake
  shore, river bank — is the 1-px edge between land and that kind of water. It
  can't be thicker than one pixel, and detail too small to see simply vanishes.
* **Major channels survive the downsample.** A strait narrower than a pixel
  (Johnstone Strait, the Discovery Passages at 6 km/px) would otherwise vanish
  and weld islands to the mainland. Sea and lakes are also rasterised at
  ~250 m, skeletonised, and any channel the LED-res mask lost is kept as a
  4-connected 1-px line of water if it separates a sizeable landmass or runs
  at least ~6 pixels. Archipelago gaps, short bays and small lakes stay land.
* **Each layer picks a source resolution for the scale** (OSM simplified vs
  full water polygons; Natural Earth 1:50M vs 1:10M vs OSM boundaries; Natural
  Earth vs OSM rivers).
* **Lines are clipped and simplified to the pixel grid** before drawing, and
  every layer has a visible scale range (municipal boundaries and minor roads
  only appear when zoomed in far enough to read them).
* **Boundaries, rivers and roads are clipped to land pixels**, so OSM's
  maritime boundary segments never draw across water.

Big GeoJSON sources (OSM extracts are up to 80 MB) are parsed once into compact
numpy packs under cache/geo/, so neither the maps job nor the hub holds raw
GeoJSON in memory.
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

from hub.weather.geo import BASEMAP_CACHE, GEODATA_ROOT, View, _hex

SS = 4                      # supersampling factor for the land/water mask
NE_DIR = GEODATA_ROOT / "borders"
NE_VEC = GEODATA_ROOT / "geodata" / "vectors"
OSM_DIR = GEODATA_ROOT / "geodata" / "osm"
WATER_FULL = GEODATA_ROOT / "osm_water_cache" / "water-polygons-split-3857" / "water_polygons.shp"
WATER_SIMPLE = GEODATA_ROOT / "osm_water_cache" / "simplified-water-polygons-split-3857" / "simplified_water_polygons.shp"
GEO_CACHE = BASEMAP_CACHE.parent / "geo"
MASK_VERSION = "v10"
CHANNEL_FINE_KM = 0.25      # resolution used to find straits/inlets narrower than an LED pixel
CHANNEL_FINE_MAX_W = 8000   # cap on that raster's width (px)
CHANNEL_MIN_PX = 6          # a lost channel/inlet/narrow lake is kept if it runs at least this many LED px …
CHANNEL_ISLAND_PX = 30      # … or if it cuts off a landmass of at least this many LED px (Vancouver Island)

# OSM `water=*` values that are rivers rather than lakes
RIVER_WATER = {"river", "canal", "stream", "riverbank", "rapids", "oxbow", "ditch", "tidal_channel"}


# ---------------------------------------------------------------------------
# Layer catalogue. min/max_km_px = the scale range where the layer is drawn
# (km per LED pixel; smaller = more zoomed in). `sources` lists
# (coarsest scale it's used from, source) — the first whose threshold the
# view's scale is >= wins, so coarse data is used for wide views.
# ---------------------------------------------------------------------------
LAYERS = {
    "coastline":   {"label": "Coastline", "kind": "coast",
                    "color": "#5a8fd6", "opacity": 0.9, "width": 1, "min_km_px": 0.0, "max_km_px": 1e9},
    "lakes":       {"label": "Lake shores", "kind": "lake_shore",
                    "color": "#4f7fc4", "opacity": 0.85, "width": 1, "min_km_px": 0.0, "max_km_px": 8.0},
    # Rivers are either/or by zoom: shorelines (from river-area polygons in the water mask) up to
    # 1.5 km/px, where wide rivers are at least a couple of pixels across; 1-px centre lines beyond that.
    "river_banks": {"label": "River shorelines (zoomed in)", "kind": "river_bank",
                    "color": "#4f7fc4", "opacity": 0.85, "width": 1, "min_km_px": 0.0, "max_km_px": 1.5},
    "rivers":      {"label": "River centre lines (zoomed out)", "kind": "line",
                    "color": "#3f6fb8", "opacity": 0.8, "width": 1, "min_km_px": 1.5, "max_km_px": 12.0,
                    "sources": [(2.5, ("ne", "ne_10m_rivers_lake_centerlines")),
                                (0.0, ("osm_waterway", "river"))]},
    "country":     {"label": "Country borders", "kind": "line",
                    "color": "#c8c0e8", "opacity": 0.9, "width": 1, "min_km_px": 0.0, "max_km_px": 1e9,
                    "sources": [(4.0, ("ne", "ne_50m_admin_0_boundary_lines_land")),
                                (0.4, ("ne", "ne_10m_admin_0_boundary_lines_land")),
                                (0.0, ("osm_admin", "2"))]},
    "province":    {"label": "Province / state borders", "kind": "line",
                    "color": "#8f84b8", "opacity": 0.85, "width": 1, "min_km_px": 0.0, "max_km_px": 40.0,
                    "sources": [(4.0, ("ne", "ne_50m_admin_1_states_provinces_lines")),
                                (0.4, ("ne", "ne_10m_admin_1_states_provinces_lines")),
                                (0.0, ("osm_admin", "4"))]},
    "regional":    {"label": "Regional districts / counties", "kind": "line",
                    "color": "#6f6a92", "opacity": 0.7, "width": 1, "min_km_px": 0.0, "max_km_px": 1.5,
                    "sources": [(0.0, ("osm_admin", "6"))]},
    "municipal":   {"label": "City / municipal boundaries", "kind": "line",
                    "color": "#6e6e6e", "opacity": 1.0, "width": 1, "min_km_px": 0.0, "max_km_px": 0.6,
                    "sources": [(0.0, ("osm_admin", "8"))]},
    # Highways sit ABOVE the basemap but BELOW animated data (radar, satellite): "under": True.
    "major_highways": {"label": "Major highways (motorways, trunk routes)", "kind": "line", "under": True,
                       "color": "#e39b3a", "opacity": 0.95, "width": 1, "min_km_px": 0.0, "max_km_px": 8.0,
                       "sources": [(2.0, ("ne_roads", "major")),
                                   (0.0, ("osm_roads", "motorway,trunk"))]},
    "minor_highways": {"label": "Minor highways (primary routes)", "kind": "line", "under": True,
                       "color": "#d9cfa6", "opacity": 0.7, "width": 1, "min_km_px": 0.0, "max_km_px": 1.5,
                       "sources": [(0.0, ("osm_roads", "primary"))]},
}
DEFAULT_LAYER_ORDER = ["minor_highways", "major_highways", "rivers", "river_banks", "lakes",
                       "municipal", "regional", "province", "country", "coastline"]
DEFAULT_ON = {"coastline", "lakes", "river_banks", "rivers", "country", "province", "major_highways", "minor_highways"}


def layer_settings(overrides: dict | None):
    """Merge per-area overrides ({key: {enabled, color, opacity, width, min_km_px, max_km_px}}) onto defaults."""
    out = []
    overrides = overrides or {}
    order = overrides.get("_order") or DEFAULT_LAYER_ORDER
    order = list(order) + [k for k in DEFAULT_LAYER_ORDER if k not in order]
    for key in order:
        if key not in LAYERS:
            continue
        base = {k: v for k, v in LAYERS[key].items() if k not in ("sources", "label", "kind")}
        base["enabled"] = key in DEFAULT_ON
        base.update(overrides.get(key) or {})
        base["key"] = key
        out.append(base)
    return out


SOURCE_LABELS = {"ne": "Natural Earth", "ne_roads": "Natural Earth roads", "osm_admin": "OSM admin level",
                 "osm_roads": "OSM roads", "osm_waterway": "OSM waterways"}


def describe_layers(view_km_px: float, overrides=None):
    """For the control panel: each layer, whether it's in range at this scale, and the data it would use."""
    rows = []
    for s in layer_settings(overrides):
        meta = LAYERS[s["key"]]
        in_range = s["min_km_px"] <= view_km_px <= s["max_km_px"]
        src = _pick_source(meta, view_km_px)
        if src:
            source = f"{SOURCE_LABELS.get(src[0], src[0])}: {src[1]}"
        elif meta["kind"] == "coast":
            source = "OSM coastline (simplified)" if view_km_px >= 1.5 else "OSM coastline (full detail)"
        else:
            source = "OSM inland water + Natural Earth lakes"
        rows.append({**s, "label": meta["label"], "in_range": in_range, "source": source})
    return rows


def _pick_source(meta, km_px):
    for threshold, src in meta.get("sources", []):
        if km_px >= threshold:
            return src
    return None


# ---------------------------------------------------------------------------
# Compact geometry packs (parsed once from the big GeoJSON/shapefiles)
# ---------------------------------------------------------------------------
def _pack(parts, cls=None, group=None):
    """parts: list of (n, 2) float arrays → {coords, offsets, bbox, cls, group} (lon/lat, float64 bbox)."""
    if not parts:
        return {"coords": np.zeros((0, 2), np.float64), "offsets": np.zeros(1, np.int64),
                "bbox": np.zeros((0, 4), np.float64), "cls": np.zeros(0, np.uint8),
                "group": np.zeros(0, np.int32)}
    lens = np.array([len(p) for p in parts], dtype=np.int64)
    coords = np.concatenate(parts).astype(np.float64)
    offsets = np.concatenate([[0], np.cumsum(lens)])
    bbox = np.array([[p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max()] for p in parts])
    return {"coords": coords, "offsets": offsets, "bbox": bbox,
            "cls": np.asarray(cls if cls is not None else np.zeros(len(parts)), dtype=np.uint8),
            "group": np.asarray(group if group is not None else np.full(len(parts), -1), dtype=np.int32)}


def _cached_pack(name, builder):
    GEO_CACHE.mkdir(parents=True, exist_ok=True)
    p = GEO_CACHE / f"{name}.npz"
    if p.exists():
        z = np.load(p)
        return {k: z[k] for k in z.files}
    data = builder()
    np.savez(p, **data)
    return data


def _parts_in(pack, lonlat, pad=0.5):
    lo0, la0, lo1, la1 = lonlat
    bb = pack["bbox"]
    if not len(bb):
        return []
    hit = np.flatnonzero((bb[:, 2] >= lo0 - pad) & (bb[:, 0] <= lo1 + pad) & (bb[:, 3] >= la0 - pad)
                         & (bb[:, 1] <= la1 + pad))
    off, xy = pack["offsets"], pack["coords"]
    return [(xy[off[i]:off[i + 1]], int(pack["cls"][i])) for i in hit]


def _geojson_features(pattern):
    for f in sorted(OSM_DIR.glob(pattern)):
        try:
            yield from json.loads(f.read_text()).get("features", [])
        except Exception:
            continue


@lru_cache(maxsize=32)
def _ne_pack(name):
    def build():
        import shapefile
        for d in (NE_DIR, NE_VEC):
            p = d / f"{name}.shp"
            if p.exists():
                break
        else:
            return _pack([])
        sf = shapefile.Reader(str(p))
        fields = [f[0] for f in sf.fields[1:]]
        rank_i = fields.index("scalerank") if "scalerank" in fields else None
        parts, ranks = [], []
        for sr in sf.iterShapeRecords():
            s = sr.shape
            pts = np.asarray(s.points, dtype=np.float64)
            idx = list(s.parts) + [len(pts)]
            rank = sr.record[rank_i] if rank_i is not None else 0
            for a, b in zip(idx[:-1], idx[1:]):
                if b - a >= 2:
                    parts.append(pts[a:b, :2])
                    ranks.append(min(255, int(rank or 0)))
        return _pack(parts, ranks)
    return _cached_pack(f"ne_{name}", build)


@lru_cache(maxsize=32)
def _osm_line_pack(kind, value):
    """OSM lines (admin boundaries by level, roads by class, waterways by type), de-duplicated across region files."""
    def build():
        pattern = {"osm_admin": "*_boundaries.geojson", "osm_roads": "*_roads.geojson",
                   "osm_waterway": "*_waterways.geojson"}[kind]
        tag = {"osm_admin": "admin_level", "osm_roads": "highway", "osm_waterway": "waterway"}[kind]
        want = {value} if kind != "osm_roads" else {c for v in value.split(",") for c in (v, f"{v}_link")}
        seen, parts, groups, names = set(), [], [], {}
        for feat in _geojson_features(pattern):
            p = feat.get("properties") or {}
            if p.get(tag) not in want:
                continue
            gid = names.setdefault(p.get("name") or "", len(names)) if p.get("name") else -1
            g = feat.get("geometry") or {}
            geoms = [g.get("coordinates")] if g.get("type") == "LineString" else (g.get("coordinates") or [])
            for part in geoms:
                if not part or len(part) < 2:
                    continue
                sig = (round(part[0][0], 5), round(part[0][1], 5), round(part[-1][0], 5), round(part[-1][1], 5), len(part))
                if sig in seen:
                    continue
                seen.add(sig)
                parts.append(np.asarray([c[:2] for c in part], dtype=np.float64))
                groups.append(gid)
        return _pack(parts, group=groups)
    return _cached_pack(f"osm2_{kind}_{value}", build)


NE_ROAD_TYPES = {"major": {"Major Highway"}, "minor": {"Secondary Highway"}}


@lru_cache(maxsize=4)
def _ne_roads_pack(which):
    def build():
        import shapefile
        p = NE_DIR / "ne_10m_roads.shp"
        if not p.exists():
            return _pack([])
        sf = shapefile.Reader(str(p))
        fields = [f[0] for f in sf.fields[1:]]
        ti = fields.index("type")
        parts = []
        for sr in sf.iterShapeRecords():
            if sr.record[ti] not in NE_ROAD_TYPES[which]:
                continue
            pts = np.asarray(sr.shape.points, dtype=np.float64)
            idx = list(sr.shape.parts) + [len(pts)]
            parts += [pts[a:b, :2] for a, b in zip(idx[:-1], idx[1:]) if b - a >= 2]
        return _pack(parts)
    return _cached_pack(f"ne_roads_{which}", build)


def _group_lengths_km(pack, lonlat=None):
    """Total length (km) of each named group (e.g. every OSM segment of 'Fraser River'), counting only
    segments near `lonlat` — names like 'Cabin Creek' repeat all over the region."""
    off, xy, g = pack["offsets"], pack["coords"], pack["group"]
    if lonlat is not None:
        lo0, la0, lo1, la1 = lonlat
        px, py = (lo1 - lo0) * 0.25, (la1 - la0) * 0.25
        bb = pack["bbox"]
        near = (bb[:, 2] >= lo0 - px) & (bb[:, 0] <= lo1 + px) & (bb[:, 3] >= la0 - py) & (bb[:, 1] <= la1 + py)
        g = np.where(near, g, -1)
    seg = np.diff(xy, axis=0)
    coslat = np.cos(np.radians(xy[:-1, 1]))
    d = np.hypot(seg[:, 0] * coslat, seg[:, 1]) * 111.32
    within = np.ones(len(d), dtype=bool)
    within[off[1:-1] - 1] = False                     # don't join the end of one part to the next
    part_len = np.add.reduceat(np.where(within, d, 0), off[:-1]) if len(d) else np.zeros(len(g))
    totals = {}
    for gid, ln in zip(g.tolist(), part_len.tolist()):
        if gid >= 0:
            totals[gid] = totals.get(gid, 0.0) + ln
    return totals


@lru_cache(maxsize=4)
def _inland_water_pack():
    """OSM natural=water polygons (outer rings) classed 1 = lake/reservoir/pond, 2 = river/canal."""
    def build():
        seen, parts, cls = set(), [], []
        feats = list(_geojson_features("*_water_polygons.geojson")) + list(_geojson_features("*_river_areas.geojson"))
        for feat in feats:
            p = feat.get("properties") or {}
            g = feat.get("geometry") or {}
            t, c = g.get("type"), g.get("coordinates") or []
            rings = []
            if t == "Polygon" and c:
                rings = [c[0]]
            elif t == "MultiPolygon":
                rings = [poly[0] for poly in c if poly]
            elif t == "LineString" and len(c) >= 4 and c[0] == c[-1]:
                rings = [c]
            for r in rings:
                if len(r) < 4:
                    continue
                sig = (round(r[0][0], 5), round(r[0][1], 5), len(r))
                if sig in seen:
                    continue
                seen.add(sig)
                parts.append(np.asarray([q[:2] for q in r], dtype=np.float64))
                cls.append(2 if (p.get("water") in RIVER_WATER or p.get("waterway")) else 1)
        return _pack(parts, cls)
    return _cached_pack("osm_inland_water_v2", build)


# ---------------------------------------------------------------------------
# Land / water masks
# ---------------------------------------------------------------------------
@lru_cache(maxsize=4)
def _shp_bbox_index(shp_path: str):
    """Per-record bounding boxes of a (large) shapefile, read straight from the .shx/.shp headers."""
    shp = Path(shp_path)
    cache = BASEMAP_CACHE / f"idx_{hashlib.sha1(shp_path.encode()).hexdigest()[:10]}_{int(shp.stat().st_mtime)}.npy"
    if cache.exists():
        return np.load(cache)
    shx = np.fromfile(shp.with_suffix(".shx"), dtype=">i4", offset=100).reshape(-1, 2)
    boxes = np.zeros((len(shx), 4), dtype=np.float64)
    with open(shp, "rb") as f:
        for i, (off, _ln) in enumerate(shx):
            f.seek(off * 2 + 8 + 4)
            boxes[i] = struct.unpack("<4d", f.read(32))
    BASEMAP_CACHE.mkdir(parents=True, exist_ok=True)
    np.save(cache, boxes)
    return boxes


def _ring_is_outer(r):
    x, y = r[:, 0], r[:, 1]
    return (np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) > 0


def _draw_ocean(draw, view, shp_path, factor=SS):
    import shapefile
    idx = _shp_bbox_index(str(shp_path))
    x0, y0, x1, y1 = view.merc
    world = 2 * 20037508.342789244
    # views that cross the date line also need the polygons from the other side of the world
    shifts = [0.0] + ([-world] if x0 < -world / 2 else []) + ([world] if x1 > world / 2 else [])
    sf = shapefile.Reader(str(shp_path))
    k = factor / view.mpp
    for sh in shifts:
        hit = np.flatnonzero((idx[:, 0] + sh <= x1) & (idx[:, 2] + sh >= x0) & (idx[:, 1] <= y1) & (idx[:, 3] >= y0))
        for i in hit:
            shp = sf.shape(int(i))
            pts = np.asarray(shp.points, dtype=np.float64)
            parts = list(shp.parts) + [len(pts)]
            rings = [np.column_stack(((pts[a:b, 0] + sh - x0) * k, (y1 - pts[a:b, 1]) * k))
                     for a, b in zip(parts[:-1], parts[1:]) if b - a >= 3]
            for r in rings:
                if _ring_is_outer(r):
                    draw.polygon(r.ravel().tolist(), fill=255)
            for r in rings:
                if not _ring_is_outer(r):
                    draw.polygon(r.ravel().tolist(), fill=0)


def _draw_lonlat_polys(draw, view, parts, fill=255, factor=SS):
    for ring in parts:
        px, py = view.project_many(ring[:, 0], ring[:, 1])
        if len(px) >= 3 and (px.max() - px.min()) * factor >= 1 and (py.max() - py.min()) * factor >= 1:
            draw.polygon(np.column_stack((px * factor, py * factor)).ravel().tolist(), fill=fill)


def _prune_spurs(sk, n, keep):
    """Remove skeleton branches shorter than ~n px by peeling endpoints n times. Endpoints inside ``keep``
    (where a channel meets open water) are never peeled, so through-channels keep their full length."""
    k = np.ones((3, 3), dtype=np.uint8)
    for _ in range(n):
        nb = ndimage.convolve(sk.astype(np.uint8), k, mode="constant") - 1
        ends = sk & (nb <= 1) & ~keep
        if not ends.any():
            break
        sk = sk & ~ends
    return sk


def _lost_channels(fine, f, coarse, min_px, island_px=CHANNEL_ISLAND_PX):
    """LED-res mask of narrow water channels present in ``fine`` (f x f per LED px) but lost in ``coarse``.

    Kept: channels that run >= min_px LED px, or that cut off a landmass of >= island_px LED px.
    Only the water the coarse mask lost is skeletonised (open sea would take far longer to thin)."""
    from skimage.morphology import skeletonize
    up = np.repeat(np.repeat(coarse, f, axis=0), f, axis=1)
    lostfine = fine & ~up
    if not lostfine.any():
        return np.zeros_like(coarse)
    sl = ndimage.find_objects(lostfine.astype(np.uint8))[0]
    sl = tuple(slice(max(0, q.start - 2), q.stop + 2) for q in sl)     # work in the bounding box only
    oy, ox = sl[0].start, sl[1].start
    near_open = ndimage.binary_dilation(up[sl], iterations=2)
    sk = _prune_spurs(skeletonize(lostfine[sl]), f, near_open)
    lab, n = ndimage.label(sk, structure=np.ones((3, 3)))
    if n == 0:
        return np.zeros_like(coarse)
    # LED cells each piece passes through; length = how many
    ly, lx = np.nonzero(lab)
    cells = np.unique(np.column_stack((lab[ly, lx], (ly + oy) // f, (lx + ox) // f)), axis=0)
    starts = np.searchsorted(cells[:, 0], np.arange(1, n + 2))
    ch = np.zeros_like(coarse)
    land = ~coarse

    def big_parts(m):
        lb, k = ndimage.label(m)
        return int((np.bincount(lb.ravel())[1:] >= island_px).sum()) if k else 0

    base = big_parts(land)
    order = sorted(range(1, n + 1), key=lambda i: starts[i - 1] - starts[i])     # longest first
    for i in order:
        c = cells[starts[i - 1]:starts[i]]
        if len(c) >= min_px:
            ch[c[:, 1], c[:, 2]] = True
            continue
        trial = land & ~ch
        trial[c[:, 1], c[:, 2]] = False
        parts = big_parts(trial)
        if parts > base:                     # it separates a landmass worth showing
            ch[c[:, 1], c[:, 2]] = True
            base = parts
    # A diagonal-only step in a channel would let the land on either side touch at a corner: fill one side.
    for _ in range(2):
        water = coarse | ch
        a, b, c, d = water[:-1, :-1], water[1:, 1:], water[1:, :-1], water[:-1, 1:]
        ca, cb, cc, cd = ch[:-1, :-1], ch[1:, 1:], ch[1:, :-1], ch[:-1, 1:]
        ch[1:, :-1] |= a & b & ~c & ~d & (ca | cb)      # "\" step: fill below-left
        ch[:-1, :-1] |= c & d & ~a & ~b & (cc | cd)     # "/" step: fill above-left
    return ch & ~coarse


def water_masks(view: View, min_feature_px: float = 3.0):
    """(land, ocean, lakes, rivers) bool masks at LED resolution. Cached per view."""
    key = hashlib.sha1(json.dumps([view.key, min_feature_px, MASK_VERSION]).encode()).hexdigest()[:16]
    cached = BASEMAP_CACHE / f"water_{key}.png"
    if cached.exists():
        a = np.asarray(Image.open(cached).convert("RGBA"))
        return a[..., 0] > 0, a[..., 1] > 0, a[..., 2] > 0, a[..., 3] > 0
    km_px = view.km_per_px
    W, H = view.w * SS, view.h * SS

    ocean_img = Image.new("L", (W, H), 0)
    _draw_ocean(ImageDraw.Draw(ocean_img), view,
                WATER_SIMPLE if km_px >= 1.5 or not WATER_FULL.exists() else WATER_FULL)

    lake_img, river_img = Image.new("L", (W, H), 0), Image.new("L", (W, H), 0)
    ld, rd = ImageDraw.Draw(lake_img), ImageDraw.Draw(river_img)
    # Natural Earth lakes everywhere (cover areas outside the OSM extracts) …
    _draw_lonlat_polys(ld, view, [p for p, _ in _parts_in(_ne_pack("ne_10m_lakes"), view.lonlat)])
    # … plus OSM inland water where we have it (lakes, reservoirs, river areas)
    for ring, cls in _parts_in(_inland_water_pack(), view.lonlat, pad=0.05):
        _draw_lonlat_polys(rd if cls == 2 else ld, view, [ring])

    def down(img):
        return np.asarray(img.resize((view.w, view.h), Image.BOX), dtype=np.float32) >= 127.5

    ocean, lakes, rivers = down(ocean_img), down(lake_img), down(river_img)
    if km_px >= CHANNEL_FINE_KM * SS * 2:
        # Re-find straits/inlets narrower than a pixel at ~250 m and keep the major ones as 1-px water.
        f = max(SS, min(int(np.ceil(km_px / CHANNEL_FINE_KM)), CHANNEL_FINE_MAX_W // view.w))
        fo = Image.new("L", (view.w * f, view.h * f), 0)
        _draw_ocean(ImageDraw.Draw(fo), view, WATER_FULL if WATER_FULL.exists() and km_px < 12 else WATER_SIMPLE, f)
        fine_ocean = np.asarray(fo) > 127
        del fo
        ocean |= _lost_channels(fine_ocean, f, ocean, CHANNEL_MIN_PX)
        fl = Image.new("L", (view.w * f, view.h * f), 0)
        fld = ImageDraw.Draw(fl)
        _draw_lonlat_polys(fld, view, [p for p, _ in _parts_in(_ne_pack("ne_10m_lakes"), view.lonlat)], factor=f)
        for ring, cls in _parts_in(_inland_water_pack(), view.lonlat, pad=0.05):
            if cls != 2:
                _draw_lonlat_polys(fld, view, [ring], factor=f)
        fine_lakes = (np.asarray(fl) > 127) & ~fine_ocean
        del fl, fine_ocean
        lakes |= _lost_channels(fine_lakes, f, lakes | ocean, CHANNEL_MIN_PX) & ~ocean
    lakes &= ~ocean
    rivers &= ~ocean & ~lakes
    # Islands, ponds and slivers smaller than min_feature_px LED pixels would be noise at this scale.
    lakes = _drop_small(lakes, min_feature_px)
    rivers = _drop_small(rivers, min_feature_px)
    land = ~(ocean | lakes | rivers)
    land = _drop_small(land, min_feature_px)
    ocean |= ~land & ~lakes & ~rivers          # islets too small to keep become sea, not "nothing"
    water = ~land
    water_kept = _drop_small(water, min_feature_px)
    land |= water & ~water_kept
    ocean &= ~land
    lakes &= ~land
    rivers &= ~land
    BASEMAP_CACHE.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.dstack([land, ocean, lakes, rivers]).astype(np.uint8) * 255, "RGBA").save(cached)
    return land, ocean, lakes, rivers


def land_mask(view: View, min_feature_px: float = 3.0) -> np.ndarray:
    return water_masks(view, min_feature_px)[0]


def _drop_small(mask, min_px):
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, index=np.arange(1, n + 1))
    keep = np.zeros(n + 1, dtype=bool)
    keep[1:] = sizes >= min_px
    return keep[lab]


def edge_of(mask: np.ndarray) -> np.ndarray:
    """1-px boundary on the True side of a bool mask (4-neighbour)."""
    pad = np.pad(mask, 1, mode="edge")
    neighbour_false = (~pad[:-2, 1:-1]) | (~pad[2:, 1:-1]) | (~pad[1:-1, :-2]) | (~pad[1:-1, 2:])
    return mask & neighbour_false


# ---------------------------------------------------------------------------
# Line layers
# ---------------------------------------------------------------------------
def _lines_for(src, km_px, lonlat=None):
    kind, value = src
    if kind == "ne_roads":
        return _ne_roads_pack(value)
    if kind == "ne":
        pack = _ne_pack(value)
        if value == "ne_10m_rivers_lake_centerlines":
            # thin out by importance as the view widens (scalerank 0 = most important)
            max_rank = 1 if km_px >= 12 else 3 if km_px >= 5 else 5
            return _subset(pack, pack["cls"] <= max_rank)
        return pack
    pack = _osm_line_pack(kind, value)
    if kind == "osm_waterway":
        # OSM tags many mountain creeks as rivers. Keep only named rivers long enough to matter at this
        # scale: total length >= ~60 LED pixels (≈20 km at 0.34 km/px, ≈48 km at 0.8 km/px).
        totals = _group_lengths_km(pack, lonlat)
        need = 60 * km_px
        keep = np.array([g >= 0 and totals.get(g, 0) >= need for g in pack["group"].tolist()], dtype=bool)
        return _subset(pack, keep)
    return pack


def _subset(pack, keep):
    """Same pack, with parts not in `keep` given an impossible bbox so _parts_in skips them."""
    bbox = np.where(keep[:, None], pack["bbox"], np.array([9e9, 9e9, -9e9, -9e9]))
    return {**pack, "bbox": bbox}


def _draw_lines(view, pack, width):
    """Clip, join, simplify (0.5 px) and draw. Returns a writable bool (h, w) mask.

    OSM splits roads and rivers into short ways between intersections; joining connected pieces
    before simplifying keeps lines continuous instead of dropping sub-pixel pieces (dashes)."""
    from shapely.geometry import MultiLineString
    from shapely.ops import linemerge
    img = Image.new("1", (view.w, view.h), 0)
    draw = ImageDraw.Draw(img)
    clip = box(-2, -2, view.w + 2, view.h + 2)
    pieces = []
    for pts, _ in _parts_in(pack, view.lonlat, pad=0.2):
        px, py = view.project_many(pts[:, 0], pts[:, 1])
        if px.max() < -2 or px.min() > view.w + 2 or py.max() < -2 or py.min() > view.h + 2:
            continue
        geom = LineString(np.column_stack((px, py))).intersection(clip)
        for part in getattr(geom, "geoms", [geom]):
            if part.geom_type == "LineString" and not part.is_empty:
                pieces.append(part)
    if pieces:
        merged = linemerge(MultiLineString(pieces))
        merged = shp_simplify(merged, 0.5, preserve_topology=False)
        for part in getattr(merged, "geoms", [merged]):
            if part.geom_type == "LineString" and not part.is_empty:
                coords = [(round(x), round(y)) for x, y in part.coords]
                if len(coords) == 1 or len(set(coords)) == 1:
                    draw.point(coords[0], fill=1)
                else:
                    draw.line(coords, fill=1, width=width)
    return np.array(img, dtype=bool)


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------
def render_overlay(view: View, overrides: dict | None = None, min_feature_px: float = 3.0,
                   group: str = "over") -> np.ndarray:
    """RGBA (h, w, 4) uint8 with every enabled, in-range layer of one group for this view. Cached.

    group "under": drawn on the basemap, below animated data (highways).
    group "over":  drawn on top of everything (coastlines, borders, rivers, boundaries)."""
    all_layers = [s for s in layer_settings(overrides) if s["enabled"]]
    layers = [s for s in all_layers if bool(s.get("under")) == (group == "under")]
    km_px = view.km_per_px
    key = hashlib.sha1(json.dumps([view.key, layers, min_feature_px, MASK_VERSION, group],
                                  sort_keys=True).encode()).hexdigest()[:16]
    cached = BASEMAP_CACHE / f"carto_{key}.png"
    if cached.exists():
        return np.asarray(Image.open(cached).convert("RGBA"))

    land, ocean, lakes, rivers = water_masks(view, min_feature_px)
    shore = edge_of(land)
    near = {name: ndimage.binary_dilation(m) for name, m in (("ocean", ocean), ("lakes", lakes), ("rivers", rivers))}
    coast = shore & near["ocean"]
    lake_shore = shore & near["lakes"] & ~coast
    river_bank = shore & near["rivers"] & ~coast & ~lake_shore
    # Where a river is wide enough to show as water with two 1-px banks, its centre line is redundant;
    # where it's too narrow at this scale, the centre line stands in for it.
    banks_on = any(s["key"] == "river_banks" and s["min_km_px"] <= km_px <= s["max_km_px"] for s in all_layers)
    resolved_rivers = ndimage.binary_dilation(rivers | river_bank, iterations=2) if banks_on else None
    out = np.zeros((view.h, view.w, 4), dtype=np.uint8)
    for s in layers:
        if not (s["min_km_px"] <= km_px <= s["max_km_px"]):
            continue
        meta = LAYERS[s["key"]]
        kind = meta["kind"]
        if kind == "coast":
            m = coast
        elif kind == "lake_shore":
            m = lake_shore
        elif kind == "river_bank":
            m = river_bank
        else:
            src = _pick_source(meta, km_px)
            if not src:
                continue
            m = _draw_lines(view, _lines_for(src, km_px, view.lonlat), int(s.get("width", 1)))
            m &= land                          # strictly on land: nothing drawn across water
            if s["key"] == "rivers" and resolved_rivers is not None:
                m &= ~resolved_rivers          # centre lines only where the banks can't be resolved
        out[m, :3] = np.array(_hex(s["color"]), dtype=np.uint8)
        out[m, 3] = int(255 * float(s.get("opacity", 1.0)))
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
