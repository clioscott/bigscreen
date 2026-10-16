"""DriveBC screen: incidents, closures and road conditions in Metro Vancouver, using the whole wall.

Data: data/drivebc.json from hub/jobs/drivebc.py — {"items": [{type, kind, severity, closed, road, highways,
direction, from, to, landmark, where, description, geometry: {type, points}, updated, next_update,
cam: {id, name, caption, w, h, km}}]}, webcam stills (data/drivebc/cam_<id>.bin, 336×192 RGB), and traffic maps
drawn for their exact scale by hub/bc/trafficmap.py: roads_metro (the overview) and roadevt_<id> (zoomed to each
event), whose .json lists where each route's shield goes.

DriveBC's text is short ("Vehicle incident. Right lane closed. Expect delays."), so the themes spend the wall on
the road itself: big real shields, big incident icons, direction arrows, and animation that means something —
beacons on closures, chevrons marching along the affected stretch in the direction of travel, a queue creeping
behind a crash, snow falling on a snowy road.

  auto        picks per event: closure for closures, conditions for road conditions, map for the rest
  vms         an overhead variable-message sign on its gantry, hero shield and details beneath
  closure     a road-closed barricade with flashing lamps (cones for lane closures), the road and the detour
  map         the traffic map zoomed to the event: the stretch highlighted, chevrons, route shields, a detail card
  overview    Metro Vancouver with every current event on it, and the list beside
  camera      the nearest DriveBC camera full height, details beside
  strip       the highway as a diagram: lanes moving, the blocked lane, the queue behind it
  board       a traffic-desk board: every event in rows, the one in focus expanded
  conditions  road conditions: snow (or rain) falling over the road ahead
"""

from __future__ import annotations

import math
import os
import re

import numpy as np

from display.modules import bc_kit as k
from display.modules import shields as sh
from display.modules.bc_kit import BLACK, H, W, WHITE, Para

AMB, AMB_D, AMB_DD = (255, 170, 0), (120, 78, 0), (40, 26, 0)
RED = (255, 45, 35)
KIND_COL = {"closed": RED, "crash": (255, 120, 0), "fire": (255, 70, 0), "debris": (255, 190, 0), "slide": (230, 140, 60),
            "flood": (80, 170, 255), "winter": (170, 220, 255), "construction": (255, 200, 0), "hazard": (255, 190, 0),
            "incident": (255, 150, 0)}
KIND_WORD = {"crash": "CRASH", "fire": "VEHICLE FIRE", "debris": "DEBRIS ON ROAD", "slide": "LANDSLIDE", "flood": "FLOODING",
             "winter": "SLIPPERY ROADS", "construction": "ROAD WORK", "hazard": "HAZARD", "incident": "INCIDENT"}
DIR = {"northbound": "NB", "southbound": "SB", "eastbound": "EB", "westbound": "WB", "both directions": "BOTH WAYS"}
DIR_VEC = {"northbound": (0, -1), "southbound": (0, 1), "eastbound": (1, 0), "westbound": (-1, 0)}

# ------------------------------------------------------------------------------------------------ icons (24 px)
ART24 = {
    "barrier": ["........................", "...##..............##...", "...##..............##...", "########################",
                "#..###..###..###..###..#", "##..###..###..###..###.#", "###..###..###..###..####", "########################",
                "...##..............##...", "...##..............##...", "########################", "#..###..###..###..###..#",
                "##..###..###..###..###.#", "###..###..###..###..####", "########################", "...##..............##...",
                "...##..............##...", "...##..............##...", "..####............####..", ".######..........######."],
    "cone": [".........######.........", "........########........", "........########........", ".......##########.......",
             ".......#........#.......", "......#..........#......", "......############......", ".....##############.....",
             ".....##############.....", "....#................#....", "....##################....", "...####################...",
             "...####################...", "..#....................#..", "..######################..", ".########################.",
             "##########################", "##########################"],
    "crash": [".......##########.......", ".....##############.....", "....###..........###....", "...###............###...",
              "..########################", ".##########################", "####....##########....####", "####....##########....####",
              "##########################", "##########################", ".###......#....#......###.", ".###......#....#......###.",
              "..........................", "..#.......##..##.......#..", ".#.#.....#..##..#.....#.#.", "#...#...#........#...#...#"],
    "flame": [".........##.............", "........###.............", "........####............", ".......#####......#.....",
              "......######.....##.....", "......#######...###.....", ".....#########.####.....", ".....##############.....",
              "....################....", "...######..#########....", "...#####....########....", "..#####......########...",
              "..####...##...#######...", "..####..####...######...", "..####.######..######...", "..#####.######.######...",
              "...##############.###...", "...#################....", "....###############.....", "......###########......."],
    "rocks": ["..........######........", "........##########......", ".......####....####.....", "......####......####....",
              "..######.........#####..", ".##########.....#######.", "####....####...#########", "###......###..####..####",
              "####....######.###....##", ".############.####....##", "..##########...########.", "########################"],
}
_ART = {}


def art24(name):
    if name not in _ART:
        rows = ART24[name]
        wd = max(len(r) for r in rows)
        _ART[name] = np.array([[ch == "#" for ch in r.ljust(wd, ".")] for r in rows], bool)
    return _ART[name]


def big_icon(c, item, x, y, col, scale=1):
    """The event's icon at 24 px (or 48 at scale 2): barrier, cone, crash, flame, rocks, snow, flood, warning."""
    kind = "closed" if item.get("closed") else item.get("kind")
    name = {"closed": "barrier", "construction": "cone", "crash": "crash", "fire": "flame", "slide": "rocks"}.get(kind)
    if name:
        m = art24(name)
    else:
        from display.modules import pixel_icons as px
        m = px.mask({"winter": "snow", "flood": "flood"}.get(kind, "warning-diamond"), 24)
    if scale > 1:
        m = np.repeat(np.repeat(m, scale, 0), scale, 1)
    c.BlitMask(m, int(x), int(y), k.C(col))
    return m.shape[1]


def col_of(item):
    return KIND_COL["closed"] if item.get("closed") else KIND_COL.get(item.get("kind"), KIND_COL["incident"])


def word_of(item):
    w = KIND_WORD.get(item.get("kind"), "INCIDENT")
    if item.get("closed"):
        return "ROAD CLOSED" if item.get("kind") == "construction" else f"{w} · CLOSED"
    return w


def arrow(c, cx, cy, direction, size, col):
    """Direction-of-travel arrow (a double arrow for both directions); returns its width."""
    v = DIR_VEC.get((direction or "").lower())
    s = size

    def one(cx, cy, vx, vy):
        hl = s // 2                                            # head: a triangle, its tip at the front
        for i in range(hl):
            hw = i + 1
            if vx:
                k.box(c, cx + vx * (hl - i), cy - hw + 1, 1, 2 * hw - 1, col)
            else:
                k.box(c, cx - hw + 1, cy + vy * (hl - i), 2 * hw - 1, 1, col)
        t = max(2, s // 5)                                     # shaft, behind the head
        if vx:
            k.box(c, cx - hl if vx > 0 else cx, cy - t // 2, hl + 1, t, col)
        else:
            k.box(c, cx - t // 2, cy - hl if vy > 0 else cy, t, hl + 1, col)
    if v:
        one(cx, cy, *v)
    else:
        one(cx - s // 3, cy, 0, -1)
        one(cx + s // 3, cy, 0, 1)
    return s


def hero(c, x, y, item, h=48, maxw=200):
    """The route's real shield(s) big, or a green street blade for roads without a number; returns the width."""
    hw = item.get("highways") or []
    if hw:
        wd = 0
        for n in hw[:2]:
            wd += sh.draw(c, x + wd, y, n, h) + 6
        return wd - 6
    name = item.get("road") or ""
    f = k.pick(("h20", "h18", "h16", "lutb12", "hvb10"), name, maxw - 12)
    bh = max(f.h + 8, h // 2)
    w = k.tw(f, name) + 12
    k.box(c, x, y + (h - bh) // 2, w, bh, (0, 100, 60))
    k.frame(c, x, y + (h - bh) // 2, w, bh, (200, 220, 200))
    k.text(c, f, x + 6, y + (h - f.h) // 2, WHITE, name)
    return w


def beacon(c, x, y, on, r=4):
    if on:
        k.disc(c, x, y, r + 2, (90, 55, 0))
        k.disc(c, x, y, r, AMB)
        k.disc(c, x - 1, y - 1, max(1, r // 2), (255, 230, 150))
    else:
        k.disc(c, x, y, r, (60, 38, 0))


def sign_lines(item):
    """The three lines an overhead sign would show."""
    d = (item.get("description") or "").lower()
    at = item.get("from") if item.get("from") and item.get("from") != item.get("road") else ""
    where = f"AT {at}" if at else (f"NEAR {item['landmark']}" if item.get("landmark") else "")
    hw = item.get("highways") or []
    road = item.get("road") or ""
    short = f"HWY {hw[0]}" if hw and not re.search(r"bridge|tunnel|causeway", road, re.I) else road.upper()
    l1 = f"{short} {DIR.get((item.get('direction') or '').lower(), '')}".strip()
    if where and len(l1) + len(where) < 36:
        l1 = f"{l1} {where.upper()}"
    if item.get("closed"):
        l2 = "ROAD CLOSED" if "both" in (item.get("direction") or "").lower() else f"{KIND_WORD.get(item.get('kind'), 'INCIDENT')} - CLOSED"
    else:
        m = re.search(r"((?:left|right|centre|center|two|three|one|single|\w+bound)\s+lanes?\s+(?:closed|blocked))", d)
        l2 = KIND_WORD.get(item.get("kind"), "INCIDENT")
        if m:
            l2 = f"{l2} - {m.group(1).upper()}" if len(l2) + len(m.group(1)) < 34 else m.group(1).upper()
    l3 = ("DETOUR IN EFFECT" if "detour" in d else "EXPECT MAJOR DELAYS" if "major delay" in d else "EXPECT DELAYS" if "delay" in d
          else "USE ALTERNATE ROUTE" if item.get("closed") else "DRIVE WITH CARE" if item.get("kind") == "winter"
          else "WATCH FOR CREWS" if item.get("kind") == "construction" else "USE CAUTION")
    return [l1, l2, l3]


def _desc(item, body=(225, 225, 225), where=(150, 170, 200)):
    out = [Para(item.get("description") or "", "body", body, 0)]
    if item.get("where"):
        out.append(Para(item["where"].replace("\n", " · "), "small", where, 1))
    return out


def _updated(item):
    u = item.get("updated")
    return f"Updated {k.day_hhmm(u)}" if u else ""


# ------------------------------------------------------------------------------------------------ maps
def _evt_map(s, item):
    mid = "roadevt_" + re.sub(r"[^A-Za-z0-9_-]", "_", str(item.get("id")))
    return k.Map.load(s.data_dir, mid) or k.Map.load(s.data_dir, "roads_metro")


def _px(mp, item, ox=0, oy=0):
    return [(mp.project(la, lo)[0] + ox, mp.project(la, lo)[1] + oy) for lo, la in (item.get("geometry") or {}).get("points") or []]


def _centre(pts):
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else (W / 2, H / 2)


def _route_shields(c, mp, avoid=(), h=14, ox=0, oy=0, clip=(0, 0, W, H)):
    """The real shield on each route, where the map's renderer placed them (none over the event)."""
    for a in mp.meta.get("shields") or []:
        x, y = a["x"] + ox, a["y"] + oy
        if any((x - ax) ** 2 + (y - ay) ** 2 < 30 ** 2 for ax, ay in avoid):
            continue
        wd = sh.width(a["num"], h, a["kind"])
        if clip[0] <= x - wd / 2 and x + wd / 2 <= clip[2] and clip[1] <= y - h / 2 and y + h / 2 <= clip[3]:
            sh.draw(c, int(x - wd / 2), int(y - h / 2), a["num"], h, kind=a["kind"])


def _march(c, pts, now, col, forward=True, spacing=9, speed=14):
    """Chevrons moving along a polyline, in its drawing order or against it."""
    segs, total = [], 0.0
    for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
        L = math.hypot(x1 - x0, y1 - y0)
        if L > 0:
            segs.append((x0, y0, x1, y1, L, total))
            total += L
    if total < spacing:
        return
    d = (now * speed) % spacing
    while d < total:
        dd = d if forward else total - d
        for x0, y0, x1, y1, L, at in segs:
            if at <= dd <= at + L:
                t = (dd - at) / L
                x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
                ux, uy = ((x1 - x0) / L, (y1 - y0) / L) if forward else (-(x1 - x0) / L, -(y1 - y0) / L)
                nx, ny = -uy, ux
                k.line(c, x - 3 * ux + 2.5 * nx, y - 3 * uy + 2.5 * ny, x, y, col)
                k.line(c, x - 3 * ux - 2.5 * nx, y - 3 * uy - 2.5 * ny, x, y, col)
                break
        d += spacing


def _travel_forward(item, pts):
    v = DIR_VEC.get((item.get("direction") or "").lower())
    if not v or len(pts) < 2:
        return None
    return (pts[-1][0] - pts[0][0]) * v[0] + (pts[-1][1] - pts[0][1]) * v[1] >= 0


def _draw_stretch(c, mp, item, now, ox=0, oy=0, big=True):
    """The affected stretch in the alert colour (glow, chevrons in the travel direction) and the event marker."""
    col = col_of(item)
    pts = _px(mp, item, ox, oy)
    if not pts:
        return None
    if len(pts) > 1:
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            k.thick_line(c, x0, y0, x1, y1, k.dim(col, 0.5), 5 if big else 3)
        for (x0, y0), (x1, y1) in zip(pts[:-1], pts[1:]):
            k.thick_line(c, x0, y0, x1, y1, col, 3 if big else 2)
        fw = _travel_forward(item, pts)
        if fw is not None and big:
            _march(c, pts, now, WHITE, fw)
    mx, my = _centre(pts)
    if big:
        for i in range(2):
            r = ((now * 16 + i * 10) % 20) + 14
            k.circle(c, mx, my, r, k.dim(col, max(0.15, 1 - (r - 14) / 20)))
        k.box(c, mx - 14, my - 14, 28, 28, BLACK)
        k.frame(c, mx - 14, my - 14, 28, 28, col)
        big_icon(c, item, mx - 12, my - 10, col)
    else:
        k.disc(c, mx, my, 4 + int(2 * k.pulse(now, 1.0, 0, 1)), col)
    return mx, my


def _cam(s, item):
    cam = item.get("cam")
    if not cam or not s.data_dir:
        return None
    try:
        return np.fromfile(os.path.join(s.data_dir, "drivebc", f"cam_{cam['id']}.bin"), dtype=np.uint8).reshape(cam["h"], cam["w"], 3)
    except Exception:
        return None


# ================================================================================================ themes
def _vms(s, c, item, now, el):
    lines = sign_lines(item)
    col = col_of(item)
    sy, sh_ = 9, 98

    def static():
        k.box(c, 0, 0, W, H, (5, 6, 8))
        k.box(c, 0, 0, W, 2, (70, 72, 78))                    # the gantry truss
        k.box(c, 0, 6, W, 1, (70, 72, 78))
        for x in range(0, W, 8):
            k.line(c, x, 2, x + 4, 5, (55, 57, 62))
            k.line(c, x + 4, 2, x + 8, 5, (55, 57, 62))
        k.box(c, 24, sy, W - 48, sh_, (44, 46, 50))
        k.frame(c, 24, sy, W - 48, sh_, (80, 82, 88))
        k.box(c, 32, sy + 6, W - 64, sh_ - 12, BLACK)
        y0 = sy + sh_ + 7
        bw = hero(c, 8, y0, item, 48, 170)
        x = 8 + bw + 10
        f = k.pick(("h20", "h18", "h16", "lutb12"), item.get("road") or "", W - x - 56)
        k.text(c, f, x, y0, WHITE, item.get("road") or "")
        arrow(c, x + 9, y0 + f.h + 11, item.get("direction"), 16, AMB)
        k.text(c, k.font("lutb12"), x + 22, y0 + f.h + 5, (210, 210, 220), item.get("direction") or "")
        k.box(c, W - 44, y0, 36, 36, (20, 20, 24))
        k.frame(c, W - 44, y0, 36, 36, col)
        big_icon(c, item, W - 38, y0 + 6, col)
        ty = y0 + f.h + 24
        lines_, _ = k.fit(_desc(item, (230, 205, 150), (150, 135, 110)), W - x - 52, H - ty - 1,
                          [{"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}, {"body": "s8", "small": "s8"}])
        k.draw_fit(c, lines_, x, ty)

    s.static(c, ("vms", item["id"], item.get("updated")), static)
    fw = W - 64
    for i, ln in enumerate(lines):
        f = k.font("m20b") if k.tw(k.font("m20b"), ln) <= fw - 12 else k.font("m16b")
        y = sy + 10 + i * 29 + (20 - f.h) // 2
        lit = not (i == 1 and (item.get("closed") or item.get("severity") == "MAJOR")) or k.blink(now, 1.0, 0.72)
        k.text_c(c, f, 32 + fw // 2, y, AMB if lit else AMB_DD, ln)
    on = k.blink(now, 0.8)
    for bx, by, ph in ((13, sy + 25, on), (13, sy + sh_ - 25, not on), (W - 14, sy + 25, not on), (W - 14, sy + sh_ - 25, on)):
        beacon(c, bx, by, ph)


def _closure(s, c, item, now, el):
    closed = bool(item.get("closed"))
    bx, by, bw = 8, 30, 196

    def static():
        k.box(c, 0, 0, W, H, (8, 6, 4))
        if closed:                                             # the barricade
            for px in (bx + 22, bx + bw - 30):
                k.box(c, px, by + 20, 8, H - by - 30, (150, 150, 155))
                k.box(c, px - 6, H - 12, 20, 4, (110, 110, 115))
            for j in range(3):
                yy = by + 22 + j * 34
                k.box(c, bx, yy, bw, 22, WHITE)
                k.hatch(c, bx, yy, bw, 22, (255, 120, 0), 8, j * 3)
                k.frame(c, bx, yy, bw, 22, (60, 40, 20))
        else:                                                  # a row of cones
            for j in range(3):
                cx = bx + 30 + j * 66
                for r in range(46):
                    wd = 4 + r * 34 // 46
                    k.box(c, cx - wd // 2, by + 40 + r, wd, 1, (255, 120, 0) if (r // 9) % 2 == 0 else WHITE)
                k.box(c, cx - 26, by + 86, 52, 6, (255, 120, 0))
        x0 = bx + bw + 16
        d = (item.get("description") or "").lower()
        head = ("ROAD CLOSED" if closed and "both" in (item.get("direction") or "").lower() else "CLOSED" if closed
                else "LANE CLOSED" if "lane" in d else "CAUTION")
        f = k.pick(("h24", "h20"), head, W - x0 - 4)
        k.box(c, x0 - 6, 6, W - x0 + 2, f.h + 8, RED if closed else (255, 140, 0))
        k.text(c, f, x0, 10, WHITE if closed else BLACK, head)
        y = 6 + f.h + 14
        hw = hero(c, x0, y, item, 40, W - x0 - 8)
        rx = x0 + hw + 8
        k.fit_text(c, item.get("road") or "", rx, y + 1, W - rx - 6, 22, ("h16", "lutb12", "hvb10"), WHITE)
        arrow(c, rx + 8, y + 32, item.get("direction"), 14, AMB)
        k.text(c, k.font("hvb10"), rx + 20, y + 27, (220, 220, 230), item.get("direction") or "")
        y += 48
        lines_, _ = k.fit(_desc(item), W - x0 - 6, H - y - 14,
                          [{"body": "lutb14", "small": "lut12"}, {"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}])
        k.draw_fit(c, lines_, x0, y)
        k.text(c, k.font("s8"), x0, H - 10, (130, 120, 110), f"DriveBC · {_updated(item)}")

    s.static(c, ("clo", item["id"], item.get("updated")), static)
    on = k.blink(now, 0.7)
    if closed:
        for i, px in enumerate((bx + 26, bx + bw // 2, bx + bw - 26)):
            beacon(c, px, by + 12, on if i != 1 else not on, 6)
    else:
        for j in range(3):
            beacon(c, bx + 30 + j * 66, by + 32, on if j % 2 == 0 else not on, 4)


def _map(s, c, item, now, el):
    mp = _evt_map(s, item)
    col = col_of(item)
    pts = _px(mp, item) if mp else []
    ex, ey = _centre(pts)
    cw, ch = 214, 78
    # the card takes the corner farthest from the incident
    corners = [(6, 22), (W - cw - 6, 22), (6, H - ch - 6), (W - cw - 6, H - ch - 6)]
    cx, cy = max(corners, key=lambda p: (p[0] + cw / 2 - ex) ** 2 + (p[1] + ch / 2 - ey) ** 2)

    def static():
        if mp:
            mp.blit(c, 0, 0)
            _route_shields(c, mp, avoid=[(ex, ey)], h=14)
        k.shade(c, cx - 2, cy - 2, cw + 4, ch + 4, 0.2)        # the detail card
        k.box(c, cx, cy, cw, ch, (10, 12, 18))
        k.box(c, cx, cy, 3, ch, col)
        hw = hero(c, cx + 8, cy + 6, item, 32, 100)
        tx = cx + 8 + hw + 8
        k.chip(c, k.font("m10b"), tx, cy + 6, word_of(item), col, BLACK, 3, 1)
        arrow(c, tx + 6, cy + 27, item.get("direction"), 10, AMB)
        k.text(c, k.font("hvb10"), tx + 14, cy + 22, (220, 220, 230), item.get("direction") or "")
        lines_, _ = k.fit([Para(item.get("description") or "", "body", (220, 220, 225))], cw - 14, ch - 42,
                          [{"body": "hv10"}, {"body": "s8"}, {"body": "s7"}])
        k.draw_fit(c, lines_, cx + 8, cy + 40)
        t = item.get("road") or ""
        f = k.pick(("h16", "lutb12"), t, W - 160)
        k.shade(c, 0, 0, k.tw(f, t) + 14, f.h + 6, 0.3)
        k.text(c, f, 6, 3, WHITE, t)
        k.text_r(c, k.font("s8"), W - 6, 4, (150, 155, 170), f"DriveBC · {_updated(item)}")

    s.static(c, ("map", item["id"], item.get("updated")), static)
    if mp:
        _draw_stretch(c, mp, item, now)


def _overview(s, c, item, now, el):
    mp = k.Map.load(s.data_dir, "roads_metro")
    items = s.queue or [item]
    lw = 152

    def static():
        if mp:
            mp.blit(c, 0, 0, 0, 0, W - lw, H)
            _route_shields(c, mp, avoid=[_centre(_px(mp, it)) for it in items if _px(mp, it)], h=12, clip=(0, 0, W - lw, H))
        k.box(c, W - lw, 0, lw, H, (10, 11, 16))
        k.box(c, W - lw, 0, 1, H, (50, 56, 70))
        k.text(c, k.font("lutb12"), W - lw + 6, 3, WHITE, "METRO TRAFFIC")
        y = 20
        for it in items[:7]:
            on = it is item
            if on:
                k.box(c, W - lw + 1, y - 2, lw - 1, 23, (30, 32, 44))
            k.box(c, W - lw + 1, y - 2, 3, 23, col_of(it))
            hw_ = sh.draw(c, W - lw + 7, y, it["highways"][0], 18) if it.get("highways") else 0
            tx = W - lw + 9 + hw_ + 4
            k.text(c, k.pick(("hvb10", "s8"), it.get("road") or "", W - tx - 4), tx, y, WHITE if on else (190, 190, 200), it.get("road") or "")
            k.text(c, k.font("s8"), tx, y + 10, col_of(it), word_of(it))
            y += 24

    s.static(c, ("ov", item["id"], len(items)), static)
    if mp:
        for it in items:
            if it is not item:
                pts = _px(mp, it)
                if pts:
                    k.disc(c, *_centre(pts), 3, col_of(it))
        _draw_stretch(c, mp, item, now, big=False)


def _camera(s, c, item, now, el):
    col = col_of(item)
    img = _cam(s, item)
    cam = item.get("cam") or {}
    iw = 336
    px_ = iw + 2

    def static():
        if img is not None:
            c.Blit(img, 0, 0)
            k.shade(c, 0, H - 15, iw, 15, 0.3)
            k.fit_text(c, cam.get("caption") or cam.get("name") or "", 4, H - 13, iw - 70, 12, ("hv10", "s8"), (230, 230, 235))
            k.text_r(c, k.font("s8"), iw - 4, H - 11, (200, 200, 210), f"{cam.get('km', '?')} km away")
        else:
            mp = _evt_map(s, item)
            if mp:
                ex, _ = _centre(_px(mp, item))
                sx_ = int(max(0, min(mp.w - iw, ex - iw / 2)))
                s.state["crop"] = sx_
                mp.blit(c, 0, 0, sx_, 0, iw, H)
                _route_shields(c, mp, avoid=[(ex, H / 2)], h=14, ox=-sx_, clip=(0, 0, iw, H))
            k.text(c, k.font("s8"), 4, H - 11, (150, 155, 170), "No DriveBC camera within 3 km: the map")
        k.box(c, iw, 0, W - iw, H, (10, 12, 18))
        k.box(c, iw, 0, 2, H, col)
        y = 6
        hero(c, px_ + 6, y, item, 40, W - px_ - 12)
        y += 46
        y += k.fit_text(c, item.get("road") or "", px_ + 6, y, W - px_ - 10, 26, ("lutb12", "hvb10", "s8"), WHITE) + 4
        arrow(c, px_ + 12, y + 7, item.get("direction"), 12, AMB)
        k.text(c, k.font("hvb10"), px_ + 24, y + 2, (210, 210, 220), DIR.get((item.get("direction") or "").lower(), ""))
        big_icon(c, item, W - 30, y - 4, col)
        y += 24
        k.chip(c, k.font("s8"), px_ + 6, y, word_of(item), col, BLACK, 2, 1)
        y += 14
        lines_, _ = k.fit(_desc(item), W - px_ - 12, H - y - 2, [{"body": "hv10", "small": "s8"}, {"body": "s8", "small": "s8"}])
        k.draw_fit(c, lines_, px_ + 6, y)

    s.static(c, ("cam", item["id"], item.get("updated"), cam.get("at")), static)
    if img is not None:
        k.box(c, 4, 4, 40, 13, BLACK)
        k.disc(c, 11, 10, 3, (255, 40, 40) if k.blink(now, 1.2) else (90, 0, 0))
        k.text(c, k.font("s8"), 17, 6, WHITE, "LIVE")
    elif "crop" in s.state:
        mp = _evt_map(s, item)
        if mp:
            _draw_stretch(c, mp, item, now, ox=-s.state["crop"])


def _strip(s, c, item, now, el):
    """The highway as a diagram across the wall: lanes moving, the blocked lane, the queue behind it."""
    col = col_of(item)
    ry, rh = 98, 42
    d = (item.get("direction") or "").lower()
    fwd = d not in ("westbound", "southbound")
    sgn = 1 if fwd else -1
    ix = W // 2 + 40 * sgn
    lanes = 3
    lh = (rh - 4) // lanes
    desc = (item.get("description") or "").lower()
    lane_hit = re.search(r"lanes?\s+(closed|blocked)", desc)
    blocked = (set(range(lanes)) if item.get("closed") else set() if not lane_hit else {0} if "left" in desc
               else {1} if "centre" in desc or "center" in desc else {lanes - 1})
    slow = bool(blocked) or "delay" in desc

    def static():
        k.box(c, 0, 0, W, H, (6, 8, 10))
        k.box(c, 6, 6, 52, 52, (18, 18, 22))
        k.frame(c, 6, 6, 52, 52, col)
        big_icon(c, item, 8, 8, col, 2)
        hw = hero(c, 66, 6, item, 40, 180)
        x = 66 + hw + 10
        k.text(c, k.pick(("h20", "h18", "h16", "lutb12"), item.get("road") or "", W - x - 6), x, 6, WHITE, item.get("road") or "")
        k.chip(c, k.font("m13b"), x, 30, word_of(item), col, BLACK, 4, 1)
        lines_, _ = k.fit([Para(item.get("description") or "", "body", (220, 220, 225))], W - 12, ry - 64 - 16,
                          [{"body": "lut12"}, {"body": "hv10"}, {"body": "s8"}])
        k.draw_fit(c, lines_, 6, 64)
        k.box(c, 0, ry, W, rh, (40, 40, 44))                  # the road
        k.box(c, 0, ry, W, 2, (200, 200, 200))
        k.box(c, 0, ry + rh - 2, W, 2, (200, 200, 200))
        a, b = (item.get("from") or item.get("landmark") or ""), (item.get("to") or "")
        left, right = (a, b) if fwd else (b, a)
        k.text(c, k.font("hvb10"), 4, ry + rh + 6, (180, 185, 200), left)
        k.text_r(c, k.font("hvb10"), W - 4, ry + rh + 6, (180, 185, 200), right)
        if d in DIR_VEC or "both" in d:
            arrow(c, W // 2, ry + rh + 12, "eastbound" if fwd else "westbound", 14, AMB) if d in DIR_VEC else None
            k.text_c(c, k.font("hvb10"), W // 2, ry + rh + 22, (210, 210, 220), item.get("direction") or "")
        k.text(c, k.font("s8"), 4, H - 10, (120, 125, 140), f"DriveBC · {_updated(item)} · diagram, not to scale")

    s.static(c, ("strip", item["id"], item.get("updated")), static)
    ph = int(now * 30 * sgn) % 16
    for l in range(1, lanes):
        y = ry + 2 + l * lh
        for x in range(-16, W + 16, 16):
            k.box(c, x + ph, y, 8, 1, (220, 220, 200))
    for l in range(lanes):
        y = ry + 3 + l * lh
        if l in blocked:
            k.box(c, ix - 18, y + 1, 36, lh - 2, (90, 0, 0))
            k.hatch(c, ix - 18, y + 1, 36, lh - 2, RED, 3, int(now * 8))
        for j in range(9 if slow else 0):
            q = j * 20 + (now * (2 if l in blocked else 9)) % 20     # the queue creeps; open lanes crawl past
            qx = ix - 28 * sgn - sgn * (180 - q)
            if 0 <= qx < W and (l in blocked or j % 2 == 0):
                cc = (255, 60, 50) if l in blocked else (220, 120, 120)
                k.box(c, int(qx) - 6, y + 2, 12, lh - 4, cc)
        for j in range(0 if item.get("closed") or l in blocked else 4):            # traffic moving past
            fx = ix + sgn * (36 + ((j * 60 + now * (50 if slow else 30)) % (W // 2 - 60))) if slow else                 (j * 117 + now * 35 * sgn) % W
            if 0 <= fx < W:
                k.box(c, int(fx) - 6, y + 2, 12, lh - 4, (230, 230, 240))
    k.box(c, ix - 14, ry - 15, 28, 28, BLACK)
    k.frame(c, ix - 14, ry - 15, 28, 28, col)
    big_icon(c, item, ix - 12, ry - 12, col)


def _board(s, c, item, now, el):
    items = s.queue or [item]
    cur = items.index(item) if item in items else 0
    row_h, top = 26, 20
    rows = max(1, min(len(items), 4))
    first = max(0, min(cur - 1, len(items) - rows))
    col = col_of(item)

    def static():
        k.box(c, 0, 0, W, 17, (20, 22, 30))
        k.text(c, k.font("lutb12"), 6, 2, WHITE, "METRO VANCOUVER TRAFFIC")
        n = len(items)
        k.text_r(c, k.font("hv10"), W - 6, 3, (170, 175, 190), f"{n} event{'s' if n != 1 else ''} · DriveBC")
        for r in range(rows):
            it = items[first + r]
            y = top + r * row_h
            on = it is item
            k.box(c, 0, y, W, row_h - 2, (34, 36, 48) if on else (12, 13, 18))
            hw_ = hero(c, 8, y + 1, it, 22, 120)
            fx = 8 + hw_ + 8
            dir_ = DIR.get((it.get("direction") or "").lower(), "")
            name = f"{it.get('road') or ''} {dir_}".strip() if it.get("highways") else dir_
            k.text(c, k.font("lutb12"), fx, y + 6, WHITE if on else (190, 190, 200), name)
            k.text(c, k.font("m10b"), fx + (k.tw(k.font("lutb12"), name) + 10 if name else 0), y + 7, col_of(it), word_of(it))
            k.text_r(c, k.font("hv10"), W - 6, y + 7, (150, 155, 170), k.day_hhmm(it.get("updated")))
        y = top + rows * row_h + 2
        k.box(c, 0, y, W, 1, col)
        big_icon(c, item, 6, y + 6, col)
        lines_, _ = k.fit(_desc(item), W - 44, H - y - 4,
                          [{"body": "lut14", "small": "lut12"}, {"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}])
        k.draw_fit(c, lines_, 38, y + 6)

    s.static(c, ("board", item["id"], len(items), item.get("updated")), static)
    y = top + (cur - first) * row_h
    if item.get("closed"):
        k.hatch(c, 0, y, 4, row_h - 2, RED, 2, int(now * 8))
    else:
        k.box(c, 0, y, 4, row_h - 2, k.dim(col, k.pulse(now, 1.0, 0.4, 1.0)))


def _conditions(s, c, item, now, el):
    col = col_of(item)
    desc = (item.get("description") or "").lower()
    snowy = item.get("kind") == "winter" and any(w in desc for w in ("snow", "ice", "slush", "frost", "winter"))

    def static():
        k.gradient_v(c, 0, 0, W, H, (16, 22, 34), (4, 6, 10))
        for y in range(112, H):                               # the road ahead, in perspective
            t = (y - 112) / (H - 112)
            half = int(30 + t * 200)
            k.box(c, W // 2 - half, y, 2 * half, 1, (60, 64, 72) if snowy else (36, 38, 44))
            k.box(c, W // 2 - half, y, 2, 1, (200, 200, 200))
            k.box(c, W // 2 + half - 2, y, 2, 1, (200, 200, 200))
        k.box(c, 6, 6, 52, 52, (14, 20, 30))
        k.frame(c, 6, 6, 52, 52, col)
        big_icon(c, item, 8, 8, col, 2)
        hw = hero(c, 66, 6, item, 40, 160)
        x = 66 + hw + 10
        k.text(c, k.pick(("h20", "h18", "h16", "lutb12"), item.get("road") or "", W - x - 6), x, 6, WHITE, item.get("road") or "")
        k.text(c, k.font("lutb12"), x, 30, col, "ROAD CONDITIONS" if item.get("kind") == "winter" else word_of(item))
        lines_, _ = k.fit(_desc(item, (230, 235, 245)), W - 16, 46,
                          [{"body": "lutb14", "small": "lut12"}, {"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}])
        k.draw_fit(c, lines_, 8, 62)

    s.static(c, ("cond", item["id"], item.get("updated")), static)
    for i in range(6):                                         # centre dashes rushing toward us
        t = (now * 0.6 + i / 6) % 1.0
        y = int(114 + t * t * 76)
        lw = int(2 + t * 6)
        k.box(c, W // 2 - lw // 2, y, lw, max(1, int(2 + t * 8)), (230, 200, 60))
    for i in range(70):                                        # snow, or rain
        x = (i * 73 + int(now * (12 + i % 5 * 4))) % W
        y = (i * 41 + int(now * (18 + (i % 4) * 9))) % H
        if snowy:
            k.box(c, x, y, 1 + (i % 3 == 0), 1 + (i % 3 == 0), (235, 240, 255))
        else:
            k.line(c, x, y, x - 1, y + 4, (110, 140, 190))


def _auto(s, c, item, now, el):
    t = "closure" if item.get("closed") else "conditions" if item.get("kind") in ("winter", "flood") else "map"
    return THEMES[t]["draw"](s, c, item, now, el)


# ------------------------------------------------------------------------------------------------ samples
def _smp(**kw):
    base = {"type": "INCIDENT", "severity": "MAJOR", "closed": False, "category": "majorEvents", "highways": [], "from": "",
            "to": "", "landmark": "", "where": "", "updated_rel": -540, "next_update_rel": 1800}
    base.update(kw)
    return base


SAMPLES = {
    "crash_hwy1": {"label": "Crash on Highway 1 (Surrey)", "item": _smp(
        kind="crash", road="Trans-Canada Highway", highways=["1"], direction="Eastbound", landmark="Surrey",
        **{"from": "176 Street", "to": "200 Street"},
        description="Vehicle incident at 176 Street. Right lane closed. Expect major delays.",
        where="In Surrey\nAt 176 Street (Hwy 15)", geometry={"type": "LineString", "points": [[-122.73269, 49.1808], [-122.72987, 49.18006], [-122.72697, 49.17929], [-122.72502, 49.17878]]})},
    "massey": {"label": "Massey Tunnel closed northbound", "item": _smp(
        kind="crash", road="George Massey Tunnel", highways=["99"], direction="Northbound", closed=True, severity="CLOSURE",
        landmark="Richmond", **{"from": "Highway 17A", "to": "Steveston Highway"},
        description="Vehicle incident. Northbound closed. Detour via the Alex Fraser Bridge (Hwy 91). Expect major delays.",
        where="Between Hwy 17A and Steveston Hwy",
        geometry={"type": "LineString", "points": [[-123.07196, 49.1188], [-123.07845, 49.12408]]})},
    "lions_gate": {"label": "Lions Gate Bridge stall", "item": _smp(
        kind="incident", road="Lions Gate Bridge", highways=["99"], direction="Southbound", severity="MINOR",
        **{"from": "Marine Drive", "to": "Stanley Park Causeway"},
        description="Stalled vehicle. Centre lane closed southbound. Expect delays.", where="Between Marine Drive and Stanley Park Causeway",
        geometry={"type": "LineString", "points": [[-123.13088, 49.32379], [-123.1419, 49.31181]]})},
    "snow_port_mann": {"label": "Snow on the Port Mann", "item": _smp(
        type="ROAD_CONDITION", kind="winter", road="Port Mann Bridge", highways=["1"], direction="Both directions", severity="MINOR",
        description="Compact snow. Slippery sections. Drive with care; winter tires required.",
        geometry={"type": "LineString", "points": [[-122.80834, 49.21108], [-122.80856, 49.21125], [-122.8091, 49.21168], [-122.80935, 49.2119], [-122.80959, 49.21214], [-122.8099, 49.21251]]})},
}

THEMES = {
    "auto": {"label": "Automatic", "hint": "Barricade for closures, the road scene for conditions, the incident map otherwise",
             "draw": _auto, "badge": (W - 50, H - 12)},
    "vms": {"label": "Overhead sign", "hint": "Amber variable-message sign on its gantry with beacons; hero shield, direction, icon beneath",
            "draw": _vms, "badge": (W - 96, H - 12)},
    "closure": {"label": "Road closed", "hint": "A barricade with flashing lamps (cones for lane closures), the shield, road, direction, detour",
                "draw": _closure, "badge": (W - 50, H - 12)},
    "map": {"label": "Incident map", "hint": "Traffic map zoomed to the event: stretch highlighted, chevrons in the travel direction, shields",
            "draw": _map, "badge": (W - 120, 14)},
    "overview": {"label": "Metro overview", "hint": "Every current event on the Metro traffic map with route shields, the list beside",
                 "draw": _overview, "badge": (4, H - 14)},
    "camera": {"label": "Highway camera", "hint": "The nearest DriveBC camera full height (or the zoomed map), shield and details beside",
               "draw": _camera, "badge": (4, 20)},
    "strip": {"label": "Route diagram", "hint": "The highway as a diagram: lanes moving, the blocked lane, the queue creeping behind",
              "draw": _strip, "badge": (W - 50, H - 12)},
    "board": {"label": "Traffic desk", "hint": "Every current event in rows with shields; the one in focus expanded below",
              "draw": _board, "badge": (230, 3)},
    "conditions": {"label": "Road conditions", "hint": "Snow or rain falling over the road ahead; the shield and the conditions",
                   "draw": _conditions, "badge": (W - 50, H - 12)},
}


class Screen(k.BCScreen):
    PANEL, DATA, SETTINGS = "road_alert", "drivebc", "road_alerts"
    THEMES, SAMPLES, DEFAULT_THEME = THEMES, SAMPLES, "auto"

    def dwell(self, item):
        return max(float(self.settings.get("dwell_sec") or 20), min(45.0, 10 + len((item.get("description") or "").split()) / 3))

    def _queue(self, entry):
        if entry.get("sample") == "_all":                     # list previews: every sample at once
            return [self.sample(key) for key in self.SAMPLES]
        return super()._queue(entry)


def render_preview(theme, sample=None, at=4.0, settings=None, data=None, data_dir=None):
    return k.render_preview(Screen, theme, sample, at, settings, data, data_dir)
