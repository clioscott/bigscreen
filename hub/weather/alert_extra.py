"""Two more official sources for the alert screen's data pages, written to data/alertextra.json:

* Tides — Fisheries and Oceans Canada (Canadian Hydrographic Service) Integrated Water Level System API
  (api-iwls.dfo-mpo.gc.ca): predicted tide (wlp), observed water level (wlo) where the gauge has one, and the
  predicted high / low tides (wlp-hilo), for the next 36 h (observed: the last 12 h). Levels are metres above
  chart datum; observed minus predicted is the surge.
* AQHI — ECCC Air Quality Health Index (api.weather.gc.ca, OGC API collections aqhi-observations-realtime and
  aqhi-forecasts-realtime): the latest reading and the latest forecast periods for each AQHI area.

No keys. Tides refresh every 3 h (every 20 min while a coastal flood alert is in effect), AQHI hourly (every
20 min with an air-quality alert).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

from hub import paths
from hub.util import atomic_write_json, read_json

OUT = paths.DATA_DIR / "alertextra.json"
IWLS = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
OGC = "https://api.weather.gc.ca/collections"
UA = "BigScreen/2.0 (personal LED weather display; a few requests per hour)"
_s = requests.Session()
_s.headers["User-Agent"] = UA

# DFO station ids (stable): Point Atkinson, Steveston, New Westminster, White Rock
TIDE_STATIONS = [("Point Atkinson", "5cebf1de3d0f4a073c4bb94c"), ("Steveston", "5cebf1e13d0f4a073c4bbf8c"),
                 ("New Westminster", "5cebf1e13d0f4a073c4bbf93"), ("White Rock", "5cebf1de3d0f4a073c4bb933")]
AQHI_BBOX = "-124.0,48.95,-121.3,50.4"             # Lower Mainland, Sea to Sky, Sunshine Coast


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _series(sid, code, start, end, every_min):
    r = _s.get(f"{IWLS}/stations/{sid}/data", params={"time-series-code": code, "from": _iso(start), "to": _iso(end)}, timeout=40)
    r.raise_for_status()
    out, last = [], None
    for p in r.json():
        t = datetime.strptime(p["eventDate"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if every_min and last and (t - last) < timedelta(minutes=every_min):
            continue
        out.append([_iso(t), round(float(p["value"]), 3)])
        last = t
    return out


def tides():
    now = datetime.now(timezone.utc)
    out = []
    for name, sid in TIDE_STATIONS:
        st = {"name": name}
        try:
            st["pred"] = _series(sid, "wlp", now - timedelta(hours=12), now + timedelta(hours=36), 30)
            st["hilo"] = _series(sid, "wlp-hilo", now - timedelta(hours=1), now + timedelta(hours=36), 0)
        except Exception as e:
            st["error"] = str(e)[:120]
            continue
        try:
            st["obs"] = _series(sid, "wlo", now - timedelta(hours=12), now, 15)
        except Exception:
            st["obs"] = []
        out.append(st)
    return out


def aqhi():
    obs = _s.get(f"{OGC}/aqhi-observations-realtime/items",
                 params={"f": "json", "bbox": AQHI_BBOX, "latest": "true", "limit": 200}, timeout=40).json().get("features") or []
    fc = _s.get(f"{OGC}/aqhi-forecasts-realtime/items",
                params={"f": "json", "bbox": AQHI_BBOX, "limit": 1000, "sortby": "-publication_datetime"}, timeout=40).json().get("features") or []
    locs = {}
    for f in obs:
        p = f["properties"]
        locs.setdefault(p["location_id"], {"name": p["location_name_en"], "forecast": []})
        locs[p["location_id"]].update(obs=p.get("aqhi"), obs_time=p.get("observation_datetime"))
    latest = {}
    for f in fc:
        p = f["properties"]
        lid = p["location_id"]
        pub = p.get("publication_datetime")
        if lid not in latest:
            latest[lid] = pub
        if pub != latest[lid]:
            continue
        loc = locs.setdefault(lid, {"name": p["location_name_en"], "forecast": []})
        loc["forecast"].append([p.get("forecast_datetime"), p.get("aqhi")])
    for loc in locs.values():
        loc["forecast"].sort(key=lambda f: str(f[0]))
    return [dict(id=k, **v) for k, v in sorted(locs.items(), key=lambda kv: str(kv[1].get("name") or ""))]


def update(active_hazards=(), log=print):
    old = read_json(OUT, {}) or {}
    ages = dict(old.get("ages") or {})
    now = time.time()
    done, errors = [], []
    jobs = {"tides": (tides, 1200 if "flood" in active_hazards else 3 * 3600),
            "aqhi": (aqhi, 1200 if "smoke" in active_hazards else 3600)}
    for key, (fn, every) in jobs.items():
        if key in old and now - ages.get(key, 0) < every - 20:
            continue
        try:
            old[key] = fn()
            ages[key] = now
            done.append(key)
            log(f"  alert extra {key}: {len(old[key])} entries")
        except Exception as e:
            errors.append(f"{key}: {type(e).__name__}: {e}"[:160])
    old["ages"] = ages
    old["generated_at"] = now
    atomic_write_json(OUT, old)
    return done, errors
