"""Traffic maps for the DriveBC screen, drawn for their exact LED scale.

Unlike the weather basemaps (resampled tiles), these are drawn pixel by pixel at the target size so every line
is crisp on the matrix:

  * land and water as two flat colours from the shared land/water mask, a 1-px coastline;
  * OSM roads by class (motorway, trunk, primary, secondary), projected, snapped to whole pixels and drawn
    without anti-aliasing — 2 px motorways when zoomed in, minor classes only at the zooms where they fit;
  * neutral greys, so the affected stretch (drawn by the display in alert colours) is the only colour on the map;
  * shield anchors: for each numbered route, a spot on the line itself, spread so markers never collide —
    the display draws the real shield there. No text labels.

``render(name, area, w, h)`` writes data/bcmaps/<name>.bin + .json (with ``shields``), like hub/bc/maps.py.
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache

import numpy as np

from hub import paths
from hub.bc import maps
from hub.util import atomic_write_json, read_json

VERSION = 3
LAND, WATER, COAST = (21, 22, 27), (9, 22, 44), (40, 74, 120)
ROAD = {"secondary": (52, 53, 60), "primary": (88, 88, 98), "trunk": (140, 140, 150), "motorway": (190, 190, 200)}
ORDER = ["secondary", "primary", "trunk", "motorway"]
SOURCES = ["metro_vancouver_roads.geojson", "bc_south_roads.geojson"]


def route_of(ref, cls):
    """OSM ref → (shield kind, number): "TCH 1" → tch 1, "YH 5" → yellowhead 5, "CN 3" → crowsnest 3, "BC 99",
    "I 5", "US 101", "WA 20"; a bare number on a BC road is a BC route (1 is the Trans-Canada)."""
    ref = (ref or "").split(";")[0].strip()
    if not ref:
        return None
    parts = ref.split()
    if len(parts) == 1:
        n = parts[0]
        return ("tch", n) if n == "1" else ("bc", n) if n[:1].isdigit() else None
    p, n = parts[0].upper(), parts[-1]
    kind = {"TCH": "tch", "YH": "yellowhead", "CN": "crowsnest", "BC": "bc", "I": "interstate", "US": "us", "WA": "wa",
            "SR": "wa"}.get(p)
    if kind == "bc" and n == "1":
        kind = "tch"
    return (kind, n) if kind else None


@lru_cache(maxsize=2)
def _roads(fname):
    from hub.weather.geo import GEODATA_ROOT
    out = []
    p = GEODATA_ROOT / "geodata" / "osm" / fname
    if not p.exists():
        return out
    for f in json.loads(p.read_text()).get("features", []):
        pr = f.get("properties") or {}
        cls = pr.get("highway")
        if cls not in ROAD:
            continue
        g = f.get("geometry") or {}
        lines = [g["coordinates"]] if g.get("type") == "LineString" else g.get("coordinates") or [] if g.get("type") == "MultiLineString" else []
        for ln in lines:
            if len(ln) >= 2:
                a = np.asarray(ln, dtype=np.float64)[:, :2]
                out.append((cls, pr.get("ref") or "", a, (a[:, 0].min(), a[:, 1].min(), a[:, 0].max(), a[:, 1].max()),
                            (pr.get("name") or "").lower()))
    return out


def _width(cls, kpp):
    if cls == "motorway":
        return 2 if kpp <= 0.09 else 1
    if cls == "trunk":
        return 2 if kpp <= 0.05 else 1
    return 1


def _visible(cls, kpp):
    return {"secondary": kpp <= 0.12, "primary": kpp <= 0.6}.get(cls, True)


def render(name, area, w, h, force=False):
    from PIL import Image, ImageDraw
    from scipy import ndimage
    from hub.weather import carto
    from hub.weather.geo import View
    sig = hashlib.sha1(json.dumps([area, w, h, VERSION], sort_keys=True).encode()).hexdigest()[:12]
    meta_p = maps.OUT / f"{name}.json"
    meta = read_json(meta_p, {}) or {}
    if not force and meta.get("sig") == sig and (maps.OUT / f"{name}.bin").exists():
        return meta
    view = View(dict(area, id=f"traffic_{name}"), w, h)
    kpp = view.km_per_px
    land, ocean, lakes, rivers = carto.water_masks(view, 3.0 if kpp > 0.1 else 2.0)
    img = np.where(land[..., None], np.array(LAND, np.uint8), np.array(WATER, np.uint8)).astype(np.uint8)
    shore = carto.edge_of(land) & ndimage.binary_dilation(~land)
    img[shore] = COAST
    pil = Image.fromarray(img)
    d = ImageDraw.Draw(pil)
    lo0, la0, lo1, la1 = view.lonlat
    bb = (min(lo0, lo1) - 0.02, min(la0, la1) - 0.02, max(lo0, lo1) + 0.02, max(la0, la1) + 0.02)
    roads = [r for src in SOURCES for r in _roads(src)]
    seen = set()
    pts_by_route = {}
    for cls in ORDER:
        if not _visible(cls, kpp):
            continue
        wd = _width(cls, kpp)
        for c, ref, a, (x0, y0, x1, y1), _nm in roads:
            if c != cls or x1 < bb[0] or x0 > bb[2] or y1 < bb[1] or y0 > bb[3]:
                continue
            xs, ys = view.project_many(a[:, 0], a[:, 1])
            px = np.stack([np.floor(xs) + 0.5, np.floor(ys) + 0.5], 1)          # snap to pixel centres
            keep = np.ones(len(px), bool)
            keep[1:] = np.any(np.diff(px, axis=0) != 0, axis=1)
            px = px[keep]
            if len(px) < 2:
                continue
            key = (cls, px.tobytes()[:64], len(px))
            if key in seen:                                                     # the same way in both extracts
                continue
            seen.add(key)
            d.line([tuple(p) for p in px], fill=ROAD[cls], width=wd)
            r = route_of(ref, cls)
            if r and cls in ("motorway", "trunk", "primary"):
                pts_by_route.setdefault(r, []).extend((float(x), float(y), ORDER.index(cls)) for x, y in px)
    out = np.asarray(pil)
    # shield anchors: on the line, spread out, the most important routes first
    anchors = []
    margin_x, margin_y = 12, 10
    min_gap = 64 if w >= 300 else 48
    cx, cy = w / 2, h / 2
    routes = sorted(pts_by_route.items(), key=lambda kv: (-max(p[2] for p in kv[1]), -len(kv[1])))
    for (kind, num), pts in routes:
        cand = [(x, y) for x, y, _ in pts if margin_x <= x <= w - margin_x and margin_y <= y <= h - margin_y]
        if len(cand) < 6:
            continue
        cand.sort(key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        placed = 0
        for x, y in cand[::3]:
            if all((x - a["x"]) ** 2 + (y - a["y"]) ** 2 >= min_gap ** 2 for a in anchors):
                anchors.append({"kind": kind, "num": num, "x": round(x), "y": round(y)})
                placed += 1
                if placed >= (2 if w >= 300 else 1):
                    break
        if len(anchors) >= 10:
            break
    maps.OUT.mkdir(parents=True, exist_ok=True)
    np.ascontiguousarray(out, dtype=np.uint8).tofile(maps.OUT / f"{name}.bin")
    meta = {"name": name, "w": view.w, "h": view.h, "merc": list(view.merc), "mpp": view.mpp, "km_per_px": kpp,
            "lonlat": list(view.lonlat), "lon0": -180.0, "sig": sig, "shields": anchors, "style": "traffic"}
    atomic_write_json(meta_p, meta)
    return meta


def event_area(points, w=448, h=192, min_kpp=0.02, max_kpp=0.12):
    """A zoom that frames an event's geometry (points [[lon, lat], …]) with room around it."""
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    clat, clon = (min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2
    span_x = (max(lons) - min(lons)) * 111.32 * math.cos(math.radians(clat))
    span_y = (max(lats) - min(lats)) * 110.57
    kpp = max(span_x * 3.0 / w, span_y * 3.0 / h, min_kpp)
    return {"center": [round(clat, 5), round(clon, 5)], "km_per_px": round(min(max_kpp, kpp), 4)}


DIRS = {"northbound": (0, 1), "southbound": (0, -1), "eastbound": (1, 0), "westbound": (-1, 0)}


def match_road(points, highways=(), name="", corridor_m=180.0, point_reach_m=450.0, direction=""):
    """The real road an event is on: OSM ways with the event's route number (or its name) inside a corridor round
    the event's geometry, cut to that corridor. Returns lines as [[lon, lat], …] (empty if nothing matches)."""
    from shapely.geometry import LineString, MultiLineString, Point
    pts = [tuple(p[:2]) for p in points or []]
    if not pts:
        return []
    lat0 = sum(p[1] for p in pts) / len(pts)
    deg = 1.0 / 111_000.0
    kx = math.cos(math.radians(lat0))
    geom = LineString(pts) if len(pts) > 1 else Point(pts[0])
    reach = corridor_m if len(pts) > 1 else point_reach_m
    # buffer in metres (scale x by cos(lat) so the corridor is round on the ground)
    from shapely.affinity import scale
    area = scale(scale(geom, xfact=kx, origin=(0, 0)).buffer(reach * deg), xfact=1 / kx, origin=(0, 0))
    nums = {str(h).upper() for h in highways or []}
    nm = (name or "").lower()
    minx, miny, maxx, maxy = area.bounds
    out = []
    for src in SOURCES:
        for cls, ref, a, (x0, y0, x1, y1), onm in _roads(src):
            if x1 < minx or x0 > maxx or y1 < miny or y0 > maxy:
                continue
            rt = route_of(ref, cls)
            if nums:
                hit = bool(rt and rt[1].upper() in nums)
            else:                                    # a street, bridge or tunnel without a number: by name
                hit = bool(nm and onm and (onm == nm or (len(onm) > 5 and (onm in nm or nm in onm))))
            if not hit:
                continue
            seg = LineString(a).intersection(area)
            if seg.is_empty:
                continue
            parts = seg.geoms if isinstance(seg, MultiLineString) or hasattr(seg, "geoms") else [seg]
            for g in parts:
                if g.geom_type == "LineString" and g.length > 0:
                    out.append([[round(x, 5), round(y, 5)] for x, y in g.coords])
        if out:
            break                                   # the Metro extract matched; the regional one would duplicate it
    v = DIRS.get((direction or "").lower())
    if v and out:                                   # one-way carriageways: keep the ones running that way
        def heading(l):
            return (l[-1][0] - l[0][0]) * kx * v[0] + (l[-1][1] - l[0][1]) * v[1]
        same = [l for l in out if heading(l) > 0]
        if same:
            out = same
    return out
