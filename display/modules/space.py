"""Caption sections for a webcam in space (``cams.<id>.space: "iss"``), from data/iss.json (hub/jobs/iss.py):
what the ISS is over, its next orbital sunrise or sunset, the local time below, who is aboard (with flags) and the
mission. Everything is for the moment the video shows: webcams play ``LAG`` seconds behind real time, and the ISS
covers ~270 km in that."""

import bisect
import time
from datetime import datetime

from display.modules.flags import flag_char

LAG = 35.0                   # hub/webcams.py DELAY_SEC: what's on the wall was filmed this long ago
KEYS = ("over", "coords", "orbit", "below", "crew", "mission")   # the sections a webcam in space can show
_times: dict = {}            # id(rows) -> their times (the caption is drawn every frame: no list built each time)


def _ts(rows):
    k = id(rows)
    hit = _times.get(k)
    if hit is None or hit[0] is not rows:
        if len(_times) > 16:
            _times.clear()
        hit = _times[k] = (rows, [r[0] for r in rows])
    return hit[1]


def _at(rows, t):
    """The row in [[t, …], …] (sorted) in force at t: the last one at or before it, or None."""
    if not rows:
        return None
    i = bisect.bisect_right(_ts(rows), t) - 1
    return rows[i] if i >= 0 else None


def position(track, t):
    """(lat, lon) at t, between the track's points (10 s apart: a straight line is within metres of the orbit)."""
    ts = _ts(track)
    i = bisect.bisect_right(ts, t) - 1
    if i < 0 or i + 1 >= len(track):
        return None
    (t0, la0, lo0, _), (t1, la1, lo1, _) = track[i], track[i + 1]
    f = (t - t0) / max(1e-6, t1 - t0)
    d = lo1 - lo0
    if d > 180:                                          # crossing the date line
        d -= 360
    elif d < -180:
        d += 360
    lon = (lo0 + f * d + 540.0) % 360.0 - 180.0
    return la0 + f * (la1 - la0), lon


def coords(lat, lon) -> str:
    """'12.35°S 145.68°W'."""
    return f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"


def _mins(sec) -> str:
    """A countdown that ticks every second: '13:42'."""
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def sections(data, ov, show, now=None, lag=LAG) -> dict:
    """{"over", "orbit", "below", "crew", "mission"}: the texts that are on and have data ("" otherwise)."""
    out = {k: "" for k in KEYS}
    if not isinstance(data, dict):
        return out
    t = (now or time.time()) - lag
    track = data.get("track") or []
    if not track or t > track[-1][0] + 60:               # the job hasn't run for hours: nothing rather than old
        return out
    if show.get("over"):
        row = _at(data.get("over"), t)
        out["over"] = row[1] if row else ""
    if show.get("coords"):
        p = position(track, t)
        out["coords"] = coords(*p) if p else ""
    if show.get("orbit"):
        here = _at(track, t)
        nxt = next((e for e in data.get("events") or [] if e[0] > t), None)
        if here and nxt:
            out["orbit"] = ("Daylight" if here[3] else "Night") + f" · {nxt[1]} in {_mins(nxt[0] - t)}"
    if show.get("below"):
        row = _at(data.get("tz"), t)
        if row:
            try:
                from zoneinfo import ZoneInfo
                lt = datetime.fromtimestamp(t, ZoneInfo(row[1]))
                hhmm = lt.strftime("%I:%M %p").lstrip("0") if ov.get("time_format") == "12h" else lt.strftime("%H:%M")
                out["below"] = f"{hhmm} below"
            except Exception:
                pass
    c = data.get("crew")
    if show.get("crew") and isinstance(c, dict) and c.get("count"):
        s = f"{c['count']} aboard"
        if ov.get("crew_flags", True) and c.get("by"):
            s += ": " + " ".join(f"{n}{flag_char(code) or ' ' + str(code).upper()}" for code, n in c["by"])
        out["crew"] = s
    m = data.get("mission")
    if show.get("mission") and isinstance(m, dict) and m.get("name"):
        s = m["name"]
        if ov.get("mission_days") and m.get("start"):
            s += f" · day {int((t - float(m['start'])) // 86400) + 1}"
        out["mission"] = s
    return out
