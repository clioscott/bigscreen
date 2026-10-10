"""NIED Kyoshin Monitor (強震モニタ) helpers: station table, colour ↔ intensity, image decoding.

NIED publishes a live image every second in which each strong-motion station (K-NET/KiK-net)
is a coloured pixel; the colour encodes the real-time seismic intensity on a continuous scale
from -3 to 7. The station table (pixel position + latitude/longitude) comes from
ingen084/kyoshin-monitor-observation-points (MIT); the colour → value polynomial is the one
KyoshinMonitorLib (MIT) uses.
"""

from __future__ import annotations

import base64
import colorsys
import json
import math
import os
import random

import numpy as np

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POINTS_PATH = os.path.join(_HERE, "assets", "data", "kmoni_points.json")
_POINTS = None


def points():
    """[(code, lat, lon, img_x, img_y, region)] — 1,600-odd stations."""
    global _POINTS
    if _POINTS is None:
        _POINTS = json.load(open(POINTS_PATH, encoding="utf-8"))["points"]
    return _POINTS


def colour_to_intensity(rgb):
    """Kyoshin Monitor colour → real-time intensity (-3..7), or None for no data."""
    r, g, b = (v / 255.0 for v in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if v <= 0.1 or s <= 0.75:
        return None
    if h > 0.1476:
        p = 280.31 * h ** 6 - 916.05 * h ** 5 + 1142.6 * h ** 4 - 709.95 * h ** 3 + 234.65 * h ** 2 - 40.27 * h + 3.2217
    elif h > 0.001:
        p = 151.4 * h ** 4 - 49.32 * h ** 3 + 6.753 * h ** 2 - 2.481 * h + 0.9033
    else:
        p = -0.005171 * v ** 2 - 0.3282 * v + 1.2236
    return max(0.0, min(1.0, p)) * 10 - 3


# the monitor's look: deep blue (quiet) → green → yellow → red → magenta (violent)
_STOPS = [(-3, (0, 0, 170)), (-2, (0, 60, 255)), (-1, (0, 170, 255)), (0, (0, 220, 170)), (1, (50, 230, 60)),
          (2, (190, 240, 0)), (3, (255, 235, 0)), (4, (255, 160, 0)), (5, (255, 80, 0)), (6, (240, 0, 0)), (7, (170, 0, 90))]


def intensity_colour(i):
    if i <= _STOPS[0][0]:
        return _STOPS[0][1]
    for (a, ca), (b, cb) in zip(_STOPS, _STOPS[1:]):
        if i <= b:
            t = (i - a) / (b - a)
            return tuple(int(ca[k] + (cb[k] - ca[k]) * t) for k in range(3))
    return _STOPS[-1][1]


def decode_image(rgba):
    """Station values from a Kyoshin Monitor image (H, W, 4) → list of intensity or None, in table order."""
    out = []
    h, w = rgba.shape[:2]
    for _, _, _, x, y, _ in points():
        if 0 <= x < w and 0 <= y < h and rgba[y, x, 3] > 0:
            out.append(colour_to_intensity(tuple(int(v) for v in rgba[y, x, :3])))
        else:
            out.append(None)
    return out


def encode(values):
    """Intensities → compact base64 (one byte each: 0 = no data, else (i+3)*20+1)."""
    b = bytes(0 if v is None else max(1, min(255, int(round((v + 3) * 20)) + 1)) for v in values)
    return base64.b64encode(b).decode()


def decode(s):
    return [None if c == 0 else (c - 1) / 20.0 - 3 for c in base64.b64decode(s)]


def synthetic(ev, now, seed=0):
    """Plausible station readings for a simulated event: quiet background noise, rising as the
    P wave passes and reaching the predicted intensity behind the S wave (then slowly decaying)."""
    from common import quake_calc as qc
    rnd = random.Random(seed + int(now))
    t = now - float(ev["origin"])
    out = []
    for code, la, lo, _, _, _ in points():
        d = qc.haversine_km(ev["lat"], ev["lon"], la, lo)
        target = qc.intensity_value(ev["mag"], ev["depth"], d) + (hash(code) % 100 - 50) / 70.0
        tp, ts = qc.arrival_s(d, ev["depth"], qc.VP), qc.arrival_s(d, ev["depth"], qc.VS)
        noise = -2.6 + rnd.random() * 0.6
        if t < tp:
            v = noise
        elif t < ts:
            v = max(noise, target - 1.6 + 0.6 * (t - tp) / max(1.0, ts - tp))
        else:
            decay = max(0.0, (t - ts) / 60.0)
            v = max(noise, target - decay * 1.5 + rnd.random() * 0.3)
        out.append(v)
    return out
