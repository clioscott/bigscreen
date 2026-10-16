"""Alert Ready job: the stored NAAD messages (hub/bc/naad_listener.py) → data/naad.json.

Keeps public, unexpired alerts for the chosen area (Metro Vancouver, the Lower Mainland or all of BC), follows
updates and cancellations (a message that references earlier ones replaces them), and drops what the wall
already shows elsewhere so nothing appears twice:

  * ECCC weather alerts (sender cap-pac@canada.ca, the EC-MSC-SMC layer) — the weather alert screen
  * earthquakes / early warnings (NRCan)  → the earthquake screen      (listed under ``routed``)
  * tsunami                                → the tsunami screen
  * wildfire / forest fire                 → the wildfire screen
  * road closures and delays               → the DriveBC screen

Alerts flagged for immediate broadcast take over the wall at once (once per alert).
"""

from __future__ import annotations

import time

from hub import paths
from hub.bc import cap, common
from hub.jobs import _harness

DEFAULTS = {"enabled": True, "area": "lower_mainland", "show_tests": False, "takeover": "immediate",
            "min_severity": "unknown", "theme": "alert_ready", "dwell_sec": 25, "refresh_interval_sec": 300}
ECCC_SENDERS = {"cap-pac@canada.ca"}
ROUTES = {
    "quakes": {"earthquake", "eew", "earthquakeeew"},
    "tsunami": {"tsunami"},
    "wildfire": {"wildfire", "forestfire", "grassfire", "fireban"},
    "roads": {"roadclosure", "roaddelay", "roadcondition", "bridgeclosure"},
}
SEVERITY = ["unknown", "minor", "moderate", "severe", "extreme"]
AREAS = {
    # CAP-CP geocodes are Statistics Canada SGC codes (59 = BC; 4-digit census divisions)
    "metro": {"label": "Metro Vancouver", "sgc": ("5915",), "box": [-123.30, 49.00, -122.45, 49.45]},
    "lower_mainland": {"label": "Lower Mainland", "sgc": ("5915", "5909", "5929", "5931"),
                       "box": [-124.20, 48.95, -121.20, 50.20]},
    "bc": {"label": "British Columbia", "sgc": ("59",), "box": [-139.10, 48.20, -114.00, 60.00]},
}


def route_of(a):
    if (a.get("sender") or "").lower() in ECCC_SENDERS or any("EC-MSC-SMC" in c for c in a.get("codes") or []):
        return "eccc"
    code = (a.get("event_code") or "").lower().replace("_", "")
    ev = (a.get("event") or "").lower()
    sender = (a.get("sender") or "").lower()
    if "nrcan" in sender or "earthquake" in sender:
        return "quakes"
    for r, codes in ROUTES.items():
        if code in codes:
            return r
    if "tsunami" in ev:
        return "tsunami"
    if "earthquake" in ev:
        return "quakes"
    if "wildfire" in ev or "forest fire" in ev:
        return "wildfire"
    if "road" in ev and ("clos" in ev or "delay" in ev):
        return "roads"
    return "naad"


def in_area(a, area):
    spec = AREAS.get(area) or AREAS["lower_mainland"]
    geos = [g for g in a.get("geocodes") or [] if g.isdigit()]
    if geos:
        return any(g.startswith(spec["sgc"]) for g in geos)
    from shapely.geometry import Point, Polygon, box
    b = box(*spec["box"])
    for ring in a.get("polygons") or []:
        try:
            if Polygon(ring).intersects(b):
                return True
        except Exception:
            continue
    for lo, la, r in a.get("circles") or []:
        if Point(lo, la).buffer(max(0.01, r / 111.0)).intersects(b):
            return True
    return False


def item_of(a):
    from hub.bc.common import simplify
    polys = [simplify(r, 0.004) for r in (a.get("polygons") or [])][:40]
    immediate = cap.param(a, "Broadcast_Immediately").lower() == "yes" or cap.param(a, "WirelessImmediate").lower() == "yes"
    return {
        "id": a["key"], "identifier": a["identifier"], "event": a.get("event") or "Alert", "code": a.get("event_code") or "",
        "category": a.get("category"), "severity": (a.get("severity") or "Unknown"), "urgency": a.get("urgency") or "",
        "certainty": a.get("certainty") or "", "response": a.get("response") or "",
        "headline": (a.get("headline") or "").strip(), "description": (a.get("description") or "").strip(),
        "instruction": (a.get("instruction") or "").strip(), "areas": a.get("areas") or [], "polygons": polys,
        "sender": a.get("sender_name") or a.get("sender"), "sent": a.get("sent"), "effective": a.get("effective"),
        "expires": a.get("expires"), "msg_type": a.get("msg_type"), "status": a.get("status"), "web": a.get("web") or "",
        "immediate": immediate, "updates": a.get("updates", 0),
        "params": {k_: v[0] for k_, v in (a.get("params") or {}).items() if v}, "circles": a.get("circles") or [],
    }


def collect(st, now=None):
    """Every stored message, parsed; returns (current alerts, routed messages by screen)."""
    now = now or time.time()
    from hub.bc import naad_listener
    idx = naad_listener._index()
    msgs = []
    for k in idx:
        p = naad_listener.DIR / f"{k}.xml"
        try:
            a = cap.parse(p.read_bytes())
        except Exception as e:
            print(f"skip {k}: {e}", flush=True)
            continue
        a["key"] = k
        msgs.append(a)
    msgs.sort(key=lambda a: a.get("sent") or 0)
    # updates / cancels replace what they reference
    by_key = {}
    for a in msgs:
        for sender, ident, _sent in a.get("references") or []:
            old = by_key.pop(naad_listener.key_of(sender, ident), None)
            if old is not None:
                a["updates"] = old.get("updates", 0) + 1
        if a.get("msg_type") == "Cancel":
            continue
        by_key[a["key"]] = a
    current, routed = [], {r: [] for r in ROUTES}
    min_sev = SEVERITY.index(st.get("min_severity") or "unknown") if st.get("min_severity") in SEVERITY else 0
    for a in by_key.values():
        if a.get("scope") != "Public" or (a.get("expires") and a["expires"] < now):
            continue
        status = a.get("status")
        if status not in ("Actual",) and not (st.get("show_tests") and status in ("Test", "Exercise")):
            continue
        if not in_area(a, st.get("area")):
            continue
        r = route_of(a)
        if r == "eccc":
            continue
        if r in routed:
            routed[r].append(item_of(a))
            continue
        sev = (a.get("severity") or "unknown").lower()
        if (SEVERITY.index(sev) if sev in SEVERITY else 0) < min_sev:
            continue
        current.append(item_of(a))
    order = {s: i for i, s in enumerate(reversed(SEVERITY))}
    current.sort(key=lambda i: (order.get(i["severity"].lower(), 9), -(i["sent"] or 0)))
    return current, routed


def run(cfg):
    st = common.settings(cfg, "naad_alerts", DEFAULTS)
    from hub.bc import maps, naad_listener
    maps.ensure_all(["naad_lm", "naad_bc"], log=lambda m: print(m, flush=True))
    naad_listener.prune()
    if st.get("enabled") is False:
        common.write("naad", {"items": [], "disabled": True, "area": AREAS[st["area"]]["label"]})
        return {"ok": True, "message": "Alert Ready screen off", "files": ["naad.json"]}
    items, routed = collect(st)
    common.save_cache("naad_routed", routed)
    common.write("naad", {"items": items, "area": AREAS.get(st["area"], AREAS["lower_mainland"])["label"],
                          "routed": {k: len(v) for k, v in routed.items()}, "stored": len(naad_listener._index())})
    for i in items:
        print(f"{i['severity']} {i['event']}: {i['headline'][:100]} — {', '.join(i['areas'])[:80]}", flush=True)
    if st.get("takeover") != "never":
        hot = [i["id"] for i in items if st.get("takeover") == "all" or i["immediate"]]
        if hot:
            common.takeover("naad_alert", hot, "naad_takeover", log=lambda m: print(m, flush=True))
    n_routed = sum(len(v) for v in routed.values())
    return {"ok": True, "message": (f"{len(items)} alert(s) in effect" if items else "no alerts in effect")
            + (f", {n_routed} shown on other screens" if n_routed else ""),
            "alerts": [f"{i['event']} ({i['severity']})" for i in items], "files": ["naad.json"]}


if __name__ == "__main__":
    _harness.main(run)
