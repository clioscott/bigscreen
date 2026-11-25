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

from common.orbit import gmst, julian, sun_dir  # noqa: F401  (shared with the display's ISS maps)
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
BACK = 95 * 60               # … and from this long before now (the maps' trail: the last 90 min, as filmed)
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


def _cached_get(name, url, ttl, binary=False, timeout=60):
    """A file fetched at most every ttl (kept in the cache; an old copy is used when the fetch fails)."""
    p = cache_dir() / name
    if p.exists() and time.time() - p.stat().st_mtime < ttl:
        return p.read_bytes() if binary else p.read_text()
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
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
def people():
    """Everyone in space (the Space Map's crew panels): [{"name", "flag", "craft", "launched", "iss", "role",
    "agency"}], or None."""
    try:
        d = json.loads(_cached_get("people.json", CREW_URL, CREW_TTL))
    except Exception:
        return None
    out = []
    for p in d.get("people") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        try:
            launched = int(float(p.get("launched")))
        except (TypeError, ValueError):
            launched = None
        out.append({"name": str(p["name"]), "flag": str(p.get("flag_code") or "").lower()[:2],
                    "craft": str(p.get("spacecraft") or ""), "launched": launched, "iss": bool(p.get("iss")),
                    "role": str(p.get("position") or ""), "agency": str(p.get("agency") or "")})
    return out


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


# --- the ISS map's extras (display/modules/issmap.py) ---------------------------------------------------------
CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
CSS_ID = 48274                      # Tiangong (Tianhe core module)
RELAY_TTL = 12 * 3600
DOCKED_URL = "https://corquaid.github.io/international-space-station-APIs/JSON/iss-docked-spacecraft.json"
OVATION_URL = "https://services.swpc.noaa.gov/json/ovation_aurora_latest.json"
LIGHTS_URL = ("https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0"
              "&LAYERS=VIIRS_Black_Marble&CRS=EPSG:4326&BBOX=-90,-180,90,180&WIDTH=1440&HEIGHT=720&FORMAT=image/png"
              "&STYLES=")
LIGHTS_TTL = 90 * 86400
LIGHTS_RETRY = 86400                # a failed city-lights fetch is tried again after this
EXTRAS_BUDGET = 120.0               # the extras' fetches stop starting after this long (the job's timeout is 240 s)
FETCH_TIMEOUT = 20
MIN_TDRS = 7                        # TDRS 1-6 (the first generation, 1983-1993) are left out: retired or spares


def _tles(text):
    """[(name, line 1, line 2)] from a 3-line TLE listing."""
    ls = [x.rstrip() for x in text.splitlines() if x.strip()]
    out = []
    for i in range(len(ls) - 2):
        if ls[i + 1].startswith("1 ") and ls[i + 2].startswith("2 "):
            out.append((ls[i].strip(), ls[i + 1], ls[i + 2]))
    return out


def relays(now):
    """The relay satellites, where they are now: NASA's TDRS fleet, Russia's Luch-5 and China's Tianlian II
    (geostationary, so their positions barely move between runs). [{"name", "net": "tdrs"|"luch"|"tianlian", "lat",
    "lon"}]"""
    from sgp4.api import Satrec
    out = []
    for net, url, keep in (("tdrs", CELESTRAK + "?GROUP=tdrss&FORMAT=tle", lambda n: n.startswith("TDRS")),
                           ("luch", CELESTRAK + "?NAME=LUCH-5&FORMAT=tle", lambda n: n in ("LUCH-5A", "LUCH-5B", "LUCH-5V")),
                           ("tianlian", CELESTRAK + "?NAME=TIANLIAN&FORMAT=tle", lambda n: n.startswith("TIANLIAN 2"))):
        try:
            got = _tles(_cached_get(f"relays_{net}.tle", url, RELAY_TTL, timeout=FETCH_TIMEOUT))
        except Exception:
            continue
        for name, l1, l2 in got:
            if not keep(name):
                continue
            try:
                sat = Satrec.twoline2rv(l1, l2)
                jd = julian(now)
                e, r, _v = sat.sgp4(int(jd), jd - int(jd))
            except Exception:
                continue
            if e or abs(sat.no_kozai * 1440 / (2 * math.pi) - 1.0027) > 0.01:   # (geostationary only)
                continue
            num = name.split()[-1]
            if net == "tdrs" and num.isdigit() and int(num) < MIN_TDRS:
                continue
            lat, lon = ground(r, jd)
            out.append({"name": name.title().replace("Tdrs", "TDRS"), "net": net, "lat": round(lat, 2),
                        "lon": round(lon, 2)})
    return out


def css_track(now):
    """Tiangong's track over the same span as the ISS's ([[t, lat, lon, lit], …] every 20 s), or None."""
    try:
        got = _tles(_cached_get("css.tle", f"{CELESTRAK}?CATNR={CSS_ID}&FORMAT=tle", TLE_TTL, timeout=FETCH_TIMEOUT))
        _n, l1, l2 = got[0]
        return track(l1, l2, now - BACK, horizon=HORIZON + BACK, step=20)
    except Exception:
        return None


def docked(now):
    """The spacecraft docked to the ISS: [{"name", "flag", "docked" (unix), "port"}], or None."""
    try:
        d = json.loads(_cached_get("docked.json", DOCKED_URL, CREW_TTL, timeout=FETCH_TIMEOUT))
        items = d.get("spacecraft") if isinstance(d, dict) else None
    except Exception:
        return None
    out = []
    for x in items if isinstance(items, list) else []:
        if not isinstance(x, dict) or not x.get("iss") or not x.get("name"):
            continue
        try:
            when = int(float(x.get("docked")))
        except (TypeError, ValueError):
            when = None
        out.append({"name": str(x["name"]), "flag": str(x.get("flag_code") or "").lower()[:2], "docked": when,
                    "port": x.get("docking_port")})
    return out


SWPC = "https://services.swpc.noaa.gov"
LAUNCHES_URL = "https://ll.thespacedevs.com/2.3.0/launches/upcoming/?limit=8&mode=normal"
LAUNCHES_TTL = 3600                 # (Launch Library 2's free tier: 15 requests an hour)
PAD_FLAG = {"gf": "eu", "kz": "ru", "mh": "us"}  # Kourou launches for Europe, Baikonur for Russia, Kwajalein the US


def spacewx():
    """Space weather now (NOAA SWPC): {"kp", "kp_at", "wind" km/s, "bt", "bz" nT, "xray" W/m², "scales": {"R", "S",
    "G"}}; parts that fail are left out."""
    out = {}
    get = lambda path: requests.get(SWPC + path, headers=UA, timeout=FETCH_TIMEOUT).json()
    for name, fn in (
            ("kp", lambda: get("/json/planetary_k_index_1m.json")[-1]),
            ("wind", lambda: get("/products/summary/solar-wind-speed.json")[0]),
            ("mag", lambda: get("/products/summary/solar-wind-mag-field.json")[0]),
            ("xray", lambda: [x for x in get("/json/goes/primary/xrays-6-hour.json") if x.get("energy") == "0.1-0.8nm"][-1]),
            ("scales", lambda: get("/products/noaa-scales.json")["0"])):
        try:
            d = fn()
        except Exception:
            continue
        if name == "kp":
            out.update(kp=float(d.get("estimated_kp", d.get("kp_index"))), kp_at=d.get("time_tag"))
        elif name == "wind":
            out["wind"] = float(d["proton_speed"])
        elif name == "mag":
            out.update(bt=float(d["bt"]), bz=float(d["bz_gsm"]))
        elif name == "xray":
            out["xray"] = float(d["flux"])
        else:
            out["scales"] = {k: str((d.get(k) or {}).get("Scale") or "0") for k in ("R", "S", "G")}
    return out or None


def launches(now):
    """The next launches anywhere (Launch Library 2): [{"name", "rocket", "net" (unix), "status", "pad", "lat",
    "lon", "flag", "provider"}], or None."""
    from datetime import datetime
    try:
        d = json.loads(_cached_get("launches.json", LAUNCHES_URL, LAUNCHES_TTL, timeout=FETCH_TIMEOUT))
    except Exception:
        return None
    out = []
    for r in d.get("results") or []:
        try:
            net = datetime.fromisoformat(str(r["net"]).replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError, TypeError):
            continue
        if net < now - 3600:
            continue
        pad = r.get("pad") or {}
        cc = str(((pad.get("country") or {}).get("alpha_2_code")) or "").lower()
        name = str(r.get("name") or "")
        rocket, _, payload = name.partition(" | ")
        try:
            lat, lon = float(pad.get("latitude")), float(pad.get("longitude"))
        except (TypeError, ValueError):
            lat = lon = None
        out.append({"name": payload or name, "rocket": rocket if payload else "", "net": int(net),
                    "status": str((r.get("status") or {}).get("abbrev") or ""), "pad": str(pad.get("name") or ""),
                    "lat": lat, "lon": lon, "flag": PAD_FLAG.get(cc, cc),
                    "provider": str((r.get("launch_service_provider") or {}).get("name") or "")})
    return out[:6]


def _write_grid(path, magic, grid):
    import numpy as np
    g = np.ascontiguousarray(grid, np.uint8)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(magic + int(g.shape[1]).to_bytes(2, "big") + int(g.shape[0]).to_bytes(2, "big") + g.tobytes())
    tmp.replace(path)


def aurora(out_dir):
    """NOAA's OVATION aurora forecast → issmap/aurora.bin: 181 rows (lat -90..90) × 360 (lon 0..359), 0-100."""
    import numpy as np
    r = requests.get(OVATION_URL, headers=UA, timeout=FETCH_TIMEOUT)
    r.raise_for_status()
    d = r.json()
    g = np.zeros((181, 360), np.uint8)
    for lo, la, v in d.get("coordinates") or []:
        g[int(la) + 90, int(lo) % 360] = max(0, min(100, int(v)))
    _write_grid(out_dir / "aurora.bin", b"ISA1", g)
    return d.get("Forecast Time")


def city_lights(out_dir):
    """NASA's Black Marble (VIIRS city lights, via GIBS) → issmap/lights.bin, 720×360 (lat 90..-90), each pixel the
    brightest of its 2×2 (small towns stay); fetched once every LIGHTS_TTL."""
    import numpy as np
    from PIL import Image
    p = out_dir / "lights.bin"
    tried = cache_dir() / "lights.tried"
    if p.exists() and time.time() - p.stat().st_mtime < LIGHTS_TTL:
        return False
    if tried.exists() and time.time() - tried.stat().st_mtime < LIGHTS_RETRY:
        return "waiting to try again"                 # (failed lately: not every run)
    tried.touch()
    r = requests.get(LIGHTS_URL, headers=UA, timeout=60)
    r.raise_for_status()
    img = np.asarray(Image.open(io.BytesIO(r.content)).convert("L"), np.uint8)
    h, w = img.shape
    img = img[:h // 2 * 2, :w // 2 * 2].reshape(h // 2, 2, w // 2, 2).max(axis=(1, 3))
    _write_grid(p, b"ISL1", img)
    return True


def extras(now, old=None):
    """Everything the map's extras need; each part on its own (one failing or out of time keeps what the last run
    had, from ``old``), none started after EXTRAS_BUDGET."""
    old = old if isinstance(old, dict) else {}
    out = {}
    d = paths.DATA_DIR / "issmap"
    d.mkdir(parents=True, exist_ok=True)
    kept = ("relays", "css", "docked", "people", "spacewx", "launches")      # (in iss.json: an old copy beats none)
    for name, fn in (("relays", lambda: relays(now)), ("css", lambda: css_track(now)), ("docked", lambda: docked(now)),
                     ("people", people), ("spacewx", spacewx), ("launches", lambda: launches(now)),
                     ("aurora", lambda: aurora(d)), ("lights", lambda: city_lights(d))):
        if time.time() - now > EXTRAS_BUDGET:
            out[name] = old.get(name) if name in kept else "skipped: out of time"
            continue
        try:
            out[name] = fn()
        except Exception as e:
            out[name] = old.get(name) if name in kept else f"failed: {str(e)[:120]}"
        if name in kept and not out[name]:
            out[name] = old.get(name)
    return out


def wanted(cfg) -> bool:
    """The ISS is on the wall somewhere: a webcam in space, or the ISS map in the rotation (or set up)."""
    mods = (cfg or {}).get("modules") or {}
    cams = ((mods.get("webcams") or {}).get("cams") or {})
    if any(isinstance(c, dict) and c.get("space") for c in cams.values()):
        return True
    lc = mods.get("layout_cycle") or {}
    return bool(mods.get("iss_map") or mods.get("sats_overhead")) or any(
        str((e or {}).get("panel", "")).startswith(("iss_map", "sats_overhead"))
                                            for side in ("left", "right") for e in lc.get(side) or [] if isinstance(e, dict))


def run(cfg):
    from hub.util import atomic_write_json
    if not wanted(cfg):
        return {"ok": True, "skipped": "the ISS isn't on the wall (no webcam in space, no ISS map)"}
    now = time.time()
    l1, l2 = elements()
    # from BACK before now: the wall shows video from 35 s ago (and more for a webcam that lags), so the track must
    # already cover then the moment the file lands (starting at now blanked the sections for 35 s after every run)
    tr = track(l1, l2, now - BACK, horizon=HORIZON + BACK)
    below = Below()
    over, tz = [], []
    for row in [r for r in tr if r[0] >= now - 300][::max(1, OVER_STEP // STEP)]:   # (the past is only the trail)
        over.append([row[0], below.name(row[1], row[2])])
        tz.append([row[0], below.zone(row[1], row[2])])
    c, m = crew()
    try:
        old = json.loads((paths.DATA_DIR / "iss.json").read_text())
    except Exception:
        old = {}
    out = {"at": now, "elements": [l1, l2], "track": tr, "events": events(tr), "over": over, "tz": tz,
           "crew": c, "mission": m, **{k: old.get(k) for k in ("relays", "css", "docked", "people", "spacewx", "launches")}}
    atomic_write_json(paths.DATA_DIR / "iss.json", out)          # the orbit first: the extras can't hold it up
    x = extras(now, old)
    out.update({k: x[k] for k in ("relays", "css", "docked", "people", "spacewx", "launches")})
    atomic_write_json(paths.DATA_DIR / "iss.json", out)
    return {"ok": True, "points": len(tr), "events": len(out["events"]), "crew": (c or {}).get("count"),
            "relays": len(x["relays"] or []), "css": bool(x["css"]), "docked": len(x["docked"] or []),
            "people": len(x["people"] or []), "spacewx": bool(x["spacewx"]), "launches": len(x["launches"] or []),
            "aurora": x["aurora"], "lights": x["lights"]}


if __name__ == "__main__":
    _harness.main(run)
