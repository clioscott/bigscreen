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
from display.modules.alert_themes import HAZARD_PALETTE, SEVERITY
from display.modules.bc_kit import Para

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

    def half_spec(self, a):
        b = alert_brief.brief(a)
        lvl = (a.get("type") or "statement").lower()
        lcol = SEVERITY.get(lvl, SEVERITY["statement"])
        hz = alert_brief.hazard_of(a)
        hcol = (HAZARD_PALETTE.get(hz) or HAZARD_PALETTE["general"])[0]

        def hero_(c, x, y, mw, mh):
            px.draw(c, px.hazard(hz), x, y, hcol, size=24)
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

        body = []
        for p in alert_brief.paragraphs(b):
            style = p["style"]
            col = {"lead": (255, 255, 255), "impact": (255, 210, 150), "advice": (255, 230, 120), "regions": (160, 175, 200)}.get(style, (215, 220, 230))
            body.append(Para(p["text"], "head" if style in ("lead", "advice") else "small" if style == "regions" else "body", col))
        title = (a.get("name") or "weather alert")
        title = title[:1].upper() + title[1:]
        return {"label": self.LABEL, "col": lcol, "chip": (lvl.upper(), lcol), "hero": hero_, "title": title,
                "lines": [(b.get("where") or "", (220, 225, 235)), (f"Until {b['until']}" if b.get("until") else "", (170, 175, 190)),
                          (b.get("amount") or "", hcol)],
                "body": body, "map": map_ if a.get("map") else None}
