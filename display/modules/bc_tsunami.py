"""Tsunami screen: NTWC messages for British Columbia (and BC's own zone alerts from Alert Ready).

Data: data/tsunami.json from hub/jobs/tsunami.py — {"items": [{level, bc_threat, title, region, mag, lat, lon,
depth_km, origin, issued, number, headline, evaluation: [..], instruction, arrivals: [{place, hhmm, tz, date}],
zones: {A..E: level}, gauges: [{code, name, lat, lon, wlo, wlp, residual}], eta_tofino, source}]}.

Themes in the language of tsunami warning, unlike any other screen:

  pacific   the North Pacific: epicentre, hourly travel-time rings, where the wave front would be now
  zones     the BC coast by notification zone (A–E) in alert colours, tide gauges, arrival times
  bulletin  the official message: level bar, evaluation, earthquake parameters, arrivals
  sign      a tsunami hazard sign: MOVE TO HIGH GROUND for a threat, NO THREAT TO BC for information
"""

from __future__ import annotations

import math

import numpy as np

from display.modules import bc_kit as k
from display.modules.bc_kit import BLACK, H, W, WHITE, Para

LV_COL = {"warning": (255, 30, 30), "advisory": (255, 140, 0), "watch": (255, 220, 0), "information": (60, 200, 120),
          "cancellation": (150, 150, 160), "none": (90, 90, 100)}
LV_WORD = {"warning": "TSUNAMI WARNING", "advisory": "TSUNAMI ADVISORY", "watch": "TSUNAMI WATCH",
           "information": "TSUNAMI INFORMATION", "cancellation": "TSUNAMI CANCELLATION", "none": "NO ALERT"}
SPEED_KMH = 720.0                        # deep-ocean wave speed used for the rings (≈ √(g·4 km))
ZONE_POLY = {
    "A": [[-134.6, 55.0], [-129.6, 55.0], [-129.6, 52.6], [-131.2, 51.9], [-134.6, 51.9]],
    "B": [[-131.2, 51.9], [-129.6, 52.6], [-126.6, 52.6], [-125.0, 50.35], [-126.6, 50.25], [-128.3, 50.75], [-129.7, 51.0]],
    "C": [[-129.7, 51.0], [-128.3, 50.75], [-127.4, 50.4], [-124.4, 48.55], [-125.4, 48.15], [-129.7, 48.15]],
    "D": [[-124.4, 48.55], [-123.25, 48.55], [-123.25, 48.2], [-125.4, 48.15]],
    "E": [[-125.4, 50.15], [-124.0, 50.15], [-122.6, 49.35], [-122.85, 48.7], [-123.25, 48.55], [-124.4, 48.95], [-125.0, 49.6]],
}
ZONE_LABEL = {"A": (53.4, -133.4), "B": (51.6, -128.6), "C": (49.2, -127.6), "D": (48.3, -124.2), "E": (49.5, -123.9)}
ZONE_NAME = {"A": "North Coast & Haida Gwaii", "B": "Central Coast & NE Island", "C": "Outer west coast of the Island",
             "D": "Juan de Fuca Strait", "E": "Strait of Georgia"}


def col_of(item):
    return LV_COL.get(item.get("level"), LV_COL["none"])


def title_of(item):
    return LV_WORD.get(item.get("level"), "TSUNAMI")


def _origin(item):
    o = item.get("origin")
    return f"{k.day_hhmm(o)}  ·  {k.ago(o)}" if o else ""


def _dest(lat, lon, d_km, brg):
    """Great-circle destination point."""
    p1, l1, b = math.radians(lat), math.radians(lon), math.radians(brg)
    dr = d_km / 6371.0
    p2 = math.asin(math.sin(p1) * math.cos(dr) + math.cos(p1) * math.sin(dr) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(dr) * math.cos(p1), math.cos(dr) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), math.degrees(l2)


def _ring(c, mp, lat, lon, d_km, col, dashed=False):
    prev = None
    for i in range(0, 361, 4):
        la, lo = _dest(lat, lon, d_km, i)
        x, y = mp.project(la, lo)
        if prev is not None and abs(x - prev[0]) < 120 and (not dashed or (i // 4) % 2 == 0):
            k.line(c, prev[0], prev[1], x, y, col)
        prev = (x, y)


def _main_text(item):
    ev = [e for e in item.get("evaluation") or [] if e and not e.lower().startswith("an earthquake has occurred")]
    return ev


def _arrival_rows(item, n=6):
    return [(a["place"], f"{a['hhmm'][:2]}:{a['hhmm'][2:]} {a.get('tz', '')}") for a in (item.get("arrivals") or [])[:n]]


# ------------------------------------------------------------------------------------------------ pacific
def _pacific(s, c, item, now, el):
    mp = k.Map.load(s.data_dir, "tsunami_pacific")
    col = col_of(item)
    cx, cy, cw, ch = 118, 100, 236, 88

    def static():
        if mp:
            mp.blit(c, 0, 0)
            if item.get("lat") is not None:
                for h in range(1, 10):
                    _ring(c, mp, item["lat"], item["lon"], h * SPEED_KMH, (30, 60, 100), dashed=True)
                    la, lo = _dest(item["lat"], item["lon"], h * SPEED_KMH, 90)
                    x, y = mp.project(la, lo)
                    if 0 < x < W - 14 and 0 < y < H - 8:
                        k.text(c, k.font("s8"), x + 2, y - 4, (70, 110, 160), f"{h}h")
            # the BC coast in the alert colour
            for z, ring in ZONE_POLY.items():
                lv = (item.get("zones") or {}).get(z, "none")
                if lv != "none":
                    k.tint(c, mp.poly_mask([ring]), LV_COL[lv], 0.55)
        # level badge, top-left
        t = title_of(item)
        f = k.font("h16")
        k.box(c, 0, 0, k.tw(f, t) + 16, 22, col)
        k.text(c, f, 8, 3, BLACK if item.get("level") in ("watch", "information") else WHITE, t)
        sub = "British Columbia" if item.get("bc") else (item.get("source") or "")
        k.text(c, k.font("hvb10"), 8, 25, (220, 230, 240), sub)
        # the card in open ocean
        k.shade(c, cx, cy, cw, ch, 0.25)
        k.frame(c, cx, cy, cw, ch, k.dim(col, 0.8))
        y = cy + 4
        mtxt = f"M{item['mag']:.1f}" if item.get("mag") else ""
        if mtxt:
            k.text(c, k.font("h18"), cx + 6, y, WHITE, mtxt)
        k.fit_text(c, item.get("region") or "", cx + 64, y, cw - 70, 30, ("lutb12", "hvb10", "s8"), WHITE)
        y += 30
        k.text(c, k.font("hv10"), cx + 6, y, (190, 200, 215), _origin(item))
        y += 13
        ev = _main_text(item)
        k.fit_text(c, ev[0] if ev else (item.get("headline") or ""), cx + 6, y, cw - 12, cy + ch - y - 4,
                   ("lutb12", "hvb10", "s8"), col if item.get("level") != "information" else (150, 230, 170))

    s.static(c, ("pac", item["id"], item.get("number")), static)
    if mp and item.get("lat") is not None:
        ex, ey = mp.project(item["lat"], item["lon"])
        d = max(0.0, (now - (item.get("origin") or now)) / 3600.0 * SPEED_KMH)
        if 0 < d < 9.5 * SPEED_KMH and item.get("level") != "information":
            _ring(c, mp, item["lat"], item["lon"], d, k.dim(col, k.pulse(now, 1.5, 0.6, 1.0)))
        k.disc(c, ex, ey, 3, col if k.blink(now, 0.8) else WHITE)
        k.circle(c, ex, ey, 6, col)


# ------------------------------------------------------------------------------------------------ zones
def _zones(s, c, item, now, el):
    mp = k.Map.load(s.data_dir, "tsunami_bc")
    col = col_of(item)
    mw = mp.w if mp else 200
    x0 = mw + 8

    def static():
        if mp:
            mp.blit(c, 0, 0)
            water = (mp.img[..., 2].astype(int) > mp.img[..., 0].astype(int) + 20)
            for z, ring in ZONE_POLY.items():
                lv = (item.get("zones") or {}).get(z, "none")
                m = mp.poly_mask([ring]) & water
                if lv != "none":
                    k.tint(c, m, LV_COL[lv], 0.55)
                k.paint(c, k.edge(mp.poly_mask([ring])) & water, (40, 70, 110) if lv == "none" else LV_COL[lv])
            for z, (la, lo) in ZONE_LABEL.items():
                x, y = mp.project(la, lo)
                lv = (item.get("zones") or {}).get(z, "none")
                k.disc(c, x, y, 6, LV_COL[lv] if lv != "none" else (30, 40, 60))
                k.text_c(c, k.font("hvb10"), x, y - 5, BLACK if lv != "none" else (170, 180, 200), z)
            for g in item.get("gauges") or []:
                x, y = mp.project(g["lat"], g["lon"])
                k.box(c, x - 1, y - 1, 3, 3, WHITE)
                r = g.get("residual")
                if r is not None:
                    t = f"{r:+.1f} m"
                    k.text(c, k.font("s8"), x + 3, y - 4, (255, 240, 160) if abs(r) >= 0.3 else (200, 210, 225), t)
        k.box(c, mw, 0, 1, H, (40, 60, 90))
        # level and text
        t = title_of(item)
        f = k.pick(("h16", "lutb12"), t, W - x0 - 4)
        k.box(c, x0 - 4, 0, W - x0 + 4, f.h + 6, col)
        k.text(c, f, x0, 3, BLACK if item.get("level") in ("watch", "information") else WHITE, t)
        y = f.h + 10
        quake = " · ".join(v for v in (f"M{item['mag']:.1f}" if item.get("mag") else "", item.get("region") or "") if v)
        y += k.fit_text(c, quake, x0, y, W - x0 - 4, 24, ("lutb12", "hvb10", "s8"), WHITE) + 2
        k.text(c, k.font("hv10"), x0, y, (170, 180, 200), _origin(item))
        y += 14
        arr = _arrival_rows(item, 5)
        zones_on = [z for z in "ABCDE" if (item.get("zones") or {}).get(z, "none") != "none"]
        tail = (len(arr) * 12 + 14 if arr else 0) + (13 * len(zones_on[:3]) if zones_on and not arr else 0)
        paras = [Para(e, "body", (220, 225, 235), i) for i, e in enumerate(_main_text(item))]
        if item.get("bc_alerts"):
            paras.insert(0, Para(item["bc_alerts"][0].get("headline") or "", "head", col, 0))
        lines, used = k.fit(paras, W - x0 - 4, H - y - tail - 4,
                            [{"head": "lutb12", "body": "lut12"}, {"head": "hvb10", "body": "hv10"}, {"head": "s8", "body": "s8"}])
        k.draw_fit(c, lines, x0, y)
        y = H - tail
        if arr:
            k.text(c, k.font("hvb10"), x0, y, (150, 170, 200), "ESTIMATED FIRST WAVE")
            y += 13
            for place, when in arr:
                k.text(c, k.font("hv10"), x0, y, WHITE, place)
                k.text_r(c, k.font("hvb10"), W - 6, y, col, when)
                y += 12
        elif zones_on:
            for z in zones_on[:3]:
                k.text(c, k.font("hv10"), x0, y, LV_COL[item["zones"][z]], f"Zone {z}: {ZONE_NAME[z]}")
                y += 13

    s.static(c, ("zones", item["id"], item.get("number"), len(item.get("gauges") or [])), static)


# ------------------------------------------------------------------------------------------------ bulletin
def _bulletin(s, c, item, now, el):
    col = col_of(item)

    def static():
        k.box(c, 0, 0, W, 30, col)
        dark = item.get("level") in ("watch", "information")
        fg = BLACK if dark else WHITE
        k.icon(c, "wave", 8, 7, fg)
        t = title_of(item)
        k.text(c, k.pick(("h24", "h20", "h18"), t, W - 200), 30, 3, fg, t)
        sub = f"Statement {item['number']}" if item.get("number") else ""
        k.text_r(c, k.font("lutb12"), W - 8, 4, fg, item.get("source") or "")
        k.text_r(c, k.font("hv10"), W - 8, 17, fg, " · ".join(v for v in (sub, k.day_hhmm(item.get("issued"))) if v))
        if not dark and item.get("level") == "warning":
            k.hatch(c, 0, 30, W, 4, (255, 220, 0), 4)
        # left: what it means
        lx, lw = 8, 268
        paras = []
        if item.get("bc_alerts"):
            paras.append(Para(item["bc_alerts"][0].get("headline") or "", "head", col, 0))
        paras += [Para(e, "body" if i else "head", WHITE if i == 0 else (205, 210, 225), i) for i, e in enumerate(_main_text(item))]
        if item.get("level") != "information" and item.get("instruction"):
            paras.append(Para(item["instruction"], "body", (255, 225, 120), 5))
        lines, _ = k.fit(paras, lw, H - 40 - 6,
                         [{"head": "lutb14", "body": "lut14"}, {"head": "lutb12", "body": "lut12"}, {"head": "hvb10", "body": "hv10"},
                          {"head": "s8", "body": "s8"}])
        k.draw_fit(c, lines, lx, 40)
        # right: the earthquake and the arrivals
        rx = lx + lw + 10
        k.box(c, rx - 6, 38, 1, H - 44, (50, 56, 70))
        y = 40
        for lab, val in (("Magnitude", f"{item['mag']:.1f} {item.get('mag_type') or ''}".strip() if item.get("mag") else "—"),
                         ("Region", item.get("region") or "—"), ("Origin", k.day_hhmm(item.get("origin"))),
                         ("Depth", f"{item['depth_km']:.0f} km" if item.get("depth_km") else "—")):
            k.text(c, k.font("hvb10"), rx, y, (150, 160, 180), lab.upper())
            y += 11
            y += k.fit_text(c, val, rx, y, W - rx - 6, 24, ("lutb12", "hvb10", "s8"), WHITE) + 4
        arr = _arrival_rows(item, 4)
        if arr:
            k.text(c, k.font("hvb10"), rx, y, (150, 160, 180), "FIRST WAVE")
            y += 12
            for place, when in arr:
                if y > H - 12:
                    break
                k.text(c, k.font("hv10"), rx, y, WHITE, place)
                k.text_r(c, k.font("hvb10"), W - 6, y, col, when.split()[0])
                y += 12

    s.static(c, ("bul", item["id"], item.get("number")), static)


# ------------------------------------------------------------------------------------------------ sign
def _sign(s, c, item, now, el):
    threat = item.get("bc_threat") or item.get("level") in ("warning", "advisory", "watch")
    col = col_of(item)

    def static():
        # the sign: blue plate with the wave and a runner heading uphill (threat) or a green "no threat" plate
        sx, sy, sw = 8, 8, 170
        plate = (0, 70, 160) if threat else (0, 110, 60)
        k.box(c, sx, sy, sw, H - 16, plate)
        k.frame(c, sx + 3, sy + 3, sw - 6, H - 22, WHITE)
        if threat:
            k.icon(c, "wave", sx + 20, sy + 26, WHITE, 32)
            k.icon(c, "run", sx + 82, sy + 18, WHITE, 32)
            k.icon(c, "arrow_up", sx + 128, sy + 24, WHITE, 32)
            k.text_c(c, k.font("h16"), sx + sw // 2, sy + 86, WHITE, "TSUNAMI")
            k.text_c(c, k.font("h13"), sx + sw // 2, sy + 106, WHITE, "HAZARD ZONE")
            k.text_c(c, k.font("hvb10"), sx + sw // 2, sy + 126, (200, 220, 255), "In an earthquake go")
            k.text_c(c, k.font("hvb10"), sx + sw // 2, sy + 138, (200, 220, 255), "to high ground or inland")
        else:
            k.icon(c, "wave", sx + 69, sy + 22, WHITE, 32)
            k.text_c(c, k.font("h20"), sx + sw // 2, sy + 70, WHITE, "NO THREAT")
            k.text_c(c, k.font("h16"), sx + sw // 2, sy + 96, WHITE, "TO BC")
            k.text_c(c, k.font("hv10"), sx + sw // 2, sy + 124, (200, 240, 210), "Tsunami information")
            k.text_c(c, k.font("hv10"), sx + sw // 2, sy + 136, (200, 240, 210), "statement")
        # the words beside it
        x0 = sx + sw + 12
        head = "MOVE TO HIGH GROUND" if item.get("level") == "warning" else "STAY OFF BEACHES AND HARBOURS" if threat else title_of(item)
        f = k.pick(("h20", "h18", "h16", "lutb12"), head, W - x0 - 6)
        k.text(c, f, x0, 10, col, head)
        y = 10 + f.h + 6
        quake = " · ".join(v for v in (f"M{item['mag']:.1f}" if item.get("mag") else "", item.get("region") or "") if v)
        y += k.fit_text(c, quake, x0, y, W - x0 - 6, 28, ("lutb14", "lutb12", "hvb10"), WHITE) + 2
        k.text(c, k.font("hv10"), x0, y, (180, 190, 205), _origin(item))
        y += 16
        paras = [Para(e, "body", (215, 220, 230), i) for i, e in enumerate(_main_text(item))]
        if threat and item.get("instruction"):
            paras.insert(0, Para(item["instruction"], "body", (255, 225, 120), 0))
        arr = _arrival_rows(item, 1)
        if arr:
            paras.insert(0, Para(f"First wave expected at {arr[0][0]} around {arr[0][1]}", "head", WHITE, 0))
        lines, _ = k.fit(paras, W - x0 - 6, H - y - 4,
                         [{"head": "lutb14", "body": "lut14"}, {"head": "lutb12", "body": "lut12"}, {"head": "hvb10", "body": "hv10"},
                          {"head": "s8", "body": "s8"}])
        k.draw_fit(c, lines, x0, y)

    s.static(c, ("sign", item["id"], item.get("number")), static)
    if threat:
        on = k.blink(now, 0.7)
        k.frame(c, 8, 8, 170, H - 16, (255, 220, 0) if on else (0, 70, 160), 2)


# ------------------------------------------------------------------------------------------------ samples
def _gauges(t_arr_rel, amp):
    """Synthetic gauge records for samples: tide plus a wave train after the arrival."""
    import time as _t
    now = _t.time()
    out = []
    for code, name, la, lo, lag in (("08615", "Tofino", 49.154, -125.913, 0), ("08408", "Port Hardy", 50.722, -127.489, 900),
                                    ("07120", "Victoria", 48.424, -123.371, 2700), ("07795", "Point Atkinson", 49.337, -123.253, 5400)):
        obs, pred = [], []
        for i in range(0, 6 * 3600, 120):
            t = now - 6 * 3600 + i
            tide = 1.6 * math.sin((t / 3600.0) * 2 * math.pi / 12.42)
            dt = t - (now + t_arr_rel + lag)
            wave = amp * math.exp(-max(0, dt) / 5400) * math.sin(dt / 600 * 2 * math.pi) if dt > 0 else 0
            pred.append([int(t), round(tide + 2.5, 3)])
            obs.append([int(t), round(tide + 2.5 + wave * (0.5 if lag > 2000 else 1), 3)])
        out.append({"code": code, "name": name, "lat": la, "lon": lo, "wlo": obs, "wlp": pred,
                    "residual": round(obs[-1][1] - pred[-1][1], 2)})
    return out


SAMPLES = {
    "kamchatka": {"label": "M7.0 Kamchatka — no threat to BC", "item": {
        "level": "information", "bc": True, "bc_threat": False, "title": "Tsunami Information Statement Number 1",
        "region": "95 miles SW of Petropavlovsk, Kamchatka", "mag": 7.0, "mag_type": "Mwp", "lat": 51.9, "lon": 157.4,
        "depth_km": 148, "origin_rel": -900, "issued_rel": -480, "number": 1, "source": "NTWC",
        "evaluation": ["There is no tsunami danger for the U.S. West Coast, British Columbia, or Alaska.",
                       "Based on the depth of the earthquake, a tsunami is not expected."],
        "instruction": "", "arrivals": [], "zones": {z: "none" for z in "ABCDE"}}},
    "alaska": {"label": "M7.8 Alaska Peninsula — advisory for BC", "item": {
        "level": "advisory", "bc": True, "bc_threat": True, "title": "Tsunami Advisory Number 3",
        "region": "105 miles S of Sand Point, Alaska", "mag": 7.8, "mag_type": "Mww", "lat": 54.6, "lon": -159.6,
        "depth_km": 28, "origin_rel": -2400, "issued_rel": -600, "number": 3, "source": "NTWC",
        "evaluation": ["A tsunami advisory is in effect for the coast of British Columbia from the Alaska border to the "
                       "north tip of Vancouver Island and the outer west coast of Vancouver Island.",
                       "Strong currents and waves dangerous to people in or very near the water are expected. Significant "
                       "widespread inundation is not expected."],
        "instruction": "Move out of the water, off the beach and away from harbours and marinas. Do not go to the shore to watch.",
        "arrivals": [{"place": "Langara", "hhmm": "1305", "tz": "PDT", "date": "Oct 16"},
                     {"place": "Port Hardy", "hhmm": "1342", "tz": "PDT", "date": "Oct 16"},
                     {"place": "Tofino", "hhmm": "1358", "tz": "PDT", "date": "Oct 16"},
                     {"place": "Prince Rupert", "hhmm": "1404", "tz": "PDT", "date": "Oct 16"}],
        "zones": {"A": "advisory", "B": "advisory", "C": "advisory", "D": "none", "E": "none"}}},
    "cascadia": {"label": "M9.0 Cascadia — warning for BC", "item": {
        "level": "warning", "bc": True, "bc_threat": True, "title": "Tsunami Warning Number 1",
        "region": "Off the west coast of Vancouver Island", "mag": 9.0, "mag_type": "Mwp", "lat": 48.8, "lon": -127.2,
        "depth_km": 20, "origin_rel": -360, "issued_rel": -240, "number": 1, "source": "NTWC",
        "evaluation": ["A tsunami warning is in effect for the coast of British Columbia, including the Strait of Georgia "
                       "and Juan de Fuca Strait.", "Widespread hazardous tsunami waves are possible. Waves may continue for many hours."],
        "instruction": "Move to high ground or inland now. Follow the instructions of local officials. Stay away from the coast "
                       "until officials say it is safe.",
        "arrivals": [{"place": "Tofino", "hhmm": "1203", "tz": "PDT", "date": "Oct 16"},
                     {"place": "Port Alberni", "hhmm": "1231", "tz": "PDT", "date": "Oct 16"},
                     {"place": "Victoria", "hhmm": "1255", "tz": "PDT", "date": "Oct 16"},
                     {"place": "Vancouver", "hhmm": "1342", "tz": "PDT", "date": "Oct 16"}],
        "zones": {z: "warning" for z in "ABCDE"}, "gauges": None}},
}
SAMPLES["alaska"]["item"]["gauges"] = _gauges(-1500, 0.6)
SAMPLES["cascadia"]["item"]["gauges"] = _gauges(-300, 2.4)

THEMES = {
    "pacific": {"label": "North Pacific", "hint": "Epicentre, hourly travel-time rings and the wave front now; the BC coast in alert colours",
                "draw": _pacific, "badge": (W - 50, 4)},
    "zones": {"label": "BC zones", "hint": "The BC coast by notification zone A–E, tide gauge readings, first-wave times",
              "draw": _zones, "badge": (4, H - 14)},
    "bulletin": {"label": "Bulletin", "hint": "The official message: level bar, evaluation, earthquake parameters, arrivals",
                 "draw": _bulletin, "badge": (W - 50, H - 14)},
    "sign": {"label": "Hazard sign", "hint": "Tsunami sign with MOVE TO HIGH GROUND — or NO THREAT TO BC for information statements",
             "draw": _sign},
}


class Screen(k.BCScreen):
    PANEL, DATA, SETTINGS = "tsunami_alert", "tsunami", "tsunami"
    THEMES, SAMPLES, DEFAULT_THEME = THEMES, SAMPLES, "pacific"
    LABEL = "TSUNAMI"

    def extra_pages(self, item):
        return ["zones", "bulletin"]

    def half_spec(self, item):
        col = col_of(item)

        def hero_(c, x, y, mw, mh):
            k.icon(c, "wave", x, y, col, 32)
            return 32, 32

        def map_(c, x, y, w, h, now):
            mp = k.Map.load(self.data_dir, "tsunami_bc")
            if not mp:
                return
            cx, cy = mp.project(49.6, -126.0)
            sx, sy = k.map_crop(c, mp, x + max(0, (w - mp.w) // 2), y, w, h, cx, cy)
            ox = x + max(0, (w - mp.w) // 2) - sx
            key = ("half_zones", item.get("id"), sx, sy)
            layers = self.state.get(key)
            if layers is None:
                water = (mp.img[..., 2].astype(int) > mp.img[..., 0].astype(int) + 20)
                layers = self.state[key] = [(LV_COL[(item.get("zones") or {}).get(z, "none")], mp.poly_mask([ring]) & water)
                                            for z, ring in ZONE_POLY.items() if (item.get("zones") or {}).get(z, "none") != "none"]
            for lc, m in layers:
                sub = m[sy:sy + h, sx:sx + min(w, mp.w)]
                k.tint(c, sub, lc, 0.5 * k.pulse(now, 1.6, 0.6, 1.0), ox + sx, y)
            for z, (la, lo) in ZONE_LABEL.items():
                zx, zy = mp.project(la, lo)
                lv = (item.get("zones") or {}).get(z, "none")
                if 0 <= zy - sy < h:
                    k.disc(c, ox + zx, y + zy - sy, 5, LV_COL[lv] if lv != "none" else (30, 40, 60))
                    k.text_c(c, k.font("s8"), ox + zx, y + zy - sy - 4, BLACK if lv != "none" else (170, 180, 200), z)

        arr = _arrival_rows(item, 1)
        quake = " · ".join(v for v in (f"M{item['mag']:.1f}" if item.get("mag") else "", item.get("region") or "") if v)
        body = [Para(e, "body", (215, 220, 230)) for e in _main_text(item)]
        if item.get("instruction") and item.get("level") != "information":
            body.append(Para(item["instruction"], "head", (255, 225, 120)))
        return {"label": self.LABEL, "col": col, "chip": ((item.get("level") or "").upper(), col), "hero": hero_,
                "title": quake, "lines": [(_origin(item), (190, 200, 215)),
                                          (f"First wave {arr[0][0]} {arr[0][1]}" if arr else "", col)],
                "body": body, "map": map_ if any(v != "none" for v in (item.get("zones") or {}).values()) else None}

    def dwell(self, item):
        return 45.0 if item.get("bc_threat") else max(float(self.settings.get("dwell_sec") or 30), 20.0)


def render_preview(theme, sample=None, at=4.0, settings=None, data=None, data_dir=None):
    return k.render_preview(Screen, theme, sample, at, settings, data, data_dir)
