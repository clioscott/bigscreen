"""What matters about an alert beyond its text: the forecast numbers for its kind of weather (from the hub's
data/alertdata.json), the alert's own details (times, the full list of municipalities), and its source.

Sources: everything today is ECCC (Environment and Climate Change Canada public weather alerts). An alert from
another feed (tsunami, wildfire evacuation, Alert Ready…) carries ``source`` and its own fields; ``facts`` and
the special pages fall back to what any alert has (level, where, when) for a source they don't know.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from display.gfx import resolve_asset
from display.modules import alert_brief
from display.modules import alert_parts as ap

SOURCES = {"eccc": {"name": "Environment and Climate Change Canada", "short": "ECCC"}}
ELEVATION_M = {"Richmond": 1, "Vancouver": 70, "North Van": 120, "Coquitlam": 100, "Surrey": 80, "Squamish": 10,
               "Whistler": 670, "Abbotsford": 40, "Chilliwack": 10, "Sechelt": 30}
_DATA = {}
_MUNI = []


def source_of(ctx):
    return SOURCES.get((ctx.b.get("source") or "eccc").lower(), {"name": ctx.b.get("source") or "", "short": ctx.b.get("source") or ""})


# ------------------------------------------------------------------------------------------------ forecast data
def data(data_dir):
    """The hub's forecast series (cached; re-read when the file changes)."""
    if not data_dir:
        return None
    p = os.path.join(data_dir, "alertdata.json")
    try:
        mt = os.stat(p).st_mtime
    except OSError:
        return None
    hit = _DATA.get(p)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    d["_times"] = [datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone() for t in d.get("times") or []]
    _DATA[p] = (mt, d)
    return d


def series(d, var):
    s = (d or {}).get("series", {}).get(var)
    return s if s and s.get("values") else None


def towns(d):
    return [t["name"] for t in (d or {}).get("towns") or []]


def town_order(ctx, d):
    """Town indices: the alert's own regions first (home first of all), then the rest."""
    hit = ap.affected(ctx)
    tw = (d or {}).get("towns") or []
    home = getattr(ctx, "home_name", "Richmond")
    mine = [i for i, t in enumerate(tw) if t["region"] in hit]
    mine.sort(key=lambda i: tw[i]["region"] != home)
    return mine, [i for i in range(len(tw)) if i not in mine]


def total(vals):
    """Accumulated-field total over the window (the field counts up from the model start)."""
    vs = [v for v in vals if v is not None]
    return max(0.0, vs[-1] - vs[0]) if len(vs) > 1 else 0.0


def steps(vals):
    """Per-step amounts of an accumulated field."""
    out = [None]
    for a, b in zip(vals, vals[1:]):
        out.append(None if a is None or b is None else max(0.0, b - a))
    return out


def peak(vals, times, low=False):
    """(value, time) of the highest (or lowest) value."""
    idx = [i for i, v in enumerate(vals) if v is not None]
    if not idx:
        return None, None
    i = (min if low else max)(idx, key=lambda k: vals[k])
    return vals[i], times[i]


def humidex(t, td):
    import math
    if t is None or td is None:
        return None
    e = 6.11 * math.exp(5417.7530 * (1 / 273.16 - 1 / (273.15 + td)))
    h = t + 0.5555 * (e - 10.0)
    return h if h > t else t


def wind_chill(t, v):
    if t is None or v is None:
        return None
    if t > 10 or v < 5:
        return t
    return 13.12 + 0.6215 * t - 11.37 * v ** 0.16 + 0.3965 * t * v ** 0.16


def when(dt):
    if dt is None:
        return ""
    h = dt.hour % 12 or 12
    return f"{dt.strftime('%a')} {h}{'am' if dt.hour < 12 else 'pm'}"


def num(v, unit="", digits=0):
    """'2,736 m', '12 mm', '-3°C', '0.4 km'."""
    if v is None:
        return "-"
    s = f"{v:,.{digits}f}"
    if unit in ("°C", "°"):
        return f"{s}°"
    return f"{s} {unit}".strip()


# ------------------------------------------------------------------------------------------------ the alert's own details
def municipalities(ctx):
    """[(wall region name, region code, [municipalities])] for the alert's ECCC regions."""
    if not _MUNI:
        try:
            with open(resolve_asset("alerts/municipalities.json"), encoding="utf-8") as f:
                _MUNI.append(json.load(f).get("regions") or {})
        except OSError:
            _MUNI.append({})
    table = _MUNI[0]
    out = {}
    for r in ctx.b.get("regions") or []:
        head = r.split(" including ")[0].strip()
        names = table.get(head)
        if names is None:
            names = next((v for k, v in table.items() if head.startswith(k) or k.startswith(head)), None)
        if names is None:
            names = alert_brief.cities([r])
        labs = alert_brief.region_labels([r], getattr(ctx, "home_name", "Richmond"))
        name, code = labs[0] if labs else (head, head.upper())
        cur = out.setdefault(name, (code, []))
        for n in names:
            if n not in cur[1]:
                cur[1].append(n)
    return [(n, c, ms) for n, (c, ms) in out.items()]


def alert_times(ctx):
    """(issued, expires, event_end) as local datetimes (or None)."""
    def dt(ts):
        return datetime.fromtimestamp(ts).astimezone() if ts else None
    return dt(ctx.b.get("_issued_ts")), dt(ctx.b.get("_expires_ts")), dt(ctx.b.get("_event_end"))


# ------------------------------------------------------------------------------------------------ key facts by weather type
def key_facts(ctx, d):
    """[(label, value, sub)] — the two or three numbers that matter most for this alert's weather, from the
    forecast where there is one; the alert's own amount first when it gives one."""
    out = []
    if ctx.b.get("amount"):
        out.append(("Expected", ctx.b["amount"], "in the alert"))
    if not d or (ctx.b.get("source") or "eccc").lower() != "eccc":
        return out
    hz = ctx.b["hazard"]
    T = d["_times"]
    names = towns(d)
    mine, rest = town_order(ctx, d)
    pool = mine or rest
    home = pool[0] if pool else 0

    def best(var, low=False, transform=None):
        s = series(d, var)
        if not s:
            return None
        cands = []
        for i in pool:
            vals = s["values"][i] if i < len(s["values"]) else []
            if transform:
                vals = transform(i, vals)
            v, t = peak(vals, T, low)
            if v is not None:
                cands.append((v, t, i))
        if not cands:
            return None
        return (min if low else max)(cands, key=lambda x: x[0])

    if hz in ("rain", "general"):
        s = series(d, "precip")
        if s:
            tots = [(total(s["values"][i]), i) for i in pool]
            v, i = max(tots) if tots else (0, home)
            out.append(("Wettest", num(v, "mm"), f"{names[i]} · 36 h"))
            st = steps(s["values"][home])
            pv, pt = peak(st, T)
            if pv:
                out.append(("Heaviest 3 h", num(pv, "mm"), f"{names[home]} · {when(pt)}"))
    elif hz == "snow":
        b = best("snowlevel", low=True)
        if b:
            v, t, i = b
            town_m = ELEVATION_M.get(names[i], 0)
            out.append(("Snow level", num(v, "m"), f"down to town level {when(t)}" if v <= town_m + 200
                        else f"{num(v - town_m, 'm')} above {names[i]}"))
        b = best("temp", low=True)
        if b:
            out.append(("Coldest", num(b[0], "°C"), f"{names[b[2]]} · {when(b[1])}"))
    elif hz == "freezing":
        b = best("fzra")
        if b and b[0] > 0:
            out.append(("Freezing rain", num(b[0], "%"), f"chance · {when(b[1])}"))
        elif b:
            out.append(("Freezing rain", "None", "in the forecast"))
        b = best("temp", low=True)
        if b:
            out.append(("Lowest temp", num(b[0], "°C"), f"{names[b[2]]} · {when(b[1])}"))
    elif hz in ("wind", "tornado"):
        b = best("wind")
        if b:
            out.append(("Strongest wind", num(b[0], "km/h"), f"{names[b[2]]} · {when(b[1])}"))
    elif hz == "thunder":
        b = best("tstorm")
        if b and b[0] > 0:
            out.append(("Thunderstorms", num(b[0], "%"), f"chance · {when(b[1])}"))
        elif b:
            out.append(("Thunderstorms", "None", "in the forecast"))
    elif hz == "flood":
        s = series(d, "sealevel")
        if s:
            cands = [(peak(vals, T)[0], peak(vals, T)[1], k) for k, vals in enumerate(s["values"]) if peak(vals, T)[0] is not None]
            if cands:
                v, t, k = max(cands)
                out.append(("Highest water", num(v, "m", 1), f"{d['sea'][k]['name']} · {when(t)}"))
    elif hz == "heat":
        b = best("temp")
        if b:
            out.append(("High", num(b[0], "°C"), f"{names[b[2]]} · {when(b[1])}"))
        tv, dv = series(d, "temp"), series(d, "dew")
        if tv and dv:
            b = best("temp", transform=lambda i, vals: [humidex(a, bb) for a, bb in zip(vals, dv["values"][i])])
            if b:
                out.append(("Humidex", num(b[0]), f"{names[b[2]]} · {when(b[1])}"))
    elif hz in ("cold", "frost"):
        b = best("temp", low=True)
        if b:
            out.append(("Low", num(b[0], "°C"), f"{names[b[2]]} · {when(b[1])}"))
        wv = series(d, "wind")
        if hz == "cold" and wv:
            b = best("temp", low=True, transform=lambda i, vals: [wind_chill(a, w) for a, w in zip(vals, wv["values"][i])])
            if b:
                out.append(("Wind chill", num(b[0]), f"{names[b[2]]} · {when(b[1])}"))
    elif hz == "fog":
        b = best("vis", low=True)
        if b and b[0] < 1.0:
            out.append(("Lowest visibility", num(b[0], "km", 1), f"{names[b[2]]} · {when(b[1])}"))
        elif b:
            out.append(("Fog", "None", "in the forecast at these towns"))
    elif hz == "smoke":
        b = best("pm25")
        if b:
            out.append(("PM2.5", num(b[0], "µg/m³"), f"{aq_word(b[0])} · {when(b[1])}"))
    return out


def aq_word(pm):
    """Plain-language air quality for a PM2.5 concentration (µg/m³)."""
    if pm is None:
        return ""
    for lim, word in ((12, "good"), (35, "moderate"), (55, "poor"), (150, "very poor")):
        if pm < lim:
            return word
    return "hazardous"
