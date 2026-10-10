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


def pick_area(cfg, rings, home_hit):
    """Core Metro for alerts on the home city whose zones sit in Metro Vancouver; else the Lower Mainland."""
    areas = cfg["modules"].get("weather_maps", {}).get("areas", {})
    if home_hit and "core_metro" in areas:
        return "core_metro"
    return "metro" if "metro" in areas else next(iter(areas), None)


def render(alert, cfg, home_city="Richmond"):
    """Write the alert's map layers; returns meta for alerts.json (or None if it can't be drawn)."""
    rings = [r for g in alert.get("geometries") or [] for r in _rings(g)]
    if not rings:
        return None
    aid = pick_area(cfg, rings, alert.get("home"))
    areas = cfg["modules"].get("weather_maps", {}).get("areas", {})
    if aid not in areas:
        return None
    area = dict(areas[aid], id=aid)
    key = hashlib.sha1(json.dumps([aid, area, rings], sort_keys=True, default=str).encode()).hexdigest()[:12]
    OUT.mkdir(parents=True, exist_ok=True)
    view = View(area, W, H, fit_width=W)
    meta = {"file": f"alertmaps/{key}.bin", "mask": f"alertmaps/{key}.mask", "w": W, "h": H,
            "area": area.get("label") or aid}
    home = HOME_LONLAT.get((home_city or "").lower())
    if home:
        hx, hy = view.project(*home)
        if 0 <= hx < W and 0 <= hy < H:
            meta["home_px"] = [int(hx), int(hy)]
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
    names = {m.split("/")[-1] for meta in keep for m in (meta["file"], meta["mask"])}
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


def render_sample(cfg, cities, home_city="Richmond"):
    """Map for the built-in test alert: its cities' outlines as the 'affected zones'."""
    polys = _city_polygons()
    geoms = []
    for c in cities:
        k = _match_city(c, polys)
        if k:
            geoms.append(polys[k])
    return render({"geometries": geoms, "home": True}, cfg, home_city)
