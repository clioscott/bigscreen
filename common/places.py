"""Reference places for the alert maps, shared by the hub (zoomed basemaps) and the display (map crops).

A zoomed alert map keeps home in view when it can, so an area is always seen in relation to the house. When
home is too far away (fitting it would zoom out until the alert area is a speck), the map keeps the biggest
nearby BC city in view instead; failing that, the city nearest the area.
"""

from __future__ import annotations

import math

R = 6378137.0
# (name, lat, lon, population — rough, only used to rank). Vancouver is home's own reference, so not listed.
CITIES = [
    ("Victoria", 48.4284, -123.3656, 400000), ("Kelowna", 49.8880, -119.4960, 220000),
    ("Abbotsford", 49.0504, -122.3045, 200000), ("Nanaimo", 49.1659, -123.9401, 115000),
    ("Kamloops", 50.6745, -120.3273, 115000), ("Chilliwack", 49.1579, -121.9515, 100000),
    ("Prince George", 53.9171, -122.7497, 90000), ("Vernon", 50.2671, -119.2720, 65000),
    ("Courtenay", 49.6841, -124.9904, 60000), ("Penticton", 49.4991, -119.5937, 45000),
    ("Duncan", 48.7787, -123.7079, 45000), ("Campbell River", 50.0244, -125.2475, 40000),
    ("Cranbrook", 49.5097, -115.7688, 27000), ("Squamish", 49.7016, -123.1558, 25000),
    ("Port Alberni", 49.2339, -124.8055, 25000), ("Fort St. John", 56.2524, -120.8466, 21000),
    ("Salmon Arm", 50.7001, -119.2838, 20000), ("Powell River", 49.8352, -124.5247, 20000),
    ("Williams Lake", 52.1417, -122.1417, 20000), ("Whistler", 50.1163, -122.9574, 14000),
    ("Terrace", 54.5182, -128.6032, 12000), ("Prince Rupert", 54.3150, -130.3208, 12000),
    ("Dawson Creek", 55.7596, -120.2353, 12000), ("Nelson", 49.4928, -117.2948, 11000),
    ("Quesnel", 52.9784, -122.4927, 10000), ("Sechelt", 49.4742, -123.7545, 10000),
    ("Castlegar", 49.3237, -117.6593, 8000), ("Trail", 49.0966, -117.7117, 8000),
    ("Revelstoke", 50.9981, -118.1957, 8000), ("Merritt", 50.1113, -120.7862, 7000),
    ("Hope", 49.3858, -121.4419, 6000), ("Oliver", 49.1828, -119.5503, 5000),
    ("Osoyoos", 49.0323, -119.4682, 5000), ("Smithers", 54.7804, -127.1743, 5000),
    ("Port Hardy", 50.7224, -127.4969, 4000), ("Golden", 51.2985, -116.9631, 4000),
    ("Grand Forks", 49.0300, -118.4400, 4000), ("Princeton", 49.4590, -120.5062, 3000),
    ("Pemberton", 50.3192, -122.8035, 3000), ("Tofino", 49.1530, -125.9066, 2500),
    ("Lillooet", 50.6868, -121.9422, 2300), ("Clearwater", 51.6500, -120.0333, 2300),
    ("100 Mile House", 51.6418, -121.2940, 2000),
]
MAX_ZOOM_OUT = 3.0          # a reference may widen a zoomed map this much (the area keeps ≥ 1/3 of its size) …
MIN_AREA_PX = 30            # … or, for areas zoomed in to the limit, until the area is this many pixels across


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, a)))


TIERS = (50000, 10000, 0)    # city sizes: a bigger city wins over a smaller one that fits more tightly


def reference(cost, home, near):
    """The place a map keeps in view. ``cost((lat, lon))`` is how much the map must widen to show it (None when it
    can't): home if it fits, else the city of the biggest size tier that fits, the cheapest one in that tier, else
    the place (home or a city) nearest ``near`` (lat, lon). Returns {"name", "lat", "lon", "home"}; on the map the
    house marks home (its name is only used when the map points to it from an edge)."""
    if home and cost(home) is not None:
        return {"name": "Vancouver", "lat": home[0], "lon": home[1], "home": True}
    ok = [(cost((ct[1], ct[2])), ct) for ct in CITIES]
    ok = [(v, ct) for v, ct in ok if v is not None]
    tier = next((t for t in TIERS if any(ct[3] >= t for _, ct in ok)), None)
    if tier is not None:
        ct = min(((v, ct) for v, ct in ok if ct[3] >= tier), key=lambda vc: vc[0])[1]
    else:
        ct = min(CITIES + ([("Vancouver", home[0], home[1], 0)] if home else []),
                 key=lambda ct: km(near[0], near[1], ct[1], ct[2]))
    return {"name": ct[0], "lat": ct[1], "lon": ct[2], "home": bool(home) and (ct[1], ct[2]) == tuple(home)}


def _merc(lat, lon):
    return math.radians(lon) * R, math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R


def _unmerc(x, y):
    return math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2), math.degrees(x / R)


def zoom_area(pts, w, h, home, kpp, pad=14):
    """A centre + km-per-pixel area (hub/weather/geo.View) for a w×h map of the points ``pts`` [(lat, lon)] at
    ``kpp`` km per pixel, widened (within MAX_ZOOM_OUT / MIN_AREA_PX) and re-centred so a reference place shows too.
    Returns (area, ref); when even the nearest city won't fit, the area keeps its own zoom and the display
    points to the reference from the map's edge instead."""
    xs, ys = zip(*(_merc(la, lo) for la, lo in pts))
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    lat_c, lon_c = _unmerc((x0 + x1) / 2, (y0 + y1) / 2)
    mpp_t = kpp * 1000.0 / math.cos(math.radians(lat_c))         # projected metres per pixel
    mpp_max = max(mpp_t * MAX_ZOOM_OUT, max(x1 - x0, y1 - y0) / MIN_AREA_PX)

    def need(p):
        x, y = _merc(*p)
        return max((max(x1, x) - min(x0, x)) / max(1, w - 2 * pad), (max(y1, y) - min(y0, y)) / max(1, h - 2 * pad))

    ref = reference(lambda p: need(p) if need(p) <= mpp_max else None, home, (lat_c, lon_c))
    mpp = need((ref["lat"], ref["lon"]))
    if mpp > mpp_max:
        return {"center": [round(lat_c, 5), round(lon_c, 5)], "km_per_px": round(kpp, 4)}, ref
    rx, ry = _merc(ref["lat"], ref["lon"])
    lat, lon = _unmerc((min(x0, rx) + max(x1, rx)) / 2, (min(y0, ry) + max(y1, ry)) / 2)
    mpp = max(mpp, mpp_t)
    return {"center": [round(lat, 5), round(lon, 5)], "km_per_px": round(mpp * math.cos(math.radians(lat)) / 1000.0, 4)}, ref
