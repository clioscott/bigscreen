"""Earthquakes job: quakes that matter in south-west BC → data/quakes.json.

Sources (free, no login): the USGS FDSN event service (``earthquake.usgs.gov/fdsnws``; Pacific Northwest Seismic
Network and NRCan locations, plus "Did You Feel It?" reports and PAGER levels) and Natural Resources Canada's
FDSN service (``earthquakescanada.nrcan.gc.ca/fdsnws``; every located BC event). The two are merged
(same event = within 30 s and 60 km). Canada's earthquake early warnings come through Alert Ready and are
routed here by the NAAD job.

An event is shown when any threshold holds (all configurable): close (M ≥ 2.5 within 100 km of home), regional
(M ≥ 4 within 400 km), large (M ≥ 6 within 1,500 km), reported felt by many, or strong enough to be felt at home
by the Atkinson & Wald (2007) intensity estimate. Each event carries what the wall can say about it: distance and
bearing from home, the estimated intensity there and at nearby cities, the tectonic setting, and the last 30 days
of activity for the map.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone

from hub.bc import common
from hub.jobs import _harness

USGS = "https://earthquake.usgs.gov/fdsnws/event/1/query"
NRCAN = "https://www.earthquakescanada.nrcan.gc.ca/fdsnws/event/1/query"
DEFAULTS = {"enabled": True, "near_km": 100, "near_mag": 2.5, "regional_km": 400, "regional_mag": 4.0, "large_km": 1500,
            "large_mag": 6.0, "felt_reports": 25, "home_mmi": 3.0, "show_hours": 12, "large_show_hours": 48,
            "takeover_mmi": 4.0, "eew": True, "theme": "pnsn", "dwell_sec": 25, "refresh_interval_sec": 60}
CITIES = [("Vancouver", 49.2827, -123.1207), ("Victoria", 48.4284, -123.3656), ("Nanaimo", 49.1659, -123.9401),
          ("Surrey", 49.1913, -122.8490), ("Abbotsford", 49.0504, -122.3045), ("Squamish", 49.7016, -123.1558),
          ("Whistler", 50.1163, -122.9574), ("Seattle", 47.6062, -122.3321), ("Bellingham", 48.7519, -122.4787),
          ("Tofino", 49.1530, -125.9066), ("Port Alberni", 49.2339, -124.8055), ("Courtenay", 49.6841, -124.9936),
          ("Powell River", 49.8353, -124.5247), ("Hope", 49.3800, -121.4400), ("Kelowna", 49.8880, -119.4960),
          ("Kamloops", 50.6745, -120.3273), ("Port Hardy", 50.7230, -127.4950), ("Tacoma", 47.2529, -122.4443),
          ("Olympia", 47.0379, -122.9007), ("Port Angeles", 48.1181, -123.4307)]


def mmi_aw07(mag, dist_km, depth_km=10.0):
    """Atkinson & Wald (2007) intensity prediction (western North America). Hypocentral distance used."""
    rh = math.sqrt(dist_km ** 2 + depth_km ** 2)
    R = math.sqrt(rh ** 2 + 14.0 ** 2)
    B = max(0.0, math.log10(R / 50.0))
    m = mag - 6.0
    v = 12.27 + 2.270 * m + 0.1304 * m * m - 1.30 * math.log10(R) - 0.0007070 * R + 1.95 * B - 0.577 * mag * math.log10(R)
    return max(1.0, min(10.0, v))


def setting(lat, lon, depth):
    """A sentence on where this sits in the Cascadia system (rough geographic classes)."""
    if lon < -127.6 and lat > 50.5:
        return "Offshore, on the Queen Charlotte fault and Explorer plate boundary"
    if lon < -127.0:
        return "Offshore, along the Juan de Fuca and Explorer plate boundaries"
    if depth >= 35 and lon > -124.5 and lat < 49.8:
        return "Deep, inside the subducting Juan de Fuca plate (an in-slab quake)"
    if depth < 30 and lon > -124.8 and lat < 49.6:
        return "Shallow, in the crust of the North American plate"
    if lon > -121.0:
        return "In the crust east of the Coast Mountains"
    return "In the crust of the North American plate"


def _usgs(params):
    r = common.session.get(USGS, params={"format": "geojson", "orderby": "time", **params}, timeout=40)
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        lo, la, dep = (f["geometry"]["coordinates"] + [10])[:3]
        if p.get("type") not in (None, "earthquake"):
            continue
        out.append({"id": f["id"], "source": "USGS", "mag": p.get("mag"), "mag_type": p.get("magType"),
                    "place": p.get("place") or "", "lat": la, "lon": lo, "depth": dep or 0.0, "time": p["time"] / 1000.0,
                    "felt": p.get("felt") or 0, "cdi": p.get("cdi"), "mmi": p.get("mmi"), "alert": p.get("alert"),
                    "tsunami": bool(p.get("tsunami")), "url": p.get("url"), "status": p.get("status")})
    return out


def _nrcan(start, box):
    r = common.session.get(NRCAN, params={"starttime": start, "minlatitude": box[1], "maxlatitude": box[3],
                                          "minlongitude": box[0], "maxlongitude": box[2], "format": "text"}, timeout=40)
    r.raise_for_status()
    out = []
    for line in r.text.splitlines():
        if not line or line.startswith("#"):
            continue
        p = line.split("|")
        try:
            name = p[7].split("/")[0].strip()
            if name.lower().startswith("blast"):
                continue                                         # quarry and construction blasts
            felt = name.endswith(", felt")
            name = name[:-6] if felt else name
            out.append({"id": p[0], "source": "NRCan", "mag": float(p[6]), "mag_type": p[5], "place": name,
                        "lat": float(p[2]), "lon": float(p[3]), "depth": float(p[4] or 0),
                        "time": datetime.fromisoformat(p[1].replace("Z", "+00:00")).timestamp(), "felt": 0,
                        "felt_nrcan": felt})
        except (ValueError, IndexError):
            continue
    return out


def merge(a, b):
    """USGS and NRCan lists → one, the USGS record keeping DYFI/PAGER, the NRCan BC place name preferred."""
    out = list(a)
    for e in b:
        twin = next((x for x in out if abs(x["time"] - e["time"]) < 30 and common.km(x["lat"], x["lon"], e["lat"], e["lon"]) < 60), None)
        if twin:
            twin.setdefault("ids", [twin["id"]]).append(e["id"])
            twin["source"] = f"{twin['source']} + {e['source']}" if e["source"] not in twin["source"] else twin["source"]
            if ", BC" in e["place"] or "Canada" in twin["place"]:
                twin["place_ca"] = e["place"]
            twin["felt_nrcan"] = twin.get("felt_nrcan") or e.get("felt_nrcan")
        else:
            out.append(e)
    return out


def enrich(e, home):
    hl, ha = home
    e["dist_km"] = round(common.km(ha, hl, e["lat"], e["lon"]), 1)
    from display.modules.bc_kit import bearing_word
    e["bearing"] = bearing_word(ha, hl, e["lat"], e["lon"])
    e["mmi_home"] = round(mmi_aw07(e["mag"] or 0, e["dist_km"], e["depth"]), 1)
    e["setting"] = setting(e["lat"], e["lon"], e["depth"])
    e["cities"] = sorted(({"name": n, "km": round(common.km(la, lo, e["lat"], e["lon"])),
                           "mmi": round(mmi_aw07(e["mag"] or 0, common.km(la, lo, e["lat"], e["lon"]), e["depth"]), 1)}
                          for n, la, lo in CITIES), key=lambda c: c["km"])[:8]
    e["place_short"] = (e.get("place_ca") or e.get("place") or "").replace(", Canada", "").replace(", BC", "")
    return e


def shown(e, st, now):
    m, d = e["mag"] or 0, e["dist_km"]
    big = m >= float(st["large_mag"]) and d <= float(st["large_km"])
    hours = float(st["large_show_hours"] if big or m >= 5 else st["show_hours"])
    if now - e["time"] > hours * 3600:
        return False
    return (big or (m >= float(st["near_mag"]) and d <= float(st["near_km"]))
            or (m >= float(st["regional_mag"]) and d <= float(st["regional_km"]))
            or (e.get("felt") or 0) >= float(st["felt_reports"]) and d <= float(st["regional_km"])
            or e["mmi_home"] >= float(st["home_mmi"]))


def _eew_params(a):
    """Epicentre, magnitude, depth and origin time from an NRCan warning's CAP parameters (names vary; matched
    loosely), or from its CAP circle (centre = epicentre)."""
    import re
    from hub.bc.cap import ts
    out = {}
    for name, v in (a.get("params") or {}).items():
        n = name.lower()
        try:
            if "latlon" in n or n.endswith("epicentre") or n.endswith("epicenter"):
                la, lo = [float(x) for x in re.findall(r"-?\d+\.?\d*", v)[:2]]
                out.setdefault("lat", la)
                out.setdefault("lon", lo)
            elif "latitude" in n:
                out.setdefault("lat", float(re.findall(r"-?\d+\.?\d*", v)[0]))
            elif "longitude" in n:
                out.setdefault("lon", float(re.findall(r"-?\d+\.?\d*", v)[0]))
            elif "magnitude" in n and "type" not in n:
                out.setdefault("mag", float(re.findall(r"\d+\.?\d*", v)[0]))
            elif "depth" in n:
                out.setdefault("depth", float(re.findall(r"\d+\.?\d*", v)[0]))
            elif "origin" in n and "time" in n:
                t = ts(v)
                if t:
                    out.setdefault("origin", t)
        except (ValueError, IndexError):
            continue
    if "lat" not in out and a.get("circles"):
        lo, la, _r = a["circles"][0]
        out.update(lat=la, lon=lo)
    return out


def eew_items(st, home, routed):
    if not st.get("eew"):
        return []
    out = []
    for a in routed:
        p = _eew_params(a)
        it = {"id": f"eew-{a['id']}", "kind": "eew", "headline": a.get("headline"), "description": a.get("description"),
              "instruction": a.get("instruction"), "areas": a.get("areas"), "polygons": a.get("polygons"),
              "time": p.get("origin") or a.get("sent") or time.time(), "origin_known": "origin" in p,
              "expires": a.get("expires"), "sender": a.get("sender"), "severity": a.get("severity"),
              "mag": p.get("mag"), "lat": p.get("lat"), "lon": p.get("lon"), "depth": p.get("depth", 10.0)}
        if it["lat"] is not None:
            it["dist_km"] = round(common.km(home[1], home[0], it["lat"], it["lon"]), 1)
            if it["mag"]:
                it["mmi_home"] = round(mmi_aw07(it["mag"], it["dist_km"], it["depth"]), 1)
        out.append(it)
    return out


def _routed_quakes():
    """NRCan warnings straight from the stored NAAD messages (not waiting for the NAAD job's routing)."""
    try:
        from hub.jobs import naad as naad_job
        from hub import config
        st = common.settings(config.load(), "naad_alerts", naad_job.DEFAULTS)
        _, routed = naad_job.collect(dict(st, area="bc"))
        return routed.get("quakes", [])
    except Exception as e:
        print(f"early warnings: {e}", flush=True)
        return []


def run(cfg):
    st = common.settings(cfg, "bc_quakes", DEFAULTS)
    from hub.bc import maps
    maps.ensure_all(["quake_swbc", "quake_casc"], log=lambda m: print(m, flush=True))
    if st.get("enabled") is False:
        common.write("quakes", {"items": [], "disabled": True})
        return {"ok": True, "message": "earthquake screen off", "files": ["quakes.json"]}
    home = common.home(cfg)
    now = time.time()
    # early warnings first: they take over the wall before the (slower) catalogue fetches
    eew = eew_items(st, home, _routed_quakes())
    if eew:
        prev = common.cache("quakes_last", {}) or {}
        common.write("quakes", {**prev, "items": eew + [i for i in prev.get("items", []) if i.get("kind") != "eew"]})
        common.takeover("quake_alert", [i["id"] for i in eew], "quakes_takeover", log=lambda m: print(m, flush=True))
    start7 = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    start30 = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")
    errors = []
    try:
        usgs = _usgs({"latitude": home[1], "longitude": home[0], "maxradiuskm": float(st["large_km"]), "minmagnitude": 2.0,
                      "starttime": start7})
    except Exception as e:
        usgs, errors = [], errors + [f"USGS: {e}"]
    try:
        nrc = _nrcan(start7, [-136.0, 46.0, -114.0, 56.0])
    except Exception as e:
        nrc, errors = [], errors + [f"NRCan: {e}"]
    if not usgs and not nrc and errors:
        raise RuntimeError("; ".join(errors))
    events = [enrich(e, home) for e in merge(usgs, nrc) if e.get("mag") is not None]
    items = sorted((e for e in events if shown(e, st, now)), key=lambda e: -e["time"])
    for it in items:
        it["kind"] = "quake"
    # background activity for the maps: 30 days, M ≥ 1.5 within 500 km
    recent = []
    try:
        rec = merge(_usgs({"latitude": home[1], "longitude": home[0], "maxradiuskm": 500, "minmagnitude": 1.5,
                           "starttime": start30}), _nrcan(start30, [-130.5, 46.0, -117.0, 52.5]))
        recent = [[round(e["lat"], 3), round(e["lon"], 3), e["mag"], int(e["time"]), round(e["depth"])] for e in rec
                  if e.get("mag") is not None and common.km(home[1], home[0], e["lat"], e["lon"]) <= 500]
    except Exception as e:
        errors.append(f"recent: {e}")
    items = eew + items
    near30 = sum(1 for r in recent if common.km(home[1], home[0], r[0], r[1]) <= 100 and r[2] >= 2)
    out = common.write("quakes", {"items": items, "recent": recent, "home": list(home), "near30": near30,
                                  "counts": {"usgs": len(usgs), "nrcan": len(nrc), "events": len(events)}, "errors": errors})
    common.save_cache("quakes_last", out)
    for i in items:
        print(f"{i.get('kind')} M{i.get('mag')} {i.get('place_short', i.get('headline'))} — {i.get('dist_km')} km, "
              f"MMI {i.get('mmi_home')} at home", flush=True)
    hot = [i["id"] for i in items if i["kind"] == "eew" or (i.get("mmi_home") or 0) >= float(st["takeover_mmi"])]
    if hot and now - max(i["time"] for i in items if i["id"] in hot) < 3600:
        common.takeover("quake_alert", hot, "quakes_takeover", log=lambda m: print(m, flush=True))
    return {"ok": True, "message": (f"{len(items)} on the wall · " if items else "nothing to show · ")
            + f"{len(events)} events this week within {int(st['large_km'])} km",
            "events": [f"M{i.get('mag')} {i.get('place_short', '')}" for i in items[:8]], "errors": errors,
            "files": ["quakes.json"]}


if __name__ == "__main__":
    _harness.main(run)
