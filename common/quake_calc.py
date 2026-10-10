"""Earthquake maths shared by the hub (listener) and the display (EEW screen).

* wave fronts: P ≈ 6.0 km/s, S ≈ 3.5 km/s from the hypocentre (straight rays — what EEW
  viewer apps draw; real fronts depend on depth tables, this is close for display purposes)
* predicted JMA seismic intensity at a site, the usual EEW-app estimate:
    Si & Midorikawa (1999) PGV on stiff soil (Vs=600 m/s), × 1.4 site amplification,
    then Midorikawa et al. (1999) intensity = 2.68 + 1.72·log10(PGV)
"""

from __future__ import annotations

import math

VP, VS = 6.0, 3.5                     # km/s
SITES = {"Tokyo": (35.6895, 139.6917), "Yokohama": (35.4437, 139.6380)}
# JMA shindo classes and the colours EEW apps use
SHINDO = ["0", "1", "2", "3", "4", "5-", "5+", "6-", "6+", "7"]
SHINDO_JP = {"0": "0", "1": "1", "2": "2", "3": "3", "4": "4", "5-": "5弱", "5+": "5強", "6-": "6弱", "6+": "6強", "7": "7"}
SHINDO_COLOURS = {"0": (110, 110, 120), "1": (130, 165, 210), "2": (40, 130, 235), "3": (40, 200, 100), "4": (255, 215, 0),
                  "5-": (255, 150, 0), "5+": (255, 100, 0), "6-": (255, 40, 30), "6+": (200, 0, 40), "7": (160, 0, 110)}


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, a)))


def front_radius_km(elapsed_s, depth_km, v):
    """Surface radius of a wave front ``elapsed_s`` after origin (0 until it reaches the surface)."""
    d = v * max(0.0, elapsed_s)
    return math.sqrt(d * d - depth_km * depth_km) if d > depth_km else 0.0


def arrival_s(dist_km, depth_km, v):
    return math.hypot(dist_km, depth_km) / v


def intensity_value(mag, depth_km, dist_km, amp=1.4):
    """Predicted JMA instrumental intensity (float) at epicentral distance ``dist_km``."""
    mw = min(mag, 8.3)                                      # the formula's calibrated range
    # big quakes rupture a long fault: use (roughly) the distance to the fault, not to the epicentre
    half_fault = 0.5 * 10 ** (0.5 * mag - 1.85) if mag >= 6.5 else 0.0
    x = max(3.0, math.hypot(max(0.0, dist_km - half_fault), depth_km))
    log_pgv = 0.58 * mw + 0.0038 * depth_km - 1.29 - math.log10(x + 0.0028 * 10 ** (0.5 * mw)) - 0.002 * x
    pgv = (10 ** log_pgv) * amp
    return 2.68 + 1.72 * math.log10(max(1e-6, pgv))


def shindo_class(i):
    """JMA class from an instrumental intensity value."""
    if i < 0.5:
        return "0"
    for lim, cls in ((1.5, "1"), (2.5, "2"), (3.5, "3"), (4.5, "4"), (5.0, "5-"), (5.5, "5+"), (6.0, "6-"), (6.5, "6+")):
        if i < lim:
            return cls
    return "7"


def class_from_text(s):
    """'5弱' / '5-' / '6+' / '4' → class key (or '0')."""
    s = str(s or "").strip().replace("弱", "-").replace("強", "+").replace("不明", "")
    return s if s in SHINDO else "0"


def class_rank(cls):
    return SHINDO.index(cls) if cls in SHINDO else 0


def site_forecast(ev, now):
    """Per site: distance, predicted class, seconds until the S wave (negative once it has passed)."""
    out = {}
    for name, (la, lo) in SITES.items():
        d = haversine_km(ev["lat"], ev["lon"], la, lo)
        i = intensity_value(ev["mag"], ev["depth"], d)
        out[name] = {"dist_km": d, "intensity": i, "class": shindo_class(i),
                     "s_in": arrival_s(d, ev["depth"], VS) - (now - ev["origin"])}
    return out
