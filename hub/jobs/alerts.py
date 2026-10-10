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
                "text": text, "regions": [],
                "published": _t(p.get("publication_datetime")), "expires": expires,
                "valid": _t(p.get("validity_datetime")), "event_end": _t(p.get("event_end_datetime")),
            }
        region = (p.get(f"feature_name_{lang}") or p.get("feature_name_en") or "").strip()
        if region and region not in a["regions"]:
            a["regions"].append(region)
    alerts = sorted(merged.values(), key=lambda a: (SEVERITY.get(a["type"], 9), -(a["published"] or 0)))
    return alerts


def area_bbox(cfg, acfg):
    """[w, s, e, n] and a label: an explicit bbox, else the extent of a map area (default Metro Vancouver)."""
    if acfg.get("bbox"):
        return [float(v) for v in acfg["bbox"]], acfg.get("area_label") or "Custom area"
    areas = cfg["modules"].get("weather_maps", {}).get("areas", {})
    aid = acfg.get("area_id") or ("core_metro" if "core_metro" in areas else None)
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
    atomic_write_json(paths.DATA_DIR / "alerts.json", {
        "updated_utc": time.time(), "area": label, "bbox": bbox, "alerts": alerts})
    for a in alerts:
        print(f"{a['type'].upper()}: {a['name']} — {', '.join(a['regions'])[:120]}", flush=True)
    return {"ok": True, "message": f"{len(alerts)} alert(s) in effect" if alerts else "no alerts in effect",
            "alerts": [f"{a['name']} ({a['type']})" for a in alerts], "files": ["alerts.json"]}


if __name__ == "__main__":
    _harness.main(run)
