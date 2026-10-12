"""Alert screen designs: the layouts and graphic concepts the themes (alert_themes.py) are built from.

Each design is a Structure (weather_alert.py): ``boxes`` says where the alert text flows, ``chrome`` draws the
static graphics after the text (cached once the reveal is done), ``dynamic`` draws what moves every frame.
Icons are 1-bit pixel art (pixel_icons.py); every font comes from the theme's family, at native size.
"""

from __future__ import annotations

import math
import time
from datetime import datetime

import numpy as np

from display.gfx import Color
from display.modules import alert_brief
from display.modules import pixel_icons as px
from display.modules import weather_alert as wa

W, H = wa.W, wa.H
_dim = wa._dim
U = wa.U


def kind_word(ctx):
    return ctx.b["kind"].upper()


def status_word(ctx):
    return (ctx.b["status"] or "in effect").upper()


def hz(ctx):
    return px.hazard(ctx.b["hazard"])


def level(ctx):
    return alert_brief.SEVERITY_LEVEL.get(ctx.b["kind"], 1)


def line(c, x, y, w, h, col):
    if w > 0 and h > 0:
        c.FillRect(int(x), int(y), int(w), int(h), Color(*col))


def text_fit(f, s, w):
    """``s`` cut with an ellipsis-free trim at a word boundary so it fits ``w`` px."""
    if f.width(s) <= w:
        return s
    words = s.split()
    while words and f.width(" ".join(words)) > w:
        words.pop()
    return " ".join(words) if words else s[:max(1, w // max(1, f.width("M")))]


def essentials(ctx):
    b = ctx.b
    bits = [wa.title_text(ctx)]
    if b["amount"]:
        bits.append(b["amount"])
    if b["until"]:
        bits.append(("UNTIL " if ctx.upper else "until ") + b["until"])
    bits.append(wa.area_line(ctx, ctx.fam["key"], 10_000))
    return U(ctx, "  ·  ".join(bits))


def fact_cells(ctx):
    """[(label, value, colour role)] — what a fact strip shows."""
    b = ctx.b
    out = []
    if b["until"]:
        out.append(("UNTIL", b["until"], "strong"))
    if b["amount"]:
        out.append(("AMOUNT", b["amount"], "amount"))
    elif b["issued"]:
        out.append(("ISSUED", b["issued"], "text"))
    out.append(("AREA", None, "strong"))
    return out


class Design(wa.Structure):
    lists_cities = False

    def title_font(self, ctx, width=None):
        width = width or self.title_width(ctx)
        tt = wa.title_text(ctx)
        for f in ctx.fam["titles"]:
            if f.width(tt) <= width:
                return f
        return ctx.fam["key"]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 120


# ------------------------------------------------------------------------------------------------- masthead
class Masthead(Design):
    """A broadcast masthead: severity bar with icon, alert name and level; a three-cell fact strip; the text."""

    def bar_h(self, ctx):
        return max(28, ctx.title_f.h + 10)

    def strip_h(self, ctx):
        return ctx.fam["small"].line + ctx.fam["key"].line + 6

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.bar_h(ctx) + self.strip_h(ctx) + 4
        return [(x0 + 2, top, x1 - x0 - 4, y1 - top)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 40 - ctx.fam["key"].width(kind_word(ctx)) - 24

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        bh, f, k, sf = self.bar_h(ctx), ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        line(c, x0, y0, x1 - x0, bh, ctx.sev)
        px.draw(c, hz(ctx), x0 + 6, y0 + (bh - 24) // 2, (0, 0, 0))
        f.draw(c, x0 + 38, y0 + (bh - f.h) // 2, (0, 0, 0), wa.title_text(ctx))
        kw = kind_word(ctx)
        wa.chip(c, x1 - k.width(kw) - 12, y0 + (bh - k.h - 2) // 2, kw, k, (0, 0, 0), ctx.sev, 4)
        y = y0 + bh + 2
        cells = fact_cells(ctx)
        cw = (x1 - x0) // len(cells)
        for i, (lab, val, role) in enumerate(cells):
            cx = x0 + i * cw + 4
            sf.draw(c, cx, y + 1, ctx.pal["dim"], U(ctx, lab))
            v = val if val is not None else wa.area_line(ctx, k, (x1 - cx - 4) if i == len(cells) - 1 else cw - 8)
            wa.draw_home_text(c, ctx, k, cx, y + sf.line + 1, U(ctx, text_fit(k, v, (x1 - cx - 4) if i == len(cells) - 1 else cw - 8)),
                              ctx.pal[role])
            if i:
                line(c, x0 + i * cw - 2, y + 1, 1, self.strip_h(ctx) - 4, _dim(ctx.sev, 0.4))
        line(c, x0, y + self.strip_h(ctx), x1 - x0, 1, _dim(ctx.sev, 0.6))
        wa.chip(c, x1 - sf.width(U(ctx, ctx.header)) - 8, y + 1, U(ctx, ctx.header), sf, _dim(ctx.sev, 0.3), ctx.pal["strong"], 2) \
            if sf.width(U(ctx, ctx.header)) < cw - 40 else None


# ------------------------------------------------------------------------------------------------- ledger
class Ledger(Design):
    """Split card: the essentials stacked in a left column (icon, level, name, amount, expiry, places); text right."""
    LW = 150
    lists_cities = True

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + ctx.fam["small"].line + 6
        return [(x0 + self.LW + 10, top, x1 - x0 - self.LW - 12, y1 - top)]

    def title_width(self, ctx):
        return self.LW - 12

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        lw, k, sf, f = self.LW, ctx.fam["key"], ctx.fam["small"], ctx.title_f
        line(c, x0, y0, lw, y1 - y0, ctx.pal["panel"])
        for yy in range(y0, y1, 3):                                       # dotted column rule
            line(c, x0 + lw + 4, yy, 1, 1, _dim(ctx.sev, 0.7))
        x = x0 + 6
        y = y0 + 5
        px.draw(c, hz(ctx), x, y, ctx.sev)
        k.draw(c, x + 30, y + (24 - k.h) // 2, ctx.sev, kind_word(ctx))
        y += 28
        title = wa.title_text(ctx)
        if f is not k:
            f.draw(c, x, y, ctx.pal["strong"], title)
            y += f.line + 2
        else:
            for ln in wa._wrap_plain(k, title, lw - 12):
                k.draw(c, x, y, ctx.pal["strong"], ln)
                y += k.line
            y += 2
        b = ctx.b
        if b["amount"]:
            for ln in wa._wrap_plain(k, U(ctx, b["amount"]), lw - 12):
                k.draw(c, x, y, ctx.pal["amount"], ln)
                y += k.line
        if b["until"]:
            sf.draw(c, x, y, ctx.pal["dim"], U(ctx, "UNTIL"))
            y += sf.line
            for ln in wa._wrap_plain(k, U(ctx, b["until"]), lw - 12):
                k.draw(c, x, y, ctx.pal["strong"], ln)
                y += k.line
        line(c, x, y + 2, lw - 12, 1, _dim(ctx.sev, 0.5))
        wa.city_list(c, ctx, x, y + 6, lw - 10, y1 - 3, sf, fonts=[sf])
        rx = x0 + lw + 10
        wa.chip(c, rx, y0 + 1, U(ctx, ctx.header), sf, _dim(ctx.sev, 0.35), ctx.pal["strong"], 3)


# ------------------------------------------------------------------------------------------------- ticker tape
class Tape(Design):
    """A live crawl band repeating the essentials, the name beneath, two columns of text, the places at the foot."""
    lists_cities = True

    def band_h(self, ctx):
        return ctx.fam["key"].h + 6

    def foot_h(self, ctx):
        return ctx.fam["small"].line + 4

    def head_h(self, ctx):
        return max(24, ctx.title_f.h) + 6

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.band_h(ctx) + self.head_h(ctx) + 2
        bot = y1 - self.foot_h(ctx) - 2
        gap = 14
        cw = (x1 - x0 - 4 - gap) // 2
        return [(x0 + 2, top, cw, bot - top), (x0 + 2 + cw + gap, top, cw, bot - top)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        y = y0 + self.band_h(ctx) + 2
        hh = max(24, f.h)
        px.draw(c, hz(ctx), x0 + 2, y + (hh - 24) // 2, ctx.sev)
        f.draw(c, x0 + 32, y + (hh - f.h) // 2, ctx.sev, wa.title_text(ctx))
        st = status_word(ctx)
        wa.chip(c, x1 - k.width(st) - 8, y + (hh - k.h - 2) // 2, st, k, ctx.sev, (0, 0, 0))
        b = self.boxes(ctx)
        line(c, b[1][0] - 7, b[1][1], 1, b[1][3], _dim(ctx.sev, 0.3))
        fy = y1 - self.foot_h(ctx)
        line(c, x0, fy, x1 - x0, self.foot_h(ctx), ctx.pal["panel"])
        px.draw(c, "pin", x0 + 3, fy + (self.foot_h(ctx) - 8) // 2, ctx.pal["label"], 8)
        wa.draw_home_text(c, ctx, sf, x0 + 14, fy + 2, wa.area_line(ctx, sf, x1 - x0 - 20), ctx.pal["strong"])

    def dynamic(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        bh = self.band_h(ctx)
        line(c, x0, y0, x1 - x0, bh, ctx.sev)
        msg = essentials(ctx) + "        "
        mw = max(1, k.width(msg))
        xx = x0 - int(ctx.t * 38) % mw
        sub = c.buf[y0:y0 + bh, :x0].copy(), c.buf[y0:y0 + bh, x1:].copy()
        while xx < x1:
            k.draw(c, xx, y0 + 3, (0, 0, 0), msg)
            xx += mw
        c.buf[y0:y0 + bh, :x0], c.buf[y0:y0 + bh, x1:] = sub
        super().dynamic(c, ctx, seg, p)


# ------------------------------------------------------------------------------------------------- signboard
class Signboard(Design):
    """An overhead variable-message sign: a bolted housing with three centred lines, the full text below."""

    def sign_h(self, ctx):
        return ctx.title_f.h + 2 * ctx.fam["key"].line + 18

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.sign_h(ctx) + 8
        return [(x0 + 6, top, x1 - x0 - 12, y1 - top)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 70

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        sh = self.sign_h(ctx)
        housing = (58, 60, 66)
        line(c, x0, y0, x1 - x0, sh, housing)
        line(c, x0 + 3, y0 + 3, x1 - x0 - 6, sh - 6, (0, 0, 0))
        for bx in (x0 + 1, x1 - 3):                                       # bolts
            for by in (y0 + 1, y0 + sh - 3):
                line(c, bx, by, 2, 2, (150, 152, 160))
        line(c, (x0 + x1) // 2 - 20, y0 + sh, 4, 6, housing)              # gantry posts
        line(c, (x0 + x1) // 2 + 16, y0 + sh, 4, 6, housing)
        f, k = ctx.title_f, ctx.fam["key"]
        cx = (x0 + x1) // 2
        y = y0 + 6
        tt = wa.title_text(ctx)
        f.draw(c, cx - f.width(tt) // 2, y, ctx.pal["strong"], tt)
        px.draw(c, hz(ctx), x0 + 10, y0 + (sh - 24) // 2, ctx.sev)
        px.draw(c, hz(ctx), x1 - 34, y0 + (sh - 24) // 2, ctx.sev)
        y += f.line + 2
        b = ctx.b
        l2 = U(ctx, " · ".join(v for v in (b["amount"], ("UNTIL " if ctx.upper else "until ") + b["until"] if b["until"] else "") if v)
                  or status_word(ctx))
        l2 = text_fit(k, l2, x1 - x0 - 80)
        k.draw(c, cx - k.width(l2) // 2, y, ctx.pal["amount"], l2)
        y += k.line
        l3 = wa.area_line(ctx, k, x1 - x0 - 80)
        wa.draw_home_text(c, ctx, k, cx - k.width(l3) // 2, y, l3, ctx.pal["text"])

    def dynamic(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        if int(ctx.t * 1.6) % 2:                                          # beacons flash in turn
            sh = self.sign_h(ctx)
            line(c, x0 + 10, y0 + sh - 6, 24, 2, ctx.sev)
        else:
            sh = self.sign_h(ctx)
            line(c, x1 - 34, y0 + sh - 6, 24, 2, ctx.sev)
        super().dynamic(c, ctx, seg, p)


# ------------------------------------------------------------------------------------------------- dossier
class Dossier(Design):
    """A case file: name on a folder tab, a severity stamp, punched margin and the text as the report."""

    def head_h(self, ctx):
        return max(ctx.title_f.h + 8, 30)

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.head_h(ctx) + ctx.fam["small"].line + 6
        return [(x0 + 26, top, x1 - x0 - 30, y1 - top)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 150

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        hh = self.head_h(ctx)
        tt = wa.title_text(ctx)
        tw = f.width(tt) + 44
        line(c, x0, y0, tw, hh, ctx.pal["panel"])                          # the tab
        line(c, x0 + tw, y0 + 6, 6, hh - 6, ctx.pal["panel"])
        px.draw(c, hz(ctx), x0 + 6, y0 + (hh - 24) // 2, ctx.sev)
        f.draw(c, x0 + 36, y0 + (hh - f.h) // 2, ctx.pal["strong"], tt)
        line(c, x0, y0 + hh, x1 - x0, 2, ctx.pal["panel"])
        # stamp
        kw, st = kind_word(ctx), status_word(ctx)
        sw = max(k.width(kw), sf.width(st)) + 14
        sx, sy = x1 - sw - 4, y0 + 2
        for i in range(2):
            c.FillRect(sx + i, sy + i, sw - 2 * i, 1, Color(*ctx.sev))
            c.FillRect(sx + i, sy + hh - 4 - i, sw - 2 * i, 1, Color(*ctx.sev))
            c.FillRect(sx + i, sy + i, 1, hh - 4 - 2 * i, Color(*ctx.sev))
            c.FillRect(sx + sw - 1 - i, sy + i, 1, hh - 4 - 2 * i, Color(*ctx.sev))
        k.draw(c, sx + (sw - k.width(kw)) // 2, sy + 3, ctx.sev, kw)
        sf.draw(c, sx + (sw - sf.width(st)) // 2, sy + 4 + k.line, ctx.sev, st)
        y = y0 + hh + 4
        wa.fact_line(c, ctx, x0 + 26, y, sf, x1 - x0 - 30)
        # punched margin
        line(c, x0 + 20, y0 + hh + 2, 1, y1 - y0 - hh - 2, _dim(ctx.sev, 0.55))
        for frac in (0.3, 0.6, 0.9):
            hy = int(y0 + hh + (y1 - y0 - hh) * frac) - 3
            line(c, x0 + 6, hy, 6, 6, ctx.pal["panel"])
            line(c, x0 + 7, hy + 1, 4, 4, (0, 0, 0))


# ------------------------------------------------------------------------------------------------- console
class Console(Design):
    """A control console: a column of severity lamps (the alert's level lit and blinking), the icon, then the
    message in a bezelled screen."""
    LW = 92
    LEVELS = [("WARNING", "warning"), ("WATCH", "watch"), ("ADVISORY", "advisory"), ("STATEMENT", "statement")]

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(24, ctx.title_f.h) + ctx.fam["small"].line + 10
        return [(x0 + self.LW + 10, top, x1 - x0 - self.LW - 14, y1 - top - 3)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - self.LW - 100

    def lamp_rows(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        sf = ctx.fam["small"]
        rh = max(12, sf.line + 3)
        return [(y0 + 4 + i * rh, lab, kind) for i, (lab, kind) in enumerate(self.LEVELS)], rh

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        lw = self.LW
        line(c, x0, y0, lw, y1 - y0, ctx.pal["panel"])
        line(c, x0 + lw, y0, 1, y1 - y0, (90, 92, 100))
        rows, rh = self.lamp_rows(ctx)
        from display.modules.alert_themes import SEVERITY
        for y, lab, kind in rows:
            on = kind == ctx.b["kind"]
            col = SEVERITY[kind]
            line(c, x0 + 6, y + 1, 9, 9, _dim(col, 1.0 if on else 0.18))
            sf.draw(c, x0 + 20, y + (rh - sf.h) // 2 - 1, ctx.pal["strong"] if on else ctx.pal["dim"], lab)
        iy = rows[-1][0] + rh + 8
        px.draw(c, hz(ctx), x0 + (lw - 24) // 2, iy, ctx.pal["icon"])
        st = status_word(ctx)
        sf.draw(c, x0 + (lw - sf.width(st)) // 2, iy + 28, ctx.sev, st)
        x = x0 + lw + 10
        hh = max(24, f.h)
        f.draw(c, x, y0 + (hh - f.h) // 2, ctx.sev, wa.title_text(ctx))
        wa.fact_line(c, ctx, x, y0 + hh + 3, sf, x1 - x - 4)
        bx = self.boxes(ctx)[0]
        line(c, bx[0] - 4, bx[1] - 3, bx[2] + 8, 1, (90, 92, 100))                 # screen bezel
        line(c, bx[0] - 4, bx[1] - 3, 1, bx[3] + 6, (90, 92, 100))
        line(c, bx[0] - 4, bx[1] + bx[3] + 2, bx[2] + 8, 1, (30, 30, 34))
        line(c, bx[0] + bx[2] + 3, bx[1] - 3, 1, bx[3] + 6, (30, 30, 34))

    def dynamic(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        rows, rh = self.lamp_rows(ctx)
        for y, lab, kind in rows:
            if kind == ctx.b["kind"] and int(ctx.t * 2) % 2:
                line(c, x0 + 6, y + 1, 9, 9, (255, 255, 255))
        super().dynamic(c, ctx, seg, p)


# ------------------------------------------------------------------------------------------------- transit
class Transit(Design):
    """A transit line: the hazard as a coloured line badge, the facts as stops on a route, the text beside it."""
    LW = 140
    lists_cities = True

    def head_h(self, ctx):
        return max(30, ctx.title_f.h + 6)

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.head_h(ctx) + 6
        return [(x0 + self.LW + 8, top, x1 - x0 - self.LW - 10, y1 - top)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 40 - ctx.fam["key"].width(status_word(ctx)) - 30

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        hh = self.head_h(ctx)
        line(c, x0, y0, 30, 30, ctx.sev)                                   # line badge
        px.draw(c, hz(ctx), x0 + 3, y0 + 3, (0, 0, 0))
        f.draw(c, x0 + 38, y0 + (hh - f.h) // 2, ctx.pal["strong"], wa.title_text(ctx))
        st = status_word(ctx)
        sx = x1 - k.width(st) - 4
        line(c, sx - 10, y0 + (hh - 6) // 2, 6, 6, ctx.sev)
        k.draw(c, sx, y0 + (hh - k.h) // 2, ctx.sev, st)
        line(c, x0, y0 + hh + 2, x1 - x0, 1, _dim(ctx.sev, 0.5))
        b = ctx.b
        stops = [("ISSUED", b["issued"], "text")] if b["issued"] else []
        stops += [(kind_word(ctx), b["status"] and status_word(ctx) or "IN EFFECT", "strong")]
        if b["amount"]:
            stops.append(("AMOUNT", b["amount"], "amount"))
        if b["until"]:
            stops.append(("UNTIL", b["until"], "strong"))
        y = y0 + hh + 8
        avail = y1 - y - 4
        step = max(sf.line + k.line + 3, min(avail // max(1, len(stops) + 1), 36))
        ys = []
        for lab, val, role in stops:
            sf.draw(c, x0 + 16, y, ctx.pal["dim"], U(ctx, lab))
            for i, ln in enumerate(wa._wrap_plain(k, U(ctx, val), self.LW - 20)[:2]):
                k.draw(c, x0 + 16, y + sf.line + i * k.line, ctx.pal[role], ln)
            ys.append(y + sf.line + k.h // 2)
            y += step
        area = wa.area_line(ctx, sf, 10_000)
        sf.draw(c, x0 + 16, y, ctx.pal["dim"], U(ctx, "AREA"))
        yy = y + sf.line
        for ln in wa._wrap_plain(sf, area, self.LW - 20):
            if yy + sf.h > y1:
                break
            wa.draw_home_text(c, ctx, sf, x0 + 16, yy, ln, ctx.pal["strong"])
            yy += sf.line
        ys.append(y + sf.line + sf.h // 2)
        line(c, x0 + 5, ys[0], 3, ys[-1] - ys[0], ctx.sev)                  # the route
        for i, sy in enumerate(ys):
            line(c, x0 + 2, sy - 4, 9, 9, ctx.sev)
            line(c, x0 + 4, sy - 2, 5, 5, (0, 0, 0) if i != len(ys) - 1 else ctx.sev)


# ------------------------------------------------------------------------------------------------- flipboard
class Flipboard(Design):
    """A split-flap departures board: each fact spelt out on flap tiles, the text below."""

    def rows(self, ctx):
        b = ctx.b
        rs = [("ALERT", wa.title_text(ctx))]
        if b["until"]:
            rs.append(("UNTIL", b["until"]))
        if b["amount"]:
            rs.append(("AMOUNT", b["amount"]))
        rs.append(("LEVEL", f"{kind_word(ctx)} · {status_word(ctx)}"))
        return rs

    def cell(self, ctx):
        k = ctx.fam["key"]
        return k.width("W") + 3, k.h + 4

    def board_h(self, ctx):
        cw, ch = self.cell(ctx)
        return len(self.rows(ctx)) * (ch + 2) + 4

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.board_h(ctx) + 6
        return [(x0 + 4, top, x1 - x0 - 8, y1 - top)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        cw, ch = self.cell(ctx)
        lab_w = max(sf.width(lab) for lab, _ in self.rows(ctx)) + 8
        tile = _dim(ctx.pal["panel"], 1.6) if max(ctx.pal["panel"]) < 80 else ctx.pal["panel"]
        y = y0 + 2
        px.draw(c, hz(ctx), x1 - 26, y0 + 2, ctx.sev)
        ncols = (x1 - x0 - lab_w - 34) // cw
        for lab, val in self.rows(ctx):
            sf.draw(c, x0 + lab_w - 4 - sf.width(lab), y + (ch - sf.h) // 2, ctx.pal["dim"], lab)
            txt = val.upper()[:ncols]
            for i in range(ncols):
                tx = x0 + lab_w + i * cw
                line(c, tx, y, cw - 1, ch, tile)
                ch_ = txt[i] if i < len(txt) else " "
                if ch_ != " ":
                    col = ctx.sev if lab == "ALERT" else ctx.pal["amount"] if lab == "AMOUNT" else ctx.pal["strong"]
                    k.draw(c, tx + (cw - 1 - k.width(ch_)) // 2, y + 2, col, ch_)
                line(c, tx, y + ch // 2, cw - 1, 1, (0, 0, 0))                # the flap split
            y += ch + 2
        line(c, x0, y + 1, x1 - x0, 1, _dim(ctx.sev, 0.5))


# ------------------------------------------------------------------------------------------------- poster
class Poster(Design):
    """A centred poster: icon, the alert name, level and facts on the axis; the text in two columns below."""

    def head_h(self, ctx):
        return 26 + ctx.title_f.line + ctx.fam["key"].line + ctx.fam["small"].line + 8

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.head_h(ctx)
        gap = 16
        cw = (x1 - x0 - 24 - gap) // 2
        return [(x0 + 12, top, cw, y1 - top), (x0 + 12 + cw + gap, top, cw, y1 - top)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 20

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        cx = (x0 + x1) // 2
        px.draw(c, hz(ctx), cx - 12, y0 + 1, ctx.sev)
        line(c, x0 + 20, y0 + 12, cx - 20 - x0 - 20, 1, _dim(ctx.sev, 0.6))
        line(c, cx + 20, y0 + 12, x1 - 20 - cx - 20, 1, _dim(ctx.sev, 0.6))
        y = y0 + 26
        tt = wa.title_text(ctx)
        f.draw(c, cx - f.width(tt) // 2, y, ctx.pal["strong"], tt)
        y += f.line
        kw = f"{kind_word(ctx)} · {status_word(ctx)}"
        w = k.width(kw) + 8
        wa.chip(c, cx - w // 2, y, kw, k, ctx.sev, (0, 0, 0), 4)
        y += k.line + 3
        wa.fact_line(c, ctx, x0 + 8, y, sf, x1 - x0 - 16, centre=True)
        b = self.boxes(ctx)
        line(c, cx, b[0][1] + 2, 1, b[0][3] - 4, _dim(ctx.sev, 0.3))


# ------------------------------------------------------------------------------------------------- validity
class Validity(Design):
    """The alert's lifetime as a strip along the foot: issued → now → until, the marker moving in real time."""

    def strip_h(self, ctx):
        return ctx.fam["small"].line * 2 + 10

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(24, ctx.title_f.h) + ctx.fam["small"].line + 8
        return [(x0 + 2, top, x1 - x0 - 4, y1 - top - self.strip_h(ctx) - 4)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        hh = max(24, f.h)
        px.draw(c, hz(ctx), x0 + 2, y0 + (hh - 24) // 2, ctx.sev)
        f.draw(c, x0 + 32, y0 + (hh - f.h) // 2, ctx.sev, wa.title_text(ctx))
        kw = kind_word(ctx)
        wa.chip(c, x1 - k.width(kw) - 8, y0 + (hh - k.h - 2) // 2, kw, k, ctx.sev, (0, 0, 0))
        wa.fact_line(c, ctx, x0 + 2, y0 + hh + 3, sf, x1 - x0 - 4)
        sy = y1 - self.strip_h(ctx)
        line(c, x0, sy - 3, x1 - x0, 1, _dim(ctx.sev, 0.5))
        b = ctx.b
        if b["issued"]:
            sf.draw(c, x0 + 2, sy, ctx.pal["dim"], U(ctx, "ISSUED " + b["issued"]))
        if b["until"]:
            t = U(ctx, "UNTIL " + b["until"])
            sf.draw(c, x1 - 2 - sf.width(t), sy, ctx.pal["strong"], t)
        line(c, x0 + 2, sy + sf.line + 3, x1 - x0 - 4, 3, _dim(ctx.sev, 0.22))

    def dynamic(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        sf = ctx.fam["small"]
        sy = y1 - self.strip_h(ctx) + sf.line + 3
        i0, i1 = ctx.b.get("_issued_ts"), ctx.b.get("_expires_ts")
        now = getattr(ctx, "now", time.time())
        frac = (now - i0) / (i1 - i0) if i0 and i1 and i1 > i0 else 0.5
        frac = max(0.0, min(1.0, frac))
        w = x1 - x0 - 4
        line(c, x0 + 2, sy, int(w * frac), 3, ctx.sev)
        mx = x0 + 2 + int(w * frac)
        line(c, mx - 1, sy - 3, 3, 9, (255, 255, 255) if int(ctx.t * 2) % 2 else ctx.sev)
        lab = U(ctx, "NOW")
        lx = max(x0 + 2, min(x1 - sf.width(lab) - 2, mx - sf.width(lab) // 2))
        sf.draw(c, lx, sy + 6, (255, 255, 255), lab)
        super().dynamic(c, ctx, seg, p)


# ------------------------------------------------------------------------------------------------- quad
class Quad(Design):
    """Four labelled panels: WHAT (summary + details), EXPECT (impacts), DO (advice), WHERE (the places)."""
    GROUPS = [("WHAT", ("lead", "detail"), "warning"), ("EXPECT", ("impact",), "eye"), ("DO", ("advice",), "arrow"),
              ("WHERE", ("regions",), "pin")]

    def head_h(self, ctx):
        return max(24, ctx.title_f.h) + 4

    def meta(self, ctx):
        return U(ctx, " · ".join(v for v in (kind_word(ctx), ctx.b["until"] and "until " + ctx.b["until"], ctx.b["amount"]) if v))

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 40 - ctx.fam["small"].width(self.meta(ctx))

    def panels(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.head_h(ctx) + 2
        gap = 6
        pw, ph = (x1 - x0 - gap) // 2, (y1 - top - gap) // 2
        return [(x0, top, pw, ph), (x0 + pw + gap, top, pw, ph), (x0, top + ph + gap, pw, ph), (x0 + pw + gap, top + ph + gap, pw, ph)]

    def boxes(self, ctx):
        lab = ctx.fam["small"].line + 3
        return [(x + 4, y + lab + 2, w - 8, h - lab - 4) for x, y, w, h in self.panels(ctx)]

    def groups(self, paras):
        """The four panels for these paragraphs. A panel with nothing to show (an alert without advice, say)
        takes the detail paragraphs instead, labelled DETAILS, so no panel sits empty."""
        present = {p["style"] for p in paras}
        groups = [list(g) for g in self.GROUPS]
        if "detail" in present:
            for i, (name, styles, ic) in enumerate(groups):
                if i and not present & set(styles):
                    groups[0][1] = ("lead",)
                    groups[i] = ["DETAILS", ("detail",), "signpost"]
                    break
        return [tuple(g) for g in groups]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        """Each panel fits on its own: an over-full WHAT panel drops its details before the whole screen
        has to give up text (so EXPECT and DO keep theirs)."""
        out, over = [], False
        ctx.quad_groups = self.groups(paras)
        for (name, styles, _), box in zip(ctx.quad_groups, boxes):
            ps = [dict(p) for p in paras if p["style"] in styles]
            if not ps:
                continue
            lines, o = wa.layout(ps, [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap, home=ctx.home_name)
            if o and len(ps) > 1:
                ps = ps[:1]
                lines, o = wa.layout(ps, [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap, home=ctx.home_name)
            base = paras.index(next(p for p in paras if p["style"] in styles))
            for ln in lines:
                ln["para"] += base
            out += lines
            over = over or o
        return out, over

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        hh = max(24, f.h)
        px.draw(c, hz(ctx), x0 + 2, y0 + (hh - 24) // 2, ctx.sev)
        f.draw(c, x0 + 32, y0 + (hh - f.h) // 2, ctx.sev, wa.title_text(ctx))
        meta = self.meta(ctx)
        if x0 + 40 + f.width(wa.title_text(ctx)) + sf.width(meta) < x1:
            sf.draw(c, x1 - sf.width(meta) - 2, y0 + (hh - sf.h) // 2, ctx.pal["strong"], meta)
        for (name, _, ic), (x, y, w, h) in zip(getattr(ctx, "quad_groups", self.GROUPS), self.panels(ctx)):
            lab = sf.line + 3
            line(c, x, y, w, lab, _dim(ctx.sev, 0.3))
            px.draw(c, ic, x + 3, y + (lab - 8) // 2, ctx.pal["strong"], 8)
            sf.draw(c, x + 14, y + 2, ctx.pal["strong"], name)
            line(c, x, y + lab, 1, h - lab, _dim(ctx.sev, 0.3))
            line(c, x + w - 1, y + lab, 1, h - lab, _dim(ctx.sev, 0.3))
            line(c, x, y + h - 1, w, 1, _dim(ctx.sev, 0.3))


# ------------------------------------------------------------------------------------------------- teletype
class Teletype(Design):
    """An ECCC text bulletin as it comes off the wire: WMO-style header lines, tagged paragraphs, a cursor."""
    CODES = {"rain": 11, "snow": 12, "freezing": 13, "wind": 14, "thunder": 15, "tornado": 16, "flood": 17, "heat": 18,
             "cold": 19, "frost": 20, "fog": 21, "smoke": 22, "general": 23}

    def head_lines(self, ctx):
        b = ctx.b
        ts = b.get("_issued_ts") or time.time()
        stamp = datetime.utcfromtimestamp(ts).strftime("%d%H%M")
        n = self.CODES.get(b["hazard"], 23)
        return [f"WWCN{n:02d} CWVR {stamp}",
                (b["title"] if kind_word(ctx) in b["title"].upper() else f"{b['title']} {kind_word(ctx)}").upper() + " " + status_word(ctx),
                f"ISSUED BY ENVIRONMENT CANADA {('AT ' + b['issued']) if b['issued'] else ''}".upper().strip()]

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        top = y0 + 3 * k.line + 8
        return [(x0 + 2, top, x1 - x0 - 4, y1 - top - 2)]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        return wa.layout(paras, boxes, fonts, ctx.pal, True, prefix="tag", gap=gap, home=ctx.home_name)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        for i, ln in enumerate(self.head_lines(ctx)):
            k.draw(c, x0 + 2, y0 + i * k.line, ctx.sev if i == 1 else ctx.pal["dim"] if i == 0 else ctx.pal["text"],
                   text_fit(k, ln, x1 - x0 - 34))
        px.draw(c, hz(ctx), x1 - 26, y0 + 2, ctx.sev)
        y = y0 + 3 * k.line + 3
        for xx in range(x0 + 2, x1 - 2, 4):
            line(c, xx, y, 2, 1, _dim(ctx.sev, 0.6))

    def dynamic(self, c, ctx, seg, p):
        if p >= 1.0 and seg["lines"] and int(ctx.t * 2) % 2:
            ln = seg["lines"][-1]
            last = ln["runs"][-1] if ln["runs"] else None
            if last and last[0] != "glyph":
                x = last[0] + last[2].width(last[1]) + 3
                line(c, x, ln["y"], max(4, last[2].width("M") - 1), ln["h"], ctx.pal["strong"])
        super().dynamic(c, ctx, seg, p)


# ------------------------------------------------------------------------------------------------- radar scope
class Scope(Design):
    """A radar scope: range rings and a rotating sweep around the hazard icon; the message beside it."""

    def scope_r(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return min(54, (y1 - y0 - ctx.fam["key"].line - 10) // 2)

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        sw = 2 * self.scope_r(ctx) + 12
        top = y0 + max(24, ctx.title_f.h) + ctx.fam["small"].line + 8
        return [(x0 + sw + 6, top, x1 - x0 - sw - 8, y1 - top)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 2 * self.scope_r(ctx) - 30

    _GRID = {}

    def grid(self, r):
        if r not in self._GRID:
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
            d = np.hypot(xx, yy)
            ang = np.arctan2(yy, xx) % (2 * np.pi)
            rings = np.zeros_like(d, bool)
            for f in (1.0, 0.66, 0.33):
                rings |= np.abs(d - r * f) < 0.6
            rings |= ((np.abs(xx) < 0.5) | (np.abs(yy) < 0.5)) & (d <= r)
            self._GRID[r] = (d, ang, rings)
        return self._GRID[r]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        r = self.scope_r(ctx)
        cx, cy = x0 + 6 + r, y0 + 4 + r
        d, ang, rings = self.grid(r)
        box = c.buf[cy - r:cy + r + 1, cx - r:cx + r + 1]
        box[d <= r] = _dim(ctx.pal["panel"], 0.8)
        box[rings] = _dim(ctx.pal["label"], 0.45)
        kw = kind_word(ctx)
        k.draw(c, cx - k.width(kw) // 2, cy + r + 4, ctx.sev, kw)
        x = cx + r + 12
        hh = max(24, f.h)
        f.draw(c, x, y0 + (hh - f.h) // 2, ctx.sev, wa.title_text(ctx))
        wa.fact_line(c, ctx, x, y0 + hh + 3, sf, x1 - x - 2)

    def dynamic(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        r = self.scope_r(ctx)
        cx, cy = x0 + 6 + r, y0 + 4 + r
        d, ang, rings = self.grid(r)
        sweep = (ctx.t * 1.6) % (2 * np.pi)
        behind = (sweep - ang) % (2 * np.pi)
        trail = (behind < 1.2) & (d <= r)
        box = c.buf[cy - r:cy + r + 1, cx - r:cx + r + 1]
        k = (1 - behind[trail] / 1.2)[:, None]
        box[trail] = np.clip(box[trail] * (1 - k * 0.6) + np.array(ctx.sev) * k * 0.6, 0, 255).astype(np.uint8)
        px.draw(c, hz(ctx), cx - 12, cy - 12, ctx.pal["icon"], plate=(0, 0, 0), pad=2)
        super().dynamic(c, ctx, seg, p)


# ------------------------------------------------------------------------------------------------- stripes
class Stripes(Design):
    """Hazard-tape bands above and below; the name centred between them; the text in the middle."""
    TB, BB = 10, 8

    def head_h(self, ctx):
        return self.TB + max(24, ctx.title_f.h) + ctx.fam["small"].line + 10

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.head_h(ctx)
        return [(x0 + 4, top, x1 - x0 - 8, y1 - top - self.BB - 4)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 80

    def band(self, c, ctx, x, y, w, h, phase=0):
        yy, xx = np.mgrid[0:h, 0:w]
        on = ((xx + yy + phase) // 6) % 2 == 0
        region = c.buf[y:y + h, x:x + w]
        region[on] = ctx.sev
        region[~on] = (12, 12, 12)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        self.band(c, ctx, x0, y0, x1 - x0, self.TB)
        self.band(c, ctx, x0, y1 - self.BB, x1 - x0, self.BB, 3)
        y = y0 + self.TB + 4
        hh = max(24, f.h)
        tt = wa.title_text(ctx)
        tw = f.width(tt) + 32
        tx = (x0 + x1 - tw) // 2
        px.draw(c, hz(ctx), tx, y + (hh - 24) // 2, ctx.sev)
        f.draw(c, tx + 32, y + (hh - f.h) // 2, ctx.pal["strong"], tt)
        kw = kind_word(ctx)
        wa.chip(c, x0 + 2, y + (hh - k.h - 2) // 2, kw, k, ctx.sev, (0, 0, 0)) if tx - x0 > k.width(kw) + 16 else None
        wa.fact_line(c, ctx, x0 + 4, y + hh + 3, sf, x1 - x0 - 8, centre=True)


STRUCT_IMPL = {"masthead": Masthead(), "ledger": Ledger(), "tape": Tape(), "signboard": Signboard(), "dossier": Dossier(),
               "console": Console(), "transit": Transit(), "flipboard": Flipboard(), "poster": Poster(), "validity": Validity(),
               "quad": Quad(), "teletype": Teletype(), "scope": Scope(), "stripes": Stripes()}
