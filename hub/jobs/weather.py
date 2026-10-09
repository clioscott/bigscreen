"""Environment Canada citypage forecast + SWOB live observations → data/weather.json.

Runs every ~2 min (SWOB cadence). The citypage XML only changes ~hourly, so it is
cached in cache/ and re-fetched every `citypage_refresh_interval_sec` (default 15 min).
"""

import time

import requests

from hub import paths
from hub.jobs import _harness, ec_weather as ec
from hub.util import atomic_write_json, read_json

CACHE = paths.CACHE_DIR / "weather_citypage.json"


def run(cfg):
    w = cfg["modules"].get("weather", {})
    province = w.get("citypage_province", ec.CITYPAGE_PROVINCE)
    station = w.get("citypage_station", ec.CITYPAGE_STATION)
    swob_url = w.get("swob_url", ec.SWOB_URL)
    citypage_every = int(w.get("citypage_refresh_interval_sec", w.get("refresh_interval_sec", 900)))

    session = requests.Session()
    session.headers.update({"User-Agent": "BigScreen/2.0"})
    notes = []

    cached = read_json(CACHE)
    if not cached or time.time() - cached.get("_citypage_fetched", 0) > citypage_every:
        url = ec._resolve_citypage_url(session, province=province, station=station)
        if url:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                cached = ec._parse_citypage(r.content)
                cached["_citypage_fetched"] = time.time()
                atomic_write_json(CACHE, cached)
                notes.append("forecast refreshed")
            else:
                notes.append(f"citypage HTTP {r.status_code}")
        else:
            notes.append("citypage URL not found")
    if not cached:
        return {"ok": False, "message": "; ".join(notes) or "no citypage data"}

    data = dict(cached)
    swob = ec._fetch_swob(session, url=swob_url)
    if swob:
        ec._merge_swob_into_current(data, swob)
    else:
        notes.append("SWOB unavailable")
    data["_fetched_utc"] = time.time()
    data["_source"] = "ec_citypage+swob"
    atomic_write_json(paths.DATA_DIR / "weather.json", data)

    cur = data.get("current", {})
    return {"ok": True, "message": f"{cur.get('temp_c', '?')}°C {cur.get('condition', '')}".strip()
            + (f" ({'; '.join(notes)})" if notes else ""), "files": ["weather.json"]}


if __name__ == "__main__":
    _harness.main(run)
