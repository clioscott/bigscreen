"""The ECCC weather alert's half-width version and its place in the shared alert lifecycle.

``WeatherAlerts`` adapts display.modules.weather_alert.AlertScreen to what bc_kit.HalfPanel and the rotation
rules expect (ids, lifecycle phases, samples, half-width content). The content comes from the weather screen's own
parsing (alert_brief: summary, details, what to expect, what to do) and its own zone maps (data/alertmaps, the
alert's zones over the Lower Mainland), cropped round home with the zones pulsing in the level colour.
"""

from __future__ import annotations

import hashlib
import os

import numpy as np

from display.modules import alert_brief, alert_life
from display.modules import bc_kit as k
from display.modules import pixel_icons as px
from display.modules.alert_themes import FAMILIES, HAZARD_PALETTE, PALETTES, SEVERITY, THEMES, resolve
from display.modules.bc_kit import BLACK, Para

SOURCE = "weather"
_MAPS = {}


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

        def map_(c, x, y, w, h, now):
            meta = a.get("map")
            if not meta or not self.data_dir:
                return
            arr = _map_arrays(self.data_dir, meta)
            if arr is None:
                return
            img, mask = arr
            hx, hy = meta.get("home_px") or (img.shape[1] / 2, img.shape[0] / 2)
            sx = int(max(0, min(img.shape[1] - w, hx - w / 2)))
            sy = int(max(0, min(img.shape[0] - h, hy - h / 2)))
            c.Blit(img[sy:sy + h, sx:sx + w], x, y)
            m = mask[sy:sy + h, sx:sx + w]
            k.tint(c, m, lcol, 0.45 * k.pulse(now, 1.6, 0.6, 1.0), x, y)
            k.paint(c, k.edge(m), lcol, x, y)
            if 0 <= hx - sx < w and 0 <= hy - sy < h:
                k.icon_outlined(c, "house", int(x + hx - sx) - 8, int(y + hy - sy) - 7, (240, 240, 245))

        role = {"lead": "strong", "impact": "impact", "advice": "advice", "regions": "dim"}
        body = []
        for p in alert_brief.paragraphs(b):
            style = p["style"]
            body.append(Para(p["text"], "head" if style in ("lead", "advice") else "small" if style == "regions" else "body",
                             pal[role.get(style, "text")]))
        title = (a.get("name") or "weather alert")
        title = title[:1].upper() + title[1:]
        lines = [(b.get("where") or "", pal["text"]), (f"Until {b['until']}" if b.get("until") else "", pal["dim"])]
        if th.get("structure") != "infographic":
            lines.append((b.get("amount") or "", pal["amount"]))
        summary = None
        if th.get("structure") == "infographic" and b.get("amount"):

            def summary(c, x, y, w, h, now, el):                     # big numbers: the amount is the headline
                px.draw(c, px.hazard(hz), x, y, hcol, size=24)
                t = title.upper() if lk["upper"] else title
                ty = y + k.fit_text(c, t, x + 30, y + 2, w - 30, 30, lk["line_fonts"], pal["strong"]) + 6
                ty = max(ty, y + 30)
                amt = b["amount"].upper() if lk["upper"] else b["amount"]
                ty += k.fit_text(c, amt, x, ty, w, 40, lk["title_fonts"], pal["amount"]) + 5
                for line_, lc in lines:
                    if line_ and ty < y + h - 10:
                        ty += k.fit_text(c, line_.upper() if lk["upper"] else line_, x, ty, w, 26, lk["line_fonts"], lc) + 2
        return {"summary": summary, "label": th.get("header") or self.LABEL, "col": lcol, "chip": (lvl.upper(), lcol), "hero": hero_, "title": title,
                "lines": lines, "body": body, "map": map_ if a.get("map") else None, "look": lk}


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
