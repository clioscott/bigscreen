"""The ECCC weather alert's half-width version and its place in the shared alert lifecycle.

``WeatherAlerts`` adapts display.modules.weather_alert.AlertScreen to what bc_kit.HalfPanel and the rotation
rules expect (ids, lifecycle phases, samples, half-width content). Pages: the facts (the hazard, where — with how
far and which way from home when home is outside it, a house when inside, never home's name — until when, how
much, when issued); the essentials of the text (alert_brief: the summary, two sentences each of what to expect and
what to do; the full text stays on the full-wall screen); the zone map (data/alertmaps) cropped to the alert's
area, with home when both fit, the zones pulsing in the level colour and captioned.
"""

from __future__ import annotations

import hashlib
import math
import os

import numpy as np

from display.modules import alert_brief, alert_life
from display.modules import bc_kit as k
from display.modules import pixel_icons as px
from display.modules.alert_themes import FAMILIES, HAZARD_PALETTE, PALETTES, SEVERITY, THEMES, resolve
from display.modules.bc_kit import BLACK, Para

SOURCE = "weather"
_MAPS = {}
HOME = "Richmond"                                  # the wall's city when the settings name none (never drawn)


def alert_id(a):
    """A stable id for an ECCC alert across its updates (same code, name and first region)."""
    if a.get("id"):
        return a["id"]
    key = f"{a.get('code')}|{(a.get('name') or '').lower()}|{sorted(a.get('regions') or [''])[0]}"
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _map_arrays(data_dir, meta):
    key = meta.get("file")
    hit = _MAPS.get(key)
    if hit is None:
        try:
            img = np.fromfile(os.path.join(data_dir, meta["file"]), dtype=np.uint8).reshape(meta["h"], meta["w"], 3)
            mask = np.fromfile(os.path.join(data_dir, meta["mask"]), dtype=np.uint8).reshape(meta["h"], meta["w"]) > 127
        except Exception:
            return None
        if len(_MAPS) > 8:
            _MAPS.clear()
        hit = _MAPS[key] = (img, mask)
    return hit


def _zone(data_dir, meta):
    """The alert's zones on its map: image, mask, the zones' bounding box and centre, home, and which way the zones
    lie from home (a compass word, "" when home is in them or they're off the map)."""
    arr = _map_arrays(data_dir, meta) if meta and data_dir else None
    if arr is None:
        return None
    img, mask = arr
    hx, hy = meta.get("home_px") or (img.shape[1] / 2, img.shape[0] / 2)
    ys, xs = np.nonzero(mask)
    z = {"img": img, "mask": mask, "home": (hx, hy), "in_view": bool(len(xs)), "dir": ""}
    if len(xs):
        z["bbox"] = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        z["centre"] = (float(xs.mean()), float(ys.mean()))
        if not mask[int(min(max(hy, 0), mask.shape[0] - 1)), int(min(max(hx, 0), mask.shape[1] - 1))]:
            z["dir"] = _compass(z["centre"][0] - hx, z["centre"][1] - hy)
    return z


def _compass(dx, dy):
    """One of the eight compass points for a step (dx, dy) on a north-up map (y down)."""
    b = (math.degrees(math.atan2(dx, -dy)) + 360) % 360
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][int((b + 22.5) // 45) % 8]


def _window(z, w, h, home):
    """Top-left of the w×h crop of the zone map: the whole area (and home, when both fit), else round home when
    home is in it, else round the area."""
    x0, y0, x1, y1 = z["bbox"]
    hx, hy = z["home"]
    if x1 - x0 <= w - 16 and y1 - y0 <= h - 16:
        ux0, uy0, ux1, uy1 = min(x0, hx), min(y0, hy), max(x1, hx), max(y1, hy)
        fits = ux1 - ux0 <= w - 16 and uy1 - uy0 <= h - 16
        cx, cy = ((ux0 + ux1) / 2, (uy0 + uy1) / 2) if fits else ((x0 + x1) / 2, (y0 + y1) / 2)
    else:
        cx, cy = (hx, hy) if home else z["centre"]
    img = z["img"]
    return int(max(0, min(img.shape[1] - w, cx - w / 2))), int(max(0, min(img.shape[0] - h, cy - h / 2)))


def essentials(b, words=35):
    """The text pages' paragraphs: the summary, then up to two sentences (about ``words`` words) of what to expect
    and of what to do; the detail paragraph only when there are neither. The full text is the full-wall screen's."""

    def some(sentences):
        keep, used = [], 0
        for s in sentences[:2]:
            n = len(s.split())
            if keep and used + n > words:
                break
            keep.append(s)
            used += n
        return " ".join(keep)

    out = [{"style": "lead", "text": b["summary"]}]
    if not b["impacts"] and not b["advice"] and b["details"]:
        out.append({"style": "detail", "text": some(b["details"])})
    out.append({"style": "impact", "text": some(b["impacts"])})
    out.append({"style": "advice", "text": some(b["advice"])})
    return [p for p in out if p["text"]]


class WeatherAlerts:
    """The weather alert screen seen through the alert lifecycle (for rules, priority and the half panel)."""

    LABEL = "WEATHER ALERT"
    source = SOURCE

    def __init__(self, screen, data_dir=None):
        self.scr = screen
        self.data_dir = data_dir
        self.state = {}
        self._clock = 0.0

    def all_items(self):
        return [dict(a, id=alert_id(a)) for a in self.scr.alerts()]

    def live_items(self, phase=None):
        return [a for a in self.all_items() if alert_life.in_phase(SOURCE, a["id"], phase)]

    def active(self, phase=None):
        if (self.scr.settings or {}).get("enabled") is False:
            return False
        return bool(self.live_items(phase))

    def new_token(self):
        return tuple(alert_life.new_ids(SOURCE, [a["id"] for a in self.all_items()]))

    def sample(self, key):
        import time
        from display.modules.alert_samples import SAMPLES
        base = SAMPLES.get(key) or next(iter(SAMPLES.values()))
        now = self._clock or time.time()
        data = (self.scr.loader.get_data("alerts") if self.scr.loader else None) or {}
        smap = (data.get("sample_maps") or {}).get(key) or data.get("sample_map")
        return dict(base, id=f"sample-{key}", sample=True, test=True, status="issued", published=now - 600,
                    expires=now + 12 * 3600, event_end=now + 20 * 3600, map=smap)

    def theme_for(self, a, entry):
        """The weather theme the half-width version is drawn in: the entry's, else the alert screen's own choice."""
        tid = (entry or {}).get("theme")
        return tid if tid in THEMES else self.scr.theme_id()

    def half_spec(self, a, theme=None):
        b = alert_brief.brief(a)
        lvl = (a.get("type") or "statement").lower()
        lcol = SEVERITY.get(lvl, SEVERITY["statement"])
        hz = alert_brief.hazard_of(a)
        hcol, hcol2 = HAZARD_PALETTE.get(hz) or HAZARD_PALETTE["general"]
        th = self.scr.theme() if theme in (None, self.scr.theme_id()) else dict(resolve(theme))
        lk, pal = half_look(th, hcol, lcol)

        def hero_(c, x, y, mw, mh):
            px.draw(c, px.hazard(hz), x, y, pal["icon"] if th.get("palette") != "blackout" else hcol, size=24)
            return 24, 24

        home = bool(a["home"]) if "home" in a else alert_brief.mentions(a, (self.scr.settings or {}).get("home_city") or HOME)
        zone = _zone(self.data_dir, a.get("map"))
        where = b.get("where") or ""
        if not home:                                                # how far and which way, never which city home is
            km = a.get("home_km")
            away = " ".join(s for s in (f"{km:.0f} km" if km else "", zone["dir"] if zone else "") if s)
            where = f"{where} · {away}" if where and away else where or away
        title = (a.get("name") or "weather alert")
        title = title[:1].upper() + title[1:]

        def map_(c, x, y, w, h, now):
            img, mask = zone["img"], zone["mask"]
            sx, sy = _window(zone, w, h, home)
            c.Blit(img[sy:sy + h, sx:sx + w], x, y)
            m = mask[sy:sy + h, sx:sx + w]
            k.tint(c, m, lcol, 0.45 * k.pulse(now, 1.6, 0.6, 1.0), x, y)
            k.paint(c, k.edge(m), lcol, x, y)
            hx, hy = zone["home"]
            if 0 <= hx - sx < w and 0 <= hy - sy < h:
                k.icon_outlined(c, "house", int(x + hx - sx) - 8, int(y + hy - sy) - 7, (240, 240, 245))
            cap = where.upper() if lk["upper"] else where
            if cap:                                                 # what the tinted area is
                f = k.font(lk["ladder"]["small"])
                k.box(c, x, y + h - f.h - 5, min(w, k.tw(f, cap) + 7), f.h + 5, BLACK)   # clear of the frame
                k.text(c, f, x + 3, y + h - f.h - 3, pal["text"], cap)

        def summary(c, x, y, w, h, now, el):
            """Facts first: the hazard (its level is in the title and the header chip), then where, until when, how
            much and when it was issued, as labelled rows in one size."""
            hero_(c, x, y + 1, 24, 24)
            t = title.upper() if lk["upper"] else title
            used = k.fit_text(c, t, x + 30, y, w - 30, 34, lk["title_fonts"], pal["strong"])
            ty = y + max(26, used) + 4
            k.box(c, x, ty, w, 1, k.dim(pal["dim"], 0.7))
            ty += 4
            small = k.font(lk["ladder"]["small"])
            rows = [("WHERE", where, pal["text"]),
                    ("UNTIL", " · ".join(s for s in (b.get("until"), k.until(a.get("expires"), now)) if s), pal["text"]),
                    ("EXPECT", b.get("amount") or "", pal["amount"]),
                    ("ISSUED", k.ago(a.get("published"), now), pal["dim"])]
            rows = [(lb, v.upper() if lk["upper"] else v, col) for lb, v, col in rows if v]
            lw = max((k.tw(small, r[0]) for r in rows), default=0) + 6
            vw = w - lw
            bold, reg, smallest = lk["line_fonts"]                  # one size for every fact: the largest that
            f = next((k.font(r) for r in (reg, smallest)            # keeps each on a line (the area may wrap)
                      if all(k.tw(k.font(r), v) <= vw for lb, v, _ in rows if lb != "WHERE")), k.font(smallest))
            amount = next((v for lb, v, _ in rows if lb == "EXPECT"), "")
            fb = k.font(bold) if k.font(bold).h == f.h and k.tw(k.font(bold), amount) <= vw else f   # the amount bold
            for label, val, col in rows:
                ff = fb if label == "EXPECT" else f
                if ty + ff.h > y + h:
                    break
                house = label == "WHERE" and home                  # home inside the area: the house, no name
                lines = k.wrap(ff, val, vw - (20 if house else 0))[:2]
                for i, ln in enumerate(lines):
                    k.text(c, ff, x + lw, ty + i * ff.h, col, ln)
                used = len(lines) * ff.h
                k.text(c, small, x, ty + max(0, (ff.h - small.h) // 2), pal["dim"], label)
                if house:
                    k.icon_outlined(c, "house", x + w - 16, ty - 1, (240, 240, 245))
                ty += max(used, small.h) + 3

        role = {"lead": "strong", "impact": "impact", "advice": "advice"}
        body = [Para(p["text"], "head" if p["style"] in ("lead", "advice") else "body", pal[role.get(p["style"], "text")])
                for p in essentials(b)]
        return {"summary": summary, "label": th.get("header") or self.LABEL, "col": lcol, "chip": (lvl.upper(), lcol),
                "hero": hero_, "title": title, "lines": [], "body": body,
                "map": map_ if zone and zone["in_view"] else None, "look": lk}


# the header style each weather layout's half-width version uses (see bc_kit.HalfPanel)
HEADS = {"console": "bar", "headline": "slab", "ticket": "tab", "checklist": "line", "infographic": "bar", "sign": "sign",
         "wire": "term", "poster": "slab", "kanban": "line", "strip": "bar", "rail": "bar", "deck": "line", "dossier": "tab"}


def half_look(th, hcol, lcol):
    """A weather theme as a half-width look: its palette (accents in the weather type's colour), its font family,
    header text and capitals, its border; the header styled after its layout. Returns (look, palette colours)."""
    pal = dict(PALETTES.get(th.get("palette")) or PALETTES["blackout"])
    for key, v in list(pal.items()):
        if v == "sev":
            pal[key] = hcol
    if pal.get("tint"):                                             # weather-tinted grounds
        pal["bg"], pal["panel"] = k.dim(hcol, pal["tint"][0]), k.dim(hcol, pal["tint"][1])
    fam = FAMILIES.get(th.get("family")) or FAMILIES["helvetica"]
    step = next((s for s in fam["ladder"] if k.font(s[0]).h <= 13), fam["ladder"][-1])
    reg, bold = step[0], step[1] or step[0]
    titles = tuple(p for p, _ in fam["title"]) + (bold,)
    key = fam["key"] if k.font(fam["key"]).h <= 13 else bold
    st = th.get("structure")
    head = HEADS.get(st, "bar")
    lk = {"bg": pal["panel"] if st == "poster" else pal["bg"], "head": head, "label_font": key,
          "head_h": max(13, k.font(key).h + 2), "head_bg": lcol if head in ("slab", "tab") else pal["panel"],
          "head_fg": (BLACK if lcol in ((255, 210, 0),) else (255, 255, 255)) if head in ("slab", "tab") else pal["strong"],
          "rule": pal["border"], "pulse": bool(th.get("pulse")) and head == "slab",
          "border": th.get("border"), "border_col": pal["border"],
          "title_fonts": titles, "title_col": pal["strong"], "line_fonts": (bold, reg, fam["small"]),
          "ladder": {"body": reg, "head": bold, "small": fam["small"]}, "upper": bool(th.get("upper")),
          "dot_on": pal["strong"], "dot_off": pal["dim"], "type": 80 if th.get("reveal") == "type" else 0}
    if st in ("console", "rail"):
        lk["side"] = (3 if st == "console" else 5, lcol)
    if head == "sign":
        lk["head_fg"] = (255, 170, 0)
    return lk, pal
