"""Helpers shared by the BC alert jobs: settings with defaults, home location, output, and wall takeovers."""

from __future__ import annotations

import json
import math
import time

import requests

from hub import paths
from hub.util import atomic_write_json, read_json

UA = "BigScreen/2.0 (personal LED display in Vancouver; polls every few minutes)"
HOME_LONLAT = (-123.0918, 49.1442)

session = requests.Session()
session.headers["User-Agent"] = UA


def settings(cfg, key, defaults):
    """modules.<key> over ``defaults`` (one level of nested dicts merged)."""
    s = json.loads(json.dumps(defaults))
    for k, v in ((cfg.get("modules") or {}).get(key) or {}).items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k] = {**s[k], **v}
        else:
            s[k] = v
    return s


def home(cfg):
    """(lon, lat) of the wall's home — the weather alerts' home point."""
    hl = ((cfg.get("modules") or {}).get("weather_alerts") or {}).get("home_lonlat")
    return tuple(hl) if hl else HOME_LONLAT


def write(name, payload):
    payload = dict(payload, updated_utc=time.time())
    atomic_write_json(paths.DATA_DIR / f"{name}.json", payload)
    return payload


def cache(name, default=None):
    return read_json(paths.CACHE_DIR / f"{name}.json", default)


def save_cache(name, data):
    atomic_write_json(paths.CACHE_DIR / f"{name}.json", data)


def km(la1, lo1, la2, lo2):
    p1, p2 = math.radians(la1), math.radians(la2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, a)))


def in_box(lat, lon, box):
    """box = [w, s, e, n]"""
    return box[0] <= lon <= box[2] and box[1] <= lat <= box[3]


def simplify(ring, tol):
    """Douglas–Peucker on [[lon, lat], …] (tolerance in degrees) — keeps the JSON small for the Pi."""
    if len(ring) < 4:
        return ring
    import numpy as np
    pts = np.asarray(ring, dtype=float)
    keep = np.zeros(len(pts), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        seg = q - p
        n = math.hypot(*seg) or 1e-12
        q = pts[a + 1:b] - p
        d = np.abs(seg[0] * q[:, 1] - seg[1] * q[:, 0]) / n
        i = int(np.argmax(d))
        if d[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return [[round(x, 4), round(y, 4)] for x, y in pts[keep]]


def takeover(panel, ids, state_name, log=print, entry_extra=None):
    """Put ``panel`` on the wall now (played once: every item, then the rotation resumes) for item ids not
    taken over before. The data is pushed to the Pi first so the screen has it when it starts."""
    first = cache(state_name) is None
    seen = cache(state_name, {}) or {}
    new = [i for i in ids if i not in seen]
    if not new:
        return False
    if first:                                             # first run: what's already in effect isn't news
        save_cache(state_name, {i: time.time() for i in ids})
        log(f"takeover {panel}: {len(ids)} already in effect at first run, not taken over")
        return False
    import os
    if os.environ.get("BIGSCREEN_NO_PI"):                  # dev / test runs never touch the wall
        log(f"takeover {panel} for {', '.join(new)} (skipped: BIGSCREEN_NO_PI)")
        return False
    from hub import pi, sync
    try:
        sync._push_once()
        code, body = pi.display_post("/play", {"side": "right", "entry": {"panel": panel, "advance": "cycles", "cycles": 1,
                                                                           "max_sec": 900, **(entry_extra or {})}, "hold": False}, timeout=4)
        log(f"takeover {panel} for {', '.join(new)}: {code} {body}")
        if code >= 400:
            return False
    except Exception as e:
        log(f"takeover {panel} failed: {e}")
        return False
    now = time.time()
    seen.update({i: now for i in new})
    seen = {k: v for k, v in seen.items() if now - v < 14 * 86400}
    save_cache(state_name, seen)
    return True
