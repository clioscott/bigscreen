"""Tsunami job: NTWC bulletins for the BC coast → data/tsunami.json.

Sources (free, no login):
  * The US National Tsunami Warning Center (Palmer, AK), which issues the tsunami messages for British Columbia:
    its Atom feed (tsunami.gov/events/xml/PAAQAtom.xml), each bulletin's CAP document and plain-text bulletin
    (evaluation, earthquake parameters, estimated arrival times by place). The Pacific Tsunami Warning Center
    feed (PHEB) is read for context only.
  * BC's own Alert Ready tsunami messages (EMCR, by notification zone), routed here by the NAAD job.
  * DFO / CHS tide gauges (IWLS API) on the coast — observed and predicted water level — while a tsunami
    message is in effect.

Information statements for big quakes ("no tsunami danger for British Columbia") are shown for a few hours;
advisories, watches and warnings that include BC stay up until cancelled or expired, and take over the wall.
"""

from __future__ import annotations

import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from hub.bc import cap, common
from hub.jobs import _harness

FEEDS = {"PAAQ": "https://www.tsunami.gov/events/xml/PAAQAtom.xml", "PHEB": "https://www.tsunami.gov/events/xml/PHEBAtom.xml"}
IWLS = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
DEFAULTS = {"enabled": True, "info_min_mag": 7.0, "info_hours": 3, "takeover": True, "gauges": True,
            "theme": "pacific", "dwell_sec": 30, "refresh_interval_sec": 120}
LEVELS = ["none", "information", "watch", "advisory", "warning"]
GAUGES = [("09354", "Prince Rupert"), ("08408", "Port Hardy"), ("08615", "Tofino"), ("08545", "Bamfield"),
          ("07120", "Victoria"), ("07795", "Point Atkinson")]
ZONES = {"A": "North Coast and Haida Gwaii", "B": "Central Coast and NE Vancouver Island", "C": "Outer west coast of Vancouver Island",
         "D": "Juan de Fuca Strait", "E": "Strait of Georgia"}
TOFINO = (49.153, -125.907)
NS = {"a": "http://www.w3.org/2005/Atom", "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#"}


def level_of(text):
    t = (text or "").lower()
    for lv in ("warning", "advisory", "watch"):
        if f"tsunami {lv}" in t or t.strip() == lv:
            return lv
    if "cancel" in t:
        return "cancellation"
    if "threat" in t:
        return "watch"
    return "information"


def _feed(name):
    r = common.session.get(FEEDS[name], timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for e in root.findall("a:entry", NS):
        links = {l.get("title") or l.get("rel"): l.get("href") for l in e.findall("a:link", NS)}
        summ = ET.tostring(e.find("a:summary", NS), encoding="unicode", method="text") if e.find("a:summary", NS) is not None else ""
        cat = re.search(r"Category:\s*([A-Za-z ]+)", summ)
        mag = re.search(r"Preliminary Magnitude:\s*([\d.]+)", summ)
        out.append({"feed": name, "title": (e.findtext("a:title", "", NS) or "").strip(),
                    "updated": cap.ts(e.findtext("a:updated", "", NS)), "id": e.findtext("a:id", "", NS),
                    "lat": float(e.findtext("geo:lat", "0", NS) or 0), "lon": float(e.findtext("geo:long", "0", NS) or 0),
                    "category": (cat.group(1).strip() if cat else ""), "mag": float(mag.group(1)) if mag else None,
                    "cap": links.get("CapXML document"), "bulletin": links.get("Bulletin"),
                    "header_title": (root.findtext("a:title", "", NS) or "").strip()})
    return out


def _bulletin_bits(txt):
    """From an NTWC text bulletin: the evaluation bullets, BC arrival times, BC zones mentioned, statement number."""
    out = {"evaluation": [], "arrivals": [], "bc_text": "", "number": None}
    m = re.search(r"Number\s+(\d+)", txt, re.I)
    out["number"] = int(m.group(1)) if m else None
    sec = re.search(r"EVALUATION\s*-+\s*(.*?)(?:\n[A-Z][A-Z /]{6,}\n-{4,}|\$\$)", txt, re.S)
    if sec:
        bullets = re.split(r"\n\s*\*\s+", "\n" + sec.group(1))
        out["evaluation"] = [re.sub(r"\s+", " ", b).strip() for b in bullets if b.strip()]
    # arrival table rows: "  Langara BC          0215 PDT Oct 16"
    for line in txt.splitlines():
        m = re.match(r"\s*\*?\s*([A-Z][A-Za-z .'-]+?)\s+BC\s+(\d{4})\s+([A-Z]{3})\s+([A-Z][a-z]{2})\s+(\d{1,2})", line)
        if m:
            out["arrivals"].append({"place": m.group(1).strip(), "hhmm": m.group(2), "tz": m.group(3),
                                    "date": f"{m.group(4)} {m.group(5)}"})
    bc = [l.strip() for l in txt.splitlines() if "british columbia" in l.lower() or " bc " in l.lower()]
    out["bc_text"] = " ".join(bc)[:600]
    return out


def _zones(level, bulletin, naad):
    """Zone A–E → level. Alert Ready's zone messages win; otherwise NTWC's level on the outer coast."""
    zones = {z: "none" for z in ZONES}
    for a in naad:
        lv = level_of(f"{a.get('event', '')} {a.get('headline', '')}")
        for area in a.get("areas") or []:
            m = re.search(r"Zone\s+([A-E])", area)
            if m and LEVELS.index(lv if lv in LEVELS else "information") > LEVELS.index(zones[m.group(1)]):
                zones[m.group(1)] = lv
    if all(v == "none" for v in zones.values()) and level in ("warning", "advisory", "watch"):
        text = " ".join(bulletin.get("evaluation") or []).lower() + " " + (bulletin.get("bc_text") or "").lower()
        for z in "ABCD":
            zones[z] = level
        if any(w in text for w in ("strait of georgia", "vancouver", "inner coast", "inside waters")):
            zones["E"] = level
    return zones


def _gauge(code, name, hours=6):
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    st = common.session.get(f"{IWLS}/stations", params={"code": code}, timeout=20).json()
    if not st:
        return None
    sid = st[0]["id"]
    out = {"code": code, "name": name, "lat": st[0]["latitude"], "lon": st[0]["longitude"]}
    for series in ("wlo", "wlp"):
        r = common.session.get(f"{IWLS}/stations/{sid}/data", timeout=25, params={
            "time-series-code": series, "from": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
        pts = [[cap.ts(p["eventDate"]), p["value"]] for p in (r.json() if r.ok else []) if p.get("value") is not None]
        step = max(1, len(pts) // 180)
        out[series] = [[int(t), round(v, 3)] for t, v in pts[::step] if t]
    obs, pred = out.get("wlo") or [], out.get("wlp") or []
    if obs and pred:
        last_t, last_v = obs[-1]
        p = min(pred, key=lambda q: abs(q[0] - last_t))
        out["residual"] = round(last_v - p[1], 2)
    return out


def item_from(entry, st):
    """One feed entry (the latest bulletin for its event) → a screen item, or None."""
    a = {}
    if entry.get("cap"):
        try:
            a = cap.parse(common.session.get(entry["cap"], timeout=25).content)
        except Exception as e:
            print(f"CAP {entry['cap']}: {e}", flush=True)
    txt = ""
    if entry.get("bulletin"):
        try:
            txt = common.session.get(entry["bulletin"], timeout=25).text
        except Exception as e:
            print(f"bulletin: {e}", flush=True)
    b = _bulletin_bits(txt)
    level = level_of(a.get("event") or entry.get("category") or entry.get("header_title"))
    mag = entry.get("mag") or float(cap.param(a, "EventPreliminaryMagnitude") or 0) or None
    origin = cap.ts(cap.param(a, "EventOriginTime")) or entry.get("updated")
    depth = re.search(r"([\d.]+)\s*kilomet", cap.param(a, "EventDepth") or "")
    bc = "british columbia" in (txt + a.get("headline", "") + a.get("description", "")).lower()
    no_threat = bool(re.search(r"no tsunami (danger|threat)[^.]*british columbia", re.sub(r"\s+", " ", txt), re.I))
    dist = common.km(entry["lat"], entry["lon"], *TOFINO)
    return {
        "id": f"{entry['feed']}-{a.get('incidents') or entry['id']}", "feed": entry["feed"], "level": level,
        "bc": bc, "bc_threat": bc and not no_threat and level in ("warning", "advisory", "watch"),
        "title": entry.get("header_title") or a.get("event") or "", "region": cap.param(a, "EventLocationName") or entry["title"],
        "mag": mag, "mag_type": cap.param(a, "EventPreliminaryMagnitudeType"), "lat": entry["lat"], "lon": entry["lon"],
        "depth_km": float(depth.group(1)) if depth else None, "origin": origin, "issued": entry.get("updated"),
        "number": b["number"], "headline": a.get("headline") or "", "evaluation": b["evaluation"],
        "instruction": re.sub(r"\s+", " ", a.get("instruction") or "").strip(), "arrivals": b["arrivals"],
        "dist_tofino_km": round(dist), "eta_tofino": origin + dist / 720.0 * 3600 if origin else None,
        "expires": (a.get("expires") or 0) + (st["info_hours"] - 1) * 3600 if level == "information" else None,
        "bulletin_url": entry.get("bulletin"), "source": "NTWC" if entry["feed"] == "PAAQ" else "PTWC",
    }


def run(cfg):
    st = common.settings(cfg, "tsunami", DEFAULTS)
    from hub.bc import maps
    maps.ensure_all(["tsunami_pacific", "tsunami_bc"], log=lambda m: print(m, flush=True))
    if st.get("enabled") is False:
        common.write("tsunami", {"items": [], "disabled": True})
        return {"ok": True, "message": "tsunami screen off", "files": ["tsunami.json"]}
    now = time.time()
    entries, errors = {}, []
    for f in FEEDS:
        try:
            entries[f] = _feed(f)
        except Exception as e:
            errors.append(f"{f}: {e}")
            entries[f] = []
    if not any(entries.values()) and errors:
        raise RuntimeError("; ".join(errors))
    naad = (common.cache("naad_routed", {}) or {}).get("tsunami", [])
    prev = {i["id"]: i for i in (common.cache("tsunami_items", {}) or {}).get("items", [])}
    items = []
    for e in entries.get("PAAQ", []):
        age_h = (now - (e.get("updated") or 0)) / 3600
        cat = level_of(e.get("category") or e.get("header_title"))
        if cat == "information" and ((e.get("mag") or 0) < float(st["info_min_mag"]) or age_h > float(st["info_hours"])):
            continue
        if cat in ("warning", "advisory", "watch") and age_h > 24:
            continue
        if cat == "cancellation":
            continue
        it = item_from(e, st)
        if it["level"] == "information" or it["bc"]:
            items.append(it)
    # PTWC: only as context when NTWC is quiet but a big Pacific quake is being evaluated
    if not items:
        for e in entries.get("PHEB", []):
            if (e.get("mag") or 0) >= float(st["info_min_mag"]) and (now - (e.get("updated") or 0)) / 3600 <= float(st["info_hours"]):
                it = item_from(e, st)
                it["level"] = "information"
                items.append(it)
                break
    for it in items:
        it["zones"] = _zones(it["level"], {"evaluation": it["evaluation"], "bc_text": ""}, naad)
        it["bc_alerts"] = [{"headline": a.get("headline"), "areas": a.get("areas"), "instruction": a.get("instruction"),
                            "sender": a.get("sender")} for a in naad]
        if st.get("gauges") and it["level"] in ("warning", "advisory", "watch"):
            old = prev.get(it["id"], {})
            if old.get("gauges_at", 0) > now - 240:
                it["gauges"], it["gauges_at"] = old.get("gauges"), old.get("gauges_at")
            else:
                gs = []
                for code, name in GAUGES:
                    try:
                        g = _gauge(code, name)
                        if g:
                            gs.append(g)
                    except Exception as ex:
                        errors.append(f"gauge {name}: {ex}")
                it["gauges"], it["gauges_at"] = gs, now
    # BC's own zone messages with no NTWC event (rare): show them on their own
    if naad and not any(i["bc"] for i in items):
        a = naad[0]
        lv = level_of(f"{a.get('event', '')} {a.get('headline', '')}")
        items.insert(0, {"id": f"naad-{a['id']}", "feed": "NAAD", "level": lv if lv in LEVELS else "information", "bc": True,
                         "bc_threat": lv in ("warning", "advisory", "watch"), "title": a.get("event") or "Tsunami",
                         "region": "", "mag": None, "lat": None, "lon": None, "origin": a.get("sent"), "issued": a.get("sent"),
                         "headline": a.get("headline"), "evaluation": [a.get("description") or ""],
                         "instruction": a.get("instruction") or "", "arrivals": [], "zones": _zones(lv, {}, naad),
                         "bc_alerts": [], "expires": a.get("expires"), "source": a.get("sender") or "Emergency Info BC"})
    order = {lv: i for i, lv in enumerate(reversed(LEVELS))}
    items.sort(key=lambda i: (not i.get("bc_threat"), order.get(i["level"], 9), -(i.get("issued") or 0)))
    common.save_cache("tsunami_items", {"items": items})
    common.write("tsunami", {"items": items, "errors": errors,
                             "latest": {f: (v[0]["title"] if v else None) for f, v in entries.items()}})
    for i in items:
        print(f"{i['level'].upper()} {i['source']}: M{i.get('mag')} {i.get('region')} (BC threat: {i['bc_threat']})", flush=True)
    if st.get("takeover"):
        hot = [i["id"] + f"#{i.get('number')}" for i in items if i.get("bc_threat")]
        if hot:
            common.takeover("tsunami_alert", hot, "tsunami_takeover", log=lambda m: print(m, flush=True))
    return {"ok": True, "message": (f"{len(items)} on the wall" if items else "no tsunami messages for BC")
            + (" · BC THREAT" if any(i.get("bc_threat") for i in items) else ""), "errors": errors, "files": ["tsunami.json"]}


if __name__ == "__main__":
    _harness.main(run)
