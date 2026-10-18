"""DriveBC job: road incidents and closures in Metro Vancouver → data/drivebc.json (+ webcam stills).

Source: the public DriveBC.ca API (no login) — ``/api/events/`` (the same events as the official Open511 feed,
with DriveBC's own ``closed`` flag, direction and landmark) and ``/api/webcams/`` (1,000+ highway cameras).

Only events touching Metro Vancouver are kept, and by default only what a driver needs to know now:
incidents (crashes, vehicle fires, debris, hazards), closures, and road conditions (snow, ice). Major and
planned construction can be switched on. For each event the nearest DriveBC camera within a few km is fetched
and stored at LED size as raw RGB (data/drivebc/cam_<id>.bin), so the Pi only copies pixels.

A new closure of a highway, bridge or tunnel can take over the wall once.
"""

from __future__ import annotations

import io
import re
import time
import unicodedata
from datetime import datetime

import numpy as np

from hub import paths
from hub.bc import common
from hub.jobs import _harness

API = "https://www.drivebc.ca/api"
DEFAULTS = {"enabled": True, "incidents": True, "closures": True, "conditions": True, "major_construction": False,
            "planned_hours": 0, "cameras": True, "takeover": "closures", "theme": "vms", "dwell_sec": 20,
            "refresh_interval_sec": 120, "bbox": [-123.30, 49.00, -122.45, 49.45]}
CAM_W, CAM_H = 336, 192                      # full wall height; DriveBC stills are 800×468 (captions cropped)
METRO = {"fit": {"nw": [49.4085, -123.2975], "se": [49.0021, -122.4593]}}
OUT = paths.DATA_DIR / "drivebc"
MAJOR_ROUTES = re.compile(r"highway|hwy|bridge|tunnel|causeway|trans-canada", re.I)


def fold(s):
    """Text the BDF fonts can draw: combining marks dropped, letters outside Latin-1 folded (ə → e)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("ə", "e").replace("Ə", "E")
    return "".join(ch if ord(ch) < 256 else "" for ch in s).replace("  ", " ").strip()


def _ts(s):
    try:
        return datetime.fromisoformat(s).timestamp() if s else None
    except ValueError:
        return None


def _points(geom):
    g = geom or {}
    cs = g.get("coordinates") or []
    t = g.get("type")
    if t == "Point":
        return [cs]
    if t == "LineString":
        return cs
    if t in ("MultiLineString", "Polygon"):
        return [p for part in cs for p in part]
    if t == "MultiPolygon":
        return [p for poly in cs for part in poly for p in part]
    return []


def _in_box(e, box):
    return any(box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3] for p in _points(e.get("location")) if len(p) >= 2)


def highways(e):
    nums = re.findall(r"Highway\s+(\d+[A-Z]?)", f"{e.get('route_at', '')} {e.get('route_display', '')}")
    out = []
    for n in nums:
        if n not in out:
            out.append(n)
    return out


def wanted(e, st, now):
    cat, typ = e.get("display_category"), e.get("event_type")
    if cat == "futureEvents":
        start = _ts(e.get("start"))
        return bool(st.get("planned_hours")) and start is not None and start - now <= float(st["planned_hours"]) * 3600
    if e.get("closed") and st.get("closures") and (typ != "CONSTRUCTION" or st.get("major_construction")):
        return True                                     # scheduled work closures only with major construction on
    if typ == "INCIDENT" and st.get("incidents"):
        return True
    if typ in ("ROAD_CONDITION", "WEATHER_CONDITION") and st.get("conditions"):
        return True
    if typ == "CONSTRUCTION" and e.get("severity") == "MAJOR" and st.get("major_construction"):
        return True
    return False


def kind_of(e):
    """What the screen calls it (drives the icon and the sign's second line)."""
    d = (e.get("optimized_description") or e.get("description") or "").lower()
    sub = (e.get("event_sub_type") or "").upper()
    typ = e.get("event_type")
    for words, k in ((("vehicle fire", "fire"), "fire"), (("crash", "collision", "vehicle incident", "mva"), "crash"),
                     (("debris", "obstruction", "object"), "debris"), (("landslide", "rockfall", "mudslide"), "slide"),
                     (("flood",), "flood"), (("snow", "ice", "slippery", "compact"), "winter")):
        if any(w in d for w in words):
            return k
    if typ == "CONSTRUCTION":
        return "construction"
    if typ == "ROAD_CONDITION":
        return "winter"
    if sub == "HAZARD":
        return "hazard"
    return "incident"


def _nearest_cam(cams, e, max_km=3.0):
    pts = _points(e.get("location"))
    if not pts:
        return None
    lo = sum(p[0] for p in pts) / len(pts)
    la = sum(p[1] for p in pts) / len(pts)
    best, bd = None, max_km
    for c in cams:
        cl = (c.get("location") or {}).get("coordinates") or []
        if len(cl) < 2 or not c.get("is_on", True) or c.get("marked_stale"):
            continue
        d = common.km(la, lo, cl[1], cl[0])
        if d < bd:
            best, bd = c, d
    return (best, round(bd, 1)) if best else None


def _fetch_cam(cam):
    from PIL import Image
    url = "https://www.drivebc.ca" + (cam.get("links") or {}).get("imageDisplay", f"/images/{cam['id']}.jpg")
    r = common.session.get(url, timeout=20)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    w, h = img.size
    # crop DriveBC's caption strip (bottom ~6 %), centre-crop to CAM_W:CAM_H and fit
    h2 = int(h * 0.94)
    tw = min(w, int(h2 * CAM_W / CAM_H))
    x0 = (w - tw) // 2
    img = img.crop((x0, 0, x0 + tw, h2)).resize((CAM_W, CAM_H), Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    np.ascontiguousarray(np.asarray(img), dtype=np.uint8).tofile(OUT / f"cam_{cam['id']}.bin")
    return {"id": cam["id"], "name": fold(cam.get("name") or ""), "caption": fold(cam.get("caption") or ""),
            "w": CAM_W, "h": CAM_H, "at": time.time(),
            "updated": _ts(cam.get("last_update_modified")), "orientation": cam.get("orientation") or "",
            "lon": ((cam.get("location") or {}).get("coordinates") or [None, None])[0],
            "lat": ((cam.get("location") or {}).get("coordinates") or [None, None])[1]}


def item_of(e):
    loc = re.sub(r"<br\s*/?>", "\n", e.get("location_description") or "")
    loc = "\n".join(l.strip() for l in loc.split("\n") if l.strip() and l.strip() not in ("Starts:", "Ends:"))
    geom = e.get("location") or {}
    pts = _points(geom)
    if len(pts) > 60:
        pts = common.simplify(pts, 0.0004)
    return {
        "id": e["id"], "type": e.get("event_type"), "subtype": e.get("event_sub_type"), "kind": kind_of(e),
        "severity": e.get("severity"), "closed": bool(e.get("closed")), "category": e.get("display_category"),
        "road": fold(e.get("route_display") or ""), "route_at": fold(e.get("route_at") or ""), "highways": highways(e),
        "direction": e.get("direction_display") or "", "from": fold(e.get("route_from") or ""),
        "to": fold(e.get("route_to") or ""), "landmark": fold(e.get("closest_landmark") or ""), "where": fold(loc),
        "description": fold(e.get("optimized_description") or e.get("description") or ""),
        "geometry": {"type": geom.get("type"), "points": pts},
        "updated": _ts(e.get("last_updated")), "created": _ts(e.get("first_created")),
        "next_update": _ts(e.get("next_update")), "start": _ts(e.get("start")), "end": _ts(e.get("end")),
    }


def run(cfg):
    st = common.settings(cfg, "road_alerts", DEFAULTS)
    from hub.bc import maps, trafficmap
    try:
        trafficmap.render("roads_metro", METRO, 448, 192)
        trafficmap.render("roads_metro_mini", METRO, 120, 52)        # the Incident map's corner minimap
    except Exception as e:
        print(f"overview map: {e}", flush=True)
    if st.get("enabled") is False:
        common.write("drivebc", {"items": [], "disabled": True})
        return {"ok": True, "message": "DriveBC screen off", "files": ["drivebc.json"]}
    r = common.session.get(f"{API}/events/", timeout=40)
    r.raise_for_status()
    events = r.json()
    now = time.time()
    box = [float(v) for v in st.get("bbox") or DEFAULTS["bbox"]]
    metro = [e for e in events if _in_box(e, box)]
    items = [item_of(e) for e in metro if wanted(e, st, now)]
    rank = {"CLOSURE": 0, "MAJOR": 1, "MODERATE": 2, "MINOR": 3}
    items.sort(key=lambda i: (not i["closed"], i["type"] != "INCIDENT", rank.get(i["severity"], 4), -(i["updated"] or 0)))
    # cameras: the nearest one for what's on the wall (DriveBC refreshes stills every few minutes), and for each
    # sample so the previews show a real camera too (those refreshed less often)
    from display.modules.bc_roads import SAMPLES
    old_cams = common.cache("drivebc_cams", {}) or {}
    sample_cams = old_cams.get("samples") or {}
    if st.get("cameras"):
        prev = {i["id"]: i.get("cam") for i in old_cams.get("items", [])}
        stale_samples = now - (old_cams.get("samples_at") or 0) > 1800 or not set(SAMPLES) <= set(sample_cams)
        cams = []
        if items or stale_samples:
            try:
                cams = common.session.get(f"{API}/webcams/", timeout=40).json()
            except Exception as e:
                print(f"webcams: {e}", flush=True)
        keep = set()
        samples_at = old_cams.get("samples_at")

        def attach(key, ev, old, max_age):
            got = _nearest_cam(cams, ev)
            if not got:
                return None
            cam, dist = got
            try:
                if old and old.get("id") == cam["id"] and now - old.get("at", 0) < max_age and (OUT / f"cam_{cam['id']}.bin").exists():
                    out = old
                else:
                    out = _fetch_cam(cam)
                out["km"] = dist
                keep.add(f"cam_{cam['id']}.bin")
                return out
            except Exception as e:
                print(f"camera {cam.get('id')} ({key}): {e}", flush=True)
                return None
        if cams:
            for it in items[:8]:
                src = next((e for e in metro if e["id"] == it["id"]), None)
                cam = attach(it["id"], src, prev.get(it["id"]), 240) if src else None
                if cam:
                    it["cam"] = cam
            if stale_samples:
                sample_cams = {}
                for key, v in SAMPLES.items():
                    pts = (v["item"].get("geometry") or {}).get("points") or []
                    sample_cams[key] = attach(key, {"location": {"type": "LineString", "coordinates": pts}}, None, 0)
                samples_at = now
        keep |= {f"cam_{c['id']}.bin" for c in sample_cams.values() if c}
        for p in OUT.glob("cam_*.bin"):
            if p.name not in keep:
                p.unlink(missing_ok=True)
        common.save_cache("drivebc_cams", {"items": [{"id": i["id"], "cam": i.get("cam")} for i in items], "samples": sample_cams,
                                           "samples_at": samples_at})
    # the real road each event is on (OSM ways matched by route number or name), its zoomed map, and the exact
    # pixels the highlight covers on that map and on the Metro overview (rasterised the way the maps draw roads)
    from hub.util import read_json as _rj
    overview = _rj(maps.OUT / "roads_metro.json", {}) or {}
    keep = []

    def prepare(iid, it):
        try:
            match = trafficmap.match_road((it.get("geometry") or {}).get("points"), it.get("highways"), it.get("road"),
                                          direction=it.get("direction"))
        except Exception as e:
            print(f"road match {iid}: {e}", flush=True)
            match = {}
        pts = [p for l in match.get("lines", []) for p in l] or (it.get("geometry") or {}).get("points")
        hl = {}
        if pts:
            name = "roadevt_" + re.sub(r"[^A-Za-z0-9_-]", "_", iid)
            try:
                meta = trafficmap.render(name, trafficmap.event_area(pts), 448, 192)
                keep.append(name)
                if match:
                    hl[name] = trafficmap.hl_pixels(meta, match)
            except Exception as e:
                print(f"event map {iid}: {e}", flush=True)
        if match and overview:
            hl["roads_metro"] = trafficmap.hl_pixels(overview, match)
        return match.get("lines", []), hl

    for it in items:
        it["road_lines"], it["hl"] = prepare(it["id"], it) if it in items[:8] else ([], {})
    sample_lines, sample_hl = {}, {}
    for key, v in SAMPLES.items():
        sample_lines[key], sample_hl[key] = prepare(f"sample-{key}", v["item"])
    maps.prune("roadevt_", keep)
    counts = {"metro_events": len(metro), "incidents": sum(i["type"] == "INCIDENT" for i in items),
              "closures": sum(i["closed"] for i in items)}
    common.write("drivebc", {"items": items, "counts": counts, "home": list(common.home(cfg)), "sample_lines": sample_lines,
                             "sample_hl": sample_hl, "sample_cams": {k_: v for k_, v in sample_cams.items() if v}})
    for i in items:
        print(f"{i['severity']} {i['kind']}: {i['road']} {i['direction']} — {i['description'][:90]}", flush=True)
    if st.get("takeover") == "closures":
        hot = [i["id"] for i in items if i["closed"] and i["type"] != "CONSTRUCTION" and MAJOR_ROUTES.search(f"{i['road']} {i['route_at']}")]
        if hot:
            common.takeover("road_alert", hot, "drivebc_takeover", log=lambda m: print(m, flush=True))
    return {"ok": True, "message": f"{len(items)} event(s) on the wall · {len(metro)} in Metro Vancouver",
            "events": [f"{i['road']}: {i['kind']}" for i in items[:10]], "files": ["drivebc.json", "drivebc/"]}


if __name__ == "__main__":
    _harness.main(run)
