"""Wildfire job: BC Wildfire Service data for south-west BC and the Okanagan → data/wildfire.json.

Sources (BCWS / EMCR public ArcGIS layers, free, no login):
  * BCWS_ActiveFires_PublicView           every fire not yet out: stage of control, size, cause, fire of note
  * BCWS_FirePerimeters_PublicView        mapped perimeters (simplified for the Pi)
  * Evacuation_Orders_and_Alerts          EMCR evacuation orders and alerts (wildfire events only)
  * British_Columbia_Danger_Rating_-_View fire danger class by area (layer 7)
  * British_Columbia_Bans_and_Prohibition_Areas_-_View  campfire / open-burning bans (layer 14)

Regions are named boxes the control panel can switch on and off. What goes on the wall: fires of note,
evacuation orders and alerts, fires out of control above a size within a distance of home, and any new fire
close to home. Every item carries the regional picture (all active fires, perimeters, evacuations) for the map.
"""

from __future__ import annotations

import time

from hub.bc import common
from hub.jobs import _harness

BASE = "https://services6.arcgis.com/ubm4tcTYICKBpist/arcgis/rest/services"
REGIONS = {
    "lower_mainland": {"label": "Lower Mainland & Fraser Valley", "box": [-123.60, 48.95, -121.20, 49.70]},
    "sea_to_sky": {"label": "Sea to Sky & Pemberton", "box": [-123.60, 49.60, -122.20, 50.80]},
    "sunshine_coast": {"label": "Sunshine Coast", "box": [-124.70, 49.40, -123.40, 50.10]},
    "south_island": {"label": "South Vancouver Island", "box": [-125.30, 48.30, -123.20, 49.40]},
    "fraser_canyon": {"label": "Fraser Canyon & Lillooet", "box": [-122.20, 49.40, -121.00, 50.90]},
    "nicola_similkameen": {"label": "Nicola & Similkameen", "box": [-121.00, 49.00, -119.70, 50.30]},
    "okanagan": {"label": "Okanagan", "box": [-120.00, 49.00, -118.70, 50.80]},
}
DEFAULTS = {"enabled": True, "regions": list(REGIONS), "fires_of_note": True, "evacuations": True,
            "near_km": 100, "near_min_ha": 10, "close_km": 25, "theme": "situation", "dwell_sec": 25,
            "refresh_interval_sec": 600, "takeover": True}
STATUS = {"Out of Control": "ooc", "Being Held": "held", "Under Control": "uc", "Out": "out"}


def _query(service, layer, box, where="1=1", geometry=True, fields="*"):
    r = common.session.get(f"{BASE}/{service}/FeatureServer/{layer}/query", timeout=40, params={
        "f": "json", "where": where, "outFields": fields, "returnGeometry": "true" if geometry else "false", "outSR": 4326,
        "geometry": ",".join(str(v) for v in box), "geometryType": "esriGeometryEnvelope", "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects", "resultRecordCount": 2000})
    r.raise_for_status()
    d = r.json()
    if d.get("error"):
        raise RuntimeError(f"{service}: {d['error'].get('message')}")
    return d.get("features", [])


def _rings(geom, tol):
    return [common.simplify([[round(x, 4), round(y, 4)] for x, y in ring], tol) for ring in (geom or {}).get("rings", [])
            if len(ring) >= 3][:30]


def _in_regions(lat, lon, regions):
    return any(common.in_box(lat, lon, REGIONS[r]["box"]) for r in regions if r in REGIONS)


def run(cfg):
    st = common.settings(cfg, "wildfire", DEFAULTS)
    from hub.bc import maps
    maps.ensure_all(["fire_south"], log=lambda m: print(m, flush=True))
    if st.get("enabled") is False:
        common.write("wildfire", {"items": [], "disabled": True})
        return {"ok": True, "message": "wildfire screen off", "files": ["wildfire.json"]}
    regions = [r for r in st.get("regions") or [] if r in REGIONS] or list(REGIONS)
    boxes = [REGIONS[r]["box"] for r in regions]
    env = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
    home = common.home(cfg)
    now = time.time()
    errors = []

    fires = []
    for f in _query("BCWS_ActiveFires_PublicView", 0, env, "FIRE_STATUS<>'Out'", geometry=False):
        a = f["attributes"]
        la, lo = a.get("LATITUDE"), a.get("LONGITUDE")
        if la is None or not _in_regions(la, lo, regions):
            continue
        fires.append({"id": a.get("FIRE_NUMBER"), "name": a.get("INCIDENT_NAME") or a.get("FIRE_NUMBER"),
                      "number": a.get("FIRE_NUMBER"), "status": STATUS.get(a.get("FIRE_STATUS"), "ooc"),
                      "status_text": a.get("FIRE_STATUS"), "ha": round(float(a.get("CURRENT_SIZE") or 0), 1),
                      "cause": a.get("FIRE_CAUSE") or "", "where": a.get("GEOGRAPHIC_DESCRIPTION") or "",
                      "lat": la, "lon": lo, "of_note": a.get("FIRE_OF_NOTE_IND") == "Y",
                      "discovered": (a.get("IGNITION_DATE") or 0) / 1000.0 or None, "url": a.get("FIRE_URL"),
                      "km": round(common.km(home[1], home[0], la, lo))})
    perims = []
    try:
        for f in _query("BCWS_FirePerimeters_PublicView", 0, env, "FIRE_STATUS<>'Out'"):
            a = f["attributes"]
            rings = _rings(f.get("geometry"), 0.003)
            if rings:
                perims.append({"number": a.get("FIRE_NUMBER"), "ha": a.get("FIRE_SIZE_HECTARES"), "rings": rings})
    except Exception as e:
        errors.append(f"perimeters: {e}")
    evacs = []
    try:
        for f in _query("Evacuation_Orders_and_Alerts", 0, env):
            a = f["attributes"]
            if (a.get("ORDER_ALERT_STATUS") or "") not in ("Order", "Alert"):
                continue
            if "fire" not in (a.get("EVENT_TYPE") or "").lower():
                continue                                   # floods and slides aren't this screen's
            rings = _rings(f.get("geometry"), 0.002)
            if not rings:
                continue
            la = sum(p[1] for r in rings for p in r) / sum(len(r) for r in rings)
            lo = sum(p[0] for r in rings for p in r) / sum(len(r) for r in rings)
            if not _in_regions(la, lo, regions):
                continue
            evacs.append({"id": f"evac-{a.get('EMRG_OAA_SYSID')}", "status": a.get("ORDER_ALERT_STATUS"),
                          "event": a.get("EVENT_NAME") or "", "name": a.get("ORDER_ALERT_NAME") or "",
                          "agency": a.get("ISSUING_AGENCY") or "", "homes": a.get("MULTI_SOURCED_HOMES") or 0,
                          "people": a.get("MULTI_SOURCED_POPULATION") or 0, "updated": (a.get("DATE_MODIFIED") or 0) / 1000.0,
                          "lat": la, "lon": lo, "rings": rings, "km": round(common.km(home[1], home[0], la, lo))})
    except Exception as e:
        errors.append(f"evacuations: {e}")
    danger = []
    try:
        for f in _query("British_Columbia_Danger_Rating_-_View", 7, env, geometry=False):
            a = {k.lower(): v for k, v in f["attributes"].items()}
            rating = a.get("danger_rating_desc") or a.get("danger_rating") or a.get("dangerrating")
            if rating:
                danger.append(str(rating))
    except Exception as e:
        errors.append(f"danger rating: {e}")
    bans = []
    try:
        for f in _query("British_Columbia_Bans_and_Prohibition_Areas_-_View", 14, env, geometry=False):
            a = {k.lower(): v for k, v in f["attributes"].items()}
            t = a.get("access_prohibition_description") or a.get("type") or a.get("fire_centre_name")
            cat = a.get("type") or ""
            label = " — ".join(v for v in (str(a.get("fire_centre_name") or ""), str(t or "")) if v)
            if label and label not in bans:
                bans.append(label)
    except Exception as e:
        errors.append(f"bans: {e}")
    danger_home = None
    try:
        hb = [home[0] - 0.01, home[1] - 0.01, home[0] + 0.01, home[1] + 0.01]
        hit = _query("British_Columbia_Danger_Rating_-_View", 7, hb, geometry=False, fields="DANGER_RATING_DESC")
        danger_home = hit[0]["attributes"].get("DANGER_RATING_DESC") if hit else None
    except Exception as e:
        errors.append(f"danger at home: {e}")
    order = ["Extreme", "High", "Moderate", "Low", "Very Low"]
    top_danger = next((o for o in order if any(o.lower() == d.lower() for d in danger)), danger[0] if danger else None)

    items = []
    for e in evacs if st.get("evacuations") else []:
        items.append({"kind": "evac", **e})
    for f in fires:
        why = []
        if st.get("fires_of_note") and f["of_note"]:
            why.append("Wildfire of note")
        if f["status"] == "ooc" and f["ha"] >= float(st["near_min_ha"]) and f["km"] <= float(st["near_km"]):
            why.append(f"Out of control within {int(st['near_km'])} km")
        if f["km"] <= float(st["close_km"]) and f["discovered"] and now - f["discovered"] < 48 * 3600:
            why.append("New fire nearby")
        if why:
            items.append({"kind": "fire", **f, "why": why,
                          "evacs": [e["id"] for e in evacs if common.km(e["lat"], e["lon"], f["lat"], f["lon"]) < 40]})
    items.sort(key=lambda i: (i["kind"] != "evac", i.get("status") != "Order", -(i.get("ha") or 0)))
    # a zoomed map round each evacuation area on the wall (the regional map is ~1.3 km per LED)
    keep = []
    for e in [i for i in items if i["kind"] == "evac"][:6]:
        name = f"evac_{e['id']}"
        try:
            xs = [p[0] for r in e["rings"] for p in r]
            ys = [p[1] for r in e["rings"] for p in r]
            span_km = max((max(xs) - min(xs)) * 72.0, (max(ys) - min(ys)) * 111.0, 2.0)
            maps.ensure_custom(name, {**{k: v for k, v in maps.SPECS["fire_south"].items() if k not in ("area", "w", "h")},
                                      "area": {"center": [e["lat"], e["lon"]], "km_per_px": round(max(0.05, span_km * 1.6 / 140), 3)},
                                      "w": 170, "h": 150})
            keep.append(name)
        except Exception as ex:
            errors.append(f"evac map {e['id']}: {ex}")
    maps.prune("evac_", keep)
    counts = {s: sum(f["status"] == s for f in fires) for s in ("ooc", "held", "uc")}
    common.write("wildfire", {"items": items, "fires": fires, "perimeters": perims, "evacs": evacs, "danger": top_danger, "danger_home": danger_home,
                              "danger_all": sorted(set(danger)), "bans": bans[:8], "counts": counts, "home": list(home),
                              "regions": [REGIONS[r]["label"] for r in regions], "errors": errors})
    for i in items:
        print(f"{i['kind']}: {i.get('name')} — {i.get('status_text') or i.get('status')} {i.get('ha', '')}", flush=True)
    if st.get("takeover"):
        hot = [i["id"] for i in items if i["kind"] == "evac" and i["status"] == "Order"]
        if hot:
            common.takeover("fire_alert", hot, "wildfire_takeover", log=lambda m: print(m, flush=True))
    return {"ok": True, "message": f"{len(items)} on the wall · {len(fires)} active fires in the regions"
            + (f" · danger {top_danger}" if top_danger else ""), "errors": errors, "files": ["wildfire.json"]}


if __name__ == "__main__":
    _harness.main(run)
