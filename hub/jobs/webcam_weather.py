"""Weather and sunrise/sunset at each webcam → data/webcam_weather.json (for the webcam captions).

Only webcams with a ``lat`` and ``lon`` are fetched, all in one Open-Meteo request (free, no key):
the current temperature, weather code and day/night, and the next two days' sunrise and sunset.
Output, keyed by webcam id: {"temp", "code", "is_day", "sunrise": [epoch, …], "sunset": [epoch, …], "at"}.
"""

from __future__ import annotations

import time

import requests

from hub import paths
from hub.jobs import _harness

URL = "https://api.open-meteo.com/v1/forecast"
UA = {"User-Agent": "BigScreen/2 (home LED wall; webcam captions)"}


def located(cfg) -> dict:
    cams = (((cfg.get("modules") or {}).get("webcams") or {}).get("cams") or {})
    out = {}
    for cid, cam in cams.items():
        try:
            lat, lon = float(cam["lat"]), float(cam["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            out[cid] = (round(lat, 4), round(lon, 4))
    return out


def run(cfg):
    from hub.util import atomic_write_json
    cams = located(cfg)
    out_path = paths.DATA_DIR / "webcam_weather.json"
    if not cams:
        atomic_write_json(out_path, {})
        return {"ok": True, "webcams": 0}
    ids = list(cams)
    params = {"latitude": ",".join(str(cams[i][0]) for i in ids), "longitude": ",".join(str(cams[i][1]) for i in ids),
              "current": "temperature_2m,weather_code,is_day", "daily": "sunrise,sunset", "forecast_days": 2,
              "timezone": "GMT", "timeformat": "unixtime"}
    r = requests.get(URL, params=params, headers=UA, timeout=30)
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else [data]
    out = {}
    for cid, row in zip(ids, rows):
        cur, daily = row.get("current") or {}, row.get("daily") or {}
        out[cid] = {"temp": cur.get("temperature_2m"), "code": cur.get("weather_code"), "is_day": cur.get("is_day", 1),
                    "sunrise": daily.get("sunrise") or [], "sunset": daily.get("sunset") or [], "at": time.time()}
    atomic_write_json(out_path, out)
    return {"ok": True, "webcams": len(out)}


if __name__ == "__main__":
    _harness.main(run)
