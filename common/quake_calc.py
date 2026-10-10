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


# major cities (the biggest, then prefecture capitals and a few exposed coastal towns); order = label priority
CITIES = [("Tokyo", "東京", 35.690, 139.692), ("Yokohama", "横浜", 35.444, 139.638), ("Osaka", "大阪", 34.694, 135.502),
          ("Nagoya", "名古屋", 35.181, 136.906), ("Sapporo", "札幌", 43.062, 141.354), ("Fukuoka", "福岡", 33.590, 130.402),
          ("Kobe", "神戸", 34.690, 135.196), ("Kawasaki", "川崎", 35.531, 139.703), ("Kyoto", "京都", 35.012, 135.768),
          ("Saitama", "さいたま", 35.861, 139.645), ("Hiroshima", "広島", 34.385, 132.455), ("Sendai", "仙台", 38.268, 140.872),
          ("Chiba", "千葉", 35.607, 140.106), ("Kitakyushu", "北九州", 33.883, 130.875), ("Niigata", "新潟", 37.916, 139.036),
          ("Hamamatsu", "浜松", 34.711, 137.726), ("Kumamoto", "熊本", 32.803, 130.708), ("Okayama", "岡山", 34.655, 133.919),
          ("Shizuoka", "静岡", 34.976, 138.383), ("Kagoshima", "鹿児島", 31.597, 130.557), ("Utsunomiya", "宇都宮", 36.555, 139.883),
          ("Matsuyama", "松山", 33.839, 132.766), ("Kanazawa", "金沢", 36.561, 136.656), ("Oita", "大分", 33.238, 131.613),
          ("Nagasaki", "長崎", 32.750, 129.878), ("Toyama", "富山", 36.696, 137.214), ("Gifu", "岐阜", 35.423, 136.761),
          ("Miyazaki", "宮崎", 31.911, 131.424), ("Nagano", "長野", 36.651, 138.181), ("Wakayama", "和歌山", 34.226, 135.167),
          ("Nara", "奈良", 34.685, 135.805), ("Takamatsu", "高松", 34.340, 134.047), ("Koriyama", "郡山", 37.400, 140.360),
          ("Iwaki", "いわき", 37.050, 140.888), ("Otsu", "大津", 35.018, 135.855), ("Maebashi", "前橋", 36.389, 139.063),
          ("Kochi", "高知", 33.559, 133.531), ("Asahikawa", "旭川", 43.771, 142.365), ("Akita", "秋田", 39.720, 140.103),
          ("Aomori", "青森", 40.822, 140.747), ("Morioka", "盛岡", 39.702, 141.154), ("Fukushima", "福島", 37.761, 140.474),
          ("Mito", "水戸", 36.366, 140.471), ("Fukui", "福井", 36.064, 136.220), ("Yamagata", "山形", 38.240, 140.364),
          ("Tokushima", "徳島", 34.066, 134.559), ("Kofu", "甲府", 35.664, 138.568), ("Tsu", "津", 34.730, 136.509),
          ("Hakodate", "函館", 41.769, 140.729), ("Saga", "佐賀", 33.249, 130.299), ("Yamaguchi", "山口", 34.186, 131.471),
          ("Matsue", "松江", 35.468, 133.049), ("Tottori", "鳥取", 35.501, 134.235), ("Hachinohe", "八戸", 40.512, 141.488),
          ("Kushiro", "釧路", 42.985, 144.381), ("Obihiro", "帯広", 42.924, 143.196), ("Wajima", "輪島", 37.391, 136.899),
          ("Ishinomaki", "石巻", 38.434, 141.303), ("Miyako", "宮古", 39.641, 141.957), ("Choshi", "銚子", 35.735, 140.827)]


def city_forecast(ev, now):
    """Major cities that will feel the quake (predicted 1 or more), with P/S arrival. A city is ``listed``
    (affected) once the P wave reaches it, straight away if it is expected to feel 4 or more (EEW
    warning territory), and always for intensity reports (the quake is over)."""
    el = now - float(ev["origin"])
    out = []
    for rank, (en, jp, la, lo) in enumerate(CITIES):
        d = haversine_km(ev["lat"], ev["lon"], la, lo)
        i = intensity_value(ev["mag"], ev["depth"], d)
        cls = shindo_class(i)
        if cls == "0":
            continue
        tp, ts = arrival_s(d, ev["depth"], VP), arrival_s(d, ev["depth"], VS)
        out.append({"name": en, "jp": jp, "lat": la, "lon": lo, "rank": rank, "dist_km": d, "intensity": i,
                    "class": cls, "tp": tp, "ts": ts, "p_in": tp - el, "s_in": ts - el,
                    "listed": el >= tp or class_rank(cls) >= 4 or ev.get("source") == "p2p"})
    return out


def site_forecast(ev, now):
    """Per site: distance, predicted class, seconds until the S wave (negative once it has passed)."""
    out = {}
    for name, (la, lo) in SITES.items():
        d = haversine_km(ev["lat"], ev["lon"], la, lo)
        i = intensity_value(ev["mag"], ev["depth"], d)
        out[name] = {"dist_km": d, "intensity": i, "class": shindo_class(i),
                     "s_in": arrival_s(d, ev["depth"], VS) - (now - ev["origin"])}
    return out
