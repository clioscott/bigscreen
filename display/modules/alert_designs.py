"""Alert screen layouts (third generation). Every layout answers, in this order: what is it and how bad, where
(the wall's region names, a region map or tile cartogram, home marked), until when / how much — then the full
ECCC text, paged in readable type rather than shrunk. Non-home alerts are told apart graphically (dim house,
outlined chips, calmer border), never with a NEARBY label.

Each layout is a Structure (weather_alert.py): ``boxes`` → where the text flows, ``chrome`` → static graphics
(cached once the reveal is done), ``dynamic`` → what moves (map pulses, scenes, crawls, page turns). Theme
options (``ctx.th["opts"]``) switch a layout's variants: map extent, page count, sign colour, panel colour.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from display.gfx import Color
from display.modules import alert_brief
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


def opt(ctx, key, default=None):
    return (ctx.th.get("opts") or {}).get(key, default)


def kind_word(ctx):
    return ctx.b["kind"].upper()


def status_word(ctx):
    return (ctx.b["status"] or "in effect").upper()


def where_text(ctx, f, maxw):
    """Region names ("Richmond + Vancouver"); a single region may use ECCC's full wording; else codes; else as
    many codes as fit."""
    labs = ap.labels(ctx)
    cands = []
    if len(ctx.b.get("regions") or []) == 1:            # one region: ECCC's own wording, if there's room
        cands.append(ctx.b["regions"][0])
    cands += [" + ".join(n for n, _ in labs), " · ".join(c for _, c in labs)]
    for s in cands:
        if s and f.width(U(ctx, s)) <= maxw:
            return U(ctx, s)
    out = ""
    for _, code in labs:
        cand = (out + " · " if out else "") + code
        if f.width(cand) > maxw:
            break
        out = cand
    return out


def fit(f, s, w):
    if f.width(s) <= w:
        return s
    dots = "…" if f.width("…") > 0 else "..."
    while s and f.width(s + dots) > w:
        s = s[:-1]
    return s.rstrip() + dots


def box_rect(c, x, y, w, h, col):
    c.FillRect(x, y, w, 1, Color(*col))
    c.FillRect(x, y + h - 1, w, 1, Color(*col))
    c.FillRect(x, y, 1, h, Color(*col))
    c.FillRect(x + w - 1, y, 1, h, Color(*col))


def pulse(ctx):
    return 0.72 + 0.28 * (0.5 + 0.5 * math.sin(ctx.t * 2.6))


class Design(wa.Structure):
    lists_cities = True          # where is drawn graphically; the regions paragraph would repeat it
    paged = True
    max_pages = 3

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return (x0, y0, x1 - x0, y1 - y0)

    def boxes(self, ctx):
        return [self.text_box(ctx)]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        if not self.paged:
            return super().flow(ctx, paras, boxes, fonts, gap)
        return ap.paged_flow(wa.layout, ctx, paras, boxes[0], fonts, gap, opt(ctx, "pages", self.max_pages))

    def title_font(self, ctx, width=None):
        width = width or self.title_width(ctx)
        tt = wa.title_text(ctx)
        # title faces, then the key face, then the body faces' bold cuts: the whole name always shows
        for f in list(ctx.fam["titles"]) + [ctx.fam["key"]] + [bold for _, bold in ctx.fam["ladder"]]:
            if f.width(tt) <= width:
                return f
        return ctx.fam["ladder"][-1][1]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 60

    def dynamic(self, c, ctx, seg, p):
        shown = seg
        if self.paged:
            page, pages = ap.page_now(seg, ctx)
            shown = dict(seg, lines=[ln for ln in seg["lines"] if ln.get("page", 0) == page])
            bx, by, bw, bh = self.text_box(ctx)
            if page > 0:
                c.FillRect(bx, by, bw, bh, Color(*self.text_bg(ctx)))
                wa.draw_lines(c, [ln for ln in seg["lines"] if ln.get("page", 0) == page])
            if pages > 1 and p >= 1.0:
                ap.page_dots(c, bx + bw - pages * 6, by + bh - 4, page, pages, ctx.sev, _dim(ctx.sev, 0.25))
        self.moving(c, ctx, seg, p)
        super().dynamic(c, ctx, shown, p)

    def text_bg(self, ctx):
        return ctx.pal["bg"]

    def moving(self, c, ctx, seg, p):
        pass

    def header_row(self, c, ctx, x, y, w, col=None):
        """Icon + alert name; returns the row height."""
        f = ctx.title_f
        hh = max(24, f.h)
        px.draw(c, px.hazard(ctx.b["hazard"]), x, y + (hh - 24) // 2, col or ctx.sev)
        f.draw(c, x + 30, y + (hh - f.h) // 2, col or ctx.sev, fit(f, wa.title_text(ctx), w - 30))
        return hh


# ============================================================================================ 1 command
class Command(Design):
    """Map-led: the region map down the left (affected regions pulsing, codes on them, home house); name, region
    chips, expiry and amount on the right; the text paged beneath."""

    def map_dims(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        _, mw, mh = ap.map_size(opt(ctx, "extent", "metro"), opt(ctx, "map_w", 170), y1 - y0)
        return mw, mh

    def right_x(self, ctx):
        return self.inner(ctx)[0] + self.map_dims(ctx)[0] + 8

    def top_h(self, ctx):
        k = ctx.fam["key"]
        return max(24, ctx.title_f.h) + 4 + (k.h + 4) + 4 + max(2 * k.line, 24) + 4

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = self.right_x(ctx)
        top = y0 + self.top_h(ctx)
        return (rx, top, x1 - rx, y1 - top)

    def title_width(self, ctx):
        return self.inner(ctx)[2] - self.right_x(ctx) - 32

    def draw_map(self, c, ctx, k=1.0):
        x0, y0, x1, y1 = self.inner(ctx)
        mw, mh = self.map_dims(ctx)
        ap.region_map(c, ctx, x0, y0 + (y1 - y0 - mh) // 2, mw, y1 - y0, opt(ctx, "extent", "metro"), tiny(), k=k)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        self.draw_map(c, ctx)
        rx = self.right_x(ctx)
        y = y0 + self.header_row(c, ctx, rx, y0, x1 - rx) + 4
        y += ap.chips(c, ctx, rx, y, x1 - rx, k, lines=1) + 4
        b = ctx.b
        if not ap.amount_gauge(c, ctx, rx, y, min(150, (x1 - rx) // 2), 24, k, tiny()):
            sf.draw(c, rx, y, ctx.pal["dim"], kind_word(ctx) + " · " + status_word(ctx))
            ap.home_badge(c, ctx, rx, y + sf.line + 2, sf)
        if b["until"]:
            t = U(ctx, "until " + b["until"])
            k.draw(c, x1 - k.width(t), y, ctx.pal["strong"], t)
            r = ap.remaining_text(ctx)
            if r:
                sf.draw(c, x1 - sf.width(r + " left"), y + k.line, ctx.pal["dim"], r + " left")
        c.FillRect(rx, self.text_box(ctx)[1] - 3, x1 - rx, 1, Color(*_dim(ctx.sev, 0.5)))

    def moving(self, c, ctx, seg, p):
        self.draw_map(c, ctx, pulse(ctx))


# ============================================================================================ 2 headline
class Headline(Design):
    """Location first: the affected regions as the headline, the alert beneath, a tile cartogram at the right,
    a fact strip, then the text (paged, or two columns)."""

    def carto(self, ctx):
        return (100, 56) if opt(ctx, "extent", "metro") == "metro" else (170, 56)

    def top_h(self, ctx):
        return max(ctx.title_f.line + 28, self.carto(ctx)[1] + 2)

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.top_h(ctx) + ctx.fam["small"].line + 10
        if self.paged:
            return [(x0, top, x1 - x0, y1 - top)]
        gap = 14
        cw = (x1 - x0 - gap) // 2
        return [(x0, top, cw, y1 - top), (x0 + cw + gap, top, cw, y1 - top)]

    def text_box(self, ctx):
        return self.boxes(ctx)[0]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - self.carto(ctx)[0] - 12

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        cw, ch = self.carto(ctx)
        ap.cartogram(c, ctx, x1 - cw, y0, cw, ch, tiny(), opt(ctx, "extent", "metro"))
        avail = x1 - x0 - cw - 10
        where = where_text(ctx, f, avail)
        if f.width(where) > avail:
            where = where_text(ctx, k, avail)
            wa.draw_home_text(c, ctx, k, x0, y0 + (f.h - k.h) // 2, where, ctx.pal["strong"])
        else:
            wa.draw_home_text(c, ctx, f, x0, y0, where, ctx.pal["strong"])
        y = y0 + f.line + 2
        px.draw(c, px.hazard(ctx.b["hazard"]), x0, y, ctx.sev)
        k.draw(c, x0 + 30, y + (24 - k.h) // 2, ctx.sev, fit(k, wa.title_text(ctx), avail - 34))
        b = ctx.b
        facts = [(t, v, r) for t, v, r in (("UNTIL", b["until"], "strong"), ("AMOUNT", b["amount"], "amount"),
                                         ("ISSUED", b["issued"], "text")) if v]
        y = y0 + self.top_h(ctx) + 2
        x = x0 + ap.home_badge(c, ctx, x0, y - 1, sf) + 10
        for lab, val, role in facts:
            s = U(ctx, val)
            if x + sf.width(lab) + sf.width(s) + 14 > x1:
                break
            sf.draw(c, x, y + 1, ctx.pal["dim"], lab)
            x += sf.width(lab) + 4
            sf.draw(c, x, y + 1, ctx.pal[role], s)
            x += sf.width(s) + 12
        c.FillRect(x0, y + sf.line + 4, x1 - x0, 1, Color(*_dim(ctx.sev, 0.5)))
        if not self.paged:
            bx = self.boxes(ctx)
            c.FillRect(bx[1][0] - 7, bx[1][1], 1, bx[1][3], Color(*_dim(ctx.sev, 0.3)))

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        cw, ch = self.carto(ctx)
        ap.cartogram(c, ctx, x1 - cw, y0, cw, ch, tiny(), opt(ctx, "extent", "metro"), k=pulse(ctx))


class HeadlineCols(Headline):
    paged = False


# ============================================================================================ 3 dashboard
class Dashboard(Design):
    """Four instrument cards — WHAT, WHERE (mini map), WHEN (countdown ring), HOW MUCH (gauge or severity
    ladder) — then the text, paged, full width."""
    CH = 84

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return (x0, y0 + self.CH + 6, x1 - x0, y1 - y0 - self.CH - 6)

    def cards(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        gap = 4
        tot = x1 - x0 - 3 * gap
        out, x = [], x0
        for wf in (0.30, 0.25, 0.18, 0.27):
            w = int(tot * wf)
            out.append((x, y0, w, self.CH))
            x += w + gap
        return out

    def title_width(self, ctx):
        return self.cards(ctx)[0][2] - 8

    def where_map(self, c, ctx, k=1.0):
        bx, by, bw, bh = self.cards(ctx)[1]
        ext = opt(ctx, "extent", "metro")
        _, mw, mh = ap.map_size(ext, bw - 6, bh - 12)
        ap.region_map(c, ctx, bx + (bw - mw) // 2, by + 10, mw, bh - 12, ext, tiny(), k=k)

    def chrome(self, c, ctx):
        k, sf = ctx.fam["key"], ctx.fam["small"]
        cards = self.cards(ctx)
        (ax, ay, aw, ah), _, (cx_, cy_, cw_, ch_), (dx, dy, dw, dh) = cards
        for (x, y, w, h), lab in zip(cards, ("WHAT", "WHERE", "WHEN", "HOW MUCH")):
            c.FillRect(x, y, w, h, Color(*ctx.pal["panel"]))
            c.FillRect(x, y, w, 1, Color(*ctx.sev))
            tiny().draw(c, x + 3, y + 3, ctx.pal["dim"], lab)
        px.draw(c, px.hazard(ctx.b["hazard"]), ax + 4, ay + 11, ctx.sev)
        k.draw(c, ax + 32, ay + 12, ctx.sev, kind_word(ctx))
        sf.draw(c, ax + 32, ay + 12 + k.line, ctx.pal["dim"], status_word(ctx))
        yy = ay + 40
        for ln in wa._wrap_plain(k, wa.title_text(ctx), aw - 8)[:3]:
            k.draw(c, ax + 4, yy, ctx.pal["strong"], ln)
            yy += k.line
        self.where_map(c, ctx)
        r = min(cw_ // 2 - 6, (ch_ - 24) // 2)
        ap.countdown_ring(c, ctx, cx_ + cw_ // 2, cy_ + 12 + r, r, k, tiny())
        if ctx.b["until"]:
            u = fit(sf, U(ctx, ctx.b["until"]), cw_ - 6)
            sf.draw(c, cx_ + (cw_ - sf.width(u)) // 2, cy_ + ch_ - sf.h - 3, ctx.pal["strong"], u)
        if ap.amount_gauge(c, ctx, dx + 4, dy + 14, dw - 8, 34, k, tiny()):
            ap.severity_ladder(c, ctx, dx + 4, dy + dh - 14, dw - 8, 11, tiny(), ctx.t, vertical=False)
        else:
            ap.severity_ladder(c, ctx, dx + 4, dy + 12, dw - 8, dh - 16, tiny(), ctx.t)

    def moving(self, c, ctx, seg, p):
        self.where_map(c, ctx, pulse(ctx))


# ============================================================================================ 4 storyboard
class Storyboard(Design):
    """NOW / EXPECT / DO columns under a header with the region chips; the validity timeline along the foot."""
    paged = False
    GROUPS = [("NOW", ("lead", "detail"), "warning"), ("EXPECT", ("impact",), "eye"), ("DO", ("advice",), "arrow")]

    def head_h(self, ctx):
        return max(24, ctx.title_f.h) + ctx.fam["key"].h + 10

    def cols(self, ctx, n=None):
        n = n or len(getattr(ctx, "story", self.GROUPS))
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.head_h(ctx)
        foot = ctx.fam["small"].line + 10
        gap = 6
        cw = (x1 - x0 - (n - 1) * gap) // n
        return [(x0 + i * (cw + gap), top, cw, y1 - top - foot) for i in range(n)]

    def boxes(self, ctx, n=None):
        lab = ctx.fam["small"].line + 3
        return [(x + 3, y + lab + 2, w - 6, h - lab - 3) for x, y, w, h in self.cols(ctx, n)]

    def groups(self, paras):
        present = {p["style"] for p in paras}
        g = [list(x) for x in self.GROUPS]
        if "detail" in present:
            for i in (1, 2):
                if not present & set(g[i][1]):
                    g[0][1] = ("lead",)
                    g[i] = ["MORE", ("detail",), "signpost"]
                    break
        return [tuple(x) for x in g]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        out, over = [], False
        present = {p["style"] for p in paras}
        ctx.story = [g for g in self.groups(paras) if present & set(g[1])] or self.GROUPS[:1]   # no empty columns
        for (name, styles, _), box in zip(ctx.story, self.boxes(ctx, len(ctx.story))):
            ps = [dict(p) for p in paras if p["style"] in styles]
            if not ps:
                continue
            lines, o = wa.layout(ps, [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap, home=ctx.home_name)
            if o and len(ps) > 1:
                lines, o = wa.layout(ps[:1], [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap, home=ctx.home_name)
            base = paras.index(next(p for p in paras if p["style"] in styles))
            for ln in lines:
                ln["para"] += base
            out += lines
            over = over or o
        return out, over

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 40

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        hh = self.header_row(c, ctx, x0, y0, x1 - x0)
        bw = sf.width((ctx.home_name or "").upper()) + 34
        ap.chips(c, ctx, x0, y0 + hh + 3, x1 - x0 - bw - 8, k, lines=1)
        ap.home_badge(c, ctx, x1 - bw, y0 + hh + 3, sf)
        for (name, _, ic), (x, y, w, h) in zip(getattr(ctx, "story", self.GROUPS), self.cols(ctx)):
            lab = sf.line + 3
            c.FillRect(x, y, w, lab, Color(*_dim(ctx.sev, 0.3)))
            px.draw(c, ic, x + 3, y + (lab - 8) // 2, ctx.pal["strong"], 8)
            sf.draw(c, x + 14, y + 2, ctx.pal["strong"], name)
            box_rect(c, x, y, w, h, _dim(ctx.sev, 0.3))
        ap.timeline(c, ctx, x0, y1 - sf.line - 8, x1 - x0, sf)


# ============================================================================================ 5 scene
class Scene(Design):
    """An animated weather scene for the hazard on the left (the amount over it); name, chips, home badge and
    expiry on the right; the text paged beneath them."""
    SW = 150

    def top_h(self, ctx):
        return max(24, ctx.title_f.h) + ctx.fam["key"].h + ctx.fam["small"].h + 18

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x0 + self.SW + 8
        return (rx, y0 + self.top_h(ctx), x1 - rx, y1 - y0 - self.top_h(ctx))

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - self.SW - 40

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        rx = x0 + self.SW + 8
        hh = self.header_row(c, ctx, rx, y0, x1 - rx)
        y = y0 + hh + 3
        y += ap.chips(c, ctx, rx, y, x1 - rx, k, lines=1) + 3
        bw = ap.home_badge(c, ctx, rx, y, sf)
        if ctx.b["until"]:
            sf.draw(c, rx + bw + 8, y + 2, ctx.pal["strong"], U(ctx, "until " + ctx.b["until"]))
        c.FillRect(rx, self.text_box(ctx)[1] - 3, x1 - rx, 1, Color(*_dim(ctx.sev, 0.5)))

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        ap.scene(c, ctx, x0, y0, self.SW, y1 - y0, ctx.t)
        label = (ctx.b["amount"] or "").upper() if ap.parse_amount(ctx) else kind_word(ctx)
        if label:
            label = fit(k, label, self.SW - 8)
            ap.outline_text(c, k, x0 + (self.SW - k.width(label)) // 2, y1 - k.h - 6, label, ctx.pal["amount"])
        ap.outline_text(c, tiny(), x0 + 4, y0 + 4, kind_word(ctx), ctx.sev)


# ============================================================================================ 6 broadcast
class Broadcast(Design):
    """TV weather: a big labelled region map with a hazard bug in its corner, the name, chips and summary beside
    it, and the rest of the message crawling along the foot."""
    paged = False

    def crawl_h(self, ctx):
        return ctx.fam["key"].h + 6

    def map_dims(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        _, mw, mh = ap.map_size(opt(ctx, "extent", "metro"), 210, y1 - y0 - self.crawl_h(ctx) - 2)
        return mw, mh

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x0 + self.map_dims(ctx)[0] + 8
        top = y0 + max(24, ctx.title_f.h) + ctx.fam["key"].h + 12
        return [(rx, top, x1 - rx, y1 - self.crawl_h(ctx) - 3 - top)]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        lead = [p for p in paras if p["style"] in ("lead", "advice")] or paras[:1]
        ctx.crawl_paras = [p for p in paras if p not in lead and p["style"] != "regions"]
        return wa.layout(lead, boxes, fonts, ctx.pal, ctx.upper, gap=gap, home=ctx.home_name)

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - self.map_dims(ctx)[0] - 12

    def bug(self, c, ctx):
        x0, y0 = self.inner(ctx)[:2]
        y0 += self.map_dims(ctx)[1] - 26              # bottom-left corner of the map, clear of the north labels
        k = ctx.fam["key"]
        word = kind_word(ctx)
        c.FillRect(x0, y0, k.width(word) + 34, 26, Color(*ctx.sev))
        px.draw(c, px.hazard(ctx.b["hazard"]), x0 + 2, y0 + 1, (0, 0, 0))
        k.draw(c, x0 + 29, y0 + (26 - k.h) // 2, (0, 0, 0), word)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        mw, mh = self.map_dims(ctx)
        ap.region_map(c, ctx, x0, y0, mw, mh, opt(ctx, "extent", "metro"), tiny())
        self.bug(c, ctx)
        rx = x0 + mw + 8
        f = ctx.title_f
        hh = max(24, f.h)
        f.draw(c, rx, y0 + (hh - f.h) // 2, ctx.pal["strong"], fit(f, wa.title_text(ctx), x1 - rx))
        ap.chips(c, ctx, rx, y0 + hh + 4, x1 - rx, k, lines=1)

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        mw, mh = self.map_dims(ctx)
        ap.region_map(c, ctx, x0, y0, mw, mh, opt(ctx, "extent", "metro"), tiny(), k=pulse(ctx))
        self.bug(c, ctx)
        ch = self.crawl_h(ctx)
        cy = y1 - ch
        c.FillRect(x0, cy, x1 - x0, ch, Color(*ctx.pal["panel"]))
        c.FillRect(x0, cy, x1 - x0, 1, Color(*ctx.sev))
        b = ctx.b
        bits = [U(ctx, p["text"]) for p in getattr(ctx, "crawl_paras", [])]
        if b["until"]:
            bits.insert(0, U(ctx, "In effect until " + b["until"]))
        if b["amount"]:
            bits.insert(1, U(ctx, b["amount"]))
        msg = "   ·   ".join(bits) + "         "
        mw_ = max(1, k.width(msg))
        xx = x0 - int(ctx.t * 34) % mw_
        keep_l, keep_r = c.buf[cy:cy + ch, :x0].copy(), c.buf[cy:cy + ch, x1:].copy()
        while xx < x1:
            k.draw(c, xx, cy + 3, ctx.pal["text"], msg)
            xx += mw_
        c.buf[cy:cy + ch, :x0], c.buf[cy:cy + ch, x1:] = keep_l, keep_r


# ============================================================================================ 7 ladder
class Ladder(Design):
    """A severity ladder (statement → warning, this alert's step lit) and the tile cartogram in a left rail;
    name, chips and facts at the top right; the text paged below."""
    RW = 104

    def top_h(self, ctx):
        return max(24, ctx.title_f.h) + ctx.fam["key"].h + ctx.fam["small"].line + 14

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x0 + self.RW + 8
        return (rx, y0 + self.top_h(ctx), x1 - rx, y1 - y0 - self.top_h(ctx))

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - self.RW - 44

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        c.FillRect(x0, y0, self.RW, y1 - y0, Color(*ctx.pal["panel"]))
        rx = x0 + self.RW + 8
        hh = self.header_row(c, ctx, rx, y0, x1 - rx)
        y = y0 + hh + 3
        ap.chips(c, ctx, rx, y, x1 - rx, k, lines=1)
        wa.fact_line(c, ctx, rx, y + k.h + 8, sf, x1 - rx)

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        c.FillRect(x0, y0, self.RW, y1 - y0, Color(*ctx.pal["panel"]))
        ap.severity_ladder(c, ctx, x0 + 4, y0 + 4, self.RW - 8, y1 - y0 - 74, ctx.fam["small"], ctx.t)
        ap.cartogram(c, ctx, x0 + 4, y1 - 64, self.RW - 8, 60, tiny(), "metro", k=pulse(ctx))


# ============================================================================================ 8 countdown
class Countdown(Design):
    """Time-led: a countdown ring of the alert's remaining validity beside the name and regions; the text paged
    in the middle; the issued → now → until timeline along the foot."""

    def top_h(self, ctx):
        return 58

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.top_h(ctx) + 4
        return (x0, top, x1 - x0, y1 - top - ctx.fam["small"].line - 12)

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 100

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        ap.countdown_ring(c, ctx, x0 + 27, y0 + 27, 26, k, tiny())
        rx = x0 + 62
        hh = self.header_row(c, ctx, rx, y0, x1 - rx)
        bw = sf.width((ctx.home_name or "").upper()) + 34
        ap.chips(c, ctx, rx, y0 + hh + 4, x1 - rx - bw - 8, k, lines=1)
        ap.home_badge(c, ctx, x1 - bw, y0 + hh + 4, sf)
        c.FillRect(x0, y0 + self.top_h(ctx) + 1, x1 - x0, 1, Color(*_dim(ctx.sev, 0.5)))

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        sf = ctx.fam["small"]
        c.FillRect(x0, y1 - sf.line - 9, x1 - x0, sf.line + 9, Color(*ctx.pal["bg"]))
        ap.timeline(c, ctx, x0, y1 - sf.line - 8, x1 - x0, sf, getattr(ctx, "now", None))


# ============================================================================================ 9 gazette
class Gazette(Design):
    """Newspaper: masthead, the alert as the headline, "For Richmond + Vancouver" as the deck, two columns of
    text with the region map inset at the top of the right column."""
    paged = False

    def mast_h(self, ctx):
        return ctx.fam["small"].line + 4

    def head_h(self, ctx):
        return self.mast_h(ctx) + ctx.title_f.line + ctx.fam["key"].line + 6

    def inset(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        cw = (x1 - x0 - 14) // 2
        _, mw, mh = ap.map_size(opt(ctx, "extent", "lm"), cw, 70)
        return (x0 + cw + 14 + (cw - mw) // 2, y0 + self.head_h(ctx) + 2, mw, mh)

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.head_h(ctx) + 2
        cw = (x1 - x0 - 14) // 2
        ix, iy, iw, ih = self.inset(ctx)
        y2 = iy + ih + ctx.fam["small"].line + 4
        return [(x0, top, cw, y1 - top), (x0 + cw + 14, y2, cw, y1 - y2)]

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        sf.draw(c, x0, y0, ctx.pal["dim"], U(ctx, ctx.th.get("header") or "WEATHER"))
        ed = U(ctx, f"{kind_word(ctx)} · ISSUED {ctx.b['issued']}" if ctx.b["issued"] else f"{kind_word(ctx)} · {status_word(ctx)}")
        sf.draw(c, x1 - sf.width(ed), y0, ctx.pal["dim"], ed)
        c.FillRect(x0, y0 + sf.line + 1, x1 - x0, 1, Color(*ctx.pal["dim"]))
        y = y0 + self.mast_h(ctx)
        tt = wa.title_text(ctx)
        f.draw(c, x0 + (x1 - x0 - f.width(tt)) // 2, y, ctx.sev, tt)
        y += f.line
        until = U(ctx, " · until " + ctx.b["until"]) if ctx.b["until"] else ""
        deck = U(ctx, "For ") + where_text(ctx, k, x1 - x0 - 30 - k.width(until)) + until
        deck = fit(k, deck, x1 - x0)
        wa.draw_home_text(c, ctx, k, x0 + (x1 - x0 - k.width(deck)) // 2, y + 1, deck, ctx.pal["strong"])
        c.FillRect(x0, y + k.line + 3, x1 - x0, 1, Color(*_dim(ctx.sev, 0.6)))
        ix, iy, iw, ih = self.inset(ctx)
        ap.region_map(c, ctx, ix, iy, iw, ih, opt(ctx, "extent", "lm"), tiny())
        cap = U(ctx, "Area affected")
        sf.draw(c, ix + (iw - sf.width(cap)) // 2, iy + ih + 1, ctx.pal["dim"], cap)
        bx = self.boxes(ctx)
        c.FillRect(bx[1][0] - 7, bx[0][1], 1, y1 - bx[0][1], Color(*_dim(ctx.pal["dim"], 0.6)))


# ============================================================================================ 10 sign
class Sign(Design):
    """Overhead variable-message sign: three lit lines (what · where · until) between flashing beacons; the
    region tiles and the text below."""

    def sign_h(self, ctx):
        return ctx.title_f.line + 2 * ctx.fam["key"].line + 12

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.sign_h(ctx) + 8
        return (x0 + 132, top, x1 - x0 - 132, y1 - top)

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 80

    def tiles(self, c, ctx, k=1.0):
        x0, y0, x1, y1 = self.inner(ctx)
        ty = y0 + self.sign_h(ctx) + 8
        ap.cartogram(c, ctx, x0, ty, 124, min(66, y1 - ty), tiny(), "metro", k=k)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k = ctx.title_f, ctx.fam["key"]
        sh = self.sign_h(ctx)
        c.FillRect(x0, y0, x1 - x0, sh, Color(58, 60, 66))
        c.FillRect(x0 + 3, y0 + 3, x1 - x0 - 6, sh - 6, Color(0, 0, 0))
        for bx in (x0 + 1, x1 - 3):
            for by in (y0 + 1, y0 + sh - 3):
                c.FillRect(bx, by, 2, 2, Color(150, 152, 160))
        cx = (x0 + x1) // 2
        lit = tuple(opt(ctx, "lit", (255, 176, 0)))
        tt = fit(f, wa.title_text(ctx), x1 - x0 - 70)
        f.draw(c, cx - f.width(tt) // 2, y0 + 5, lit, tt)
        w2 = where_text(ctx, k, x1 - x0 - 70)
        k.draw(c, cx - k.width(w2) // 2, y0 + 5 + f.line, lit, w2)
        b = ctx.b
        l3 = U(ctx, " · ".join(v for v in (("until " + b["until"]) if b["until"] else "", b["amount"]) if v))
        l3 = fit(k, l3, x1 - x0 - 70)
        k.draw(c, cx - k.width(l3) // 2, y0 + 5 + f.line + k.line, _dim(lit, 0.85), l3)
        self.tiles(c, ctx)

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        sh = self.sign_h(ctx)
        on = int(ctx.t * 1.6) % 2
        for i, bx in enumerate((x0 + 8, x1 - 22)):
            c.FillRect(bx, y0 + (sh - 14) // 2, 14, 14, Color(*(ctx.sev if i == on else _dim(ctx.sev, 0.15))))
        self.tiles(c, ctx, pulse(ctx))


# ============================================================================================ 11 scope
class Scope(Design):
    """A radar scope: the region map inside a round scope with a rotating sweep; the message beside it."""
    _G = {}

    def r(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return min(80, (y1 - y0) // 2)

    def top_h(self, ctx):
        return max(24, ctx.title_f.h) + ctx.fam["key"].h + 10

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x0 + 2 * self.r(ctx) + 10
        return (rx, y0 + self.top_h(ctx), x1 - rx, y1 - y0 - self.top_h(ctx))

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 2 * self.r(ctx) - 44

    def grid(self, r):
        if r not in self._G:
            yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
            self._G[r] = (np.hypot(xx, yy), np.arctan2(yy, xx) % (2 * np.pi))
        return self._G[r]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x0 + 2 * self.r(ctx) + 10
        hh = self.header_row(c, ctx, rx, y0, x1 - rx)
        ap.chips(c, ctx, rx, y0 + hh + 3, x1 - rx, ctx.fam["key"], lines=1)

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        r = self.r(ctx)
        cx, cy = x0 + r, (y0 + y1) // 2
        ext = opt(ctx, "extent", "metro")
        _, mw, mh = ap.map_size(ext, 2 * r - 10, 2 * r - 10)
        c.FillRect(cx - r, cy - r, 2 * r + 1, 2 * r + 1, Color(*ctx.pal["bg"]))
        ap.region_map(c, ctx, cx - mw // 2, cy - mh // 2, mw, mh, ext, tiny(), k=pulse(ctx))
        d, ang = self.grid(r)
        box = c.buf[cy - r:cy + r + 1, cx - r:cx + r + 1]
        if box.shape[:2] != d.shape:
            return
        box[d > r] = ctx.pal["bg"]
        for f in (1.0, 0.66, 0.33):
            box[np.abs(d - r * f) < 0.6] = _dim(ctx.pal["dim"], 0.8)
        behind = ((ctx.t * 1.4) % (2 * np.pi) - ang) % (2 * np.pi)
        trail = (behind < 1.0) & (d <= r)
        kk = (1 - behind[trail])[:, None] * 0.5
        box[trail] = np.clip(box[trail] * (1 - kk) + np.array(ctx.sev) * kk, 0, 255).astype(np.uint8)


# ============================================================================================ 12 split
class Split(Design):
    """Half and half: a solid colour panel on the left carrying icon, name, regions and expiry in dark type;
    the text paged on the right."""
    LW = 168

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x0 + self.LW + 8
        return (rx, y0, x1 - rx, y1 - y0)

    def title_font(self, ctx, width=None):
        """Largest title face whose wrapped name takes at most two lines of the panel."""
        tt = wa.title_text(ctx)
        for f in ctx.fam["titles"]:
            lines = wa._wrap_plain(f, tt, self.LW - 12)
            if len(lines) <= 2 and all(f.width(ln) <= self.LW - 12 for ln in lines):
                return f
        return ctx.fam["key"]

    def panel_col(self, ctx):
        return ctx.sev

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f, k, sf = ctx.title_f, ctx.fam["key"], ctx.fam["small"]
        col = self.panel_col(ctx)
        ink = (0, 0, 0) if sum(col) > 330 else (255, 255, 255)
        c.FillRect(x0, y0, self.LW, y1 - y0, Color(*col))
        x, y = x0 + 6, y0 + 5
        px.draw(c, px.hazard(ctx.b["hazard"]), x, y, ink)
        k.draw(c, x + 30, y + (24 - k.h) // 2, ink, kind_word(ctx))
        y += 28
        for ln in wa._wrap_plain(f, wa.title_text(ctx), self.LW - 12)[:2]:
            f.draw(c, x, y, ink, ln)
            y += f.line
        y += 4
        labs = ap.labels(ctx)
        for name, code in labs[:4]:
            if name == ctx.home_name or (name == "Metro Vancouver" and ctx.home):
                ap.draw_house(c, x + 4, y + k.h // 2, True, ctx)
            k.draw(c, x + 11, y, ink, fit(k, U(ctx, name), self.LW - 20))
            y += k.line
        if len(labs) > 4:
            sf.draw(c, x + 11, y, ink, f"+{len(labs) - 4} more")
        if ctx.b["until"]:
            sf.draw(c, x, y1 - sf.h - 5, ink, fit(sf, U(ctx, "until " + ctx.b["until"]), self.LW - 12))


class SplitHazard(Split):
    """Split with the panel in the hazard's own colour (rain blue, snow white…)."""

    def panel_col(self, ctx):
        return ap.hazard_colour(ctx)


# ============================================================================================ 13 stack
class Stack(Design):
    """A headline card, then three fact cards — WHERE (chips), WHEN (until + time left), HOW MUCH (gauge) —
    then the text."""

    def head_h(self, ctx):
        return max(24, ctx.title_f.h) + 6

    def facts_h(self, ctx):
        return 40

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.head_h(ctx) + self.facts_h(ctx) + 8
        return (x0, top, x1 - x0, y1 - top)

    def title_width(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return x1 - x0 - 140

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        hh = self.head_h(ctx)
        c.FillRect(x0, y0, x1 - x0, hh, Color(*ctx.pal["panel"]))
        c.FillRect(x0, y0, 3, hh, Color(*ctx.sev))
        bw = sf.width((ctx.home_name or "").upper()) + 34
        self.header_row(c, ctx, x0 + 6, y0 + 3, x1 - x0 - bw - 16)
        ap.home_badge(c, ctx, x1 - bw - 4, y0 + (hh - sf.h - 4) // 2, sf)
        y = y0 + hh + 4
        gap = 4
        cw = (x1 - x0 - 2 * gap) // 3
        for i, lab in enumerate(("WHERE", "WHEN", "HOW MUCH")):
            bx = x0 + i * (cw + gap)
            box_rect(c, bx, y, cw, self.facts_h(ctx), _dim(ctx.sev, 0.4))
            tiny().draw(c, bx + 3, y + 2, ctx.pal["dim"], lab)
        ap.chips(c, ctx, x0 + 3, y + 10, cw - 6, sf, lines=2)
        b = ctx.b
        wx = x0 + cw + gap + 4
        if b["until"]:
            k.draw(c, wx, y + 10, ctx.pal["strong"], fit(k, U(ctx, b["until"]), cw - 8))
        r = ap.remaining_text(ctx)
        if r:
            sf.draw(c, wx, y + 12 + k.line, ctx.pal["dim"], f"{r} left")
        hx = x0 + 2 * (cw + gap) + 4
        if not ap.amount_gauge(c, ctx, hx, y + 10, cw - 8, 28, sf, tiny()):
            ap.severity_ladder(c, ctx, hx, y + 12, cw - 8, 22, tiny(), ctx.t, vertical=False)


# ============================================================================================ 14 teletype
class Teletype(Design):
    """The bulletin as it comes off the wire, beside a region table: each region's code, AFFECTED or clear."""
    CODES = {"rain": 11, "snow": 12, "freezing": 13, "wind": 14, "thunder": 15, "tornado": 16, "flood": 17, "heat": 18,
             "cold": 19, "frost": 20, "fog": 21, "smoke": 22, "general": 23}
    TW = 118

    def head_lines(self, ctx):
        b = ctx.b
        ts = b.get("_issued_ts") or 0
        stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%d%H%M") if ts else "------"
        n = self.CODES.get(b["hazard"], 23)
        title = b["title"] if kind_word(ctx) in b["title"].upper() else f"{b['title']} {kind_word(ctx)}"
        return [f"WWCN{n:02d} CWVR {stamp}", f"{title} {status_word(ctx)}".upper()]

    def text_box(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + 2 * ctx.fam["key"].line + 8
        return (x0, top, x1 - x0 - self.TW - 8, y1 - top)

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        def lay(ps, bx, fo, pal, upper, gap=3, home=""):
            return wa.layout(ps, bx, fo, pal, True, prefix="tag", gap=gap, home=home)
        return ap.paged_flow(lay, ctx, paras, boxes[0], fonts, gap, opt(ctx, "pages", 3))

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        for i, ln in enumerate(self.head_lines(ctx)):
            k.draw(c, x0, y0 + i * k.line, ctx.pal["dim"] if i == 0 else ctx.sev, fit(k, ln, x1 - x0 - self.TW - 8))
        y = y0 + 2 * k.line + 3
        for xx in range(x0, x1 - self.TW - 8, 4):
            c.FillRect(xx, y, 2, 1, Color(*_dim(ctx.sev, 0.6)))
        tx = x1 - self.TW
        c.FillRect(tx - 5, y0, 1, y1 - y0, Color(*_dim(ctx.sev, 0.4)))
        hit = ap.affected(ctx)
        yy = y0
        sf.draw(c, tx, yy, ctx.pal["dim"], "REGION")
        sf.draw(c, x1 - sf.width("STATUS"), yy, ctx.pal["dim"], "STATUS")
        yy += sf.line + 2
        for name, code in alert_brief.REGIONS:
            if yy + sf.h > y1:
                break
            on = name in hit
            if name == ctx.home_name:
                ap.draw_house(c, tx + 3, yy + sf.h // 2, on, ctx)
            sf.draw(c, tx + 9, yy, ctx.pal["strong"] if on else ctx.pal["dim"], code)
            st = "AFFECTED" if on else "-"
            sf.draw(c, x1 - sf.width(st), yy, ctx.sev if on else ctx.pal["dim"], st)
            yy += sf.line

    def moving(self, c, ctx, seg, p):
        page, _ = ap.page_now(seg, ctx)
        lines = [ln for ln in seg["lines"] if ln.get("page", 0) == page]
        if p >= 1.0 and lines and int(ctx.t * 2) % 2:
            ln = lines[-1]
            runs = [r for r in ln["runs"] if r[0] != "glyph"]
            if runs:
                last = runs[-1]
                x = last[0] + last[2].width(last[1]) + 3
                c.FillRect(x, ln["y"], max(4, last[2].width("M") - 1), ln["h"], Color(*ctx.pal["strong"]))


STRUCT_IMPL = {"command": Command(), "headline": Headline(), "headline_cols": HeadlineCols(),
               "dashboard": Dashboard(), "storyboard": Storyboard(), "scene": Scene(), "broadcast": Broadcast(),
               "ladder": Ladder(), "countdown": Countdown(), "gazette": Gazette(), "sign": Sign(), "scope": Scope(),
               "split": Split(), "split_hazard": SplitHazard(), "stack": Stack(), "teletype": Teletype()}
