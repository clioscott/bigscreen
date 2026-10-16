"""Basemaps for the BC alert screens, rendered once per spec into data/bcmaps/<name>.bin (RGB) + .json.

Each screen has its own map style so it never looks like the weather maps: flat colours from the land/water
mask (crisp coasts), selected cartography on top, sometimes a dimmed relief or dark tile layer underneath.
The .json holds the projection (Web Mercator bbox + metres per pixel) so the display can place points,
lines and polygons itself; ``lon0`` lets a map cross the antimeridian (the Pacific tsunami map).
"""

from __future__ import annotations

import hashlib
import json
import threading

import numpy as np

from hub import paths
from hub.util import atomic_write_json, read_json

OUT = paths.DATA_DIR / "bcmaps"
RENDER_VERSION = 3              # bump to re-render every map after a change to the drawing code
_lock = threading.Lock()

_OFF = {k: {"enabled": False} for k in ("rivers", "river_banks", "lakes", "municipal", "regional", "province", "country",
                                        "major_highways", "minor_highways", "coastline")}


def _carto(**on):
    out = json.loads(json.dumps(_OFF))
    for k, v in on.items():
        out[k] = dict(v, enabled=True)
    return out


SPECS = {
    # Alert Ready: plain civil-defence map — near-black land, navy water, grey-blue coast
    "naad_lm": {"area": {"fit": {"nw": [49.78, -123.80], "se": [48.98, -121.55]}}, "w": 448, "h": 192,
                "land": "#121318", "water": "#081226", "min_px": 3,
                "carto": _carto(coastline={"color": "#5c7fb8"}, lakes={"color": "#3c5c90"}, river_banks={"color": "#3c5c90"},
                                rivers={"color": "#2c4a7a"}, country={"color": "#9a90c0"},
                                major_highways={"color": "#4a4a56", "opacity": 0.9},
                                municipal={"color": "#2a2a34", "opacity": 0.9, "max_km_px": 0.8})},
    "naad_bc": {"area": {"fit": {"nw": [60.05, -139.2], "se": [48.25, -114.0]}}, "w": 232, "h": 192,
                "land": "#15161c", "water": "#081226", "min_px": 2,
                "carto": _carto(coastline={"color": "#5c7fb8"}, country={"color": "#9a90c0"}, province={"color": "#6f6a92"},
                                lakes={"color": "#2c4a7a"})},
    # DriveBC: transit-map look — charcoal land, ink-blue water, every highway drawn
    "roads_metro": {"area": {"fit": {"nw": [49.4085, -123.2975], "se": [49.0021, -122.4593]}}, "w": 448, "h": 192,
                    "land": "#16171b", "water": "#0a1830", "min_px": 3,
                    "carto": _carto(coastline={"color": "#2f5486"}, lakes={"color": "#2a4a78"}, river_banks={"color": "#2a4a78"},
                                    country={"color": "#7d7398"},
                                    municipal={"color": "#262630", "opacity": 1.0, "max_km_px": 1.0},
                                    minor_highways={"color": "#5a5648", "opacity": 0.9, "max_km_px": 2.0},
                                    major_highways={"color": "#8a7a5a", "opacity": 1.0})},
    "roads_metro_r": {"area": {"fit": {"nw": [49.4085, -123.2975], "se": [49.0021, -122.4593]}}, "w": 288, "h": 192,
                      "fit_width": 448, "same_as": "roads_metro"},
    # Earthquakes: dark shaded relief, coast and borders
    "quake_swbc": {"area": {"center": [48.95, -123.25], "km_per_px": 1.45}, "w": 448, "h": 192,
                   "basemap": {"source": "esri_relief", "brightness": 0.30, "contrast": 1.3, "saturation": 0.0},
                   "land_tint": "#0c0e12", "land_k": 0.55, "water": "#06101c", "water_k": 0.85, "min_px": 3,
                   "carto": _carto(coastline={"color": "#6f8fb0"}, country={"color": "#b0a8d0"}, province={"color": "#7a7498"},
                                   major_highways={"color": "#4a4438", "opacity": 0.8})},
    "quake_casc": {"area": {"center": [48.6, -126.2], "km_per_px": 5.0}, "w": 448, "h": 192,
                   "basemap": {"source": "esri_relief", "brightness": 0.30, "contrast": 1.3, "saturation": 0.0},
                   "land_tint": "#0c0e12", "land_k": 0.55, "water": "#06101c", "water_k": 0.85, "min_px": 2,
                   "carto": _carto(coastline={"color": "#6f8fb0"}, country={"color": "#b0a8d0"}, province={"color": "#7a7498"})},
    # Tsunami: the North Pacific (dark tiles, wraps the antimeridian) and the BC coast (flat ocean blues)
    "tsunami_pacific": {"area": {"fit": {"nw": [63.5, 138.0], "se": [17.0, 244.0]}}, "w": 448, "h": 192, "lon0": 100.0,
                        "ne_land": {"land": "#1e2228", "water": "#04142e", "coast": "#4f7fb8"}},
    "tsunami_bc": {"area": {"fit": {"nw": [54.9, -134.4], "se": [48.15, -122.7]}}, "w": 200, "h": 192,
                   "land": "#1a1c1e", "water": "#05183a", "min_px": 2,
                   "carto": _carto(coastline={"color": "#6aa6e0"}, country={"color": "#9a90c0"})},
    # Wildfire: warm relief, rivers and lakes, highways
    "fire_south": {"area": {"fit": {"nw": [51.0, -125.4], "se": [48.3, -118.6]}}, "w": 448, "h": 192,
                   "basemap": {"source": "esri_relief", "brightness": 0.55, "contrast": 1.2, "saturation": 0.0},
                   "land_tint": "#4a3018", "land_k": 0.45, "water": "#071a24", "water_k": 0.9, "min_px": 3,
                   "carto": _carto(coastline={"color": "#4f8090"}, lakes={"color": "#3d6f80"}, rivers={"color": "#2d5560", "max_km_px": 3.0},
                                   country={"color": "#a89cc8"}, major_highways={"color": "#a08458", "opacity": 0.5})},
}


def _sig(name):
    spec = SPECS[name]
    if spec.get("same_as"):
        base = dict(SPECS[spec["same_as"]])
        base.update({k: v for k, v in spec.items() if k != "same_as"})
        spec = base
    return spec, hashlib.sha1(json.dumps([spec, RENDER_VERSION], sort_keys=True).encode()).hexdigest()[:12]


def ensure(name, force=False):
    """Render a map if missing or its spec changed; returns its meta."""
    with _lock:
        spec, sig = _sig(name)
        meta_p = OUT / f"{name}.json"
        meta = read_json(meta_p, {}) or {}
        if not force and meta.get("sig") == sig and (OUT / f"{name}.bin").exists():
            return meta
        img, view = render(spec, name)
        OUT.mkdir(parents=True, exist_ok=True)
        np.ascontiguousarray(img, dtype=np.uint8).tofile(OUT / f"{name}.bin")
        meta = {"name": name, "w": view.w, "h": view.h, "merc": list(view.merc), "mpp": view.mpp,
                "km_per_px": view.km_per_px, "lonlat": list(view.lonlat), "lon0": spec.get("lon0", -180.0), "sig": sig}
        atomic_write_json(meta_p, meta)
        return meta


def ensure_custom(name, spec, force=False):
    """A one-off map (e.g. a zoomed map round one evacuation area) from an explicit spec."""
    SPECS[name] = spec
    return ensure(name, force)


def prune(prefix, keep):
    """Drop rendered maps named prefix* that aren't in ``keep``."""
    for p in OUT.glob(f"{prefix}*.json"):
        if p.stem not in keep:
            p.unlink(missing_ok=True)
            (OUT / f"{p.stem}.bin").unlink(missing_ok=True)


def ensure_all(names, log=print):
    out = {}
    for n in names:
        try:
            out[n] = ensure(n)
        except Exception as e:
            log(f"map {n}: {e}")
    return out


def _hex(c):
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def render(spec, name="bc"):
    from hub.weather import carto
    from hub.weather.geo import View, alpha_over, render_basemap
    view = View(dict(spec["area"], id=f"bc_{name}"), spec["w"], spec["h"], fit_width=spec.get("fit_width"))
    if spec.get("basemap"):
        base = render_basemap(view, spec["basemap"]).astype(np.float32)
    else:
        base = np.zeros((view.h, view.w, 3), np.float32)
    if spec.get("ne_land"):
        return _ne_land(view, spec), view
    mfp = float(spec.get("min_px", 3))
    if "land" in spec or "water" in spec or "land_tint" in spec:
        land = carto.land_mask(view, mfp)
        if "land" in spec:
            base[land] = _hex(spec["land"])
        if "land_tint" in spec:
            k = float(spec.get("land_k", 0.4))
            base[land] = base[land] * (1 - k) + _hex(spec["land_tint"]) * k
        if "water" in spec:
            k = float(spec.get("water_k", 1.0))
            base[~land] = base[~land] * (1 - k) + _hex(spec["water"]) * k
    img = np.clip(base + 0.5, 0, 255).astype(np.uint8)
    ov = spec.get("carto") or {}
    img = alpha_over(img, carto.render_overlay(view, ov, mfp, group="under"))
    img = alpha_over(img, carto.render_overlay(view, ov, mfp, group="over"))
    return img, view


def _ne_land(view, spec):
    """Land from Natural Earth polygons, longitudes shifted into [lon0, lon0 + 360) so a map can span the
    antimeridian (the OSM water masks behind the other maps can't)."""
    import shapefile
    from PIL import Image, ImageDraw
    from hub.weather.carto import NE_DIR, NE_VEC
    from hub.weather.geo import GEODATA_ROOT
    st = spec["ne_land"]
    lon0 = float(spec.get("lon0", -180.0))
    ss = 4
    img = Image.new("L", (view.w * ss, view.h * ss), 0)
    d = ImageDraw.Draw(img)
    for base in (GEODATA_ROOT / "natural_earth_cache", NE_DIR, NE_VEC):
        shp = base / "ne_10m_land.shp"
        if shp.exists():
            break
    from shapely.affinity import translate
    from shapely.geometry import box, shape as to_shape
    lo_w, la_s, lo_e, la_n = view.lonlat
    span = (view.merc[2] - view.merc[0]) / 6378137.0 * 57.29578
    left = view.merc[0] / 6378137.0 * 57.29578                 # unwrapped (can exceed 180)
    window = box(left - 1, max(-85, min(la_s, la_n) - 1), left + span + 1, min(85, max(la_s, la_n) + 1))

    def rings(g):
        for poly in getattr(g, "geoms", [g]):
            if poly.geom_type == "Polygon" and not poly.is_empty:
                yield poly.exterior.coords, True
                for hole in poly.interiors:
                    yield hole.coords, False

    for shp_ in shapefile.Reader(str(shp)).iterShapes():
        geom = to_shape(shp_.__geo_interface__)
        for off in (-360.0, 0.0, 360.0):                     # the copy of the world that falls in the window
            g = translate(geom, xoff=off)
            if not g.intersects(window):
                continue
            for coords, outer in rings(g.intersection(window)):
                c = np.asarray(coords)
                x, y = view.project_many(c[:, 0], c[:, 1])
                d.polygon(list(zip((x * ss).tolist(), (y * ss).tolist())), fill=255 if outer else 0)
    land = np.asarray(img.resize((view.w, view.h), Image.BOX)) >= 128
    out = np.where(land[..., None], _hex(st["land"]), _hex(st["water"]))
    p = np.pad(land, 1, mode="edge")
    coast = land & ~(p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:])
    out[coast] = _hex(st.get("coast", "#4f7fb8"))
    return np.clip(out, 0, 255).astype(np.uint8)


def project(meta, lat, lon):
    """Pixel position of a point on a rendered map (same maths as the display's bc_kit.Map)."""
    import math
    R = 6378137.0
    lon = float(lon)
    lon0 = float(meta.get("lon0", -180.0))
    while lon < lon0:
        lon += 360.0
    while lon >= lon0 + 360.0:
        lon -= 360.0
    x = math.radians(lon) * R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    return (x - meta["merc"][0]) / meta["mpp"], (meta["merc"][3] - y) / meta["mpp"]
