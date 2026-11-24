"""Satellite constellations for the Space Map (modules.iss_map.map.sats: Starlink, Amazon Leo, GPS, GOES…) →
data/issmap/sats_<layer>.bin + .json.

Only the layers switched on are fetched. CelesTrak's current elements for each (common/satlayers.py: its group or
name search), at most every TLE_TTL (CelesTrak asks for no more than every couple of hours), are propagated here with
sgp4 for the next HORIZON every few seconds (common/satlayers.STEP: 30 s for low orbits); the display interpolates
between them, so the Pi never runs sgp4 for thousands of satellites.

For layers whose satellites talk to each other by laser (``mesh``), the likely links: each satellite to the ones just
ahead and behind in its own plane and to its nearest neighbours in the planes either side, within its own shell (the
real links aren't published; this is how these networks are built).

.bin: b"SAT2", n (u32), steps (u16), pairs (u32), t0 (f64), step (f32), then lat×100 and lon×100 as int16
[steps][n], then the pairs as uint16 [pairs][2]. .json: {"at", "names", "count"}.
"""

from __future__ import annotations

import json
import math
import struct
import time

import requests

from common.orbit import julian
from common.satlayers import LAYERS, STEP
from hub import paths
from hub.jobs import _harness

CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
UA = {"User-Agent": "BigScreen/2 (home LED wall; space map)"}
TLE_TTL = 6 * 3600
BACK = 120                       # from this long before now (the wall shows the map as filmed, 35 s ago)
HORIZON = 25 * 60                # … to this long after it (the job runs every 10 min)
MAX_SATS = 65000                 # (pair indices are uint16)
MESH_KM = 3500                   # no laser link longer than this
R_EARTH = 6378.137


def cache_dir():
    d = paths.CACHE_DIR / "sats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tles(text):
    ls = [x.rstrip() for x in text.splitlines() if x.strip()]
    out = []
    for i in range(len(ls) - 2):
        if ls[i + 1].startswith("1 ") and ls[i + 2].startswith("2 "):
            out.append((ls[i].strip(), ls[i + 1], ls[i + 2]))
    return out


def elements(key):
    """[(name, l1, l2)] for a layer, from the cache when it's fresh enough (an old copy when the fetch fails)."""
    p = cache_dir() / f"{key}.tle"
    if not (p.exists() and time.time() - p.stat().st_mtime < TLE_TTL):
        try:
            r = requests.get(f"{CELESTRAK}?{LAYERS[key]['query']}&FORMAT=tle", headers=UA, timeout=60)
            if "No GP data found" in r.text:               # a layer with nothing in orbit yet (CelesTrak says 404)
                text = ""
            else:
                r.raise_for_status()
                text = r.text
            p.write_text(text)
        except Exception:
            if not p.exists():
                raise
    return _tles(p.read_text())


def propagate(tles, t0, steps, step):
    """(lat, lon) in degrees, arrays [steps, n]; NaN where sgp4 fails."""
    import numpy as np
    from sgp4.api import Satrec, SatrecArray
    from common.orbit import gmst
    arr = SatrecArray([Satrec.twoline2rv(l1, l2) for _n, l1, l2 in tles])
    ts = t0 + np.arange(steps) * step
    jd = np.array([julian(t) for t in ts])
    jd_i = np.floor(jd)
    e, r, _v = arr.sgp4(jd_i, jd - jd_i)                    # r: [n, steps, 3] km, TEME
    r = np.where(e[..., None] == 0, r, np.nan)
    g = np.array([gmst(j) for j in jd])                     # Earth's turn at each step
    lon = np.degrees(np.arctan2(r[..., 1], r[..., 0]) - g[None, :])
    lon = (lon + 540.0) % 360.0 - 180.0
    lat_gc = np.arctan2(r[..., 2], np.hypot(r[..., 0], r[..., 1]))
    lat = np.degrees(np.arctan(np.tan(lat_gc) / (1 - 0.00669438)))
    return lat.T, lon.T, r


def mesh(tles, r):
    """Likely laser links: [(i, j)] — per satellite, the nearest ahead and behind in its own plane and the nearest in
    the next plane either side, among satellites of its shell (inclination and height alike)."""
    import numpy as np
    from scipy.spatial import cKDTree
    n = len(tles)
    if n < 8:
        return []
    inc = np.array([float(l2[8:16]) for _n, _l1, l2 in tles])
    pos = r[:, 0, :]
    vel = r[:, 1, :] - r[:, 0, :]
    ok = np.isfinite(pos).all(axis=1) & np.isfinite(vel).all(axis=1)
    alt = np.linalg.norm(np.nan_to_num(pos), axis=1) - R_EARTH
    shell = np.round(inc).astype(int) * 1000 + np.round(alt / 25).astype(int)
    pairs = set()
    for s in np.unique(shell[ok]):
        idx = np.flatnonzero((shell == s) & ok & (alt > 300))
        if idx.size < 8:
            continue
        p, v = pos[idx], vel[idx]
        v = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
        tree = cKDTree(p)
        d, nb = tree.query(p, k=min(9, idx.size))
        for a in range(idx.size):
            fore = aft = left = right = None
            h = np.cross(p[a], v[a])                         # the orbit's normal: which side a neighbour is on
            h /= max(np.linalg.norm(h), 1e-9)
            for dist, b in zip(d[a][1:], nb[a][1:]):
                if dist > MESH_KM:
                    break
                rel = (p[b] - p[a]) / dist
                along, across = float(rel @ v[a]), float(rel @ h)
                if abs(along) > 0.85 and abs(float(v[b] @ v[a])) > 0.95:   # same plane: ahead / behind
                    if along > 0 and fore is None:
                        fore = b
                    elif along < 0 and aft is None:
                        aft = b
                elif abs(across) > 0.5:                      # the planes either side
                    if across > 0 and right is None:
                        right = b
                    elif across < 0 and left is None:
                        left = b
            for b in (fore, aft, left, right):
                if b is not None:
                    i, j = int(idx[a]), int(idx[b])
                    pairs.add((min(i, j), max(i, j)))
    return sorted(pairs)


def write(key, tles, now):
    import numpy as np
    step = STEP[LAYERS[key]["orbit"]]
    t0 = math.floor((now - BACK) / step) * step
    steps = int((BACK + HORIZON) // step) + 2
    tles = tles[:MAX_SATS]
    if tles:
        lat, lon, r = propagate(tles, t0, steps, step)
    else:
        lat = lon = np.zeros((steps, 0))
        r = None
    pairs = mesh(tles, r) if LAYERS[key]["mesh"] and r is not None else []
    la = np.where(np.isfinite(lat), np.round(lat * 100), 32767).astype("<i2")
    lo = np.where(np.isfinite(lon), np.round(lon * 100), 32767).astype("<i2")
    pr = np.asarray(pairs, "<u2").reshape(-1, 2)
    head = struct.pack("<4sIHIdf", b"SAT2", len(tles), steps, len(pr), float(t0), float(step))
    d = paths.DATA_DIR / "issmap"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"sats_{key}.tmp"
    tmp.write_bytes(head + la.tobytes() + lo.tobytes() + pr.tobytes())
    tmp.replace(d / f"sats_{key}.bin")
    from hub.util import atomic_write_json
    atomic_write_json(d / f"sats_{key}.json", {"at": now, "count": len(tles), "names": [n for n, _a, _b in tles]})
    return len(tles), len(pr)


def wanted(cfg):
    """The layers switched on for the Space Map."""
    mods = (cfg or {}).get("modules") or {}
    sats = (((mods.get("iss_map") or {}).get("map") or {}).get("sats") or {})
    return [k for k in LAYERS if isinstance(sats.get(k), dict) and sats[k].get("on")]


def run(cfg):
    keys = wanted(cfg)
    d = paths.DATA_DIR / "issmap"
    for k in LAYERS:                                        # layers switched off: their files go (and stop syncing)
        if k not in keys:
            for ext in ("bin", "json"):
                try:
                    (d / f"sats_{k}.{ext}").unlink()
                except OSError:
                    pass
    if not keys:
        return {"ok": True, "skipped": "no satellite layers switched on"}
    now = time.time()
    out, errs = {}, {}
    for k in keys:
        try:
            n, m = write(k, elements(k), now)
            out[k] = f"{n} sats" + (f", {m} links" if m else "")
        except Exception as e:
            errs[k] = str(e)[:160]
    return {"ok": not errs or bool(out), **out, **({"failed": errs} if errs else {}),
            "took": round(time.time() - now, 1)}


if __name__ == "__main__":
    _harness.main(run)
