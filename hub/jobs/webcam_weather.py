"""Weather and sunrise/sunset at each webcam → data/webcam_weather.json (for the webcam captions).

Only webcams with a ``lat`` and ``lon`` are fetched, all in one Open-Meteo request (free, no key):
the current temperature, feels-like, humidity, wind, weather code and day/night, today's high and low, and the next
two days' sunrise and sunset.
Output, keyed by webcam id: {"temp", "feels", "humidity", "wind", "wind_dir", "gust", "code", "is_day", "high", "low",
"sunrise": [epoch, …], "sunset": [epoch, …], "lat", "lon", "at"}.
``fetch`` is also used by the hub's previews for a location that hasn't been saved (and fetched) yet.
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
        pt = point(cam)
        if pt:
            out[cid] = pt
    return out


def point(cam) -> tuple | None:
    """(lat, lon) rounded to 4 places, or None when the webcam has no usable location."""
    try:
        lat, lon = float(cam["lat"]), float(cam["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if -90 <= lat <= 90 and -180 <= lon <= 180:
        return round(lat, 4), round(lon, 4)
    return None


def fetch(points: dict, timeout=30) -> dict:
    """{key: (lat, lon)} → {key: weather}, in one request."""
    if not points:
        return {}
    keys = list(points)
    params = {"latitude": ",".join(str(points[k][0]) for k in keys),
              "longitude": ",".join(str(points[k][1]) for k in keys),
              "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,wind_direction_10m,"
                         "wind_gusts_10m,weather_code,is_day",
              "daily": "sunrise,sunset,temperature_2m_max,temperature_2m_min", "forecast_days": 2,
              "timezone": "auto", "timeformat": "unixtime"}             # auto: "today" is the webcam's own day
    r = requests.get(URL, params=params, headers=UA, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    rows = data if isinstance(data, list) else [data]
    out = {}
    for key, row in zip(keys, rows):
        cur, daily = row.get("current") or {}, row.get("daily") or {}
        first = lambda k: (daily.get(k) or [None])[0]
        out[key] = {"temp": cur.get("temperature_2m"), "code": cur.get("weather_code"), "is_day": cur.get("is_day", 1),
                    "feels": cur.get("apparent_temperature"), "humidity": cur.get("relative_humidity_2m"),
                    "wind": cur.get("wind_speed_10m"), "wind_dir": cur.get("wind_direction_10m"),
                    "gust": cur.get("wind_gusts_10m"), "high": first("temperature_2m_max"),
                    "low": first("temperature_2m_min"),
                    "sunrise": daily.get("sunrise") or [], "sunset": daily.get("sunset") or [],
                    "lat": points[key][0], "lon": points[key][1], "at": time.time()}
    return out


def run(cfg):
    from hub.util import atomic_write_json
    out = fetch(located(cfg))
    atomic_write_json(paths.DATA_DIR / "webcam_weather.json", out)
    return {"ok": True, "webcams": len(out)}


if __name__ == "__main__":
    _harness.main(run)
