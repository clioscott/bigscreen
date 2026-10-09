"""Add the v2 weather-maps config (areas + tracks mirroring v1's screens) and convert
v1 weather_panels_half rotation entries to the new panel types. Idempotent:

    .venv/bin/python -m tools.add_weather_maps [--force]
"""

import sys

from hub import config

VAN = {"name": "Vancouver", "lat": 49.2827, "lon": -123.1207}
BASE_SAT = {"source": "esri_imagery", "brightness": 0.5, "contrast": 1.05, "saturation": 0.9}

AREAS = {
    "metro": {"label": "Metro Vancouver", "center": [49.22, -123.05], "km_per_px": 0.8, "markers": [VAN],
              "carto": {"regional": {"enabled": True}, "motorways": {"enabled": True}}},
    "pnw": {"label": "Pacific Northwest", "center": [49.2, -123.12], "km_per_px": 6.4, "markers": [VAN]},
    "west": {"label": "Western North America", "center": [49.2, -123.12], "km_per_px": 25.5, "markers": [VAN]},
}

TRACKS = {
    "precip_totals": {"label": "Rain & snow totals", "area": "metro", "size": "half", "kind": "accumulation",
                      "basemap": BASE_SAT, "window_hours": 24, "step_min": 30, "refresh_min": 6,
                      "snow_on_top_cm": 0.5, "fps": 10, "hold_last_sec": 3, "loops": 2},
    "precip_radar": {"label": "Radar", "area": "pnw", "size": "half", "kind": "loop", "basemap": BASE_SAT,
                     "layers": [{"type": "radar", "opacity": 0.9}],
                     "window_min": 180, "step_min": 6, "refresh_min": 6, "fps": 8, "hold_last_sec": 1.5, "loops": 2},
    "goes": {"label": "Satellite (continental)", "area": "west", "size": "half", "kind": "loop", "basemap": BASE_SAT,
             "layers": [{"type": "wms", "layer": "GOES-West_1km_DayVis-NightIR", "opacity": 1.0, "scale": 2}],
             "window_min": 720, "step_min": 30, "refresh_min": 10, "fps": 8, "hold_last_sec": 1.5, "loops": 2},
    "goes_day_night": {"label": "Satellite (regional)", "area": "pnw", "size": "half", "kind": "loop",
                       "basemap": BASE_SAT,
                       "layers": [{"type": "wms", "layer": "GOES-West_1km_DayVis-NightIR", "opacity": 1.0, "scale": 2}],
                       "window_min": 360, "step_min": 10, "refresh_min": 10, "fps": 10, "hold_last_sec": 1.5,
                       "loops": 2},
}

INFO_SCREENS = {"current", "hourly", "forecast", "warning", "minutely"}


def convert_entry(e):
    if e.get("panel") not in ("weather_panels_half", "weather_panels_full"):
        return e
    ws = (e.get("weather_screen") or "").replace("map_", "")
    full = e["panel"].endswith("full")
    if ws in INFO_SCREENS:
        return {"panel": "weather_full" if full else "weather", "weather_screen": ws, "when": e.get("when", "always"),
                "dwell_sec": e.get("dwell_sec", 15)}
    return {"panel": "map_full" if full else "map", "track": ws, "when": e.get("when", "always"),
            "dwell_sec": 120}       # safety cap; the map panel advances itself after its loops


def main():
    cfg = config.load()
    wm = cfg["modules"].get("weather_maps")
    if wm and "--force" not in sys.argv:
        print("weather_maps already present (use --force to reset)")
    else:
        cfg["modules"]["weather_maps"] = {"job_interval_sec": 180, "areas": AREAS, "tracks": TRACKS}
    cyc = cfg["modules"].get("layout_cycle", {})
    if any(e.get("panel", "").startswith("weather_panels") for s in cyc.values() for e in s):
        import copy
        cfg.setdefault("hub", {})["v1_layout_cycle"] = copy.deepcopy(cyc)     # what the v1 display keeps getting
    for side in ("left", "right"):
        cyc[side] = [convert_entry(e) for e in cyc.get(side, [])]
    config.save(cfg, reason="weather maps v2 config")
    print("saved revision", config.load()["revision"])


if __name__ == "__main__":
    main()
