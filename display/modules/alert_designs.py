"""Alert screen layouts: panels. Every layout splits the screen into labelled panels — the key facts each in
their own (the alert and its level box, WHERE on the Lower Mainland region grid, VALID UNTIL with the time left,
the AMOUNT) — and the message itself into SITUATION / IMPACTS / WHAT TO DO panels sized to their text, so the
whole alert shows on one screen with nothing wasted and nothing cut.

Text rules: a paragraph never splits across panels or columns (a panel that can't hold its paragraphs makes the
engine try the next smaller face), amounts ("50 to 70 mm") never break across lines, and big key facts take the
largest face that fits on one line instead of wrapping. Everything is measured in the theme's own fonts.

Each layout is a Structure (weather_alert.py): ``flow`` places the text in its panels, ``chrome`` draws the panels
and facts (cached once the reveal ends), ``dynamic`` the per-frame parts (the alert counter, amount pulse).
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from display.gfx import Color
from display.modules import alert_parts as ap
from display.modules import pixel_icons as px
from display.modules import weather_alert as wa

W, H = wa.W, wa.H
_dim = wa._dim
U = wa.U
_TINY = []


def tiny():
    if not _TINY:
        _TINY.append(wa.FI("alert/tom-thumb.bdf"))
    return _TINY[0]


def LH():
    """Height of a panel's label strip."""
    return tiny().h + 5


def kind_word(ctx):
    return ctx.b["kind"].upper()


def status_word(ctx):
    return (ctx.b["status"] or "in effect").upper()


def fit(f, s, w):
    """``s`` shortened (with an ellipsis) until it fits ``w`` in ``f``."""
    if f.width(s) <= w:
        return s
    dots = "…" if f.width("…") > 0 else "..."
    while s and f.width(s + dots) > w:
        s = s[:-1]
    return (s.rstrip() + dots) if s else ""


def faces(ctx, big=False, cap=None):
    """The family's faces for fixed text, largest first: (titles), key, the body bolds, small."""
    out, seen = [], set()
    for f in (list(ctx.fam["titles"]) if big else []) + [ctx.fam["key"]] + [b for _, b in ctx.fam["ladder"]] + [ctx.fam["small"]]:
        if id(f) not in seen and (cap is None or f.h <= cap or f is ctx.fam["small"]):
            seen.add(id(f))
            out.append(f)
    return out


def pick(ctx, s, w, big=False, cap=None):
    """(font, text): the largest face in which ``s`` fits ``w`` on one line; else the smallest face, shortened."""
    fs = faces(ctx, big, cap)
    for f in fs:
        if f.width(s) <= w:
            return f, s
    return fs[-1], fit(fs[-1], s, w)


def title_lines(ctx, w, max_lines=2, max_h=None, big=True):
    """(font, lines): the alert name wrapped in the largest face that needs at most ``max_lines`` lines of
    ``w`` (and fits ``max_h`` pixels tall, if given)."""
    tt = wa.title_text(ctx)
    for f in faces(ctx, big):
        ls = wa._wrap_plain(f, tt, w)
        if len(ls) <= max_lines and all(f.width(x) <= w for x in ls) and (max_h is None or len(ls) * f.line <= max_h):
            return f, ls
    f = faces(ctx)[-1]
    return f, [fit(f, x, w) for x in wa._wrap_plain(f, tt, w)[:max_lines]]


def where_text(ctx, f, maxw):
    """The affected regions on one line, fullest wording that fits; else as many codes as fit."""
    for var in ap.label_variants(ctx):
        s = U(ctx, (" · " if var[0][1] == ap.CODES.get(var[0][0]) else " + ").join(t for _, t in var))
        if s and f.width(s) <= maxw:
            return s
    out = ""
    for _, code in ap.labels(ctx):
        cand = (out + " · " if out else "") + code
        if f.width(cand) > maxw:
            break
        out = cand
    return out or fit(f, ap.labels(ctx)[0][1] if ap.labels(ctx) else "", maxw)


def region_lines(ctx, f, w, wrap=4):
    """Region lines for columns: the fullest wording whose lines fit ``w``. A single region's ECCC text may
    wrap over up to ``wrap`` lines; otherwise one region per line."""
    for var in ap.label_variants(ctx):
        if len(var) == 1 and var[0][1] not in (var[0][0], ap.CODES.get(var[0][0])):
            ls = wa._wrap_plain(f, U(ctx, var[0][1]), w)
            if len(ls) <= wrap and all(f.width(x) <= w for x in ls):
                return [(var[0][0], x) for x in ls]
            continue
        if all(f.width(U(ctx, t)) <= w for _, t in var):
            return [(n, U(ctx, t)) for n, t in var]
    return [(n, U(ctx, t)) for n, t in ap.label_variants(ctx)[-1]]


def badge_font(ctx, cap=None):
    return faces(ctx, cap=cap or ctx.fam["key"].h)[0]


def badge_w(ctx, f):
    return f.width(kind_word(ctx)) + 8


def level_badge(c, ctx, x, y, f, h=None):
    """The alert level as a label box in its own colour (WARNING red, WATCH orange, ADVISORY yellow,
    STATEMENT blue). Returns its width."""
    s = kind_word(ctx)
    w, h = f.width(s) + 8, h or f.h + 4
    c.FillRect(x, y, w, h, Color(*ctx.level))
    f.draw(c, x + 4, y + (h - f.h) // 2, (0, 0, 0), s)
    return w


def frame(c, x, y, w, h, col):
    for rx, ry, rw, rh in ((x, y, w, 1), (x, y + h - 1, w, 1), (x, y, 1, h), (x + w - 1, y, 1, h)):
        c.FillRect(rx, ry, rw, rh, Color(*col))


# ------------------------------------------------------------------------------------------------ panels
def panel(c, ctx, x, y, w, h, label=None, fill=True, icon=None):
    """A panel: raised surface (or just an outline, for text panels), a top rule in the weather colour and a
    small label."""
    if fill:
        c.FillRect(x, y, w, h, Color(*ctx.pal["panel"]))
    else:
        frame(c, x, y, w, h, _dim(ctx.sev, 0.32))
    if label:
        c.FillRect(x, y, w, LH(), Color(*_dim(ctx.sev, 0.22 if fill else 0.3)))
        lx = x + 3
        if icon:
            px.draw(c, icon, lx, y + (LH() - 8) // 2, ctx.pal["strong"], 8)
            lx += 11
        tiny().draw(c, lx, y + 3, ctx.pal["strong"] if not fill else ctx.pal["dim"], fit(tiny(), U(ctx, label).upper(), w - (lx - x) - 3))
    c.FillRect(x, y, w, 1, Color(*ctx.sev))


def alert_block(c, ctx, x, y, w, h, tf, tl):
    """Level box, then the alert's name (pre-wrapped in ``tf``), then its status."""
    bf = badge_font(ctx)
    c.FillRect(x, y, w, h, Color(*ctx.pal["panel"]))
    c.FillRect(x, y, w, 1, Color(*ctx.sev))
    px.draw(c, px.hazard(ctx.b["hazard"]), x + 3, y + 3, ctx.sev)
    level_badge(c, ctx, x + 30, y + 3 + (24 - bf.h - 4) // 2, bf)
    sf = ctx.fam["small"]
    st = fit(sf, status_word(ctx), w - 36 - badge_w(ctx, bf))
    sf.draw(c, x + 36 + badge_w(ctx, bf), y + 3 + (24 - sf.h) // 2, ctx.pal["dim"], st)
    yy = y + 30
    for ln in tl:
        if yy + tf.h > y + h - 1:
            break
        tf.draw(c, x + 4, yy, ctx.sev, ln)
        yy += tf.line


def alert_block_h(ctx, tf, tl):
    return 30 + len(tl) * tf.line + 3


def where_block(c, ctx, x, y, w, h):
    panel(c, ctx, x, y, w, h, "Where")
    cw, ch = ap.cart_size(tiny())
    gw, gh = min(cw, w - 6), min(ch, h - LH() - 3)
    ap.cartogram(c, ctx, x + (w - gw) // 2, y + LH() + 1 + (h - LH() - 3 - gh) // 2, tiny(), w=gw, h=gh)


def where_h():
    return LH() + ap.cart_size(tiny())[1] + 4


def when_block(c, ctx, x, y, w, h):
    """VALID UNTIL: the end time large, the time left, and a bar of how much of the alert's life is left."""
    panel(c, ctx, x, y, w, h, "Valid until")
    k, sf = ctx.fam["key"], ctx.fam["small"]
    b = ctx.b
    yy = y + LH() + 2
    room = h - LH() - 9
    uf, us = pick(ctx, U(ctx, b["until"] or "until further notice"), w - 8, big=True, cap=max(k.h, room - sf.line))
    uf.draw(c, x + 4, yy, ctx.pal["strong"], us)
    yy += uf.line
    r = ap.remaining_text(ctx)
    sub = U(ctx, (r + " left" if r else "") + (f" · issued {b['issued']}" if b["issued"] else ""))
    if yy + sf.h <= y + h - 5:
        sf.draw(c, x + 4, yy, ctx.pal["dim"], fit(sf, sub.strip(" ·"), w - 8))
    i0, i1 = ap.times(ctx)
    if i0 and i1 and i1 > i0:
        frac = max(0.0, min(1.0, (time.time() - i0) / (i1 - i0)))
        c.FillRect(x + 4, y + h - 4, w - 8, 2, Color(*_dim(ctx.sev, 0.25)))
        c.FillRect(x + 4, y + h - 4, max(1, int((w - 8) * (1 - frac))), 2, Color(*ctx.sev))


def when_h(ctx):
    return LH() + ctx.fam["key"].line + ctx.fam["small"].line + 8


def amount_block(c, ctx, x, y, w, h):
    """AMOUNT on one line in the largest face that fits; with no amount, the alert's place on the level scale."""
    amt = U(ctx, ctx.b["amount"] or "")
    panel(c, ctx, x, y, w, h, "Amount" if amt else "Alert level")
    room = h - LH() - 3
    if amt:
        fs = [f for f in faces(ctx, big=True) if f.h <= room] or [tiny()]
        for f in fs:
            if f.width(amt) <= w - 8:
                break
        else:
            f, amt = fs[-1], fit(fs[-1], amt, w - 8)
        f.draw(c, x + (w - f.width(amt)) // 2, y + LH() + 1 + (room - f.h) // 2, ctx.pal["amount"], amt)
    else:
        lh = min(room - 2, tiny().h + 7)
        ap.severity_ladder(c, ctx, x + 3, y + LH() + 1 + (room - lh) // 2, w - 6, lh, tiny(), ctx.t, vertical=False)


def title_strip(c, ctx, x, y, w, m):
    """Icon, level box, alert name; the end time at the right."""
    hh = m["hh"]
    px.draw(c, px.hazard(ctx.b["hazard"]), x, y + (hh - 24) // 2, ctx.sev)
    bf = m["bf"]
    level_badge(c, ctx, x + 30, y + (hh - bf.h - 4) // 2, bf)
    m["tf"].draw(c, x + 30 + badge_w(ctx, bf) + 6, y + (hh - m["tf"].h) // 2, ctx.sev, m["ts"])
    if m["us"]:
        m["uf"].draw(c, x + w - m["uf"].width(m["us"]), y + (hh - m["uf"].h) // 2, ctx.pal["strong"], m["us"])


def title_strip_metrics(ctx, w, with_until=True):
    k = ctx.fam["key"]
    bf = badge_font(ctx)
    uf, us = pick(ctx, U(ctx, "until " + ctx.b["until"]) if (with_until and ctx.b["until"]) else "", w // 3, cap=k.h)
    room = w - 30 - badge_w(ctx, bf) - 6 - (uf.width(us) + 10 if us else 0)
    tf, ts = pick(ctx, wa.title_text(ctx), room, big=True, cap=24)
    return {"bf": bf, "uf": uf, "us": us, "tf": tf, "ts": ts, "hh": max(24, tf.h) + 2}


# ------------------------------------------------------------------------------------------------ message sections
SECTIONS = [("Situation", ("lead", "detail"), "warning"), ("Impacts", ("impact",), "eye"), ("What to do", ("advice",), "arrow")]


def section_groups(paras):
    """[(label, icon, [paragraph indices])] for the sections the message has."""
    out = []
    for label, styles, icon in SECTIONS:
        idx = [i for i, p in enumerate(paras) if p["style"] in styles]
        if idx:
            out.append((label, icon, idx))
    if not out and paras:
        out.append(("Message", "warning", list(range(len(paras)))))
    return out


def _lay(ctx, paras, idx, box, fonts, gap):
    lines, over = wa.layout([paras[i] for i in idx], [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap, home=ctx.home_name)
    right = box[0] + box[2]
    for ln in lines:
        ln["para"] = idx[ln["para"]]
        for r in ln["runs"]:                       # a word wider than its panel: try the next face down
            if r[0] != "glyph" and r[0] + r[2].width(r[1]) > right + 1:
                over = True
    return lines, over


def _text_h(ctx, paras, idx, w, fonts, gap):
    lines, _ = _lay(ctx, paras, idx, (0, 0, w, 5000), fonts, gap)
    return max((ln["y"] + ln["h"] for ln in lines), default=0)


PAD = 3


def arrange_stack(ctx, paras, groups, rect, fonts, gap):
    """Sections one above the other, each panel as tall as its text; spare height shared out."""
    x, y, w, h = rect
    need = [LH() + 2 + _text_h(ctx, paras, g[2], w - 2 * PAD, fonts, gap) + 3 for g in groups]
    gapy = 4
    total = sum(need) + gapy * (len(groups) - 1)
    over = total > h
    extra = max(0, h - total) // max(1, len(groups))
    lines, panels, yy = [], [], y
    for g, nh in zip(groups, need):
        ph = nh + extra
        panels.append((x, yy, w, ph, g[0], g[1]))
        ls, o = _lay(ctx, paras, g[2], (x + PAD, yy + LH() + 2, w - 2 * PAD, ph - LH() - 4), fonts, gap)
        lines += ls
        over = over or o
        yy += ph + gapy
    return lines, panels, over


def _share(avail, wts, min_w):
    """Widths in proportion to ``wts``, none under ``min_w`` (the others give way), summing to ``avail``."""
    n = len(wts)
    fixed = {}
    while True:
        free = avail - sum(fixed.values())
        rest = [i for i in range(n) if i not in fixed]
        tot = sum(wts[i] for i in rest) or 1
        ws = {i: free * wts[i] / tot for i in rest}
        low = [i for i in rest if ws[i] < min_w]
        if not low or len(fixed) + len(low) >= n:
            break
        for i in low:
            fixed[i] = min_w
    out = [int(fixed.get(i, ws.get(i, min_w))) for i in range(n)]
    out[-1] = avail - sum(out[:-1])
    return out


def arrange_cols(ctx, paras, groups, rect, fonts, gap, min_w=96):
    """Sections side by side, widths shared by how much text each holds."""
    x, y, w, h = rect
    n = len(groups)
    gapx = 4
    avail = w - gapx * (n - 1)
    wts = [sum(len(paras[i]["text"]) for i in g[2]) + 80 for g in groups]
    ws = _share(avail, wts, min_w)
    lines, panels, over, xx = [], [], False, x
    for g, cw in zip(groups, ws):
        panels.append((xx, y, cw, h, g[0], g[1]))
        ls, o = _lay(ctx, paras, g[2], (xx + PAD, y + LH() + 2, cw - 2 * PAD, h - LH() - 4), fonts, gap)
        lines += ls
        over = over or o or cw < min_w - 1
        xx += cw + gapx
    return lines, panels, over


def arrange_flow(ctx, paras, groups, rect, fonts, gap, n=2):
    """Sections packed into ``n`` columns, top to bottom, never split; each column's last panel takes the rest."""
    x, y, w, h = rect
    gapx, gapy = 6, 4
    cw = (w - gapx * (n - 1)) // n
    cols = [[] for _ in range(n)]
    need = [LH() + 2 + _text_h(ctx, paras, g[2], cw - 2 * PAD, fonts, gap) + 3 for g in groups]
    ci, used, over = 0, 0, False
    for g, nh in zip(groups, need):
        if cols[ci] and used + gapy + nh > h and ci + 1 < n:
            ci, used = ci + 1, 0
        cols[ci].append((g, nh))
        used += (gapy if len(cols[ci]) > 1 else 0) + nh
        over = over or used > h
    lines, panels = [], []
    for k, col in enumerate(cols):
        xx, yy = x + k * (cw + gapx), y
        for j, (g, nh) in enumerate(col):
            ph = (y + h - yy) if j == len(col) - 1 else nh
            panels.append((xx, yy, cw, ph, g[0], g[1]))
            ls, o = _lay(ctx, paras, g[2], (xx + PAD, yy + LH() + 2, cw - 2 * PAD, ph - LH() - 4), fonts, gap)
            lines += ls
            over = over or o
            yy += ph + gapy
    return lines, panels, over


ARRANGE = {"stack": arrange_stack, "cols": arrange_cols, "flow": arrange_flow}


# ------------------------------------------------------------------------------------------------ base
class Design(wa.Structure):
    lists_cities = True          # where is the region grid / chips; the regions paragraph would repeat it
    arrange = "stack"
    fallbacks = ("stack", "flow", "cols")

    def m(self, ctx):
        cache = ctx.__dict__.setdefault("_m", {})
        key = type(self).__name__
        if key not in cache:
            cache[key] = self.metrics(ctx)
        return cache[key]

    def metrics(self, ctx):
        return {}

    def title_font(self, ctx, width=None):
        return self.m(ctx).get("tf") or ctx.fam["key"]

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return (x0, y0, x1 - x0, y1 - y0)

    def text_area(self, ctx):
        x, y, w, h = self.text_rect(ctx)
        foot = ctx.fam["small"].h + 3 if getattr(ctx, "count", 1) > 1 else 0
        return (x, y, w, h - foot)

    def boxes(self, ctx):
        return [self.text_area(ctx)]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        """The sections in this layout's preferred arrangement, else its alternatives (stacked, side by side,
        two columns) at the same size — the largest face that fits any of them wins. Whatever can't fit (the
        engine's last resort) is clipped to the text area, never drawn over the facts."""
        groups = section_groups(paras)
        rect = self.text_area(ctx)
        first = None
        for name in (self.arrange,) + tuple(a for a in self.fallbacks if a != self.arrange):
            lines, panels, over = ARRANGE[name](ctx, paras, groups, rect, fonts, gap)
            if first is None:
                first = (lines, panels)
            if not over:
                ctx.panels = panels
                return lines, False
        lines, panels = first
        x, y, w, h = rect
        ctx.panels = [(px_, py, pw, min(ph, y + h - py), lab, ic) for px_, py, pw, ph, lab, ic in panels if py < y + h - LH()]
        return [ln for ln in lines if ln["y"] + ln["h"] <= y + h - 1], True

    def chrome(self, c, ctx):
        for x, y, w, h, label, icon in getattr(ctx, "panels", []):
            panel(c, ctx, x, y, w, h, label, fill=False, icon=icon)
        self.facts(c, ctx)

    def facts(self, c, ctx):
        pass

    def moving(self, c, ctx, seg, p):
        pass

    def dynamic(self, c, ctx, seg, p):
        self.moving(c, ctx, seg, p)
        if p >= 1.0 and ctx.th.get("pulse"):                 # amounts glow every couple of seconds
            k = max(0.0, math.sin(ctx.t * 2.4)) ** 3
            if k > 0.02:
                amt = tuple(ctx.pal["amount"])
                col = tuple(int(a + (255 - a) * k) for a in amt)
                for ln in seg["lines"]:
                    for run in ln["runs"]:
                        if run[0] != "glyph" and tuple(run[3]) == amt:
                            run[2].draw(c, run[0], ln["y"], col, run[1])
        if ctx.count > 1:
            x, y, w, h = self.text_rect(ctx)
            sf = ctx.fam["small"]
            s = f"{ctx.index + 1}/{ctx.count}"
            cx, cy = x + w - sf.width(s) - 4, y + h - sf.h - 1
            c.FillRect(cx - 2, cy - 1, sf.width(s) + 4, sf.h + 2, Color(*ctx.sev))
            sf.draw(c, cx, cy, (0, 0, 0), s)



# ------------------------------------------------------------------------------------------------ key facts
def key_facts(ctx):
    """The weather type's key numbers for this alert (alert_facts.key_facts), cached on the alert."""
    if not hasattr(ctx, "_kf"):
        from display.modules import alert_facts as af
        from display.modules import alert_story as st
        facts = af.key_facts(ctx, getattr(ctx, "data", None))
        got = st.forecast_series(ctx, getattr(ctx, "data", None)) if ctx.b.get("test") else None
        if got and got[3]:                                   # sample numbers: the same ones the forecast page shows
            _, _, summ, _ = got
            var, how, label, unit, _, _, _ = st.FORECAST[ctx.b["hazard"]]
            hit = ap.affected(ctx)
            pool = {r: v for r, v in summ.items() if r in hit and v is not None} or {r: v for r, v in summ.items() if v is not None}
            low = how in ("chill", "low") or var == "vis"
            r, v = (min if low else max)(pool.items(), key=lambda kv: kv[1])
            word = {"accum": "Most", "accum3": "Most", "chill": "Coldest", "low": "Lowest"}.get(how, "Peak")
            sh = {"Chance of freezing rain": "Freezing rain", "Chance of thunderstorms": "Storm chance", "Visibility in fog": "Visibility",
                     "Rain so far": "Rain", "Snow so far": "Snow", "Wind chill": "Wind chill", "Humidex": "Humidex"}.get(label, label)
            facts = [f for f in facts if f[2] == "in the alert"] + [(f"{word} {label.lower()}|{word} {sh.lower()}|{sh}",
                                                                     st._fmt(v, unit), f"{r} · sample|{r}")]
        ctx._kf = facts
    return ctx._kf


def fact_tile(c, ctx, x, y, w, h, fact, accent=None):
    """One fact as a tile: small label, the value on one line in the largest face that fits, a sub-line."""
    from display.modules import alert_charts as ch
    lab, val, sub = fact
    val = str(val).replace(" degrees", "°").replace(" degree", "°")
    v, unit = val, ""
    for u in (" µg/m³", " km/h", " mm", " cm", " km", " m", " %"):
        if str(val).endswith(u):
            v, unit = str(val)[: -len(u)], u.strip()
    fs = [f for f in faces(ctx, big=True) if f.h <= 24] or [ctx.fam["key"]]
    ch.tile(c, x, y, w, h, lab, U(ctx, v), unit, U(ctx, sub or ""), ctx.pal, fs + [ctx.fam["small"]], accent or ctx.sev, tiny())


def facts_row(c, ctx, x, y, w, h, facts, gap=4):
    if not facts:
        return
    n = len(facts)
    tw = (w - gap * (n - 1)) // n
    for k, f in enumerate(facts):
        fact_tile(c, ctx, x + k * (tw + gap), y, tw, h, f, (255, 255, 255) if k == 0 else None)


def facts_col(c, ctx, x, y, w, h, facts, gap=4):
    if not facts:
        return
    n = len(facts)
    th = (h - gap * (n - 1)) // n
    for k, f in enumerate(facts):
        fact_tile(c, ctx, x, y + k * (th + gap), w, th, f, (255, 255, 255) if k == 0 else None)


def tile_h(ctx):
    return tiny().h + 5 + min(24, max(f.h for f in faces(ctx, big=True) if f.h <= 24)) + ctx.fam["small"].h + 6


def chip_row(c, ctx, x, y, w, font=None):
    return ap.chips(c, ctx, x, y, w, font or ctx.fam["key"], lines=1)


# ============================================================================================ 1 console
class Console(Design):
    """A fact column — the alert and its level, WHERE (region grid), the weather type's key numbers — beside the
    message in section panels."""
    CW = 156

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        tf, tl = title_lines(ctx, self.CW - 8, 3, max_h=48)
        return {"tf": tf, "tl": tl, "ah": alert_block_h(ctx, tf, tl), "wh": where_h()}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return (x0 + self.CW + 6, y0, x1 - x0 - self.CW - 6, y1 - y0)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        alert_block(c, ctx, x0, y0, self.CW, m["ah"], m["tf"], m["tl"])
        y = y0 + m["ah"] + 4
        where_block(c, ctx, x0, y, self.CW, m["wh"])
        y += m["wh"] + 4
        kf = key_facts(ctx)[:2]
        if kf and y1 - y >= tile_h(ctx) - 6:
            facts_col(c, ctx, x0, y, self.CW, y1 - y, kf[: max(1, (y1 - y) // tile_h(ctx))])
        elif y1 - y > LH() + 8:
            amount_block(c, ctx, x0, y, self.CW, y1 - y)


# ============================================================================================ 2 headline
class Headline(Design):
    """Where first: the affected regions as the headline, then the alert's name with its level box, the key
    numbers as a row of tiles, and the message in section columns."""
    arrange = "cols"
    fallbacks = ("cols", "flow", "stack")

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        w = x1 - x0
        k = ctx.fam["key"]
        wf, ws = k, where_text(ctx, k, w)
        for f in faces(ctx, big=True):
            s = where_text(ctx, f, w)
            if f.width(s) <= w and f.h <= 24:
                wf, ws = f, s
                break
        bf = badge_font(ctx)
        tf, ts = pick(ctx, wa.title_text(ctx), w - badge_w(ctx, bf) - 36, cap=k.h)
        b = ctx.b
        size = len(b.get("summary") or "") + sum(len(x) for x in (b.get("details") or []) + (b.get("impacts") or []) + (b.get("advice") or []))
        kf = key_facts(ctx)[:3] if size < 480 else []          # long messages keep the room (the numbers are on page 3)
        th = tile_h(ctx) if kf else 0
        top = wf.line + 2 + max(24, tf.h) + 3 + (th + 4 if kf else 0)
        return {"wf": wf, "ws": ws, "bf": bf, "tf": tf, "ts": ts, "kf": kf, "th": th, "top": top}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["top"]
        return (x0, t, x1 - x0, y1 - t)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        wa.draw_home_text(c, ctx, m["wf"], x0, y0, m["ws"], ctx.pal["strong"])
        y = y0 + m["wf"].line + 2
        px.draw(c, px.hazard(ctx.b["hazard"]), x0, y, ctx.sev)
        bw = level_badge(c, ctx, x0 + 30, y + (24 - m["bf"].h - 4) // 2, m["bf"])
        m["tf"].draw(c, x0 + 36 + bw, y + (24 - m["tf"].h) // 2, ctx.sev, m["ts"])
        y += max(24, m["tf"].h) + 3
        if m["kf"]:
            facts_row(c, ctx, x0, y, x1 - x0, m["th"], m["kf"])


# ============================================================================================ 3 ticket
class Ticket(Design):
    """A ticket: a perforated stub on the left with the level, the weather icon and the alert's regions (one
    per line); the body carries the alert's name and the message."""
    SW = 118

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        bw = x1 - x0 - self.SW - 8
        tf, tl = title_lines(ctx, bw, 1, max_h=24)
        return {"tf": tf, "tl": tl, "bw": bw}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["tf"].line + 6
        return (x0 + self.SW + 8, t, m["bw"], y1 - t)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        sw = self.SW
        c.FillRect(x0, y0, sw, y1 - y0, Color(*ctx.pal["panel"]))
        c.FillRect(x0, y0, sw, 2, Color(*ctx.sev))
        for yy in range(y0 + 4, y1 - 2, 4):                       # perforation
            c.FillRect(x0 + sw + 3, yy, 1, 2, Color(*_dim(ctx.pal["dim"], 0.8)))
        for cy in (y0, y1 - 1):
            c.FillRect(x0 + sw + 1, cy - 2 if cy == y0 else cy - 2, 5, 3, Color(*ctx.pal["bg"]))
        bf = badge_font(ctx, cap=max(k.h, 16))
        level_badge(c, ctx, x0 + (sw - badge_w(ctx, bf)) // 2, y0 + 6, bf, h=bf.h + 6)
        px.draw(c, px.hazard(ctx.b["hazard"]), x0 + (sw - 24) // 2, y0 + bf.h + 16, ctx.sev)
        y = y0 + bf.h + 46
        tiny().draw(c, x0 + 5, y, ctx.pal["dim"], "AREAS")
        y += tiny().h + 3
        lines = region_lines(ctx, sf, sw - 10)
        for i, (name, t) in enumerate(lines):
            if y + sf.h > y1 - 3:
                tiny().draw(c, x0 + 5, y - 1, ctx.pal["dim"], f"+{len(lines) - i} MORE")
                break
            home = ap.is_home(ctx, name)
            if home:
                c.FillRect(x0 + 3, y - 1, sf.width(t) + 4, sf.h + 2, Color(*ctx.sev))
            sf.draw(c, x0 + 5, y, (0, 0, 0) if home else ctx.pal["strong"], t)
            y += sf.line
        bx = x0 + sw + 8
        m["tf"].draw(c, bx, y0, ctx.sev, m["tl"][0] if m["tl"] else "")
        c.FillRect(bx, y0 + m["tf"].line + 2, m["bw"], 1, Color(*_dim(ctx.sev, 0.5)))


# ============================================================================================ 4 checklist
class Checklist(Design):
    """What to do as a checklist: the situation and impacts on the left, the advice on the right as tick-box
    items, the regions as chips above them."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = title_strip_metrics(ctx, x1 - x0, with_until=False)
        m["cw"] = int((x1 - x0) * 0.42)
        m["chip_h"] = ctx.fam["key"].h + 4
        return m

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["hh"] + 4
        return (x0, t, x1 - x0, y1 - t)

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        x, y, w, h = self.text_area(ctx)
        m = self.m(ctx)
        cw = m["cw"]
        adv = [i for i, p in enumerate(paras) if p["style"] == "advice"]
        rest = [g for g in section_groups(paras) if g[0] != "What to do"]
        lw = w - (cw + 6 if adv else 0)
        lines, panels, over = arrange_stack(ctx, paras, rest, (x, y, lw, h), fonts, gap) if rest else ([], [], False)
        ctx.checks = []
        if adv:
            rx = x + lw + 6
            top = y + m["chip_h"] + 4
            panels.append((rx, top, cw, y + h - top, "What to do", "check"))
            # one tick box per advice sentence
            yy = top + LH() + 3
            items = []
            for i in adv:
                for s in alert_brief_sentences(paras[i]["text"]):
                    items.append(dict(paras[i], text=s))
            for it in items:
                ls, o = wa.layout([it], [(rx + 14, yy, cw - 18, y + h - yy - 2)], fonts, ctx.pal, ctx.upper, prefix="none",
                                  gap=gap, home=ctx.home_name)
                for ln in ls:
                    ln["para"] = adv[0]
                if ls:
                    ctx.checks.append((rx + 4, ls[0]["y"] + (ls[0]["h"] - 7) // 2))
                    yy = ls[-1]["y"] + ls[-1]["h"] + gap + 1
                lines += ls
                over = over or o
        ctx.panels = panels
        return lines, over

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        title_strip(c, ctx, x0, y0, x1 - x0, m)
        x, y, w, h = self.text_area(ctx)
        if any(p[4] == "What to do" for p in getattr(ctx, "panels", [])):
            rx = x + w - m["cw"]
            chip_row(c, ctx, rx, y, m["cw"])
        for cx, cy in getattr(ctx, "checks", []):
            frame(c, cx, cy, 7, 7, ctx.pal["advice"])
            c.FillRect(cx + 2, cy + 3, 1, 2, Color(*ctx.pal["advice"]))
            c.FillRect(cx + 3, cy + 4, 1, 1, Color(*ctx.pal["advice"]))
            c.FillRect(cx + 4, cy + 2, 1, 2, Color(*ctx.pal["advice"]))


def alert_brief_sentences(text):
    from display.modules import alert_brief
    return alert_brief._sentences(text) or [text]


# ============================================================================================ 5 infographic
class Infographic(Design):
    """Numbers first: the weather type's key numbers as big tiles beside the region grid, the alert's name above
    them, the message in columns below."""
    arrange = "cols"
    fallbacks = ("cols", "flow", "stack")

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = title_strip_metrics(ctx, x1 - x0, with_until=False)
        m["rh"] = max(where_h(), tile_h(ctx))
        return m

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["hh"] + 3 + m["rh"] + 4
        return (x0, t, x1 - x0, y1 - t)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        title_strip(c, ctx, x0, y0, x1 - x0, m)
        y = y0 + m["hh"] + 3
        gw = min(ap.cart_size(tiny())[0], 170) + 6
        where_block(c, ctx, x1 - gw, y, gw, m["rh"])
        kf = key_facts(ctx)[:3]
        if kf:
            facts_row(c, ctx, x0, y, x1 - x0 - gw - 4, m["rh"], kf)
        else:
            amount_block(c, ctx, x0, y, x1 - x0 - gw - 4, m["rh"])


# ============================================================================================ 6 sign
class Sign(Design):
    """An overhead variable-message sign: what · where · the key number, lit in the weather's colour between a
    flashing beacon (in the level's colour); the message in panels below."""
    arrange = "cols"
    fallbacks = ("cols", "flow", "stack")

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        bf = badge_font(ctx)
        w = x1 - x0 - 2 * (badge_w(ctx, bf) + 14)
        tf, ts = pick(ctx, wa.title_text(ctx).upper(), w, big=True, cap=20)
        wf, ws = k, where_text(ctx, k, w).upper()
        for f in faces(ctx, cap=k.h):
            s_ = where_text(ctx, f, w).upper()
            if f.width(s_) <= w:
                wf, ws = f, s_
                break
        kf = key_facts(ctx)[:1]
        from display.modules import alert_charts as ch
        l3 = "|".join(f"{alt}: {kf[0][1]}" for alt in kf[0][0].split("|")).upper() if kf else ""
        lf = next((f for f in faces(ctx, cap=k.h) if f.width(ch.choose(f, l3, w)) <= w and ch.choose(f, l3, w)), faces(ctx)[-1])
        ls = ch.choose(lf, l3, w)
        sh = tf.line + wf.line + (lf.line if ls else 0) + 10
        return {"tf": tf, "ts": ts, "wf": wf, "ws": ws, "lf": lf, "ls": ls, "sh": sh, "bf": bf}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["sh"] + 5
        return (x0, t, x1 - x0, y1 - t)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sh = m["sh"]
        c.FillRect(x0, y0, x1 - x0, sh, Color(58, 60, 66))
        c.FillRect(x0 + 3, y0 + 3, x1 - x0 - 6, sh - 6, Color(0, 0, 0))
        level_badge(c, ctx, x0 + 8, y0 + (sh - m["bf"].h - 4) // 2, m["bf"])
        lit = ap._mix(ctx.sev, (255, 255, 255), 0.15)
        cx, y = (x0 + x1) // 2, y0 + 5
        for f, s_, col in ((m["tf"], m["ts"], lit), (m["wf"], m["ws"], lit), (m["lf"], m["ls"], ctx.pal["amount"])):
            if s_:
                f.draw(c, cx - f.width(s_) // 2, y, col, s_)
                y += f.line

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        sh = self.m(ctx)["sh"]
        on = int(ctx.t * 1.6) % 2
        sz = min(14, sh - 12)
        c.FillRect(x1 - 8 - sz, y0 + (sh - sz) // 2, sz, sz, Color(*(ctx.level if on else _dim(ctx.level, 0.15))))


# ============================================================================================ 7 wire
class Wire(Design):
    """The bulletin as it comes off the wire (tagged WHAT / INFO / EXPECT / DO), with a column for the level, the
    affected AREAS (home in its tint) and the key numbers."""
    CODES = {"rain": 11, "snow": 12, "freezing": 13, "wind": 14, "thunder": 15, "tornado": 16, "flood": 17, "heat": 18,
             "cold": 19, "frost": 20, "fog": 21, "smoke": 22, "general": 23}
    RW = 124

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        lw = x1 - x0 - self.RW - 8
        b = ctx.b
        ts = b.get("_issued_ts") or 0
        stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%d%H%M") if ts else "------"
        title = b["title"] if kind_word(ctx) in b["title"].upper() else f"{b['title']} {kind_word(ctx)}"
        h1 = pick(ctx, f"WWCN{self.CODES.get(b['hazard'], 23):02d} CWVR {stamp}", lw, cap=k.h)
        h2 = pick(ctx, f"{title} {status_word(ctx)}".upper(), lw, cap=k.h)
        return {"lw": lw, "h1": h1, "h2": h2, "top": h1[0].line + h2[0].line + 6, "tf": h2[0]}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        return (x0, y0 + m["top"], m["lw"], y1 - y0 - m["top"])

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        ctx.panels = []
        lines, over = wa.layout(paras, [self.text_area(ctx)], fonts, ctx.pal, True, prefix="tag", gap=gap, home=ctx.home_name)
        return lines, over

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sf = ctx.fam["small"]
        (f1, s1), (f2, s2) = m["h1"], m["h2"]
        f1.draw(c, x0, y0, ctx.pal["dim"], s1)
        f2.draw(c, x0, y0 + f1.line, ctx.sev, s2)
        for xx in range(x0, x0 + m["lw"], 4):
            c.FillRect(xx, y0 + m["top"] - 3, 2, 1, Color(*_dim(ctx.sev, 0.6)))
        x, w, y = x1 - self.RW, self.RW, y0
        bf = badge_font(ctx)
        level_badge(c, ctx, x, y, bf, h=bf.h + 6)
        y += bf.h + 10
        labs = ap.labels(ctx)
        kf = key_facts(ctx)[:2]
        th = tile_h(ctx)
        ah = min(LH() + len(labs) * sf.line + 4, y1 - y - len(kf) * (th + 4))
        panel(c, ctx, x, y, w, ah, "Areas")
        yy = y + LH() + 2
        for i, (name, code) in enumerate(labs):
            if yy + sf.h > y + ah - 2:
                break
            sf.draw(c, x + 4, yy, ap.HOME_TINT if ap.is_home(ctx, name) else ctx.pal["strong"], code)
            yy += sf.line
        y += ah + 4
        if kf:
            facts_col(c, ctx, x, y, w, y1 - y, kf)

    def moving(self, c, ctx, seg, p):
        lines = seg["lines"]
        if p >= 1.0 and lines and int(ctx.t * 2) % 2:
            ln = lines[-1]
            runs = [r for r in ln["runs"] if r[0] != "glyph"]
            if runs:
                last = runs[-1]
                x = last[0] + last[2].width(last[1]) + 3
                if x + 4 < ln["x0"] + ln["w"]:
                    c.FillRect(x, ln["y"], max(4, last[2].width("M") - 1), ln["h"], Color(*ctx.pal["strong"]))


# ============================================================================================ 8 poster
class Poster(Design):
    """A colour block in the weather's own colour with the icon, level box, name and regions in large type; the
    message in panels to its right."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        lw = 150
        tf, tl = title_lines(ctx, lw - 12, 3, max_h=72)
        return {"lw": lw, "tf": tf, "tl": tl}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        lw = self.m(ctx)["lw"]
        return (x0 + lw + 6, y0, x1 - x0 - lw - 6, y1 - y0)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        lw, iw = m["lw"], m["lw"] - 12
        col = ctx.sev
        ink = (0, 0, 0) if sum(col) > 330 else (255, 255, 255)
        c.FillRect(x0, y0, lw, y1 - y0, Color(*col))
        x, y = x0 + 6, y0 + 5
        px.draw(c, px.hazard(ctx.b["hazard"]), x, y, ink)
        bf = badge_font(ctx)
        c.FillRect(x + 29, y + (24 - bf.h - 4) // 2 - 1, badge_w(ctx, bf) + 2, bf.h + 6, Color(0, 0, 0))
        level_badge(c, ctx, x + 30, y + (24 - bf.h - 4) // 2, bf)
        y += 28
        for ln in m["tl"]:
            m["tf"].draw(c, x, y, ink, ln)
            y += m["tf"].line
        y += 4
        kf = key_facts(ctx)[:1]
        bottom = y1 - (k.line + sf.line + 8 if kf else 2)
        for i, (name, t) in enumerate(region_lines(ctx, k, iw)):
            f, s_ = pick(ctx, t, iw, cap=k.h)
            if y + f.h > bottom:
                break
            if ap.is_home(ctx, name):
                c.FillRect(x - 2, y - 1, f.width(s_) + 4, f.h + 2, Color(*ink))
                f.draw(c, x, y, col, s_)
            else:
                f.draw(c, x, y, ink, s_)
            y += f.line
        if kf:
            lab, val, sub = kf[0]
            from display.modules import alert_charts as ch
            sf.draw(c, x, y1 - k.line - sf.line - 5, ink, ch.choose(sf, U(ctx, lab).upper(), iw))
            f, s_ = pick(ctx, U(ctx, val), iw, cap=k.h)
            f.draw(c, x, y1 - k.line - 3, ink, s_)


# ============================================================================================ 9 kanban
class Kanban(Design):
    """Three columns — NOW, EXPECT, DO — under a header with the name, the regions as chips and the level; the
    columns are as wide as their text needs."""
    arrange = "cols"
    fallbacks = ("cols",)

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = title_strip_metrics(ctx, x1 - x0, with_until=False)
        m["chip_h"] = ctx.fam["key"].h + 4
        return m

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["hh"] + 3 + m["chip_h"] + 5
        return (x0, t, x1 - x0, y1 - t)

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        lines, over = super().flow(ctx, paras, boxes, fonts, gap)
        names = {"Situation": "Now", "Impacts": "Expect", "What to do": "Do"}
        ctx.panels = [(a, b_, w, h, names.get(lab, lab), ic) for a, b_, w, h, lab, ic in ctx.panels]
        return lines, over

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        title_strip(c, ctx, x0, y0, x1 - x0, m)
        chip_row(c, ctx, x0, y0 + m["hh"] + 3, x1 - x0)


# ============================================================================================ 10 strip
class Strip(Design):
    """The alert's name with the region grid beside it; the message in full-width section panels; the key numbers
    along the foot."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        cw, ch_ = ap.cart_size(tiny())
        cw = min(cw, 200)
        lw = x1 - x0 - cw - 8
        bf = badge_font(ctx)
        top = bf.h + 6
        tf, tl = title_lines(ctx, lw - 30, 2, max_h=max(ch_ + 8 - top, ctx.fam["small"].line))
        th = max(ch_, top + len(tl) * tf.line, 24) + 2
        kf = key_facts(ctx)[:3]
        sb = tile_h(ctx) if kf else 0
        return {"cw": cw, "ch": ch_, "lw": lw, "bf": bf, "top": top, "tf": tf, "tl": tl, "th": th, "sb": sb, "kf": kf}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["th"] + 4
        return (x0, t, x1 - x0, y1 - t - (m["sb"] + 4 if m["sb"] else 0))

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sf = ctx.fam["small"]
        px.draw(c, px.hazard(ctx.b["hazard"]), x0, y0 + (m["th"] - 24) // 2, ctx.sev)
        bw = level_badge(c, ctx, x0 + 30, y0, m["bf"])
        sf.draw(c, x0 + 36 + bw, y0 + (m["bf"].h + 4 - sf.h) // 2, ctx.pal["dim"], fit(sf, status_word(ctx), m["lw"] - 40 - bw))
        for i, ln in enumerate(m["tl"]):
            m["tf"].draw(c, x0 + 30, y0 + m["top"] + i * m["tf"].line, ctx.sev, ln)
        ap.cartogram(c, ctx, x1 - m["cw"], y0 + (m["th"] - m["ch"]) // 2, tiny(), w=m["cw"], h=m["ch"])
        if m["kf"]:
            facts_row(c, ctx, x0, y1 - m["sb"], x1 - x0, m["sb"], m["kf"])


# ============================================================================================ 11 rail
class Rail(Design):
    """The four alert levels as a rail down the left edge (this one lit); the name and regions across the top;
    the message beside the rail, with a key number tile at the top right."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        sf = ctx.fam["small"]
        rf = sf if sf.width("STATEMENT") + 8 <= 64 else tiny()
        rw = max(rf.width(lab) for _, lab in ap.LEVELS) + 8
        rx = x0 + rw + 6
        kf = key_facts(ctx)[:1]
        kw = 120 if kf else 0
        tf, ts = pick(ctx, wa.title_text(ctx), x1 - rx - 30 - kw - 6, big=True, cap=24)
        hh = max(24, tf.h) + 2
        top = max(hh + ctx.fam["key"].h + 8, tile_h(ctx) if kf else 0) + 3
        return {"rf": rf, "rw": rw, "rx": rx, "kf": kf, "kw": kw, "tf": tf, "ts": ts, "hh": hh, "top": top}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        return (m["rx"], y0 + m["top"], x1 - m["rx"], y1 - y0 - m["top"])

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        rx = m["rx"]
        px.draw(c, px.hazard(ctx.b["hazard"]), rx, y0 + (m["hh"] - 24) // 2, ctx.sev)
        m["tf"].draw(c, rx + 30, y0 + (m["hh"] - m["tf"].h) // 2, ctx.sev, m["ts"])
        chip_row(c, ctx, rx, y0 + m["hh"] + 2, x1 - rx - m["kw"] - 6)
        if m["kf"]:
            fact_tile(c, ctx, x1 - m["kw"], y0, m["kw"], m["top"] - 4, m["kf"][0], (255, 255, 255))

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        ap.severity_ladder(c, ctx, x0, y0, m["rw"], y1 - y0, m["rf"], ctx.t)


# ============================================================================================ 12 deck
class Deck(Design):
    """A strip of instruments under the title — WHERE (region grid), the key numbers, the level scale — and the
    message in section columns below."""
    arrange = "cols"
    fallbacks = ("cols", "flow", "stack")

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = title_strip_metrics(ctx, x1 - x0, with_until=False)
        m["rh"] = max(where_h(), tile_h(ctx))
        return m

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["hh"] + 3 + m["rh"] + 4
        return (x0, t, x1 - x0, y1 - t)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        title_strip(c, ctx, x0, y0, x1 - x0, m)
        y = y0 + m["hh"] + 3
        gw = min(ap.cart_size(tiny())[0], 166) + 6
        where_block(c, ctx, x0, y, gw, m["rh"])
        lvw = 92
        kf = key_facts(ctx)[:2]
        fx = x0 + gw + 4
        fw = x1 - fx - lvw - 4
        if kf:
            facts_row(c, ctx, fx, y, fw, m["rh"], kf)
        else:
            amount_block(c, ctx, fx, y, fw, m["rh"])
        lx = x1 - lvw
        panel(c, ctx, lx, y, lvw, m["rh"], "Alert level")
        ap.severity_ladder(c, ctx, lx + 3, y + LH() + 2, lvw - 6, m["rh"] - LH() - 5, tiny(), ctx.t)


# ============================================================================================ 13 dossier
class Dossier(Design):
    """A case file: a tab with the level on top of a folder; the name and regions on the folder's label; the
    message in two columns of section panels; the key numbers in a margin column."""
    arrange = "flow"
    fallbacks = ("flow", "stack", "cols")
    MW = 112

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        bf = badge_font(ctx)
        tab = bf.h + 6
        tf, ts = pick(ctx, wa.title_text(ctx), x1 - x0 - 40, big=True, cap=20)
        lh = max(24, tf.h) + 2
        return {"bf": bf, "tab": tab, "tf": tf, "ts": ts, "lh": lh, "kf": key_facts(ctx)[:3]}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["tab"] + m["lh"] + 6
        mw = self.MW + 6 if m["kf"] else 0
        return (x0 + 3, t, x1 - x0 - 6 - mw, y1 - t - 3)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        tab = m["tab"]
        bw = badge_w(ctx, m["bf"]) + 16
        c.FillRect(x0, y0, bw, tab, Color(*ctx.pal["panel"]))
        level_badge(c, ctx, x0 + 8, y0 + 1, m["bf"])
        frame(c, x0, y0 + tab, x1 - x0, y1 - y0 - tab, _dim(ctx.sev, 0.5))
        c.FillRect(x0, y0 + tab, x1 - x0, 1, Color(*ctx.sev))
        ly = y0 + tab + 3
        px.draw(c, px.hazard(ctx.b["hazard"]), x0 + 4, ly, ctx.sev)
        m["tf"].draw(c, x0 + 34, ly + (m["lh"] - 2 - m["tf"].h) // 2, ctx.sev, m["ts"])
        sf = ctx.fam["small"]
        ws = where_text(ctx, sf, x1 - x0 - 40 - bw)
        sf.draw(c, x0 + bw + 8, y0 + (tab - sf.h) // 2, ctx.pal["strong"], ws)
        if m["kf"]:
            x, y, w, h = self.text_rect(ctx)
            facts_col(c, ctx, x1 - 3 - self.MW, y, self.MW, h, m["kf"])


STRUCT_IMPL = {"console": Console(), "headline": Headline(), "ticket": Ticket(), "checklist": Checklist(),
               "infographic": Infographic(), "sign": Sign(), "wire": Wire(), "poster": Poster(), "kanban": Kanban(),
               "strip": Strip(), "rail": Rail(), "deck": Deck(), "dossier": Dossier()}
