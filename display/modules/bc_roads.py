"""DriveBC screen: incidents, closures and road conditions in Metro Vancouver.

Data: data/drivebc.json from hub/jobs/drivebc.py — {"items": [{type, kind, severity, closed, road, highways,
direction, from, to, landmark, where, description, geometry: {type, points}, updated, cam: {id, name, w, h, …}}]}
and webcam stills as raw RGB in data/drivebc/cam_<id>.bin.

Themes borrow from the road itself:

  vms      an overhead variable-message sign in amber LEDs (three lines), the full DriveBC text beneath
  map      the Metro map, transit-diagram style, with the affected stretch flashing and a detail card
  camera   the nearest DriveBC highway camera beside the event's details
  board    a traffic-desk board: every current event in one list, the one in focus expanded
"""

from __future__ import annotations

import os
import re

import numpy as np

from display.modules import bc_kit as k
from display.modules.bc_kit import BLACK, H, W, WHITE, Para

AMB, AMB_D, AMB_DD = (255, 170, 0), (120, 78, 0), (40, 26, 0)
KIND_COL = {"closed": (255, 50, 40), "crash": (255, 120, 0), "fire": (255, 70, 0), "debris": (255, 190, 0),
            "slide": (230, 140, 60), "flood": (80, 170, 255), "winter": (170, 220, 255), "construction": (255, 200, 0),
            "hazard": (255, 190, 0), "incident": (255, 150, 0)}
KIND_ICON = {"crash": "crash", "fire": "flame", "debris": "warning", "slide": "warning", "flood": "drop", "winter": "drop",
             "construction": "cone", "hazard": "warning", "incident": "warning"}
KIND_WORD = {"crash": "CRASH", "fire": "VEHICLE FIRE", "debris": "DEBRIS ON ROAD", "slide": "LANDSLIDE", "flood": "FLOODING",
             "winter": "SLIPPERY ROADS", "construction": "ROAD WORK", "hazard": "HAZARD", "incident": "INCIDENT"}
DIR = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB", "both directions": "BOTH WAYS"}


def col_of(item):
    return KIND_COL["closed"] if item.get("closed") else KIND_COL.get(item.get("kind"), KIND_COL["incident"])


def icon_of(item):
    return "barrier" if item.get("closed") else KIND_ICON.get(item.get("kind"), "warning")


def _short_road(item):
    hw = item.get("highways") or []
    road = item.get("road") or ""
    if hw and not re.search(r"bridge|tunnel|causeway", road, re.I):
        return f"HWY {hw[0]}"
    return road.upper()


def sign_lines(item):
    """The three lines an overhead sign would show."""
    d = (item.get("description") or "").lower()
    at = item.get("from") if item.get("from") and item.get("from") != item.get("road") else ""
    where = f"AT {at}" if at else (f"NEAR {item['landmark']}" if item.get("landmark") else "")
    l1 = f"{_short_road(item)} {DIR.get((item.get('direction') or '').lower(), '')}".strip()
    if where and len(l1) + len(where) < 36:
        l1 = f"{l1} {where.upper()}"
    if item.get("closed"):
        l2 = "ROAD CLOSED" if "both" in (item.get("direction") or "").lower() else f"{KIND_WORD.get(item.get('kind'), 'INCIDENT')} - CLOSED"
    else:
        m = re.search(r"((?:left|right|centre|center|two|three|one|single|\w+bound)\s+lanes?\s+(?:closed|blocked))", d)
        l2 = KIND_WORD.get(item.get("kind"), "INCIDENT")
        if m:
            l2 = f"{l2} - {m.group(1).upper()}" if len(l2) + len(m.group(1)) < 34 else m.group(1).upper()
    if "detour" in d:
        l3 = "DETOUR IN EFFECT"
    elif "major delay" in d:
        l3 = "EXPECT MAJOR DELAYS"
    elif "delay" in d:
        l3 = "EXPECT DELAYS"
    elif item.get("closed"):
        l3 = "USE ALTERNATE ROUTE"
    elif item.get("kind") == "winter":
        l3 = "DRIVE WITH CARE"
    elif item.get("kind") == "construction":
        l3 = "WATCH FOR CREWS"
    else:
        l3 = "USE CAUTION"
    return [l1, l2, l3]


# ------------------------------------------------------------------------------------------------ shields
def shield(c, x, y, num, h=20):
    """The real sign for a route (BC, Trans-Canada, Yellowhead, Crowsnest…) as LED pixel art; returns its width."""
    from display.modules import shields
    return shields.draw(c, x, y, num, h)


def street_sign(c, x, y, name, f=None, h=None):
    """Green street-name blade for roads without a highway number. Returns its width."""
    f = f or k.font("lutb12")
    h = h or f.h + 4
    w = k.tw(f, name) + 8
    k.box(c, x, y, w, h, (0, 100, 60))
    k.frame(c, x, y, w, h, (200, 220, 200))
    k.text(c, f, x + 4, y + (h - f.h) // 2, WHITE, name)
    return w


def road_badge(c, x, y, item, h=20, f=None, maxw=None):
    """Shield(s) for numbered highways, else a street blade; returns the width used."""
    hw = item.get("highways") or []
    if hw:
        w = 0
        for n in hw[:2]:
            w += shield(c, x + w, y, n, h) + 3
        return w - 3
    name = item.get("road") or ""
    f = f or k.pick(("lutb12", "hvb10", "s8"), name, (maxw or 200) - 8)
    return street_sign(c, x, y, name, f, h)


# ------------------------------------------------------------------------------------------------ helpers
def _desc_paras(item, body=(220, 220, 220), where=(150, 170, 200)):
    out = [Para(item.get("description") or "", "body", body, 0)]
    if item.get("where"):
        out.append(Para(item["where"].replace("\n", " · "), "small", where, 1))
    return out


def _updated(item):
    u = item.get("updated")
    return f"Updated {k.day_hhmm(u)}" if u else ""


def _next(item):
    n = item.get("next_update")
    return f"next update {k.day_hhmm(n)}" if n else ""


def _event_px(mp, item, ox=0, oy=0):
    pts = (item.get("geometry") or {}).get("points") or []
    return [(mp.project(la, lo)[0] + ox, mp.project(la, lo)[1] + oy) for lo, la in pts]


def _draw_event(c, mp, item, now, ox=0, oy=0, sx=0, sy=0, big=True):
    """The affected stretch (flashing) and the event icon on a map placed at (ox, oy) cropped at (sx, sy)."""
    col = col_of(item)
    pts = _event_px(mp, item, ox - sx, oy - sy)
    if not pts:
        return None
    on = k.blink(now, 0.9, 0.6)
    lc = col if on else k.dim(col, 0.45)
    if len(pts) > 1:
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            k.thick_line(c, x0, y0, x1, y1, lc, 3 if big else 2)
    mx = sum(p[0] for p in pts) / len(pts)
    my = sum(p[1] for p in pts) / len(pts)
    r = int(4 + 3 * k.pulse(now, 1.2, 0, 1)) if big else 3
    k.circle(c, mx, my, r + 3, k.dim(col, 0.6))
    k.icon_outlined(c, icon_of(item), int(mx) - 8, int(my) - 7, col if on else WHITE)
    return mx, my


def _cam(s, item):
    cam = item.get("cam")
    if not cam or not s.data_dir:
        return None
    p = os.path.join(s.data_dir, "drivebc", f"cam_{cam['id']}.bin")
    try:
        return np.fromfile(p, dtype=np.uint8).reshape(cam["h"], cam["w"], 3)
    except Exception:
        return None


# ------------------------------------------------------------------------------------------------ vms
def _vms(s, c, item, now, el):
    lines = sign_lines(item)
    sy, sh = 4, 104

    def static():
        # the gantry: dark housing, bolts, the LED face
        k.box(c, 0, 0, W, H, (6, 7, 9))
        k.box(c, 30, 0, 6, sy, (50, 52, 56))
        k.box(c, W - 36, 0, 6, sy, (50, 52, 56))
        k.box(c, 6, sy, W - 12, sh, (44, 46, 50))
        k.frame(c, 6, sy, W - 12, sh, (70, 72, 78))
        for bx in (10, W - 13):
            for by in (sy + 4, sy + sh - 7):
                k.box(c, bx, by, 3, 3, (90, 92, 96))
        k.box(c, 16, sy + 6, W - 32, sh - 12, (0, 0, 0))
        # beneath: shield, the DriveBC text in full, the update time
        y0 = sy + sh + 6
        bw = road_badge(c, 8, y0, item, 20, maxw=150)
        f = k.font("lutb12")
        head = f"{item.get('road') or ''}  {item.get('direction') or ''}".strip()
        k.text(c, k.pick(("lutb12", "hvb10"), head, W - bw - 24), 8 + bw + 8, y0 + 4, WHITE, head)
        lines_, _ = k.fit(_desc_paras(item, (230, 200, 140), (150, 130, 100)), W - 16, H - (y0 + 24) - 12,
                          [{"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}, {"body": "s8", "small": "s8"}])
        k.draw_fit(c, lines_, 8, y0 + 24)
        fm = k.font("s8")
        k.text(c, fm, 8, H - fm.h - 1, (110, 110, 120), "DriveBC")
        k.text_r(c, fm, W - 8, H - fm.h - 1, (110, 110, 120), " · ".join(t for t in (_updated(item), _next(item)) if t))

    s.static(c, ("vms", item["id"], item.get("updated")), static)
    # the sign face: three centred lines of amber LEDs (a lit "pixel grid" look from the dim lattice behind)
    face_x, face_w = 16, W - 32
    for i, ln in enumerate(lines):
        f = k.font("m20b") if k.tw(k.font("m20b"), ln) <= face_w - 12 else k.font("m16b")
        y = sy + 9 + i * 31 + (20 - f.h) // 2
        col = AMB if not (i == 1 and item.get("closed")) or k.blink(now, 1.0, 0.7) else AMB_D
        k.text_c(c, f, face_x + face_w // 2, y, col, ln)
    # flashing beacons on a closure
    if item.get("closed"):
        on = k.blink(now, 0.8)
        k.disc(c, 22, sy + sh // 2, 3, AMB if on else AMB_DD)
        k.disc(c, W - 23, sy + sh // 2, 3, AMB_DD if on else AMB)


# ------------------------------------------------------------------------------------------------ map
def _map(s, c, item, now, el):
    mp = k.Map.load(s.data_dir, "roads_metro")
    col = col_of(item)
    pts = _event_px(mp, item) if mp else []
    ex = sum(p[0] for p in pts) / len(pts) if pts else W / 2
    cw = 186
    cx = 4 if ex > W / 2 else W - cw - 4                     # the card goes on the side away from the event

    def static():
        if mp:
            mp.blit(c, 0, 0)
            hx, hy = mp.project(49.1442, -123.0918)
            k.icon_outlined(c, "house", int(hx) - 8, int(hy) - 7, (200, 200, 210))
        k.shade(c, cx - 2, 2, cw + 4, H - 4, 0.25)
        k.box(c, cx, 4, cw, H - 8, (12, 14, 20))
        k.box(c, cx, 4, cw, 3, col)
        bw = road_badge(c, cx + 6, 12, item, 20, maxw=cw - 12)
        k.icon(c, icon_of(item), cx + cw - 22, 14, col)
        d = item.get("direction") or ""
        road = item.get("road") or ""
        k.text(c, k.pick(("lutb12", "hvb10", "s8"), road, cw - 12), cx + 6, 36, WHITE, road)
        k.text(c, k.font("hv10"), cx + 6, 50, (180, 190, 210), d)
        word = ("CLOSED · " if item.get("closed") else "") + KIND_WORD.get(item.get("kind"), "INCIDENT")
        k.chip(c, k.font("m10b"), cx + 6, 63, word, col, BLACK, 3, 1)
        lines_, _ = k.fit(_desc_paras(item), cw - 12, H - 8 - 80 - 14,
                          [{"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}, {"body": "s8", "small": "s8"}])
        k.draw_fit(c, lines_, cx + 6, 80)
        k.text(c, k.font("s8"), cx + 6, H - 15, (120, 125, 140), _updated(item))

    s.static(c, ("map", item["id"], item.get("updated")), static)
    if mp:
        _draw_event(c, mp, item, now)


# ------------------------------------------------------------------------------------------------ camera
def _camera(s, c, item, now, el):
    col = col_of(item)
    img = _cam(s, item)
    cam = item.get("cam") or {}
    iw, ih = (img.shape[1], img.shape[0]) if img is not None else (176, 103)
    tx = 6 + iw + 10

    def static():
        k.box(c, 0, 0, W, 26, (1, 51, 102))                 # DriveBC blue header
        k.box(c, 0, 26, W, 2, (252, 186, 25))
        bw = road_badge(c, 6, 3, item, 20, maxw=200)
        head = f"{item.get('road') or ''}   {item.get('direction') or ''}".strip()
        k.text(c, k.pick(("h16", "lutb12", "hvb10"), head, W - bw - 20), 6 + bw + 8, 5, WHITE, head)
        # picture (or a map crop when there's no camera nearby)
        y0 = 34
        if img is not None:
            c.Blit(img, 6, y0)
        else:
            mp = k.Map.load(s.data_dir, "roads_metro")
            if mp:
                pts = _event_px(mp, item)
                ex = sum(p[0] for p in pts) / len(pts) if pts else mp.w / 2
                ey = sum(p[1] for p in pts) / len(pts) if pts else mp.h / 2
                sx_, sy_ = int(max(0, min(mp.w - iw, ex - iw / 2))), int(max(0, min(mp.h - ih, ey - ih / 2)))
                mp.blit(c, 6, y0, sx_, sy_, iw, ih)
                s.state["crop"] = (sx_, sy_)
        k.frame(c, 5, y0 - 1, iw + 2, ih + 2, (70, 80, 100))
        fs = k.font("hv10")
        if cam:
            k.fit_text(c, cam.get("caption") or cam.get("name") or "", 6, y0 + ih + 4, iw, 26, ("hv10", "s8"), (170, 180, 200))
            k.text(c, k.font("s8"), 6, H - 10, (120, 125, 140), f"DriveBC camera · {cam.get('km', '?')} km away")
        else:
            k.fit_text(c, "Map: no DriveBC camera within 3 km", 6, y0 + ih + 4, iw, 26, ("hv10", "s8"), (140, 145, 160))
        # details
        word = ("CLOSED · " if item.get("closed") else "") + KIND_WORD.get(item.get("kind"), "INCIDENT")
        k.icon(c, icon_of(item), tx, y0 + 1, col)
        k.chip(c, k.font("m13b"), tx + 22, y0, word, col, BLACK, 4, 1)
        lines_, _ = k.fit(_desc_paras(item), W - tx - 6, H - (y0 + 22) - 16,
                          [{"body": "lut14", "small": "lut12"}, {"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"},
                           {"body": "s8", "small": "s8"}])
        k.draw_fit(c, lines_, tx, y0 + 22)
        k.text_r(c, k.font("s8"), W - 6, H - 10, (120, 125, 140), _updated(item))

    s.static(c, ("cam", item["id"], item.get("updated"), cam.get("at")), static)
    if img is not None:
        # a live dot: the picture is a few minutes old at most
        k.disc(c, 6 + iw - 8, 34 + 7, 3, (255, 40, 40) if k.blink(now, 1.2) else (90, 0, 0))
    elif s.state.get("crop"):
        mp = k.Map.load(s.data_dir, "roads_metro")
        if mp:
            sx_, sy_ = s.state["crop"]
            c.buf[34:34 + ih, 6:6 + iw] = mp.img[sy_:sy_ + ih, sx_:sx_ + iw]
            _draw_event(c, mp, item, now, 6, 34, sx_, sy_, big=False)


# ------------------------------------------------------------------------------------------------ board
def _board(s, c, item, now, el):
    items = s.queue or [item]
    cur = items.index(item) if item in items else 0
    row_h, top = 20, 22
    rows = max(1, min(len(items), 5))
    first = max(0, min(cur - 2, len(items) - rows))
    col = col_of(item)

    def static():
        k.box(c, 0, 0, W, 18, (20, 22, 30))
        k.text(c, k.font("lutb12"), 6, 3, WHITE, "METRO VANCOUVER TRAFFIC")
        n = len(items)
        k.text_r(c, k.font("hv10"), W - 6, 4, (170, 175, 190), f"{n} event{'s' if n != 1 else ''} · DriveBC")
        for r in range(rows):
            it = items[first + r]
            y = top + r * row_h
            on = it is item
            k.box(c, 0, y, W, row_h - 2, (34, 36, 48) if on else (12, 13, 18))
            k.box(c, 0, y, 4, row_h - 2, col_of(it))
            bw = road_badge(c, 8, y + 1, it, 16, f=k.font("hvb10"), maxw=130)
            fx = 8 + max(bw, 26) + 6
            dir_ = DIR.get((it.get("direction") or "").lower(), "")
            word = ("CLOSED " if it.get("closed") else "") + KIND_WORD.get(it.get("kind"), "INCIDENT")
            name = f"{it.get('road') or ''} {dir_}".strip() if it.get("highways") else dir_
            k.text(c, k.font("lutb12"), fx, y + 3, WHITE if on else (190, 190, 200), name)
            k.text(c, k.font("m10b"), fx + (k.tw(k.font("lutb12"), name) + 10 if name else 0), y + 4, col_of(it), word)
            k.text_r(c, k.font("hv10"), W - 6, y + 4, (150, 155, 170), k.day_hhmm(it.get("updated")))
        y = top + rows * row_h + 2
        k.box(c, 0, y, W, 1, col)
        lines_, _ = k.fit(_desc_paras(item), W - 12, H - y - 4,
                          [{"body": "lut14", "small": "lut12"}, {"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"},
                           {"body": "s8", "small": "s8"}])
        k.draw_fit(c, lines_, 6, y + 4)

    s.static(c, ("board", item["id"], len(items), item.get("updated")), static)
    # the focused row's edge marker pulses
    y = top + (cur - first) * row_h
    k.box(c, 0, y, 4, row_h - 2, k.dim(col, k.pulse(now, 1.0, 0.4, 1.0)))


# ------------------------------------------------------------------------------------------------ samples
def _smp(**kw):
    base = {"type": "INCIDENT", "severity": "MAJOR", "closed": False, "category": "majorEvents", "highways": [], "from": "",
            "to": "", "landmark": "", "where": "", "updated_rel": -540, "next_update_rel": 1800}
    base.update(kw)
    return base


SAMPLES = {
    "crash_hwy1": {"label": "Crash on Highway 1 (Surrey)", "item": _smp(
        kind="crash", road="Trans-Canada Highway", highways=["1"], direction="Eastbound", landmark="Surrey",
        description="Vehicle incident at 176 Street. Right lane closed. Expect major delays.",
        where="In Surrey\nAt 176 Street (Hwy 15)", geometry={"type": "Point", "points": [[-122.7375, 49.1655]]})},
    "massey": {"label": "Massey Tunnel closed northbound", "item": _smp(
        kind="crash", road="George Massey Tunnel", highways=["99"], direction="Northbound", closed=True, severity="CLOSURE",
        landmark="Richmond", description="Vehicle incident. Northbound closed. Detour via the Alex Fraser Bridge (Hwy 91). "
        "Expect major delays.", where="Between Hwy 17A and Steveston Hwy",
        geometry={"type": "LineString", "points": [[-123.0758, 49.1100], [-123.0745, 49.1166], [-123.0750, 49.1262]]})},
    "lions_gate": {"label": "Lions Gate Bridge stall", "item": _smp(
        kind="incident", road="Lions Gate Bridge", highways=["99"], direction="Southbound", severity="MINOR",
        description="Stalled vehicle. Centre lane closed southbound. Expect delays.", where="Between Marine Drive and Stanley Park Causeway",
        geometry={"type": "LineString", "points": [[-123.1382, 49.3237], [-123.1386, 49.3148]]})},
    "snow_port_mann": {"label": "Snow on the Port Mann", "item": _smp(
        type="ROAD_CONDITION", kind="winter", road="Port Mann Bridge", highways=["1"], direction="Both directions", severity="MINOR",
        description="Compact snow. Slippery sections. Drive with care; winter tires required.",
        geometry={"type": "LineString", "points": [[-122.8170, 49.2100], [-122.7980, 49.2180]]})},
}

THEMES = {
    "vms": {"label": "Overhead sign", "hint": "An amber variable-message sign (three lines), the full DriveBC text beneath",
            "draw": _vms, "badge": (W - 50, H - 22)},
    "map": {"label": "Metro map", "hint": "Transit-style Metro map, the stretch flashing, a detail card on the far side",
            "draw": _map},
    "camera": {"label": "Highway camera", "hint": "The nearest DriveBC camera (or a map crop) beside the details",
               "draw": _camera, "badge": (W - 50, 6)},
    "board": {"label": "Traffic desk", "hint": "Every current event as a list; the one in focus expanded below",
              "draw": _board, "badge": (230, 4)},
}


class Screen(k.BCScreen):
    PANEL, DATA, SETTINGS = "road_alert", "drivebc", "road_alerts"
    THEMES, SAMPLES, DEFAULT_THEME = THEMES, SAMPLES, "vms"

    def dwell(self, item):
        return max(float(self.settings.get("dwell_sec") or 20), min(45.0, 8 + len((item.get("description") or "").split()) / 3))

    def _queue(self, entry):
        if entry.get("sample") == "_all":                   # the board's preview: every sample at once
            return [self.sample(key) for key in self.SAMPLES]
        return super()._queue(entry)


def render_preview(theme, sample=None, at=4.0, settings=None, data=None, data_dir=None):
    return k.render_preview(Screen, theme, sample, at, settings, data, data_dir)
