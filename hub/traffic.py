"""DriveBC traffic cameras for the Webcams page: grouped by bridge or tunnel, and by city.

Built from DriveBC's camera list (``/api/webcams/``). Bridges and tunnels are a curated list of camera ids, best view
first; every camera in the Lower Mainland, Fraser Valley and up the Sea-to-Sky is also filed under its nearest city.
"""

from __future__ import annotations

import math

# (name, short name for labels, DriveBC camera ids: the best view first)
CROSSINGS = [
    ("Lions Gate Bridge", "Lions Gate", [18, 20, 22, 17]),
    ("Ironworkers Memorial Bridge", "Ironworkers", [72, 73, 763, 1029, 71, 851, 852]),
    ("Port Mann Bridge", "Port Mann", [292, 275]),
    ("Alex Fraser Bridge", "Alex Fraser", [92, 91, 731]),
    ("Knight Street Bridge", "Knight St", [797, 796, 695, 696]),
    ("Oak Street Bridge", "Oak St", [70, 67, 66]),
    ("Golden Ears Bridge", "Golden Ears", [720, 827, 826, 719]),
    ("Queensborough Bridge", "Queensborough", [88, 87, 732]),
    ("Annacis Channel (Hwy 91)", "Annacis", [735, 780, 767, 768, 791]),
    ("George Massey Tunnel (Hwy 99)", "Massey Tunnel", [30, 31, 34, 33, 29]),
    ("Cassiar Tunnel (Hwy 1)", "Cassiar Tunnel", [36, 1183]),
    ("Peace Arch border crossing", "Peace Arch", [13, 14, 445]),
]

CITIES = {
    "Vancouver": (49.2610, -123.1140), "Burnaby": (49.2488, -122.9805), "Richmond": (49.1666, -123.1336),
    "Surrey": (49.1044, -122.8011), "Delta": (49.0900, -123.0300), "New Westminster": (49.2057, -122.9110),
    "Coquitlam": (49.2838, -122.7932), "Port Coquitlam": (49.2625, -122.7811), "Port Moody": (49.2830, -122.8317),
    "North Vancouver": (49.3200, -123.0724), "West Vancouver": (49.3286, -123.1602), "Lions Bay": (49.4560, -123.2370),
    "Langley": (49.1044, -122.6600), "Maple Ridge": (49.2193, -122.6019), "Pitt Meadows": (49.2210, -122.6890),
    "White Rock": (49.0253, -122.8026), "Abbotsford": (49.0504, -122.3045), "Mission": (49.1330, -122.3090),
    "Chilliwack": (49.1579, -121.9515), "Hope": (49.3830, -121.4420), "Squamish": (49.7016, -123.1558),
    "Whistler": (50.1163, -122.9574),
}
AREA = (48.95, 50.25, -123.45, -121.3)      # lat min/max, lon min/max
CITY_KM = 15.0                              # farther than this from every city: "Elsewhere"
LOOK = {"N": "north", "NE": "northeast", "E": "east", "SE": "southeast", "S": "south", "SW": "southwest", "W": "west",
        "NW": "northwest"}


def _km(a, b):
    """Distance in km (equirectangular; fine at these scales)."""
    x = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    y = math.radians(b[0] - a[0])
    return 6371 * math.hypot(x, y)


def city_of(lat, lon):
    name, d = min(((n, _km((lat, lon), c)) for n, c in CITIES.items()), key=lambda t: t[1])
    return name if d <= CITY_KM else "Elsewhere"


def camera(c, fold=lambda s: s):
    """The fields the page needs from one DriveBC camera record (or None outside the area)."""
    lon, lat = ((c.get("location") or {}).get("coordinates") or [None, None])[:2]
    if lat is None or not (AREA[0] <= lat <= AREA[1] and AREA[2] <= lon <= AREA[3]):
        return None
    return {"id": c.get("id"), "name": fold(c.get("name") or ""), "caption": fold(c.get("caption") or ""),
            "lat": lat, "lon": lon, "orientation": c.get("orientation") or "", "highway": c.get("highway_display") or "",
            "on": bool(c.get("is_on", True)) and not c.get("marked_stale"), "delayed": bool(c.get("marked_delayed")),
            "updated": c.get("last_update_modified"), "period": c.get("update_period_mean"),
            "city": city_of(lat, lon)}


def build(raw, fold=lambda s: s):
    """{"crossings": [{name, short, cams}], "cities": [{city, cams}]} from DriveBC's camera list."""
    cams = [x for x in (camera(c, fold) for c in raw or []) if x]
    by_id = {c["id"]: c for c in cams}
    crossings = []
    for name, short, ids in CROSSINGS:
        got = [dict(by_id[i], look=LOOK.get(by_id[i]["orientation"], "")) for i in ids if i in by_id]
        if got:
            crossings.append({"name": name, "short": short, "cams": got})
    cities = {}
    for c in cams:
        cities.setdefault(c["city"], []).append(c)
    order = sorted(cities, key=lambda n: (n == "Elsewhere", n))
    return {"crossings": crossings,
            "cities": [{"city": n, "cams": sorted(cities[n], key=lambda c: c["name"].lower())} for n in order],
            "count": len(cams)}
