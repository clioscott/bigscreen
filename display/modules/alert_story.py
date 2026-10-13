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
    if unit == "aqhi":
        return f"{v:.0f} {af.aqhi_word(v).split()[0]}"
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


METRO_REGIONS = ["North Shore", "Vancouver", "Tri-Cities", "Richmond", "Surrey"]
REGION_ZONE = {"Richmond": "Metro Vancouver - southwest", "Vancouver": "Metro Vancouver - central",
               "North Shore": "Metro Vancouver - North Shore", "Tri-Cities": "Metro Vancouver - northeast",
               "Surrey": "Metro Vancouver - southeast", "Howe Sound": "Howe Sound", "Sea to Sky": "Whistler",
               "Abbotsford": "Fraser Valley - west", "Chilliwack": "Fraser Valley - central",
               "Sunshine Coast": "Sunshine Coast - Gibsons"}
_MUNI_TABLE = []


def metro_only(ctx):
    hit = ap.affected(ctx)
    return bool(hit) and hit <= set(METRO_REGIONS)


def _munis(region):
    """The municipalities in a wall region (assets/alerts/municipalities.json)."""
    if not _MUNI_TABLE:
        import json
        from display.gfx import resolve_asset
        try:
            with open(resolve_asset("alerts/municipalities.json"), encoding="utf-8") as f:
                _MUNI_TABLE.append(json.load(f).get("regions") or {})
        except OSError:
            _MUNI_TABLE.append({})
    head = REGION_ZONE.get(region, region)
    return next((v for k, v in _MUNI_TABLE[0].items() if k.startswith(head)), [])


def _spark(c, x, y, w, h, vals, k, col, dim):
    """A small bar sparkline of the 36 h, the current step bright."""
    vs = [v for v in vals if v is not None]
    if not vs or w < 4 or h < 2:
        return
    lo, hi = min(0, min(vs)), max(vs)
    rng = (hi - lo) or 1
    n = len(vals)
    bw = max(1, (w - (n - 1)) // n)
    for i, v in enumerate(vals):
        if v is None:
            continue
        bh = max(1, int((v - lo) / rng * h))
        c.FillRect(x + i * (bw + 1), y + h - bh, bw, bh, Color(*(col if i == k else dim)))


def metro_diagram(c, ctx, p, x, y, w, h, T, series, vals, unit, colour, k, how):
    """The five Metro Vancouver regions, big: the region's name and its municipalities, the value now in large
    type, the 36 h as a sparkline (current step lit), and the peak (or low) with its time."""
    hit = ap.affected(ctx)
    home = getattr(ctx, "home_name", "Richmond")
    rects = ap.tile_rects("metro", x, y, w, h, gap=3)
    big = [f for f in list(ctx.fam["titles"]) if f.h <= 24] + [p.K]
    low = how in ("chill", "low")
    for name, (tx, ty, tw, th) in rects.items():
        v = vals.get(name)
        col = colour(v) if v is not None else ctx.pal["panel"]
        c.FillRect(tx, ty, tw, th, Color(*_dim(col, 0.5)))
        c.FillRect(tx, ty, 4, th, Color(*col))
        if name in hit or name == home:
            edge = ap.HOME_TINT if name == home else (255, 255, 255)
            for rx, ry, rw, rr in ((tx, ty, tw, 1), (tx, ty + th - 1, tw, 1), (tx, ty, 1, th), (tx + tw - 1, ty, 1, th)):
                c.FillRect(rx, ry, rw, rr, Color(*edge))
        ink = (255, 255, 255)
        nm = _U(ctx, name).upper()
        p.K.draw(c, tx + 8, ty + 3, ink, nm)
        munis = " · ".join(_munis(name))
        room = tw - 22 - p.K.width(nm)
        mf = next((f for f in (p.S, p.T) if munis and f.width(munis) <= room), None)
        below = False
        if mf:
            mf.draw(c, tx + 16 + p.K.width(nm), ty + 3 + (p.K.h - mf.h), ctx.pal["strong"], munis)
        elif munis and p.T.width(munis) <= tw - 12:
            p.T.draw(c, tx + 8, ty + 4 + p.K.line, ctx.pal["strong"], munis)
            below = True
        vs = _fmt(v, unit)
        vy = ty + p.K.line + (p.T.line + 1 if below else 0) + 4
        vf = next((f for f in big if f.h <= th - (vy - ty) - 3 and f.width(vs) <= tw // 2), p.S)
        vf.draw(c, tx + 8, vy, ink, vs)
        seq = series.get(name) or []
        sx = tx + 8 + max(vf.width(vs) + 12, tw // 2 - 10)
        sw = tx + tw - 6 - sx
        if seq and sw > 30:
            idx = [i for i, q in enumerate(seq) if q is not None]
            if idx:
                pk = (min if low else max)(idx, key=lambda i: seq[i])
                word = "low" if low else "peak"
                lab = f"{word} {_fmt(seq[pk], unit)} · {af.when(T[pk])}"
                lf = next((f for f in (p.S, p.T) if f.width(lab) <= sw), None)
                if lf is None:
                    lab = f"{word} {_fmt(seq[pk], unit)}"
                    lf = p.T
                lf.draw(c, sx, ty + th - lf.h - 3, ctx.pal["strong"], lab)
                _spark(c, sx, vy, sw, max(4, th - (vy - ty) - lf.h - 8), seq, min(k, len(seq) - 1),
                       (255, 255, 255), _dim(col, 0.95))


_ZONES = {}


def _map_layers(data_dir, meta):
    """(base rgb, zone index, zone names) of an alert map, cached by file."""
    import os
    import numpy as np
    if not data_dir or not meta or not meta.get("zones_file"):
        return None
    fb, fz = os.path.join(data_dir, meta["file"]), os.path.join(data_dir, meta["zones_file"])
    try:
        key = (fb, os.path.getmtime(fb), os.path.getmtime(fz))
    except OSError:
        return None
    if _ZONES.get("key") != key:
        w, h = meta["w"], meta["h"]
        _ZONES.update(key=key, base=np.fromfile(fb, dtype=np.uint8).reshape(h, w, 3),
                      zones=np.fromfile(fz, dtype=np.uint8).reshape(h, w))
    return _ZONES["base"], _ZONES["zones"], meta.get("zones") or []


def _fit_img(img, w, h, bg=(0, 0, 0)):
    """Nearest-neighbour scale so the whole map fits (w, h), centred on ``bg``; returns (image, scale, x offset,
    y offset) where a map pixel (px, py) lands at ((px - ox) * k, (py - oy) * k)."""
    import numpy as np
    ih, iw = img.shape[:2]
    k = min(w / iw, h / ih)
    ox, oy = (iw - w / k) / 2, (ih - h / k) / 2
    yi = (np.arange(h) / k + oy).astype(int)
    xi = (np.arange(w) / k + ox).astype(int)
    out = np.empty((h, w, 3), dtype=np.uint8)
    out[:] = bg
    vy, vx = (yi >= 0) & (yi < ih), (xi >= 0) & (xi < iw)
    out[np.ix_(vy, vx)] = img[yi[vy]][:, xi[vx]]
    return out, k, ox, oy


def zone_map(c, ctx, x, y, w, h, vals, colour, unit, fonts, labels_fmt=None):
    """Our own forecast map: the alert map's basemap with every ECCC zone filled in its value's colour (the
    alert's zones outlined white), each region's value printed at its town. False without a map."""
    import numpy as np
    from display.modules import alert_brief
    got = _map_layers(getattr(ctx, "data_dir", None), getattr(ctx, "amap", None))
    if not got:
        return False
    base, zones, names = got
    region_of = {}
    for i, n in enumerate(names, 1):
        labs = alert_brief.region_labels([n], getattr(ctx, "home_name", "Richmond"))
        if labs and labs[0][0] in vals:
            region_of[i] = labs[0][0]
    out = base.astype(np.float32) * 0.45
    for i, r in region_of.items():
        v = vals.get(r)
        if v is None:
            continue
        m = zones == i
        out[m] = base[m] * 0.25 + np.array(colour(v), dtype=np.float32) * 0.75
    img = out.clip(0, 255).astype(np.uint8)
    up = np.pad(zones, 1, mode="edge")
    edge = ((up[1:-1, 1:-1] != up[:-2, 1:-1]) | (up[1:-1, 1:-1] != up[1:-1, :-2])) & (zones > 0)
    img[edge] = (0, 0, 0)
    hit = ap.affected(ctx)
    lit = np.isin(zones, [i for i, r in region_of.items() if r in hit])
    lp = np.pad(lit, 1)
    img[lit & ~(lp[:-2, 1:-1] & lp[2:, 1:-1] & lp[1:-1, :-2] & lp[1:-1, 2:])] = (255, 255, 255)
    view, k, ox, oy = _fit_img(img, w, h, ctx.pal["bg"])
    c.buf[y:y + h, x:x + w] = view
    vf, nf = fonts[0], fonts[-1]
    placed = []
    towns = sorted((ctx.amap.get("towns_px") or {}).items(), key=lambda kv: (kv[0] not in hit, kv[0] != getattr(ctx, "home_name", "")))
    for r, (px_, py_) in towns:
        v = vals.get(r)
        if v is None:
            continue
        sx, sy = int((px_ - ox) * k) + x, int((py_ - oy) * k) + y
        if not (x <= sx < x + w and y <= sy < y + h):
            continue
        val = labels_fmt(v) if labels_fmt else _fmt(v, unit)
        name = _U(ctx, r).upper()
        cw = max(vf.width(val), nf.width(name)) + 8
        chh = nf.h + vf.h + 5
        for lx, ly in ((sx - cw // 2, sy - chh - 3), (sx - cw // 2, sy + 3), (sx + 4, sy - chh // 2), (sx - cw - 4, sy - chh // 2)):
            box = (lx - 1, ly - 1, lx + cw + 1, ly + chh + 1)
            inside = x + 1 <= box[0] and box[2] < x + w - 1 and y + 1 <= box[1] and box[3] < y + h - 1
            clear = all(box[2] < q[0] or box[0] > q[2] or box[3] < q[1] or box[1] > q[3] for q in placed)
            if inside and clear:
                placed.append(box)
                col = colour(v)
                c.FillRect(lx, ly, cw, chh, Color(8, 10, 14))
                c.FillRect(lx, ly, 3, chh, Color(*col))
                edge = ap.HOME_TINT if r == getattr(ctx, "home_name", "") else ((255, 255, 255) if r in hit else (70, 76, 86))
                c.FillRect(lx, ly, cw, 1, Color(*edge))
                c.FillRect(lx, ly + chh - 1, cw, 1, Color(*edge))
                nf.draw(c, lx + 5, ly + 2, ctx.pal["text"], name)
                vf.draw(c, lx + 5, ly + 3 + nf.h, (255, 255, 255), val)
                c.FillRect(sx - 1, sy - 1, 3, 3, Color(255, 255, 255))
                break
    return True


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
    metro = metro_only(ctx)
    title = f"{label} · next 36 hours"
    sub = ("Metro Vancouver" if metro else "Lower Mainland") + ", forecast zone by zone" + \
          (" · sample data: today's forecast has none" if sample else "")
    p = sp.Page(c, ctx, inner, title, sub, "SAMPLE DATA · NOT A FORECAST" if sample else sp._credit(d))
    x, y, w, h = p.body
    foot = p.K.line + 4
    vfonts = [f for f in list(ctx.fam["titles"]) + [p.K] if f.h <= 20] + [p.S]
    if not zone_map(c, ctx, x, y, w, h - foot, vals, colour, unit, [p.K, p.T]):
        region_diagram(c, ctx, x, y, w, h - foot, {r: _fmt(v, unit) for r, v in vals.items()},
                       {r: colour(v) for r, v in vals.items() if v is not None}, [p.S, p.T], vfonts)
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
    if "Vancouver" in by_region and "North Shore" not in by_region:    # ECCC's Metro NW area covers both
        by_region["North Shore"] = by_region["Vancouver"]
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
    if not zone_map(c, ctx, x, y, w, h - foot, {r: v for r, v in vals.items() if v is not None}, af.aqhi_colour,
                    "aqhi", [p.K, p.T], labels_fmt=lambda v: f"{v:.0f} {af.aqhi_word(v).split()[0]}"):
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
    p = sp.Page(c, ctx, inner, "About this alert", f"{ctx.b['title'].capitalize()} · {(ctx.b['status'] or 'in effect')}",
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
        where, wcol = f"{km:.0f} km from {home}", ctx.pal["strong"]
    else:
        where, wcol = f"Nearby: {', '.join(n for n, _ in ap.labels(ctx)[:2])}", ctx.pal["strong"]
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
    band_h = K.line + -(-len(rows) // 3) * (T.h + S.line + 4) + 6
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
    # below: Metro Vancouver's municipalities as a diagram when that's all it covers; otherwise the real map
    gy = y + band_h + 4
    if metro_only(ctx) and ap.grid_def("municipalities_metro"):
        gid = "municipalities_metro"
        g = ap.grid_def(gid)
        full, _, _ = ap.grid_cover(ctx, gid)
        lab = f"{g['label']} · {len(full)} of {len(g['units'])} in the alert"
        bx, by, bw, bh = p.panel(x, gy, w, y + h - gy, lab)
        ap.unit_grid(c, ctx, gid, bx, by, bw, bh, [K, S, T])
    elif not _area_inset(c, ctx, p, x, gy, w, y + h - gy):
        bx, by, bw, bh = p.panel(x, gy, w, y + h - gy, "Regions in the alert")
        region_diagram(c, ctx, bx, by, bw, bh, {r: "" for r in ap.TILES["lm"]["tiles"]},
                       {r: ctx.sev for r in ap.affected(ctx)}, [S, T], [S])
    return True


def _area_inset(c, ctx, p, x, y, w, h):
    """The alert's area on the real map (its zones lit, home marked), for alerts reaching beyond Metro."""
    import os
    import numpy as np
    meta = getattr(ctx, "amap", None)
    dd = getattr(ctx, "data_dir", None)
    if not meta or not dd:
        return False
    try:
        base = np.fromfile(os.path.join(dd, meta["file"]), dtype=np.uint8).reshape(meta["h"], meta["w"], 3)
        mask = np.fromfile(os.path.join(dd, meta["mask"]), dtype=np.uint8).reshape(meta["h"], meta["w"]) > 127
    except (OSError, ValueError, KeyError):
        return False
    img = base.astype(np.float32) * 0.55
    img[mask] = base[mask] * 0.3 + np.array(ctx.sev, dtype=np.float32) * 0.7
    pad = np.pad(mask, 1)
    img[mask & ~(pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])] = (255, 255, 255)
    bx, by, bw, bh = p.panel(x, y, w, h, f"Area in the alert · {meta.get('area') or 'map'}")
    view, k, ox, oy = _fit_img(img.clip(0, 255).astype(np.uint8), bw, bh, ctx.pal["panel"])
    c.buf[by:by + bh, bx:bx + bw] = view
    hp = meta.get("home_px")
    if hp:
        sx, sy = int((hp[0] - ox) * k) + bx, int((hp[1] - oy) * k) + by
        if bx + 3 <= sx < bx + bw - 3 and by + 3 <= sy < by + bh - 3:
            c.FillRect(sx - 2, sy - 2, 5, 5, Color(0, 0, 0))
            c.FillRect(sx - 1, sy - 1, 3, 3, Color(*ap.HOME_TINT))
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
