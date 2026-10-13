"""Forecast numbers for the alert screen's special pages: ECCC model values (HRDPS 2.5 km, RAQDPS smoke, CIOPS
sea level) at one town per wall region, every 3 hours for the next 36 — temperature, dew point, wind,
precipitation (and its chance), snow, snow level, freezing rain and thunderstorm chances, fog visibility,
wildfire smoke and total PM2.5, sea level at the coast.

Written to data/alertdata.json (synced to the Pi):
    {"generated_at", "run", "times": [iso…], "towns": [{name, region, lon, lat}], "sea": [{name, lon, lat}],
     "series": {var: {"unit", "label", "values": [[per time] per town]}}}

MSC usage: GetFeatureInfo, ~13 times × 10 towns per variable, refreshed when a new model run is out (at most
every 3 h; hourly for the variables today's alerts use), four requests at a time.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from hub import paths
from hub.util import atomic_write_json, read_json
from hub.weather import wms

OUT = paths.DATA_DIR / "alertdata.json"
HOURS, STEP_H = 36, 3

# one town per wall region (alert_brief.REGIONS order), so a page can show the alert's own regions
TOWNS = [("Richmond", "Richmond", -123.1336, 49.1666), ("Vancouver", "Vancouver", -123.1207, 49.2827),
         ("North Van", "North Shore", -123.0724, 49.3200), ("Coquitlam", "Tri-Cities", -122.7932, 49.2838),
         ("Surrey", "Surrey", -122.8490, 49.1913), ("Squamish", "Howe Sound", -123.1558, 49.7016),
         ("Whistler", "Sea to Sky", -122.9574, 50.1163), ("Abbotsford", "Abbotsford", -122.3045, 49.0504),
         ("Chilliwack", "Chilliwack", -121.9514, 49.1579), ("Sechelt", "Sunshine Coast", -123.7550, 49.4742)]
SEA = [("Steveston", -123.20, 49.11), ("Pt Atkinson", -123.27, 49.33), ("White Rock", -122.83, 49.015),
       ("Gibsons", -123.50, 49.39)]

# var → (layer, factor, value when GeoMet masks the point, unit, label, where)
VARS = {
    "temp":      ("HRDPS-WEonG_2.5km_AirTemp", 1.0, None, "°C", "Temperature", "towns"),
    "dew":       ("HRDPS-WEonG_2.5km_DewPointTemp", 1.0, None, "°C", "Dew point", "towns"),
    "wind":      ("HRDPS.CONTINENTAL_WSPD", 3.6, None, "km/h", "Wind", "towns"),
    "precip":    ("HRDPS.CONTINENTAL_PR", 1.0, 0.0, "mm", "Precipitation (total)", "towns"),
    "pop":       ("HRDPS-WEonG_2.5km_Precip-Prob", 1.0, 0.0, "%", "Chance of precipitation", "towns"),
    "snow":      ("HRDPS-WEonG_2.5km_SolidSnowCondAmt", 100.0, 0.0, "cm", "Snow (per hour, if it falls)", "towns"),
    "snowlevel": ("HRDPS-WEonG_2.5km_SnowLevelHeight", 1.0, None, "m", "Snow level", "towns"),
    "fzra":      ("HRDPS-WEonG_2.5km_FreezingRain-Prob", 1.0, 0.0, "%", "Chance of freezing rain", "towns"),
    "tstorm":    ("HRDPS-WEonG_2.5km_Thunderstorm-Prob", 1.0, 0.0, "%", "Chance of thunderstorms", "towns"),
    "vis":       ("HRDPS-WEonG_2.5km_LiquidFogVisibility", 0.001, None, "km", "Visibility in fog", "towns"),
    "smoke":     ("RAQDPS.Sfc_PM2.5-WildfireSmokePlume", 1e9, 0.0, "µg/m³", "Wildfire smoke PM2.5", "towns"),
    "pm25":      ("RAQDPS.SFC_PM2.5", 1e9, 0.0, "µg/m³", "PM2.5 (all sources)", "towns"),
    "sealevel":  ("CIOPS-West_2km_SeaSfcHeight", 1.0, None, "m", "Sea level", "sea"),
}


def _point(layer, lon, lat, t):
    for attempt in range(2):
        try:
            props = wms.point_value(layer, lon, lat, t)
            return props.get("value") if props else None
        except Exception:
            time.sleep(1 + attempt)
    return None


def _series(var, times):
    layer, k, none, _, _, where = VARS[var]
    pts = [(lo, la) for _, _, lo, la in TOWNS] if where == "towns" else [(lo, la) for _, lo, la in SEA]
    avail = wms.layer_times(layer)
    jobs = []
    for i, (lo, la) in enumerate(pts):
        for j, t in enumerate(times):
            # nearest available time at or before t (RAQDPS / CIOPS steps differ from HRDPS)
            cand = [x for x in avail if x <= t + timedelta(minutes=1)]
            jobs.append((i, j, cand[-1] if cand else None, lo, la))
    out = [[None] * len(times) for _ in pts]
    with ThreadPoolExecutor(4) as ex:
        for (i, j, t, lo, la), v in zip(jobs, ex.map(lambda q: _point(layer, q[3], q[4], q[2]) if q[2] else None, jobs)):
            if v is None:
                v = none
            out[i][j] = None if v is None else round(float(v) * k, 2)
    return out


def update(cfg, active_vars=(), log=print, budget_sec=240):
    """Refresh variables that are due; returns (updated vars, errors)."""
    old = read_json(OUT, {}) or {}
    try:
        run = wms.layer_times("HRDPS-WEonG_2.5km_AirTemp", max_age=600)[0].isoformat()
    except Exception as e:
        return [], [f"alert data: model times: {e}"]
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    times = [now + timedelta(hours=h) for h in range(0, HOURS + 1, STEP_H)]
    iso = [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]
    fresh = old.get("times") == iso
    series = dict(old.get("series") or {}) if fresh else {}
    ages = dict(old.get("ages") or {}) if fresh else {}
    t0, done, errors = time.time(), [], []
    order = sorted(VARS, key=lambda v: v not in active_vars)
    for var in order:
        if time.time() - t0 > budget_sec:
            break
        age = time.time() - ages.get(var, 0)
        due = var not in series or (old.get("run") != run and age > 3 * 3600) or age > (3600 if var in active_vars else 6 * 3600)
        if not due:
            continue
        try:
            vals = _series(var, times)
        except Exception as e:
            errors.append(f"alert data {var}: {e}"[:160])
            continue
        layer, k, none, unit, label, where = VARS[var]
        series[var] = {"unit": unit, "label": label, "where": where, "values": vals}
        ages[var] = time.time()
        done.append(var)
        log(f"  alert data {var}: {sum(v is not None for row in vals for v in row)} values")
        atomic_write_json(OUT, {"generated_at": time.time(), "run": run, "times": iso,
                                "towns": [{"name": n, "region": r, "lon": lo, "lat": la} for n, r, lo, la in TOWNS],
                                "sea": [{"name": n, "lon": lo, "lat": la} for n, lo, la in SEA],
                                "series": series, "ages": ages})
    return done, errors
