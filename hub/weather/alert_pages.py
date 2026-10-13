"""Weather-specific data pages for the alert screen: after an alert's text and its area map, the wall shows the
ECCC data that matters for that kind of weather — the radar and lightning for a thunderstorm, the gust forecast
for a wind warning, the humidex for a heat warning, the FireWork smoke forecast for an air-quality statement…

Each page is a pack like the weather-map tracks (frames of the Lower Mainland at the full-width map size, synced
to the Pi) in data/alertpages/<page>.bin|.json, plus the model value at a few towns for every frame so the wall
can print real numbers on the map. Forecast pages re-render when a new model run arrives (at most every few
hours); the radar page every 10 min while an alert that uses it is in effect, hourly otherwise.

MSC usage: a forecast page is ~13 GetMap + ~65 GetFeatureInfo requests per model run; the radar page reuses the
radar archive the weather maps keep and adds the lightning frames.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import numpy as np

from hub import paths
from hub.util import atomic_write_json, read_json
from hub.weather import carto, tracks, wms
from hub.weather.geo import alpha_over, render_basemap

OUT = paths.DATA_DIR / "alertpages"
STATE = paths.CACHE_DIR / "alertpages_state.json"
AREA = "metro"                      # the Lower Mainland view the radar archive already keeps

# where the wall prints values (lon, lat); kept to five so labels don't crowd the 448 × 130 map
TOWNS = [("Richmond", -123.1336, 49.1666), ("Squamish", -123.1558, 49.7016), ("Abbotsford", -122.3045, 49.0504),
         ("Gibsons", -123.5050, 49.3975), ("Coquitlam", -122.7932, 49.2838)]

# page id → what to draw. layer/style: the GeoMet image; value: GetFeatureInfo layer (default: the same layer),
# k: unit conversion of the point value, none: what a missing point value means (GeoMet masks zero fields)
PAGES = {
    "radar":      {"title": "Radar and lightning · last 3 hours", "kind": "radar"},
    "precip":     {"title": "Rain and snow forecast · total", "layer": "HRDPS.CONTINENTAL_PR", "style": "PRECIPMM",
                   "unit": "mm", "none": 0.0, "fmt": "{:.0f}"},
    "snowfall":   {"title": "Snowfall forecast · total", "layer": "HRDPS.CONTINENTAL_SN", "style": "Snow-Accum_0to100cm_BlueRed",
                   "unit": "cm", "none": 0.0, "fmt": "{:.0f}", "k": 100.0},
    "snowlevel":  {"title": "Snow level forecast", "layer": "HRDPS-WEonG_2.5km_SnowLevelHeight", "style": "",
                   "unit": "m", "fmt": "{:.0f}"},
    "fzra":       {"title": "Chance of freezing rain", "layer": "HRDPS-WEonG_2.5km_FreezingRain-Prob", "style": "",
                   "unit": "%", "none": 0.0, "fmt": "{:.0f}"},
    "temp":       {"title": "Temperature forecast", "layer": "HRDPS-WEonG_2.5km_AirTemp", "style": "TEMPERATURE-LINEAR",
                   "unit": "°C", "fmt": "{:.0f}"},
    "humidex":    {"title": "Humidex forecast", "layer": "HRDPS.CONTINENTAL_HMX", "style": "Humidex_20to54_Dis",
                   "value": "HRDPS-WEonG_2.5km_AirTemp", "value_title": "temperature", "unit": "°C", "fmt": "{:.0f}"},
    "gust":       {"title": "Wind gust forecast", "layer": "HRDPS-WEonG_2.5km_WindGust", "style": "GUST_MS2KMH",
                   "value": "HRDPS.CONTINENTAL_WSPD", "value_title": "wind", "unit": "km/h", "k": 3.6, "fmt": "{:.0f}"},
    "wind":       {"title": "Wind speed forecast", "layer": "HRDPS.CONTINENTAL_WSPD", "style": "WINDSPEEDKMH",
                   "unit": "km/h", "k": 3.6, "fmt": "{:.0f}"},
    "tstorm":     {"title": "Chance of thunderstorms", "layer": "HRDPS-WEonG_2.5km_Thunderstorm-Prob", "style": "",
                   "unit": "%", "none": 0.0, "fmt": "{:.0f}"},
    "fog":        {"title": "Visibility in fog", "layer": "HRDPS-WEonG_2.5km_LiquidFogVisibility", "style": "",
                   "unit": "km", "k": 0.001, "fmt": "{:.1f}"},
    "smoke":      {"title": "Wildfire smoke forecast (FireWork)", "layer": "RAQDPS.Sfc_PM2.5-WildfireSmokePlume",
                   "style": "", "unit": "µg/m³", "k": 1e9, "none": 0.0, "fmt": "{:.0f}"},
    "sealevel":   {"title": "Sea level forecast", "layer": "CIOPS-West_2km_SeaSfcHeight", "style": "",
                   "unit": "m", "fmt": "{:.1f}"},
    "satellite":  {"title": "Satellite · last 3 hours", "kind": "satellite"},
}
FORECAST_HOURS, STEP_H = 36, 3


def _view(cfg):
    wm = cfg["modules"].get("weather_maps", {})
    area = dict(wm.get("areas", {})[AREA], id=AREA)
    view, _, _ = tracks.views_for(area, "full", cfg)
    return area, view


def _town_px(view):
    out = []
    for name, lon, lat in TOWNS:
        x, y = view.project(lon, lat)
        if 4 <= x < view.w - 4 and 4 <= y < view.h - 4:
            out.append([name, int(x), int(y)])
    return out


def _value(spec, layer, lon, lat, t):
    try:
        props = wms.point_value(layer, lon, lat, t)
    except Exception:
        props = None
    v = props.get("value") if props else None
    if v is None:
        v = spec.get("none")
    return None if v is None else float(v) * float(spec.get("k", 1.0))


def _render_forecast(pid, spec, cfg, log):
    area, view = _view(cfg)
    times_all = wms.layer_times(spec["layer"])
    now = datetime.now(timezone.utc)
    times = [t for t in times_all if now - timedelta(hours=1) <= t <= now + timedelta(hours=FORECAST_HOURS)]
    times = times[::STEP_H] or times_all[:1]
    if not times:
        return {"ok": False, "message": "no forecast times"}
    base = render_basemap(view, {"source": "esri_dark", "brightness": 0.9, "contrast": 1.1})
    mfp = float(area.get("min_feature_px", 3))
    over = carto.render_overlay(view, area.get("carto") or {}, mfp, group="over")
    towns = _town_px(view)
    coords = {n: (lo, la) for n, lo, la in TOWNS}
    frames, values, kept, fill = [], [], [], 0.0
    for t in times:
        try:
            rgba = tracks._wms_frame(spec["layer"], view, t, spec.get("style", ""), 2)
        except Exception as e:
            log(f"  {pid} @ {t}: {e}")
            continue
        fill = max(fill, float((rgba[..., 3] > 0).mean()))    # how much of the map the field paints
        img = alpha_over(base, rgba, 0.85)
        if over is not None:
            img = alpha_over(img, over)
        frames.append(img)
        kept.append(t)
        vl = spec.get("value", spec["layer"])
        values.append([_value(spec, vl, *coords[n], t) for n, _, _ in towns])
    if not frames:
        return {"ok": False, "message": "every frame failed"}
    nonzero = any(v not in (None, 0, 0.0) for row in values for v in row)
    return _write(pid, spec, frames, kept, view, towns, values, kind="forecast", data_frac=fill if (nonzero or fill) else 0.0)


def _render_loop(pid, spec, cfg, log):
    if spec["kind"] == "radar":
        track = {"label": spec["title"], "area": AREA, "size": "full", "kind": "loop", "layers": [{"type": "radar"}],
                 "lightning": True, "nowcast": True, "window_min": 180, "step_min": 10}
    else:
        track = {"label": spec["title"], "area": AREA, "size": "full", "kind": "loop", "window_min": 180, "step_min": 20,
                 "layers": [{"type": "wms", "layer": "GOES-West_1km_DayVis-NightIR", "scale": 2, "time": "past"}]}
    tid = f".alertpage_{pid}"
    res = tracks.render_track(tid, track, cfg, log)
    if not res.get("ok"):
        return res
    src_bin, src_json = tracks.ANIM_DIR / f"{tid}.bin", tracks.ANIM_DIR / f"{tid}.json"
    meta = read_json(src_json, {})
    OUT.mkdir(parents=True, exist_ok=True)
    src_bin.replace(OUT / f"{pid}.bin")
    src_json.unlink(missing_ok=True)
    meta.update(id=pid, title=spec["title"], page_kind=spec["kind"], towns=[], values=[], unit="")
    if spec["kind"] == "radar":                                   # radar / lightning: pixels that change across the loop
        n, h, w = meta["n"], meta["h"], meta["w"]
        fr = np.fromfile(OUT / f"{pid}.bin", dtype=np.uint8).reshape(n, h, w, 3).astype(np.int16)
        meta["data_frac"] = float((np.abs(fr - fr[:1]).max(axis=(0, 3)) > 40).mean()) if n > 1 else 0.0
    else:
        meta["data_frac"] = 1.0
    atomic_write_json(OUT / f"{pid}.json", meta)
    return res


def _write(pid, spec, frames, times, view, towns, values, kind, data_frac=1.0):
    OUT.mkdir(parents=True, exist_ok=True)
    pack = np.ascontiguousarray(np.stack(frames))
    tmp = OUT / f".{pid}.bin.tmp"
    pack.tofile(tmp)
    tmp.replace(OUT / f"{pid}.bin")
    meta = {"id": pid, "title": spec["title"], "page_kind": kind, "w": view.w, "h": view.h, "n": len(frames),
            "times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times], "unit": spec.get("unit", ""),
            "fmt": spec.get("fmt", "{:.0f}"), "value_title": spec.get("value_title", ""),
            "towns": towns, "values": values, "generated_at": time.time(), "attribution": "ECCC",
            "data_frac": round(data_frac, 4)}
    atomic_write_json(OUT / f"{pid}.json", meta)
    return {"ok": True, "message": f"{len(frames)} frames", "frames": len(frames)}


def render(pid, cfg, log=print):
    spec = PAGES[pid]
    if spec.get("kind") in ("radar", "satellite"):
        return _render_loop(pid, spec, cfg, log)
    return _render_forecast(pid, spec, cfg, log)


def update(cfg, active_hazards=(), log=print, progress=None, budget_sec=240):
    """Re-render the pages that are due. Every page is kept (so any theme and weather type can be previewed);
    the ones today's alerts use refresh sooner. Stops after ``budget_sec`` (the rest go next run), pages in use
    first."""
    from display.modules.alert_brief import HAZARD_PAGES
    hot = {p for h in active_hazards for p in HAZARD_PAGES.get(h, [])}
    state = read_json(STATE, {}) or {}
    now = time.time()
    try:
        run = wms.layer_times("HRDPS.CONTINENTAL_PR", max_age=600)[0].isoformat()
    except Exception:
        run = None
    done, errors = [], []
    order = sorted(PAGES, key=lambda p: p not in hot)
    for i, pid in enumerate(order):
        if time.time() - now > budget_sec:
            break
        st = state.get(pid, {})
        age = now - st.get("at", 0)
        loop = PAGES[pid].get("kind") in ("radar", "satellite")
        if loop:
            due = age >= (600 if pid in hot else 3600) - 20
        else:
            due = (run and st.get("run") != run and age >= (1800 if pid in hot else 3 * 3600)) or age >= 6 * 3600
        if not due and (OUT / f"{pid}.json").exists():
            continue
        if progress:
            progress(i, len(PAGES), f"alert page · {PAGES[pid]['title']}")
        t0 = time.time()
        try:
            res = render(pid, cfg, log)
        except Exception as e:
            res = {"ok": False, "message": f"{type(e).__name__}: {e}"}
        log(f"  alert page {pid}: {'ok' if res.get('ok') else 'FAILED'} in {time.time() - t0:.1f}s — {res['message']}")
        if res.get("ok"):
            state[pid] = {"at": now, "run": run}
            done.append(pid)
        else:
            state[pid] = dict(st, tried=now)
            errors.append(f"{pid}: {res['message']}")
        atomic_write_json(STATE, state, indent=1)
    return done, errors
