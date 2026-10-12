"""Alert screen layouts. Information first: what it is and how bad, where (region chips or the Lower Mainland
tile grid, home in its own tint), until when / how much, then the whole ECCC text — all of it on one screen,
the body in the family's smaller faces, the larger ones kept for the name and key facts. Graphics are UI
(cards, tables, scales, chips) that carry information, never decoration or animation for its own sake.

Everything is measured in the theme's own fonts (which the control panel can override): rows take the
height of the font drawn in them, columns the width of what they hold, and every fixed label is fitted — a
smaller face of the family, then shortened — so nothing runs past its box or the screen edge.

Each layout is a Structure (weather_alert.py): ``boxes`` → where the text flows, ``chrome`` → static parts
(cached once the reveal ends), ``dynamic`` → per-frame parts (beacons, the countdown ring, the alert counter).
"""

from __future__ import annotations

import math
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
    """(font, text): the largest face in which ``s`` fits ``w``; else the smallest face, shortened."""
    fs = faces(ctx, big, cap)
    for f in fs:
        if f.width(s) <= w:
            return f, s
    return fs[-1], fit(fs[-1], s, w)


def pick_list(ctx, items, w, cap=None, sep=" · "):
    """(font, text) for as many of ``items`` (most important first) as fit ``w`` in one line."""
    items = [i for i in items if i]
    for n in range(len(items), 0, -1):
        s = sep.join(items[:n])
        for f in faces(ctx, cap=cap):
            if f.width(s) <= w:
                return f, s
    return pick(ctx, items[0] if items else "", w, cap=cap)


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
    """The affected regions on one line, in the fullest wording that fits: ECCC's text for a single region,
    "Metro Vancouver (All Regions)", the wall's names joined with "+", then the codes; else as many codes
    as fit."""
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


def best_where(ctx, w, big=False, cap=None, min_h=0):
    """(font, text): the fullest wording of the affected regions that fits ``w`` on one line in a face at
    least ``min_h`` tall — wording before size, so one region gets ECCC's full text when there's room."""
    fs = [f for f in faces(ctx, big, cap) if f.h >= min_h] or faces(ctx, big, cap)[:1]
    for var in ap.label_variants(ctx):
        s = U(ctx, (" · " if var[0][1] == ap.CODES.get(var[0][0]) else " + ").join(t for _, t in var))
        for f in fs:
            if f.width(s) <= w:
                return f, s
    f = faces(ctx, big, cap)[-1]
    return f, where_text(ctx, f, w)


def region_lines(ctx, f, w, wrap=4):
    """Region lines for column layouts: the fullest wording whose lines fit ``w`` in ``f``. A single region's
    ECCC text may wrap over up to ``wrap`` lines; otherwise one region per line."""
    for var in ap.label_variants(ctx):
        if len(var) == 1 and var[0][1] not in (var[0][0], ap.CODES.get(var[0][0])):
            ls = wa._wrap_plain(f, U(ctx, var[0][1]), w)
            if len(ls) <= wrap and all(f.width(x) <= w for x in ls):
                return [(var[0][0], x) for x in ls]
            continue
        if all(f.width(U(ctx, t)) <= w for _, t in var):
            return [(n, U(ctx, t)) for n, t in var]
    return [(n, U(ctx, t)) for n, t in ap.label_variants(ctx)[-1]]


def fact_items(ctx, issued=False):
    """The key facts as short strings, most important first."""
    b = ctx.b
    out = []
    if b["until"]:
        out.append(U(ctx, "until " + b["until"]))
    if b["amount"]:
        out.append(U(ctx, b["amount"]))
    r = ap.remaining_text(ctx)
    if r:
        out.append(U(ctx, r + " left"))
    if issued and b["issued"]:
        out.append(U(ctx, "issued " + b["issued"]))
    return out


def amount_text(c, ctx, x, y, w, h):
    """The forecast amount as plain text in the largest face that fits the card (wrapping to two lines if
    it must). False if the alert has none."""
    amt = U(ctx, ctx.b["amount"] or "")
    if not amt:
        return False
    for f in faces(ctx, big=True, cap=h):
        ls = wa._wrap_plain(f, amt, w)
        if len(ls) * f.line <= h and all(f.width(x_) <= w for x_ in ls):
            for i, ln in enumerate(ls):
                f.draw(c, x, y + i * f.line, ctx.pal["amount"], ln)
            return True
    f = faces(ctx)[-1]
    f.draw(c, x, y, ctx.pal["amount"], fit(f, amt, w))
    return True


def frame(c, x, y, w, h, col):
    for rx, ry, rw, rh in ((x, y, w, 1), (x, y + h - 1, w, 1), (x, y, 1, h), (x + w - 1, y, 1, h)):
        c.FillRect(rx, ry, rw, rh, Color(*col))


class Design(wa.Structure):
    lists_cities = True          # where is shown by chips / tiles; the regions paragraph would repeat it

    # -- measurements, once per alert -------------------------------------------------------------------
    def m(self, ctx):
        cache = ctx.__dict__.setdefault("_m", {})
        key = type(self).__name__
        if key not in cache:
            cache[key] = self.metrics(ctx)
        return cache[key]

    def metrics(self, ctx):
        return {}

    def foot(self, ctx):
        """Strip under the text for the alert counter (only when several alerts take turns)."""
        return ctx.fam["small"].h + 3 if getattr(ctx, "count", 1) > 1 else 0

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        return (x0, y0, x1 - x0, y1 - y0)

    def text_box(self, ctx):
        bx, by, bw, bh = self.text_area(ctx)
        return (bx, by, bw, bh - self.foot(ctx))

    def boxes(self, ctx):
        return [self.text_box(ctx)]

    def title_font(self, ctx, width=None):
        return self.m(ctx).get("tf") or ctx.fam["key"]

    # -- drawing helpers --------------------------------------------------------------------------------
    def header(self, c, ctx, x, y, w, col=None):
        """Icon + alert name on one line, the name in the largest face that fits. Returns the height."""
        f, s = pick(ctx, wa.title_text(ctx), w - 30, big=True)
        hh = max(24, f.h)
        col = col or ctx.sev
        px.draw(c, px.hazard(ctx.b["hazard"]), x, y + (hh - 24) // 2, col)
        f.draw(c, x + 30, y + (hh - f.h) // 2, col, s)
        return hh

    def header_h(self, ctx, w):
        f, _ = pick(ctx, wa.title_text(ctx), w - 30, big=True)
        return max(24, f.h)

    def moving(self, c, ctx, seg, p):
        pass

    def dynamic(self, c, ctx, seg, p):
        shown = seg
        self.moving(c, ctx, seg, p)
        if p >= 1.0 and ctx.th.get("pulse"):                 # amounts glow every couple of seconds
            k = max(0.0, math.sin(ctx.t * 2.4)) ** 3
            if k > 0.02:
                amt = tuple(ctx.pal["amount"])
                col = tuple(int(a + (255 - a) * k) for a in amt)
                for ln in shown["lines"]:
                    for run in ln["runs"]:
                        if run[0] != "glyph" and tuple(run[3]) == amt:
                            run[2].draw(c, run[0], ln["y"], col, run[1])
        bx, by, bw, bh = self.text_area(ctx)
        sf = ctx.fam["small"]
        x, y = bx + bw, by + bh - sf.h - 1
        if ctx.count > 1:
            s = f"{ctx.index + 1}/{ctx.count}"
            x -= sf.width(s) + 4
            c.FillRect(x - 2, y - 1, sf.width(s) + 4, sf.h + 2, Color(*ctx.sev))
            sf.draw(c, x, y, (0, 0, 0), s)



# ============================================================================================ briefing
class Briefing(Design):
    """Name (wrapped to two lines if it needs) with the key facts under it; the Lower Mainland region grid at
    the top right; the text full width below."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        cw, ch = ap.cart_size(tiny())
        lw = x1 - x0 - cw - 10
        tf, tl = title_lines(ctx, lw - 30, 2)
        k = ctx.fam["key"]
        ff, fs = pick_list(ctx, fact_items(ctx), lw, cap=k.h)
        th = max(24, len(tl) * tf.line)
        top = max(ch, th + 3 + ff.h) + 5
        return {"cw": cw, "ch": ch, "lw": lw, "tf": tf, "tl": tl, "th": th, "ff": ff, "fs": fs, "top": top}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["top"] + 3
        return (x0, t, x1 - x0, y1 - t)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        ap.cartogram(c, ctx, x1 - m["cw"], y0, tiny())
        px.draw(c, px.hazard(ctx.b["hazard"]), x0, y0 + (m["th"] - 24) // 2, ctx.sev)
        for i, ln in enumerate(m["tl"]):
            m["tf"].draw(c, x0 + 30, y0 + i * m["tf"].line + (m["th"] - len(m["tl"]) * m["tf"].line) // 2, ctx.sev, ln)
        m["ff"].draw(c, x0, y0 + m["th"] + 3, ctx.pal["strong"], m["fs"])
        c.FillRect(x0, y0 + m["top"], x1 - x0, 1, Color(*_dim(ctx.sev, 0.5)))


# ============================================================================================ headline
class Headline(Design):
    """Location first: the affected regions as the headline, the alert name under it with the expiry at the
    right, a strip of further facts, then the text (one column, or two)."""
    two_cols = False

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        w = x1 - x0
        k = ctx.fam["key"]
        wf, ws = best_where(ctx, w, big=True, min_h=k.h)
        uf, us = pick(ctx, U(ctx, "until " + ctx.b["until"]) if ctx.b["until"] else "", w // 3, cap=k.h)
        tf, ts = pick(ctx, wa.title_text(ctx), w - 30 - uf.width(us) - 12, cap=max(k.h, 24))
        rest = fact_items(ctx, issued=True)[1:] if ctx.b["until"] else fact_items(ctx, issued=True)
        sf, ss = pick_list(ctx, rest, w, cap=ctx.fam["small"].h)
        row2 = max(24, tf.h)
        top = wf.line + 2 + row2 + 3 + (sf.line + 3 if ss else 0)
        return {"wf": wf, "ws": ws, "uf": uf, "us": us, "tf": tf, "ts": ts, "sf": sf, "ss": ss, "row2": row2, "top": top}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["top"] + 4
        return (x0, t, x1 - x0, y1 - t)

    def boxes(self, ctx):
        bx, by, bw, bh = self.text_box(ctx)
        if not self.two_cols:
            return [(bx, by, bw, bh)]
        cw = (bw - 14) // 2
        return [(bx, by, cw, bh), (bx + cw + 14, by, cw, bh)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        wa.draw_home_text(c, ctx, m["wf"], x0, y0, m["ws"], ctx.pal["strong"])
        y = y0 + m["wf"].line + 2
        px.draw(c, px.hazard(ctx.b["hazard"]), x0, y + (m["row2"] - 24) // 2, ctx.sev)
        m["tf"].draw(c, x0 + 30, y + (m["row2"] - m["tf"].h) // 2, ctx.sev, m["ts"])
        m["uf"].draw(c, x1 - m["uf"].width(m["us"]), y + (m["row2"] - m["uf"].h) // 2, ctx.pal["strong"], m["us"])
        y += m["row2"] + 3
        if m["ss"]:
            m["sf"].draw(c, x0, y, ctx.pal["dim"], m["ss"])
        c.FillRect(x0, y0 + m["top"] + 1, x1 - x0, 1, Color(*_dim(ctx.sev, 0.5)))
        if self.two_cols:
            b2 = self.boxes(ctx)[1]
            c.FillRect(b2[0] - 7, b2[1], 1, b2[3], Color(*_dim(ctx.sev, 0.3)))


class HeadlineCols(Headline):
    two_cols = True


# ============================================================================================ dashboard
class Dashboard(Design):
    """Header, then a row of cards — WHERE (region grid), WHEN (expiry, time left, validity bar), HOW MUCH
    (the amount, or where this alert sits among the four levels) — then the text."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        w = x1 - x0
        sf, k = ctx.fam["small"], ctx.fam["key"]
        kind = f"{kind_word(ctx)} · {status_word(ctx)}"
        kf, ks = pick(ctx, kind, w // 3, cap=sf.h)
        hh = self.header_h(ctx, w - kf.width(ks) - 10)
        cw, ch = ap.cart_size(tiny())
        rest = w - (cw + 6) - 8
        ww = rest // 2
        lab = tiny().h + 4
        uf, us = pick(ctx, U(ctx, ctx.b["until"] or "-"), ww - 6, cap=k.h)
        body = max(ch, uf.line + sf.line + 6, 26)
        return {"kf": kf, "ks": ks, "hh": hh, "cw": cw, "ch": ch, "ww": ww, "hw": rest - ww, "lab": lab,
                "uf": uf, "us": us, "card": lab + body + 3}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["hh"] + 4 + m["card"] + 5
        return (x0, t, x1 - x0, y1 - t)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sf = ctx.fam["small"]
        self.header(c, ctx, x0, y0, x1 - x0 - m["kf"].width(m["ks"]) - 10)
        m["kf"].draw(c, x1 - m["kf"].width(m["ks"]), y0 + (m["hh"] - m["kf"].h) // 2, ctx.pal["dim"], m["ks"])
        y = y0 + m["hh"] + 4
        cards = [(x0, m["cw"] + 6, "WHERE"), (x0 + m["cw"] + 10, m["ww"], "WHEN"),
                 (x0 + m["cw"] + 14 + m["ww"], m["hw"], "HOW MUCH")]
        for x, w, lab in cards:
            c.FillRect(x, y, w, m["card"], Color(*ctx.pal["panel"]))
            c.FillRect(x, y, w, 1, Color(*ctx.sev))
            tiny().draw(c, x + 3, y + 3, ctx.pal["dim"], lab)
        by = y + m["lab"]
        ap.cartogram(c, ctx, x0 + 3, by, tiny())
        wx, ww = cards[1][0] + 3, m["ww"] - 6
        m["uf"].draw(c, wx, by, ctx.pal["strong"], m["us"])
        r = ap.remaining_text(ctx)
        if r:
            sf.draw(c, wx, by + m["uf"].line, ctx.pal["dim"], fit(sf, U(ctx, r + " left"), ww))
        i0, i1 = ap.times(ctx)
        if i0 and i1 and i1 > i0:
            import time
            frac = max(0.0, min(1.0, (time.time() - i0) / (i1 - i0)))
            bb = y + m["card"] - 6
            c.FillRect(wx, bb, ww, 3, Color(*_dim(ctx.sev, 0.25)))
            c.FillRect(wx, bb, max(1, int(ww * (1 - frac))), 3, Color(*ctx.sev))
        hx, hw = cards[2][0] + 3, m["hw"] - 6
        bh = m["card"] - m["lab"] - 4
        if not amount_text(c, ctx, hx, by, hw, bh):
            ap.severity_ladder(c, ctx, hx, by + 2, hw, min(bh - 2, tiny().h + 6), tiny(), ctx.t, vertical=False)


# ============================================================================================ storyboard
class Storyboard(Design):
    """NOW / EXPECT / DO columns (only those with something in them) under a header with the region chips and
    the expiry; the validity timeline along the foot."""
    GROUPS = [("NOW", ("lead", "detail"), "warning"), ("EXPECT", ("impact",), "eye"), ("DO", ("advice",), "arrow")]

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        w = x1 - x0
        k, sf = ctx.fam["key"], ctx.fam["small"]
        hh = self.header_h(ctx, w)
        uf, us = pick(ctx, U(ctx, "until " + ctx.b["until"]) if ctx.b["until"] else "", w // 3, cap=k.h)
        lab = sf.line + 3
        return {"hh": hh, "uf": uf, "us": us, "chip_h": k.h + 4, "lab": lab}

    def foot(self, ctx):
        return ctx.fam["small"].line + 10

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["hh"] + 3 + m["chip_h"] + 5
        return (x0, t, x1 - x0, y1 - t)

    def cols(self, ctx, n=None):
        n = n or len(getattr(ctx, "story", self.GROUPS))
        bx, by, bw, bh = self.text_box(ctx)
        gap = 6
        avail = bw - (n - 1) * gap
        wts = getattr(ctx, "story_w", None)
        if not wts or len(wts) != n:
            wts = [1] * n
        # columns share the width by how much text each holds, none narrower than 96 px
        ws = [max(96, int(avail * w / sum(wts))) for w in wts]
        ws[-1] = avail - sum(ws[:-1])
        out, x = [], bx
        for w in ws:
            out.append((x, by, w, bh))
            x += w + gap
        return out

    def boxes(self, ctx, n=None):
        lab = self.m(ctx)["lab"]
        return [(x + 3, y + lab + 2, w - 6, h - lab - 4) for x, y, w, h in self.cols(ctx, n)]

    def groups(self, paras):
        present = {p["style"] for p in paras}
        g = [list(x) for x in self.GROUPS]
        if "detail" in present:
            for i in (1, 2):
                if not present & set(g[i][1]):
                    g[0][1] = ("lead",)
                    g[i] = ["MORE", ("detail",), "signpost"]
                    break
        return [tuple(x) for x in g if present & set(x[1])] or [tuple(self.GROUPS[0])]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        out, over = [], False
        ctx.story = self.groups(paras)
        ctx.story_w = [sum(len(p["text"]) for p in paras if p["style"] in g[1]) + 120 for g in ctx.story]
        for (name, styles, _), box in zip(ctx.story, self.boxes(ctx, len(ctx.story))):
            ps = [dict(p) for p in paras if p["style"] in styles]
            if not ps:
                continue
            lines, o = wa.layout(ps, [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap, home=ctx.home_name)
            base = paras.index(next(p for p in paras if p["style"] in styles))
            for ln in lines:
                ln["para"] += base
            out += lines
            over = over or o
        return out, over

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sf = ctx.fam["small"]
        hh = self.header(c, ctx, x0, y0, x1 - x0)
        y = y0 + hh + 3
        uw = m["uf"].width(m["us"])
        ap.chips(c, ctx, x0, y, x1 - x0 - uw - 10, ctx.fam["key"], lines=1)
        m["uf"].draw(c, x1 - uw, y + 2, ctx.pal["strong"], m["us"])
        for (name, _, ic), (x, yy, w, h) in zip(getattr(ctx, "story", self.GROUPS), self.cols(ctx)):
            lab = m["lab"]
            c.FillRect(x, yy, w, lab, Color(*_dim(ctx.sev, 0.3)))
            px.draw(c, ic, x + 3, yy + (lab - 8) // 2, ctx.pal["strong"], 8)
            sf.draw(c, x + 14, yy + 2, ctx.pal["strong"], fit(sf, name, w - 16))
            frame(c, x, yy, w, h, _dim(ctx.sev, 0.3))
        ap.timeline(c, ctx, x0, y1 - sf.line - 8, x1 - x0, sf)

    def dynamic(self, c, ctx, seg, p):
        super().dynamic(c, ctx, seg, p)

    def text_area_for_status(self, ctx):
        return self.text_area(ctx)


# ============================================================================================ ladder
class Ladder(Design):
    """A narrow rail with the four alert levels, this one lit; name, chips and facts at the right; the text
    below them."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        sf, k = ctx.fam["small"], ctx.fam["key"]
        rf = sf if sf.width("STATEMENT") + 8 <= 64 else tiny()
        rw = max(rf.width(lab) for _, lab in ap.LEVELS) + 8
        rx = x0 + rw + 8
        hh = self.header_h(ctx, x1 - rx)
        ff, fs = pick_list(ctx, fact_items(ctx, issued=True), x1 - rx, cap=sf.h)
        top = hh + 3 + k.h + 4 + 3 + ff.line + 3
        return {"rf": rf, "rw": rw, "rx": rx, "hh": hh, "ff": ff, "fs": fs, "top": top}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        return (m["rx"], y0 + m["top"], x1 - m["rx"], y1 - y0 - m["top"])

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        rx = m["rx"]
        self.header(c, ctx, rx, y0, x1 - rx)
        y = y0 + m["hh"] + 3
        ap.chips(c, ctx, rx, y, x1 - rx, ctx.fam["key"], lines=1)
        y += ctx.fam["key"].h + 4 + 3
        m["ff"].draw(c, rx, y, ctx.pal["text"], m["fs"])

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        ap.severity_ladder(c, ctx, x0, y0, m["rw"], y1 - y0, m["rf"], ctx.t)


# ============================================================================================ countdown
class Countdown(Design):
    """Time first: a ring showing how much of the alert's validity is left, beside the name and region chips;
    the text in the middle; the issued → now → until timeline along the foot."""
    R = 24

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x0 + 2 * self.R + 10
        hh = self.header_h(ctx, x1 - rx)
        top = max(2 * self.R + 2, hh + 3 + ctx.fam["key"].h + 4) + 4
        return {"rx": rx, "hh": hh, "top": top}

    def foot(self, ctx):
        return ctx.fam["small"].line + 10

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["top"] + 3
        return (x0, t, x1 - x0, y1 - t)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        self.header(c, ctx, m["rx"], y0, x1 - m["rx"])
        ap.chips(c, ctx, m["rx"], y0 + m["hh"] + 3, x1 - m["rx"], ctx.fam["key"], lines=1)
        c.FillRect(x0, y0 + m["top"], x1 - x0, 1, Color(*_dim(ctx.sev, 0.5)))

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        sf = ctx.fam["small"]
        c.FillRect(x0, y0, 2 * self.R + 2, 2 * self.R + 2, Color(*ctx.pal["bg"]))
        ap.countdown_ring(c, ctx, x0 + self.R, y0 + self.R, self.R, ctx.fam["key"], tiny())
        c.FillRect(x0, y1 - sf.line - 9, x1 - x0, sf.line + 9, Color(*ctx.pal["bg"]))
        ap.timeline(c, ctx, x0, y1 - sf.line - 8, x1 - x0, sf, getattr(ctx, "now", None))

    def dynamic(self, c, ctx, seg, p):
        super().dynamic(c, ctx, seg, p)


# ============================================================================================ gazette
class Gazette(Design):
    """Newspaper: masthead, the alert as the headline, "For Richmond + Vancouver · until …" as the deck, two
    columns of text with the region grid at the top of the right-hand one."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        w = x1 - x0
        sf, k = ctx.fam["small"], ctx.fam["key"]
        mast = U(ctx, ctx.th.get("header") or "WEATHER")
        ed = U(ctx, f"{kind_word(ctx)} · ISSUED {ctx.b['issued']}" if ctx.b["issued"] else f"{kind_word(ctx)} · {status_word(ctx)}")
        mast = fit(sf, mast, w // 2 - 6)
        if sf.width(mast) + sf.width(ed) + 12 > w:
            ed = fit(sf, ed, w - sf.width(mast) - 12)
        tf, ts = pick(ctx, wa.title_text(ctx), w, big=True)
        until = U(ctx, " · until " + ctx.b["until"]) if ctx.b["until"] else ""
        df, ws = best_where(ctx, w - k.width(until + "For "), cap=k.h, min_h=sf.h + 1)
        deck = fit(df, U(ctx, "For ") + ws + until, w)
        cw = (w - 14) // 2
        nat = ap.cart_size(tiny())
        cart = (min(nat[0], cw), nat[1])
        head = sf.line + 4 + tf.line + df.line + 6
        return {"mast": mast, "ed": ed, "tf": tf, "ts": ts, "df": df, "deck": deck, "cw": cw, "cart": cart, "head": head}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["head"] + 2
        return (x0, t, x1 - x0, y1 - t)

    def boxes(self, ctx):
        bx, by, bw, bh = self.text_box(ctx)
        m = self.m(ctx)
        cw = m["cw"]
        y2 = by + m["cart"][1] + 6
        return [(bx, by, cw, bh), (bx + cw + 14, y2, cw, by + bh - y2)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sf = ctx.fam["small"]
        sf.draw(c, x0, y0, ctx.pal["dim"], m["mast"])
        sf.draw(c, x1 - sf.width(m["ed"]), y0, ctx.pal["dim"], m["ed"])
        c.FillRect(x0, y0 + sf.line + 1, x1 - x0, 1, Color(*ctx.pal["dim"]))
        y = y0 + sf.line + 4
        m["tf"].draw(c, x0 + (x1 - x0 - m["tf"].width(m["ts"])) // 2, y, ctx.sev, m["ts"])
        y += m["tf"].line
        df = m["df"]
        wa.draw_home_text(c, ctx, df, x0 + (x1 - x0 - df.width(m["deck"])) // 2, y + 1, m["deck"], ctx.pal["strong"])
        c.FillRect(x0, y0 + m["head"], x1 - x0, 1, Color(*_dim(ctx.sev, 0.6)))
        bx = self.boxes(ctx)
        cx = bx[1][0] + (m["cw"] - m["cart"][0]) // 2
        ap.cartogram(c, ctx, cx, bx[0][1], tiny(), w=m["cart"][0], h=m["cart"][1])
        c.FillRect(bx[1][0] - 7, bx[0][1], 1, bx[0][3], Color(*_dim(ctx.pal["dim"], 0.6)))


# ============================================================================================ sign
class Sign(Design):
    """Overhead variable-message sign: three lit lines (what · where · until) between flashing beacons; the
    text below it."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        w = x1 - x0 - 60
        k = ctx.fam["key"]
        tf, ts = pick(ctx, wa.title_text(ctx), w, big=True)
        wf, ws = best_where(ctx, w, cap=k.h, min_h=ctx.fam["small"].h + 1)
        lf, ls = pick_list(ctx, fact_items(ctx), w, cap=k.h)
        sh = tf.line + wf.line + lf.line + 10
        return {"tf": tf, "ts": ts, "wf": wf, "ws": ws, "lf": lf, "ls": ls, "sh": sh}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        t = y0 + self.m(ctx)["sh"] + 6
        return (x0, t, x1 - x0, y1 - t)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sh = m["sh"]
        c.FillRect(x0, y0, x1 - x0, sh, Color(58, 60, 66))
        c.FillRect(x0 + 3, y0 + 3, x1 - x0 - 6, sh - 6, Color(0, 0, 0))
        for bx in (x0 + 1, x1 - 3):
            for by in (y0 + 1, y0 + sh - 3):
                c.FillRect(bx, by, 2, 2, Color(150, 152, 160))
        cx = (x0 + x1) // 2
        lit = tuple((ctx.th.get("opts") or {}).get("lit", (255, 176, 0)))
        y = y0 + 5
        for f, s, col in ((m["tf"], m["ts"], lit), (m["wf"], m["ws"], lit), (m["lf"], m["ls"], _dim(lit, 0.85))):
            f.draw(c, cx - f.width(s) // 2, y, col, s)
            y += f.line

    def moving(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        sh = self.m(ctx)["sh"]
        on = int(ctx.t * 1.6) % 2
        sz = min(14, sh - 12)
        for i, bx in enumerate((x0 + 8, x1 - 8 - sz)):
            c.FillRect(bx, y0 + (sh - sz) // 2, sz, sz, Color(*(ctx.sev if i == on else _dim(ctx.sev, 0.15))))


# ============================================================================================ split
class Split(Design):
    """A solid colour panel down the left, sized to what it holds (icon, level, name, regions, expiry), in
    dark or light type to suit the colour; the text on the right."""

    def panel_col(self, ctx):
        return ctx.sev

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        names = [t for _, t in region_lines(ctx, k, 158)]
        want = max([k.width(kind_word(ctx)) + 36] + [k.width(n) + 12 for n in names[:4]] + [120])
        lw = min(170, want)
        inner_w = lw - 12
        tf, tl = title_lines(ctx, inner_w, 3, max_h=(y1 - y0) // 2)
        return {"lw": lw, "tf": tf, "tl": tl, "names": names}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x0 + self.m(ctx)["lw"] + 8
        return (rx, y0, x1 - rx, y1 - y0)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        lw, iw = m["lw"], m["lw"] - 12
        col = self.panel_col(ctx)
        ink = (0, 0, 0) if sum(col) > 330 else (255, 255, 255)
        c.FillRect(x0, y0, lw, y1 - y0, Color(*col))
        x, y = x0 + 6, y0 + 5
        px.draw(c, px.hazard(ctx.b["hazard"]), x, y, ink)
        k.draw(c, x + 30, y + (24 - k.h) // 2, ink, fit(k, kind_word(ctx), iw - 30))
        y += 28
        for ln in m["tl"]:
            m["tf"].draw(c, x, y, ink, ln)
            y += m["tf"].line
        y += 4
        bottom = y1 - (sf.line + 6 if ctx.b["until"] else 2)
        labs = region_lines(ctx, k, iw)
        for i, (name, text) in enumerate(labs):
            f, s = pick(ctx, text, iw, cap=k.h)
            if y + f.h > bottom - (sf.line if i < len(labs) - 1 else 0):
                sf.draw(c, x, y, ink, fit(sf, f"+{len(labs) - i} more", iw))
                break
            if ap.is_home(ctx, name):                  # home: reversed out of the panel
                c.FillRect(x - 2, y - 1, f.width(s) + 4, f.h + 2, Color(*ink))
                f.draw(c, x, y, col, s)
            else:
                f.draw(c, x, y, ink, s)
            y += f.line
        if ctx.b["until"]:
            sf.draw(c, x, y1 - sf.h - 5, ink, fit(sf, U(ctx, "until " + ctx.b["until"]), iw))


class SplitHazard(Split):
    """Split with the panel in the hazard's own colour (rain blue, snow white…)."""

    def panel_col(self, ctx):
        return ap.hazard_colour(ctx)


# ============================================================================================ stack
class Stack(Design):
    """A headline card, then three fact cards — WHERE (chips), WHEN (until, time left), HOW MUCH (the amount, or
    the alert level) — then the text."""

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        w = x1 - x0
        hh = self.header_h(ctx, w - 12) + 6
        cw = (w - 8) // 3
        lab = tiny().h + 4
        uf, us = pick(ctx, U(ctx, ctx.b["until"] or "-"), cw - 8, cap=k.h)
        body = max(2 * (sf.h + 4) + 3, uf.line + sf.line, 28)
        return {"hh": hh, "cw": cw, "lab": lab, "uf": uf, "us": us, "card": lab + body + 3}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["hh"] + 4 + m["card"] + 5
        return (x0, t, x1 - x0, y1 - t)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sf = ctx.fam["small"]
        c.FillRect(x0, y0, x1 - x0, m["hh"], Color(*ctx.pal["panel"]))
        c.FillRect(x0, y0, 3, m["hh"], Color(*ctx.sev))
        self.header(c, ctx, x0 + 6, y0 + 3, x1 - x0 - 12)
        y = y0 + m["hh"] + 4
        cw = m["cw"]
        for i, lab in enumerate(("WHERE", "WHEN", "HOW MUCH")):
            frame(c, x0 + i * (cw + 4), y, cw, m["card"], _dim(ctx.sev, 0.4))
            tiny().draw(c, x0 + i * (cw + 4) + 3, y + 2, ctx.pal["dim"], lab)
        by = y + m["lab"]
        ap.chips(c, ctx, x0 + 3, by, cw - 6, sf, lines=2)
        wx = x0 + cw + 4 + 4
        m["uf"].draw(c, wx, by, ctx.pal["strong"], m["us"])
        r = ap.remaining_text(ctx)
        if r:
            sf.draw(c, wx, by + m["uf"].line, ctx.pal["dim"], fit(sf, U(ctx, r + " left"), cw - 8))
        hx = x0 + 2 * (cw + 4) + 4
        bh = m["card"] - m["lab"] - 3
        if not amount_text(c, ctx, hx, by, cw - 8, bh):
            ap.severity_ladder(c, ctx, hx, by + 2, cw - 8, min(bh - 2, tiny().h + 6), tiny(), ctx.t, vertical=False)


# ============================================================================================ sidebar
class Sidebar(Design):
    """A labelled info column — ALERT, AREAS (one region per line, home reversed out), UNTIL, LEFT, AMOUNT —
    with the name and the text to its right."""

    def rows(self, ctx):
        b = ctx.b
        out = [("ALERT", [kind_word(ctx)], ctx.sev)]
        out.append(("AREAS", [t for _, t in region_lines(ctx, ctx.fam["key"], 150)], ctx.pal["strong"]))
        if b["until"]:
            out.append(("UNTIL", [U(ctx, b["until"])], ctx.pal["strong"]))
        r = ap.remaining_text(ctx)
        if r:
            out.append(("LEFT", [r], ctx.pal["strong"]))
        if b["amount"]:
            out.append(("AMOUNT", [U(ctx, b["amount"])], ctx.pal["amount"]))
        return out

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        widths = [k.width(v) for _, vals, _ in self.rows(ctx) for v in vals]
        lw = max(100, min(160, max(widths) + 12))
        rx = x0 + lw + 8
        return {"lw": lw, "rx": rx, "hh": self.header_h(ctx, x1 - rx)}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        t = y0 + m["hh"] + 5
        return (m["rx"], t, x1 - m["rx"], y1 - t)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        k, tf = ctx.fam["key"], tiny()
        lw, iw = m["lw"], m["lw"] - 10
        c.FillRect(x0, y0, lw, y1 - y0, Color(*ctx.pal["panel"]))
        c.FillRect(x0 + lw - 1, y0, 1, y1 - y0, Color(*ctx.sev))
        y = y0 + 4
        for label, vals, col in self.rows(ctx):
            if y + tf.h + 2 + k.h > y1:
                break
            tf.draw(c, x0 + 5, y, ctx.pal["dim"], label)
            y += tf.h + 3
            for i, v in enumerate(vals):
                f, s = pick(ctx, v, iw, cap=k.h)
                if y + f.h > y1 - 2 or (i == 4 and len(vals) > 5):
                    tf.draw(c, x0 + 5, y, ctx.pal["dim"], f"+{len(vals) - i} MORE")
                    y += tf.h + 2
                    break
                if label == "AREAS" and ap.is_home(ctx, region_lines(ctx, ctx.fam["key"], 150)[i][0]):
                    c.FillRect(x0 + 3, y - 1, f.width(s) + 4, f.h + 2, Color(*ctx.sev))
                    f.draw(c, x0 + 5, y, (0, 0, 0), s)
                else:
                    f.draw(c, x0 + 5, y, col, s)
                y += f.line
            y += 5
        self.header(c, ctx, m["rx"], y0, x1 - m["rx"])
        c.FillRect(m["rx"], y0 + m["hh"] + 2, x1 - m["rx"], 1, Color(*_dim(ctx.sev, 0.5)))


# ============================================================================================ teletype
class Teletype(Design):
    """The bulletin as it comes off the wire, beside a region table: each region's code, AFFECTED or clear,
    the home region's code in its own tint."""
    CODES = {"rain": 11, "snow": 12, "freezing": 13, "wind": 14, "thunder": 15, "tornado": 16, "flood": 17, "heat": 18,
             "cold": 19, "frost": 20, "fog": 21, "smoke": 22, "general": 23}

    def metrics(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        sf, k = ctx.fam["small"], ctx.fam["key"]
        tw = max([sf.width(code) for _, code in ap.labels(ctx)] + [sf.width("AREAS")]) + 6
        lw = x1 - x0 - tw - 10
        b = ctx.b
        ts = b.get("_issued_ts") or 0
        stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%d%H%M") if ts else "------"
        title = b["title"] if kind_word(ctx) in b["title"].upper() else f"{b['title']} {kind_word(ctx)}"
        heads = [pick(ctx, f"WWCN{self.CODES.get(b['hazard'], 23):02d} CWVR {stamp}", lw, cap=k.h),
                 pick(ctx, f"{title} {status_word(ctx)}".upper(), lw, cap=k.h)]
        return {"tw": tw, "lw": lw, "heads": heads, "top": sum(f.line for f, _ in heads) + 6}

    def text_area(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        return (x0, y0 + m["top"], m["lw"], y1 - y0 - m["top"])

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        return wa.layout(paras, boxes, fonts, ctx.pal, True, prefix="tag", gap=gap, home=ctx.home_name)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        m = self.m(ctx)
        sf = ctx.fam["small"]
        y = y0
        for i, (f, s) in enumerate(m["heads"]):
            f.draw(c, x0, y, ctx.pal["dim"] if i == 0 else ctx.sev, s)
            y += f.line
        for xx in range(x0, x0 + m["lw"], 4):
            c.FillRect(xx, y + 2, 2, 1, Color(*_dim(ctx.sev, 0.6)))
        tx = x1 - m["tw"]
        c.FillRect(tx - 5, y0, 1, y1 - y0, Color(*_dim(ctx.sev, 0.4)))
        yy = y0
        sf.draw(c, tx, yy, ctx.pal["dim"], "AREAS")
        yy += sf.line + 2
        labs = ap.labels(ctx)
        for i, (name, code) in enumerate(labs):
            if yy + 2 * sf.line > y1 and i < len(labs) - 1:
                sf.draw(c, tx, yy, ctx.pal["dim"], f"+{len(labs) - i}")
                break
            sf.draw(c, tx, yy, ap.HOME_TINT if ap.is_home(ctx, name) else ctx.sev, code)
            yy += sf.line

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


STRUCT_IMPL = {"briefing": Briefing(), "headline": Headline(), "headline_cols": HeadlineCols(), "dashboard": Dashboard(),
               "storyboard": Storyboard(), "ladder": Ladder(), "countdown": Countdown(), "gazette": Gazette(),
               "sign": Sign(), "split": Split(), "split_hazard": SplitHazard(), "stack": Stack(), "sidebar": Sidebar(),
               "teletype": Teletype()}
