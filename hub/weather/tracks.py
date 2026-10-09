"""Render map animation tracks into packs the display plays back.

Config: modules.weather_maps
    areas:  {id: {label, center: [lat, lon], km_per_px, markers: [{name, lat, lon}],
                  carto: {<layer>: {enabled, color, opacity, width, min_km_px, max_km_px}},
                  min_feature_px}}
    tracks: {id: {label, enabled, area, size: "half"|"full", kind: "loop"|"accumulation",
                  basemap: {source, brightness, contrast, saturation, water, land},
                  layers: [...], window_min | window_hours, step_min, refresh_min,
                  fps, hold_last_sec, loops, timestamp, legend}}
    palettes: {name: [[threshold, "#rrggbb"], ...]}   (overrides hub/weather/radar.py PALETTES)

Loop layers (drawn in order over the basemap, under the cartography):
    {"type": "wms", "layer": "GOES-West_1km_DayVis-NightIR", "style": "", "opacity": 1, "scale": 2,
     "time": "past" | "forecast" | "static"}
    {"type": "radar", "opacity": 0.9}                     live rate, TV colours, from the archive

Pack output (synced to the Pi): data/anim/<id>.bin  = n frames of (h, w, 3) uint8
                                data/anim/<id>.json = {w, h, n, fps, times, markers, legend, ...}
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone

import numpy as np
from PIL import Image

from hub import paths
from hub.util import atomic_write_json
from hub.weather import carto, radar, wms
from hub.weather.geo import View, alpha_over, render_basemap, shade_day_night

ANIM_DIR = paths.DATA_DIR / "anim"
WMS_CACHE = paths.CACHE_DIR / "wms"


def zone_sizes(cfg):
    """(half_w, full_w, h) from the display layout — packs must match the zones exactly."""
    L = cfg.get("layout", {})
    w, h = L.get("width", 448), L.get("height", 192)
    top = L.get("top_bar_height", 44)
    main_h = (h - 17) - (top + 1)
    half_w = int(w * L.get("calendar_split_ratio", 0.5))
    return half_w, w, main_h


def views_for(area, size, cfg):
    """(display view, archive view, crop) — archives are full width; half views are their centre crop."""
    half_w, full_w, h = zone_sizes(cfg)
    full = View(area, full_w, h, fit_width=half_w)      # same scale as the half view (corner-fit areas)
    if size == "full":
        return full, full, None
    x0 = (full_w - half_w) // 2
    return View(area, half_w, h), full, (x0, x0 + half_w)


def _frame_times(all_times, start, end, step_min):
    """Times within [start, end], thinned so consecutive frames are >= step_min apart (keeps the newest)."""
    sel = [t for t in all_times if start <= t <= end]
    out = []
    for t in reversed(sel):
        if not out or (out[-1] - t) >= timedelta(minutes=step_min) - timedelta(seconds=30):
            out.append(t)
    return out[::-1]


def _wms_frame(layer, view, t, style, scale):
    """WMS frame cached on disk per view/layer/time (GOES etc. are re-used across renders)."""
    key = hashlib.sha1(f"{view.key}|{layer}|{style}|{scale}".encode()).hexdigest()[:12]
    d = WMS_CACHE / key
    p = d / (t.strftime("%Y%m%dT%H%M") + ".png") if t else d / "static.png"
    if p.exists():
        return np.asarray(Image.open(p).convert("RGBA"))
    arr = wms.get_map(layer, view, t, style=style, scale=scale)
    d.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGBA").save(p)
    return arr


def prune_wms_cache(max_age_h=50):
    cutoff = time.time() - max_age_h * 3600
    for p in WMS_CACHE.glob("*/*.png"):
        if p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)


LABEL_DEFAULTS = {"enabled": True, "font": "tom-thumb.bdf", "color": "#ffe65a", "dot": True,
                  "dot_color": "#ffe65a", "outline": True}


def label_style(area):
    lab = area.get("labels") or {}
    return {k: lab.get(k, v) for k, v in LABEL_DEFAULTS.items()}


def _markers(view, area):
    """City labels inside the view -> pixel positions. Each city may override font/colour."""
    lab = area.get("labels") or {}
    if lab.get("enabled", True) is False:
        return []
    cities = lab.get("cities")
    if cities is None:
        cities = area.get("markers") or []        # pre-labels configs
    out = []
    for m in cities:
        if m.get("enabled", True) is False:
            continue
        x, y = view.project(float(m["lon"]), float(m["lat"]))
        if 0 <= x < view.w and 0 <= y < view.h:
            item = {"name": m.get("name", ""), "x": int(round(x)), "y": int(round(y))}
            for k in ("font", "color", "anchor"):
                if m.get(k):
                    item[k] = m[k]
            out.append(item)
    return out


def _basemap_for(base, view, track, t, under=None):
    """Basemap for frame time t: day/night shading (basemap only), then the under-data layers (highways)."""
    dn = (track.get("basemap") or {}).get("daynight") or {}
    if dn.get("enabled"):
        base = shade_day_night(base, view, t, float(dn.get("night_brightness", 0.25)),
                               float(dn.get("twilight_deg", 6)))
    return alpha_over(base, under) if under is not None and under[..., 3].any() else base


def _legend_spec(pals):
    return [{"label": label, "unit": unit, "bands": [[t, c] for t, c in pals[name]]}
            for name, label, unit in (("rain_total", "Rain", "mm"), ("snow_total", "Snow", "cm"))]


def render_track(tid, track, cfg, log=print):
    wm = cfg["modules"].get("weather_maps", {})
    area = dict(wm.get("areas", {})[track["area"]], id=track["area"])
    view, arch_view, crop = views_for(area, track.get("size", "half"), cfg)
    pals = {k: radar.palette(wm.get("palettes"), k) for k in radar.PALETTES}

    base = render_basemap(view, track.get("basemap") or {})
    bm = track.get("basemap") or {}
    if bm.get("water") or bm.get("land"):
        base = carto.tint_water(base, view, bm.get("water"), bm.get("land"), float(bm.get("tint_strength", 1.0)),
                                float(area.get("min_feature_px", 3)))
    carto_cfg = {**(area.get("carto") or {}), **(track.get("carto") or {})}
    mfp = float(area.get("min_feature_px", 3))
    under = carto.render_overlay(view, carto_cfg, mfp, group="under")      # highways: above basemap, below data
    overlay = carto.render_overlay(view, carto_cfg, mfp, group="over")
    now = datetime.now(timezone.utc)
    kind = track.get("kind", "loop")
    frames, times = [], []

    if kind == "accumulation":
        hours = float(track.get("window_hours", 24))
        arch = [t for t in radar.archive_times(arch_view) if t > now - timedelta(hours=hours)]
        if not arch:
            return {"ok": False, "message": "no radar archived yet for this area"}
        show = set(_frame_times(arch, arch[0], arch[-1], float(track.get("step_min", 30))))
        show.add(arch[-1])
        for t, rain_mm, snow_cm in radar.accumulate(arch_view, arch, crop):
            if t in show:
                layer = radar.accumulation_layer(rain_mm, snow_cm, wm.get("palettes"),
                                                 float(track.get("snow_on_top_cm", 0.5)))
                img = alpha_over(_basemap_for(base, view, track, t, under), layer)
                frames.append(alpha_over(img, overlay) if overlay is not None else img)
                times.append(t)
        coverage = f"{(arch[-1] - arch[0]).total_seconds() / 3600:.1f} of {hours:g} h archived"
    else:
        window = timedelta(minutes=float(track.get("window_min", 180)))
        step = float(track.get("step_min", 10))
        layers = track.get("layers") or []
        # The first time-varying layer drives the frame times.
        driver = next((l for l in layers if l.get("type") == "radar" or l.get("time", "past") != "static"), None)
        if driver is None:
            driver_times = [now]
        elif driver.get("type") == "radar":
            driver_times = radar.archive_times(arch_view)
        else:
            driver_times = wms.layer_times(driver["layer"])
        if driver is not None and driver.get("time") == "forecast":
            times = _frame_times(driver_times, now - timedelta(minutes=step), now + window, step)
        else:
            times = _frame_times(driver_times, now - window, now + timedelta(minutes=5), step)
        if not times:
            return {"ok": False, "message": "no frames available in the time window"}
        layer_times = {id(l): (radar.archive_times(arch_view) if l.get("type") == "radar"
                               else ([] if l.get("time") == "static" else wms.layer_times(l["layer"])))
                       for l in layers}
        kept = []
        for t in times:
            img = _basemap_for(base, view, track, t, under)   # day/night shades the basemap only
            missing = False
            for l in layers:
                lt = layer_times[id(l)]
                if l.get("type") != "radar" and l.get("time") == "static":
                    src_t = None
                else:
                    # nearest available time at or before the frame (within 20 min)
                    cand = [x for x in lt if x <= t + timedelta(minutes=1) and t - x <= timedelta(minutes=20)]
                    if not cand:
                        if l is driver:
                            missing = True
                        continue
                    src_t = cand[-1]
                try:
                    if l.get("type") == "radar":
                        rain, snow, ptype = radar.load_frame(arch_view, src_t, crop)
                        img = alpha_over(img, radar.rate_layer(rain, snow, ptype, wm.get("palettes")))
                    else:
                        rgba = _wms_frame(l["layer"], view, src_t, l.get("style", ""), int(l.get("scale", 2)))
                        img = alpha_over(img, rgba, float(l.get("opacity", 1.0)))
                except wms.FrameGap:
                    missing = missing or l is driver
                except Exception as e:
                    log(f"  {tid}: {l.get('layer', 'radar')} @ {src_t}: {e}")
                    missing = missing or l is driver
            if missing:
                continue
            frames.append(alpha_over(img, overlay) if overlay is not None else img)
            kept.append(t)
        times = kept
        coverage = f"{len(times)} frames"
        if not frames:
            return {"ok": False, "message": "every frame failed to fetch"}

    pack = np.ascontiguousarray(np.stack(frames))
    ANIM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ANIM_DIR / f".{tid}.bin.tmp"
    pack.tofile(tmp)
    tmp.replace(ANIM_DIR / f"{tid}.bin")
    meta = {
        "id": tid, "label": track.get("label", tid), "kind": kind, "w": view.w, "h": view.h, "n": len(frames),
        "size": track.get("size", "half"),
        "fps": float(track.get("fps", 8)), "hold_last_sec": float(track.get("hold_last_sec", 1.5)),
        "loops": int(track.get("loops", 2)), "timestamp": track.get("timestamp", True),
        "times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times],
        "markers": _markers(view, area) if track.get("labels", track.get("markers", True)) else [],
        "label_style": label_style(area),
        "legend": _legend_spec(pals) if kind == "accumulation" and track.get("legend", True) else None,
        "window_hours": float(track.get("window_hours", 24)) if kind == "accumulation" else None,
        "generated_at": time.time(), "attribution": "ECCC",
    }
    atomic_write_json(ANIM_DIR / f"{tid}.json", meta)
    return {"ok": True, "message": f"{len(frames)} frames ({coverage})", "frames": len(frames),
            "bytes": int(pack.nbytes)}
