"""Wildfire screen: BC Wildfire Service fires and EMCR evacuations in south-west BC and the Okanagan.

Data: data/wildfire.json from hub/jobs/wildfire.py — {"items": [{kind: "fire"|"evac", …}], "fires": [{name,
number, status: ooc|held|uc, status_text, ha, cause, where, lat, lon, of_note, discovered, km}], "perimeters":
[{number, ha, rings}], "evacs": [{status: Order|Alert, event, name, agency, homes, people, rings, km}],
"danger", "danger_home", "bans", "counts", "regions"}.

Themes from the fire world:

  situation  the region's map: perimeters, evacuation areas, every fire coloured by stage of control
  incident   a BCWS incident card: name, size, stage of control, cause, discovery, linked evacuations
  danger     the roadside fire-danger sign (LOW · MODERATE · HIGH · EXTREME) with its needle, and the bans
  evac       the evacuation order / alert notice: who issued it, homes and people, the area on the map
"""

from __future__ import annotations

import math

from display.modules import bc_kit as k
from display.modules.bc_kit import BLACK, H, W, WHITE, Para

ST_COL = {"ooc": (255, 50, 20), "held": (255, 200, 0), "uc": (70, 210, 90), "out": (120, 120, 120)}
ST_WORD = {"ooc": "OUT OF CONTROL", "held": "BEING HELD", "uc": "UNDER CONTROL", "out": "OUT"}
BCWS = (240, 110, 30)
EVAC_COL = {"Order": (255, 30, 30), "Alert": (255, 150, 0)}
DANGER = [("LOW", (40, 170, 70)), ("MODERATE", (40, 110, 220)), ("HIGH", (255, 210, 0)), ("EXTREME", (230, 30, 30))]
DANGER_IDX = {"very low": 0, "low": 0, "moderate": 1, "high": 2, "very high": 2, "extreme": 3}


def _ctx(s, item):
    """The regional picture: real data, or the sample's own context."""
    return item.get("ctx") if item.get("sample") else s.data()


def _home(ctx):
    lon, lat = ctx.get("home") or [-123.0918, 49.1442]
    return lat, lon


def _pts(item):
    """The item's area on the map: an evacuation's outline, or the fire."""
    pts = [(la, lo) for r in item.get("rings") or [] for lo, la in r]
    return pts or ([(item["lat"], item["lon"])] if item.get("lat") is not None else [])


def _ha(v):
    v = float(v or 0)
    return f"{v:,.0f} ha" if v >= 10 else f"{v:.1f} ha"


def _title(item):
    if item.get("kind", "evac") == "evac":
        return f"EVACUATION {item['status'].upper()}"
    return "WILDFIRE OF NOTE" if item.get("of_note") else "WILDFIRE"


def _draw_region(s, c, mp, item, ctx, ox=0, oy=0, sx=0, sy=0, w=W, h=H, big_icons=True):
    """Perimeters, evacuation areas and fires on a (cropped) fire map."""
    for p in ctx.get("perimeters") or []:
        m = mp.poly_mask(p["rings"])[sy:sy + h, sx:sx + w]
        k.tint(c, m, (255, 90, 0), 0.55, ox, oy)
        k.paint(c, k.edge(m), (255, 140, 40), ox, oy)
    for e in ctx.get("evacs") or []:
        m = mp.poly_mask(e["rings"])[sy:sy + h, sx:sx + w]
        col = EVAC_COL.get(e["status"], EVAC_COL["Alert"])
        k.tint(c, m, col, 0.35, ox, oy)
        k.paint(c, k.edge(m), col, ox, oy)
    for f in sorted(ctx.get("fires") or [], key=lambda f: f.get("ha") or 0):
        x, y = mp.project(f["lat"], f["lon"])
        x, y = x - sx + ox, y - sy + oy
        if not (ox <= x < ox + w and oy <= y < oy + h):
            continue
        col = ST_COL.get(f["status"], ST_COL["ooc"])
        if big_icons and (f.get("ha") or 0) >= 100 or f.get("of_note"):
            k.icon_outlined(c, "flame", int(x) - 8, int(y) - 14, col)
        else:
            k.box(c, x - 1, y - 1, 3, 3, BLACK)
            k.box(c, x, y, 1, 1, col) if (f.get("ha") or 0) < 1 else k.box(c, x - 1, y - 1, 3, 3, col)
    hla, hlo = _home(ctx)
    hx, hy = mp.project(hla, hlo)
    if 0 <= hx - sx < w and 0 <= hy - sy < h:
        k.icon_outlined(c, "house", int(hx - sx + ox) - 8, int(hy - sy + oy) - 7, (220, 220, 230))


def _status_chip(c, f, x, y, item):
    if item["kind"] == "evac":
        t, col = item["status"].upper(), EVAC_COL.get(item["status"], EVAC_COL["Alert"])
    else:
        t, col = ST_WORD.get(item.get("status"), "ACTIVE"), ST_COL.get(item.get("status"), ST_COL["ooc"])
    return k.chip(c, f, x, y, t, col, BLACK if item.get("status") in ("held", "uc", "Alert") else WHITE, 3, 1)


def _facts(item):
    if item.get("kind", "evac") == "evac":
        out = [("Event", item.get("event") or ""), ("Issued by", item.get("agency") or "")]
        if item.get("homes"):
            out.append(("Homes", f"{item['homes']:,}"))
        if item.get("people"):
            out.append(("People", f"{item['people']:,}"))
        if item.get("updated"):
            out.append(("Updated", k.day_hhmm(item["updated"])))
        return out
    out = [("Size", _ha(item.get("ha"))), ("Cause", item.get("cause") or "Under investigation")]
    if item.get("discovered"):
        out.append(("Discovered", k.ago(item["discovered"])))
    if item.get("where"):
        out.append(("Location", item["where"]))
    out.append(("Fire no.", item.get("number") or ""))
    return out


# ------------------------------------------------------------------------------------------------ situation
def _situation(s, c, item, now, el):
    mp = k.Map.load(s.data_dir, "fire_south")
    ctx = _ctx(s, item)
    pw = 164
    px_ = W - pw

    def static():
        if mp:
            # fitted to the band between the heading (16 px) and the count chips (14 px)
            sx, sy, ref = k.frame_crop(mp, px_, H - 30, _pts(item) or [_home(ctx)], _home(ctx))
            sy = int(max(0, min(max(0, mp.h - H), sy - 16)))
            mp.blit(c, 0, 0, sx, sy, px_, H)
            _draw_region(s, c, mp, item, ctx, sx=sx, sy=sy, w=px_)
            k.ref_marker(c, mp, ref, 0, 16, sx, sy + 16, px_, H - 30)
        # heading + counts over the map
        k.shade(c, 0, 0, px_, 16, 0.3)
        k.text(c, k.font("hvb10"), 4, 3, (255, 200, 150), "WILDFIRES · SW BC & OKANAGAN")
        x = 4
        for st in ("ooc", "held", "uc"):
            n = (ctx.get("counts") or {}).get(st, 0)
            t = f"{n} {ST_WORD[st].lower()}"
            x += k.chip(c, k.font("s8"), x, H - 12, t, ST_COL[st], BLACK if st != "ooc" else WHITE, 2, 1) + 3
        # panel
        k.box(c, px_, 0, pw, H, (16, 10, 6))
        k.box(c, px_, 0, 1, H, BCWS)
        k.text(c, k.font("hvb10"), px_ + 6, 4, BCWS, _title(item))
        y = 17
        y += k.fit_text(c, item.get("name") or "", px_ + 6, y, pw - 10, 34, ("h16", "lutb12", "hvb10"), WHITE) + 3
        y += _status_chip(c, k.font("m10b"), px_ + 6, y, item) and 0
        y += 15
        for lab, val in _facts(item):
            if y > H - 14:
                break
            k.text(c, k.font("hvb10"), px_ + 6, y, (200, 150, 110), lab)
            y += 11
            y += k.fit_text(c, str(val), px_ + 6, y, pw - 10, 24, ("hv10", "s8"), (235, 230, 225)) + 3
        if item["kind"] == "fire" and item.get("why"):
            k.fit_text(c, " · ".join(item["why"]), px_ + 6, H - 13, pw - 10, 12, ("s8",), (255, 200, 120))

    s.static(c, ("sit", item["id"], s.data().get("updated_utc")), static)
    if mp and item.get("lat") is not None:
        x, y = mp.project(item["lat"], item["lon"])
        if x < px_:
            r = 6 + 4 * k.pulse(now, 1.4, 0, 1)
            k.circle(c, x, y, r, BCWS)


# ------------------------------------------------------------------------------------------------ incident
def _incident(s, c, item, now, el):
    ctx = _ctx(s, item)

    def static():
        col = EVAC_COL.get(item.get("status"), BCWS) if item["kind"] == "evac" else BCWS
        k.box(c, 0, 0, W, 24, col)
        k.icon(c, "flame", 6, 4, WHITE)
        k.text(c, k.font("h16"), 28, 4, WHITE, _title(item))
        k.text_r(c, k.font("hv10"), W - 6, 7, (255, 230, 210), "BC Wildfire Service" if item["kind"] == "fire" else "EMCR")
        name = item.get("name") or ""
        f = k.pick(("h24", "h20", "h18", "h16"), name, W - 16)
        k.text(c, f, 8, 30, WHITE, name)
        y = 30 + f.h + 6
        _status_chip(c, k.font("m13b"), 8, y, item)
        if item.get("km") is not None:
            k.icon(c, "house", W - 70, y, (200, 200, 210))
            k.text(c, k.font("lutb12"), W - 50, y + 1, (220, 220, 230), f"{item['km']} km")
        y += 22
        facts = _facts(item)
        colw = (W - 16) // 3
        for i, (lab, val) in enumerate(facts[:6]):
            cx = 8 + (i % 3) * colw
            cy = y + (i // 3) * 34
            k.text(c, k.font("hvb10"), cx, cy, (210, 150, 100), lab.upper())
            k.fit_text(c, str(val), cx, cy + 12, colw - 8, 20, ("lutb12", "hvb10", "s8"), WHITE)
        y += 34 * ((min(6, len(facts)) + 2) // 3) + 2
        links = [e for e in ctx.get("evacs") or [] if e["id"] in (item.get("evacs") or [])]
        if links and y < H - 12:
            k.box(c, 0, y - 2, W, 1, (60, 40, 30))
            t = " · ".join(f"{e['status']}: {e['name']}" for e in links[:3])
            k.fit_text(c, t, 8, y + 2, W - 16, H - y - 4, ("hvb10", "s8"), (255, 170, 120))

    s.static(c, ("inc", item["id"], s.data().get("updated_utc")), static)


# ------------------------------------------------------------------------------------------------ danger
def _danger(s, c, item, now, el):
    ctx = _ctx(s, item)
    rating = (ctx.get("danger_home") or ctx.get("danger") or "low").lower()
    di = DANGER_IDX.get(rating, 0)
    cx, cy, r = 112, 150, 98

    def static():
        # the sign board
        k.box(c, 6, 4, 212, H - 8, (110, 70, 30))
        k.frame(c, 6, 4, 212, H - 8, (60, 36, 14), 2)
        k.text_c(c, k.font("h16"), cx, 9, WHITE, "FIRE DANGER")
        import numpy as np
        yy, xx = np.mgrid[0:H, 0:W]
        dx, dy = xx + 0.5 - cx, cy - (yy + 0.5)
        rad = np.hypot(dx, dy)
        ang = np.arctan2(dy, dx)
        ring = (rad >= r - 34) & (rad < r) & (dy >= 0)
        for i, (word, col) in enumerate(DANGER):
            a0, a1 = math.pi - i * math.pi / 4, math.pi - (i + 1) * math.pi / 4
            sel = ring & (ang <= a0) & (ang > a1)
            c.buf[sel] = col
            edge_ = ring & (np.abs(ang - a1) < 0.012 * (r / np.maximum(rad, 1)))
            c.buf[edge_ & (i < 3)] = (110, 70, 30)
            am = (a0 + a1) / 2
            f = k.font("hvb10")
            tx, ty = cx + (r - 17) * math.cos(am), cy - (r - 17) * math.sin(am)
            k.text_c(c, f, tx, ty - 5, BLACK if word in ("HIGH",) else WHITE, word if word != "MODERATE" else "MOD.")
        k.text_c(c, k.font("lutb12"), cx, cy + 10, WHITE, rating.upper())
        # beside it: what's burning and the bans
        x0 = 230
        k.text(c, k.font("h16"), x0, 6, BCWS, "Today's fire picture")
        y = 26
        counts = ctx.get("counts") or {}
        for st in ("ooc", "held", "uc"):
            k.box(c, x0, y + 2, 8, 8, ST_COL[st])
            k.text(c, k.font("lutb12"), x0 + 14, y, WHITE, f"{counts.get(st, 0)} {ST_WORD[st].lower()}")
            y += 15
        y += 4
        top = item if item["kind"] == "fire" else None
        if top:
            y += k.fit_text(c, f"{top.get('name')}: {_ha(top.get('ha'))}, {ST_WORD.get(top.get('status'), '').lower()}",
                            x0, y, W - x0 - 6, 28, ("lutb12", "hvb10"), (255, 210, 170)) + 4
        bans = ctx.get("bans") or []
        k.text(c, k.font("hvb10"), x0, y, (210, 150, 100), "BANS & PROHIBITIONS")
        y += 12
        k.fit_text(c, " · ".join(bans) if bans else "No campfire or open-burning bans in these regions",
                   x0, y, W - x0 - 6, H - y - 14, ("hv10", "s8"), WHITE)
        rng = ctx.get("danger_all") or []
        if rng:
            k.text(c, k.font("s8"), x0, H - 10, (170, 150, 130), "Across the regions: " + " to ".join([rng[0], rng[-1]] if len(rng) > 1 else rng))

    s.static(c, ("dan", item["id"], rating), static)
    # the needle, settling on the rating
    target = math.pi - (di + 0.5) * math.pi / 4
    a = target + 0.08 * math.sin(now * 3) * math.exp(-min(el, 6) / 2)
    k.thick_line(c, cx, cy, cx + (r - 38) * math.cos(a), cy - (r - 38) * math.sin(a), BLACK, 3)
    k.disc(c, cx, cy, 5, BLACK)


# ------------------------------------------------------------------------------------------------ evac
def _evac(s, c, item, now, el):
    ctx = _ctx(s, item)
    ev = item if item["kind"] == "evac" else next((e for e in ctx.get("evacs") or [] if e["id"] in (item.get("evacs") or [])), None)
    if ev is None:
        return _incident(s, c, item, now, el)
    col = EVAC_COL.get(ev["status"], EVAC_COL["Alert"])
    mw, mh = 170, 150

    def static():
        k.box(c, 0, 0, W, 30, col)
        k.text(c, k.font("h24"), 8, 3, WHITE if ev["status"] == "Order" else BLACK, f"EVACUATION {ev['status'].upper()}")
        x0 = 8
        y = 36
        y += k.fit_text(c, ev.get("name") or "", x0, y, W - mw - 24, 40, ("h18", "h16", "lutb12"), WHITE) + 4
        msg = ("Leave the area now." if ev["status"] == "Order" else "Be ready to leave on short notice.")
        k.text(c, k.font("lutb14"), x0, y, col, msg)
        y += 18
        for lab, val in _facts(ev):
            if y > H - 12:
                break
            k.text(c, k.font("hvb10"), x0, y + 1, (210, 160, 130), lab)
            used = k.fit_text(c, str(val), x0 + 72, y, W - mw - 24 - 72, 26, ("lut12", "hv10", "s8"), WHITE)
            y += max(15, used + 3)
        local = k.Map.load(s.data_dir, f"evac_{ev['id']}") if not item.get("sample") else None
        mp = local or k.Map.load(s.data_dir, "fire_south")
        if mp:
            sx, sy, ref = k.frame_crop(mp, mw, mh, _pts(ev), _home(ctx))
            mx, my = W - mw - 6, 36
            mp.blit(c, mx, my, sx, sy, mw, mh)
            _draw_region(s, c, mp, item, ctx, mx, my, sx, sy, mw, mh, big_icons=False)
            k.ref_marker(c, mp, ref, mx, my, sx, sy, mw, mh)
            k.frame(c, mx - 1, my - 1, mw + 2, mh + 2, (90, 60, 40))

    s.static(c, ("evac", item["id"], s.data().get("updated_utc")), static)
    if ev["status"] == "Order" and k.blink(now, 1.0, 0.5):
        k.box(c, 0, 30, W, 2, WHITE)


# ------------------------------------------------------------------------------------------------ samples
def _ring(lat, lon, rkm, n=14, wobble=0.25):
    out = []
    for i in range(n + 1):
        a = 2 * math.pi * i / n
        rr = rkm * (1 + wobble * math.sin(3 * a) * math.cos(2 * a))
        out.append([round(lon + rr / 72.0 * math.cos(a), 4), round(lat + rr / 111.0 * math.sin(a), 4)])
    return out


_MCD = {"lat": 49.90, "lon": -119.58}
_CTX = {
    "fires": [{"name": "McDougall Creek", "number": "K52767", "status": "ooc", "ha": 11345, "lat": 49.90, "lon": -119.58, "of_note": True},
              {"name": "Crater Creek", "number": "K52125", "status": "held", "ha": 4300, "lat": 49.14, "lon": -120.1},
              {"name": "Kookipi Creek", "number": "K71086", "status": "ooc", "ha": 1100, "lat": 49.95, "lon": -121.65},
              {"name": "Burke Mountain", "number": "V10461", "status": "ooc", "ha": 3.5, "lat": 49.31, "lon": -122.74},
              {"name": "V12230", "number": "V12230", "status": "uc", "ha": 0.4, "lat": 49.52, "lon": -121.9},
              {"name": "K21012", "number": "K21012", "status": "uc", "ha": 2.0, "lat": 50.2, "lon": -119.9}],
    "perimeters": [{"number": "K52767", "ha": 11345, "rings": [_ring(49.90, -119.58, 6)]},
                   {"number": "K52125", "ha": 4300, "rings": [_ring(49.14, -120.1, 3.5)]}],
    "evacs": [{"id": "evac-s1", "status": "Order", "name": "West Kelowna: Westside Road North", "event": "McDougall Creek Wildfire",
               "agency": "Regional District of Central Okanagan", "homes": 2400, "people": 4800, "rings": [_ring(49.93, -119.52, 4)],
               "lat": 49.93, "lon": -119.52},
              {"id": "evac-s2", "status": "Alert", "name": "Lake Country: Okanagan Centre", "event": "McDougall Creek Wildfire",
               "agency": "District of Lake Country", "homes": 900, "people": 2000, "rings": [_ring(50.0, -119.47, 3)],
               "lat": 50.0, "lon": -119.47}],
    "counts": {"ooc": 3, "held": 1, "uc": 2}, "danger_home": "Extreme", "danger_all": ["High", "Extreme"],
    "bans": ["Kamloops Fire Centre: campfire ban", "Coastal Fire Centre: Category 2 and 3 open fires prohibited"],
    "home": [-123.0918, 49.1442],
}
SAMPLES = {
    "mcdougall": {"label": "Wildfire of note (West Kelowna)", "item": {
        "kind": "fire", "name": "McDougall Creek", "number": "K52767", "status": "ooc", "status_text": "Out of Control",
        "ha": 11345, "cause": "Lightning", "where": "McDougall Creek, west of West Kelowna", "of_note": True,
        "discovered_rel": -3 * 86400, "km": 300, "why": ["Wildfire of note"], "evacs": ["evac-s1", "evac-s2"], "ctx": _CTX, **_MCD}},
    "evac_order": {"label": "Evacuation order (West Kelowna)", "item": {
        "kind": "evac", "id": "evac-s1", "status": "Order", "name": "West Kelowna: Westside Road North",
        "event": "McDougall Creek Wildfire", "agency": "Regional District of Central Okanagan", "homes": 2400, "people": 4800,
        "updated_rel": -1800, "rings": _CTX["evacs"][0]["rings"], "lat": 49.93, "lon": -119.52, "km": 302, "ctx": _CTX}},
    "burke": {"label": "New fire nearby (Burke Mountain)", "item": {
        "kind": "fire", "name": "Burke Mountain", "number": "V10461", "status": "ooc", "status_text": "Out of Control",
        "ha": 3.5, "cause": "Under investigation", "where": "Burke Mountain, north of Coquitlam", "of_note": False,
        "discovered_rel": -5400, "km": 21, "why": ["New fire nearby"], "evacs": [], "lat": 49.31, "lon": -122.74, "ctx": _CTX}},
}

THEMES = {
    "situation": {"label": "Situation map", "hint": "Perimeters, evacuation areas and every fire by stage of control; the item's details",
                  "draw": _situation, "badge": (W - 164 - 48, 2)},
    "incident": {"label": "Incident card", "hint": "BCWS-style card: name, size, stage of control, cause, discovery, evacuations",
                 "draw": _incident, "badge": (W - 120, 28)},
    "danger": {"label": "Fire danger sign", "hint": "The roadside danger sign with its needle on today's rating, fires and bans",
               "draw": _danger, "badge": (W - 50, H - 14)},
    "evac": {"label": "Evacuation notice", "hint": "Order or alert: the area, who issued it, homes and people, the map",
             "draw": _evac, "badge": (W - 50, 34)},
}


class Screen(k.BCScreen):
    PANEL, DATA, SETTINGS = "fire_alert", "wildfire", "wildfire"
    THEMES, SAMPLES, DEFAULT_THEME = THEMES, SAMPLES, "situation"
    LABEL = "WILDFIRE"

    def extra_pages(self, item):
        if item.get("kind") == "evac":
            return ["evac"]
        return ["incident"] + (["evac"] if item.get("evacs") else [])

    def half_spec(self, item, theme=None):
        evac = item.get("kind") == "evac"
        col = EVAC_COL.get(item.get("status"), EVAC_COL["Alert"]) if evac else ST_COL.get(item.get("status"), ST_COL["ooc"])
        theme = theme if theme in THEMES else self.DEFAULT_THEME

        def hero_(c, x, y, mw, mh):
            k.icon(c, "flame", x, y, col, 32)
            return 32, 32

        def map_(c, x, y, w, h, now):
            mp = k.Map.load(self.data_dir, "fire_south")
            if not mp or item.get("lat") is None:
                return
            fx, fy = mp.project(item["lat"], item["lon"])
            ctx = _ctx(self, item)
            sx, sy, ref = k.frame_crop(mp, w, h, _pts(item), _home(ctx), key=item.get("id"))
            mp.blit(c, x, y, sx, sy, min(w, mp.w - sx), min(h, mp.h - sy))
            _draw_region(self, c, mp, item, ctx, x, y, sx, sy, w, h, big_icons=False)
            k.ref_marker(c, mp, ref, x, y, sx, sy, w, h)
            k.circle_clip(c, x + fx - sx, y + fy - sy, 6 + 3 * k.pulse(now, 1.4, 0, 1), BCWS, (x, y, x + w, y + h))

        chip = (f"EVAC {item['status'].upper()}" if evac else ST_WORD.get(item.get("status"), "ACTIVE"), col)
        lines = ([(item.get("event") or "", (230, 200, 180)), (f"{item['homes']:,} homes" if item.get("homes") else "", WHITE)]
                 if evac else [(_ha(item.get("ha")), WHITE), (item.get("where") or "", (220, 200, 180)),
                               (f"{item['km']} km away" if item.get("km") is not None else "", (190, 190, 200))])
        body = [Para(f"{lab}: {val}", "body", (225, 220, 215)) for lab, val in _facts(item) if val]
        spec = {"label": self.LABEL, "col": col, "chip": chip, "hero": hero_, "title": item.get("name") or "",
                "lines": lines, "body": body, "map": map_}
        return HALF[theme](self, item, spec, col)

    def theme_for(self, item, entry):
        tid = super().theme_for(item, entry)
        if item.get("kind") == "evac" and not (entry or {}).get("theme") and tid == "incident":
            return "evac"
        return tid


# ------------------------------------------------------------------------------------------------ half-width looks
def _half_situation(s, item, spec, col):
    """The situation map first (perimeters, evacuation areas, every fire by stage), the counts beneath it."""
    base = spec["map"]
    ctx = _ctx(s, item)

    def map_(c, x, y, w, h, now):
        base(c, x, y, w, h - 13, now)
        k.box(c, x, y + h - 13, w, 13, (16, 10, 6))
        cx = x + 3
        for st in ("ooc", "held", "uc"):
            n = (ctx.get("counts") or {}).get(st, 0)
            cx += k.chip(c, k.font("s8"), cx, y + h - 11, f"{n} {'held' if st == 'held' else ST_WORD[st].lower()}", ST_COL[st],
                         BLACK if st != "ooc" else WHITE, 2, 0) + 3

    spec.update(map=map_, label="WILDFIRES · SW BC", look={"order": ("map", "summary", "text"), "bg": (12, 8, 5),
                                                          "head_bg": (16, 10, 6), "rule": BCWS})
    return spec


def _half_incident(s, item, spec, col):
    """The BCWS incident card: an orange slab, the name large, the stage of control and the facts in a grid."""
    def summary(c, x, y, w, h, now, el):
        ty = y + k.fit_text(c, item.get("name") or "", x, y, w, 36, ("h18", "h16", "lutb12", "hvb10"), WHITE) + 3
        _status_chip(c, k.font("m10b"), x, ty, item)
        if item.get("km") is not None:
            k.text_r(c, k.font("hvb10"), x + w, ty + 1, (220, 220, 230), f"{item['km']} km from home")
        ty += 16
        cw = w // 2
        for i, (lab, val) in enumerate(_facts(item)[:6]):
            cx, cy = x + (i % 2) * cw, ty + (i // 2) * 24
            if cy > y + h - 20:
                break
            k.text(c, k.font("s8"), cx, cy, (210, 150, 100), lab.upper())
            k.fit_text(c, str(val), cx, cy + 9, cw - 6, 13, ("hvb10", "s8"), WHITE)

    hcol = EVAC_COL.get(item.get("status"), BCWS) if item.get("kind") == "evac" else BCWS
    spec["body"] = [p for p in spec["body"] if not p.text.startswith(("Size:", "Fire no.:"))] or spec["body"]
    spec.update(summary=summary, label="BC WILDFIRE SERVICE" if item.get("kind") == "fire" else "EMCR",
                look={"head": "slab", "head_bg": hcol, "head_fg": WHITE, "head_h": 15, "label_font": "h13", "bg": (10, 8, 6)})
    return spec


def _half_danger(s, item, spec, col):
    """The roadside fire danger sign, its needle settling on today's rating; the fires and bans beside it."""
    ctx = _ctx(s, item)
    rating = (ctx.get("danger_home") or ctx.get("danger") or "low").lower()
    di = DANGER_IDX.get(rating, 0)
    gw, gh, r = 104, 64, 50

    def gauge(cv):
        import numpy as np
        k.box(cv, 0, 0, gw, gh + 20, (110, 70, 30))
        k.frame(cv, 0, 0, gw, gh + 20, (60, 36, 14), 2)
        k.text_c(cv, k.font("hvb10"), gw // 2, 3, WHITE, "FIRE DANGER")
        cx, cy = gw / 2, gh + 6
        yy, xx = np.mgrid[0:gh + 20, 0:gw]
        dx, dy = xx + 0.5 - cx, cy - (yy + 0.5)
        rad = np.hypot(dx, dy)
        ang = np.arctan2(dy, dx)
        ring = (rad >= r - 18) & (rad < r) & (dy >= 0)
        for i, (word, dc) in enumerate(DANGER):
            a0, a1 = math.pi - i * math.pi / 4, math.pi - (i + 1) * math.pi / 4
            cv.buf[:gh + 20, :gw][ring & (ang <= a0) & (ang > a1)] = dc
        k.text_c(cv, k.font("s8"), gw // 2, gh + 10, WHITE, rating.upper())

    def summary(c, x, y, w, h, now, el):
        c.Blit(k.half_layer(s, ("danger", rating), gw, gh + 20, gauge), x - 2, y)
        cx, cy = x - 2 + gw / 2, y + gh + 6
        target = math.pi - (di + 0.5) * math.pi / 4
        a = target + 0.08 * math.sin(now * 3) * math.exp(-min(el, 6) / 2)
        k.thick_line(c, cx, cy, cx + (r - 20) * math.cos(a), cy - (r - 20) * math.sin(a), BLACK, 2)
        k.disc(c, cx, cy, 3, BLACK)
        tx, tw_ = x + gw + 4, w - gw - 4
        ty = y
        counts = ctx.get("counts") or {}
        for st in ("ooc", "held", "uc"):
            k.box(c, tx, ty + 2, 7, 7, ST_COL[st])
            k.text(c, k.pick(("hvb10", "s8"), f"{counts.get(st, 0)} {ST_WORD[st].lower()}", tw_ - 10), tx + 10, ty, WHITE,
                   f"{counts.get(st, 0)} {ST_WORD[st].lower()}")
            ty += 13
        ty += 3
        ty += k.fit_text(c, f"{item.get('name') or ''}: {_ha(item.get('ha'))}" if item.get("kind") == "fire" else (item.get("name") or ""),
                         tx, ty, tw_, 26, ("hvb10", "s8"), (255, 210, 170)) + 3
        bans = ctx.get("bans") or []
        by = y + gh + 24
        k.text(c, k.font("s8"), x, by, (210, 150, 100), "BANS & PROHIBITIONS")
        k.fit_text(c, " · ".join(bans) if bans else "No campfire or open-burning bans in these regions", x, by + 10, w,
                   y + h - by - 10, ("hv10", "s8"), WHITE)

    spec.update(summary=summary, label="FIRE DANGER", chip=(rating.upper(), DANGER[di][1]),
                look={"bg": (14, 9, 4), "head_bg": (60, 36, 14), "rule": (110, 70, 30)})
    return spec


def _half_evac(s, item, spec, col):
    """The evacuation notice: ORDER (red) or ALERT (orange) slab, what to do now, the facts, then the map."""
    ctx = _ctx(s, item)
    ev = item if item.get("kind") == "evac" else next((e for e in ctx.get("evacs") or [] if e["id"] in (item.get("evacs") or [])), None)
    if ev is None:
        return _half_incident(s, item, spec, col)
    ecol = EVAC_COL.get(ev["status"], EVAC_COL["Alert"])

    def summary(c, x, y, w, h, now, el):
        ty = y + k.fit_text(c, ev.get("name") or "", x, y, w, 36, ("h16", "lutb14", "lutb12", "hvb10"), WHITE) + 3
        msg = "Leave the area now." if ev["status"] == "Order" else "Be ready to leave on short notice."
        ty += k.fit_text(c, msg, x, ty, w, 28, ("lutb14", "lutb12", "hvb10"), ecol) + 4
        for lab, val in _facts(ev):
            if ty > y + h - 12:
                break
            k.text(c, k.font("hvb10"), x, ty, (210, 160, 130), lab)
            ty += max(13, k.fit_text(c, str(val), x + 62, ty, w - 62, 24, ("hv10", "s8"), WHITE) + 2)

    spec["body"] = [Para(f"{lab}: {val}", "body", (225, 220, 215)) for lab, val in _facts(ev) if val]
    spec.update(summary=summary, label=f"EVACUATION {ev['status'].upper()}", chip=None,
                look={"head": "slab", "head_bg": ecol, "head_fg": WHITE if ev["status"] == "Order" else BLACK, "head_h": 15,
                      "label_font": "h13", "bg": (10, 6, 6), "pulse": ev["status"] == "Order"})
    return spec


HALF = {"situation": _half_situation, "incident": _half_incident, "danger": _half_danger, "evac": _half_evac}


def render_preview(theme, sample=None, at=4.0, settings=None, data=None, data_dir=None):
    return k.render_preview(Screen, theme, sample, at, settings, data, data_dir)
