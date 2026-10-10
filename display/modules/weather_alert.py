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
                "Metro Vancouver - northeast including Coquitlam, Port Coquitlam, Port Moody, Maple Ridge and Pitt Meadows",
                "Metro Vancouver - southeast including Surrey, White Rock and Langley",
                "Metro Vancouver - southwest including Richmond and Delta"],
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
    """A BDF font at an integer scale with measured metrics. (``faux`` double-striking is not used: it smears
    on the LEDs — bold always means a real bold BDF face.)"""

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
def _words(text, upper, home=""):
    """[(word pieces)] where a piece is (text, highlighted); punctuation glued to the previous word.
    Words of the home city come out with highlight "home"."""
    words = []
    home_words = {w.lower() for w in home.split()} if home else set()
    for chunk, hi in alert_brief.spans(text):
        t = chunk.upper() if upper else chunk
        parts = t.split()
        for k, w in enumerate(parts):
            h = "home" if home_words and w.strip(".,;:()").lower() in home_words else hi
            if k == 0 and words and not t[:1].isspace() and w[0] in PUNCT:
                words[-1].append((w, h))
            else:
                words.append([(w, h)])
    return words


def layout(paras, boxes, fonts, pal, upper, prefix="glyph", gap=3, home=""):
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
                    if hi == "home":
                        runs.append((x, txt, f, (0, 0, 0), pal["home_bg"]))
                    else:
                        runs.append((x, txt, f, pal["amount"] if hi else col))
                    x += f.width(txt)
                    chars += len(txt)
                x += fnt_par.space
                chars += 1
            lines.append({"y": state["cy"], "h": fnt_par.h, "x0": bx, "w": bw, "runs": runs, "para": pi, "chars": chars})
            state["cy"] += fnt_par.line
            return True

        cur, cw, first = [], 0, True
        for pieces in _words(p["text"], upper, home if p["style"] == "regions" else ""):
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
            x, txt, f, col = run[:4]
            if len(run) > 4:                                   # boxed (the home city)
                c.FillRect(x + dx - 1, ln["y"] - 1, f.width(txt) + 2, f.h + 2, Color(*_dim(run[4], colour_k)))
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


BORDER_W = {"chase": 2, "pulse": 2, "hazard": 5, "double": 4, "lights": 3, "comet": 2, "corners": 3, "glow": 3, "rails": 5,
            "bevel": 4, "notch": 3, "textloop": 9, "scanbars": 5}
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
    elif kind == "bevel":
        hi, lo = np.clip(sev * 1.0 + 40, 0, 255).astype(np.uint8), (sev * 0.3).astype(np.uint8)
        for i in range(4):
            c.buf[i, i:W - i] = hi if i < 2 else lo
            c.buf[i:H - i, i] = hi if i < 2 else lo
            c.buf[H - 1 - i, i:W - i] = lo if i < 2 else hi
            c.buf[i:H - i, W - 1 - i] = lo if i < 2 else hi
        g = int(t * 120) % (W + 60) - 30                       # a glint sliding along the top edge
        a, b = max(0, g), min(W, g + 24)
        if b > a:
            c.buf[0:2, a:b] = (255, 255, 255)
    elif kind == "notch":
        col = (sev * 0.55).astype(np.uint8)
        ys, xs = _perimeter(1)
        keep = ~(((xs < 9) | (xs > W - 10)) & ((ys < 9) | (ys > H - 10)))
        c.buf[ys[keep], xs[keep]] = col
        for d in range(9):                                     # chamfered corners
            for (x, y) in ((1 + d, 9 - d), (W - 2 - d, 9 - d), (1 + d, H - 10 + d), (W - 2 - d, H - 10 + d)):
                c.buf[y, x] = sev.astype(np.uint8)
        n = (W // 16)
        lit = int(t * 8) % n
        for k in range(1, n):
            x = k * 16
            col2 = (255, 255, 255) if k == lit else tuple(int(v) for v in sev)
            c.buf[0:3, x] = col2
            c.buf[H - 3:H, W - x] = col2
    elif kind == "textloop":
        f = FI("alert/5x7.bdf")
        msg = "  WARNING  ▪  ALERT  ▪ "
        msg = msg.replace("▪", "*")
        mw = f.width(msg)
        c.FillRect(0, 0, W, 9, Color(*(sev * 0.25).astype(np.uint8)))
        c.FillRect(0, H - 9, W, 9, Color(*(sev * 0.25).astype(np.uint8)))
        off = int(t * 30) % mw
        x = -off
        while x < W:
            f.draw(c, x, 1, sev.astype(np.uint8), msg)
            f.draw(c, W - x - mw, H - 8, sev.astype(np.uint8), msg)
            x += mw
        c.FillRect(0, 0, 2, H, Color(*sev.astype(np.uint8)))
        c.FillRect(W - 2, 0, 2, H, Color(*sev.astype(np.uint8)))
    elif kind == "scanbars":
        for y0 in (0, H - 4):
            c.FillRect(0, y0, W, 4, Color(*(sev * 0.35).astype(np.uint8)))
        x = int(t * 140) % (W + 80) - 40
        for y0 in (0, H - 4):
            a, b = max(0, x), min(W, x + 40)
            if b > a:
                ramp = np.linspace(0.3, 1.0, b - a)[:, None]
                c.buf[y0:y0 + 4, a:b] = (sev[None, :] * ramp).clip(0, 255).astype(np.uint8)[None, :, :]
        for xx in (0, W - 2):
            c.buf[4:22, xx:xx + 2] = sev.astype(np.uint8)
            c.buf[H - 22:H - 4, xx:xx + 2] = sev.astype(np.uint8)
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
DENSITIES = {"dense": "Dense", "balanced": "Balanced", "minimal": "Minimal"}
_SQ = []


def _squeezed():
    if not _SQ:
        _SQ.append((FI("alert/u8g2_squeezed_regular_6.bdf"), FI("alert/u8g2_squeezed_bold_6.bdf")))
    return _SQ[0]
_DENSITY_ALIASES = {"max": "dense", "normal": "balanced", "large": "minimal"}


def density_of(settings):
    d = (settings or {}).get("density") or "balanced"
    return _DENSITY_ALIASES.get(d, d) if _DENSITY_ALIASES.get(d, d) in DENSITIES else "balanced"


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
        self._sample = None          # the test alert, frozen per showing (its times must not change per frame)
        self._fast = None            # (source identity, plan) so unchanged data skips re-hashing
        self._tour = None            # theme id while touring all themes (entry {"tour": true})
        self._tour_t0 = None
        self._reveal_cap = None

    # -- data ------------------------------------------------------------------
    def alerts(self, entry=None):
        if entry and entry.get("test"):
            from display.modules.alert_samples import SAMPLES
            kind = self.settings.get("sample") or "rain"
            if self._sample is None or self._sample[0] != kind:
                now = time.time()
                base = SAMPLES.get(kind) or SAMPLES["rain"]
                self._sample = (kind, [dict(base, code="TEST", test=True, status="issued", published=now - 600,
                                            expires=now + 12 * 3600, event_end=now + 20 * 3600)])
            return self._sample[1]
        data = (self.loader.get_data("alerts") if self.loader else None) or {}
        now = time.time()
        key = (id(data), int(now // 30))              # same list object until new data or 30 s pass
        if getattr(self, "_alerts_cache", None) and self._alerts_cache[0] == key:
            return self._alerts_cache[1]
        if now - float(data.get("updated_utc") or 0) > 3 * 3600:       # hub down: don't show stale alerts
            out = []
        else:
            out = [a for a in data.get("alerts") or [] if not a.get("expires") or a["expires"] > now]
            out.sort(key=lambda a: not alert_brief.mentions(a, self.home))     # stable: the wall's own city first
        self._alerts_cache = (key, out)
        return out

    def active(self):
        return bool(self.alerts())

    @property
    def home(self):
        return (self.settings.get("home_city") or "Richmond").strip()

    def area(self):
        data = (self.loader.get_data("alerts") if self.loader else None) or {}
        return data.get("area") or "Metro Vancouver"

    # -- theme -----------------------------------------------------------------
    def theme(self):
        st = self.settings
        if self._tour:
            return dict(resolve(self._tour))
        th = dict(resolve(st.get("theme") or "t01"))
        for key, allowed in (("palette", PALETTES), ("family", FAMILIES), ("border", BORDERS), ("reveal", REVEALS),
                             ("structure", STRUCTURES)):
            if st.get(key) in allowed:
                th[key] = st[key]
        if st.get("header") or st.get("title"):
            th["header"] = st.get("header") or st["title"]
        if st.get("upper") in (True, False):
            th["upper"] = st["upper"]
        if st.get("pulse") in (True, False):
            th["pulse"] = st["pulse"]
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
                   # real bold faces only: a size without one highlights by colour, never by double-striking
                   "ladder": [(FI(r, s), FI(b, s) if b else FI(r, s)) for r, b, s in fam["ladder"]]}
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
            ctx.home_name = self.home
            ctx.home = alert_brief.mentions(a, self.home)
            ctx.header = (f"INCLUDES {self.home.upper()}" if ctx.home else f"NEARBY · NOT {self.home.upper()}") \
                if self.home else th["header"]
            ctx.pal = {k: (sev if v == "sev" else v) for k, v in pal.items() if k != "name"}
            ctx.pal["home_bg"] = sev
            ctx.area = "Metro Vancouver" if b["test"] else self.area()
            ctx.title_f = struct.title_font(ctx)
            b["_issued_ts"], b["_expires_ts"] = a.get("published"), a.get("expires")
            # density is a global choice (Dense / Balanced / Minimal) applied to whichever design is shown
            density = density_of(st)
            if density == "minimal":
                paras = [p for p in alert_brief.paragraphs(b, "essentials") if p["style"] in ("lead", "advice", "regions")]
            else:
                paras = alert_brief.paragraphs(b, level)
            if struct.lists_cities:
                paras = [p for p in paras if p["style"] != "regions"]
            boxes = struct.boxes(ctx)
            if density == "dense":
                # smaller sizes first, ending in an extra-compact (real bold) face so more of the text stays
                ladder = (fam["ladder"][1:] if len(fam["ladder"]) > 1 else list(fam["ladder"])) + [_squeezed()]
            elif density == "minimal":
                ladder = [(fam["key"], fam["key"])] + list(fam["ladder"])
            else:
                ladder = fam["ladder"]
            chosen = None
            def with_area(ps, lvl):
                out = []
                for p in ps:
                    if p["style"] == "regions":
                        items = (b.get("areas") or [[]] * 4)[lvl] or [b["where"] or ctx.area]
                        p = dict(p, text=(" · " if lvl == 0 else "; ").join(items))
                    out.append(p)
                return out
            variants = [with_area(paras, 0), with_area(paras, 1), with_area(paras, 2),
                        with_area([p for p in paras if p["style"] != "detail"], 2),
                        with_area([p for p in paras if p["style"] not in ("detail", "impact")], 2)]
            for use in variants:
                for reg, bold in ladder:
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
                use = variants[-1]
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
            if self._reveal_cap:
                reveal_t = min(reveal_t, self._reveal_cap)
            hold = max(min_hold, 2.0 + words / wps)
            segs.append({"alert": ai, "ctx": ctx, "lines": lines, "paras": use, "fonts": fonts, "start": t,
                         "reveal": reveal_t, "hold": hold})
            t += reveal_t + hold
            amap = a.get("map") or (self._sample_map() if b["test"] else None)
            if amap and st.get("show_map", True) is not False and self._reveal_cap is None:
                msec = max(4.0, float(st.get("map_sec") or 9))
                segs.append({"alert": ai, "ctx": ctx, "kind": "map", "map": amap, "lines": [], "paras": [],
                             "fonts": fonts, "start": t, "reveal": 1.4, "hold": msec - 1.4})
                t += msec
        self._plan_key, self._plan = key, {"segs": segs, "total": t}
        self._static = None
        return self._plan

    # -- drawing ---------------------------------------------------------------
    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        if entry and entry.get("tour"):
            return self._draw_tour(c, entry, transition, now)
        if self._tour:
            self._tour, self._reveal_cap, self._fast, self._static = None, None, None, None
        return self._draw(c, entry, transition, now)

    def _sample_map(self):
        data = (self.loader.get_data("alerts") if self.loader else None) or {}
        return (data.get("sample_maps") or {}).get(self.settings.get("sample") or "rain") or data.get("sample_map")

    def _draw_map(self, c, seg, ctx, local, p):
        """The affected zones pulsing in the severity colour over the map; the home city marked."""
        if not hasattr(self, "_maps"):
            self._maps = _MapCache()
        data_dir = getattr(self.loader, "data_dir", None) or self.settings.get("_data_dir")
        meta = seg["map"]
        if not data_dir or not self._maps.get(data_dir, meta):
            return False
        mc = self._maps
        sev = np.array(ctx.sev, dtype=np.int16)
        reach = _ease(p) * 520                                  # zones flood outward from the home city
        m = mc.mask & (mc.dist <= reach)
        k = 0.34 + 0.16 * math.sin(ctx.t * 3.2)
        out = mc.base.copy()
        out[m] = (out[m] * (1 - k) + sev * k).astype(np.int16)
        e = mc.edge & (mc.dist <= reach)
        out[e] = sev
        c.buf[:] = out.clip(0, 255).astype(np.uint8)
        f, sf, k_f = ctx.title_f, ctx.fam["small"], ctx.fam["key"]
        # top band: what and how bad
        band = max(16, f.h) + 4
        c.buf[0:band + sf.line + 3] = (c.buf[0:band + sf.line + 3] * 0.25).astype(np.uint8)
        x = 8 + icon(c, ctx, 8, 2 + (band - 16) // 2, 1, (0, 0, 0)) + 6
        x += f.draw(c, x, 2 + (band - f.h) // 2, ctx.sev, title_text(ctx)) + 10
        lab = U(ctx, "AFFECTED AREA")
        chip(c, x, 2 + (band - k_f.h - 2) // 2, lab, k_f, ctx.sev, (0, 0, 0))
        b = ctx.b
        info = U(ctx, " · ".join(v for v in (f"until {b['until']}" if b["until"] else "", b["amount"]) if v))
        sf.draw(c, 8, band + 1, ctx.pal["strong"], info)
        meter(c, W - 38, 2 + (band - 8) // 2, ctx)
        # legend + map name, bottom left
        lg = U(ctx, f"{b['kind']} zone")
        yb = H - sf.h - 8
        c.FillRect(8, yb, 10, sf.h, Color(*ctx.sev))
        c.buf[yb - 2:yb + sf.h + 2, 22:26 + sf.width(lg) + 6] = (c.buf[yb - 2:yb + sf.h + 2, 22:26 + sf.width(lg) + 6] * 0.3).astype(np.uint8)
        sf.draw(c, 24, yb, (255, 255, 255), lg)
        # home marker: pulsing ring + label
        hp = meta.get("home_px")
        if hp:
            hx, hy = hp
            r = 3 + int(4 * (0.5 + 0.5 * math.sin(ctx.t * 4)))
            for a in np.linspace(0, 2 * math.pi, 40, endpoint=False):
                px, py = int(hx + r * math.cos(a)), int(hy + r * math.sin(a))
                if 0 <= px < W and 0 <= py < H:
                    c.buf[py, px] = (255, 255, 255)
            c.FillRect(hx - 1, hy - 1, 3, 3, Color(255, 255, 255))
            name = U(ctx, ctx.home_name or "Home")
            txt = name if ctx.home else f"{name} · not in this alert"
            tw = k_f.width(txt)
            lx = hx + 12 if hx + 12 + tw + 6 < W else hx - 12 - tw - 6
            ly = max(band + sf.line + 6, min(H - k_f.h - 6, hy - k_f.h // 2))
            bg = ctx.sev if ctx.home else (20, 90, 40)
            c.FillRect(lx - 3, ly - 2, tw + 6, k_f.h + 4, Color(*bg))
            k_f.draw(c, lx, ly, (0, 0, 0) if ctx.home else (230, 255, 230), txt)
        return True

    def _draw_tour(self, c, entry, transition, now):
        """Every theme in turn with the test alert: its reveal, then a few seconds of border animation."""
        per = max(4.0, float(entry.get("tour_sec") or 9))
        ids = sorted(THEMES)
        if transition or self._tour_t0 is None:
            self._tour_t0 = now
            self._sample = None
        k = int((now - self._tour_t0) // per)
        tid = ids[(int(entry.get("tour_from") or 0) + k) % len(ids)]
        if tid != self._tour:
            self._tour, self._reveal_cap = tid, per * 0.55
            self._t0, self._static, self._fast = self._tour_t0 + k * per, None, None
        self._draw(c, {"test": True}, False, now)
        th = THEMES[tid]
        f = FI("alert/5x8.bdf")
        label = f" THEME {tid[1:]}/{len(ids)}  {th['name'].upper()}  ·  {th['description']} "
        w = min(W - 8, f.width(label))
        c.FillRect(4, H - 13, w + 2, 10, Color(0, 0, 0))
        f.draw(c, 5, H - 12, (255, 255, 255), label)
        left = per - ((now - self._tour_t0) % per)
        c.FillRect(4, H - 3, int((W - 8) * (1 - left / per)), 1, Color(255, 255, 255))
        return "done" if k >= len(ids) else None

    def _draw(self, c, entry, transition, now):
        alerts = self.alerts(entry)
        th = self.theme()
        pal = PALETTES.get(th["palette"]) or PALETTES["black"]
        if transition or self._t0 is None:
            self._t0 = now
            self._static = None
            if transition:
                self._sample = None
        if not alerts:
            c.FillRect(0, 0, W, H, Color(*pal["bg"]))
            return "done" if now - self._t0 > 5 else None
        src = (id(alerts), len(alerts), json.dumps(th, sort_keys=True), json.dumps(self.settings, sort_keys=True, default=str))
        if self._fast is not None and self._fast[0] == src:
            plan = self._fast[1]
        else:
            plan = self._plan_for(alerts, th, self.family(th))
            self._fast = (src, plan)
        t = now - self._t0
        segs = plan["segs"]
        i = 0
        while i + 1 < len(segs) and t >= segs[i + 1]["start"]:
            i += 1
        seg = segs[i]
        ctx = seg["ctx"]
        ctx.t, ctx.now = t, now
        ctx.index, ctx.count = seg["alert"], len({x["alert"] for x in segs})
        local = t - seg["start"]
        p = min(1.0, local / seg["reveal"]) if seg["reveal"] > 0 else 1.0
        if seg.get("kind") == "map" and self._draw_map(c, seg, ctx, local, p):
            draw_border(c, th["border"], ctx.sev if ctx.home or not ctx.home_name else _dim(ctx.sev, 0.5), t)
            if t >= plan["total"]:
                self._t0 = now
                self._static = None
                return "done"
            return None
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
        draw_border(c, th["border"], ctx.sev if ctx.home or not ctx.home_name else _dim(ctx.sev, 0.5), t)
        if t >= plan["total"]:
            self._t0 = now
            self._static = None
            return "done"
        return None


class _MapCache:
    """Map layers for the alert screen, loaded once per file (mtime-checked)."""

    def __init__(self):
        self.key = None
        self.base = self.mask = self.edge = self.dist = None

    def get(self, data_dir, meta):
        import os
        fb = os.path.join(data_dir, meta["file"])
        fm = os.path.join(data_dir, meta["mask"])
        try:
            key = (fb, os.path.getmtime(fb), os.path.getmtime(fm))
        except OSError:
            return False
        if key != self.key:
            w, h = meta["w"], meta["h"]
            self.base = np.fromfile(fb, dtype=np.uint8).reshape(h, w, 3).astype(np.int16)
            m = np.fromfile(fm, dtype=np.uint8).reshape(h, w) > 127
            self.mask = m
            pad = np.pad(m, 1)
            self.edge = m & ~(pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:])
            hx, hy = meta.get("home_px") or (w // 2, h // 2)
            yy, xx = np.mgrid[0:h, 0:w]
            self.dist = np.hypot(xx - hx, yy - hy)
            self.key = key
        return True


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
                    rx, txt, f = run[0], run[1], run[2]
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
    elif mode == "cascade":
        L = max(1, len(lines))

        def drop(ln):
            li = lines.index(ln)
            q = (p - 0.7 * li / L) / 0.3
            return None if q <= 0 else 0
        for li, ln in enumerate(lines):
            q = (p - 0.7 * li / L) / 0.3
            if q <= 0:
                continue
            dy = -int((1 - _ease(q)) * 10)
            moved = dict(ln, y=ln["y"] + dy)
            draw_lines(c, [moved], colour_k=0.4 + 0.6 * _ease(q))
    elif mode == "sweep":
        draw_lines(c, lines)
        edge = x0 + int((x1 - x0 + 40) * _ease(p)) - 20
        if edge < x1:
            a = max(x0, edge)
            c.buf[y0:y1, a:x1] = (c.buf[y0:y1, a:x1] * 0.28).astype(np.uint8)
        e0, e1 = max(x0, edge - 18), max(x0, min(x1, edge))
        if e1 > e0:
            band = c.buf[y0:y1, e0:e1].astype(np.int16)
            lit = band.max(axis=2, keepdims=True) > 40
            c.buf[y0:y1, e0:e1] = np.where(lit, 255, band).astype(np.uint8)
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
    near = "" if ctx.home or not getattr(ctx, "home_name", "") else "NEARBY · "
    return ("TEST - " if ctx.b["test"] else "") + near + ctx.b["title"]


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
    out.append(("pin", "", None))                              # area text is fitted to the space left
    return out


def _levels(ctx):
    lv = ctx.b.get("areas") or [[], [], [], []]
    if not any(lv):
        name = ctx.b["where"] or ctx.area
        lv = [[name], [name], [name], [name]]
    return lv


def draw_home_text(c, ctx, f, x, y, text, col):
    """Draw ``text``; every occurrence of the home city gets a bright box (black text on the severity colour)."""
    name = getattr(ctx, "home_name", "")
    if not name or name.lower() not in text.lower():
        return f.draw(c, x, y, col, text)
    x0 = x
    low, n = text.lower(), name.lower()
    i = 0
    while True:
        j = low.find(n, i)
        if j < 0:
            x += f.draw(c, x, y, col, text[i:])
            break
        x += f.draw(c, x, y, col, text[i:j])
        word = text[j:j + len(n)]
        w = f.width(word)
        c.FillRect(x - 1, y - 1, w + 3, f.h + 2, Color(*ctx.sev))
        f.draw(c, x, y, (0, 0, 0), word)
        x += w + 1
        i = j + len(n)
    return x - x0


def area_line(ctx, f, maxw, label_only_ok=True):
    """The affected area as one line: every city if it fits, else zones with their key cities, else merged
    zones with key cities, else (strips only) zone names. Never '+N'."""
    lv = _levels(ctx)
    tries = [", ".join(lv[0]), "; ".join(lv[1]), "; ".join(lv[2])] + (["; ".join(lv[3])] if label_only_ok else [])
    for t in tries:
        t = U(ctx, t)
        if f.width(t) <= maxw:
            return t
    return U(ctx, tries[-1])


def city_fits(ctx, f, w, h, levels=(0, 1, 2)):
    """Would one of these levels of the area list fit in w x h with font f?"""
    for items in [_levels(ctx)[i] for i in levels]:
        if sum(len(_wrap_plain(f, U(ctx, it), w - 10) or [""]) for it in items) * f.line <= h:
            return True
    return False


def city_list(c, ctx, x, y, w, y1, f, col=None, glyph_col=None, fonts=None):
    """The affected area one item per line (pin glyph each), wrapping long items with a hanging indent.
    Uses the most detailed level that fits the space, trying smaller fonts before less detail."""
    lv = _levels(ctx)
    cands = []
    # detail first: every city in a smaller font beats zone names in a bigger one
    for items in (lv[0], lv[1], lv[2]):
        for ff in [f] + [g for g in (fonts or []) if g is not f]:
            rows = [(_wrap_plain(ff, U(ctx, it), w - 10) or [""]) for it in items]
            if sum(len(r) for r in rows) * ff.line <= y1 - y:
                cands.append((ff, rows))
                break
        if cands:
            break
    if not cands:                                              # nothing fits: smallest font, merged level, cut
        ff = (fonts or [f])[-1]
        cands.append((ff, [_wrap_plain(ff, U(ctx, it), w - 10) for it in lv[2]]))
    ff, rows = cands[0]
    for wrapped in rows:
        for k, ln in enumerate(wrapped):
            if y + ff.h > y1:
                return y
            if k == 0:
                glyph(c, "pin", x, y + max(0, (ff.h - 7) // 2), glyph_col or ctx.pal["label"])
            # "Metro Vancouver (Central): Vancouver, …" — zone in dim, cities bright
            if k == 0 and ": " in ln:
                zone, _, rest = ln.partition(": ")
                xx = x + 10 + ff.draw(c, x + 10, y, ctx.pal["dim"], zone + ":")
                draw_home_text(c, ctx, ff, xx + ff.space, y, rest, col or ctx.pal["strong"])
            else:
                draw_home_text(c, ctx, ff, x + 10, y, ln, col or ctx.pal["strong"])
            y += ff.line
    return y


def fact_line(c, ctx, x, y, f, maxw, centre=False):
    items = facts(ctx)
    if centre:
        items = [(g, a, v if v is not None else area_line(ctx, f, maxw // 2)) for g, a, v in items]
        total = sum(10 + f.width(a + v) + 12 for _, a, v in items) - 12
        x = x + max(0, (maxw - total) // 2)
    end = x + maxw
    for g, a, v in items:
        if v is None:
            v = area_line(ctx, f, end - x - 10 - f.width(a))
        if x + 10 + f.width(a + v) > end:
            break
        glyph(c, g, x, y + max(0, (f.h - 7) // 2), ctx.pal["label"])
        x += 10
        x += f.draw(c, x, y, ctx.pal["dim"], a)
        x += draw_home_text(c, ctx, f, x, y, v, ctx.pal["strong"]) + 12
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
        return layout(paras, boxes, fonts, ctx.pal, ctx.upper, gap=gap, home=ctx.home_name)

    lists_cities = False

    def dynamic(self, c, ctx, seg, p):
        if p >= 1.0 and ctx.th.get("pulse"):                 # key facts (amounts) glow every couple of seconds
            k = max(0.0, math.sin(ctx.t * 2.4)) ** 3
            if k > 0.02:
                amt = tuple(ctx.pal["amount"])
                col = tuple(int(a + (255 - a) * k) for a in amt)
                for ln in seg["lines"]:
                    for run in ln["runs"]:
                        if run[0] != "glyph" and tuple(run[3]) == amt:
                            run[2].draw(c, run[0], ln["y"], col, run[1])  # noqa
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
        xx = chip(c, x0 + 2, y, U(ctx, ctx.header), sf, _dim(ctx.sev, 0.35), ctx.pal["strong"], 3) + x0 + 8
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
    """Left rail: big icon, alert type, severity meter, key amount, expiry and the cities; text on the right."""
    RW = 112
    lists_cities = True

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
        k, sf = ctx.fam["key"], ctx.fam["small"]
        b = ctx.b
        rows = []
        if b["amount"]:
            rows.append((b["amount"].upper(), ctx.pal["amount"], k))
        if b["until"]:
            rows += [("UNTIL", ctx.pal["dim"], sf), (b["until"].upper(), ctx.pal["strong"], k)]
        rows_h = sum(len(_wrap_plain(f, tx, rw - 8)) * f.line + 1 for tx, _, f in rows)
        # make room for the whole area list: smaller icon, then no meter, then a smaller font
        tiny = FI("alert/4x6.bdf")
        plan = None
        # every city if at all possible (shrinking the icon and font), then zones with key cities, then merged
        for level in (0, 1, 2):
            for scale, show_meter, lf in ((3, True, sf), (2, True, sf), (2, False, sf), (2, False, tiny), (1, False, tiny)):
                top_h = 16 * scale + 6 + k.line + 1 + (12 if show_meter else 2)
                avail = (y1 - 2) - (y0 + top_h + rows_h + 5)
                if city_fits(ctx, lf, rw - 10, avail, (level,)):
                    plan = (scale, show_meter, lf)
                    break
            if plan:
                break
        scale, show_meter, lf = plan or (1, False, tiny)
        iw = 16 * scale
        icon(c, ctx, cx - iw // 2, y0 + 4, scale, rail_bg)
        y = y0 + 4 + iw + 2
        kind = ctx.b["kind"].upper()
        k.draw(c, cx - k.width(kind) // 2, y, ctx.sev, kind)
        y += k.line + 1
        if show_meter:
            meter(c, cx - 14, y, ctx)
            y += 12
        else:
            y += 2
        for text, col, f in rows:
            for ln in _wrap_plain(f, text, rw - 8):
                if y + f.h > y1 - 2:
                    break
                f.draw(c, cx - f.width(ln) // 2, y, col, ln)
                y += f.line
            y += 1
        c.FillRect(x0 + 8, y + 1, rw - 16, 1, Color(*_dim(ctx.sev, 0.5)))
        city_list(c, ctx, x0 + 6, y + 4, rw - 10, y1 - 2, lf, fonts=[tiny])
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
        msg = (f"  /// {title_text(ctx)} IN EFFECT /// {', '.join(_levels(ctx)[0]).upper()}"
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
            ln, ov = layout(group, [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap, home=ctx.home_name)
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
        return layout(paras, boxes, fonts, ctx.pal, ctx.upper, prefix="tag", gap=gap, home=ctx.home_name)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        k = ctx.fam["key"]
        k.draw(c, x0 + 2, y0, ctx.pal["label"], U(ctx, f"> {ctx.header} :: ECCC FEED"))
        x, y = x0 + 2, y0 + k.line
        x += k.draw(c, x, y, ctx.sev, f"[{title_text(ctx)}]") + 8
        b = ctx.b
        for lab, val in (("STATUS", (b["status"] or "in effect").upper()), ("UNTIL", (b["until"] or "-").upper()),
                         ("AREA", None)):
            if val is None:
                val = area_line(ctx, k, x1 - x - k.width(lab + ":")).upper()
            if x + k.width(lab + ":" + val) > x1:
                break
            x += k.draw(c, x, y, ctx.pal["dim"], lab + ":")
            x += draw_home_text(c, ctx, k, x, y, val, ctx.pal["strong"]) + 8
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
    """Text on the left; a fact column on the right (type, amount, until, issued, cities, meter)."""
    FW = 118
    lists_cities = True

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
                 ("ISSUED", (b["issued"] or "-").upper(), ctx.pal["text"])]
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
        if y + sf.line * 2 < y1:
            sf.draw(c, fx + 8, y, ctx.pal["dim"], "AREAS")
            city_list(c, ctx, fx + 8, y + sf.line, self.FW - 12, y1 - 2, sf, fonts=[FI("alert/4x6.bdf")])


class CitiesRail(Structure):
    """Text on the left; a right rail listing every affected city, one per line, under the expiry."""
    RW = 104
    lists_cities = True

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + ctx.fam["small"].line + 7
        return [(x0 + 2, top, x1 - x0 - self.RW - 12, y1 - top)]

    def title_font(self, ctx, width=None):
        return super().title_font(ctx, W - 2 * self.inner(ctx)[0] - self.RW - 70)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.sev, title_text(ctx))
        meter(c, x1 - self.RW - 40, y0 + (rowh - 8) // 2, ctx)
        sf, k = ctx.fam["small"], ctx.fam["key"]
        y = y0 + rowh + 2
        xx = chip(c, x0 + 2, y, U(ctx, ctx.header), sf, _dim(ctx.sev, 0.35), ctx.pal["strong"], 3) + x0 + 8
        b = ctx.b
        if b["amount"]:
            xx += sf.draw(c, xx, y + 1, ctx.pal["amount"], U(ctx, b["amount"])) + 10
        if b["issued"]:
            sf.draw(c, xx, y + 1, ctx.pal["dim"], U(ctx, f"issued {b['issued']}"))
        c.FillRect(x0, y + sf.line + 3, x1 - x0 - self.RW - 8, 1, Color(*_dim(ctx.sev, 0.6)))
        rx = x1 - self.RW
        c.FillRect(rx, y0, self.RW, y1 - y0, Color(*ctx.pal["panel"]))
        c.FillRect(rx, y0, 2, y1 - y0, Color(*ctx.sev))
        yy = y0 + 4
        sf.draw(c, rx + 8, yy, ctx.pal["dim"], "IN EFFECT UNTIL")
        yy += sf.line
        for ln in _wrap_plain(k, (b["until"] or "further notice").upper(), self.RW - 12):
            k.draw(c, rx + 8, yy, ctx.pal["strong"], ln)
            yy += k.line
        yy += 3
        c.FillRect(rx + 8, yy, self.RW - 16, 1, Color(*_dim(ctx.sev, 0.5)))
        yy += 4
        sf.draw(c, rx + 8, yy, ctx.pal["label"], "AFFECTED")
        city_list(c, ctx, rx + 6, yy + sf.line + 1, self.RW - 10, y1 - 2, k if k.h <= 13 else sf, fonts=[sf, FI("alert/4x6.bdf")])


class DualRail(Structure):
    """Two sidebars: hazard, level and amount on the left; cities on the right; the message in the middle."""
    LW, RW = 92, 96
    lists_cities = True

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + 5
        return [(x0 + self.LW + 8, top, x1 - x0 - self.LW - self.RW - 16, y1 - top)]

    def title_font(self, ctx, width=None):
        return super().title_font(ctx, W - 2 * self.inner(ctx)[0] - self.LW - self.RW - 20)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        lw, rw = self.LW, self.RW
        lb = _dim(ctx.sev, 0.16)
        c.FillRect(x0, y0, lw, y1 - y0, Color(*lb))
        c.FillRect(x0 + lw, y0, 1, y1 - y0, Color(*ctx.sev))
        c.FillRect(x1 - rw, y0, rw, y1 - y0, Color(*ctx.pal["panel"]))
        c.FillRect(x1 - rw - 1, y0, 1, y1 - y0, Color(*ctx.sev))
        cx = x0 + lw // 2
        icon(c, ctx, cx - 16, y0 + 4, 2, lb)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        y = y0 + 40
        kind = ctx.b["kind"].upper()
        k.draw(c, cx - k.width(kind) // 2, y, ctx.sev, kind)
        y += k.line
        meter(c, cx - 14, y, ctx)
        y += 12
        b = ctx.b
        for text, col, f in ([(b["amount"].upper(), ctx.pal["amount"], k)] if b["amount"] else []) + \
                [("UNTIL", ctx.pal["dim"], sf), ((b["until"] or "-").upper(), ctx.pal["strong"], k)]:
            for ln in _wrap_plain(f, text, lw - 8):
                if y + f.h > y1:
                    break
                f.draw(c, cx - f.width(ln) // 2, y, col, ln)
                y += f.line
            y += 1
        tx = x0 + lw + 8
        f = ctx.title_f
        f.draw(c, tx, y0 + (max(16, f.h) - f.h) // 2, ctx.pal["strong"], title_text(ctx))
        c.FillRect(tx, y0 + max(16, f.h) + 2, x1 - rw - tx - 8, 1, Color(*_dim(ctx.sev, 0.6)))
        rx = x1 - rw + 6
        sf.draw(c, rx, y0 + 4, ctx.pal["label"], "AREAS")
        city_list(c, ctx, rx, y0 + 4 + sf.line + 2, rw - 10, y1 - 2, k if k.h <= 13 else sf, fonts=[sf, FI("alert/4x6.bdf")])


class KeyFacts(Structure):
    """A row of key-fact tiles (amount · until · where · level) above two columns of text."""

    def tiles_h(self, ctx):
        return ctx.fam["small"].line + ctx.fam["key"].line + 8

    def top(self, ctx):
        x0, y0, _, _ = self.inner(ctx)
        return y0 + max(16, ctx.title_f.h) + 4 + self.tiles_h(ctx) + 5

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = self.top(ctx)
        gap = 12
        cw = (x1 - x0 - 4 - gap) // 2
        return [(x0 + 2, top, cw, y1 - top), (x0 + 2 + cw + gap, top, cw, y1 - top)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.sev, title_text(ctx))
        k, sf = ctx.fam["key"], ctx.fam["small"]
        status = (ctx.b["status"] or "in effect").upper()
        chip(c, x1 - k.width(status) - 8, y0 + (rowh - k.h - 2) // 2, status, k, ctx.sev, (0, 0, 0))
        b = ctx.b
        tiles = [("AMOUNT", U(ctx, b["amount"]) if b["amount"] else b["kind"].upper(), ctx.pal["amount"]),
                 ("UNTIL", U(ctx, b["until"] or "further notice"), ctx.pal["strong"]),
                 ("WHERE", None, ctx.pal["strong"]),
                 ("LEVEL", b["kind"].upper(), ctx.sev)]
        ty = y0 + rowh + 4
        th = self.tiles_h(ctx)
        widths = [0.30, 0.22, 0.30, 0.18]
        tx = x0
        avail = x1 - x0 - 3 * 5
        for (lab, val, col), wf in zip(tiles, widths):
            tw = int(avail * wf)
            c.FillRect(tx, ty, tw, th, Color(*ctx.pal["panel"]))
            c.FillRect(tx, ty, tw, 2, Color(*ctx.sev))
            sf.draw(c, tx + 4, ty + 4, ctx.pal["dim"], lab)
            if val is None:
                val = area_line(ctx, k, tw - 8, label_only_ok=False)
                if k.width(val) > tw - 8:
                    val = area_line(ctx, sf, tw - 8)
            fv = k if k.width(val) <= tw - 8 else sf
            while fv.width(val) > tw - 8 and len(val) > 4:          # last resort in a narrow tile
                val = val[:-4].rstrip(" ,;") + "..."
            draw_home_text(c, ctx, fv, tx + 4, ty + 4 + sf.line, val, col)
            if lab == "LEVEL":
                meter(c, tx + tw - 32, ty + 4, ctx, 4, 6)
            tx += tw + 5
        bx = self.boxes(ctx)
        c.FillRect(bx[1][0] - 6, bx[1][1], 1, bx[1][3], Color(*_dim(ctx.sev, 0.3)))


class Spotlight(Structure):
    """A big callout — the key amount and the summary — on the left; everything else on the right."""
    SW = 150

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + 5
        return [(x0 + self.SW + 10, top, x1 - x0 - self.SW - 12, y1 - top)]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        # the summary lives in the spotlight; the rest flows on the right
        return layout([p for p in paras if p["style"] != "lead"], boxes, fonts, ctx.pal, ctx.upper, gap=gap, home=ctx.home_name)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.sev, title_text(ctx))
        fact_line(c, ctx, x + f.width(title_text(ctx)) + 12, y0 + (rowh - ctx.fam["small"].h) // 2, ctx.fam["small"],
                  x1 - x - f.width(title_text(ctx)) - 12)
        sw = self.SW
        top = y0 + rowh + 4
        c.FillRect(x0, top, sw, y1 - top, Color(*_dim(ctx.sev, 0.14)))
        c.FillRect(x0, top, 3, y1 - top, Color(*ctx.sev))
        b = ctx.b
        y = top + 4
        big = ctx.fam["titles"][0]
        if b["amount"]:
            for ln in _wrap_plain(big, U(ctx, b["amount"]), sw - 12):
                if big.width(ln) > sw - 12:
                    big = ctx.fam["key"]
                big.draw(c, x0 + 8, y, ctx.pal["amount"], ln)
                y += big.line
            y += 3
        k = ctx.fam["key"]
        reg, bold = ctx.fam["ladder"][0]
        for g in (bold, reg, ctx.fam["small"]):
            lines = _wrap_plain(g, U(ctx, b["summary"]), sw - 14)
            if y + len(lines) * g.line <= y1 - 2:
                break
        for ln in lines:
            if y + g.h > y1 - 2:
                break
            g.draw(c, x0 + 8, y, ctx.pal["strong"], ln)
            y += g.line


class Timeline(Structure):
    """Header, then a timeline bar from issued to expiry with a live 'now' marker, then the text."""

    def bar_h(self, ctx):
        return ctx.fam["small"].line + 10

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + 4 + self.bar_h(ctx) + 4
        return [(x0 + 2, top, x1 - x0 - 4, y1 - top)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        x += f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.sev, title_text(ctx)) + 10
        sf = ctx.fam["small"]
        b = ctx.b
        if b["amount"]:
            x += chip(c, x, y0 + (rowh - sf.h - 2) // 2, U(ctx, b["amount"]), sf, ctx.pal["amount"], (0, 0, 0)) + 8
        draw_home_text(c, ctx, sf, x, y0 + (rowh - sf.h) // 2, area_line(ctx, sf, x1 - x - 2), ctx.pal["strong"])
        y = y0 + rowh + 4
        sf.draw(c, x0 + 2, y, ctx.pal["dim"], U(ctx, f"ISSUED {b['issued']}" if b["issued"] else "ISSUED"))
        lab = U(ctx, f"UNTIL {b['until']}" if b["until"] else "UNTIL FURTHER NOTICE")
        sf.draw(c, x1 - 2 - sf.width(lab), y, ctx.pal["strong"], lab)
        c.FillRect(x0 + 2, y + sf.line + 3, x1 - x0 - 4, 2, Color(*_dim(ctx.sev, 0.35)))

    def dynamic(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        a = ctx.b
        issued, expires = ctx.b.get("_issued_ts"), ctx.b.get("_expires_ts")
        sf = ctx.fam["small"]
        y = y0 + max(16, ctx.title_f.h) + 4 + sf.line + 3
        frac = 0.1
        if issued and expires and expires > issued:
            frac = max(0.0, min(1.0, (ctx.now - issued) / (expires - issued)))
        bx0, bx1 = x0 + 2, x1 - 2
        c.FillRect(bx0, y, int((bx1 - bx0) * frac), 2, Color(*ctx.sev))
        mx = bx0 + int((bx1 - bx0) * frac)
        glow = 0.6 + 0.4 * math.sin(ctx.t * 5)
        c.FillRect(mx - 2, y - 3, 5, 8, Color(*_dim((255, 255, 255), glow)))
        super().dynamic(c, ctx, seg, p)


def frame_box(c, ctx, x, y, w, h, label, f):
    """A panel: thin frame, bright notched corners, a label tab."""
    dimc = Color(*_dim(ctx.sev, 0.45))
    c.FillRect(x, y, w, 1, dimc)
    c.FillRect(x, y + h - 1, w, 1, dimc)
    c.FillRect(x, y, 1, h, dimc)
    c.FillRect(x + w - 1, y, 1, h, dimc)
    br = Color(*ctx.sev)
    for (cx, cy, dx, dy) in ((x, y, 1, 1), (x + w - 1, y, -1, 1), (x, y + h - 1, 1, -1), (x + w - 1, y + h - 1, -1, -1)):
        c.FillRect(min(cx, cx + dx * 5), cy, 6, 1, br)
        c.FillRect(cx, min(cy, cy + dy * 5), 1, 6, br)
    if label:
        chip(c, x + 6, y - f.h // 2 - 1, label, f, ctx.pal["bg"], ctx.sev, 3)


class PanelGrid(Structure):
    """Header, then a 2x2 grid of framed panels: WHAT · EXPECT / WHAT TO DO · AREA."""
    lists_cities = True
    GROUPS = (("WHAT", ("lead", "detail")), ("EXPECT", ("impact",)), ("WHAT TO DO", ("advice",)))

    def grid(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + 9
        gap = 7
        lw = int((x1 - x0 - gap) * getattr(ctx, "panel_frac", 0.6))
        rh = int((y1 - top - gap) * 0.58)
        return {"WHAT": (x0, top, lw, rh), "EXPECT": (x0 + lw + gap, top, x1 - x0 - lw - gap, rh),
                "WHAT TO DO": (x0, top + rh + gap, lw, y1 - top - rh - gap),
                "AREA": (x0 + lw + gap, top + rh + gap, x1 - x0 - lw - gap, y1 - top - rh - gap)}

    def boxes(self, ctx):
        g = self.grid(ctx)
        return [(x + 5, y + 6, w - 10, h - 9) for x, y, w, h in (g["WHAT"], g["EXPECT"], g["WHAT TO DO"])]

    def flow(self, ctx, paras, boxes, fonts, gap=3):
        what = sum(len(p["text"]) for p in paras if p["style"] in ("lead", "detail"))
        exp = sum(len(p["text"]) for p in paras if p["style"] == "impact")
        ctx.panel_frac = min(0.68, max(0.45, what / max(1, what + exp)))
        boxes = self.boxes(ctx)
        lines, over, base = [], False, 0
        for (label, styles), box in zip(self.GROUPS, boxes):
            group = [p for p in paras if p["style"] in styles]
            if not group:
                continue
            ln, ov = layout(group, [box], fonts, ctx.pal, ctx.upper, prefix="none", gap=gap, home=ctx.home_name)
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
        for label, (bx, by, bw, bh) in self.grid(ctx).items():
            frame_box(c, ctx, bx, by, bw, bh, U(ctx, label) if not ctx.upper else label, sf)
        ax, ay, aw, ah = self.grid(ctx)["AREA"]
        city_list(c, ctx, ax + 5, ay + 7, aw - 8, ay + ah - 3, ctx.fam["key"] if ctx.fam["key"].h <= 13 else sf,
                  fonts=[sf, FI("alert/4x6.bdf")])


class Newspaper(Structure):
    """A masthead with heavy rules, then three ruled columns of text."""

    def mast_h(self, ctx):
        return 3 + max(16, ctx.title_f.h) + ctx.fam["small"].line + 7

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + self.mast_h(ctx)
        gap = 10
        cw = (x1 - x0 - 2 * gap) // 3
        return [(x0 + k * (cw + gap), top, cw, y1 - top) for k in range(3)]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        c.FillRect(x0, y0, x1 - x0, 3, Color(*ctx.sev))
        f = ctx.title_f
        tt = title_text(ctx)
        rowh = max(16, f.h)
        cx = (x0 + x1) // 2
        f.draw(c, cx - f.width(tt) // 2, y0 + 3 + (rowh - f.h) // 2, ctx.pal["strong"], tt)
        icon(c, ctx, x0 + 2, y0 + 3 + (rowh - 16) // 2, 1)
        icon(c, ctx, x1 - 18, y0 + 3 + (rowh - 16) // 2, 1)
        sf = ctx.fam["small"]
        y = y0 + 3 + rowh + 1
        fact_line(c, ctx, x0, y, sf, x1 - x0, centre=True)
        y += sf.line + 2
        c.FillRect(x0, y, x1 - x0, 1, Color(*ctx.sev))
        c.FillRect(x0, y + 2, x1 - x0, 1, Color(*_dim(ctx.sev, 0.5)))
        for bx, by, bw, bh in self.boxes(ctx)[1:]:
            c.FillRect(bx - 5, by, 1, bh, Color(*_dim(ctx.sev, 0.35)))


class Hud(Structure):
    """HUD: severity gauge column, text in the middle, a radar sweep, expiry countdown and cities on the right."""
    GW, RW = 26, 118
    lists_cities = True

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + 5
        return [(x0 + self.GW + 6, top, x1 - x0 - self.GW - self.RW - 14, y1 - top)]

    def title_font(self, ctx, width=None):
        return super().title_font(ctx, W - 2 * self.inner(ctx)[0] - self.RW - 40)

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.sev, title_text(ctx))
        sf, k = ctx.fam["small"], ctx.fam["key"]
        # gauge column
        gx, gy = x0, y0 + rowh + 5
        gh = y1 - gy
        lvl = alert_brief.SEVERITY_LEVEL.get(ctx.b["kind"], 1)
        seg = (gh - 3 * 3) // 4
        for i in range(4):
            yy = gy + gh - (i + 1) * seg - i * 3
            c.FillRect(gx + 4, yy, self.GW - 8, seg, Color(*(ctx.sev if i < lvl else _dim(ctx.sev, 0.15))))
        c.FillRect(gx + self.GW + 1, gy, 1, gh, Color(*_dim(ctx.sev, 0.5)))
        # right panel
        rx = x1 - self.RW
        frame_box(c, ctx, rx, y0, self.RW, y1 - y0, "", sf)
        b = ctx.b
        y = y0 + 70
        sf.draw(c, rx + 6, y, ctx.pal["dim"], "IN EFFECT UNTIL")
        y += sf.line
        k.draw(c, rx + 6, y, ctx.pal["strong"], (b["until"] or "-").upper())
        y += k.line + 14
        sf.draw(c, rx + 6, y, ctx.pal["dim"], "AREAS")
        city_list(c, ctx, rx + 5, y + sf.line, self.RW - 9, y1 - 3, sf, fonts=[FI("alert/4x6.bdf")])

    def dynamic(self, c, ctx, seg, p):
        x0, y0, x1, y1 = self.inner(ctx)
        rx = x1 - self.RW
        cx, cy, r = rx + self.RW // 2, y0 + 34, 26
        c.buf[cy - r:cy + r + 1, cx - r:cx + r + 1] = (c.buf[cy - r:cy + r + 1, cx - r:cx + r + 1] * 0).astype(np.uint8)
        for rr in (r, r * 2 // 3, r // 3):
            for a in np.linspace(0, 2 * math.pi, int(rr * 6), endpoint=False):
                c.buf[int(cy + rr * math.sin(a)), int(cx + rr * math.cos(a))] = _dim(ctx.sev, 0.45)
        ang = ctx.t * 2.2
        for j in range(18):                                    # sweep with a fading trail
            a = ang - j * 0.06
            k = 1 - j / 18
            for d in range(1, r):
                c.buf[int(cy + d * math.sin(a)), int(cx + d * math.cos(a))] = _dim(ctx.sev, 0.25 + 0.75 * k) if j else (255, 255, 255)
        # blips at the home city bearing
        blip = 0.5 + 0.5 * math.sin(ctx.t * 6)
        c.FillRect(cx + 9, cy - 7, 3, 3, Color(*_dim((255, 255, 255), blip)))
        exp = ctx.b.get("_expires_ts")
        if exp:
            left = max(0, exp - ctx.now)
            s = f"T- {int(left // 3600):02d}:{int(left % 3600 // 60):02d}:{int(left % 60):02d}"
            f = ctx.fam["small"]
            c.FillRect(rx + 4, y0 + 62 - f.h, self.RW - 8, f.h + 1, Color(*ctx.pal["bg"]))
            f.draw(c, rx + (self.RW - f.width(s)) // 2, y0 + 62 - f.h, ctx.pal["amount"], s)
        super().dynamic(c, ctx, seg, p)


class StatStack(Structure):
    """Text on the left; stacked stat panels on the right: amount, time left (shrinking bar), area."""
    SW = 132
    lists_cities = True

    def boxes(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        top = y0 + max(16, ctx.title_f.h) + 5
        return [(x0 + 2, top, x1 - x0 - self.SW - 10, y1 - top)]

    def title_font(self, ctx, width=None):
        return super().title_font(ctx, W - 2 * self.inner(ctx)[0] - self.SW - 30)

    def panels(self, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        sx = x1 - self.SW
        k, sf = ctx.fam["key"], ctx.fam["small"]
        h1 = sf.line + k.line + 8
        h2 = sf.line + k.line + 12
        return sx, [(y0 + 4, h1), (y0 + 4 + h1 + 8, h2), (y0 + 4 + h1 + h2 + 16, y1 - (y0 + 4 + h1 + h2 + 16))]

    def chrome(self, c, ctx):
        x0, y0, x1, y1 = self.inner(ctx)
        f = ctx.title_f
        rowh = max(16, f.h)
        x = x0 + 2 + icon(c, ctx, x0 + 2, y0 + (rowh - 16) // 2, 1) + 6
        f.draw(c, x, y0 + (rowh - f.h) // 2, ctx.pal["strong"], title_text(ctx))
        c.FillRect(x0, y0 + rowh + 2, x1 - x0 - self.SW - 8, 1, Color(*_dim(ctx.sev, 0.6)))
        sx, ps = self.panels(ctx)
        k, sf = ctx.fam["key"], ctx.fam["small"]
        b = ctx.b
        labels = ("AMOUNT" if b["amount"] else "LEVEL", "TIME LEFT", "AREAS")
        for (py, ph), lab in zip(ps, labels):
            frame_box(c, ctx, sx, py, self.SW, ph, lab, sf)
        py, ph = ps[0]
        val = U(ctx, b["amount"]) if b["amount"] else b["kind"].upper()
        (k if k.width(val) <= self.SW - 12 else sf).draw(c, sx + 6, py + 6, ctx.pal["amount"], val)
        py, ph = ps[2]
        city_list(c, ctx, sx + 5, py + 7, self.SW - 8, py + ph - 3, sf, fonts=[FI("alert/4x6.bdf")])

    def dynamic(self, c, ctx, seg, p):
        sx, ps = self.panels(ctx)
        py, ph = ps[1]
        k, sf = ctx.fam["key"], ctx.fam["small"]
        iss, exp = ctx.b.get("_issued_ts"), ctx.b.get("_expires_ts")
        c.FillRect(sx + 2, py + 4, self.SW - 4, ph - 6, Color(*ctx.pal["bg"]))
        if exp:
            left = max(0, exp - ctx.now)
            s = f"{int(left // 3600)}h {int(left % 3600 // 60):02d}m"
            k.draw(c, sx + 6, py + 6, ctx.pal["strong"], s)
            frac = max(0.0, min(1.0, left / max(1, (exp - (iss or exp - 86400)))))
            bw = self.SW - 12
            c.FillRect(sx + 6, py + ph - 7, bw, 3, Color(*_dim(ctx.sev, 0.25)))
            c.FillRect(sx + 6, py + ph - 7, int(bw * frac), 3, Color(*ctx.sev))
        super().dynamic(c, ctx, seg, p)


STRUCT_IMPL = {"band": Band(), "columns": Columns(), "rail": Rail(), "crawl": Crawl(), "boxes": Boxes(),
               "terminal": Terminal(), "centered": Centered(), "factbar": FactBar(), "cities": CitiesRail(),
               "dualrail": DualRail(), "keyfacts": KeyFacts(), "spotlight": Spotlight(), "timeline": Timeline(),
               "panels": PanelGrid(), "newspaper": Newspaper(), "hud": Hud(), "stats": StatStack()}


# ---------------------------------------------------------------------------
# hub helpers (previews)
# ---------------------------------------------------------------------------
def render_preview(theme, alerts_data=None, at=4.0, settings=None):
    """(H, W, 3) uint8 frame of the alert screen at ``at`` seconds, for the control panel's previews."""

    class _Loader:
        def get_data(self, name):
            return alerts_data if name == "alerts" else None

    s = dict(settings or {}, theme=theme)

    class _DataLoader(_Loader):
        data_dir = (settings or {}).get("_data_dir")

        def get_data(self, name):
            return alerts_data if name == "alerts" else None
    scr = AlertScreen(s, _DataLoader())
    c = Canvas(W, H)
    entry = {} if alerts_data and alerts_data.get("alerts") and scr.alerts() else {"test": True}
    t0 = time.time() - max(0.0, at)
    scr.draw(c, (0, 0, W, H), entry, True, now=t0)
    scr.draw(c, (0, 0, W, H), entry, False, now=t0 + at)
    return c.buf.copy()


def timeline(settings=None, alerts_data=None):
    """When each alert's screen starts, how long its reveal and hold last (for the preview scrubber)."""
    class _L:
        data_dir = (settings or {}).get("_data_dir")

        def get_data(self, name):
            return alerts_data if name == "alerts" else None
    scr = AlertScreen(dict(settings or {}), _L())
    th = scr.theme()
    alerts = (alerts_data or {}).get("alerts") or scr.alerts({"test": True})
    plan = scr._plan_for(alerts, th, scr.family(th))
    return {"total": round(plan["total"], 2),
            "pages": [{"start": round(x["start"], 2), "reveal": round(x["reveal"], 2), "hold": round(x["hold"], 2),
                       "label": ("MAP · " if x.get("kind") == "map" else "") + x["ctx"].b["title"], "alert": x["alert"],
                       "font_px": x["fonts"]["reg"].h}
                      for x in plan["segs"]]}


THEME_LIST = [{"id": k, "name": v["name"], "structure": v["structure"], "border": v["border"], "reveal": v["reveal"],
               "palette": v["palette"], "family": v["family"], "description": v["description"], "header": v["header"]}
              for k, v in THEMES.items()]
