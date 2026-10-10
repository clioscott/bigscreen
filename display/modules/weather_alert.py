"""Full-screen weather alert: the whole message on one dense screen, important parts highlighted.

Takes over the whole 448x192 wall (ticker and clock included) while any ECCC
alert for the configured area is in effect (data/alerts.json, written by
hub/jobs/alerts.py).

Each alert is one screen. The original ECCC text is kept almost entirely
(boilerplate dropped — see alert_brief.py) and laid out as styled paragraphs:

    summary     bold, brightest colour          amounts (50 to 70 mm…)  bold + amount colour
    details     regular                         what to expect          impact colour
    what to do  bold, advice colour             regions                 small, dim

A theme's font family has a size ladder; the largest size at which everything
fits is used, then less important paragraphs are dropped if even the smallest
size overflows. The text appears with the theme's reveal animation, then holds
for its reading time while the animated border keeps the screen alive. One
pass = every alert once.

The finished screen (minus the animated parts) is cached after the reveal, so
holding costs one array copy plus the border per frame.
"""

from __future__ import annotations

import hashlib
import json
import math
import time

import numpy as np

from display.gfx import Canvas, Color, Font, resolve_asset
from display.modules import alert_art as art
from display.modules import alert_brief
from display.modules.alert_themes import BORDERS, FAMILIES, PALETTES, REVEALS, SEVERITY, STRUCTURES, THEMES, resolve

W, H = 448, 192
SCRAMBLE = "#%&@$*+=?ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"
PUNCT = ",.;:!?)%"

SAMPLE = {
    "type": "warning", "name": "rainfall warning", "code": "TEST", "test": True, "status": "issued",
    "regions": ["Metro Vancouver - central including the City of Vancouver, Burnaby and New Westminster",
                "Metro Vancouver - North Shore including West Vancouver and North Vancouver",
                "Metro Vancouver - southeast including Surrey, Langley and Delta"],
    "text": ("Heavy rain is expected, with total rainfall amounts of 50 to 70 mm, and up to 100 mm near the "
             "North Shore mountains.\n\n"
             "An atmospheric river will bring heavy rain to the region beginning tonight and continuing through "
             "Saturday. Rainfall rates of 5 to 10 mm per hour are possible at times overnight. Rain will ease to "
             "showers Saturday evening as the system moves inland.\n\n"
             "Water will likely pool on roads and in low-lying areas. Localized flooding and washouts near rivers, "
             "creeks and culverts are possible. Visibility may be suddenly reduced in heavy downpours.\n\n"
             "Don't drive through flooded roadways. Allow extra time for travel. Keep drains and gutters clear of "
             "leaves and debris.\n\n"
             "Please continue to monitor alerts and forecasts issued by Environment Canada."),
}

STYLE = {   # paragraph style → (bold, colour role, prefix glyph, terminal tag)
    "lead": (True, "strong", "warn", "WHAT"),
    "detail": (False, "text", None, "INFO"),
    "impact": (False, "impact", "eye", "EXPECT"),
    "advice": (True, "advice", "arrow", "DO"),
    "regions": (False, "dim", "pin", "AREA"),
}

_FONTS = {}


def _font(path):
    f = _FONTS.get(path)
    if f is None:
        f = Font()
        f.LoadFont(resolve_asset("fonts/" + path))
        _FONTS[path] = f
    return f


class FI:
    """A BDF font at an integer scale with measured metrics; ``faux`` draws bold by double-striking."""

    def __init__(self, path, scale=1, faux=False):
        self.f, self.scale, self.faux = _font(path), scale, faux
        mask, top, _ = self.f.text_mask("AQgjy|")
        self.asc = -top * scale
        self.h = mask.shape[0] * scale
        self.line = self.h + max(1, scale)
        self.space = max(2, self.f.CharacterWidth(32)) * scale
        self._big = {}

    def width(self, s):
        return sum(max(0, self.f.CharacterWidth(ord(ch))) for ch in s) * self.scale + (1 if self.faux and s else 0)

    def draw(self, c, x, top, colour, s):
        if not s:
            return 0
        mask, mtop, adv = self.f.text_mask(s)
        if self.scale > 1:
            m = self._big.get(s)
            if m is None:
                m = np.repeat(np.repeat(mask, self.scale, axis=0), self.scale, axis=1)
                if len(self._big) > 120:
                    self._big.clear()
                self._big[s] = m
            mask = m
        y = top + self.asc + mtop * self.scale
        col = colour if isinstance(colour, Color) else Color(*colour)
        c.BlitMask(mask, x, y, col)
        if self.faux:
            c.BlitMask(mask, x + 1, y, col)
        return adv * self.scale + (1 if self.faux else 0)


def _dim(rgb, k):
    return tuple(max(0, min(255, int(v * k))) for v in rgb)


def _ease(q):
    return 1 - (1 - max(0.0, min(1.0, q))) ** 3


# ---------------------------------------------------------------------------
# text layout: styled paragraphs → positioned runs in one or more boxes
# ---------------------------------------------------------------------------
def _words(text, upper):
    """[(word pieces)] where a piece is (text, highlighted); punctuation glued to the previous word."""
    words = []
    for chunk, hi in alert_brief.spans(text):
        t = chunk.upper() if upper else chunk
        parts = t.split()
        for k, w in enumerate(parts):
            if k == 0 and words and not t[:1].isspace() and w[0] in PUNCT:
                words[-1].append((w, hi))
            else:
                words.append([(w, hi)])
    return words


def layout(paras, boxes, fonts, pal, upper, prefix="glyph", gap=3):
    """Flow paragraphs through ``boxes`` [(x, y, w, h)] in order.

    Returns (lines, overflow). A line = {"y", "h", "x0", "w", "runs", "para", "chars"}; a run is
    (x, text, font, colour) or ("glyph", x, name, colour)."""
    if not boxes:
        return [], True
    reg, bold = fonts["reg"], fonts["bold"]
    lines = []
    state = {"bi": 0, "cy": boxes[0][1]}
    tagw = max(reg.width(s[3] + ": ") for s in STYLE.values()) if prefix == "tag" else 0
    for pi, p in enumerate(paras):
        bold_par, role, glyph, tag = STYLE[p["style"]]
        small = p["style"] == "regions" and prefix != "tag"
        fnt_par = fonts["small"] if small else (bold if bold_par else reg)
        indent = 10 if (prefix == "glyph" and glyph) else tagw
        col = pal[role]

        def pick(hi):
            return fnt_par if small else (bold if (hi or bold_par) else reg)

        def flush(cur, first):
            bx, by, bw, bh = boxes[state["bi"]]
            if state["cy"] + fnt_par.h > by + bh:
                state["bi"] += 1
                if state["bi"] >= len(boxes):
                    return False
                bx, by, bw, bh = boxes[state["bi"]]
                state["cy"] = by
            x = bx + indent
            runs = []
            if first and prefix == "glyph" and glyph:
                runs.append(("glyph", bx + 1, glyph, pal["label"]))
            if first and prefix == "tag":
                runs.append((bx, tag + ":", reg, pal["label"]))
            chars = 0
            for pieces in cur:
                for txt, hi in pieces:
                    f = pick(hi)
                    runs.append((x, txt, f, pal["amount"] if hi else col))
                    x += f.width(txt)
                    chars += len(txt)
                x += fnt_par.space
                chars += 1
            lines.append({"y": state["cy"], "h": fnt_par.h, "x0": bx, "w": bw, "runs": runs, "para": pi, "chars": chars})
            state["cy"] += fnt_par.line
            return True

        cur, cw, first = [], 0, True
        for pieces in _words(p["text"], upper):
            ww = sum(pick(hi).width(t) for t, hi in pieces)
            avail = boxes[state["bi"]][2] - indent
            add = ww if not cur else cw + fnt_par.space + ww
            if cur and add > avail:
                if not flush(cur, first):
                    return lines, True
                first = False
                cur, cw = [pieces], ww
            else:
                cur.append(pieces)
                cw = add
        if cur and not flush(cur, first):
            return lines, True
        state["cy"] += gap
    return lines, False


def draw_lines(c, lines, upto=None, colour_k=1.0, flash_last=False, scramble=None, offset=None):
    """Draw laid-out lines. ``upto`` = characters to reveal (typewriter); ``scramble`` = (t, per-line progress)."""
    left = upto
    for li, ln in enumerate(lines):
        if left is not None and left <= 0:
            break
        dx = offset(ln) if offset else 0
        if dx is None:
            continue
        q = scramble[1](li) if scramble else 1.0
        if scramble and q <= 0:
            continue
        shown_line = int(ln["chars"] * q) if scramble else None
        k = 0
        for run in ln["runs"]:
            if run[0] == "glyph":
                _, x, name, col = run
                c.BlitMask(art.glyph_mask(name), x + dx, ln["y"] + max(0, (ln["h"] - 7) // 2), Color(*_dim(col, colour_k)))
                continue
            x, txt, f, col = run
            col = (255, 255, 255) if flash_last and li == len(lines) - 1 else _dim(col, colour_k)
            if left is not None:
                if left <= 0:
                    return
                txt = txt[:left]
                left -= len(txt) + 1
            if scramble and shown_line is not None and k + len(txt) > shown_line:
                good = max(0, shown_line - k)
                xx = x + dx + f.draw(c, x + dx, ln["y"], col, txt[:good])
                seed = int(scramble[0] * 18) + li * 31 + k
                junk = "".join(SCRAMBLE[(seed * 7 + j * 13) % len(SCRAMBLE)] for j in range(len(txt) - good))
                f.draw(c, xx, ln["y"], _dim(col, 0.45), junk)
            else:
                f.draw(c, x + dx, ln["y"], col, txt)
            k += len(txt) + 1


# ---------------------------------------------------------------------------
# borders (drawn every frame, on top)
# ---------------------------------------------------------------------------
_PERIM = {}


def _perimeter(inset):
    """Clockwise (ys, xs) of the rectangle ``inset`` px in from the edge."""
    hit = _PERIM.get(inset)
    if hit is None:
        x0, y0, x1, y1 = inset, inset, W - 1 - inset, H - 1 - inset
        xs = list(range(x0, x1 + 1)) + [x1] * (y1 - y0 - 1) + list(range(x1, x0 - 1, -1)) + [x0] * (y1 - y0 - 1)
        ys = [y0] * (x1 - x0 + 1) + list(range(y0 + 1, y1)) + [y1] * (x1 - x0 + 1) + list(range(y1 - 1, y0, -1))
        hit = (np.array(ys), np.array(xs))
        _PERIM[inset] = hit
    return hit


BORDER_W = {"chase": 2, "pulse": 2, "hazard": 5, "double": 4, "lights": 3, "comet": 2, "corners": 3, "glow": 3, "rails": 5}
_HAZ = {}


def draw_border(c, kind, sev, t):
    sev = np.array(sev, dtype=np.float32)
    if kind == "pulse":
        k = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(t * 2 * math.pi * 0.7))
        for i in range(2):
            ys, xs = _perimeter(i)
            c.buf[ys, xs] = (sev * k * (1 - 0.35 * i)).astype(np.uint8)
    elif kind == "chase":
        for i in range(2):
            ys, xs = _perimeter(i)
            idx = (np.arange(len(xs)) + int(t * 40)) % 12
            c.buf[ys, xs] = np.where((idx < 7)[:, None], sev, sev * 0.22).astype(np.uint8)
    elif kind == "hazard":
        if not _HAZ:
            yy, xx = np.mgrid[0:H, 0:W]
            _HAZ["edge"] = (xx < 5) | (xx >= W - 5) | (yy < 5) | (yy >= H - 5)
            _HAZ["diag"] = (xx + yy)
        band = ((_HAZ["diag"] + int(t * 10)) // 5) % 2 == 0
        edge = _HAZ["edge"]
        c.buf[edge & band] = sev.astype(np.uint8)
        c.buf[edge & ~band] = (18, 18, 18)
    elif kind == "double":
        ys, xs = _perimeter(0)
        c.buf[ys, xs] = sev.astype(np.uint8)
        ys, xs = _perimeter(3)
        c.buf[ys, xs] = (sev * 0.5).astype(np.uint8)
        col = Color(*((255, 255, 255) if int(t * 1.5) % 2 == 0 else tuple(int(v) for v in sev)))
        L = 16
        for (x, y, dx, dy) in ((0, 0, 1, 1), (W, 0, -1, 1), (0, H, 1, -1), (W, H, -1, -1)):
            c.FillRect(x if dx > 0 else x - L, y if dy > 0 else y - 3, L, 3, col)
            c.FillRect(x if dx > 0 else x - 3, y if dy > 0 else y - L, 3, L, col)
    elif kind == "lights":
        ys, xs = _perimeter(1)
        sel = np.arange(0, len(xs), 6)
        lit = ((np.arange(len(sel)) + int(t * 10)) % 3) == 0
        dimc = tuple(int(v) for v in sev * 0.4)
        for j, s in enumerate(sel):
            y, x = ys[s], xs[s]
            c.buf[max(0, y - 1):y + 1, max(0, x - 1):x + 1] = (255, 240, 180) if lit[j] else dimc
    elif kind == "comet":
        ys, xs = _perimeter(0)
        ys1, xs1 = _perimeter(1)
        n, n1 = len(xs), len(xs1)
        base = (sev * 0.25).astype(np.uint8)
        c.buf[ys, xs] = base
        c.buf[ys1, xs1] = base
        tail = np.arange(60)
        k = (1 - tail / 60.0)[:, None]
        col = (sev[None, :] * (0.25 + 0.75 * k) + 255 * (k ** 6) * 0.6).clip(0, 255).astype(np.uint8)
        for head in (int(t * 160) % n, (int(t * 160) + n // 2) % n):
            idx = (head - tail) % n
            c.buf[ys[idx], xs[idx]] = col
            j = (idx * n1 // n) % n1
            c.buf[ys1[j], xs1[j]] = col
    elif kind == "corners":
        ys, xs = _perimeter(0)
        c.buf[ys, xs] = (sev * 0.3).astype(np.uint8)
        k = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(t * 4))
        col = Color(*(sev * k).astype(np.uint8))
        L = 22
        for (x, y, dx, dy) in ((0, 0, 1, 1), (W, 0, -1, 1), (0, H, 1, -1), (W, H, -1, -1)):
            c.FillRect(x if dx > 0 else x - L, y if dy > 0 else y - 3, L, 3, col)
            c.FillRect(x if dx > 0 else x - 3, y if dy > 0 else y - L, 3, L, col)
    elif kind == "glow":
        for i, k in enumerate((1.0, 0.5, 0.2)):
            ys, xs = _perimeter(i)
            c.buf[ys, xs] = (sev * k).astype(np.uint8)
        ys, xs = _perimeter(0)
        idx = (int(t * 90) + np.arange(-14, 15)) % len(xs)
        c.buf[ys[idx], xs[idx]] = (255, 255, 255)
    elif kind == "rails":
        off = int(t * 24) % 12
        for y0 in (0, H - 5):
            c.FillRect(0, y0, W, 5, Color(14, 14, 14))
            for x in range(-12 + off, W, 12):
                for r in range(5):
                    xx = x + (r if r < 3 else 4 - r)
                    if 0 <= xx < W - 3:
                        c.buf[y0 + r, xx:xx + 3] = sev.astype(np.uint8)


# ---------------------------------------------------------------------------
# the screen
# ---------------------------------------------------------------------------
class St:
    """Per-alert context handed to a structure."""


class AlertScreen:
    def __init__(self, settings=None, loader=None):
        self.loader = loader
        self.settings = settings or {}
        self._plan_key = self._plan = None
        self._t0 = None
        self._fonts = {}
        self._static = None          # (key, frame) cached after the reveal

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
        for key, allowed in (("palette", PALETTES), ("family", FAMILIES), ("border", BORDERS), ("reveal", REVEALS),
                             ("structure", STRUCTURES)):
            if st.get(key) in allowed:
                th[key] = st[key]
        if st.get("header") or st.get("title"):
            th["header"] = st.get("header") or st["title"]
        if st.get("upper") in (True, False):
            th["upper"] = st["upper"]
        return th

    def family(self, th):
        fam = dict(FAMILIES.get(th["family"]) or FAMILIES["fixed"])
        st = self.settings
        if st.get("title_font"):
            fam["title"] = [(st["title_font"], int(st.get("title_scale") or 1))] + list(fam["title"])
        if st.get("body_font"):
            fam["ladder"] = [(st["body_font"], st.get("body_bold") or None, 1)] + list(fam["ladder"])
        key = json.dumps(fam, sort_keys=True)
        hit = self._fonts.get(key)
        if hit is None:
            hit = {"titles": [FI(p, s) for p, s in fam["title"]], "key": FI(fam["key"]), "small": FI(fam["small"]),
                   "ladder": [(FI(r, s), FI(b, s) if b else FI(r, s, faux=True)) for r, b, s in fam["ladder"]]}
            self._fonts = {key: hit}
        return hit

    # -- plan ------------------------------------------------------------------
    def _plan_for(self, alerts, th, fam):
        st = self.settings
        key = hashlib.sha1(json.dumps([[a.get("name"), a.get("text"), a.get("expires"), a.get("regions")] for a in alerts]
                                      + [th, st], sort_keys=True, default=str).encode()).hexdigest()
        if key == self._plan_key:
            return self._plan
        struct = STRUCT_IMPL.get(th["structure"]) or STRUCT_IMPL["band"]
        wps = max(1.0, float(st.get("reading_wpm") or 200)) / 60.0
        min_hold = max(4.0, float(st.get("min_hold_sec") or 12))
        speed = max(0.25, float(st.get("reveal_speed") or 1.0))
        level = st.get("detail") or "all"
        pal = PALETTES.get(th["palette"]) or PALETTES["black"]
        segs, t = [], 0.0
        for ai, a in enumerate(alerts):
            b = alert_brief.brief(a)
            sev = SEVERITY.get(b["kind"], SEVERITY["statement"])
            ctx = St()
            ctx.th, ctx.b, ctx.sev, ctx.upper, ctx.fam = th, b, sev, th["upper"], fam
            ctx.pal = {k: (sev if v == "sev" else v) for k, v in pal.items() if k != "name"}
            ctx.area = "Metro Vancouver" if b["test"] else self.area()
            ctx.title_f = struct.title_font(ctx)
            paras = alert_brief.paragraphs(b, level)
            boxes = struct.boxes(ctx)
            chosen = None
            for drop in ((), ("regions",), ("regions", "detail")):
                use = [p for p in paras if p["style"] not in drop]
                for reg, bold in fam["ladder"]:
                    fonts = {"reg": reg, "bold": bold, "small": fam["small"] if reg.h > fam["small"].h else reg}
                    lines, over = struct.flow(ctx, use, boxes, fonts)
                    if not over:
                        chosen = (lines, use, fonts)
                        break
                if chosen:
                    break
            if not chosen:                                  # still too long: smallest size, cut at the end
                reg, bold = fam["ladder"][-1]
                fonts = {"reg": reg, "bold": bold, "small": reg}
                use = [p for p in paras if p["style"] not in ("regions", "detail")]
                lines, _ = struct.flow(ctx, use, boxes, fonts)
                chosen = (lines, use, fonts)
            lines, use, fonts = chosen
            for g in (12, 9, 7, 5):                         # spread paragraphs out when there's room
                more, over = struct.flow(ctx, use, boxes, fonts, gap=g)
                if not over:
                    lines = more
                    break
            words = sum(len(p["text"].split()) for p in use) + 8
            chars = sum(ln["chars"] for ln in lines)
            rev = th["reveal"]
            if rev == "type":
                reveal_t = min(7.0, chars / (220.0 * speed))
            elif rev in ("decode", "sections", "fade"):
                reveal_t = 2.2 / speed
            else:
                reveal_t = 1.4 / speed
            hold = max(min_hold, 2.0 + words / wps)
            segs.append({"alert": ai, "ctx": ctx, "lines": lines, "paras": use, "fonts": fonts, "start": t,
                         "reveal": reveal_t, "hold": hold})
            t += reveal_t + hold
        self._plan_key, self._plan = key, {"segs": segs, "total": t}
        self._static = None
        return self._plan

    # -- drawing ---------------------------------------------------------------
    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        alerts = self.alerts(entry)
        th = self.theme()
        pal = PALETTES.get(th["palette"]) or PALETTES["black"]
        if transition or self._t0 is None:
            self._t0 = now
            self._static = None
        if not alerts:
            c.FillRect(0, 0, W, H, Color(*pal["bg"]))
            return "done" if now - self._t0 > 5 else None
        fam = self.family(th)
        plan = self._plan_for(alerts, th, fam)
        t = now - self._t0
        segs = plan["segs"]
        i = 0
        while i + 1 < len(segs) and t >= segs[i + 1]["start"]:
            i += 1
        seg = segs[i]
        ctx = seg["ctx"]
        ctx.t, ctx.now, ctx.index, ctx.count = t, now, i, len(segs)
        local = t - seg["start"]
        p = min(1.0, local / seg["reveal"]) if seg["reveal"] > 0 else 1.0
        struct = STRUCT_IMPL.get(th["structure"]) or STRUCT_IMPL["band"]
        skey = (self._plan_key, i)
        if p >= 1.0 and self._static is not None and self._static[0] == skey:
            c.buf[:] = self._static[1]
        else:
            c.FillRect(0, 0, W, H, Color(*ctx.pal["bg"]))
            reveal(c, ctx, seg, th["reveal"], p, t)
            struct.chrome(c, ctx)                    # after the text, so panels clip anything sliding in
            if p >= 1.0:
                self._static = (skey, c.buf.copy())
        struct.dynamic(c, ctx, seg, p)
        draw_border(c, th["border"], ctx.sev, t)
        if t >= plan["total"]:
            self._t0 = now
            self._static = None
            return "done"
        return None


def _bbox(lines):
    if not lines:
        return (0, 0, 1, 1)
    return (min(l["x0"] for l in lines), min(l["y"] for l in lines),
            max(l["x0"] + l["w"] for l in lines), max(l["y"] + l["h"] for l in lines))


def reveal(c, ctx, seg, mode, p, t):
    lines = seg["lines"]
    if p >= 1.0:
        draw_lines(c, lines)
        return
    bg = ctx.pal["bg"]
    x0, y0, x1, y1 = _bbox(lines)
    if mode == "type":
        total = sum(l["chars"] for l in lines)
        n = int(total * p)
        draw_lines(c, lines, upto=n)
        acc = 0
        for ln in lines:                                     # block cursor at the typing position
            if acc + ln["chars"] >= n:
                x, left = ln["x0"], n - acc
                for run in ln["runs"]:
                    if run[0] == "glyph":
                        continue
                    rx, txt, f, _ = run
                    if left <= len(txt):
                        x = rx + f.width(txt[:left])
                        break
                    left -= len(txt) + 1
                    x = rx + f.width(txt) + f.space
                c.FillRect(x + 1, ln["y"], 5, ln["h"], Color(*ctx.sev))
                break
            acc += ln["chars"]
    elif mode == "lines":
        n = int(len(lines) * p) + 1
        draw_lines(c, lines[:n], flash_last=True)
    elif mode == "wipe":
        draw_lines(c, lines)
        edge = x0 + int((x1 - x0) * _ease(p))
        c.buf[y0:y1, edge:x1] = bg
        c.buf[y0:y1, max(x0, edge - 1):edge + 1] = ctx.sev
    elif mode == "scan":
        draw_lines(c, lines)
        edge = y0 + int((y1 - y0) * _ease(p))
        c.buf[edge:y1, x0:x1] = bg
        a, b = max(y0, edge - 4), max(y0, edge - 1)
        c.buf[a:b, x0:x1] = (c.buf[a:b, x0:x1] * 0.5 + np.array(ctx.sev) * 0.5).astype(np.uint8)
        c.buf[b:edge + 1, x0:x1] = ctx.sev
    elif mode == "decode":
        L = max(1, len(lines))
        draw_lines(c, lines, scramble=(t, lambda li: max(0.0, min(1.0, (p - 0.6 * li / L) / 0.4))))
    elif mode == "sections":
        P = max(1, len(seg["paras"]))

        def off(ln):
            q = (p - 0.75 * ln["para"] / P) / 0.25
            return None if q <= 0 else int((1 - _ease(q)) * 80)
        draw_lines(c, lines, offset=off)
    elif mode == "blinds":
        draw_lines(c, lines)
        rows = np.arange(y0, y1)
        hide = rows[((rows - y0) % 8) >= int(8 * p) + 1]
        c.buf[hide, x0:x1] = bg
    else:   # fade, paragraph by paragraph
        P = max(1, len(seg["paras"]))
        for k in range(P):
            q = max(0.0, min(1.0, (p - 0.6 * k / P) / 0.4))
            if q > 0:
                draw_lines(c, [l for l in lines if l["para"] == k], colour_k=_ease(q))


# ---------------------------------------------------------------------------
# structures
# ---------------------------------------------------------------------------
def U(ctx, s):
    return s.upper() if ctx.upper else s


def title_text(ctx):
    return ("TEST - " if ctx.b["test"] else "") + ctx.b["title"]


def chip(c, x, y, text, f, bg, fg, pad=3):
    w = f.width(text) + 2 * pad
    c.FillRect(x, y, w, f.h + 2, Color(*bg))
    f.draw(c, x + pad, y + 1, fg, text)
    return w


def meter(c, x, y, ctx, cell=5, h=8):
    """Severity meter: 4 blocks, filled up to the alert's level."""
    lvl = alert_brief.SEVERITY_LEVEL.get(ctx.b["kind"], 1)
    for k in range(4):
        c.FillRect(x + k * (cell + 2), y, cell, h, Color(*(ctx.sev if k < lvl else _dim(ctx.sev, 0.18))))
    return 4 * (cell + 2)


def icon(c, ctx, x, y, scale, bg=None):
    rgba = art.icon_rgba(ctx.b["hazard"], ctx.pal["icon"], ctx.sev, bg or ctx.pal["bg"], scale)
    art.blit_rgba(c, rgba, x, y)
    return rgba.shape[1]


def glyph(c, name, x, y, col):
    c.BlitMask(art.glyph_mask(name), x, y, Color(*col))
    return 9


def facts(ctx):
    b = ctx.b
    out = []
    if b["until"]:
        out.append(("clock", "UNTIL " if ctx.upper else "until ", b["until"].upper() if ctx.upper else b["until"]))
    if b["issued"]:
        out.append(("check", "ISSUED " if ctx.upper else "issued ", b["issued"].upper() if ctx.upper else b["issued"]))
    n = len(b["regions"])
    out.append(("pin", "", U(ctx, b["where"] or ctx.area) + (f" ({n} zones)" if n > 1 else "")))
    return out


def fact_line(c, ctx, x, y, f, maxw, centre=False):
    items = facts(ctx)
    if centre:
        total = sum(10 + f.width(a + v) + 12 for _, a, v in items) - 12
        x = x + max(0, (maxw - total) // 2)
    end = x + maxw
    for g, a, v in items:
        if x + 10 + f.width(a + v) > end:
            break
        glyph(c, g, x, y + max(0, (f.h - 7) // 2), ctx.pal["label"])
        x += 10
        x += f.draw(c, x, y, ctx.pal["dim"], a)
        x += f.draw(c, x, y, ctx.pal["strong"], v) + 12
    return x


def _wrap_plain(f, text, width):
    out, cur = [], ""
    for w in text.split():
        cand = (cur + " " + w).strip()
        if cur and f.width(cand) > width:
            out.append(cur)
            cur = w
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


class Structure:
    def inner(self, ctx):
        m = BORDER_W.get(ctx.th["border"], 2) + 3
        return m, m, W - m, H - m

    def title_font(self, ctx, width=None):
        width = width or (W - 2 * self.inner(ctx)[0] - 110)
        tt = title_text(ctx)
        for f in ctx.fam["titles"]:
            if f.width(tt) <= width:
                return f
        return ctx.fam["key"]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        return layout(paras, boxes, fonts, ctx.pal, ctx.upper, gap=gap)

    def dynamic(self, c, ctx, seg, p):
        if ctx.count > 1:
            s = f"{ctx.index + 1}/{ctx.count}"
            f = ctx.fam["small"]
            x0, y0, x1, y1 = self.inner(ctx)
            chip(c, x1 - f.width(s) - 6, y1 - f.h - 2, s, f, ctx.sev, (0, 0, 0), 2)


class Band(Structure):
    """Icon + alert name + status chip + severity meter across the top, facts strip, then the text."""
    columns = 1

    def header_h(self, ctx):
        return max(16, ctx.title_f.h) + 4 + ctx.fam["small"].line + 5

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.header_h(ctx)
        if self.columns == 1:
            return [(x0 + 2, top, x1 - x0 - 4, y1 - top)]
        gap = 12
        cw = (x1 - x0 - 4 - gap) // 2
        return [(x0 + 2, top, cw, y1 - top), (x0 + 2 + cw + gap, top, cw, y1 - top)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.sev, title_text(ctx))
        k = ctx.fam["key"]
        status = (ctx.b["status"] or "in effect").upper()
        meter(c, x1 - 30, y0 + (rowh - 8) // 2, ctx)
        chip(c, x1 - 36 - k.width(status) - 6, y0 + (rowh - k.h - 2) // 2, status, k, ctx.sev, (0, 0, 0))
        y = y0 + rowh + 3
        sf = ctx.fam["small"]
        xx = chip(c, x0 + 2, y, U(ctx, ctx.th["header"]), sf, _dim(ctx.sev, 0.35), ctx.pal["strong"], 3) + x0 + 8
        fact_line(c, ctx, xx, y + 1, sf, x1 - xx)
        y += sf.line + 2
        c.FillRect(x0, y, x1 - x0, 1, Color(*_dim(ctx.sev, 0.6)))
        if self.columns == 2:
            bx = self.boxes(ctx)
            c.FillRect(bx[1][0] - 6, bx[1][1], 1, bx[1][3], Color(*_dim(ctx.sev, 0.3)))


class Columns(Band):
    """Header band with the text flowing through two columns."""
    columns = 2


class Rail(Structure):
    """Left rail: big icon, alert type, severity meter, key amount and times; the text on the right."""
    RW = 112

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + ctx.title_f.line + ctx.fam["small"].line + 4
        return [(x0 + self.RW + 8, top, x1 - x0 - self.RW - 10, y1 - top)]

    def title_font(self, ctx, width=None):
        return super().title_font(ctx, W - 2 * self.inner(ctx)[0] - self.RW - 14)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        rw = self.RW
        rail_bg = _dim(ctx.sev, 0.16)
        c.FillRect(x0, y0, rw, y1 - y0, Color(*rail_bg))
        c.FillRect(x0 + rw, y0, 2, y1 - y0, Color(*ctx.sev))
        cx = x0 + rw // 2
        icon(c, ctx, cx - 24, y0 + 4, 3, rail_bg)
        y = y0 + 54
        k, sf = ctx.fam["key"], ctx.fam["small"]
        kind = ctx.b["kind"].upper()
        k.draw(c, cx - k.width(kind) // 2, y, ctx.sev, kind)
        y += k.line + 1
        meter(c, cx - 14, y, ctx)
        y += 12
        b = ctx.b
        rows = []
        if b["amount"]:
            rows.append((b["amount"].upper(), ctx.pal["amount"], k))
        if b["until"]:
            rows += [("UNTIL", ctx.pal["dim"], sf), (b["until"].upper(), ctx.pal["strong"], k)]
        if b["issued"]:
            rows += [("ISSUED", ctx.pal["dim"], sf), (b["issued"].upper(), ctx.pal["text"], sf)]
        for text, col, f in rows:
            for ln in _wrap_plain(f, text, rw - 8):
                if y + f.h > y1 - 2:
                    break
                f.draw(c, cx - f.width(ln) // 2, y, col, ln)
                y += f.line
            y += 1
        x = x0 + rw + 8
        ctx.title_f.draw(c, x, y0, ctx.sev, title_text(ctx))
        fact_line(c, ctx, x, y0 + ctx.title_f.line, ctx.fam["small"], x1 - x)


class Crawl(Structure):
    """A bright crawl band across the top repeating the essentials, then two columns of text."""

    def band_h(self, ctx):
        return ctx.fam["key"].h + 6

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.band_h(ctx) + 3 + max(16, ctx.title_f.h) + 4
        gap = 12
        cw = (x1 - x0 - 4 - gap) // 2
        return [(x0 + 2, top, cw, y1 - top), (x0 + 2 + cw + gap, top, cw, y1 - top)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        y = y0 + self.band_h(ctx) + 3
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y + (rowh - 16) // 2, 1) + 6
        x += f.draw(c, x, y + (rowh - f.h) // 2, ctx.pal["strong"], title_text(ctx)) + 10
        sf = ctx.fam["small"]
        fact_line(c, ctx, x, y + (rowh - sf.h) // 2, sf, x1 - x)
        bx = self.boxes(ctx)
        c.FillRect(bx[1][0] - 6, bx[1][1], 1, bx[1][3], Color(*_dim(ctx.sev, 0.3)))

    def dynamic(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        bh = self.band_h(ctx)
        k = ctx.fam["key"]
        b = ctx.b
        msg = (f"  /// {title_text(ctx)} IN EFFECT /// {(b['where'] or ctx.area).upper()}"
               + (f" /// UNTIL {b['until'].upper()}" if b["until"] else "") + (f" /// {b['amount'].upper()}" if b["amount"] else ""))
        mw = k.width(msg)
        sub = Canvas(x1 - x0, bh)
        sub.buf[:] = ctx.sev
        xx = -(int(ctx.t * 45) % max(1, mw))
        while xx < x1 - x0:
            k.draw(sub, xx, 3, (0, 0, 0), msg)
            xx += mw
        c.buf[y0:y0 + bh, x0:x1] = sub.buf
        super().dynamic(c, ctx, seg, p)


class Boxes(Structure):
    """Header row, then WHAT / EXPECT / WHAT TO DO in labelled boxes side by side."""
    GROUPS = (("WHAT", ("lead", "detail")), ("EXPECT", ("impact", "regions")), ("WHAT TO DO", ("advice",)))

    def rects(self, ctx):
        weights = getattr(ctx, "box_weights", (5, 3, 3))
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + 4
        avail = x1 - x0 - 2 * 6
        out, x = [], x0
        for w in weights:
            bw = int(avail * w / sum(weights))
            out.append((x, top, bw, y1 - top))
            x += bw + 6
        return out

    def boxes(self, ctx):
        lab = ctx.fam["small"].line + 3
        return [(x + 4, y + lab, w - 8, h - lab - 3) for x, y, w, h in self.rects(ctx)]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        # box widths follow how much text each group has, so a long EXPECT gets room
        sizes = [max(1, sum(len(p["text"]) for p in paras if p["style"] in styles)) for _, styles in self.GROUPS]
        tot = sum(sizes)
        weights = [min(0.55, max(0.2, z / tot)) for z in sizes]
        ctx.box_weights = weights
        boxes = self.boxes(ctx)
        lines, over, base = [], False, 0
        for (label, styles), box in zip(self.GROUPS, boxes):
            group = [p for p in paras if p["style"] in styles]
            if not group:
                continue
            ln, ov = layout(group, [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap)
            for l in ln:
                l["para"] += base
            base += len(group)
            lines += ln
            over = over or ov
        return lines, over

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        x += f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.sev, title_text(ctx)) + 10
        sf = ctx.fam["small"]
        fact_line(c, ctx, x, y0 + (rowh - sf.h) // 2, sf, x1 - x)
        for (label, _), (bx, by, bw, bh) in zip(self.GROUPS, self.rects(ctx)):
            col = Color(*_dim(ctx.sev, 0.55))
            c.FillRect(bx, by, bw, 1, col)
            c.FillRect(bx, by + bh - 1, bw, 1, col)
            c.FillRect(bx, by, 1, bh, col)
            c.FillRect(bx + bw - 1, by, 1, bh, col)
            chip(c, bx, by, U(ctx, label) if not ctx.upper else label, sf, ctx.sev, (0, 0, 0), 4)


class Terminal(Structure):
    """Console feed: tagged lines, monospace, cursor."""

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + ctx.fam["key"].line * 2 + 4
        return [(x0 + 2, top, x1 - x0 - 4, y1 - top)]

    def flow(self, ctx, paras, boxes, fonts, gap=2):
        return layout(paras, boxes, fonts, ctx.pal, ctx.upper, prefix="tag", gap=gap)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        k.draw(c, x0 + 2, y0, ctx.pal["label"], U(ctx, f"> {ctx.th['header']} :: ECCC FEED"))
        x, y = x0 + 2, y0 + k.line
        x += k.draw(c, x, y, ctx.sev, f"[{title_text(ctx)}]") + 8
        b = ctx.b
        for lab, val in (("STATUS", (b["status"] or "in effect").upper()), ("UNTIL", (b["until"] or "-").upper()),
                         ("AREA", (b["where"] or ctx.area).upper())):
            if x + k.width(lab + ":" + val) > x1:
                break
            x += k.draw(c, x, y, ctx.pal["dim"], lab + ":")
            x += k.draw(c, x, y, ctx.pal["strong"], val) + 8
        c.buf[y + k.line + 1, x0:x1:2] = _dim(ctx.pal["label"], 0.6)

    def dynamic(self, c, ctx, seg, p):
        if p >= 1.0 and seg["lines"] and int(ctx.t * 2) % 2 == 0:
            ln = seg["lines"][-1]
            runs = [r for r in ln["runs"] if r[0] != "glyph"]
            if runs:
                last = runs[-1]
                c.FillRect(last[0] + last[2].width(last[1]) + 2, ln["y"], 5, ln["h"], Color(*ctx.pal["strong"]))
        super().dynamic(c, ctx, seg, p)


class Centered(Structure):
    """Icons either side of a centred alert name, centred facts, then full-width text."""

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(32, ctx.title_f.h) + ctx.fam["small"].line + 6
        return [(x0 + 4, top, x1 - x0 - 8, y1 - top)]

    def title_font(self, ctx, width=None):
        return super().title_font(ctx, W - 2 * self.inner(ctx)[0] - 90)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        tt = title_text(ctx)
        rowh = max(32, f.h)
        tw = f.width(tt)
        cx = (x0 + x1) // 2
        f.draw(c, cx - tw // 2, y0 + (rowh - f.h) // 2, ctx.sev, tt)
        icon(c, ctx, max(x0, cx - tw // 2 - 40), y0, 2)
        icon(c, ctx, min(x1 - 32, cx + tw // 2 + 8), y0, 2)
        sf = ctx.fam["small"]
        fact_line(c, ctx, x0, y0 + rowh + 2, sf, x1 - x0, centre=True)
        c.FillRect(cx - 120, y0 + rowh + sf.line + 3, 240, 1, Color(*_dim(ctx.sev, 0.6)))


class FactBar(Structure):
    """Text on the left; a fact column on the right (type, amount, until, issued, area, meter)."""
    FW = 118

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + 5
        return [(x0 + 2, top, x1 - x0 - self.FW - 12, y1 - top)]

    def title_font(self, ctx, width=None):
        return super().title_font(ctx, W - 2 * self.inner(ctx)[0] - self.FW - 34)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.pal["strong"], title_text(ctx))
        c.FillRect(x0, y0 + rowh + 2, x1 - x0 - self.FW - 8, 1, Color(*_dim(ctx.sev, 0.6)))
        fx = x1 - self.FW
        c.FillRect(fx, y0, self.FW, y1 - y0, Color(*ctx.pal["panel"]))
        c.FillRect(fx, y0, 2, y1 - y0, Color(*ctx.sev))
        k, sf = ctx.fam["key"], ctx.fam["small"]
        y = y0 + 4
        b = ctx.b
        chip(c, fx + 8, y, b["kind"].upper(), k, ctx.sev, (0, 0, 0))
        meter(c, x1 - 32, y + 3, ctx)
        y += k.line + 6
        rows = []
        if b["amount"]:
            rows.append(("AMOUNT", b["amount"].upper(), ctx.pal["amount"]))
        rows += [("UNTIL", (b["until"] or "further notice").upper(), ctx.pal["strong"]),
                 ("ISSUED", (b["issued"] or "-").upper(), ctx.pal["text"]),
                 ("AREA", U(ctx, b["where"] or ctx.area), ctx.pal["text"])]
        for lab, val, col in rows:
            if y + sf.h > y1:
                break
            sf.draw(c, fx + 8, y, ctx.pal["dim"], lab)
            y += sf.line
            for ln in _wrap_plain(k, val, self.FW - 14):
                if y + k.h > y1 - 2:
                    break
                k.draw(c, fx + 8, y, col, ln)
                y += k.line
            y += 3


STRUCT_IMPL = {"band": Band(), "columns": Columns(), "rail": Rail(), "crawl": Crawl(), "boxes": Boxes(),
               "terminal": Terminal(), "centered": Centered(), "factbar": FactBar()}


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
    """When each alert's screen starts, how long its reveal and hold last (for the preview scrubber)."""
    scr = AlertScreen(dict(settings or {}), None)
    th = scr.theme()
    alerts = (alerts_data or {}).get("alerts") or scr.alerts({"test": True})
    plan = scr._plan_for(alerts, th, scr.family(th))
    return {"total": round(plan["total"], 2),
            "pages": [{"start": round(x["start"], 2), "reveal": round(x["reveal"], 2), "hold": round(x["hold"], 2),
                       "label": x["ctx"].b["title"], "alert": x["alert"], "font_px": x["fonts"]["reg"].h}
                      for x in plan["segs"]]}


THEME_LIST = [{"id": k, "name": v["name"], "structure": v["structure"], "border": v["border"], "reveal": v["reveal"],
               "palette": v["palette"], "family": v["family"], "description": v["description"], "header": v["header"]}
              for k, v in THEMES.items()]
