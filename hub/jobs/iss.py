"""The International Space Station for webcam captions (a webcam whose location is Space) → data/iss.json.

- Orbit: CelesTrak's current elements for the ISS (NORAD 25544, refreshed every TLE_TTL), propagated here with sgp4
  for the next HORIZON: where it is over the ground, whether it is in sunlight or Earth's shadow, and so its next
  orbital sunrises and sunsets (about 16 a day).
- What it's over: Natural Earth's countries and named seas and oceans (public domain, downloaded once into the
  geodata folder); the time zone below from timezonefinder (ocean zones included).
- Crew and mission: the community-kept "people in space" list (corquaid/international-space-station-APIs):
  the people aboard the ISS with their countries' flag codes, and the expedition with its start date.

Output: {"at", "elements_at", "track": [[t, lat, lon, lit], …] every STEP s, "events": [[t, "sunrise"|"sunset"], …],
"over": [[t, text], …] and "tz": [[t, zone], …] every OVER_STEP s, "crew": {"count", "by": [[flag code, n], …]} or
null, "mission": {"name", "start"} or null}. The display (display/modules/space.py) reads it for the moment the
video shows.
"""

from __future__ import annotations

import io
import json
import math
import time
import zipfile
from pathlib import Path

import requests

from hub import paths
from hub.jobs import _harness

ELEMENTS_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=tle"
CREW_URL = "https://corquaid.github.io/international-space-station-APIs/JSON/people-in-space.json"
NE = {  # Natural Earth (public domain): countries, and named seas and oceans
    "countries": "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip",
    "marine": "https://naciscdn.org/naturalearth/10m/physical/ne_10m_geography_marine_polys.zip",
}
UA = {"User-Agent": "BigScreen/2 (home LED wall; ISS webcam caption)"}
HORIZON = 3 * 3600           # computed this far ahead (the job runs every 10 min)
STEP = 10                    # track resolution (s)
OVER_STEP = 60               # what it's over / the zone below, this often (the ISS covers ~460 km a minute)
TLE_TTL = 6 * 3600
CREW_TTL = 3 * 3600
R_EARTH = 6378.137
SHORT = {"United States of America": "the United States", "Russian Federation": "Russia",
         "Dem. Rep. Congo": "DR Congo", "Central African Rep.": "the Central African Republic",
         "United Kingdom": "the United Kingdom", "Netherlands": "the Netherlands", "Philippines": "the Philippines",
         "Bahamas": "the Bahamas", "United Arab Emirates": "the UAE", "Czechia": "Czechia"}


def cache_dir() -> Path:
    d = paths.CACHE_DIR / "iss"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cached_get(name, url, ttl, binary=False):
    """A file fetched at most every ttl (kept in the cache; an old copy is used when the fetch fails)."""
    p = cache_dir() / name
    if p.exists() and time.time() - p.stat().st_mtime < ttl:
        return p.read_bytes() if binary else p.read_text()
    try:
        r = requests.get(url, headers=UA, timeout=60)
        r.raise_for_status()
        p.write_bytes(r.content)
    except Exception:
        if not p.exists():
            raise
    return p.read_bytes() if binary else p.read_text()


# --- orbit ----------------------------------------------------------------------------------------------------
def elements():
    lines = [l.strip() for l in _cached_get("iss.tle", ELEMENTS_URL, TLE_TTL).splitlines() if l.strip()]
    l1 = next(l for l in lines if l.startswith("1 "))
    l2 = next(l for l in lines if l.startswith("2 "))
    return l1, l2


def julian(t):
    return t / 86400.0 + 2440587.5


def gmst(jd):
    """Greenwich mean sidereal time (rad)."""
    d = jd - 2451545.0
    return math.radians((280.46061837 + 360.98564736629 * d) % 360.0)


def sun_dir(jd):
    """Unit vector to the Sun in the equatorial frame (low precision: well under 0.1 degree)."""
    n = jd - 2451545.0
    L = math.radians((280.460 + 0.9856474 * n) % 360)
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    return (math.cos(lam), math.cos(eps) * math.sin(lam), math.sin(eps) * math.sin(lam))


def sunlit(r, s) -> bool:
    """In sunlight, or in Earth's (cylindrical) shadow."""
    d = r[0] * s[0] + r[1] * s[1] + r[2] * s[2]
    if d > 0:
        return True
    px, py, pz = r[0] - d * s[0], r[1] - d * s[1], r[2] - d * s[2]
    return math.sqrt(px * px + py * py + pz * pz) > R_EARTH


def ground(r, jd):
    """(lat, lon) in degrees under an equatorial-frame position (km)."""
    x, y, z = r
    lon = math.degrees(math.atan2(y, x) - gmst(jd))
    lon = (lon + 540.0) % 360.0 - 180.0
    lat_gc = math.atan2(z, math.hypot(x, y))
    lat = math.degrees(math.atan(math.tan(lat_gc) / (1 - 0.00669438)))     # geocentric -> geodetic
    return lat, lon


def track(l1, l2, t0, horizon=HORIZON, step=STEP):
    from sgp4.api import Satrec
    sat = Satrec.twoline2rv(l1, l2)
    out = []
    for i in range(int(horizon // step) + 1):
        t = t0 + i * step
        jd = julian(t)
        e, r, _v = sat.sgp4(int(jd), jd - int(jd))
        if e:
            continue
        lat, lon = ground(r, jd)
        out.append([round(t), round(lat, 3), round(lon, 3), 1 if sunlit(r, sun_dir(jd)) else 0])
    return out


def events(tr):
    """Sunrises and sunsets along the track: [[t, "sunrise"|"sunset"], …]."""
    out = []
    for a, b in zip(tr, tr[1:]):
        if a[3] != b[3]:
            out.append([b[0], "sunrise" if b[3] else "sunset"])
    return out


# --- what it's over -------------------------------------------------------------------------------------------
class Below:
    """Country or named sea under a point, and its time zone."""

    def __init__(self):
        import shapefile
        from shapely.geometry import shape
        from shapely.strtree import STRtree
        self.layers = {}
        for name, url in NE.items():
            raw = _cached_get(f"ne_{name}.zip", url, 365 * 86400, binary=True)
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                base = next(n[:-4] for n in z.namelist() if n.endswith(".shp"))
                r = shapefile.Reader(shp=io.BytesIO(z.read(base + ".shp")), shx=io.BytesIO(z.read(base + ".shx")),
                                     dbf=io.BytesIO(z.read(base + ".dbf")), encoding="utf-8")
                fields = [f[0].lower() for f in r.fields[1:]]
                geoms, names = [], []
                for sr in r.iterShapeRecords():
                    rec = dict(zip(fields, sr.record))
                    nm = rec.get("name") if name == "countries" else (rec.get("name") or rec.get("label"))
                    if not nm:
                        continue
                    try:
                        geoms.append(shape(sr.shape.__geo_interface__))
                        names.append(str(nm))
                    except Exception:
                        continue
                self.layers[name] = (STRtree(geoms), geoms, names)
        from timezonefinder import TimezoneFinder
        self.tzf = TimezoneFinder()

    def name(self, lat, lon) -> str:
        from shapely.geometry import Point
        p = Point(lon, lat)
        tree, geoms, names = self.layers["countries"]
        for i in tree.query(p, predicate="intersects"):
            return "Over " + SHORT.get(names[i], names[i])
        tree, geoms, names = self.layers["marine"]
        hits = list(tree.query(p, predicate="intersects"))
        if hits:                                          # the smallest named water it's in (a bay before its ocean)
            i = min(hits, key=lambda k: geoms[k].area)
            return "Over " + sea(names[i])
        i = tree.nearest(p)                               # open ocean the polygons leave out: the nearest named one
        return "Over " + sea(names[i]) if i is not None else "Over the ocean"

    def zone(self, lat, lon) -> str:
        z = self.tzf.timezone_at(lng=lon, lat=lat)
        if z:
            return z
        h = round(lon / 15)                               # nautical time: Etc/GMT has its sign the other way round
        return "UTC" if h == 0 else f"Etc/GMT{'-' if h > 0 else '+'}{abs(h)}"


def sea(name) -> str:
    """'SOUTH PACIFIC OCEAN' -> 'the South Pacific'; 'Bay of Bengal' -> 'the Bay of Bengal'."""
    n = " ".join(w if w.lower() in ("of", "the", "and", "de") else w.capitalize() for w in str(name).split())
    n = n.replace(" Of ", " of ").replace(" The ", " the ")
    for o in ("North Pacific", "South Pacific", "North Atlantic", "South Atlantic"):
        if n == o + " Ocean":
            n = o
    return n if n.lower().startswith("the ") else "the " + n


# --- crew -----------------------------------------------------------------------------------------------------
def crew():
    """({"count", "by": [[flag code, n], …]}, {"name", "start"}) from the people-in-space list (Nones on failure)."""
    try:
        d = json.loads(_cached_get("people.json", CREW_URL, CREW_TTL))
    except Exception:
        return None, None
    aboard = [p for p in d.get("people") or [] if p.get("iss")]
    by = {}
    for p in aboard:
        code = str(p.get("flag_code") or "").lower()[:2] or "??"
        by[code] = by.get(code, 0) + 1
    c = {"count": len(aboard), "by": sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))} if aboard else None
    m = None
    if d.get("iss_expedition"):
        m = {"name": f"Expedition {d['iss_expedition']}", "start": d.get("expedition_start_date")}
    return c, m


def run(cfg):
    from hub.util import atomic_write_json
    cams = (((cfg.get("modules") or {}).get("webcams") or {}).get("cams") or {})
    if not any(isinstance(c, dict) and c.get("space") for c in cams.values()):
        return {"ok": True, "skipped": "no webcam is in space"}
    now = time.time()
    l1, l2 = elements()
    tr = track(l1, l2, now)
    below = Below()
    over, tz = [], []
    for row in tr[::max(1, OVER_STEP // STEP)]:
        over.append([row[0], below.name(row[1], row[2])])
        tz.append([row[0], below.zone(row[1], row[2])])
    c, m = crew()
    out = {"at": now, "elements": [l1, l2], "track": tr, "events": events(tr), "over": over, "tz": tz,
           "crew": c, "mission": m}
    atomic_write_json(paths.DATA_DIR / "iss.json", out)
    return {"ok": True, "points": len(tr), "events": len(out["events"]), "crew": (c or {}).get("count")}


if __name__ == "__main__":
    _harness.main(run)
