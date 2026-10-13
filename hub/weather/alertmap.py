"""Maps for the weather-alert screen: the alert's forecast zones over the Core Metro (or Lower Mainland) map.

For each alert the hub renders two layers at the wall's full size (448x192):

    <id>.bin   basemap + highways + coastlines/boundaries, (h, w, 3) uint8
    <id>.mask  the alert's zones, (h, w) uint8 (255 inside)

The display composites them every frame, pulsing the zones in the alert's
severity colour and marking the home city. Zone polygons come with the alert
from the MSC GeoMet weather-alerts API (one feature per forecast region).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import json

import numpy as np
from PIL import Image, ImageDraw

from hub import paths
from hub.weather import carto
from hub.weather.geo import View, alpha_over, render_basemap

OUT = paths.DATA_DIR / "alertmaps"
W, H = 448, 192
HOME_LONLAT = {"richmond": (-123.1336, 49.1666), "vancouver": (-123.1207, 49.2827), "burnaby": (-122.9805, 49.2488),
               "surrey": (-122.8490, 49.1913), "delta": (-123.0587, 49.0847), "coquitlam": (-122.7932, 49.2838),
               "new westminster": (-122.9110, 49.2057), "north vancouver": (-123.0724, 49.3200)}


def _rings(geometry):
    if not geometry:
        return []
    t, co = geometry.get("type"), geometry.get("coordinates") or []
    if t == "Polygon":
        return [co[0]] if co else []
    if t == "MultiPolygon":
        return [p[0] for p in co if p]
    return []


def _zones(rings):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = [Polygon(r).buffer(0) for r in rings if len(r) >= 3]
    return unary_union([p for p in polys if not p.is_empty])


def _share(view, zones):
    """Share of the zones' area (exact geometry) that falls inside ``view``."""
    from shapely.geometry import box
    if zones.is_empty or zones.area == 0:
        return 0.0
    return zones.intersection(box(*view.lonlat)).area / zones.area


def pick_area(cfg, zone_rings, home_hit=None, keep=0.5, some=0.2, max_jump=3.0):
    """The closest-in map area that shows the whole alert. ``zone_rings`` is one ring list per alert zone.

    A view shows a zone when ``keep`` of its area is on screen (ECCC zones run into empty mountains — the
    North Shore zone is half backcountry — so "all of it" is too strict). If that needs a view more than
    ``max_jump`` × coarser than one showing ``some`` of every zone (Sea to Sky and the Sunshine Coast reach far
    up the coast), the closer view wins: better a zone running off the edge than Metro shrunk to a dot."""
    areas = cfg["modules"].get("weather_maps", {}).get("areas", {})
    if not areas:
        return None
    views = sorted(((View(dict(a, id=k), W, H, fit_width=W), k) for k, a in areas.items()),
                   key=lambda vk: vk[0].km_per_px)
    zones = [z for z in (_zones(r) for r in zone_rings) if not z.is_empty]
    if not zones:
        return views[0][1]

    def first(th):
        return next(((v, k) for v, k in views if min(_share(v, z) for z in zones) >= th), views[-1])

    full, part = first(keep), first(some)
    return part[1] if full[0].km_per_px > max_jump * part[0].km_per_px else full[1]


HOME_DEFAULT = (-123.0918, 49.1442)          # home (approximate)


def render(alert, cfg, home_city="Richmond", home_ll=None):
    """Write the alert's map layers; returns meta for alerts.json (or None if it can't be drawn)."""
    rings = [r for g in alert.get("geometries") or [] for r in _rings(g)]
    if not rings:
        return None
    aid = pick_area(cfg, [_rings(g) for g in alert.get("geometries") or []], alert.get("home"))
    areas = cfg["modules"].get("weather_maps", {}).get("areas", {})
    if aid not in areas:
        return None
    area = dict(areas[aid], id=aid)
    key = hashlib.sha1(json.dumps([aid, area, rings], sort_keys=True, default=str).encode()).hexdigest()[:12]
    OUT.mkdir(parents=True, exist_ok=True)
    view = View(area, W, H, fit_width=W)
    meta = {"file": f"alertmaps/{key}.bin", "mask": f"alertmaps/{key}.mask", "w": W, "h": H,
            "area": area.get("label") or aid}
    home = tuple(home_ll) if home_ll else (HOME_DEFAULT if (home_city or "").lower() == "richmond"
                                           else HOME_LONLAT.get((home_city or "").lower()))
    if home:
        hx, hy = view.project(*home)
        if 0 <= hx < W and 0 <= hy < H:
            meta["home_px"] = [int(hx), int(hy)]
    # where the forecast towns fall on this map (value labels) and which ECCC zone each pixel is in (colouring)
    from hub.weather.alert_data import TOWNS
    meta["towns_px"] = {r: [int(v) for v in view.project(lo, la)] for _, r, lo, la in TOWNS
                        if 0 <= view.project(lo, la)[0] < W and 0 <= view.project(lo, la)[1] < H}
    meta["zones_file"] = f"alertmaps/{key}.zones"
    if not (OUT / f"{key}.zones").exists() or not (OUT / f"{key}.zones.json").exists():
        _zone_raster(view, OUT / f"{key}.zones")
    try:
        meta["zones"] = json.loads((OUT / f"{key}.zones.json").read_text())
    except (OSError, ValueError):
        meta["zones"] = []
    if (OUT / f"{key}.bin").exists() and (OUT / f"{key}.mask").exists():
        return meta
    base = render_basemap(view, {"source": "esri_dark", "brightness": 1.0, "contrast": 1.15})
    carto_cfg = area.get("carto") or {}
    mfp = float(area.get("min_feature_px", 3))
    img = alpha_over(base, carto.render_overlay(view, carto_cfg, mfp, group="under"))
    mask_img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask_img)
    for ring in rings:
        lon = np.array([p[0] for p in ring], dtype=np.float64)
        lat = np.array([p[1] for p in ring], dtype=np.float64)
        px, py = view.project_many(lon, lat)
        if len(px) >= 3:
            d.polygon(list(zip(px.tolist(), py.tolist())), fill=255)
    mask = np.asarray(mask_img, dtype=np.uint8).copy()
    mask[~carto.land_mask(view, mfp)] = 0                 # zones and city limits run out over the water: land only
    img = alpha_over(img, carto.render_overlay(view, carto_cfg, mfp, group="over"))
    np.ascontiguousarray(img, dtype=np.uint8).tofile(OUT / f"{key}.bin")
    np.ascontiguousarray(mask).tofile(OUT / f"{key}.mask")
    return meta


def prune(keep):
    """Remove map files no current alert uses."""
    if not OUT.exists():
        return
    names = {m.split("/")[-1] for meta in keep for m in (meta["file"], meta["mask"], meta.get("zones_file") or "")}
    names |= {n + ".json" for n in list(names) if n.endswith(".zones")}
    for p in OUT.iterdir():
        if p.name not in names:
            p.unlink(missing_ok=True)


# ---------------------------------------------------------------------------------------------
# city outlines (for the test alert, whose zones are made up): OSM admin_level 8 boundary lines → polygons
# ---------------------------------------------------------------------------------------------
_CITY_CACHE = paths.CACHE_DIR / "geo" / "city_polygons.json"


def _city_polygons():
    if _CITY_CACHE.exists():
        return json.loads(_CITY_CACHE.read_text())
    from shapely.geometry import LineString, mapping
    from shapely.ops import polygonize, unary_union
    from hub.weather.geo import GEODATA_ROOT
    src = GEODATA_ROOT / "geodata" / "osm" / "metro_vancouver_boundaries.geojson"
    lines = {}
    for f in json.loads(src.read_text())["features"]:
        p = f.get("properties") or {}
        if str(p.get("admin_level")) != "8" or not p.get("name"):
            continue
        co = f["geometry"]["coordinates"]
        if len(co) >= 2:
            lines.setdefault(p["name"], []).append(LineString(co))
    out = {}
    for name, ls in lines.items():
        polys = list(polygonize(unary_union(ls)))
        if polys:
            geom = unary_union(polys)
            out[name] = mapping(geom)
    _CITY_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CITY_CACHE.write_text(json.dumps(out))
    return out


def _match_city(name, polys):
    n = name.lower()
    for k in polys:
        kk = k.lower()
        if kk == n or kk in (f"city of {n}", f"district of {n}", f"township of {n}", f"{n} municipality"):
            return k
    return next((k for k in polys if n in k.lower()), None)


def _zone_raster(view, path):
    """uint8 raster: 0 outside, k+1 inside ECCC alert zone k (names in <path>.json), land only."""
    from shapely.geometry import shape
    from hub import paths as _p
    fc = json.loads((_p.CACHE_DIR / "grids" / "eccc_zone.geojson").read_text())
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    names = []
    lo0, la0, lo1, la1 = view.lonlat
    for f in fc["features"]:
        g = shape(f["geometry"])
        b = g.bounds
        if b[2] < min(lo0, lo1) or b[0] > max(lo0, lo1) or b[3] < min(la0, la1) or b[1] > max(la0, la1):
            continue
        names.append(f["properties"]["name"])
        k = len(names)
        polys = [g] if g.geom_type == "Polygon" else list(getattr(g, "geoms", []))
        for pg in polys:
            if pg.geom_type != "Polygon":
                continue
            lon = np.array([q[0] for q in pg.exterior.coords]); lat = np.array([q[1] for q in pg.exterior.coords])
            px, py = view.project_many(lon, lat)
            if len(px) >= 3:
                d.polygon(list(zip(px.tolist(), py.tolist())), fill=k)
    z = np.asarray(img, dtype=np.uint8).copy()
    z[~carto.land_mask(view, 3.0)] = 0
    np.ascontiguousarray(z).tofile(path)
    Path(str(path) + ".json").write_text(json.dumps(names))


def render_sample(cfg, cities, home_city="Richmond", home_hit=True, home_ll=None):
    """Map for a sample alert: its cities' outlines as the 'affected zones'."""
    polys = _city_polygons()
    geoms = []
    for c in cities:
        k = _match_city(c, polys)
        if k:
            geoms.append(polys[k])
    return render({"geometries": geoms, "home": home_hit}, cfg, home_city, home_ll)
