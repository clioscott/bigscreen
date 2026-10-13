"""The alert screen's second and third pages.

``details`` — the alert itself: when it was issued and when it ends (on a timeline with "now"), its status,
the region grid, and every municipality in the affected regions, grouped by region (home highlighted).

``special`` — a page made for the kind of weather, from ECCC's forecast for the towns in the alert's regions
(alert_facts.data): rainfall totals by town and the accumulation curve for a rain warning; the wind through
the next day and each town's peak for a wind warning; temperature and humidex with tonight's low for heat; a
towns × hours grid of thunderstorm / freezing-rain / fog / smoke risk… Each layout is its own; the theme gives
the palette and fonts. Numbers are put in context (a snow level far above the towns says so).

An alert from a source other than ECCC gets ``details`` only (until it has a page of its own).
"""

from __future__ import annotations

import time

from display.gfx import Color
from display.modules import alert_charts as ch
from display.modules import alert_facts as af
from display.modules import alert_parts as ap
from display.modules import pixel_icons as px

LINE_COLS = [(255, 255, 255), (255, 196, 64), (96, 220, 255), (255, 120, 200)]


def _U(ctx, s):
    return s.upper() if ctx.upper else s


class Page:
    """Fonts and frame for one page: header (icon, title, sub-line, level box), body rect, footer credit."""

    def __init__(self, c, ctx, inner, title, sub, credit):
        self.c, self.ctx = c, ctx
        x0, y0, x1, y1 = inner
        self.K, self.S, self.T = ctx.fam["key"], ctx.fam["small"], _tiny()
        self.titles = list(ctx.fam["titles"]) + [self.K]
        pal = ctx.pal
        c.FillRect(0, 0, c.buf.shape[1], c.buf.shape[0], Color(*pal["bg"]))
        lev = ctx.b["kind"].upper()
        lw = self.K.width(lev) + 8
        tw = x1 - x0 - 30 - lw - 8
        tf = next((f for f in self.titles if f.width(_U(ctx, title)) <= tw and f.h <= 20), self.K)
        hh = max(24, tf.h + self.S.line + 1)
        px.draw(c, px.hazard(ctx.b["hazard"]), x0, y0 + (hh - 24) // 2, ctx.sev)
        tf.draw(c, x0 + 30, y0, ctx.sev, ch.fit(tf, _U(ctx, title), tw))
        self.S.draw(c, x0 + 30, y0 + tf.line, pal["text"], ch.fit(self.S, sub, tw))
        c.FillRect(x1 - lw, y0 + 1, lw, self.K.h + 4, Color(*ctx.level))
        self.K.draw(c, x1 - lw + 4, y0 + 3, (0, 0, 0), lev)
        cr = ch.fit(self.T, credit.upper(), x1 - x0)
        self.T.draw(c, x1 - self.T.width(cr), y1 - self.T.h, pal["dim"], cr)
        c.FillRect(x0, y0 + hh + 1, x1 - x0, 1, Color(*_dim(ctx.sev, 0.45)))
        self.body = (x0, y0 + hh + 4, x1 - x0, y1 - self.T.h - 3 - (y0 + hh + 4))

    def panel(self, x, y, w, h, label=None):
        c, ctx = self.c, self.ctx
        c.FillRect(x, y, w, h, Color(*ctx.pal["panel"]))
        c.FillRect(x, y, w, 1, Color(*ctx.sev))
        if label:
            self.T.draw(c, x + 3, y + 3, ctx.pal["dim"], ch.fit(self.T, label.upper(), w - 6))
            return (x + 3, y + self.T.h + 6, w - 6, h - self.T.h - 8)
        return (x + 3, y + 3, w - 6, h - 6)

    def tiles(self, x, y, w, h, items):
        """Big-number tiles side by side: items = [(label, value text, sub)]."""
        if not items:
            return
        n = len(items)
        tw = (w - 4 * (n - 1)) // n
        fonts = self.titles + [self.S]
        for k, (lab, val, sub) in enumerate(items):
            v, unit = _split_unit(val)
            ch.tile(self.c, x + k * (tw + 4), y, tw, h, lab, v, unit, sub, self.ctx.pal, fonts,
                    self.ctx.sev if k else (255, 255, 255), self.T)


def _split_unit(s):
    s = str(s)
    for u in (" µg/m³", " km/h", " mm", " cm", " km", " m", " %"):
        if s.endswith(u):
            return s[: -len(u)], u.strip()
    return s, ""


_T = []


def _tiny():
    from display.modules import weather_alert as wa
    if not _T:
        _T.append(wa.FI("alert/tom-thumb.bdf"))
    return _T[0]


def _dim(rgb, k):
    return tuple(max(0, min(255, int(v * k))) for v in rgb)


def _credit(d):
    run = (d or {}).get("run") or ""
    try:
        from datetime import datetime
        r = datetime.fromisoformat(run.replace("Z", "+00:00")).astimezone()
        run = f" · model run {af.when(r)}"
    except ValueError:
        run = ""
    return f"ECCC HRDPS forecast{run}"


# ------------------------------------------------------------------------------------------------ details
def details(c, ctx, inner):
    """Page 2: when (issued → now → ends), the alert's status and regions, and every municipality."""
    issued, expires, end = af.alert_times(ctx)
    src = af.source_of(ctx)
    p = Page(c, ctx, inner, "About this alert", f"{ctx.b['title'].capitalize()} · {(ctx.b['status'] or 'in effect')}",
             f"Source: {src['name']}")
    x, y, w, h = p.body
    pal, K, S, T = ctx.pal, p.K, p.S, p.T
    # validity timeline
    th = T.h + 6 + K.line + S.line + 8
    bx, by, bw, bh = p.panel(x, y, w, th, "In effect")
    now = time.time()
    a = (issued.timestamp() if issued else now - 3600)
    z = max(a + 60, (end or expires).timestamp() if (end or expires) else now + 6 * 3600)
    lane = by + K.line + 2
    sx = lambda ts: bx + int((min(max(ts, a), z) - a) / (z - a) * (bw - 1))
    c.FillRect(bx, lane, bw, 3, Color(*_dim(ctx.sev, 0.3)))
    c.FillRect(bx, lane, max(1, sx(now) - bx), 3, Color(*ctx.sev))
    c.FillRect(sx(now) - 1, lane - 3, 3, 9, Color(255, 255, 255))
    if issued:
        K.draw(c, bx, by, pal["strong"], _U(ctx, f"Issued {af.when(issued)}"))
    fin = end if (end and expires and end.timestamp() > expires.timestamp() + 1800) else expires
    if fin:
        s1 = _U(ctx, f"{'Weather ends' if fin is end else 'Ends'} {af.when(fin)}")
        K.draw(c, bx + bw - K.width(s1), by, pal["strong"], s1)
    left = ap.remaining_text(ctx)
    nowlab = _U(ctx, f"now · alert {left} left" if left else "now")
    S.draw(c, max(bx, min(bx + bw - S.width(nowlab), sx(now) - S.width(nowlab) // 2)), lane + 6, pal["text"], nowlab)
    if fin is end and expires:
        ex = _U(ctx, f"alert expires {af.when(expires)}")
        ex_x = sx(expires.timestamp())
        c.FillRect(ex_x, lane - 2, 1, 7, Color(*pal["strong"]))
        if ex_x + S.width(ex) + 4 < bx + bw:
            S.draw(c, max(bx + S.width(nowlab) + 8, ex_x - S.width(ex) // 2), lane + 6, pal["dim"], ex)
    y2 = y + th + 4
    # the alert on a tile grid at the level that tells it best (or the one chosen in settings)
    gid = ap.pick_grid(ctx, (ctx.th.get("opts") or {}).get("grid") or getattr(ctx, "detail_grid", None))
    g = ap.grid_def(gid)
    if g:
        full, part, _ = ap.grid_cover(ctx, gid)
        n = len(g["units"])
        lab = f"{g['label']} · {len(full)} of {n} in the alert" + (f" · {len(part)} partly" if part else "")
        gx, gy, gw, gh = p.panel(x, y2, w, y + h - y2, lab)
        ap.unit_grid(c, ctx, gid, gx, gy, gw, gh, [S, T])
    else:
        n_on = sum(1 for _, _, zone, _, _ in ap.CITY_TILES if ap.city_hit(ctx, zone))
        gx, gy, gw, gh = p.panel(x, y2, w, y + h - y2, f"Communities in the alert · {n_on} of {len(ap.CITY_TILES)}")
        ap.city_grid(c, ctx, gx, gy, gw, gh, T)


def _wrap(f, text, w):
    out, cur = [], ""
    for word in text.split():
        cand = (cur + " " + word).strip()
        if cur and f.width(cand) > w:
            out.append(cur)
            cur = word
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def _muni_columns(c, ctx, x, y, w, h, groups, K, S):
    """Region headings (code chip) with their municipalities, flowed down then across up to 3 columns; the
    smaller face when the larger won't fit."""
    home = (getattr(ctx, "home_name", "") or "").lower()
    for f in (S, _tiny()):
        for ncol in (1, 2, 3):
            cw = (w - 6 * (ncol - 1)) // ncol
            items = []
            for name, code, ms in groups:
                items.append(("head", code))
                for m in ms:
                    items.append(("m", m))
            rows_per = max(1, h // f.line)
            if len(items) <= rows_per * ncol and all(f.width(t) <= cw - 6 for _, t in items):
                col, row = 0, 0
                for kind, t in items:
                    if row >= rows_per:
                        col, row = col + 1, 0
                    if kind == "head" and row == rows_per - 1:
                        col, row = col + 1, 0
                    xx, yy = x + col * (cw + 6), y + row * f.line
                    if kind == "head":
                        c.FillRect(xx, yy, f.width(t) + 4, f.h + 1, Color(*ctx.sev))
                        f.draw(c, xx + 2, yy, (0, 0, 0), t)
                    else:
                        is_home = t.lower() == home
                        f.draw(c, xx + 4, yy, ctx.sev2 if is_home else ctx.pal["strong"], t)
                    row += 1
                return
    S.draw(c, x, y, ctx.pal["text"], ch.fit(S, ", ".join(m for _, _, ms in groups for m in ms), w))


# ------------------------------------------------------------------------------------------------ special pages
def _rows(ctx, d, var, limit=8, transform=None):
    """[(town, values, emphasise)] for a variable: the alert's towns first, then the others."""
    s = af.series(d, var)
    if not s:
        return []
    mine, rest = af.town_order(ctx, d)
    names = af.towns(d)
    out = []
    for i in (mine + rest)[:limit]:
        vals = s["values"][i]
        out.append((names[i], transform(i, vals) if transform else vals, i in mine))
    return out


def special(c, ctx, inner, d):
    """Page 3 for the alert's weather type; False when there's nothing to show."""
    if (ctx.b.get("source") or "eccc").lower() != "eccc":
        return False
    if not d and not (ctx.b["hazard"] in ("flood", "smoke") and getattr(ctx, "extra", None)):
        return False
    fn = SPECIAL.get(ctx.b["hazard"], _general)
    return fn(c, ctx, inner, d) is not False


def _two_cols(p, split=0.48):
    x, y, w, h = p.body
    lw = int(w * split)
    return (x, y, lw, h), (x + lw + 6, y, w - lw - 6, h)


def _rain(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Rainfall outlook", "How much rain falls by town over the next 36 hours", _credit(d))
    T = d["_times"]
    rows = _rows(ctx, d, "precip")
    if not rows:
        return False
    tot = [(n, af.total(v), ctx.sev, e) for n, v, e in rows]
    (lx, ly, lw, lh), (rx, ry, rw, rh) = _two_cols(p, 0.46)
    bx, by, bw, bh = p.panel(lx, ly, lw, lh, "Total rain · mm · 36 h")
    ch.bars(c, bx, by, bw, bh, sorted(tot, key=lambda t: (not t[3], -t[1])), p.S, ctx.pal, "mm", val_font=p.K)
    facts = af.key_facts(ctx, d)[:3]
    th = p.T.h + p.K.line + p.S.line + 12
    p.tiles(rx, ry, rw, th, facts)
    gx, gy, gw, gh = p.panel(rx, ry + th + 4, rw, rh - th - 4, f"Rain so far · {rows[0][0]} · mm")
    acc = [None if v is None else v - (rows[0][1][0] or 0) for v in rows[0][1]]
    ch.line_chart(c, gx, gy, gw, gh, T, [(acc, ctx.sev, rows[0][0])], p.T, ctx.pal, "mm", lo=0)


def _snow(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Snow outlook", "Snow level against the towns, and the coldest spells", _credit(d))
    T = d["_times"]
    s_lvl = af.series(d, "snowlevel")
    if not s_lvl:
        return False
    mine, rest = af.town_order(ctx, d)
    names = af.towns(d)
    home = (mine or rest)[0]
    (lx, ly, lw, lh), (rx, ry, rw, rh) = _two_cols(p, 0.55)
    gx, gy, gw, gh = p.panel(lx, ly, lw, lh, f"Snow level · {names[home]} · m")
    town_m = af.ELEVATION_M.get(names[home], 0)
    guides = [(town_m, ctx.sev2, f"{names[home]} {town_m:,} m")]
    if names[home] != "Whistler":
        guides.append((670, _dim(ctx.sev2, 0.7), "Whistler 670 m"))
    ch.line_chart(c, gx, gy, gw, gh, T, [(s_lvl["values"][home], ctx.sev, "snow level")], p.T, ctx.pal, "m", lo=0, guides=guides)
    facts = af.key_facts(ctx, d)[:2]
    th = p.T.h + p.K.line + p.S.line + 12
    p.tiles(rx, ry, rw, th, facts)
    snow = af.series(d, "snow")
    items = []
    if snow:
        for n, v, e in _rows(ctx, d, "snow", limit=6):
            items.append((n, sum(x or 0 for x in v) * 3, ctx.sev, e))     # per-hour amounts sampled every 3 h
    bx, by, bw, bh = p.panel(rx, ry + th + 4, rw, rh - th - 4, "Snow · cm · model estimate")
    if items and any(v for _, v, _, _ in items):
        ch.bars(c, bx, by, bw, bh, items, p.S, ctx.pal, "cm", val_font=p.K)
    else:
        p.K.draw(c, bx, by + 2, ctx.pal["strong"], _U(ctx, "No snow in the forecast"))
        p.S.draw(c, bx, by + p.K.line + 2, ctx.pal["text"], ch.fit(p.S, "at these towns for the next 36 h", bw))


def _freezing(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Freezing rain risk", "Chance of freezing rain by town, hour by hour", _credit(d))
    rows = _rows(ctx, d, "fzra", limit=6)
    if not rows:
        return False
    x, y, w, h = p.body
    gh = int(h * 0.55)
    gx, gy, gw, ghh = p.panel(x, y, w, gh, "Chance of freezing rain · %")
    col = ch.ramp([(10, (60, 120, 200)), (30, (120, 200, 255)), (50, (230, 240, 255)), (70, (255, 120, 220))])
    ch.heat_strip(c, gx, gy, gw, ghh, rows, d["_times"], col, p.T, ctx.pal, show_vals=True)
    (lx, ly, lw, lh) = (x, y + gh + 4, int(w * 0.55), h - gh - 4)
    tv = _rows(ctx, d, "temp", limit=1)
    tx, ty, tw, tth = p.panel(lx, ly, lw, lh, f"Temperature · {tv[0][0] if tv else ''} · °C")
    if tv:
        ch.line_chart(c, tx, ty, tw, tth, d["_times"], [(tv[0][1], ctx.sev, "")], p.T, ctx.pal, "°",
                      guides=[(0, (120, 200, 255), "0°")])
    p.tiles(lx + lw + 4, ly, w - lw - 4, lh, af.key_facts(ctx, d)[:2])


def _wind(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Wind outlook", "Wind speed through the next 36 hours, and each town's peak", _credit(d))
    rows = _rows(ctx, d, "wind", limit=8)
    if not rows:
        return False
    T = d["_times"]
    (lx, ly, lw, lh), (rx, ry, rw, rh) = _two_cols(p, 0.58)
    gx, gy, gw, gh = p.panel(lx, ly, lw, lh, "Wind · km/h")
    lines = [(v, LINE_COLS[k], n) for k, (n, v, e) in enumerate([r for r in rows if r[2]][:3] or rows[:2])]
    ch.line_chart(c, gx, gy, gw, gh - p.T.line, T, lines, p.T, ctx.pal, "km/h", lo=0)
    lx2 = gx
    for vals, colr, name in lines:                                       # legend
        c.FillRect(lx2, gy + gh - p.T.h, 5, p.T.h - 1, Color(*colr))
        p.T.draw(c, lx2 + 7, gy + gh - p.T.h, ctx.pal["text"], name.upper())
        lx2 += p.T.width(name.upper()) + 14
    th = p.T.h + p.K.line + p.S.line + 12
    p.tiles(rx, ry, rw, th, af.key_facts(ctx, d)[:2])
    pk = []
    for n, v, e in rows[:6]:
        mv, mt = af.peak(v, T)
        pk.append((f"{n}", mv, ctx.sev, e))
    bx, by, bw, bh = p.panel(rx, ry + th + 4, rw, rh - th - 4, "Peak wind · km/h")
    ch.bars(c, bx, by, bw, bh, pk, p.S, ctx.pal, "km/h", val_font=p.S)


def _thunder(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Thunderstorm risk", "Chance of thunderstorms by town, hour by hour", _credit(d))
    rows = _rows(ctx, d, "tstorm", limit=7)
    if not rows:
        return False
    x, y, w, h = p.body
    gw_ = int(w * 0.66)
    gx, gy, gw, gh = p.panel(x, y, gw_, h, "Chance of thunderstorms · %")
    col = ch.ramp([(5, (90, 80, 20)), (20, (200, 170, 0)), (40, (255, 215, 0)), (60, (255, 120, 0)), (80, (255, 40, 40))])
    ch.heat_strip(c, gx, gy, gw, gh, rows, d["_times"], col, p.T, ctx.pal, show_vals=True)
    rx = x + gw_ + 4
    facts = af.key_facts(ctx, d)[:2]
    pop = _rows(ctx, d, "pop", limit=1)
    if pop:
        v, t = af.peak(pop[0][1], d["_times"])
        facts.append(("Rain chance", af.num(v, "%"), f"{pop[0][0]} · {af.when(t)}"))
    n = max(1, len(facts))
    th = (h - 4 * (n - 1)) // n
    for k, f in enumerate(facts):
        p.tiles(rx, y + k * (th + 4), w - gw_ - 4, th, [f])


def _flood(c, ctx, inner, d):
    """Tides and surge from DFO: the predicted tide through the next 36 h with the observed level over the last
    12 h, the high tides marked; tiles for the next high tide, the surge now, and the other gauges' next highs."""
    x_ = getattr(ctx, "extra", None) or {}
    sts = [st for st in x_.get("tides") or [] if st.get("pred")]
    if not sts:
        return _flood_model(c, ctx, inner, d) if d else False
    p = Page(c, ctx, inner, "Tides and storm surge", "Predicted tide, the level the gauges measure, and the next high tides",
             "Fisheries and Oceans Canada · Canadian Hydrographic Service")
    st = next((s_ for s_ in sts if s_["name"] == "Point Atkinson"), sts[0])
    times = [af.ts(t) for t, _ in st["pred"]]
    pred = [v for _, v in st["pred"]]
    ob = [(af.ts(t).timestamp(), v) for t, v in st.get("obs") or []]
    obs = []
    for t in times:                                   # measured level nearest each predicted time (within 10 min)
        near = min(ob, key=lambda o: abs(o[0] - t.timestamp()), default=None)
        obs.append(near[1] if near and abs(near[0] - t.timestamp()) <= 600 else None)
    x, y, w, h = p.body
    gw_ = int(w * 0.64)
    gx, gy, gw, gh = p.panel(x, y, gw_, h, f"{st['name']} · metres above chart datum")
    highs = af.next_high(st)
    guides = [(highs[0][1], _dim(ctx.sev2, 0.8), f"next high {af.num(highs[0][1], 'm', 1)}")] if highs else []
    ch.line_chart(c, gx, gy, gw, gh - p.T.line, times, [(pred, ctx.sev, "predicted"), (obs, (255, 255, 255), "measured")],
                  p.T, ctx.pal, "m", guides=guides, label_max=False)
    lx2 = gx
    for colr, name in ((ctx.sev, "PREDICTED"), ((255, 255, 255), "MEASURED")):
        c.FillRect(lx2, gy + gh - p.T.h, 5, p.T.h - 1, Color(*colr))
        p.T.draw(c, lx2 + 7, gy + gh - p.T.h, ctx.pal["text"], name)
        lx2 += p.T.width(name) + 14
    facts = []
    if highs:
        facts.append(("Next high tide", af.num(highs[0][1], "m", 1), f"{st['name']} · {af.when(highs[0][0])}"))
    pairs = [(o, pv) for o, pv in zip(obs, pred) if o is not None]
    if pairs:
        sg = pairs[-1][0] - pairs[-1][1]
        facts.append(("Surge now", f"{sg:+.2f} m", "measured minus predicted"))
    rx = x + gw_ + 4
    th = p.T.h + p.K.line + p.S.line + 12
    p.tiles(rx, y, w - gw_ - 4, th, facts[:2] if len(facts) < 2 else facts[:1])
    if len(facts) > 1:
        p.tiles(rx, y + th + 4, w - gw_ - 4, th, facts[1:2])
    ty = y + (th + 4) * min(2, len(facts))
    tx, tty, tw, tth = p.panel(rx, ty, w - gw_ - 4, y + h - ty, "Next high tide")
    short = {"Point Atkinson": "Pt Atkinson", "New Westminster": "New West"}
    rows = [(short.get(s_["name"], s_["name"]), f"{af.when(hs[0][0])} {hs[0][1]:.1f}m") for s_ in sts for hs in [af.next_high(s_)] if hs]
    f = next((f for f in (p.S, p.T) if all(f.width(a) + f.width(b) + 6 <= tw for a, b in rows)), p.T)
    yy = tty
    for name, v in rows:
        if yy + f.h > tty + tth:
            break
        f.draw(c, tx, yy, ctx.pal["strong"], name)
        f.draw(c, tx + tw - f.width(v), yy, ctx.sev, v)
        yy += f.line


def _flood_model(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Sea level outlook", "Forecast water level along the coast (tide plus surge)", "ECCC CIOPS-West forecast")
    s = af.series(d, "sealevel")
    if not s:
        return False
    T = d["_times"]
    x, y, w, h = p.body
    gx, gy, gw, gh = p.panel(x, y, w, h, "Water level above mean sea level · m")
    lines = [(vals, LINE_COLS[k % 4], d["sea"][k]["name"]) for k, vals in enumerate(s["values"]) if any(v is not None for v in vals)]
    ch.line_chart(c, gx, gy, gw, gh, T, lines[:3], p.T, ctx.pal, "m")


def _heat(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Heat outlook", "Temperature and humidex, and how much it cools off overnight", _credit(d))
    tv, dv = af.series(d, "temp"), af.series(d, "dew")
    if not tv:
        return False
    T = d["_times"]
    mine, rest = af.town_order(ctx, d)
    names = af.towns(d)
    home = (mine or rest)[0]
    (lx, ly, lw, lh), (rx, ry, rw, rh) = _two_cols(p, 0.56)
    gx, gy, gw, gh = p.panel(lx, ly, lw, lh, f"{names[home]} · °C")
    hum = [af.humidex(a, b) for a, b in zip(tv["values"][home], dv["values"][home])] if dv else []
    lines = [(tv["values"][home], ctx.sev, "temperature")] + ([(hum, (255, 255, 255), "humidex")] if hum else [])
    ch.line_chart(c, gx, gy, gw, gh - p.T.line, T, lines, p.T, ctx.pal, "°", guides=[(30, _dim(ctx.sev, 0.8), "30°")])
    lx2 = gx
    for vals, colr, name in lines:
        c.FillRect(lx2, gy + gh - p.T.h, 5, p.T.h - 1, Color(*colr))
        p.T.draw(c, lx2 + 7, gy + gh - p.T.h, ctx.pal["text"], name.upper())
        lx2 += p.T.width(name.upper()) + 14
    facts = af.key_facts(ctx, d)
    night = [v for v, t in zip(tv["values"][home], T) if v is not None and (t.hour >= 21 or t.hour <= 8)]
    if night:
        facts.append(("Tonight's low", af.num(min(night), "°C"), names[home]))
    th = p.T.h + p.K.line + p.S.line + 12
    p.tiles(rx, ry, rw, th, facts[:2])
    bars = [(n, max([x for x in v if x is not None] or [0]), ctx.sev, e) for n, v, e in _rows(ctx, d, "temp", limit=6)]
    bx, by, bw, bh = p.panel(rx, ry + th + 4, rw, rh - th - 4, "High by town · °C")
    ch.bars(c, bx, by, bw, bh, bars, p.S, ctx.pal, "°", lo=min(b[1] for b in bars) - 3, val_font=p.S)


def _cold(c, ctx, inner, d):
    frost = ctx.b["hazard"] == "frost"
    p = Page(c, ctx, inner, "Frost outlook" if frost else "Cold outlook",
             "Overnight lows by town" if frost else "Temperature and wind chill, and the coldest towns", _credit(d))
    tv, wv = af.series(d, "temp"), af.series(d, "wind")
    if not tv:
        return False
    T = d["_times"]
    mine, rest = af.town_order(ctx, d)
    names = af.towns(d)
    home = (mine or rest)[0]
    (lx, ly, lw, lh), (rx, ry, rw, rh) = _two_cols(p, 0.56)
    gx, gy, gw, gh = p.panel(lx, ly, lw, lh, f"{names[home]} · °C")
    lines = [(tv["values"][home], ctx.sev, "temperature")]
    if wv and not frost:
        lines.append(([af.wind_chill(a, b) for a, b in zip(tv["values"][home], wv["values"][home])], (255, 255, 255), "wind chill"))
    ch.line_chart(c, gx, gy, gw, gh - p.T.line, T, lines, p.T, ctx.pal, "°", guides=[(0, (120, 200, 255), "0°")], label_max=False)
    lx2 = gx
    for vals, colr, name in lines:
        c.FillRect(lx2, gy + gh - p.T.h, 5, p.T.h - 1, Color(*colr))
        p.T.draw(c, lx2 + 7, gy + gh - p.T.h, ctx.pal["text"], name.upper())
        lx2 += p.T.width(name.upper()) + 14
    facts = af.key_facts(ctx, d)
    below = sum(3 for v in tv["values"][home] if v is not None and v <= 0)
    facts.append(("Below 0°", f"{below} h", f"{names[home]} · next 36 h"))
    th = p.T.h + p.K.line + p.S.line + 12
    p.tiles(rx, ry, rw, th, facts[:2])
    lows = [(n, min([x for x in v if x is not None] or [0]), (120, 200, 255), e) for n, v, e in _rows(ctx, d, "temp", limit=6)]
    lo = min(b[1] for b in lows)
    hi = max(b[1] for b in lows)
    bx, by, bw, bh = p.panel(rx, ry + th + 4, rw, rh - th - 4, "Lowest by town · longer is colder")
    ch.bars(c, bx, by, bw, bh, [(n, hi + 2 - v, col, e, af.num(v, "°C")) for n, v, col, e in lows], p.S, ctx.pal, "",
            lo=0, val_font=p.S)
    _ = lo


def _fog(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Fog outlook", "Visibility by town, hour by hour (blank: clear of fog)", _credit(d))
    rows = _rows(ctx, d, "vis", limit=7)
    if not rows:
        return False
    x, y, w, h = p.body
    gw_ = int(w * 0.68)
    gx, gy, gw, gh = p.panel(x, y, gw_, h, "Visibility in fog · km")
    col = ch.ramp([(-1, (255, 60, 60)), (0.2, (255, 150, 0)), (0.5, (230, 200, 60)), (1.0, None)])
    ch.heat_strip(c, gx, gy, gw, gh, rows, d["_times"], lambda v: col(v) if v is not None and v < 1.0 else None, p.T, ctx.pal)
    facts = af.key_facts(ctx, d)[:1]
    tv, dv = _rows(ctx, d, "temp", limit=1), _rows(ctx, d, "dew", limit=1)
    if tv and dv:
        spread = [None if a is None or b is None else a - b for a, b in zip(tv[0][1], dv[0][1])]
        v, t = af.peak(spread, d["_times"], low=True)
        facts.append(("Temp - dew point", af.num(v, "°C"), f"{tv[0][0]} · smallest {af.when(t)}"))
    n = max(1, len(facts))
    th = (h - 4 * (n - 1)) // n
    for k, f in enumerate(facts):
        p.tiles(x + gw_ + 4, y + k * (th + 4), w - gw_ - 4, th, [f])


def _smoke(c, ctx, inner, d):
    """Air quality: the AQHI (ECCC's 1–10+ health index) by area now, as bars in the official colours; the alert's
    area now and its forecast peak as tiles; the forecast by area, period by period."""
    x_ = getattr(ctx, "extra", None) or {}
    areas = [a for a in af.aqhi_areas(ctx, x_) if a.get("obs") is not None or a.get("forecast")]
    if not areas:
        return _smoke_pm(c, ctx, inner, d) if d else False
    p = Page(c, ctx, inner, "Air quality", "Air Quality Health Index by area: now, and the forecast",
             "ECCC Air Quality Health Index (AQHI)")
    x, y, w, h = p.body
    hit = ap.affected(ctx)
    lw = int(w * 0.44)
    bx, by, bw, bh = p.panel(x, y, lw, h, "AQHI now · 1 low  4 moderate  7 high  10+ very high")
    items = [(af.AQHI_SHORT.get(a["name"], a["name"]), a.get("obs"), af.aqhi_colour(a.get("obs")),
              af.AQHI_REGION.get(a["name"]) in hit, f"{(a.get('obs') or 0):.0f}") for a in areas[:8]]
    ch.bars(c, bx, by, bw, bh, items, p.S, ctx.pal, "", lo=0, hi=max(10, max((i[1] or 0) for i in items)), val_font=p.K)
    rx, rw = x + lw + 4, w - lw - 4
    a0 = areas[0]
    peak = max(a0.get("forecast") or [[None, None]], key=lambda f: f[1] or 0)
    facts = [(f"{af.AQHI_SHORT.get(a0['name'], a0['name'])} now", f"{(a0.get('obs') or 0):.0f}", af.aqhi_word(a0.get("obs")))]
    if peak[1] is not None:
        facts.append(("Forecast peak", f"{peak[1]:.0f}", f"{af.aqhi_word(peak[1])} · {af.when(af.ts(peak[0]))}"))
    th = p.T.h + p.K.line + p.S.line + 12
    p.tiles(rx, y, rw, th, facts)
    gx, gy, gw, gh = p.panel(rx, y + th + 4, rw, h - th - 4, "AQHI forecast")
    now = time.time()
    fcs = [[f for f in (a.get("forecast") or []) if f[0] and af.ts(f[0]).timestamp() >= now - 3600][:12] for a in areas[:6]]
    times = [af.ts(f[0]) for f in max(fcs, key=len)] if any(fcs) else []
    if times:
        rows = [(af.AQHI_SHORT.get(a["name"], a["name"]), [f[1] for f in fc] + [None] * (len(times) - len(fc)),
                 af.AQHI_REGION.get(a["name"]) in hit) for a, fc in zip(areas[:6], fcs)]
        ch.heat_strip(c, gx, gy, gw, gh, rows, times, af.aqhi_colour, p.T, ctx.pal, show_vals=True)


def _smoke_pm(c, ctx, inner, d):
    p = Page(c, ctx, inner, "Smoke and air quality", "Fine particles (PM2.5) by town, hour by hour", "ECCC RAQDPS / FireWork forecast")
    rows = _rows(ctx, d, "pm25", limit=7)
    if not rows:
        return False
    x, y, w, h = p.body
    gx, gy, gw, gh = p.panel(x, y, w, h, "PM2.5 · µg/m³")
    col = ch.ramp([(0, (40, 120, 60)), (12, (230, 200, 40)), (35, (255, 120, 0)), (55, (230, 30, 30)), (150, (150, 40, 160))])
    ch.heat_strip(c, gx, gy, gw, gh, rows, d["_times"], col, p.T, ctx.pal, show_vals=True)


def _general(c, ctx, inner, d):
    p = Page(c, ctx, inner, "The next 36 hours", "Temperature, rain and wind in the alert's area", _credit(d))
    T = d["_times"]
    x, y, w, h = p.body
    cw = (w - 8) // 3
    for k, (var, lab, unit, acc) in enumerate((("temp", "Temperature · °C", "°", False), ("precip", "Rain so far · mm", "mm", True),
                                               ("wind", "Wind · km/h", "km/h", False))):
        rows = _rows(ctx, d, var, limit=1)
        gx, gy, gw, gh = p.panel(x + k * (cw + 4), y, cw, h, f"{lab}")
        if rows:
            vals = rows[0][1]
            if acc:
                vals = [None if v is None else v - (vals[0] or 0) for v in vals]
            ch.line_chart(c, gx, gy, gw, gh, T, [(vals, ctx.sev, rows[0][0])], p.T, ctx.pal, unit, lo=0 if acc or var == "wind" else None)


SPECIAL = {"rain": _rain, "snow": _snow, "freezing": _freezing, "wind": _wind, "thunder": _thunder, "tornado": _thunder,
           "flood": _flood, "heat": _heat, "cold": _cold, "frost": _cold, "fog": _fog, "smoke": _smoke, "general": _general}
TITLES = {"rain": "Rainfall outlook", "snow": "Snow outlook", "freezing": "Freezing rain risk", "wind": "Wind outlook",
          "thunder": "Thunderstorm risk", "tornado": "Thunderstorm risk", "flood": "Sea level outlook", "heat": "Heat outlook",
          "cold": "Cold outlook", "frost": "Frost outlook", "fog": "Fog outlook", "smoke": "Smoke and air quality",
          "general": "The next 36 hours"}
