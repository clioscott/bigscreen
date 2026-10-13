"""Small pixel charts for the alert screen's data pages: line chart, horizontal bar chart, a towns × time heat
strip, big-number tiles and a time axis. Flat colours, one LED per pixel, labels in the theme's fonts — every
label is fitted to its space."""

from __future__ import annotations

import math


from display.gfx import Color


def _dim(rgb, k):
    return tuple(max(0, min(255, int(v * k))) for v in rgb)


def _mix(a, b, k):
    return tuple(int(x + (y - x) * k) for x, y in zip(a, b))


def fit(f, s, w):
    if f.width(s) <= w:
        return s
    while s and f.width(s + "..") > w:
        s = s[:-1]
    return (s + "..") if s else ""


def hour_label(dt):
    """'TUE 3P', 'MIDNIGHT' → compact: 12A, 3P…"""
    h = dt.hour % 12 or 12
    return f"{h}{'A' if dt.hour < 12 else 'P'}"


def day_label(dt):
    return dt.strftime("%a").upper()


def fmt(v, unit=""):
    if v is None:
        return "-"
    av = abs(v)
    s = f"{v:.1f}" if (av < 10 and av != int(av) and unit in ("km", "mm", "cm")) else f"{v:,.0f}"
    return "0" if s in ("-0", "-0.0") else s


def time_axis(c, x, y, w, times, font, col, every=None, now_col=None):
    """Tick labels under a chart: the hour every few steps, the day name at midnight / the first tick."""
    n = len(times)
    if n < 2:
        return
    every = every or max(1, math.ceil(n * (font.width("12P") + 6) / w))
    last_day = None
    for i in range(0, n, every):
        tx = x + int(i * (w - 1) / (n - 1))
        d = times[i]
        lab = day_label(d) if (last_day != d.date() and (d.hour < 3 or i == 0)) else hour_label(d)
        last_day = d.date()
        lx = max(x, min(x + w - font.width(lab), tx - font.width(lab) // 2))
        c.FillRect(tx, y - 2, 1, 2, Color(*col))
        font.draw(c, lx, y, col, lab)


def line_chart(c, x, y, w, h, times, lines, font, pal, unit="", lo=None, hi=None, guides=(), label_max=True):
    """``lines`` = [(values, colour, name)]. Draws a faint grid, guide lines (value, colour, text), the series,
    and labels the highest point of the first series. Returns the plot rect."""
    vals = [v for vs, _, _ in lines for v in vs if v is not None] + [g[0] for g in guides]
    lo_fixed = lo is not None
    if not vals:
        font.draw(c, x + 2, y + h // 2 - font.h // 2, pal["dim"], "no data")
        return (x, y, w, h)
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    if hi - lo < 1e-6:
        hi, lo = hi + 1, lo - 1
    pad = (hi - lo) * 0.08
    lo, hi = (lo if lo_fixed else lo - pad), hi + pad
    ax_w = max(font.width(fmt(hi, unit)), font.width(fmt(lo, unit))) + 3
    px0, pw, ph = x + ax_w, w - ax_w, h - font.h - 4
    n = len(times)
    sx = lambda i: px0 + int(i * (pw - 1) / max(1, n - 1))
    sy = lambda v: y + int((hi - v) / (hi - lo) * (ph - 1))
    grid = _dim(pal["dim"], 0.35)
    for frac in (0, 0.5, 1):
        gy = y + int(frac * (ph - 1))
        for gx in range(px0, px0 + pw, 3):
            c.FillRect(gx, gy, 1, 1, Color(*grid))
        v = hi - frac * (hi - lo)
        font.draw(c, x, gy - font.h // 2 if frac else gy, pal["dim"], fmt(v, unit))
    for gv, gcol, gtext in guides:
        if lo <= gv <= hi:
            gy = sy(gv)
            for gx in range(px0, px0 + pw, 2):
                c.FillRect(gx, gy, 1, 1, Color(*gcol))
            if gtext:
                font.draw(c, px0 + pw - font.width(gtext) - 1, gy - font.h - 1, gcol, gtext)
    for vs, col, _ in lines:
        prev = None
        for i, v in enumerate(vs):
            if v is None:
                prev = None
                continue
            pt = (sx(i), sy(v))
            if prev:
                _seg(c, prev, pt, col)
            prev = pt
    if label_max and lines:
        vs = lines[0][0]
        idx = [i for i, v in enumerate(vs) if v is not None]
        if idx:
            i = max(idx, key=lambda k: vs[k])
            mx, my = sx(i), sy(vs[i])
            c.FillRect(mx - 1, my - 1, 3, 3, Color(255, 255, 255))
            s = fmt(vs[i], unit)
            lx = max(px0, min(px0 + pw - font.width(s), mx - font.width(s) // 2))
            font.draw(c, lx, max(y, my - font.h - 2), (255, 255, 255), s)
    time_axis(c, px0, y + ph + 3, pw, times, font, pal["dim"])
    return (px0, y, pw, ph)


def _seg(c, a, b, col):
    (x0, y0), (x1, y1) = a, b
    n = max(abs(x1 - x0), abs(y1 - y0), 1)
    for k in range(n + 1):
        c.FillRect(int(round(x0 + (x1 - x0) * k / n)), int(round(y0 + (y1 - y0) * k / n)), 1, 1, Color(*col))


def bars(c, x, y, w, h, items, font, pal, unit="", lo=0.0, hi=None, val_font=None):
    """Horizontal bars: ``items`` = [(label, value, colour, emphasise)]. Rows share the height; the label column
    is as wide as the longest label; values print at the bar end."""
    if not items:
        return
    vf = val_font or font
    n = len(items)
    row = max(vf.h + 2, min(h // n, vf.h + 8))
    lab_w = max(font.width(it[0]) for it in items) + 4
    vals = [it[1] for it in items if it[1] is not None]
    hi = hi if hi is not None else (max(vals) if vals else 1)
    hi = hi if hi > lo else lo + 1
    text = lambda it: it[4] if len(it) > 4 else fmt(it[1], unit)
    val_w = max(vf.width(text(it)) for it in items) + 4
    bw = w - lab_w - val_w
    for k, it in enumerate(items):
        lab, v, col, emph = it[:4]
        ry = y + k * row
        if ry + vf.h > y + h:
            break
        font.draw(c, x, ry + (row - font.h) // 2, pal["strong"] if emph else pal["text"], lab)
        bh = max(2, row - 3)
        c.FillRect(x + lab_w, ry + (row - bh) // 2, bw, bh, Color(*_dim(pal["panel"], 1.6)))
        if v is not None:
            L = int(bw * max(0.0, min(1.0, (v - lo) / (hi - lo))))
            c.FillRect(x + lab_w, ry + (row - bh) // 2, max(1, L), bh, Color(*(col if emph else _dim(col, 0.7))))
            s = text(it)
            vf.draw(c, x + lab_w + max(1, L) + 3, ry + (row - vf.h) // 2, pal["strong"] if emph else pal["text"], s)


def heat_strip(c, x, y, w, h, rows, times, colour, font, pal, show_vals=False):
    """A towns × time grid: ``rows`` = [(label, [values], emphasise)], ``colour(v)`` → rgb or None (empty).
    Time labels along the foot."""
    if not rows:
        return
    n = len(times)
    lab_w = max(font.width(r[0]) for r in rows) + 4
    gw = w - lab_w
    rh = max(3, (h - font.h - 4) // len(rows))
    cw = gw / n
    for k, (lab, vals, emph) in enumerate(rows):
        ry = y + k * rh
        font.draw(c, x, ry + (rh - font.h) // 2, pal["strong"] if emph else pal["text"], fit(font, lab, lab_w - 2))
        for i, v in enumerate(vals):
            cx0, cx1 = x + lab_w + int(i * cw), x + lab_w + int((i + 1) * cw) - 1
            col = colour(v) if v is not None else None
            c.FillRect(cx0, ry, max(1, cx1 - cx0), rh - 1, Color(*(col or _dim(pal["panel"], 1.4))))
            if show_vals and v is not None and cx1 - cx0 >= font.width("99") + 2 and rh >= font.h + 1 and col:
                s = fmt(v)
                ink = (0, 0, 0) if sum(col) > 380 else (255, 255, 255)
                font.draw(c, cx0 + (cx1 - cx0 - font.width(s)) // 2 + 1, ry + (rh - 1 - font.h) // 2, ink, s)
    time_axis(c, x + lab_w + int(cw / 2), y + len(rows) * rh + 3, gw - int(cw), times, font, pal["dim"])


def ramp(stops):
    """colour(v) from [(threshold, rgb)] ascending; below the first threshold → None (shown as empty)."""
    def f(v):
        out = None
        for t, col in stops:
            if v >= t:
                out = col
        return out
    return f


def choose(font, text, w):
    """The first wording of ``text`` that fits ``w``: alternatives are separated by "|" (longest first); then
    the " · " parts on their own; then trailing words dropped. Never cut mid-word, never an ellipsis."""
    opts = [t.strip() for t in str(text).split("|") if t.strip()]
    for t in opts:
        if font.width(t) <= w:
            return t
    last = opts[-1] if opts else ""
    for part in reversed(last.split(" · ")):
        if font.width(part) <= w:
            return part
    words = last.split()
    while words and font.width(" ".join(words)) > w:
        words.pop()
    return " ".join(words)


def tile(c, x, y, w, h, label, value, unit, sub, pal, fonts, colour, label_font):
    """A big-number tile: small label, the value in the largest face that fits (unit beside it), a sub-line.
    Label and sub-line take the longest of their wordings that fit (see ``choose``)."""
    c.FillRect(x, y, w, h, Color(*pal["panel"]))
    c.FillRect(x, y, w, 1, Color(*colour))
    label_font.draw(c, x + 3, y + 3, pal["dim"], choose(label_font, label.upper(), w - 6))
    body_top = y + label_font.h + 5
    room = h - (label_font.h + 5) - (fonts[-1].h + 3 if sub else 2)
    s = value
    uw = (fonts[-1].width(" " + unit) if unit else 0)
    f = next((f for f in fonts if f.h <= room and f.width(s) + uw <= w - 6), None)
    if f is None:
        f = next((f for f in fonts if f.width(s) + uw <= w - 6), fonts[-1])
    f.draw(c, x + 3, body_top + max(0, (room - f.h) // 2), colour, s)
    if unit:
        uf = fonts[-1]
        uf.draw(c, x + 3 + f.width(s) + 2, body_top + max(0, (room - f.h) // 2) + f.h - uf.h, pal["dim"], unit)
    if sub:
        sf = fonts[-1]
        sf.draw(c, x + 3, y + h - sf.h - 2, pal["text"], choose(sf, sub, w - 6))
