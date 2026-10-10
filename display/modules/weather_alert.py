"""Full-screen weather alert, styled like an emergency broadcast.

Takes over the whole 448x192 wall (ticker and clock included) while any ECCC
alert for the configured area is in effect (data/alerts.json, written by
hub/jobs/alerts.py). One pass = every alert shown once; the rotation entry
uses ``advance: "cycles"`` so the screen leaves after its passes.

Themes are plain dicts (THEMES below) so the hub can render previews of each
one with this same code (display.gfx runs anywhere).
"""

from __future__ import annotations

import time
from datetime import datetime

import numpy as np

from display.gfx import Color, Font, resolve_asset

W, H = 448, 192
WHITE, BLACK = (255, 255, 255), (0, 0, 0)
TYPE_COLOURS = {"warning": (215, 0, 0), "watch": (255, 120, 0), "advisory": (235, 190, 0), "statement": (95, 95, 120)}
RISK_COLOURS = {"red": (215, 0, 0), "orange": (255, 120, 0), "yellow": (235, 190, 0)}

# mode: "scroll" (body scrolls up), "crawl" (one big line crawls right→left), "type" (typewriter)
THEMES = {
    "ebs": {
        "label": "Emergency Broadcast System", "title": "EMERGENCY BROADCAST SYSTEM",
        "bg": BLACK, "bars": True, "title_fg": WHITE, "band_bg": (200, 0, 0), "band_fg": WHITE,
        "info_fg": (255, 210, 0), "body_fg": WHITE, "foot_fg": (130, 130, 130), "mode": "scroll",
        "upper": True, "flash": False,
    },
    "eas": {
        "label": "EAS crawl", "title": "EMERGENCY ALERT",
        "bg": BLACK, "header_bg": (190, 0, 0), "title_fg": WHITE, "band_bg": (40, 40, 40), "band_fg": (255, 230, 0),
        "info_fg": WHITE, "body_fg": (255, 230, 0), "foot_fg": (150, 150, 150), "mode": "crawl",
        "upper": True, "flash": True,
    },
    "alert_ready": {
        "label": "Alert Ready (red)", "title": "WEATHER ALERT",
        "bg": (130, 0, 0), "header_bg": (180, 0, 0), "title_fg": WHITE, "band_bg": BLACK, "band_fg": (255, 220, 0),
        "info_fg": (255, 200, 200), "body_fg": WHITE, "foot_fg": (230, 150, 150), "mode": "scroll",
        "upper": False, "flash": True,
    },
    "bulletin": {
        "label": "TV weather bulletin (blue)", "title": "WEATHER BULLETIN",
        "bg": (0, 18, 120), "header_bg": (0, 30, 170), "title_fg": (255, 225, 0), "band_bg": (210, 210, 210),
        "band_fg": (0, 18, 120), "info_fg": (170, 200, 255), "body_fg": WHITE, "foot_fg": (120, 150, 220),
        "mode": "scroll", "upper": False, "flash": False,
    },
    "severity": {
        "label": "Severity colours (ECCC)", "title": None,          # "<TYPE> IN EFFECT", coloured by type
        "bg": BLACK, "header_bg": "severity", "title_fg": WHITE, "band_bg": (30, 30, 30), "band_fg": "severity",
        "info_fg": (200, 200, 200), "body_fg": WHITE, "foot_fg": (120, 120, 120), "mode": "scroll",
        "upper": False, "flash": False,
    },
    "terminal": {
        "label": "Green terminal", "title": "> ECCC ALERT FEED",
        "bg": BLACK, "title_fg": (60, 255, 110), "band_bg": (0, 70, 25), "band_fg": (150, 255, 170),
        "info_fg": (40, 190, 80), "body_fg": (60, 255, 110), "foot_fg": (30, 130, 60), "mode": "type",
        "upper": True, "flash": False,
    },
}
BARS = [(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192), (192, 0, 0), (0, 0, 192)]

SAMPLE = {
    "type": "warning", "name": "rainfall warning", "code": "TEST", "test": True,
    "regions": ["Metro Vancouver - central including the City of Vancouver, Burnaby and New Westminster",
                "Metro Vancouver - North Shore including West Vancouver and North Vancouver"],
    "text": ("THIS IS A TEST of the BigScreen weather alert screen. This is only a test.\n\n"
             "Heavy rain is expected. Total rainfall amounts of 50 to 70 mm, with up to 100 mm near the "
             "North Shore mountains. An atmospheric river will bring heavy rain to the region beginning tonight "
             "and continuing through Saturday.\n\n"
             "Heavy downpours can cause flash floods and water pooling on roads. Localized flooding in low-lying "
             "areas is possible. Watch for possible washouts near rivers, creeks and culverts.\n\n"
             "Please continue to monitor alerts and forecasts issued by Environment Canada."),
}


def _col(c):
    return Color(*c)


class AlertScreen:
    def __init__(self, settings=None, loader=None):
        self.loader = loader
        self.settings = settings or {}
        self.f_big = self._font("fonts/8x13B.bdf")
        self.f_mid = self._font("fonts/8x13B.bdf")
        self.f_small = self._font("fonts/5x7.bdf")
        self.f_tiny = self._font("fonts/tom-thumb.bdf")
        self._wrap_cache = {}
        self._started = None
        self._idx = 0
        self._seg_t0 = None
        self._big_cache = {}

    @staticmethod
    def _font(path):
        f = Font()
        f.LoadFont(resolve_asset(path))
        return f

    # -- data ------------------------------------------------------------------
    def alerts(self, entry=None):
        if entry and entry.get("test"):
            return [dict(SAMPLE, published=time.time() - 600, expires=time.time() + 12 * 3600)]
        data = (self.loader.get_data("alerts") if self.loader else None) or {}
        now = time.time()
        if now - float(data.get("updated_utc") or 0) > 3 * 3600:       # hub down: don't show stale alerts
            return []
        return [a for a in data.get("alerts") or [] if not a.get("expires") or a["expires"] > now]

    def active(self):
        return bool(self.alerts())

    def theme(self):
        t = dict(THEMES.get(self.settings.get("theme") or "ebs", THEMES["ebs"]))
        if self.settings.get("flash") is not None:
            t["flash"] = bool(self.settings["flash"])
        if self.settings.get("title"):
            t["title"] = self.settings["title"]
        return t

    # -- text helpers ----------------------------------------------------------
    def _tw(self, font, s):
        return sum(max(0, font.CharacterWidth(ord(ch))) for ch in s)

    def _text(self, c, font, x, top, colour, s, scale=1):
        """Draw with the top of the font's line box at ``top``; optional integer pixel scaling."""
        mask, mtop, adv = font.text_mask(s)
        if scale > 1:
            key = (id(font), s, scale)
            m = self._big_cache.get(key)
            if m is None:
                m = np.repeat(np.repeat(mask, scale, axis=0), scale, axis=1)
                if len(self._big_cache) > 64:
                    self._big_cache.clear()
                self._big_cache[key] = m
            mask = m
        base = top + font.baseline * scale
        c.BlitMask(mask, x, base + mtop * scale, colour)
        return adv * scale

    def _wrap(self, font, text, width):
        key = (id(font), text, width)
        hit = self._wrap_cache.get(key)
        if hit is not None:
            return hit
        lines = []
        for para in text.replace("\r", "").split("\n"):
            words, line = para.split(), ""
            if not words:
                lines.append("")
                continue
            for w in words:
                cand = (line + " " + w).strip()
                if self._tw(font, cand) <= width:
                    line = cand
                    continue
                if line:
                    lines.append(line)
                while self._tw(font, w) > width:              # very long token: hard split
                    n = max(1, int(len(w) * width / max(1, self._tw(font, w))))
                    lines.append(w[:n])
                    w = w[n:]
                line = w
            lines.append(line)
        while lines and not lines[-1]:
            lines.pop()
        if len(self._wrap_cache) > 32:
            self._wrap_cache.clear()
        self._wrap_cache[key] = lines
        return lines

    @staticmethod
    def _when(ts):
        return datetime.fromtimestamp(ts).strftime("%a %H:%M") if ts else ""

    def _body_text(self, a, upper):
        regions = "; ".join(a.get("regions") or [])
        s = (f"For: {regions}\n\n" if regions else "") + (a.get("text") or "")
        return s.upper() if upper else s

    # -- drawing ---------------------------------------------------------------
    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        alerts = self.alerts(entry)
        x0, y0, w, h = zone
        th = self.theme()
        if transition or self._seg_t0 is None:
            self._idx, self._seg_t0 = 0, now
        if not alerts:
            c.FillRect(x0, y0, w, h, _col(th["bg"]))
            self._text(c, self.f_mid, x0 + 8, y0 + 8, _col(th["body_fg"]), "No weather alerts in effect.")
            return "done" if now - self._seg_t0 > 5 else None
        self._idx %= len(alerts)
        a = alerts[self._idx]
        sev = RISK_COLOURS.get(a.get("colour") or "") or TYPE_COLOURS.get(a.get("type"), TYPE_COLOURS["statement"])
        pick = lambda v: sev if v == "severity" else v
        flash_on = not th["flash"] or int(now * 1.25) % 2 == 0
        upper = th["upper"]

        title = th["title"] or f"{a.get('type', 'alert').upper()} IN EFFECT"
        tsize = 2 if self._tw(self.f_big, title) * 2 <= w - 8 else 1
        hh = self.f_big.height * tsize + 6
        bh = self.f_mid.height + 4
        body_top = y0 + (9 if th.get("bars") else 0) + hh + 2 + bh + 2 + self.f_small.height + 3
        fy = y0 + h - self.f_tiny.height - 1
        body_bot = fy - 2
        c.FillRect(x0, y0, w, h, _col(th["bg"]))
        # body first; the header and footer are painted over it
        speed = float(self.settings.get("scroll_px_per_sec") or 18)
        t = now - self._seg_t0
        body = self._body_text(a, upper)
        mode = th["mode"]
        if mode == "crawl":
            done = self._crawl(c, x0, body_top, w, body_bot - body_top, body, th, t, speed * 4)
        else:
            done = self._scroll(c, x0, body_top, w, body_bot - body_top, body, th, t, speed, typewriter=(mode == "type"))
        c.FillRect(x0, y0, w, body_top - y0, _col(th["bg"]))
        c.FillRect(x0, body_bot, w, y0 + h - body_bot, _col(th["bg"]))
        y = y0
        # colour bars (EBS)
        if th.get("bars"):
            bw = w / len(BARS)
            for i, col in enumerate(BARS):
                c.FillRect(x0 + int(i * bw), y, int((i + 1) * bw) - int(i * bw), 7, _col(col))
            y += 9
        # title, 2x
        hb = th.get("header_bg")
        if hb:
            hbc = pick(hb)
            if not flash_on:
                hbc = tuple(int(v * 0.45) for v in hbc)
            c.FillRect(x0, y, w, hh, _col(hbc))
        if flash_on or hb:
            tw = self._tw(self.f_big, title) * tsize
            self._text(c, self.f_big, x0 + (w - tw) // 2, y + 3, _col(th["title_fg"]), title, tsize)
        y += hh + 2
        # alert name band
        name = (a.get("name") or "weather alert").upper()
        if a.get("test"):
            name = "TEST - " + name
        count = f"{self._idx + 1}/{len(alerts)}" if len(alerts) > 1 else ""
        c.FillRect(x0, y, w, bh, _col(pick(th["band_bg"])))
        nw = self._tw(self.f_mid, name)
        self._text(c, self.f_mid, x0 + (w - nw) // 2, y + 2, _col(pick(th["band_fg"])), name)
        if count:
            self._text(c, self.f_small, x0 + w - self._tw(self.f_small, count) - 4, y + 4, _col(pick(th["band_fg"])), count)
        y += bh + 2
        # info line
        area = (self.loader.get_data("alerts") or {}).get("area") if self.loader and not a.get("test") else "Metro Vancouver"
        bits = [area or "", f"issued {self._when(a.get('published'))}" if a.get("published") else "",
                f"until {self._when(a.get('expires'))}" if a.get("expires") else ""]
        info = "  ·  ".join(b for b in bits if b)
        info = info.upper() if upper else info
        self._text(c, self.f_small, x0 + 4, y, _col(th["info_fg"]), info.replace("·", "-"))
        y += self.f_small.height + 3
        # footer
        foot = "ENVIRONMENT AND CLIMATE CHANGE CANADA" if upper else "Environment and Climate Change Canada"
        fy = y0 + h - self.f_tiny.height - 1
        self._text(c, self.f_tiny, x0 + 4, fy, _col(th["foot_fg"]), foot)
        stamp = datetime.fromtimestamp(now).strftime("%H:%M")
        self._text(c, self.f_tiny, x0 + w - self._tw(self.f_tiny, stamp) - 4, fy, _col(th["foot_fg"]), stamp)
        if done:
            self._idx += 1
            self._seg_t0 = now
            if self._idx >= len(alerts):
                self._idx = 0
                return "done"
        return None

    def _scroll(self, c, x, top, w, h, body, th, t, speed, typewriter=False):
        font = self.f_mid
        lh = font.height + 1
        lines = self._wrap(font, body, w - 8)
        visible = max(1, h // lh)
        hold_top, hold_end = 3.0, 5.0
        if typewriter:
            cps = speed * 2.2                                    # characters per second
            shown = int(max(0.0, t) * cps)
            out, left = [], shown
            for ln in lines:
                if left <= 0:
                    break
                out.append(ln[:left])
                left -= len(ln) + 1
            first = max(0, len(out) - visible)
            for i, ln in enumerate(out[first:]):
                self._text(c, font, x + 4, top + i * lh, _col(th["body_fg"]), ln)
            if left <= 0 and out and int(t * 2) % 2 == 0:       # blinking cursor
                cx = x + 4 + self._tw(font, out[-1]) + 1
                c.FillRect(cx, top + (len(out) - 1 - first) * lh + 1, 6, font.height - 2, _col(th["body_fg"]))
            total_chars = sum(len(ln) + 1 for ln in lines)
            return shown >= total_chars and t - total_chars / cps > hold_end
        overflow = max(0, len(lines) * lh - h)
        scroll = 0.0 if t < hold_top else min(overflow, (t - hold_top) * speed)
        # clip: draw lines whose box is inside the body area
        for i, ln in enumerate(lines):
            ly = top + i * lh - int(scroll)
            if ly + font.height <= top or ly >= top + h:
                continue
            self._text(c, font, x + 4, ly, _col(th["body_fg"]), ln)
        end_t = hold_top + overflow / max(0.1, speed)
        return t > end_t + hold_end

    def _crawl(self, c, x, top, w, h, body, th, t, speed):
        font, scale = self.f_big, 2
        line = "  +++  ".join(p.strip() for p in body.split("\n") if p.strip())
        tw = self._tw(font, line) * scale
        lh = font.height * scale
        cy = top + max(0, (h - lh) // 2)
        offset = int(t * speed)
        start = x + w - offset
        # draw only the visible slice of a very long line: render word chunks lazily
        self._crawl_draw(c, font, scale, start, cy, line, th, x, x + w)
        return start + tw < x

    def _crawl_draw(self, c, font, scale, start, y, line, th, xmin, xmax):
        px = start
        for word in line.split(" "):
            ww = (self._tw(font, word) + font.CharacterWidth(32)) * scale
            if px + ww >= xmin and px <= xmax:
                self._text(c, font, px, y, _col(th["body_fg"]), word, scale)
            px += ww
            if px > xmax:
                break


def render_preview(theme, alerts_data=None, at=4.0, settings=None):
    """(H, W, 3) uint8 frame of the alert screen for the control panel's theme previews."""
    from display.gfx import Canvas

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
