"""Time zone map, hub side: the newest rules, the zones' boundaries and the map rasters (hub/jobs/timezones.py runs it,
display/modules/tzmap.py draws it).

Rules: IANA's tz database. The PyPI ``tzdata`` package (usually out within days of an IANA release) is checked on
every run and its zoneinfo kept in the cache; the system's /usr/share/zoneinfo is used instead when it is newer or
PyPI can't be reached. North American rules are changing a lot right now (BC, Alberta, the NWT and Manitoba all stop
changing clocks on 2026-11-01), so a new release can change the map before any clock change: every new version is
compared with the last one and the differences kept for the control panel.

Boundaries: timezone-boundary-builder (built from OpenStreetMap, ODbL). The latest release's shapefile is downloaded
once per release (~80 MB) and cut down to the US and Canada zones (~1 MB in the cache). States, provinces, the
border and lakes come from Natural Earth (public domain), and the coastline from OpenStreetMap's simplified water
polygons (ODbL), both in the geodata the weather maps already use.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import shutil
import tempfile
import time
import zipfile
import zlib
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from hub import paths
from hub.weather.geo import GEODATA_ROOT, UA

CACHE = paths.CACHE_DIR / "tzmap"
TZDATA_DIR = CACHE / "tzdata"
SYSTEM_ZONEINFO = Path("/usr/share/zoneinfo")
PYPI = "https://pypi.org/pypi/tzdata/json"
TBB_LATEST = "https://api.github.com/repos/evansiroky/timezone-boundary-builder/releases/latest"
NE_DIR = GEODATA_ROOT / "borders"
COUNTRIES = ("US", "CA")
# zones left off the map (and so the list): Alaska and Hawaii
MAP_DROP = {"Pacific/Honolulu", "America/Adak", "America/Anchorage", "America/Juneau", "America/Sitka",
            "America/Metlakatla", "America/Yakutat", "America/Nome"}
REGION = (-180.0, 17.0, -50.0, 75.0)          # lon/lat box the zones are cut to before simplifying
SIMPLIFY_DEG = 0.01
SS = 3                                         # supersampling when rasterising
WATER_LINE_PX = 2                              # how far offshore the lines between zones carry on
RASTER_VERSION = 11                            # bump when rasterising changes


def _get(url, timeout=30, **kw):
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": UA}, **kw)
    r.raise_for_status()
    return r


# --- rules ------------------------------------------------------------------------------------------------------------
def _version_of(root: Path) -> str | None:
    """IANA version of a zoneinfo directory (from tzdata.zi's first line)."""
    try:
        with open(root / "tzdata.zi", encoding="utf-8") as f:
            m = re.match(r"#\s*version\s+(\d{4}[a-z]+)", f.readline())     # "2026b-dirty" (some wheels) → 2026b
        return m.group(1) if m else None
    except OSError:
        return None


def _cached_versions():
    out = {}
    if TZDATA_DIR.is_dir():
        for d in TZDATA_DIR.iterdir():
            if d.name.startswith(".") or not (d / "zoneinfo" / "zone.tab").exists():   # unfinished extraction
                continue
            v = _version_of(d / "zoneinfo")
            if v:
                out[v] = d / "zoneinfo"
    return out


def check_tzdata() -> dict:
    """Fetch the newest tzdata from PyPI when it's newer than what we have. {latest, error}."""
    have = _cached_versions()
    try:
        info = _get(PYPI, timeout=20).json()
        wheel = next(u for u in info["urls"] if u["filename"].endswith(".whl"))
        pkg = info["info"]["version"]
        d = TZDATA_DIR / pkg
        if not (d / "zoneinfo" / "zone.tab").exists():
            raw = _get(wheel["url"], timeout=60).content
            want = (wheel.get("digests") or {}).get("sha256")
            if not want or hashlib.sha256(raw).hexdigest() != want:
                raise ValueError("the tzdata wheel doesn't match PyPI's sha256")
            TZDATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp = Path(tempfile.mkdtemp(dir=TZDATA_DIR, prefix=".tmp-"))
            try:
                base = (tmp / "zoneinfo").resolve()
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    for name in z.namelist():
                        rel = name[len("tzdata/zoneinfo/"):]
                        if not name.startswith("tzdata/zoneinfo/") or not rel or name.endswith(("/", ".py", ".pyc")):
                            continue
                        out = (base / rel).resolve()
                        if base not in out.parents:                 # absolute or climbing names stay out
                            continue
                        out.parent.mkdir(parents=True, exist_ok=True)
                        out.write_bytes(z.read(name))
                if d.exists():
                    shutil.rmtree(d)
                tmp.replace(d)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        latest = _version_of(d / "zoneinfo")
        for v, p in sorted(_cached_versions().items())[:-3]:      # keep the newest three
            shutil.rmtree(p.parent, ignore_errors=True)
        return {"latest": latest, "pypi": pkg, "error": ""}
    except Exception as e:
        return {"latest": max(have) if have else None, "pypi": None, "error": f"PyPI: {e}"}


def rules_root() -> tuple[Path, str, str]:
    """(zoneinfo dir, IANA version, source) — the newest of the cached PyPI tzdata and the system's."""
    best = (None, "", "")
    for v, p in _cached_versions().items():
        if v > best[1]:
            best = (p, v, "PyPI tzdata")
    sv = _version_of(SYSTEM_ZONEINFO)
    if sv and sv > best[1]:
        best = (SYSTEM_ZONEINFO, sv, "system")
    if best[0] is None:
        import tzdata                                    # the hub venv's own copy, if nothing else
        p = Path(tzdata.__file__).parent / "zoneinfo"
        best = (p, _version_of(p) or getattr(tzdata, "IANA_VERSION", "?"), "tzdata package")
    return best


def zone_ids(root: Path) -> dict[str, str]:
    """US and Canada zones (zone.tab): {tzid: country code}."""
    out = {}
    for line in (root / "zone.tab").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cc, _, tzid = line.split("\t")[:3]
        if cc in COUNTRIES:
            out[tzid.strip()] = cc
    return out


def zone(root: Path, tzid: str) -> ZoneInfo:
    if ".." in tzid:
        raise ValueError(tzid)
    with open(root / tzid, "rb") as f:
        return ZoneInfo.from_file(f, key=tzid)


def _offset(z, t):
    from datetime import datetime, timezone
    d = datetime.fromtimestamp(t, timezone.utc).astimezone(z)
    return int(d.utcoffset().total_seconds() // 60), d.tzname() or ""


def schedule(z, t0: float, t1: float, step=3600) -> list:
    """[[ts, offset minutes, abbreviation], …]: what's in effect at t0, then every change up to t1 (to the minute)."""
    cur = _offset(z, t0)
    out = [[int(t0), *cur]]
    t = t0
    while t < t1:
        n = t + step
        o = _offset(z, n)
        if o != cur:
            lo, hi = t, n                                   # bisect to the minute
            while hi - lo > 60:
                mid = (lo + hi) / 2
                if _offset(z, mid) == cur:
                    lo = mid
                else:
                    hi = mid
            hi = int(hi // 60 * 60)
            while _offset(z, hi - 60) != cur and hi - 60 > t:
                hi -= 60
            out.append([hi, *o])
            cur = o
        t = n
    return out


def rule_diff(old: dict, new: dict, t0: float, t1: float = float("inf")) -> list:
    """Zones whose offsets differ between two versions' schedules in [t0, t1] (t1: where the shorter of the two
    schedules ends, so changes past it aren't mistaken for new rules): [{zone, at, old, new}]."""
    def at(sched, t):
        cur = sched[0]
        for s in sched:
            if s[0] <= t:
                cur = s
        return cur[1]
    out = []
    for tzid in sorted(set(old) | set(new)):
        a, b = old.get(tzid), new.get(tzid)
        if not a or not b:
            out.append({"zone": tzid, "at": None, "old": a and at(a, t0), "new": b and at(b, t0),
                        "what": "added" if b else "removed"})
            continue
        times = sorted({s[0] for s in a + b if t0 <= s[0] <= t1} | {int(t0)})
        for t in times:
            if at(a, t) != at(b, t):
                out.append({"zone": tzid, "at": t, "old": at(a, t), "new": at(b, t), "what": "changed"})
                break
    return out


# --- boundaries -------------------------------------------------------------------------------------------------------
def latest_boundaries_tag() -> str | None:
    return _get(TBB_LATEST, timeout=20).json().get("tag_name")


def boundaries(keep: set, tag: str | None) -> tuple[dict, str]:
    """The US/Canada zones' polygons for a timezone-boundary-builder release (downloaded and cut down once per
    release): ({"tag", "zones": {tzid: [[exterior, hole, …], …]}} with rings as [[lon, lat], …], error). When the
    newest release can't be had, the last one cached is used (a zone it lacks just isn't drawn)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    have = sorted(CACHE.glob("zones-*.json.gz"))
    load = lambda p: json.loads(gzip.decompress(p.read_bytes()))
    if tag is None:
        if have:
            return load(have[-1]), ""
        raise RuntimeError("no time zone boundaries yet and GitHub can't be reached")
    path = CACHE / f"zones-{tag}.json.gz"
    if path.exists():
        return load(path), ""
    try:
        return _fetch_boundaries(tag, keep, path, have), ""
    except Exception as e:
        if have:
            return load(have[-1]), f"boundaries {tag}: {e}"
        raise


def _fetch_boundaries(tag, keep, path, have):
    url = f"https://github.com/evansiroky/timezone-boundary-builder/releases/download/{tag}/timezones.shapefile.zip"
    tmp = Path(tempfile.mkdtemp(dir=CACHE, prefix=".tmp-"))
    try:
        zp = tmp / "tz.zip"
        with _get(url, timeout=600, stream=True) as r, open(zp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        with zipfile.ZipFile(zp) as z:
            for name in z.namelist():
                if name.endswith((".shp", ".shx", ".dbf")) and "/" not in name.strip("/") and ".." not in name:
                    (tmp / ("tz" + name[name.rindex("."):])).write_bytes(z.read(name))
        zp.unlink()
        pack = {"tag": tag, "zones": _cut(tmp / "tz", keep)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    part = path.with_suffix(".part")
    part.write_bytes(gzip.compress(json.dumps(pack, separators=(",", ":")).encode()))
    part.replace(path)
    for p in have:
        if p != path:
            p.unlink(missing_ok=True)
    return pack


def _cut(shp_base: Path, keep: set) -> dict:
    import shapefile
    from shapely.geometry import box, shape as to_shape
    region = box(*REGION)
    out = {}
    r = shapefile.Reader(str(shp_base))
    for i, rec in enumerate(r.iterRecords()):
        tzid = rec[0]
        if tzid not in keep:
            continue
        g = to_shape(r.shape(i).__geo_interface__).intersection(region).simplify(SIMPLIFY_DEG, preserve_topology=True)
        polys = [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]
        out[tzid] = [[[[round(x, 4), round(y, 4)] for x, y in ring.coords]
                      for ring in (p.exterior, *p.interiors)] for p in polys if not p.is_empty]
    return out


def _ne_lines(name, keep=None):
    """Natural Earth lines in the region as lists of (lon, lat) arrays (``keep(record)`` filters)."""
    import shapefile
    p = NE_DIR / name
    if not p.with_suffix(".shp").exists():
        return []
    out = []
    r = shapefile.Reader(str(p))
    for sr in r.iterShapeRecords():
        bb = sr.shape.bbox
        if bb[2] < REGION[0] or bb[0] > REGION[2] or bb[3] < REGION[1] or bb[1] > REGION[3]:
            continue
        if keep and not keep(sr.record):
            continue
        pts, parts = sr.shape.points, list(sr.shape.parts) + [len(sr.shape.points)]
        for a, b in zip(parts[:-1], parts[1:]):
            out.append(np.asarray(pts[a:b], float))
    return out


def _ne_lakes():
    import shapefile
    p = NE_DIR / "ne_10m_lakes"
    if not p.with_suffix(".shp").exists():
        return []
    out = []
    r = shapefile.Reader(str(p))
    for sr in r.iterShapeRecords():
        bb = sr.shape.bbox
        rank = sr.record["scalerank"]
        # the biggest lakes (the Great Lakes, Winnipeg) are rank 0: not "no rank"
        if (9 if rank is None else rank) > 3 or bb[2] < REGION[0] or bb[0] > REGION[2] or bb[3] < REGION[1] \
                or bb[1] > REGION[3]:
            continue
        pts, parts = sr.shape.points, list(sr.shape.parts) + [len(sr.shape.points)]
        out.append([np.asarray(pts[a:b], float) for a, b in zip(parts[:-1], parts[1:])])
    return out


def _is_hole(ring) -> bool:
    """A shapefile polygon's holes (a lake's islands) run counter-clockwise, its outer rings clockwise."""
    x, y = ring[:, 0], ring[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) > 0


SEA_BOX = (-142.0, 22.0, -50.0, 67.0)          # lon/lat the map can show (the sea is read for this box only)


def _sea():
    """The sea, as [[ring, …], …] in lon/lat (OpenStreetMap's simplified water polygons, the copy the weather maps
    use). The zone boundaries include territorial waters (12 nautical miles off every coast), which fills in the
    Gulf of St. Lawrence, the Bay of Fundy and the like; the sea is cut back out of them."""
    from hub.weather import carto
    from hub.weather.geo import R, to_merc
    shp = carto.WATER_SIMPLE
    import shapefile
    boxes = carto._shp_bbox_index(str(shp))
    x0, y0 = to_merc(SEA_BOX[0], SEA_BOX[1])
    x1, y1 = to_merc(SEA_BOX[2], SEA_BOX[3])
    hit = np.flatnonzero((boxes[:, 0] <= x1) & (boxes[:, 2] >= x0) & (boxes[:, 1] <= y1) & (boxes[:, 3] >= y0))
    r = shapefile.Reader(str(shp))
    out = []
    for i in hit:
        s = r.shape(int(i))
        pts = np.asarray(s.points, float)
        lon = np.degrees(pts[:, 0] / R)
        lat = np.degrees(2 * np.arctan(np.exp(pts[:, 1] / R)) - np.pi / 2)
        ll = np.column_stack((lon, lat))
        parts = list(s.parts) + [len(pts)]
        out.append([ll[a:b] for a, b in zip(parts[:-1], parts[1:]) if b - a >= 3])
    return out


def sea_source() -> str | None:
    """The sea shapefile's name and date (part of the rasters' signature, so they're rebuilt when it appears or
    changes), or None when it's missing."""
    from hub.weather import carto
    shp = carto.WATER_SIMPLE
    try:
        return f"{shp.name}:{int(shp.stat().st_mtime)}:{int(shp.with_suffix('.shx').stat().st_mtime)}"
    except OSError:
        return None


def geodata():
    """Province/state lines, the border, lakes and the sea for rasterising, and an error when the sea is missing
    (the coasts then include the territorial waters)."""
    err = ""
    try:
        sea = _sea() if sea_source() else []
        if not sea:
            err = "no sea polygons (osm_water_cache): coasts include territorial waters"
    except Exception as e:                       # a broken shapefile: draw without it, and say so
        sea, err = [], f"sea polygons: {e}"
    return {"prov": _ne_lines("ne_50m_admin_1_states_provinces_lines", lambda rec: rec["ADM0_A3"] in ("USA", "CAN")),
            "country": _ne_lines("ne_50m_admin_0_boundary_lines_land"),
            "lakes": _ne_lakes(), "sea": sea, "error": err}


# --- rasters ------------------------------------------------------------------------------------------------------------
def rasterize(zw, zh, pack, zone_list, geo):
    """The map for a zw×zh zone: (idx (zone index + 1 per pixel, 0 = water / outside), lines (1 province/state,
    2 country), frames, map rect, water (the zone a water pixel belongs to: lakes and territorial waters, so the
    lines between offsets and the border carry on through the Great Lakes and the straits; 0 elsewhere))."""
    from PIL import Image, ImageDraw
    from display.modules import tzmap
    rect = tzmap.layout(zw, zh)["map"]
    mw, mh = rect[2], rect[3]
    frames = tzmap.frames(mw, mh)
    f = frames[0]
    W, H = mw * SS, mh * SS
    idx_ss = np.zeros((H, W), np.uint8)
    order = {z: i + 1 for i, z in enumerate(zone_list)}

    def px(ring, k=SS):
        x, y = tzmap.to_px(f, ring[:, 0], ring[:, 1])
        return list(zip((x * k).tolist(), (y * k).tolist()))

    # big zones first, so small ones drawn later stay visible where simplified edges overlap
    for tzid, polys in sorted(pack["zones"].items(), key=lambda kv: -sum(len(r[0]) for r in kv[1])):
        if tzid not in order:
            continue
        im = Image.new("1", (W, H), 0)
        d = ImageDraw.Draw(im)
        for poly in polys:
            d.polygon(px(np.asarray(poly[0], float)), fill=1)
            for hole in poly[1:]:
                d.polygon(px(np.asarray(hole, float)), fill=0)
        idx_ss[np.asarray(im, bool)] = order[tzid]
    zoned_ss = idx_ss.copy()                                   # the zones before the water is cut out
    lake_ss = None
    for key in ("sea", "lakes"):                               # the sea and lakes, each with its islands kept land
        water = Image.new("1", (W, H), 0)
        d = ImageDraw.Draw(water)
        for parts in geo.get(key) or []:
            for ring in sorted(parts, key=_is_hole):
                d.polygon(px(ring), fill=0 if _is_hole(ring) else 1)
        wm = np.asarray(water, bool)
        idx_ss[wm] = 0
        if key == "lakes":
            lake_ss = wm

    # down to LED pixels: land if most of the pixel is, in the zone that covers most of it
    idx = tidy(_down(idx_ss, mw, mh))
    # the zones' water the lines may cross: lakes, and sea within WATER_LINE_PX of a coast (straits, not the
    # middle of Hudson Bay, which is all Nunavut's)
    from scipy import ndimage
    lake = _down(lake_ss.astype(np.uint8), mw, mh) > 0
    near = ndimage.binary_dilation(idx > 0, iterations=WATER_LINE_PX)
    water = np.where((idx == 0) & (lake | near), _down(zoned_ss, mw, mh), 0).astype(np.uint8)

    lines = np.zeros((mh, mw), np.uint8)
    for bit, key in ((1, "prov"), (2, "country")):
        im = Image.new("1", (mw, mh), 0)
        d = ImageDraw.Draw(im)
        for line in geo[key]:
            x, y = tzmap.to_px(f, line[:, 0], line[:, 1])
            pts = list(zip(np.floor(x).tolist(), np.floor(y).tolist()))
            if len(pts) > 1:
                d.line(pts, fill=1)
        lines[solid(np.asarray(im, bool)) & (idx > 0)] |= bit
    return idx, lines, frames, list(rect), water


def _down(ss, mw, mh):
    """Supersampled zone indices → LED pixels: covered if most of the pixel is, by the zone covering most of it."""
    blocks = ss.reshape(mh, SS, mw, SS).transpose(0, 2, 1, 3).reshape(mh, mw, SS * SS)
    covered = (blocks > 0).sum(axis=2) * 2 >= SS * SS
    best = np.zeros((mh, mw), np.uint8)
    bestn = np.zeros((mh, mw), np.int16)
    for z in np.unique(blocks):
        if z == 0:
            continue
        n = (blocks == z).sum(axis=2)
        better = n > bestn
        best[better], bestn[better] = z, n[better]
    return np.where(covered, best, 0).astype(np.uint8)


def tidy(idx, speck=2, island=2):
    """Smooth the coast for LED pixels (OpenStreetMap's coast is far finer than a pixel, so it comes out frayed):
    land pixels that a 3×3 majority calls water go (spurs, ragged points), but water is never filled, so straits
    and inlets stay open (the Strait of Georgia, the Detroit River). An island the trim would mostly lose
    (``island`` px or more: Anticosti, PEI, the Florida Keys, a chain counted with its corners) comes back as it was.
    Then land specks under ``speck`` px go and enclosed water specks under it fill, taking the nearest zone."""
    from scipy import ndimage
    land = idx > 0
    if not land.any():
        return idx
    corners = np.ones((3, 3), bool)
    smooth = land & (ndimage.uniform_filter(land.astype(np.float32), 3, mode="nearest") * 9 >= 4.5)
    lab, n = ndimage.label(land, structure=corners)
    if n:
        sizes = ndimage.sum(land, lab, range(1, n + 1))
        kept = ndimage.sum(smooth, lab, range(1, n + 1))
        lost = np.flatnonzero((sizes >= island) & (kept < 0.5 * sizes)) + 1
        smooth |= np.isin(lab, lost)
    for keep in (True, False):                                 # land specks, then water specks (the mask as it is now)
        mask = smooth if keep else ~smooth
        lab, n = ndimage.label(mask, structure=corners if keep else None)
        if n:
            sizes = ndimage.sum(mask, lab, range(1, n + 1))
            smooth[np.isin(lab, np.flatnonzero(sizes < speck) + 1)] = not keep
    _, (iy, ix) = ndimage.distance_transform_edt(~land, return_indices=True)
    return np.where(smooth, idx[iy, ix], 0).astype(np.uint8)


def solid(mask):
    """A 1 px line with no diagonal-only steps (they read as a broken, dotted line on the LEDs): where two pixels
    touch only at a corner, the pixel beside the lower one fills the gap."""
    m = mask.copy()
    down_right = mask[:-1, :-1] & mask[1:, 1:] & ~mask[:-1, 1:] & ~mask[1:, :-1]
    m[1:, :-1] |= down_right
    down_left = mask[:-1, 1:] & mask[1:, :-1] & ~mask[:-1, :-1] & ~mask[1:, 1:]
    m[1:, 1:] |= down_left
    return m


def anchors(idx, offs, ccs):
    """Where to write each country's offsets' times on the map: [[country, offset, x, y, room], …] at the middle of
    its roomiest area (room = distance to the nearest pixel of another offset, country or water)."""
    from scipy import ndimage
    o = np.array([-99999] + list(offs))[idx]
    k = np.array([""] + list(ccs))[idx]
    out = []
    for cc, off in sorted(set(zip(ccs, offs))):
        mask = (o == off) & (k == cc)
        if not mask.any():
            continue
        # wide labels: count room in x half as much
        dt = ndimage.distance_transform_edt(np.pad(mask, 1), sampling=(1.0, 0.55))[1:-1, 1:-1]
        score = dt.copy()
        score[:idx.shape[0] // 4] *= 0.6                    # rather the provinces and states than the territories
        y, x = np.unravel_index(int(np.argmax(score)), dt.shape)
        out.append([cc, int(off), int(x), int(y), round(float(dt[y, x]), 1)])
    return out


def area(polys) -> float:
    """Rough area of a zone in square degrees of longitude at the equator (for weighting names)."""
    total = 0.0
    for poly in polys:
        for k, ring in enumerate(poly):
            r = np.asarray(ring, float)
            x = r[:, 0] * np.cos(np.radians(r[:, 1]))
            a = 0.5 * abs(float(np.dot(x, np.roll(r[:, 1], 1)) - np.dot(r[:, 1], np.roll(x, 1))))
            total += -a if k else a
    return round(total, 2)


def crc(arr) -> int:
    return zlib.crc32(np.ascontiguousarray(arr).tobytes())
