"""Full-screen weather alert: get the message across in as little time as possible.

Takes over the whole 448x192 wall (ticker and clock included) while any ECCC
alert for the configured area is in effect (data/alerts.json, written by
hub/jobs/alerts.py).

How a message is shown
----------------------
Each alert becomes a short sequence of pages (see alert_brief.py):

    headline  ->  WHAT (summary, amounts highlighted)  ->  WHEN  ->  EXPECT  ->  WHAT TO DO

Text is never scrolled past: every page holds for its own reading time
(words / reading speed, plus a minimum), then the next page comes in with a
quick transition. Long cards are split into pages at line boundaries. The
alert's name, area and expiry stay on screen throughout, so a glance at any
moment tells you what's going on. One pass = every page of every alert.

Look
----
alert_themes.py combines 12 layouts with palettes and BDF type pairings into
50 named themes; settings can override the header text, palette, fonts,
reading speed, transition speed, detail level and capitals. The hub renders
previews with this same code (display.gfx runs anywhere).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime

import numpy as np

from display.gfx import Canvas, Color, Font, resolve_asset
from display.modules import alert_art as art
from display.modules import alert_brief
from display.modules.alert_themes import FONTSETS, LAYOUTS, PALETTES, SEVERITY, THEMES, resolve

W, H = 448, 192

SAMPLE = {
    "type": "warning", "name": "rainfall warning", "code": "TEST", "test": True, "status": "issued",
    "regions": ["Metro Vancouver - central including the City of Vancouver, Burnaby and New Westminster",
                "Metro Vancouver - North Shore including West Vancouver and North Vancouver",
                "Metro Vancouver - southeast including Surrey, Langley and Delta"],
    "text": ("Heavy rain is expected, with total rainfall amounts of 50 to 70 mm, and up to 100 mm near the "
             "North Shore mountains.\n\n"
             "An atmospheric river will bring heavy rain to the region beginning tonight and continuing through "
             "Saturday. Rain will ease to showers Saturday evening.\n\n"
             "Water will likely pool on roads and in low-lying areas. Localized flooding is possible.\n\n"
             "Don't drive through flooded roadways. Allow extra time for travel.\n\n"
             "Please continue to monitor alerts and forecasts issued by Environment Canada."),
}

_FONT_CACHE = {}


def _load_font(path):
    f = _FONT_CACHE.get(path)
    if f is None:
        f = Font()
        f.LoadFont(resolve_asset("fonts/" + path))
        _FONT_CACHE[path] = f
    return f


class FontInfo:
    """A BDF font at an integer scale, with measured line metrics (BDF bounding boxes are unreliable)."""

    def __init__(self, path, scale=1):
        self.f = _load_font(path)
        self.scale = scale
        mask, top, _ = self.f.text_mask("AQgjy|")
        self.asc = -top * scale
        self.desc = (mask.shape[0] + top) * scale
        self.h = self.asc + self.desc
        self.line = self.h + max(2, scale * 2)
        self.space = max(2, self.f.CharacterWidth(32)) * scale
        self._big = {}

    def width(self, s):
        return sum(max(0, self.f.CharacterWidth(ord(ch))) for ch in s) * self.scale

    def draw(self, c, x, top, colour, s):
        """Draw with the line's top at ``top``; returns the advance."""
        if not s:
            return 0
        mask, mtop, adv = self.f.text_mask(s)
        if self.scale > 1:
            m = self._big.get(s)
            if m is None:
                m = np.repeat(np.repeat(mask, self.scale, axis=0), self.scale, axis=1)
                if len(self._big) > 80:
                    self._big.clear()
                self._big[s] = m
            mask = m
        c.BlitMask(mask, x, top + self.asc + mtop * self.scale, colour)
        return adv * self.scale


def _c(rgb):
    return Color(*rgb)


def _dim(rgb, k):
    return tuple(int(v * k) for v in rgb)


# ---------------------------------------------------------------------------
# text layout
# ---------------------------------------------------------------------------
PUNCT = ",.;:!?)%"


def wrap_spans(fi, spans, width, upper=False):
    """[(text, hi)] -> lines of [(word, hi)] that fit ``width``.

    Punctuation that follows a highlighted span ("70 mm" + ", and") is glued onto the previous word
    as its own coloured piece: a word is a list of (text, hi) pieces drawn without spaces between them."""
    words = []
    for text, hi in spans:
        t = text.upper() if upper else text
        parts = t.split()
        for k, w in enumerate(parts):
            glue = k == 0 and words and not t[:1].isspace() and w[0] in PUNCT
            if glue:
                words[-1] = words[-1] + [(w, hi)]
            else:
                words.append([(w, hi)])
    lines, cur, cw = [], [], 0
    for pieces in words:
        ww = sum(fi.width(w) for w, _ in pieces)
        add = ww if not cur else cw + fi.space + ww
        if cur and add > width:
            lines.append(cur)
            cur, cw = [pieces], ww
        else:
            cur.append(pieces)
            cw = add
    if cur:
        lines.append(cur)
    return lines


def _pieces(item):
    return item if isinstance(item, list) else [item]


def line_width(fi, line):
    return sum(fi.width(w) for it in line for w, _ in _pieces(it)) + fi.space * max(0, len(line) - 1)


def line_chars(line):
    return sum(len(w) for it in line for w, _ in _pieces(it)) + len(line)


def line_words(line):
    return len(line)


def draw_line(c, fi, x, top, line, fg, hi, max_chars=None):
    """Draw a wrapped line; highlighted pieces in ``hi``. ``max_chars`` reveals a prefix (typewriter)."""
    shown = 0
    for it in line:
        for w, h in _pieces(it):
            if max_chars is not None:
                if shown >= max_chars:
                    return x
                w = w[: max(0, max_chars - shown)]
                shown += len(w)
            x += fi.draw(c, x, top, _c(hi if h else fg), w)
        shown += 1
        x += fi.space
    return x


class S:
    """Everything a layout needs for one frame."""


class AlertScreen:
    def __init__(self, settings=None, loader=None):
        self.loader = loader
        self.settings = settings or {}
        self._plan_key = None
        self._plan = None
        self._t0 = None
        self._fonts = {}

    # -- data ------------------------------------------------------------------
    def alerts(self, entry=None):
        if entry and entry.get("test"):
            return [dict(SAMPLE, published=time.time() - 600, expires=time.time() + 12 * 3600,
                         event_end=time.time() + 20 * 3600)]
        data = (self.loader.get_data("alerts") if self.loader else None) or {}
        now = time.time()
        if now - float(data.get("updated_utc") or 0) > 3 * 3600:       # hub down: don't show stale alerts
            return []
        return [a for a in data.get("alerts") or [] if not a.get("expires") or a["expires"] > now]

    def active(self):
        return bool(self.alerts())

    def area(self):
        data = (self.loader.get_data("alerts") if self.loader else None) or {}
        return data.get("area") or "Metro Vancouver"

    # -- theme -----------------------------------------------------------------
    def theme(self):
        st = self.settings
        th = dict(resolve(st.get("theme") or "t01"))
        if st.get("palette") in PALETTES:
            th["palette"] = st["palette"]
        if st.get("header") or st.get("title"):            # "title" = the first version's name for it
            th["header"] = st.get("header") or st["title"]
        if st.get("upper") in (True, False):
            th["upper"] = st["upper"]
        if st.get("transition") in ("slide", "wipe", "fade", "cut", "type"):
            th["transition"] = st["transition"]
        return th

    def fonts(self, th):
        fs = dict(FONTSETS.get(th["fonts"]) or FONTSETS["fixed"])
        st = self.settings
        if st.get("fontset") in FONTSETS:
            fs = dict(FONTSETS[st["fontset"]])
        if st.get("title_font"):
            fs["title"] = (st["title_font"], int(st.get("title_scale") or 1))
        if st.get("body_font"):
            fs["body"] = st["body_font"]
        key = json.dumps(fs, sort_keys=True)
        hit = self._fonts.get(key)
        if hit is None:
            hit = {"title": FontInfo(*fs["title"]), "big": FontInfo(*fs["big"]), "head": FontInfo(fs["head"]),
                   "label": FontInfo(fs["label_f"]), "body": FontInfo(fs["body"]), "small": FontInfo(fs["small"])}
            # one step smaller for scaled pixel fonts, so long names shrink instead of dropping to a tiny font
            hit["title_s"] = FontInfo(fs["title"][0], fs["title"][1] - 1) if fs["title"][1] > 1 else hit["title"]
            hit["big_s"] = FontInfo(fs["big"][0], fs["big"][1] - 1) if fs["big"][1] > 1 else hit["big"]
            self._fonts = {key: hit}
        return hit

    # -- plan: pages and their hold times ----------------------------------------
    def _plan_for(self, alerts, th, fonts, layout):
        st = self.settings
        key = hashlib.sha1(json.dumps([[a.get("name"), a.get("text"), a.get("expires")] for a in alerts]
                                      + [th, st], sort_keys=True, default=str).encode()).hexdigest()
        if key == self._plan_key:
            return self._plan
        wps = max(1.0, float(st.get("reading_wpm") or 170)) / 60.0
        min_hold = max(1.0, float(st.get("min_hold_sec") or 2.5))
        speed = max(0.25, float(st.get("transition_speed") or 1.0))
        level = st.get("detail") or "standard"
        bw, bh = layout.body_size(fonts)
        fi = fonts["body"]
        per_page = max(1, (bh - (fonts["label"].line if layout.labelled else 0)) // fi.line)
        segs = []
        for ai, a in enumerate(alerts):
            b = alert_brief.brief(a)
            hl_words = len((b["title"] + " " + b["where"] + " " + b["until"] + " " + b["amount"]).split())
            segs.append({"alert": ai, "brief": b, "kind": "headline", "label": "", "lines": [],
                         "hold": max(3.5, min_hold, 1.2 + hl_words / wps)})
            for label, text in alert_brief.cards(b, level):
                lines = wrap_spans(fi, alert_brief.spans(text), bw, th["upper"])
                pages = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
                for pi, pl in enumerate(pages):
                    words = sum(len(l) for l in pl)
                    segs.append({"alert": ai, "brief": b, "kind": "page", "label": label, "lines": pl,
                                 "page": pi, "pages": len(pages), "hold": max(min_hold, 1.0 + words / wps)})
        for s in segs:
            chars = sum(line_chars(l) for l in s["lines"])
            if th["transition"] == "type" and s["kind"] == "page":
                s["trans"] = min(6.0, chars / (45.0 * speed))
            else:
                s["trans"] = 0.0 if th["transition"] == "cut" else 0.45 / speed
        t = 0.0
        for s in segs:
            s["start"] = t
            t += s["trans"] + s["hold"]
        self._plan_key, self._plan = key, {"segs": segs, "total": t}
        return self._plan

    # -- drawing ---------------------------------------------------------------
    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        alerts = self.alerts(entry)
        th = self.theme()
        pal = PALETTES.get(th["palette"]) or PALETTES["severity"]
        fonts = self.fonts(th)
        layout = LAYOUT_IMPL.get(th["layout"]) or LAYOUT_IMPL["banner"]
        if transition or self._t0 is None:
            self._t0 = now
        x0, y0, w, h = zone
        if not alerts:
            c.FillRect(x0, y0, w, h, _c(pal["bg"]))
            fonts["body"].draw(c, x0 + 8, y0 + 8, _c(pal["fg"]), "No weather alerts in effect.")
            return "done" if now - self._t0 > 5 else None
        plan = self._plan_for(alerts, th, fonts, layout)
        t = now - self._t0
        segs = plan["segs"]
        i = 0
        while i + 1 < len(segs) and t >= segs[i + 1]["start"]:
            i += 1
        seg = segs[i]
        st = S()
        st.screen, st.th, st.fonts, st.now, st.t = self, th, fonts, now, t
        b = seg["brief"]
        sev = SEVERITY.get(b["kind"], SEVERITY["statement"])
        st.sev = sev
        st.pal = {k: (sev if v == "sev" else v) for k, v in pal.items()}
        st.seg, st.brief = seg, b
        st.prev = segs[i - 1] if i > 0 and segs[i - 1]["alert"] == seg["alert"] else None
        local = t - seg["start"]
        st.trans = 1.0 if seg["trans"] <= 0 else min(1.0, local / seg["trans"])
        st.hold = 0.0 if local < seg["trans"] else min(1.0, (local - seg["trans"]) / seg["hold"])
        same = [s for s in segs if s["alert"] == seg["alert"]]
        st.index, st.count = same.index(seg), len(same)
        st.alert_no, st.alerts = seg["alert"] + 1, len(alerts)
        st.header = th["header"]
        st.upper = th["upper"]
        st.area = "Metro Vancouver" if b["test"] else self.area()
        c.FillRect(x0, y0, w, h, _c(st.pal["bg"]))
        layout.draw(c, st)
        if t >= plan["total"]:
            self._t0 = now
            return "done"
        return None


# ---------------------------------------------------------------------------
# layout building blocks
# ---------------------------------------------------------------------------
def U(st, s):
    return s.upper() if st.upper else s


def fit(st, text, width, order=("title", "head", "body", "small")):
    """First font (in order) the text fits in; scaled fonts also try one size down."""
    expanded = []
    for k in order:
        expanded.append(k)
        if k in ("title", "big"):
            expanded.append(k + "_s")
    order = expanded
    for k in order:
        if st.fonts[k].width(text) <= width:
            return st.fonts[k]
    return st.fonts[order[-1]]


def meta_text(st, sep="  ·  "):
    b = st.brief
    bits = [b["where"] or st.area]
    if b["until"]:
        bits.append(f"until {b['until']}")
    return U(st, sep.join(bits))


def title_text(st):
    return ("TEST · " if st.brief["test"] else "") + st.brief["title"]


def _lum(rgb):
    return 0.3 * rgb[0] + 0.59 * rgb[1] + 0.11 * rgb[2]


def draw_icon(c, st, x, y, scale, bg=None):
    """Hazard icon; on a coloured panel (``bg``) the icon switches to the panel's text colour if it would vanish."""
    back = bg or st.pal["bg"]
    ink = st.pal["icon"]
    if abs(_lum(ink) - _lum(back)) < 90:
        ink = st.pal["accent_fg"] if abs(_lum(st.pal["accent_fg"]) - _lum(back)) >= 90 else (
            (0, 0, 0) if _lum(back) > 128 else (255, 255, 255))
    rgba = art.icon_rgba(st.brief["hazard"], ink, st.pal["accent"], back, scale)
    art.blit_rgba(c, rgba, x, y)
    return rgba.shape[1]


def draw_glyph(c, name, x, y, colour, scale=1):
    c.BlitMask(art.glyph_mask(name, scale), x, y, _c(colour))
    return 7 * scale


def progress(c, st, x, y, w, style=None, colour=None, dim=None):
    """Where we are in this alert's pages: a filling bar, page dots, or a 2/5 count."""
    style = style or st.th["progress"]
    col, dc = colour or st.pal["accent"], dim or st.pal["line"]
    sf = st.fonts["small"]
    if style == "bar":
        c.FillRect(x, y, w, 2, _c(dc))
        done = (st.index + st.hold) / max(1, st.count)
        c.FillRect(x, y, max(1, int(w * done)), 2, _c(col))
    elif style == "dots":
        gap = 7
        sx = x + (w - st.count * gap) // 2
        for k in range(st.count):
            c.FillRect(sx + k * gap, y, 4, 3, _c(col if k == st.index else dc))
    else:
        s = f"{st.index + 1}/{st.count}"
        sf.draw(c, x + w - sf.width(s), y - sf.h + 3, _c(col), s)
    if st.alerts > 1 and style != "count":
        s = f"ALERT {st.alert_no} OF {st.alerts}"
        sf.draw(c, x + w - sf.width(s), y - sf.h - 2, _c(st.pal["dim"]), s)


def _render_page(st, seg, canvas, w, align, label, label_colour, reveal=None):
    fi, lf = st.fonts["body"], st.fonts["label"]
    top = 0
    if label and seg["label"]:
        lab = seg["label"] + (f"  {seg['page'] + 1}/{seg['pages']}" if seg.get("pages", 1) > 1 else "")
        lx = (w - lf.width(lab)) // 2 if align == "center" else 0
        lf.draw(canvas, lx, 0, _c(label_colour or st.pal["label"]), lab)
        top = lf.line
    left = reveal
    for k, ln in enumerate(seg["lines"]):
        if left is not None and left <= 0:
            break
        lx = (w - line_width(fi, ln)) // 2 if align == "center" else 0
        draw_line(canvas, fi, lx, top + k * fi.line, ln, st.pal["fg"], st.pal["hi"], left)
        if left is not None:
            left -= line_chars(ln)


def body(c, st, x, y, w, h, bg, align="left", label=True, label_colour=None):
    """The current page (with its card label), drawn into a clipped box with the theme's transition."""
    seg = st.seg
    bg = tuple(int(v) for v in bg)
    sub = Canvas(w, h)
    sub.buf[:] = bg
    mode = st.th["transition"]
    if mode == "type" and st.trans < 1.0:
        total = sum(line_chars(ln) for ln in seg["lines"])
        _render_page(st, seg, sub, w, align, label, label_colour, int(total * st.trans))
    else:
        _render_page(st, seg, sub, w, align, label, label_colour)
    if st.trans < 1.0 and mode in ("slide", "wipe", "fade"):
        e = 1 - (1 - st.trans) ** 3                                # ease-out
        old = None
        if st.prev is not None and st.prev["kind"] == "page":
            old = Canvas(w, h)
            old.buf[:] = bg
            _render_page(st, st.prev, old, w, align, label, label_colour)
        if mode == "slide":
            off = int(h * e)
            mix = np.empty_like(sub.buf)
            mix[:] = bg
            if old is not None:
                mix[: h - off] = old.buf[off:]
            mix[h - off:] = sub.buf[: off]
            sub.buf[:] = mix
        elif mode == "wipe":
            cut = int(w * e)
            if old is not None:
                sub.buf[:, cut:] = old.buf[:, cut:]
            else:
                sub.buf[:, cut:] = bg
            if 0 < cut < w:
                sub.buf[:, cut - 1] = st.pal["accent"]
        else:
            base = old.buf.astype(np.float32) if old is not None else np.array(bg, dtype=np.float32)
            sub.buf[:] = (sub.buf.astype(np.float32) * e + base * (1 - e)).astype(np.uint8)
    hh, ww = max(0, min(h, c.height - y)), max(0, min(w, c.width - x))
    c.buf[y:y + hh, x:x + ww] = sub.buf[:hh, :ww]


def lead(b):
    """The summary's first clause: 'Heavy rain is expected' from 'Heavy rain is expected, with totals of …'."""
    s = (b["summary"] or "").split(". ")[0].rstrip(".")
    if b["amount"] and b["amount"] in s:
        s = s.split(b["amount"])[0]
        s = re.sub(r"[,;]?\s*(with|of|near|up to|at)?\s*(total|totals)?\s*(\w+\s)?(amounts?|totals?)?\s*(of)?\s*$", "", s, flags=re.I)
    s = s.strip(" ,;")
    if len(s.split()) < 3:                       # "Snowfall" alone says too little: keep the whole sentence
        s = (b["summary"] or "").split(". ")[0].rstrip(".")
    return s


def headline_block(c, st, x, y, w, h, align="left", colour=None, where=False):
    """Default headline page: the key amount large, what is happening, and until when."""
    b = st.brief
    yy = y
    if b["amount"]:
        amt = U(st, b["amount"])
        fi = fit(st, amt, w, ("big", "title", "head", "body"))
        ax = x + (w - fi.width(amt)) // 2 if align == "center" else x
        fi.draw(c, ax, yy, _c(colour or st.pal["hi"]), amt)
        yy += fi.line + 2
    lines = [U(st, lead(b))]
    if where:
        lines.append(U(st, b["where"] or st.area))
    if b["until"]:
        lines.append(U(st, f"In effect until {b['until']}"))
    hf = st.fonts["head"]
    for s in lines:
        for ln in wrap_spans(hf, [(s, False)], w):
            if yy + hf.h > y + h:
                return
            lx = x + (w - line_width(hf, ln)) // 2 if align == "center" else x
            draw_line(c, hf, lx, yy, ln, st.pal["fg"], st.pal["fg"])
            yy += hf.line


class Layout:
    labelled = True

    def body_size(self, fonts):
        return (W - 16, 100)

    def draw(self, c, st):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# the 12 layouts
# ---------------------------------------------------------------------------
class Banner(Layout):
    """Severity band with header and icon, the alert name large, area/expiry strip, then pages."""

    def geom(self, fonts):
        band = fonts["head"].line + 6
        title_y = band + 3
        meta_y = title_y + fonts["title"].line
        body_y = meta_y + fonts["small"].line + 4
        return band, title_y, meta_y, body_y

    def body_size(self, fonts):
        return (W - 16, H - self.geom(fonts)[3] - 8)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        band, ty, my, by = self.geom(f)
        if st.th.get("pattern"):
            c.buf[band:, :] = art.pattern(st.th["pattern"], W, H - band, _dim(p["panel"], 0.6), p["bg"])
        c.FillRect(0, 0, W, band, _c(p["accent"]))
        x = 6 + draw_icon(c, st, 6, (band - 16) // 2, 1, p["accent"]) + 6
        f["head"].draw(c, x, 3, _c(p["accent_fg"]), U(st, st.header))
        clock = datetime.fromtimestamp(st.now).strftime("%H:%M")
        f["head"].draw(c, W - 6 - f["head"].width(clock), 3, _c(p["accent_fg"]), clock)
        tt = title_text(st)
        tf = fit(st, tt, W - 12)
        tf.draw(c, 6, ty, _c(p["fg"]), tt)
        my = ty + tf.line
        gx = 6 + draw_glyph(c, "pin", 6, my + 1, p["dim"]) + 3
        f["small"].draw(c, gx, my, _c(p["dim"]), meta_text(st))
        if st.seg["kind"] == "headline":
            headline_block(c, st, 8, by, W - 16, H - by - 8)
        else:
            body(c, st, 8, by, W - 16, H - by - 8, c.buf[by, 8].copy())
        progress(c, st, 8, H - 4, W - 16)


class Sidebar(Layout):
    """Coloured column with a large hazard icon and the alert type; text on the right."""
    COL = 118

    def top(self, fonts):
        return 4 + fonts["small"].line + 2 + fonts["title"].line + 6

    def body_size(self, fonts):
        return (W - self.COL - 16, H - self.top(fonts) - 8)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        col = self.COL
        c.FillRect(0, 0, col, H, _c(p["accent"]))
        scale = 4 if st.th["icon"] == "large" else 3
        iw = 16 * scale
        draw_icon(c, st, (col - iw) // 2, 10, scale, p["accent"])
        kind = st.brief["kind"].upper()
        kf = fit(st, kind, col - 8, ("head", "label", "small"))
        kf.draw(c, (col - kf.width(kind)) // 2, 16 + iw, _c(p["accent_fg"]), kind)
        hdr = U(st, st.header)
        hf = fit(st, hdr, col - 8, ("label", "small"))
        hf.draw(c, (col - hf.width(hdr)) // 2, 20 + iw + kf.line, _c(p["accent_fg"]), hdr)
        progress(c, st, 10, H - 8, col - 20, colour=p["accent_fg"], dim=_dim(p["accent"], 0.55))
        x = col + 8
        y = 4
        f["small"].draw(c, x, y, _c(p["dim"]), meta_text(st))
        y += f["small"].line + 2
        tt = title_text(st)
        fit(st, tt, W - x - 6).draw(c, x, y, _c(p["fg"]), tt)
        y = self.top(f)
        c.FillRect(x, y - 3, W - x - 8, 1, _c(p["line"]))
        if st.seg["kind"] == "headline":
            headline_block(c, st, x, y, W - x - 8, H - y - 6)
        else:
            body(c, st, x, y, W - x - 8, H - y - 8, p["bg"])


class Dashboard(Layout):
    """Slim title bar; the headline page is three tiles (amount / until / where)."""
    labelled = False

    def bar(self, fonts):
        return fonts["head"].line + 6

    def body_size(self, fonts):
        return (W - 104, H - self.bar(fonts) - 18)

    def tile(self, c, st, x, y, w, h, label, glyph, value, big=False):
        p, f = st.pal, st.fonts
        c.FillRect(x, y, w, h, _c(p["panel"]))
        c.FillRect(x, y, w, 2, _c(p["accent"]))
        draw_glyph(c, glyph, x + 5, y + 6, p["label"])
        f["label"].draw(c, x + 16, y + 5, _c(p["label"]), U(st, label))
        yy = y + 8 + f["label"].line
        fi = f["big"] if big and f["big"].width(value.split(" ")[0]) < w - 10 else f["head"]
        for ln in wrap_spans(fi, [(value, False)], w - 10):
            if yy + fi.h > y + h - 2:
                break
            draw_line(c, fi, x + 5, yy, ln, p["hi"] if big else p["fg"], p["hi"])
            yy += fi.line

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        bar = self.bar(f)
        if st.th.get("pattern"):
            c.buf[:, :] = art.pattern(st.th["pattern"], W, H, _dim(p["panel"], 0.5), p["bg"])
        c.FillRect(0, 0, W, bar, _c(p["panel2"]))
        c.FillRect(0, 0, 6, bar, _c(p["accent"]))
        x = 12 + draw_icon(c, st, 12, (bar - 16) // 2, 1, p["panel2"]) + 6
        s = U(st, f"until {st.brief['until']}") if st.brief["until"] else ""
        sw = f["small"].width(s)
        tt = title_text(st)
        fit(st, tt, W - x - sw - 16, ("head", "label", "small")).draw(c, x, 3, _c(p["fg"]), tt)
        f["small"].draw(c, W - 8 - sw, 5, _c(p["dim"]), s)
        y = bar + 6
        b = st.brief
        if st.seg["kind"] == "headline":
            tw = (W - 8 * 4) // 3
            hh = H - y - 12
            amount = b["amount"] or b["kind"].upper()
            self.tile(c, st, 8, y, tw, hh, "how much" if b["amount"] else "alert", "drop", U(st, amount), big=True)
            self.tile(c, st, 16 + tw, y, tw, hh, "until", "clock", U(st, b["until"] or "further notice"))
            self.tile(c, st, 24 + 2 * tw, y, tw, hh, "where", "pin", U(st, b["where"] or st.area))
        else:
            c.FillRect(8, y, 82, H - y - 12, _c(p["panel"]))
            lab = st.seg["label"] + (f" {st.seg['page'] + 1}/{st.seg['pages']}" if st.seg.get("pages", 1) > 1 else "")
            lf = fit(st, lab, 74, ("label", "small"))
            lf.draw(c, 12, y + 6, _c(p["label"]), lab)
            glyph = {"WHAT": "warn", "WHEN": "clock", "EXPECT": "eye", "WHAT TO DO": "check", "WHERE": "pin"}.get(st.seg["label"], "arrow")
            draw_glyph(c, glyph, 12, y + 10 + lf.line, p["accent"], 3)
            body(c, st, 98, y, W - 104, H - y - 14, c.buf[y, 98].copy(), label=False)
        progress(c, st, 8, H - 5, W - 16)


class Poster(Layout):
    """Centred: icon and alert name big; each page centred with lots of space."""

    def body_size(self, fonts):
        return (W - 40, H - fonts["head"].line - 30)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        acc = p["accent"] if p["accent"] != p["bg"] else p["fg"]
        if st.seg["kind"] == "headline":
            scale = 3 if st.th["icon"] == "large" else 2
            iw = 16 * scale
            tt = title_text(st)
            tf = fit(st, tt, W - 20, ("big", "title", "head"))
            total = iw + 6 + tf.line + f["head"].line * 2 + (f["big"].line if st.brief["amount"] else 0)
            y = max(2, (H - 8 - total) // 2)
            draw_icon(c, st, (W - iw) // 2, y, scale)
            y += iw + 4
            tf.draw(c, (W - tf.width(tt)) // 2, y, _c(acc), tt)
            y += tf.line
            headline_block(c, st, 20, y, W - 40, H - y - 8, align="center", where=True)
        else:
            hdr = title_text(st)
            hf = fit(st, hdr, W - 40, ("head", "label"))
            hf.draw(c, (W - hf.width(hdr)) // 2, 4, _c(acc), hdr)
            c.FillRect(W // 2 - 40, 6 + hf.line, 80, 1, _c(p["line"]))
            body(c, st, 20, 12 + hf.line, W - 40, H - hf.line - 22, p["bg"], align="center")
        progress(c, st, 20, H - 5, W - 40)


class Tape(Layout):
    """Animated hazard-tape frame around a dark panel."""
    B = 8

    def top(self, fonts):
        return self.B + 4 + fonts["title"].line + fonts["small"].line + 4

    def body_size(self, fonts):
        return (W - 2 * self.B - 16, H - self.top(fonts) - self.B - 8)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        b = self.B
        dark = (25, 25, 25) if p["bg"] == (0, 0, 0) else _dim(p["bg"], 0.5)
        c.buf[:, :] = art.pattern(st.th.get("pattern") or "stripes", W, H, p["accent"], dark, phase=int(st.t * 6) % 12)
        c.FillRect(b, b, W - 2 * b, H - 2 * b, _c(p["bg"]))
        x, y = b + 8, b + 4
        x += draw_icon(c, st, x, y + max(0, (f["title"].h - 16) // 2), 1) + 6
        tt = title_text(st)
        tf = fit(st, tt, W - x - b - 8)
        tf.draw(c, x, y, _c(p["fg"]), tt)
        y += tf.line
        f["small"].draw(c, b + 8, y, _c(p["label"]), meta_text(st))
        y = self.top(f)
        if st.seg["kind"] == "headline":
            headline_block(c, st, b + 8, y, W - 2 * b - 16, H - y - b - 4)
        else:
            body(c, st, b + 8, y, W - 2 * b - 16, H - y - b - 8, p["bg"])
        progress(c, st, b + 8, H - b - 4, W - 2 * b - 16)


class Terminal(Layout):
    """Console feed: key/value headline, then pages typed out, scanlines and a cursor."""

    def body_size(self, fonts):
        return (W - 16, H - fonts["head"].line - 8 - 14)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        hdr = U(st, f"{st.header} :: {st.area}")
        f["head"].draw(c, 6, 3, _c(p["label"]), "> " + hdr)
        c.FillRect(0, f["head"].line + 4, W, 1, _c(p["line"]))
        y = f["head"].line + 8
        b = st.brief
        if st.seg["kind"] == "headline":
            rows = [("ALERT", title_text(st)), ("STATUS", (b["status"] or "in effect").upper()),
                    ("AREA", U(st, b["where"] or st.area)), ("UNTIL", U(st, b["until"] or "-"))]
            if b["amount"]:
                rows.insert(1, ("AMOUNT", U(st, b["amount"])))
            fi = f["body"]
            kw = fi.width("AMOUNT..: ")
            local = st.t - st.seg["start"]
            for k, (kk, vv) in enumerate(rows):
                if local < k * 0.25:
                    break
                fi.draw(c, 8, y, _c(p["dim"]), (kk + "........")[:8] + ": ")
                fi.draw(c, 8 + kw, y, _c(p["hi"] if kk in ("ALERT", "AMOUNT") else p["fg"]), vv)
                y += fi.line
        else:
            body(c, st, 8, y, W - 16, H - y - 14, p["bg"])
        c.buf[1::2, :] = (c.buf[1::2, :] * 0.72).astype(np.uint8)              # scanlines
        if int(st.t * 2) % 2 == 0:
            c.FillRect(W - 14, H - 13, 7, 9, _c(p["fg"]))
        progress(c, st, 8, H - 4, W - 40)


class Broadcast(Layout):
    """TV look: title box + clock box, main panel, and a lower ticker repeating the essentials."""

    def dims(self, fonts):
        return fonts["head"].line + 10, fonts["small"].line + 4

    def body_size(self, fonts):
        top, tick = self.dims(fonts)
        return (W - 28, H - top - tick - 14)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        top, tick = self.dims(f)
        c.FillRect(0, 0, W - 74, top - 2, _c(p["accent"]))
        x = 6 + draw_icon(c, st, 6, (top - 2 - 16) // 2, 1, p["accent"]) + 6
        tt = title_text(st)
        hf = fit(st, tt, W - 74 - x - 4, ("head", "label", "small"))
        hf.draw(c, x, (top - 2 - hf.h) // 2, _c(p["accent_fg"]), tt)
        c.FillRect(W - 70, 0, 70, top - 2, _c(p["panel2"]))
        clock = datetime.fromtimestamp(st.now).strftime("%H:%M")
        f["head"].draw(c, W - 35 - f["head"].width(clock) // 2, (top - 2 - f["head"].h) // 2, _c(p["fg"]), clock)
        c.FillRect(8, top + 2, W - 16, H - top - tick - 8, _c(p["panel"]))
        if st.seg["kind"] == "headline":
            headline_block(c, st, 16, top + 8, W - 32, H - top - tick - 18, where=True)
        else:
            body(c, st, 14, top + 6, W - 28, H - top - tick - 14, p["panel"])
        ty = H - tick
        c.FillRect(0, ty, W, tick, _c(p["panel2"]))
        msg = U(st, f"   {title_text(st)} IN EFFECT  ·  {st.brief['where'] or st.area}"
                    + (f"  ·  UNTIL {st.brief['until']}" if st.brief["until"] else ""))
        mw = f["small"].width(msg)
        xx = -(int(st.t * 40) % max(1, mw))
        while xx < W:
            f["small"].draw(c, xx, ty + 2, _c(p["fg"]), msg)
            xx += mw
        progress(c, st, 8, ty - 4, W - 16)


class Columns(Layout):
    """Header band; headline as WHAT | WHEN | WHERE columns; pages full width."""

    def band(self, fonts):
        return fonts["title"].line + 6

    def body_size(self, fonts):
        return (W - 20, H - self.band(fonts) - 16)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        band = self.band(f)
        c.FillRect(0, 0, W, band, _c(p["panel"]))
        c.FillRect(0, band - 2, W, 2, _c(p["accent"]))
        tt = title_text(st)
        draw_icon(c, st, W - 22, (band - 16) // 2, 1, p["panel"])
        fit(st, tt, W - 40).draw(c, 8, 3, _c(p["fg"]), tt)
        y = band + 6
        b = st.brief
        if st.seg["kind"] == "headline":
            cw = (W - 16) // 3
            cols = [("WHAT", b["amount"] or b["summary"].split(". ")[0]),
                    ("UNTIL", b["until"] or b["timing"] or "further notice"), ("WHERE", b["where"] or st.area)]
            for k, (lab, val) in enumerate(cols):
                x = 8 + k * cw
                if k:
                    c.FillRect(x - 4, y, 1, H - y - 10, _c(p["line"]))
                f["label"].draw(c, x, y, _c(p["label"]), lab)
                c.FillRect(x, y + f["label"].line, cw - 14, 1, _c(p["accent"]))
                yy = y + f["label"].line + 4
                fi = f["head"]
                for ln in wrap_spans(fi, [(U(st, val), k == 0)], cw - 12):
                    if yy + fi.h > H - 10:
                        break
                    draw_line(c, fi, x, yy, ln, p["fg"], p["hi"])
                    yy += fi.line
        else:
            body(c, st, 10, y, W - 20, H - y - 10, p["bg"])
        progress(c, st, 8, H - 4, W - 16)


class Minimal(Layout):
    """Black space, a severity rule and clean type."""

    def top(self, fonts):
        return 6 + fonts["title"].line + fonts["small"].line + 6

    def body_size(self, fonts):
        return (W - 34, H - self.top(fonts) - 10)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        c.FillRect(8, 6, 3, H - 16, _c(st.sev))
        tt = title_text(st)
        right = 0
        if st.th["icon"] != "none":
            draw_icon(c, st, W - 24, 6, 1)
            right = 26
        tf = fit(st, tt, W - 24 - right)
        tf.draw(c, 18, 6, _c(p["accent"] if p["accent"] != p["bg"] else p["fg"]), tt)
        f["small"].draw(c, 18, 6 + tf.line, _c(p["dim"]), meta_text(st))
        y = self.top(f)
        if st.seg["kind"] == "headline":
            headline_block(c, st, 18, y, W - 34, H - y - 10)
        else:
            body(c, st, 18, y, W - 34, H - y - 10, p["bg"])
        progress(c, st, 18, H - 5, W - 34)


class Card(Layout):
    """A raised card on a patterned background, with a severity chip."""

    def top(self, fonts):
        return 8 + 8 + fonts["label"].line + fonts["title"].line + 4

    def body_size(self, fonts):
        return (W - 64, H - 10 - self.top(fonts) - 12)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        c.buf[:, :] = art.pattern(st.th.get("pattern") or "dots", W, H, p["panel2"], p["bg"])
        x0, y0, x1, y1 = 20, 8, W - 20, H - 10
        c.FillRect(x0 + 3, y0 + 3, x1 - x0, y1 - y0, _c(_dim(p["bg"], 0.3)))
        c.FillRect(x0, y0, x1 - x0, y1 - y0, _c(p["panel"]))
        c.FillRect(x0, y0, x1 - x0, 1, _c(p["line"]))
        chip = st.brief["kind"].upper()
        cw = f["label"].width(chip) + 12
        c.FillRect(x0 + 10, y0 + 6, cw, f["label"].line, _c(st.sev))
        f["label"].draw(c, x0 + 16, y0 + 7, _c((255, 255, 255)), chip)
        f["small"].draw(c, x0 + 18 + cw, y0 + 8, _c(p["dim"]), meta_text(st))
        draw_icon(c, st, x1 - 26, y0 + 6, 1, p["panel"])
        y = y0 + 8 + f["label"].line
        tt = title_text(st)
        fit(st, tt, x1 - x0 - 24).draw(c, x0 + 12, y, _c(p["fg"]), tt)
        y = self.top(f)
        if st.seg["kind"] == "headline":
            headline_block(c, st, x0 + 12, y, x1 - x0 - 24, y1 - y - 10)
        else:
            body(c, st, x0 + 12, y, x1 - x0 - 24, y1 - y - 12, p["panel"])
        progress(c, st, x0 + 12, y1 - 6, x1 - x0 - 24)


class Split(Layout):
    """Top band in the accent colour with icon and alert name; pages below."""

    def top_h(self, fonts):
        return max(60, min(96, fonts["big"].line + fonts["small"].line + 14))

    def body_size(self, fonts):
        return (W - 20, H - self.top_h(fonts) - 16)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        th_ = self.top_h(f)
        c.FillRect(0, 0, W, th_, _c(p["accent"]))
        scale = 3 if st.th["icon"] == "large" and th_ >= 56 else 2
        iw = 16 * scale
        draw_icon(c, st, 10, (th_ - iw) // 2, scale, p["accent"])
        x = 22 + iw
        tt = title_text(st)
        tf = fit(st, tt, W - x - 8, ("big", "title", "head"))
        ty = max(2, (th_ - tf.line - f["small"].line) // 2)
        tf.draw(c, x, ty, _c(p["accent_fg"]), tt)
        f["small"].draw(c, x, ty + tf.line, _c(p["accent_fg"]), meta_text(st))
        y = th_ + 6
        if st.seg["kind"] == "headline":
            headline_block(c, st, 10, y, W - 20, H - y - 8)
        else:
            body(c, st, 10, y, W - 20, H - y - 10, p["bg"])
        progress(c, st, 10, H - 4, W - 20)


class Marquee(Layout):
    """Theatre-sign border of chasing lights; centred title and pages."""
    B = 9

    def top(self, fonts):
        return self.B + 4 + fonts["title"].line + 2

    def body_size(self, fonts):
        return (W - 2 * self.B - 24, H - self.top(fonts) - self.B - 12)

    def draw(self, c, st):
        f, p = st.fonts, st.pal
        b = self.B
        c.FillRect(0, 0, W, H, _c(p["panel"]))
        c.FillRect(b, b, W - 2 * b, H - 2 * b, _c(p["bg"]))
        step = int(st.t * 8)
        pts = ([(x, 3) for x in range(4, W - 4, 8)] + [(W - 6, y) for y in range(4, H - 4, 8)]
               + [(x, H - 6) for x in range(W - 8, 2, -8)] + [(3, y) for y in range(H - 8, 2, -8)])
        for k, (x, y) in enumerate(pts):
            lit = (k + step) % 3 == 0
            c.FillRect(x, y, 3, 3, _c(p["hi"] if lit else _dim(p["accent"], 0.45)))
        tt = title_text(st)
        tf = fit(st, tt, W - 2 * b - 24)
        tf.draw(c, (W - tf.width(tt)) // 2, b + 4, _c(p["accent"] if p["accent"] != p["bg"] else p["fg"]), tt)
        y = self.top(f)
        if st.seg["kind"] == "headline":
            draw_icon(c, st, (W - 32) // 2, y, 2)
            headline_block(c, st, b + 12, y + 36, W - 2 * b - 24, H - y - 36 - b - 6, align="center", where=True)
        else:
            body(c, st, b + 12, y, W - 2 * b - 24, H - y - b - 12, p["bg"], align="center")
        progress(c, st, b + 12, H - b - 4, W - 2 * b - 24)


LAYOUT_IMPL = {"banner": Banner(), "sidebar": Sidebar(), "dashboard": Dashboard(), "poster": Poster(), "tape": Tape(),
               "terminal": Terminal(), "broadcast": Broadcast(), "columns": Columns(), "minimal": Minimal(),
               "card": Card(), "split": Split(), "marquee": Marquee()}


# ---------------------------------------------------------------------------
# hub helpers (previews)
# ---------------------------------------------------------------------------
def render_preview(theme, alerts_data=None, at=4.0, settings=None):
    """(H, W, 3) uint8 frame of the alert screen at ``at`` seconds, for the control panel's previews."""

    class _Loader:
        def get_data(self, name):
            return alerts_data if name == "alerts" else None

    s = dict(settings or {}, theme=theme)
    scr = AlertScreen(s, _Loader() if alerts_data else None)
    c = Canvas(W, H)
    entry = {} if alerts_data and scr.alerts() else {"test": True}
    t0 = 1_000_000.0
    scr.draw(c, (0, 0, W, H), entry, True, now=t0)
    scr.draw(c, (0, 0, W, H), entry, False, now=t0 + at)
    return c.buf.copy()


def timeline(settings=None, alerts_data=None):
    """Page starts and holds, for the control panel's preview scrubber."""
    scr = AlertScreen(dict(settings or {}), None)
    th = scr.theme()
    fonts = scr.fonts(th)
    layout = LAYOUT_IMPL.get(th["layout"]) or LAYOUT_IMPL["banner"]
    alerts = (alerts_data or {}).get("alerts") or scr.alerts({"test": True})
    plan = scr._plan_for(alerts, th, fonts, layout)
    return {"total": round(plan["total"], 2),
            "pages": [{"start": round(x["start"] + x["trans"], 2), "hold": round(x["hold"], 2),
                       "label": x["label"] or "HEADLINE", "alert": x["alert"]} for x in plan["segs"]]}


THEME_LIST = [{"id": k, "name": v["name"], "layout": v["layout"], "layout_label": LAYOUTS[v["layout"]],
               "palette": v["palette"], "fonts": v["fonts"], "description": v["description"], "header": v["header"],
               "transition": v["transition"], "progress": v["progress"]}
              for k, v in THEMES.items()]
