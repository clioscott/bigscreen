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


# ============================================================================================ console
class Console(Design):
    """A fact column — the alert and its level, WHERE (region grid), VALID UNTIL, AMOUNT — beside the message in
    section panels."""
    side = "left"
    CW = 156

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        hh = y1 - y0
        wh, uh = where_h(), when_h(ctx)
        amin = LH() + ctx.fam["key"].h + 8
        room = hh - wh - uh - amin - 12 - 30 - 3
        tf, tl = title_lines(ctx, self.CW - 8, 3, max_h=max(ctx.fam["small"].line, room))
        ah = alert_block_h(ctx, tf, tl)
        return {"tf": tf, "tl": tl, "ah": ah, "wh": wh, "uh": uh}

    def col_x(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x0 if self.side == "left" else x1 - self.CW

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        if self.side == "left":
            return (x0 + self.CW + 6, y0, x1 - x0 - self.CW - 6, y1 - y0)
        return (x0, y0, x1 - x0 - self.CW - 6, y1 - y0)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        x, w, y = self.col_x(ctx), self.CW, y0
        alert_block(c, ctx, x, y, w, m["ah"], m["tf"], m["tl"])
        y += m["ah"] + 4
        where_block(c, ctx, x, y, w, m["wh"])
        y += m["wh"] + 4
        when_block(c, ctx, x, y, w, m["uh"])
        y += m["uh"] + 4
        amount_block(c, ctx, x, y, w, y1 - y)


class ConsoleCols(Console):
    arrange = "cols"


class ConsoleRight(Console):
    side = "right"


class ConsoleRightCols(Console):
    side = "right"
    arrange = "cols"


# ============================================================================================ deck
class Deck(Design):
    """Title strip; a row of fact panels — WHERE, VALID UNTIL, AMOUNT, ALERT LEVEL; the message in section
    columns below."""
    arrange = "cols"

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = title_strip_metrics(ctx, x1 - x0)
        m["rh"] = max(where_h(), when_h(ctx))
        return m

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["hh"] + 3 + m["rh"] + 4
        return (x0, t, x1 - x0, y1 - t)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        title_strip(c, ctx, x0, y0, x1 - x0, dict(m, us=""))
        y = y0 + m["hh"] + 3
        cw = min(ap.cart_size(tiny())[0], 170) + 6
        rest = (x1 - x0 - cw - 12) // 3
        where_block(c, ctx, x0, y, cw, m["rh"])
        when_block(c, ctx, x0 + cw + 4, y, rest, m["rh"])
        amt = bool(ctx.b["amount"])
        x = x0 + cw + 8 + rest
        if amt:
            amount_block(c, ctx, x, y, rest, m["rh"])
            x += rest + 4
            lw = x1 - x
        else:
            lw = x1 - x
        panel(c, ctx, x, y, lw, m["rh"], "Alert level")
        lh = min(m["rh"] - LH() - 4, tiny().h + 7)
        ap.severity_ladder(c, ctx, x + 3, y + LH() + 2 + (m["rh"] - LH() - 4 - lh) // 2, lw - 6, lh, tiny(), ctx.t, vertical=False)


# ============================================================================================ newsroom
class Newsroom(Design):
    """Title strip; the message in section panels on the left, a fact column (WHERE, VALID UNTIL, AMOUNT) on
    the right."""
    RW = 160

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return title_strip_metrics(ctx, x1 - x0, with_until=False)

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["hh"] + 3
        return (x0, t, x1 - x0 - self.RW - 6, y1 - t)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        title_strip(c, ctx, x0, y0, x1 - x0, m)
        x, y = x1 - self.RW, y0 + m["hh"] + 3
        wh, uh = where_h(), when_h(ctx)
        where_block(c, ctx, x, y, self.RW, wh)
        y += wh + 4
        when_block(c, ctx, x, y, self.RW, uh)
        y += uh + 4
        amount_block(c, ctx, x, y, self.RW, y1 - y)


class NewsroomCols(Newsroom):
    arrange = "flow"


# ============================================================================================ board
class Board(Design):
    """A board of cards: the alert, WHERE and WHEN / AMOUNT along the top; the message sections as cards along
    the bottom."""
    arrange = "cols"

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        rh = max(where_h(), 30 + 2 * k.line + 3)
        aw = 150
        tf, tl = title_lines(ctx, aw - 8, 2, max_h=rh - 31)
        return {"rh": rh, "aw": aw, "tf": tf, "tl": tl}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["rh"] + 4
        return (x0, t, x1 - x0, y1 - t)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        rh, aw = m["rh"], m["aw"]
        alert_block(c, ctx, x0, y0, aw, rh, m["tf"], m["tl"])
        cw = min(ap.cart_size(tiny())[0], 150) + 6
        where_block(c, ctx, x0 + aw + 4, y0, cw, rh)
        x = x0 + aw + cw + 8
        w = x1 - x
        if ctx.b["amount"]:
            half = (rh - 4) // 2 + 2
            when_block(c, ctx, x, y0, w, rh - half)
            amount_block(c, ctx, x, y0 + rh - half + 4, w, half - 4)
        else:
            when_block(c, ctx, x, y0, w, rh)


# ============================================================================================ strip
class Strip(Design):
    """The alert's name with the region grid beside it; the message in full-width section panels; a status bar
    along the foot — VALID UNTIL, TIME LEFT, AMOUNT."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        cw, ch = ap.cart_size(tiny())
        cw = min(cw, 200)
        lw = x1 - x0 - cw - 8
        bf = badge_font(ctx)
        top = bf.h + 6                                            # level box + status on the first row
        tf, tl = title_lines(ctx, lw - 30, 2, max_h=max(ch + 8 - top, ctx.fam["small"].line))
        th = max(ch, top + len(tl) * tf.line, 24) + 2
        sb = LH() + ctx.fam["key"].line + 2
        return {"cw": cw, "ch": ch, "lw": lw, "bf": bf, "top": top, "tf": tf, "tl": tl, "th": th, "sb": sb}

    def text_rect(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["th"] + 4
        return (x0, t, x1 - x0, y1 - t - m["sb"] - 4)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        px.draw(c, px.hazard(ctx.b["hazard"]), x0, y0 + (m["th"] - 24) // 2, ctx.sev)
        bw = level_badge(c, ctx, x0 + 30, y0, m["bf"])
        sf.draw(c, x0 + 36 + bw, y0 + (m["bf"].h + 4 - sf.h) // 2, ctx.pal["dim"], fit(sf, status_word(ctx), m["lw"] - 40 - bw))
        for i, ln in enumerate(m["tl"]):
            m["tf"].draw(c, x0 + 30, y0 + m["top"] + i * m["tf"].line, ctx.sev, ln)
        ap.cartogram(c, ctx, x1 - m["cw"], y0 + (m["th"] - m["ch"]) // 2, tiny(), w=m["cw"], h=m["ch"])
        # status bar
        y = y1 - m["sb"]
        b = ctx.b
        cells = [("Valid until", U(ctx, b["until"] or "further notice"), ctx.pal["strong"])]
        r = ap.remaining_text(ctx)
        if r:
            cells.append(("Time left", r, ctx.pal["strong"]))
        cells.append(("Amount", U(ctx, b["amount"]), ctx.pal["amount"]) if b["amount"] else ("Level", kind_word(ctx), ctx.level))
        if b["issued"]:
            cells.append(("Issued", U(ctx, b["issued"]), ctx.pal["text"]))
        cw = (x1 - x0 - 4 * (len(cells) - 1)) // len(cells)
        for i, (lab, val, col) in enumerate(cells):
            x = x0 + i * (cw + 4)
            panel(c, ctx, x, y, cw, m["sb"], lab)
            f, s = pick(ctx, val, cw - 8, cap=k.h)
            f.draw(c, x + 4, y + LH() + 1, col, s)


# ============================================================================================ wire
class Wire(Design):
    """The bulletin as it comes off the wire (tagged WHAT / INFO / EXPECT / DO), with a fact column: the level,
    AREAS (affected only, home in its tint), VALID UNTIL and AMOUNT."""
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
        return wa.layout(paras, [self.text_area(ctx)], fonts, ctx.pal, True, prefix="tag", gap=gap, home=ctx.home_name)

    def facts(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sf, k = ctx.fam["small"], ctx.fam["key"]
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
        ah = LH() + len(labs) * sf.line + 4
        uh = when_h(ctx)
        amt_h = LH() + k.h + 6 if ctx.b["amount"] else 0
        ah = min(ah, y1 - y - uh - amt_h - 8)
        panel(c, ctx, x, y, w, ah, "Areas")
        yy = y + LH() + 2
        for i, (name, code) in enumerate(labs):
            if yy + sf.h > y + ah - 2:
                sf.draw(c, x + 4, yy - sf.line, ctx.pal["dim"], f"+{len(labs) - i} more")
                break
            sf.draw(c, x + 4, yy, ap.HOME_TINT if ap.is_home(ctx, name) else ctx.pal["strong"], code)
            yy += sf.line
        y += ah + 4
        when_block(c, ctx, x, y, w, uh)
        y += uh + 4
        if amt_h:
            amount_block(c, ctx, x, y, w, min(amt_h, y1 - y))

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


STRUCT_IMPL = {"console": Console(), "console_cols": ConsoleCols(), "console_right": ConsoleRight(),
               "console_right_cols": ConsoleRightCols(), "deck": Deck(), "newsroom": Newsroom(), "newsroom_cols": NewsroomCols(),
               "board": Board(), "strip": Strip(), "wire": Wire(), "briefing": Console()}   # "briefing": fallback id
