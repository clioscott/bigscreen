"""Weather alerts job: current ECCC alerts for the configured area → data/alerts.json.

Source: MSC GeoMet OGC API, collection ``weather-alerts`` (GeoJSON, one feature
per alert per forecast region). Features are filtered to the area's bounding
box, ended/expired alerts are dropped, and an alert issued for several regions
becomes one entry listing all of them. The display shows them on the
full-screen alert panel (display/modules/weather_alert.py).
"""

import time
from datetime import datetime, timezone

import requests

from hub import paths
from hub.jobs import _harness
from hub.util import atomic_write_json

API = "https://api.weather.gc.ca/collections/weather-alerts/items"
UA = "BigScreen/2.0 (personal LED weather display; one request every few minutes)"
# Metro Vancouver: West Vancouver to the Township of Langley at the border
DEFAULT_BBOX = [-123.30, 49.00, -122.45, 49.45]
SEVERITY = {"warning": 0, "watch": 1, "advisory": 2, "statement": 3}
DEFAULT_TYPES = ["warning", "watch", "advisory", "statement"]


def _t(s):
    try:
        return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def fetch(bbox, types, lang="en"):
    r = requests.get(API, params={"f": "json", "limit": 500, "bbox": ",".join(f"{v:.4f}" for v in bbox)},
                     headers={"User-Agent": UA}, timeout=40)
    r.raise_for_status()
    now = time.time()
    merged = {}
    for feat in r.json().get("features", []):
        p = feat.get("properties") or {}
        kind = p.get("alert_type") or "statement"
        if p.get("status_en") == "ended" or kind not in types:
            continue
        expires = _t(p.get("expiration_datetime"))
        if expires and expires < now:
            continue
        text = (p.get(f"alert_text_{lang}") or p.get("alert_text_en") or "").strip()
        key = (p.get("alert_code"), text[:400])
        a = merged.get(key)
        if a is None:
            a = merged[key] = {
                "code": p.get("alert_code"), "type": kind,
                "name": (p.get(f"alert_name_{lang}") or p.get("alert_name_en") or "weather alert").strip(),
                "short_name": p.get(f"alert_short_name_{lang}") or p.get("alert_short_name_en"),
                "status": p.get("status_en"), "colour": p.get("risk_colour_en"),
                "impact": p.get("impact_en"), "confidence": p.get("confidence_en"),
                "text": text, "regions": [], "geometries": [],
                "published": _t(p.get("publication_datetime")), "expires": expires,
                "valid": _t(p.get("validity_datetime")), "event_end": _t(p.get("event_end_datetime")),
            }
        region = (p.get(f"feature_name_{lang}") or p.get("feature_name_en") or "").strip()
        if region and region not in a["regions"]:
            a["regions"].append(region)
            if feat.get("geometry"):
                a["geometries"].append(feat["geometry"])
    alerts = sorted(merged.values(), key=lambda a: (SEVERITY.get(a["type"], 9), -(a["published"] or 0)))
    return alerts


def area_bbox(cfg, acfg):
    """[w, s, e, n] and a label: an explicit bbox, else the extent of a map area (default Metro Vancouver)."""
    if acfg.get("bbox"):
        return [float(v) for v in acfg["bbox"]], acfg.get("area_label") or "Custom area"
    areas = cfg["modules"].get("weather_maps", {}).get("areas", {})
    # default: the Lower Mainland, so nearby alerts (Howe Sound, Fraser Valley…) show too, labelled "nearby"
    aid = acfg.get("area_id") or next((x for x in ("metro", "core_metro") if x in areas), None)
    if aid in areas:
        from hub.weather.geo import View
        lo0, la0, lo1, la1 = View(dict(areas[aid], id=aid), 224, 134).lonlat
        return [min(lo0, lo1), min(la0, la1), max(lo0, lo1), max(la0, la1)], areas[aid].get("label") or aid
    return DEFAULT_BBOX, "Metro Vancouver"


def run(cfg):
    acfg = cfg["modules"].get("weather_alerts", {}) or {}
    if acfg.get("enabled", True) is False:
        atomic_write_json(paths.DATA_DIR / "alerts.json", {"updated_utc": time.time(), "alerts": [], "disabled": True})
        return {"ok": True, "message": "alerts screen off", "files": ["alerts.json"]}
    bbox, label = area_bbox(cfg, acfg)
    types = acfg.get("types") or DEFAULT_TYPES
    alerts = fetch(bbox, types, acfg.get("language", "en"))
    home = (acfg.get("home_city") or "Richmond").strip()
    from display.modules.alert_brief import HAZARD_INFO, hazard_of, mentions
    allowed = set(acfg.get("hazards") or [h for h, _, _ in HAZARD_INFO])
    for a in alerts:
        a["hazard"] = hazard_of(a)
    skipped = [a for a in alerts if a["hazard"] not in allowed]
    alerts = [a for a in alerts if a["hazard"] in allowed]
    for a in skipped:
        print(f"skipped ({a['hazard']} off): {a['name']}", flush=True)
    for a in alerts:
        a["home"] = mentions(a, home)
    alerts.sort(key=lambda a: not a["home"])                  # the wall's own city first (stable: severity order kept)
    # maps: each alert's zones over Core Metro / Lower Mainland, plus one for the built-in test alert
    from hub.weather import alertmap
    metas = []
    for a in alerts:
        try:
            meta = alertmap.render(a, cfg, home, acfg.get("home_lonlat"))
        except Exception as e:
            print(f"map for {a['name']}: {e}", flush=True)
            meta = None
        try:                                                   # which tile-grid units it covers, every level
            from hub import geo_cover
            a["coverage"] = geo_cover.coverage(geo_cover.alert_area(a))
        except Exception as e:
            print(f"coverage for {a['name']}: {e}", flush=True)
        a.pop("geometries", None)
        if meta:
            a["map"] = meta
            metas.append(meta)
    sample_maps = {}
    if acfg.get("show_map", True) is not False:
        from display.modules.alert_samples import SAMPLES
        for kind, smp in SAMPLES.items():
            try:
                m = alertmap.render_sample(cfg, smp["map_cities"], home, home_hit=mentions(smp, home),
                                           home_ll=acfg.get("home_lonlat"))
                if m:
                    sample_maps[kind] = m
                    metas.append(m)
            except Exception as e:
                print(f"sample map {kind}: {e}", flush=True)
    sample_map = sample_maps.get("rain")
    sample_coverage, home_units = {}, {}
    try:
        from hub import geo_cover
        from display.modules.alert_samples import SAMPLES
        for kind, smp in SAMPLES.items():
            sample_coverage[kind] = geo_cover.coverage(geo_cover.zones_area(smp.get("regions")))
        hl = acfg.get("home_lonlat") or (-123.0918, 49.1442)
        home_units = geo_cover.home_units(hl[0], hl[1])
    except Exception as e:
        print(f"sample coverage: {e}", flush=True)
    alertmap.prune(metas)
    atomic_write_json(paths.DATA_DIR / "alerts.json", {
        "updated_utc": time.time(), "area": label, "bbox": bbox, "alerts": alerts, "sample_map": sample_map,
        "sample_maps": sample_maps, "sample_coverage": sample_coverage, "home_units": home_units})
    for a in alerts:
        print(f"{'HOME' if a['home'] else 'nearby'} {a['type'].upper()}: {a['name']} — {', '.join(a['regions'])[:120]}", flush=True)
    return {"ok": True, "message": f"{len(alerts)} alert(s) in effect" if alerts else "no alerts in effect",
            "alerts": [f"{a['name']} ({a['type']})" for a in alerts], "files": ["alerts.json", "alertmaps/"]}


if __name__ == "__main__":
    _harness.main(run)
