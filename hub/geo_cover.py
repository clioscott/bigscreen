"""Which tile-grid units an alert covers — for every grid in assets/grids (tools/build_grids.py), from the alert's
own shapes. Any alert source that gives polygons (or names a set of ECCC zones) gets the same grids for free.

A unit is covered when the alert's area takes in at least COVER of it, or its representative point; line
features (tsunami coastlines) count when they touch. Results go into alerts.json:
    alert["coverage"] = {grid_id: [unit ids]}, and alerts.json["home_units"] = {grid_id: unit id}.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from hub import paths

REPO = Path(__file__).resolve().parents[1]
GRIDS = REPO / "assets" / "grids"
GEOM = paths.CACHE_DIR / "grids"
COVER = 0.25


@lru_cache(maxsize=None)
def grids():
    out = {}
    for p in sorted(GRIDS.glob("*.json")):
        g = json.loads(p.read_text(encoding="utf-8"))
        out[g["id"]] = g
    return out


@lru_cache(maxsize=None)
def level_shapes(level):
    """{unit id: geometry} for a level (simplified shapes from the build), plus an STRtree over them."""
    p = GEOM / f"{level}.geojson"
    if not p.exists():
        return {}, None, []
    fc = json.loads(p.read_text(encoding="utf-8"))
    geoms = {}
    for f in fc["features"]:
        g = shape(f["geometry"])
        if g.geom_type in ("Polygon", "MultiPolygon"):
            g = g.buffer(0)
        uid = str(f["properties"]["id"])
        geoms[uid] = geoms[uid].union(g) if uid in geoms else g
    ids = list(geoms)
    return geoms, STRtree([geoms[i] for i in ids]), ids


def _unit_geoms(grid):
    geoms, _, _ = level_shapes(grid["level"])
    out = {}
    for u in grid["units"]:
        g = geoms.get(str(u["id"]))
        if g is not None:
            out[str(u["id"])] = g
    return out


def covered(area, grid):
    """Unit ids of ``grid`` that ``area`` (a shapely geometry) covers."""
    hits = []
    for uid, g in _unit_geoms(grid).items():
        if not area.intersects(g):
            continue
        if g.geom_type not in ("Polygon", "MultiPolygon"):
            hits.append(uid)
            continue
        inter = area.intersection(g).area
        if g.area and (inter / g.area >= COVER or area.contains(g.representative_point())):
            hits.append(uid)
    return hits


def coverage(area):
    if area is None or area.is_empty:
        return {}
    return {gid: covered(area, g) for gid, g in grids().items()}


def home_units(lon, lat):
    pt = Point(lon, lat)
    out = {}
    for gid, g in grids().items():
        for uid, geom in _unit_geoms(g).items():
            if geom.geom_type in ("Polygon", "MultiPolygon") and geom.contains(pt):
                out[gid] = uid
                break
    return out


@lru_cache(maxsize=None)
def _zones():
    geoms, _, _ = level_shapes("eccc_zone")
    return geoms


def zones_area(region_names):
    """The union of the ECCC alert zones named in an alert's region list (CAP area descriptions start with the
    zone's name, e.g. 'Metro Vancouver - southwest including Richmond and Delta')."""
    zs = _zones()
    parts = []
    for r in region_names or []:
        head = r.split(" including ")[0].strip()
        for name, g in zs.items():
            if name == r or name.startswith(head) or head.startswith(name.split(" including ")[0]):
                parts.append(g)
    return unary_union(parts) if parts else None


def alert_area(alert):
    """An alert's area: its own polygons when it has them, else its named ECCC zones."""
    polys = []
    for g in alert.get("geometries") or []:
        try:
            polys.append(shape(g).buffer(0))
        except Exception:
            continue
    if polys:
        return unary_union(polys)
    return zones_area(alert.get("regions"))
