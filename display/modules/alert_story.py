"""What an alert shows after its message, and in what order — decided per alert, not fixed.

``page_plan`` picks pages for the alert's source and weather type, by what data is there to show:
    forecast  — the weather type's forecast on the region diagram (10 boxes laid out like the map), animated
                through the next 36 hours, every box coloured and labelled with its value: rain so far, snow,
                wind, temperature, wind chill, chance of freezing rain or storms, fog visibility…
    tides     — the four tide gauges on a diagram, the tide rising and falling through the next 36 hours
    air       — the Air Quality Health Index by area on the region diagram, now and through the forecast
    about     — the alert itself, for checking it's real and current: source and feed, status, issued /
                expires / weather ends, when the hub last checked, whether it includes home or how far away
                it is — beside the municipalities diagram
    map, maps — the area map and the weather type's data maps (radar, forecast fields)
A source other than ECCC gets ``about`` and its own pages (none yet); test alerts get sample numbers when
today's real forecast has none of their weather, marked SAMPLE DATA.
"""

from __future__ import annotations

import math
import time

from display.gfx import Color
from display.modules import alert_charts as ch
from display.modules import alert_facts as af
from display.modules import alert_parts as ap
from display.modules import alert_specials as sp


def _U(ctx, s):
    return s.upper() if ctx.upper else s


def _dim(rgb, k):
    return tuple(max(0, min(255, int(v * k))) for v in rgb)


# ------------------------------------------------------------------------------------------------ forecast diagrams
BLUES = [(0, (30, 40, 70)), (1, (20, 70, 150)), (10, (30, 110, 220)), (25, (60, 160, 255)), (50, (140, 210, 255)), (80, (230, 245, 255))]
WINDS = [(0, (25, 50, 50)), (20, (0, 110, 100)), (40, (0, 170, 150)), (60, (230, 200, 40)), (80, (255, 120, 0)), (100, (255, 40, 40))]
TEMPS = [(-30, (120, 60, 200)), (-15, (60, 90, 230)), (-5, (80, 170, 255)), (0, (170, 230, 255)), (10, (90, 200, 120)),
         (20, (230, 210, 60)), (27, (255, 140, 0)), (32, (255, 50, 30)), (38, (190, 0, 60))]
CHANCE = [(0, (30, 35, 45)), (10, (60, 70, 110)), (30, (110, 120, 200)), (50, (200, 160, 255)), (70, (255, 120, 220))]
STORM = [(0, (35, 35, 25)), (10, (110, 100, 20)), (30, (220, 190, 0)), (50, (255, 140, 0)), (70, (255, 40, 40))]
VIS = [(0, (255, 60, 60)), (0.2, (255, 150, 0)), (0.5, (230, 200, 60)), (1, (120, 130, 140)), (5, (40, 50, 60))]
AQHI = [(v, af.aqhi_colour(v)) for v in range(0, 12)]

# hazard → (var, how, title, unit, ramp, signal test, sample (lo, hi) for the alert's regions)
#   how: "accum" (total so far), "level" (value at the time), "chill" (wind chill from temp + wind),
#        "humidex" (from temp + dew point); "low" picks the minimum for the summary frame
FORECAST = {
    "rain":     ("precip", "accum", "Rain so far", "mm", BLUES, lambda v: v >= 5, (50, 70)),
    "snow":     ("snow", "accum3", "Snow so far", "cm", BLUES, lambda v: v >= 2, (10, 15)),
    "freezing": ("fzra", "level", "Chance of freezing rain", "%", CHANCE, lambda v: v >= 20, (40, 70)),
    "wind":     ("wind", "level", "Wind", "km/h", WINDS, lambda v: v >= 40, (70, 90)),
    "thunder":  ("tstorm", "level", "Chance of thunderstorms", "%", STORM, lambda v: v >= 20, (40, 70)),
    "tornado":  ("tstorm", "level", "Chance of thunderstorms", "%", STORM, lambda v: v >= 20, (50, 80)),
    "heat":     ("temp", "humidex", "Humidex", "", TEMPS, lambda v: v >= 30, (32, 38)),
    "cold":     ("temp", "chill", "Wind chill", "", TEMPS, lambda v: v <= -10, (-24, -16)),
    "frost":    ("temp", "low", "Temperature", "°", TEMPS, lambda v: v <= 1, (-3, 0)),
    "fog":      ("vis", "low", "Visibility in fog", "km", VIS, lambda v: v < 1, (0.1, 0.4)),
    "general":  ("temp", "level", "Temperature", "°", TEMPS, lambda v: True, (8, 14)),
}
SUMMARY = {"accum": "36 h total", "accum3": "36 h total", "level": "36 h peak", "humidex": "36 h peak",
           "chill": "36 h lowest", "low": "36 h lowest"}


def _region_town(d):
    """{wall region: town index} — alert_data has one town per region."""
    return {t["region"]: i for i, t in enumerate((d or {}).get("towns") or [])}


def forecast_series(ctx, d):
    """(times, {region: [value per time]}, summary {region: value}, sample?) for the alert's weather type, or None."""
    hz = ctx.b["hazard"]
    spec = FORECAST.get(hz)
    if not spec or not d or not d.get("_times"):
        return None
    var, how, _, _, _, signal, sample = spec
    T = d["_times"]
    idx = _region_town(d)
    s = af.series(d, var)
    if not s:
        return None
    out = {}
    for region, i in idx.items():
        vals = s["values"][i]
        if how == "accum":
            base = next((v for v in vals if v is not None), 0)
            vals = [None if v is None else max(0.0, v - base) for v in vals]
        elif how == "accum3":
            acc, run = [], 0.0
            for v in vals:
                run += (v or 0) * 3
                acc.append(run)
            vals = acc
        elif how == "chill":
            wv = af.series(d, "wind")
            vals = [af.wind_chill(a, b) for a, b in zip(vals, wv["values"][i])] if wv else vals
        elif how == "humidex":
            dv = af.series(d, "dew")
            vals = [af.humidex(a, b) for a, b in zip(vals, dv["values"][i])] if dv else vals
        out[region] = vals
    low = how in ("chill", "low") or var == "vis"
    summ = {r: (min if low else max)([v for v in vs if v is not None] or [None]) for r, vs in out.items()}
    hit = ap.affected(ctx)
    real = [v for r, v in summ.items() if v is not None and (r in hit or not hit)]
    used_sample = False
    if ctx.b.get("test") and not any(signal(v) for v in real):
        out, summ = _sample_series(ctx, T, how, sample, low)
        used_sample = True
    return T, out, summ, used_sample


def _sample_series(ctx, T, how, rng, low):
    """Placeholder numbers shaped like the sample alert: its regions in the alert's range, the rest milder, the
    weather building to a peak about halfway through the 36 hours."""
    lo, hi = rng
    hit = ap.affected(ctx)
    regions = [n for n, _ in ap.TILES["lm"]["tiles"].items()]
    out, summ = {}, {}
    n = len(T)
    for k, r in enumerate(regions):
        inside = r in hit
        span = abs(hi - lo) or 1
        if inside:
            top = lo + (hi - lo) * ((k * 37) % 10) / 9
        elif how in ("humidex", "chill", "low") or rng[0] < 0:     # temperatures: a few degrees milder outside
            top = (hi + span + 3) if low else (lo - span - 3)
        else:
            top = lo * 0.4
        vals = []
        for j in range(n):
            f = j / max(1, n - 1)
            if how in ("accum", "accum3"):
                vals.append(round(top / (1 + math.exp(-10 * (f - 0.45))), 1))
            else:
                bump = math.exp(-((f - 0.5) ** 2) / 0.04)
                base = top * 0.4 if not low else (top + 8 if top < 0 else top + 0.8)
                vals.append(round(base + (top - base) * bump, 1))
        out[r] = vals
        summ[r] = (min if low else max)(vals)
    return out, summ


def _fmt(v, unit):
    if v is None:
        return "-"
    if unit == "km":
        return f"{v:.1f} km"
    if unit in ("°", ""):
        return f"{v:.0f}°"
    return f"{v:.0f} {unit}"


def region_diagram(c, ctx, x, y, w, h, values, colours, fonts, val_fonts):
    """The 10 regions as boxes laid out like the map (ap.TILES['lm']), each with its full name and a value,
    coloured by value; the alert's regions outlined white, home outlined in its tint."""
    hit = ap.affected(ctx)
    home = getattr(ctx, "home_name", "Richmond")
    rects = ap.tile_rects("lm", x, y, w, h, gap=2)
    for name, (tx, ty, tw, th) in rects.items():
        col = colours.get(name) or ctx.pal["panel"]
        c.FillRect(tx, ty, tw, th, Color(*col))
        ink = (0, 0, 0) if sum(col) > 380 else (255, 255, 255)
        if name in hit or name == home:
            edge = ap.HOME_TINT if name == home else (255, 255, 255)
            for rx, ry, rw, rr in ((tx, ty, tw, 1), (tx, ty + th - 1, tw, 1), (tx, ty, 1, th), (tx + tw - 1, ty, 1, th)):
                c.FillRect(rx, ry, rw, rr, Color(*edge))
        label = _U(ctx, name).upper()
        val = values.get(name, "")
        vf = next((f for f in val_fonts if f.width(val) <= tw - 4 and f.h + 9 <= th), val_fonts[-1])
        lines, lf = None, fonts[-1]
        for f in fonts:
            ls = ap._wrap2(f, label, tw - 4)
            if ls and len(ls) * (f.h + 1) + vf.h + 3 <= th:
                lines, lf = ls, f
                break
        lines = lines or [label]
        total = len(lines) * (lf.h + 1) + vf.h + 1
        yy = ty + (th - total) // 2
        for s_ in lines:
            lf.draw(c, tx + (tw - lf.width(s_)) // 2, yy, ink, s_)
            yy += lf.h + 1
        vf.draw(c, tx + (tw - vf.width(val)) // 2, yy + 1, ink, val)


def legend(c, ctx, x, y, w, ramp, unit, font):
    """A strip of the colour scale with its thresholds."""
    n = len(ramp)
    sw = w // n
    for k, (t, col) in enumerate(ramp):
        c.FillRect(x + k * sw, y, sw - 1, 3, Color(*col))
        lab = f"{t:g}" if not isinstance(t, float) or t != int(t) else f"{int(t)}"
        if font.width(lab) < sw - 1:
            font.draw(c, x + k * sw, y + 5, ctx.pal["dim"], lab)
    if unit:
        font.draw(c, x + n * sw + 3, y + 1, ctx.pal["dim"], unit)


def _frame(local, hold, n):
    """Which time step to show: step through the n times over 70 % of the page, then the summary."""
    run = max(1.0, hold * 0.7)
    return min(n, int(local / (run / n)))


def draw_forecast(c, ctx, inner, local, hold):
    d = getattr(ctx, "data", None)
    got = forecast_series(ctx, d)
    if not got:
        return False
    T, series, summ, sample = got
    var, how, label, unit, ramp, _, _ = FORECAST[ctx.b["hazard"]]
    n = len(T)
    k = _frame(local, hold, n)
    colour = ch.ramp(ramp)
    if k >= n:
        vals, when = summ, SUMMARY[how]
    else:
        vals, when = {r: vs[k] for r, vs in series.items()}, f"{af.when(T[k])}" + (" · now" if k == 0 else f" · +{k * 3} h")
    title = f"{label} · next 36 hours"
    sub = "Each region's forecast, step by step" + (" · sample data: today's forecast has none" if sample else "")
    p = sp.Page(c, ctx, inner, title, sub, "SAMPLE DATA · NOT A FORECAST" if sample else sp._credit(d))
    x, y, w, h = p.body
    foot = p.K.line + 4
    fonts = [p.S, p.T]
    vfonts = [f for f in list(ctx.fam["titles"]) + [p.K] if f.h <= 20] + [p.S]
    region_diagram(c, ctx, x, y, w, h - foot, {r: _fmt(v, unit) for r, v in vals.items()},
                   {r: colour(v) for r, v in vals.items() if v is not None}, fonts, vfonts)
    fy = y + h - foot + 3
    p.K.draw(c, x, fy, ctx.pal["strong"], _U(ctx, when))
    bx = x + p.K.width(_U(ctx, when)) + 8
    lw = min(200, w - (bx - x) - 4)
    legend(c, ctx, x + w - lw, fy, lw - 20, ramp, unit or "°", p.T)
    pw = x + w - lw - 8 - bx
    if pw > 20:
        c.FillRect(bx, fy + p.K.h // 2, pw, 1, Color(*_dim(ctx.sev, 0.35)))
        c.FillRect(bx, fy + p.K.h // 2 - 1, int(pw * min(1.0, (k + 1) / (n + 1))), 3, Color(*ctx.sev))
    return True


def draw_air(c, ctx, inner, local, hold):
    """AQHI by area on the region diagram: now, then each forecast period, then the forecast peak."""
    x_ = getattr(ctx, "extra", None) or {}
    areas = af.aqhi_areas(ctx, x_)
    if not areas:
        return False
    by_region = {af.AQHI_REGION[a["name"]]: a for a in areas}
    now = time.time()
    periods = sorted({f[0] for a in areas for f in a.get("forecast") or [] if f[0] and af.ts(f[0]).timestamp() >= now - 3600})[:12]
    frames = ["now"] + periods + ["peak"]
    k = min(len(frames) - 1, int(local / max(0.5, hold * 0.75 / len(frames))))
    fr = frames[k]
    vals = {}
    for r, a in by_region.items():
        if fr == "now":
            vals[r] = a.get("obs")
        elif fr == "peak":
            vals[r] = max([f[1] for f in a.get("forecast") or [] if f[1] is not None] or [None])
        else:
            vals[r] = next((f[1] for f in a.get("forecast") or [] if f[0] == fr), None)
    sample = False
    if ctx.b.get("test") and max([v or 0 for v in vals.values()] or [0]) < 4:
        hit = ap.affected(ctx)
        bump = math.exp(-((k / max(1, len(frames) - 1) - 0.5) ** 2) / 0.05)
        vals = {r: (round(5 + 3 * bump) if r in hit else round(2 + bump)) for r in by_region}
        sample = True
    when = {"now": "Now (measured)", "peak": "Forecast peak"}.get(fr) or af.when(af.ts(fr))
    p = sp.Page(c, ctx, inner, "Air Quality Health Index",
                "1-3 low · 4-6 moderate · 7-10 high · 10+ very high risk" + (" — SAMPLE DATA" if sample else ""),
                "SAMPLE DATA · NOT A FORECAST" if sample else "ECCC Air Quality Health Index (AQHI)")
    x, y, w, h = p.body
    foot = p.K.line + 4
    vfonts = [f for f in list(ctx.fam["titles"]) + [p.K] if f.h <= 20] + [p.S]
    region_diagram(c, ctx, x, y, w, h - foot,
                   {r: (f"{v:.0f} · {af.aqhi_word(v).split()[0]}" if v is not None else "no reading") for r, v in vals.items()},
                   {r: af.aqhi_colour(v) for r, v in vals.items() if v is not None}, [p.S, p.T], vfonts)
    p.K.draw(c, x, y + h - foot + 3, ctx.pal["strong"], _U(ctx, when))
    legend(c, ctx, x + w - 190, y + h - foot + 3, 170, AQHI[1:], "", p.T)
    return True


TIDE_POS = {"Point Atkinson": (0, 0), "New Westminster": (2, 0), "Steveston": (0, 1), "White Rock": (2, 1)}


def draw_tides(c, ctx, inner, local, hold):
    """The four tide gauges on a diagram (roughly where they are), each box filling with the predicted tide as
    it rises and falls through the next 36 hours, with the measured level and surge where the gauge has them."""
    x_ = getattr(ctx, "extra", None) or {}
    sts = [s for s in x_.get("tides") or [] if s.get("pred") and s["name"] in TIDE_POS]
    if not sts:
        return False
    now = time.time()
    fut = [(af.ts(t), v) for t, v in sts[0]["pred"] if af.ts(t).timestamp() >= now - 900]
    times = [t for t, _ in fut][::2]                           # hourly steps
    n = len(times)
    k = min(n - 1, int(local / max(0.3, hold * 0.85 / max(1, n))))
    tt = times[k] if times else None
    p = sp.Page(c, ctx, inner, "Tides and storm surge", "Predicted water level at each gauge, hour by hour (m above chart datum)",
                "Fisheries and Oceans Canada · Canadian Hydrographic Service")
    x, y, w, h = p.body
    foot = p.K.line + 4
    bw, bh = (w - 8) // 3, (h - foot - 4) // 2
    vf = next((f for f in list(ctx.fam["titles"]) + [p.K] if f.h <= 20), p.K)
    for st in sts:
        col_, row_ = TIDE_POS[st["name"]]
        bx, by = x + col_ * (bw + 4), y + row_ * (bh + 4)
        pred = [(af.ts(t), v) for t, v in st["pred"]]
        cur = min(pred, key=lambda q: abs((q[0] - tt).total_seconds()))[1] if tt else None
        vmin, vmax = min(v for _, v in pred), max(v for _, v in pred)
        frac = 0 if vmax == vmin or cur is None else (cur - vmin) / (vmax - vmin)
        c.FillRect(bx, by, bw, bh, Color(*ctx.pal["panel"]))
        fh = int((bh - 2) * frac)
        c.FillRect(bx + 1, by + bh - 1 - fh, bw - 2, fh, Color(*_dim(ctx.sev, 0.55)))
        c.FillRect(bx, by, bw, 1, Color(*ctx.sev))
        p.S.draw(c, bx + 4, by + 3, ctx.pal["strong"], _U(ctx, st["name"]).upper())
        s_ = f"{cur:.1f} m" if cur is not None else "-"
        vf.draw(c, bx + 4, by + 4 + p.S.line, (255, 255, 255), s_)
        hs = af.next_high(st)
        if hs:
            nh = f"next high {af.when(hs[0][0])} · {hs[0][1]:.1f} m"
            p.T.draw(c, bx + 4, by + bh - p.T.h - 3, ctx.pal["text"], ch.fit(p.T, nh.upper(), bw - 8))
    # middle column: where the alert is, and the surge
    mx = x + bw + 4
    obs = [s for s in sts if s.get("obs")]
    lines = []
    for s in obs:
        o = s["obs"][-1]
        pr = min(s["pred"], key=lambda q: abs((af.ts(q[0]) - af.ts(o[0])).total_seconds()))
        lines.append((s["name"].split()[0], f"{o[1] - pr[1]:+.2f} m"))
    c.FillRect(mx, y, bw, h - foot, Color(*ctx.pal["panel"]))
    c.FillRect(mx, y, bw, 1, Color(*ctx.sev))
    p.T.draw(c, mx + 4, y + 3, ctx.pal["dim"], "SURGE NOW (MEASURED - PREDICTED)")
    yy = y + p.T.h + 6
    for name, v in lines:
        p.S.draw(c, mx + 4, yy, ctx.pal["strong"], name.upper())
        p.K.draw(c, mx + bw - 4 - p.K.width(v), yy, ctx.sev, v)
        yy += p.K.line + 1
    if tt:
        p.K.draw(c, x, y + h - foot + 3, ctx.pal["strong"], _U(ctx, af.when(tt)))
    return True


# ------------------------------------------------------------------------------------------------ about this alert
def draw_about(c, ctx, inner, local, hold):
    """The alert itself: where it came from and whether it's current, whether it includes home, and the
    municipalities it covers on the diagram."""
    issued, expires, end = af.alert_times(ctx)
    src = af.source_of(ctx)
    p = sp.Page(c, ctx, inner, "About this alert", f"{ctx.b['title'].capitalize()} · check it's real and current",
                f"Source: {src['name']}")
    x, y, w, h = p.body
    K, S, T = p.K, p.S, p.T
    now = time.time()

    def rel(dt):
        if not dt:
            return ""
        s_ = (dt if isinstance(dt, (int, float)) else dt.timestamp()) - now
        hrs = abs(s_) / 3600
        txt = f"{hrs:.0f} h" if hrs >= 1 else f"{abs(s_) / 60:.0f} min"
        return f"in {txt}" if s_ > 0 else f"{txt} ago"

    km = (getattr(ctx, "coverage", None) or {}).get("home_km")
    if km is None:
        km = getattr(ctx, "home_km", None)
    home = getattr(ctx, "home_name", "Richmond")
    if ctx.home or km == 0:
        where, wcol = f"Includes {home}", ctx.sev
    elif km is not None:
        where, wcol = f"Not {home} · {km:.0f} km away", ctx.pal["strong"]
    else:
        where, wcol = f"Not {home}", ctx.pal["strong"]
    checked = getattr(ctx, "checked", None)
    rows = [("Source", f"{src['short'] or src['name']} weather alert"),
            ("Status", f"{(ctx.b['status'] or 'in effect').capitalize()} · {ctx.b['kind']}"),
            ("Issued", f"{af.when(issued)} · {rel(issued)}" if issued else "-"),
            ("Expires", f"{af.when(expires)} · {rel(expires)}" if expires else "until further notice")]
    if end and expires and end.timestamp() > expires.timestamp() + 1800:
        rows.append(("Weather ends", f"{af.when(end)}"))
    rows.append(("Hub checked", f"{rel(checked)}" if checked else "-"))
    if ctx.b.get("test"):
        rows.insert(0, ("Test", "sample, not a real alert"))
    # a band across the top: whether it includes home (large), then the facts in label / value cells
    band_h = K.line + 2 * (T.h + S.line + 4) + 6
    c.FillRect(x, y, w, band_h, Color(*ctx.pal["panel"]))
    c.FillRect(x, y, w, 1, Color(*ctx.sev))
    hf = next((f for f in [K, S, T] if f.width(_U(ctx, where)) <= w // 2), T)
    hf.draw(c, x + 4, y + 3, wcol, _U(ctx, where))
    cells = rows
    ncol = 3
    cw = (w - 8) // ncol
    for k, (lab, val) in enumerate(cells):
        cx = x + 4 + (k % ncol) * cw
        cy = y + K.line + 5 + (k // ncol) * (T.h + S.line + 4)
        T.draw(c, cx, cy, ctx.pal["dim"], lab.upper())
        vf = next((f for f in (S, T) if f.width(val) <= cw - 6), T)
        lines = [val] if vf.width(val) <= cw - 6 else sp._wrap(T, val, cw - 6)[:1]
        vf.draw(c, cx, cy + T.h + 2, ctx.pal["strong"], lines[0])
    # below: the municipalities diagram, full width
    gy = y + band_h + 4
    gid = ap.pick_grid(ctx, (ctx.th.get("opts") or {}).get("grid") or getattr(ctx, "detail_grid", None))
    g = ap.grid_def(gid)
    if g:
        full, part, _ = ap.grid_cover(ctx, gid)
        units, _, _ = ap.grid_units(ctx, gid)
        lab = f"{g['label']} · {len(full)} of {len(units)} in the alert"
        bx, by, bw, bh = p.panel(x, gy, w, y + h - gy, lab)
        ap.unit_grid(c, ctx, gid, bx, by, bw, bh, [S, T])
    return True


DRAW = {"forecast": draw_forecast, "air": draw_air, "tides": draw_tides, "about": draw_about}
NAMES = {"forecast": "Forecast map", "air": "Air quality", "tides": "Tides", "about": "About this alert",
         "map": "Area map", "page": "Data map"}


# ------------------------------------------------------------------------------------------------ the plan
def page_plan(ctx, has_area_map, data_pages):
    """[(kind, extra)] after the message, for this alert's source and weather type and the data there is.
    ``data_pages`` = the weather type's data map pages that have something on them."""
    src = (ctx.b.get("source") or "eccc").lower()
    hz = ctx.b["hazard"]
    plan = []
    if src == "eccc":
        if hz == "smoke" and af.aqhi_areas(ctx, getattr(ctx, "extra", None)):
            plan.append(("air", None))
        elif hz == "flood" and (getattr(ctx, "extra", None) or {}).get("tides"):
            plan.append(("tides", None))
        elif forecast_series(ctx, getattr(ctx, "data", None)):
            plan.append(("forecast", None))
        # when the alert's weather is on the radar / forecast maps, show those before the paperwork
        lead = [p_ for p_ in data_pages if p_ in ("radar", "precip", "snowfall", "tstorm", "smoke", "fog")]
        plan += [("page", p_) for p_ in lead]
        plan.append(("about", None))
        if has_area_map:
            plan.append(("map", None))
        plan += [("page", p_) for p_ in data_pages if p_ not in lead]
    else:
        plan.append(("about", None))
        if has_area_map:
            plan.append(("map", None))
    return plan
