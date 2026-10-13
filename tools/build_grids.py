"""Build the tile-grid maps the alert screen draws (and any future alert source can reuse): for each
administrative level, every unit becomes a labelled box placed on a coarse grid where it roughly sits on the
map — not to scale, but a box for each unit and west stays west.

Run on the hub (needs network, shapely, scipy):  .venv/bin/python -m tools.build_grids [grid ...]

Sources (all free, no key):
  BC Geographic Warehouse WFS (openmaps.gov.bc.ca): municipalities, regional districts, provincial electoral
      districts, health authorities, local health areas (→ health service delivery areas), community health
      service areas (neighbourhood-scale communities everywhere in BC), First Nations reserves
  Represent (represent.opennorth.ca): federal electoral districts (2023 representation order)
  City of Vancouver open data: Vancouver's 22 local areas (neighbourhoods)
  Natural Earth: provinces and territories
  MSC Geography Package (ECCC): public forecast regions, sub-regions (the alert zones), air quality zones,
      tsunami zones — shapefiles copied to <state>/cache/msc

Outputs
  assets/grids/<grid>.json        {id, label, level, source, cols, rows, units: [{id, name, labels, col, row}]}
                                  (labels: longest → shortest; the renderer takes the first that fits its box)
  <state>/cache/grids/<level>.geojson   simplified unit shapes, for working out which units an alert covers
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import requests
from scipy.optimize import linear_sum_assignment
from shapely.geometry import mapping, shape
from shapely.ops import unary_union

from hub import paths

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "assets" / "grids"
GEOM = paths.CACHE_DIR / "grids"
MSC = paths.CACHE_DIR / "msc"
UA = {"User-Agent": "BigScreen/2.0 (personal LED weather display; one-off boundary download)"}
WFS = "https://openmaps.gov.bc.ca/geo/pub/wfs"

# areas of interest (lon0, lat0, lon1, lat1)
LOWER_MAINLAND = (-124.1, 48.95, -121.2, 50.45)
METRO = (-123.35, 48.98, -122.2, 49.45)
BC = (-139.1, 48.2, -114.0, 60.1)


# ------------------------------------------------------------------------------------------------ sources
def wfs(layer, name_key, id_key, bbox=None):
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeName": layer, "outputFormat": "json",
              "srsName": "EPSG:4326"}
    if bbox:
        params["bbox"] = f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]},urn:ogc:def:crs:EPSG::4326"
    r = requests.get(WFS, params=params, headers=UA, timeout=300)
    r.raise_for_status()
    out = []
    for f in r.json()["features"]:
        g = shape(f["geometry"])
        out.append({"id": str(f["properties"][id_key]), "name": f["properties"][name_key], "geom": _lonlat(g),
                    "props": f["properties"]})
    return out


def _lonlat(g):
    """GeoServer's EPSG:4326 may come back lat, lon — swap if so."""
    x, y = g.representative_point().coords[0]
    if abs(x) <= 90 and abs(y) > 90:
        from shapely.ops import transform
        return transform(lambda a, b, z=None: (b, a), g)
    return g


def represent(boundary_set, id_prefix=""):
    """Represent allows ~60 requests a minute: list the set, then fetch the wanted shapes a second apart."""
    import time as _t
    out, url, items = [], f"https://represent.opennorth.ca/boundaries/{boundary_set}/?limit=500", []
    while url:
        j = requests.get(url, headers=UA, timeout=120).json()
        items += [b for b in j["objects"] if str(b.get("external_id") or "").startswith(id_prefix)]
        nxt = j["meta"].get("next")
        url = f"https://represent.opennorth.ca{nxt}" if nxt else None
    for b in items:
        for attempt in range(3):
            r = requests.get(f"https://represent.opennorth.ca{b['url']}simple_shape", headers=UA, timeout=60)
            if r.ok:
                out.append({"id": b["external_id"] or b["name"], "name": b["name"], "geom": shape(r.json())})
                break
            _t.sleep(5 * (attempt + 1))
        _t.sleep(1.1)
    return out


def vancouver_local_areas():
    j = requests.get("https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/local-area-boundary/exports/geojson",
                     headers=UA, timeout=120).json()
    return [{"id": f["properties"].get("mapid") or f["properties"]["name"], "name": f["properties"]["name"],
             "geom": shape(f["geometry"])} for f in j["features"]]


def provinces():
    j = requests.get("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_1_states_provinces.geojson",
                     headers=UA, timeout=300).json()
    return [{"id": f["properties"]["postal"], "name": f["properties"]["name"], "geom": shape(f["geometry"])}
            for f in j["features"] if f["properties"].get("iso_a2") == "CA"]


def msc(kind):
    import shapefile
    sf = shapefile.Reader(str(MSC / f"land_{kind}_detail_unproj.shp"), encoding="utf-8")
    out = {}
    for sr in sf.iterShapeRecords():
        r = sr.record.as_dict()
        if r.get("PROVINCE_C") != "BC":
            continue
        g = shape(sr.shape.__geo_interface__)
        key = r["NAME"]
        out[key] = out[key].union(g) if key in out else g
    return [{"id": n, "name": n, "geom": g} for n, g in out.items()]


def hsda():
    lha = wfs("WHSE_ADMIN_BOUNDARIES.BCHA_LOCAL_HEALTH_AREA_SP", "LOCAL_HLTH_AREA_NAME", "LOCAL_HLTH_AREA_CODE")
    groups = {}
    for u in lha:
        p = u["props"]
        groups.setdefault((str(p["HLTH_SERVICE_DLVR_AREA_CODE"]), p["HLTH_SERVICE_DLVR_AREA_NAME"]), []).append(u["geom"])
    return [{"id": k, "name": n, "geom": unary_union(gs)} for (k, n), gs in groups.items()]


# level id → (label, source loader)
LEVELS = {
    "province": ("Provinces and territories", provinces),
    "regional_district": ("Regional districts", lambda: wfs("WHSE_LEGAL_ADMIN_BOUNDARIES.ABMS_REGIONAL_DISTRICTS_SP", "ADMIN_AREA_NAME", "LGL_ADMIN_AREA_ID")),
    "municipality": ("Municipalities", lambda: wfs("WHSE_LEGAL_ADMIN_BOUNDARIES.ABMS_MUNICIPALITIES_SP", "ADMIN_AREA_NAME", "LGL_ADMIN_AREA_ID")),
    "community": ("Communities (health service areas)", lambda: wfs("WHSE_ADMIN_BOUNDARIES.BCHA_CMNTY_HEALTH_SERV_AREA_SP", "CMNTY_HLTH_SERV_AREA_NAME", "CMNTY_HLTH_SERV_AREA_CODE")),
    "neighbourhood_van": ("Vancouver neighbourhoods", vancouver_local_areas),
    "reserve": ("First Nations reserves", lambda: wfs("WHSE_ADMIN_BOUNDARIES.CLAB_INDIAN_RESERVES", "ENGLISH_NAME", "CLAB_ID", LOWER_MAINLAND)),
    "bc_riding": ("Provincial electoral districts", lambda: wfs("WHSE_ADMIN_BOUNDARIES.EBC_PROV_ELECTORAL_DIST_SVW", "ED_NAME", "ED_ABBREVIATION")),
    "federal_riding": ("Federal electoral districts", lambda: represent("federal-electoral-districts-2023-representation-order", "59")),
    "health_authority": ("Health authorities", lambda: wfs("WHSE_ADMIN_BOUNDARIES.BCHA_HEALTH_AUTHORITY_BNDRY_SP", "HLTH_AUTHORITY_NAME", "HLTH_AUTHORITY_CODE")),
    "hsda": ("Health service delivery areas", hsda),
    "lha": ("Local health areas", lambda: wfs("WHSE_ADMIN_BOUNDARIES.BCHA_LOCAL_HEALTH_AREA_SP", "LOCAL_HLTH_AREA_NAME", "LOCAL_HLTH_AREA_CODE")),
    "eccc_region": ("ECCC public forecast regions", lambda: msc("PubStdZone")),
    "eccc_zone": ("ECCC alert zones", lambda: msc("PubMesoZone")),
    "eccc_aq": ("ECCC air quality zones", lambda: msc("AQStdZone")),
    "eccc_tsunami": ("Tsunami zones", lambda: msc("TsuStdZone")),
}

# grid id → (level, area of interest or None for all, label, max cols, box aspect target)
GRIDS = {
    "provinces":          ("province", None, "Provinces and territories", 8),
    "regional_districts": ("regional_district", BC, "BC regional districts", 8),
    "municipalities_lm":  ("municipality", LOWER_MAINLAND, "Municipalities", 10),
    "municipalities_metro": ("municipality", METRO, "Metro Vancouver municipalities", 8),
    "communities_metro":  ("community", METRO, "Communities", 12),
    "neighbourhoods_van": ("neighbourhood_van", None, "Vancouver neighbourhoods", 7),
    "bc_ridings_lm":      ("bc_riding", LOWER_MAINLAND, "Provincial ridings", 11),
    "federal_ridings_lm": ("federal_riding", LOWER_MAINLAND, "Federal ridings", 9),
    "health_authorities": ("health_authority", None, "Health authorities", 5),
    "hsdas":              ("hsda", None, "Health service delivery areas", 6),
    "lhas_lm":            ("lha", LOWER_MAINLAND, "Local health areas", 9),
    "eccc_regions_bc":    ("eccc_region", None, "ECCC forecast regions", 9),
    "eccc_zones_lm":      ("eccc_zone", LOWER_MAINLAND, "ECCC alert zones", 6),
    "eccc_tsunami_bc":    ("eccc_tsunami", None, "Tsunami zones", 5),
}


# ------------------------------------------------------------------------------------------------ labels
DROP = re.compile(r"^(The )?(City|District|Township|Village|Town|Corporation|Resort Municipality|Municipality|"
                  r"Regional District|Island Municipality|Mountain Resort Municipality) of (the )?", re.I)
ABBR = {"North": "N", "South": "S", "East": "E", "West": "W", "Northeast": "NE", "Northwest": "NW", "Southeast": "SE",
        "Southwest": "SW", "Central": "C", "Mount": "Mt", "Port": "Pt", "Saint": "St", "Fort": "Ft", "Lake": "Lk",
        "River": "R", "Island": "I", "Islands": "Is", "Vancouver": "Van", "Coquitlam": "Coq", "Westminster": "West",
        "Regional": "Reg", "District": "Dist", "Valley": "Vly", "Columbia": "Col", "Mountain": "Mtn", "Heights": "Hts",
        "Centre": "Ctr", "Center": "Ctr", "Langley": "Lgly", "Abbotsford": "Abby", "Chilliwack": "Chwk",
        "Metro": "Metro", "Fraser": "Fraser", "Okanagan": "Okan", "Kootenay": "Koot", "Sunshine": "Sun",
        "Squamish": "Sqmsh", "Richmond": "Rich", "Burnaby": "Bby", "Surrey": "Sry", "Peninsula": "Pen"}


SMALL = {"of", "the", "and", "to", "de", "du", "la", "-", "–"}


def clean(name):
    n = DROP.sub("", name.strip())
    n = re.sub(r"\s*\([^)]*\)", "", n)                    # "(Part)", "(Unincorporated)"…
    n = re.sub(r"\s+Indian Reserve No\.?\s*", " ", n)
    if n.startswith("Metro Vancouver - "):
        n = n[len("Metro Vancouver - "):]
    n = n.split(" including ")[0]
    return re.sub(r"\s+", " ", n).strip()


def _abbr(words):
    return [ABBR.get(w, w) for w in words]


def labels(name):
    """Label variants, longest first, built to stay readable: the name; "Head - Tail" names (ECCC zones, tsunami
    zones) as head + tail's first words; words abbreviated (N, Pt, Mt…) and small words dropped; each word cut to
    6…3 letters; "A/B" names as A; initials only as a last resort."""
    full = clean(name)
    out = [full]
    head, sep, tail = full.partition(" - ")
    tw = [w for w in tail.split() if w.lower() not in SMALL]
    hw = head.split()
    if sep and re.match(r"^Zone [A-Z]$", head):            # tsunami: "Zone E - the Strait of Georgia…"
        out += [f"{head} {' '.join(tw[:2])}", f"{head} {' '.join(tw[:1])}", head]
    elif sep:
        out += [f"{' '.join(_abbr(hw))} {' '.join(tw[:2])}", f"{' '.join(_abbr(hw))} {' '.join(tw[:1])}"]
    words = [w for w in re.split(r"[\s/-]+", full) if w and w.lower() not in SMALL]
    out.append(" ".join(_abbr(re.split(r"[\s/]+", full.replace(" - ", " ")))))
    out.append(" ".join(_abbr(words)))
    for k in (6, 5, 4, 3):
        out.append(" ".join(w if len(w) <= k else w[:k] for w in _abbr(words)))
    if "/" in full:
        first = full.split("/")[0].strip()
        out += [first, " ".join(_abbr(first.split()))]
    if sep and not re.match(r"^Zone [A-Z]$", head):
        out.append(" ".join(_abbr(hw)) + " " + "".join(w[0] for w in tw[:2]))
    if len(words) > 1:
        out.append(" ".join(w[0] for w in words[:-1]) + " " + words[-1][:5])
        out.append("".join(w[0] for w in words))
    seen, res = set(), []
    for x in out:
        x = re.sub(r"\s+", " ", x).strip().upper()
        if x and x not in seen:
            seen.add(x)
            res.append(x)
    return res


# ------------------------------------------------------------------------------------------------ layout
def layout(units, max_cols):
    """Grid (cols, rows) and a cell per unit: each unit goes to the cell nearest its real position (west→east,
    north→south), dense clusters spread out by blending real position with rank."""
    n = len(units)
    pts = np.array([u["geom"].representative_point().coords[0] for u in units])
    lat0 = math.radians(pts[:, 1].mean())
    xy = np.c_[pts[:, 0] * math.cos(lat0), -pts[:, 1]]
    span = xy.max(0) - xy.min(0)
    span[span == 0] = 1
    lin = (xy - xy.min(0)) / span
    rank = np.c_[np.argsort(np.argsort(xy[:, 0])), np.argsort(np.argsort(xy[:, 1]))] / max(1, n - 1)
    pos = 0.55 * lin + 0.45 * rank
    aspect = max(0.35, min(3.0, span[0] / span[1]))
    cols = max(2, min(max_cols, round(math.sqrt(n * 1.7 * aspect * 2.2))))
    rows = max(2, math.ceil(n * 1.7 / cols))
    cells = np.array([((c + 0.5) / cols, (r + 0.5) / rows) for r in range(rows) for c in range(cols)])
    cost = ((pos[:, None, :] - cells[None, :, :]) ** 2).sum(-1)
    ri, ci = linear_sum_assignment(cost)
    for i, k in zip(ri, ci):
        units[i]["col"], units[i]["row"] = int(k % cols), int(k // cols)
    used_c = sorted({u["col"] for u in units})
    used_r = sorted({u["row"] for u in units})
    for u in units:
        u["col"], u["row"] = used_c.index(u["col"]), used_r.index(u["row"])
    return len(used_c), len(used_r)


# The Metro Vancouver diagram: these places only (City + District of North Vancouver as one, City + Township of
# Langley as one); villages and islands (Belcarra, Anmore, Lions Bay, Bowen Island) left off.
METRO_CURATED = [("West Vancouver", ["West Vancouver"]), ("North Vancouver", ["North Vancouver"]),
                 ("Vancouver", ["Vancouver"]), ("Burnaby", ["Burnaby"]), ("New Westminster", ["New Westminster"]),
                 ("Port Moody", ["Port Moody"]), ("Coquitlam", ["Coquitlam"]), ("Port Coquitlam", ["Port Coquitlam"]),
                 ("Pitt Meadows", ["Pitt Meadows"]), ("Maple Ridge", ["Maple Ridge"]), ("Richmond", ["Richmond"]),
                 ("Delta", ["Delta"]), ("Surrey", ["Surrey"]), ("White Rock", ["White Rock"]), ("Langley", ["Langley"])]


ISLAND_RDS = ("Nanaimo", "Cowichan", "Capital", "Comox", "Strathcona", "Alberni", "Mount Waddington", "Islands Trust")


def _load_cached(level):
    p = GEOM / f"{level}.geojson"
    if not p.exists():
        return None
    fc = json.loads(p.read_text())
    return [{"id": f["properties"]["id"], "name": f["properties"]["name"], "geom": shape(f["geometry"])} for f in fc["features"]]


def build(grid_id, cache):
    level, aoi, label, max_cols = GRIDS[grid_id]
    if level not in cache and (GEOM / f"{level}.geojson").exists() and "--refresh" not in sys.argv:
        fc = json.loads((GEOM / f"{level}.geojson").read_text())
        cache[level] = [{"id": f["properties"]["id"], "name": f["properties"]["name"], "geom": shape(f["geometry"])}
                        for f in fc["features"]]
    if level not in cache:
        print(f"  downloading {level} …", flush=True)
        cache[level] = LEVELS[level][1]()
        GEOM.mkdir(parents=True, exist_ok=True)
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"id": u["id"], "name": u["name"]},
             "geometry": mapping(u["geom"].simplify(0.001, preserve_topology=True))} for u in cache[level]]}
        (GEOM / f"{level}.geojson").write_text(json.dumps(fc))
    units = [dict(u) for u in cache[level] if u["geom"].is_valid or u["geom"].buffer(0).is_valid]
    if aoi:
        from shapely.geometry import box
        b = box(*aoi)
        units = [u for u in units if u["geom"].representative_point().within(b)]
    if grid_id == "municipalities_metro":                     # a curated Metro Vancouver: merged pairs, no hamlets
        rd = cache.get("regional_district") or _load_cached("regional_district")
        mv = [x["geom"] for x in rd or [] if "Metro Vancouver" in x["name"] or "Greater Vancouver" in x["name"]]
        if mv:
            mvg = unary_union([g.buffer(0) for g in mv])
            units = [u for u in units if u["geom"].representative_point().within(mvg)]
        merged = {}
        for u in units:
            n = clean(u["name"])
            name = next((k for k, pats in METRO_CURATED if any(p == n or n.startswith(p + " ") for p in pats)), None)
            if name:
                merged.setdefault(name, []).append(u["geom"].buffer(0))
        level = "municipality_metro"
        units = [{"id": re.sub(r"[^a-z]+", "_", k.lower()), "name": k, "geom": unary_union(gs)} for k, gs in merged.items()]
        GEOM.mkdir(parents=True, exist_ok=True)
        (GEOM / f"{level}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"id": u["id"], "name": u["name"]},
             "geometry": mapping(u["geom"].simplify(0.001, preserve_topology=True))} for u in units]}))
    # same cleaned name, different municipality (City / District of North Vancouver, City / Township of
    # Langley): keep the kind so the boxes can be told apart
    names = {}
    for u in units:
        names.setdefault(clean(u["name"]), set()).add(u["name"])
    for u in units:
        n = clean(u["name"])
        if len(names[n]) > 1:
            kind = re.match(r"^(?:The )?(?:Corporation of the )?(City|District|Township|Village|Town)\b", u["name"], re.I)
            if kind:
                u["name"] = f"{n} {kind.group(1).title()}"
    # merge units that share a name (multi-part municipalities etc.)
    by = {}
    for u in units:
        by.setdefault(clean(u["name"]), []).append(u)
    def fix(g):
        b = g.buffer(0) if g.geom_type in ("Polygon", "MultiPolygon") else g
        return g if b.is_empty else b
    units = [{"id": us[0]["id"], "name": clean(n), "geom": unary_union([fix(x["geom"]) for x in us])} for n, us in by.items()]
    units = [u for u in units if not u["geom"].is_empty]
    # Vancouver Island and Gulf Islands units on a mainland grid: kept, but shown only when an alert covers them
    island = set()
    if aoi in (LOWER_MAINLAND, METRO) and level not in ("eccc_zone", "eccc_region", "eccc_aq", "eccc_tsunami"):
        rd = cache.get("regional_district") or _load_cached("regional_district")
        isl = [u["geom"] for u in rd or [] if any(k in u["name"] for k in ISLAND_RDS)]
        if isl:
            ig = unary_union([g.buffer(0) for g in isl])
            island = {u["id"] for u in units if u["geom"].representative_point().within(ig)}
        # and anything west of Bowen Island: the Sunshine Coast and the islands are a ferry away
        island |= {u["id"] for u in units if u["geom"].representative_point().x < -123.42}
    cols, rows = layout(units, max_cols)
    out = {"id": grid_id, "label": label, "level": level, "source": LEVELS.get(level, LEVELS["municipality"])[0], "cols": cols, "rows": rows,
           "units": [{"id": u["id"], "name": u["name"], "labels": labels(u["name"]), "col": u["col"], "row": u["row"],
                      **({"optional": "island"} if u["id"] in island else {})}
                     for u in sorted(units, key=lambda u: (u["row"], u["col"]))]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{grid_id}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{grid_id}: {len(units)} units on {cols} x {rows}", flush=True)


if __name__ == "__main__":
    want = [a for a in sys.argv[1:] if not a.startswith("--")] or list(GRIDS)
    cache = {}
    for g in want:
        try:
            build(g, cache)
        except Exception as e:
            print(f"{g}: FAILED {type(e).__name__}: {e}", flush=True)
