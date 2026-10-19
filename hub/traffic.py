"""DriveBC traffic cameras for the Webcams page: grouped by bridge or tunnel, and by city.

Built from DriveBC's camera list (``/api/webcams/``). Bridges and tunnels are a curated list of camera ids — every
camera on the crossing and its approaches, the best view first; every camera in the Lower Mainland, Fraser Valley and
up the Sea-to-Sky is also filed under its nearest city (Vancouver, Richmond, Delta, Surrey, Burnaby and New Westminster
first).

Some of DriveBC's directions are wrong. ``FACING`` holds the ones checked by eye against landmarks in the picture
(North Shore mountains, Mt Baker, the setting sun, the bridge towers); ``fix`` applies them to DriveBC's records, and
every user of the camera list (this page, the camera map, the DriveBC alert screen) goes through it.
"""

from __future__ import annotations

import re

# (name, short name for labels, DriveBC camera ids: the best view first, a note for the page or "")
CROSSINGS = [
    ("George Massey Tunnel (Hwy 99)", "Massey Tunnel", [30, 31, 34, 33, 371, 372, 373, 374, 29, 1115, 32], ""),
    ("Oak Street Bridge", "Oak St", [70, 67, 66, 309, 310], ""),
    ("Knight Street Bridge", "Knight St", [797, 796, 695, 696], ""),
    ("Hwy 91 East-West Connector", "Hwy 91", [322, 323, 210, 211, 208, 209, 733, 735, 767, 768, 780, 791,
                                              315, 316, 317], ""),
    ("Queensborough Bridge", "Queensborough", [88, 87, 732, 318, 319, 89, 90], ""),
    ("Alex Fraser Bridge", "Alex Fraser", [92, 91, 731, 577, 578, 579, 573, 574, 575, 576], ""),
    ("stal̕əw̓asəm Bridge", "stal̕əw̓asəm", [609, 610, 526, 527, 258],
     "No camera on the bridge itself: these watch its Surrey end (Hwy 17 at Tannery Rd, King George Blvd)."),
    ("Port Mann Bridge", "Port Mann", [292, 275, 684, 683, 357, 358], ""),
    ("Ironworkers Memorial Bridge", "Ironworkers", [72, 73, 763, 71, 1029, 851, 852, 853, 854, 1068, 1069], ""),
    ("Lions Gate Bridge", "Lions Gate", [18, 20, 22, 21, 17, 19], ""),
    ("Cassiar Tunnel (Hwy 1)", "Cassiar Tunnel", [36, 1183], ""),
    ("Golden Ears Bridge", "Golden Ears", [720, 827, 826, 719], ""),
    ("Peace Arch border crossing", "Peace Arch", [13, 14, 445], ""),
]

# Which way a camera really looks, where DriveBC's label is wrong (None: can't tell, so no direction is claimed).
# Checked 2026-10-18 around 17:15 PDT, when the sun was low in the west-southwest.
FACING = {
    29: "N",        # Steveston interchange, labelled S: the North Shore mountains fill the background. It can turn
                    # ("*"), but it has sat facing north; the page marks it ↻ either way
    31: "S",        # Massey "South Approach", labelled N: Mt Baker straight ahead
    371: "W",       # Hwy 99 at Hwy 17A, labelled E: looking into the setting sun
    374: None,      # Hwy 99 at Hwy 17A, labelled W: probably swapped with 371 (no sun, open fields)
    574: "N",       # Hwy 17 at the Hwy 91 connector, labelled E: the Alex Fraser towers straight ahead
    573: None,      # same interchange, labelled N: probably swapped with 574
}

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
HOME = ["Vancouver", "Richmond", "Delta", "Surrey", "Burnaby", "New Westminster"]   # listed first, in this order
# Rough outlines (lon, lat) of the home cities, checked in this order before the nearest-centre fallback, so cameras by
# the Fraser land on the right bank (the Oak St Bridge's north end is Vancouver, Annacis Island is Delta).
BOUNDS = [
    ("New Westminster", [(-122.958, 49.200), (-122.940, 49.238), (-122.882, 49.236), (-122.882, 49.214),
                         (-122.920, 49.198)]),
    ("New Westminster", [(-122.958, 49.205), (-122.910, 49.205), (-122.910, 49.183), (-122.958, 49.178)]),  # Queensborough
    ("Vancouver", [(-123.270, 49.199), (-123.0235, 49.199), (-123.0235, 49.296), (-123.120, 49.317),
                   (-123.270, 49.300)]),
    ("Burnaby", [(-123.0235, 49.185), (-123.0235, 49.300), (-122.890, 49.300), (-122.890, 49.235),
                 (-122.960, 49.198)]),
    ("Richmond", [(-123.270, 49.199), (-122.958, 49.199), (-122.958, 49.174), (-123.060, 49.128),
                  (-123.270, 49.105)]),
    ("Coquitlam", [(-122.882, 49.214), (-122.882, 49.300), (-122.800, 49.300), (-122.800, 49.222),
                   (-122.840, 49.214)]),                                                                 # SW Coquitlam
    ("White Rock", [(-122.845, 49.010), (-122.750, 49.010), (-122.750, 49.040), (-122.845, 49.040)]),
    ("Delta", [(-123.270, 49.105), (-123.060, 49.128), (-122.958, 49.174), (-122.920, 49.190), (-122.8935, 49.208),
               (-122.8935, 49.090), (-122.930, 49.070), (-122.930, 49.000), (-123.270, 49.000)]),
    ("Surrey", [(-122.8935, 49.208), (-122.810, 49.228), (-122.684, 49.205), (-122.684, 49.000), (-122.930, 49.000),
                (-122.930, 49.070), (-122.8935, 49.090)]),
]
AREA = (48.95, 50.25, -123.45, -121.3)      # lat min/max, lon min/max
CITY_KM = 15.0                              # farther than this from every city: "Elsewhere"
LOOK = {"N": "north", "NE": "northeast", "E": "east", "SE": "southeast", "S": "south", "SW": "southwest", "W": "west",
        "NW": "northwest"}
_LOOKING = re.compile(r"\blooking\s+(north|south|east|west)(east|west)?(bound)?\b", re.I)


def _km(a, b):
    """Distance in km (equirectangular; fine at these scales)."""
    import math
    x = math.radians(b[1] - a[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
    y = math.radians(b[0] - a[0])
    return 6371 * math.hypot(x, y)


def _inside(lon, lat, poly):
    """Ray casting: is (lon, lat) inside the polygon?"""
    hit = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > lat) != (y1 > lat) and lon < x0 + (lat - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def city_of(lat, lon):
    for name, poly in BOUNDS:
        if _inside(lon, lat, poly):
            return name
    name, d = min(((n, _km((lat, lon), c)) for n, c in CITIES.items()), key=lambda t: t[1])
    return name if d <= CITY_KM else "Elsewhere"


def fix(c):
    """A DriveBC camera record with its direction corrected (FACING) and its caption to match. Cameras that can turn
    (caption ending "*") lose the "*" and get ``turns: True``: their direction is DriveBC's last known one."""
    if c.get("fixed"):                       # already done (the hub's cached list is fixed once)
        return c
    cap = c.get("caption") or ""
    turns = "*" in cap                      # DriveBC marks cameras that can turn with a "*"
    cid = c.get("id")
    if cid not in FACING and not turns:
        return c
    c = dict(c, caption=re.sub(r"\s*\*", "", cap).strip(), turns=turns, fixed=True)
    if cid in FACING:
        o = FACING[cid]
        c["orientation"] = o or ""
        if o:
            c["caption"] = _LOOKING.sub("looking " + LOOK[o], c["caption"])
        else:
            c["caption"] = re.sub(r"[,;]?\s*" + _LOOKING.pattern + r"[^.]*", "", c["caption"], flags=re.I).strip()
        c["corrected"] = True
    return c


def fix_all(raw):
    return [fix(c) for c in raw or [] if isinstance(c, dict)]


def camera(c, fold=lambda s: s):
    """The fields the page needs from one DriveBC camera record (or None outside the area)."""
    lon, lat = ((c.get("location") or {}).get("coordinates") or [None, None])[:2]
    if lat is None or not (AREA[0] <= lat <= AREA[1] and AREA[2] <= lon <= AREA[3]):
        return None
    c = fix(c)
    o = c.get("orientation") or ""
    return {"id": c.get("id"), "name": fold(c.get("name") or ""), "caption": fold(c.get("caption") or ""),
            "lat": lat, "lon": lon, "orientation": o, "look": LOOK.get(o, ""), "highway": c.get("highway_display") or "",
            "on": bool(c.get("is_on", True)) and not c.get("marked_stale"), "delayed": bool(c.get("marked_delayed")),
            "updated": c.get("last_update_modified"), "period": c.get("update_period_mean"),
            "turns": bool(c.get("turns")), "corrected": bool(c.get("corrected")), "city": city_of(lat, lon)}


def build(raw, fold=lambda s: s):
    """{"crossings": [{name, short, label, note, cams}], "cities": [{city, cams}]} from DriveBC's camera list.
    ``label`` is the short name in letters the wall's fonts can draw (fold)."""
    cams = [x for x in (camera(c, fold) for c in raw or [] if isinstance(c, dict)) if x]
    by_id = {c["id"]: c for c in cams}
    crossings = []
    for name, short, ids, note in CROSSINGS:
        got = [by_id[i] for i in ids if i in by_id]
        if got:
            crossings.append({"name": name, "short": short, "label": fold(short), "note": note, "cams": got})
    cities = {}
    for c in cams:
        cities.setdefault(c["city"], []).append(c)
    rank = {n: i for i, n in enumerate(HOME)}
    order = sorted(cities, key=lambda n: (rank.get(n, len(HOME)), n == "Elsewhere", n))
    return {"crossings": crossings,
            "cities": [{"city": n, "cams": sorted(cities[n], key=lambda c: c["name"].lower())} for n in order],
            "count": len(cams)}


# --- City of Vancouver traffic cameras (intersections; DriveBC has only a handful in Vancouver) -------------------
# The open-data list (Open Government Licence - Vancouver) gives each camera's page; the page lists one image per
# view ("Cambie Bridge - North"). The images are plain JPEGs with an exact Last-Modified, so they are added as "image"
# sources. The City's "North" can be the leg of the intersection rather than where the camera looks (checked by eye),
# so no direction is claimed: the name keeps the City's word.
COV_LIST = "https://opendata.vancouver.ca/api/explore/v2.1/catalog/datasets/web-cam-url-links/exports/json"
COV_BASE = "https://trafficcams.vancouver.ca/"
_COV_IMG = re.compile(r'<img[^>]*\bsrc="(cameraimages/[^"]+\.jpe?g)"[^>]*\balt="([^"]*)"', re.I)


def cov_views(row, html):
    """The cameras on one City of Vancouver camera page: one per image, named with the view from its alt text."""
    pt = row.get("geo_point_2d") or {}
    lat, lon = pt.get("lat"), pt.get("lon")
    name = (row.get("name") or "").strip()
    out = []
    for src, alt in _COV_IMG.findall(html or ""):
        word = alt.rsplit(" - ", 1)[-1].strip() if " - " in alt else ""
        stem = src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        out.append({"id": "cov-" + stem, "name": f"{name} - {word}" if word else name,
                    "caption": name, "lat": lat, "lon": lon, "orientation": "", "look": "", "highway": "", "on": True,
                    "delayed": False, "updated": None, "period": 300, "turns": False, "corrected": False,
                    "city": "Vancouver", "credit": "City of Vancouver", "thumb": f"/api/webcams/city/cov-{stem}.jpg",
                    "source": {"type": "image", "url": COV_BASE + src, "refresh_sec": 120}})
    return out


def cov_cameras(get):
    """Every City of Vancouver camera view. get(url) -> text. Pages that fail are skipped."""
    import json
    from concurrent.futures import ThreadPoolExecutor
    rows = [r for r in json.loads(get(COV_LIST)) if (r.get("url") or "").startswith(COV_BASE)]

    def one(r):
        try:
            return cov_views(r, get(r["url"]))
        except Exception:
            return []
    with ThreadPoolExecutor(4) as ex:                  # ~220 small pages, once a day
        return _unique([v for vs in ex.map(one, rows) for v in vs])


def _unique(views):
    seen, out = set(), []
    for v in views:
        if v["id"] not in seen and v["lat"] is not None:
            seen.add(v["id"])
            out.append(v)
    return out


# --- City of Richmond intersection cameras ------------------------------------------------------------------------
# The City's camera map page has a marker per intersection (where, its name, its details page); the details page shows
# one image per approach ("... Eastbound"), plain JPEGs with an exact Last-Modified. Like Vancouver's, "Eastbound" names
# the approach, so no direction is claimed. The images are the City's (© City of Richmond), shown as its page shows them.
RMD_BASE = "https://www.richmond.ca/services/transportation/trafficcameras/"
RMD_IMAGES = "https://www.richmond.ca/trafficcam/"
_RMD_MARKER = re.compile(r"\[\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\].*?bindPopup\(\"<h6>(.*?)</h6>"
                         r"<a href='(IntersectionDetails\.aspx\?id=\d+)'", re.S)          # within one marker's code
_BOUND = {"nb": "Northbound", "sb": "Southbound", "eb": "Eastbound", "wb": "Westbound"}
_IMG_TAG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = lambda name: re.compile(r'\b' + name + r'\s*=\s*"([^"]*)"', re.I)
_SRC, _ALT = _ATTR("src"), _ATTR("alt")
_RMD_SRC = re.compile(re.escape(RMD_IMAGES) + r'[^"/]+\.jpe?g$', re.I)


def _rmd_images(html):
    """[(src, alt)] of the City's camera images on a details page, whatever the attribute order."""
    out = []
    for tag in _IMG_TAG.findall(html or ""):
        src, alt = _SRC.search(tag), _ALT.search(tag)
        if src and _RMD_SRC.match(src.group(1)):
            out.append((src.group(1), alt.group(1) if alt else ""))
    return out


def richmond_markers(html):
    """[(lat, lon, name, details page URL)] from the City of Richmond's camera map page."""
    import html as h
    out = []
    for chunk in (html or "").split("L.marker(")[1:]:      # one marker each: a popup-less one can't borrow the next's
        m = _RMD_MARKER.search(chunk)
        if m:
            la, lo, name, page = m.groups()
            out.append((float(la), float(lo), h.unescape(name).strip(), RMD_BASE + page))
    return out


def richmond_views(lat, lon, name, html):
    """The cameras on one Richmond intersection page: one per image, named with the approach from its alt text."""
    import html as h
    out = []
    for src, alt in _rmd_images(html):
        alt = h.unescape(alt).replace("Live Camera Image:", "").strip()
        word = alt[len(name):].strip(" -") if alt.lower().startswith(name.lower()) else ""
        stem = src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if not word:                                    # some pages leave it out: the file says it ("..._eb.jpg")
            word = _BOUND.get(stem.rsplit("_", 1)[-1].lower(), "")
        out.append({"id": "rmd-" + stem, "name": f"{name} - {word}" if word else name,
                    "caption": name, "lat": lat, "lon": lon, "orientation": "", "look": "", "highway": "", "on": True,
                    "delayed": False, "updated": None, "period": 60, "turns": False, "corrected": False,
                    "city": "Richmond", "credit": "City of Richmond", "thumb": f"/api/webcams/city/rmd-{stem}.jpg",
                    "source": {"type": "image", "url": src, "refresh_sec": 60}})
    return out


def richmond_cameras(get):
    """Every City of Richmond camera view. get(url) -> text. Pages that fail are skipped."""
    from concurrent.futures import ThreadPoolExecutor
    markers = richmond_markers(get(RMD_BASE))

    def one(m):
        try:
            return richmond_views(m[0], m[1], m[2], get(m[3]))
        except Exception:
            return []
    with ThreadPoolExecutor(4) as ex:                  # ~160 small pages, once a day
        return _unique([v for vs in ex.map(one, markers) for v in vs])


CITY_SOURCES = {"Vancouver": cov_cameras, "Richmond": richmond_cameras}    # city -> crawler: the views in that city
