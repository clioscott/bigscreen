"""DriveBC screen: incidents, closures and road conditions in Metro Vancouver, using the whole wall.

Data: data/drivebc.json from hub/jobs/drivebc.py — {"items": [{type, kind, severity, closed, road, highways,
direction, from, to, landmark, where, description, geometry: {type, points}, updated, next_update,
cam: {id, name, caption, w, h, km}}]}, webcam stills (data/drivebc/cam_<id>.bin, 336×192 RGB), and traffic maps
drawn for their exact scale by hub/bc/trafficmap.py: roads_metro (the overview) and roadevt_<id> (zoomed to each
event), whose .json lists where each route's shield goes.

DriveBC's text is short ("Vehicle incident. Right lane closed. Expect delays."), so the themes spend the wall on
the road itself: big real shields, the hazard icon in the side panel, and maps whose only addition is the
affected road (the real OSM road the event is on, matched by the hub) in alternating colours running along it.

  map       the map zoomed to the event, the affected road highlighted; shield, icon and details beside
  hero      a 64-px shield and the essentials in large type, the map beside
  vms       an overhead variable-message sign, the big shield and details beneath
  overview  every current event's road highlighted on the Metro map, the list beside
  camera    the nearest DriveBC camera full height, details beside
  board     a traffic-desk board: every event in rows, the one in focus expanded
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
    name = {"closed": "barrier", "construction": "cone", "fire": "flame", "slide": "rocks"}.get(kind)
    if name:
        m = art24(name)
    else:
        from display.modules import pixel_icons as px
        m = px.mask({"winter": "snow", "flood": "flood", "crash": "car"}.get(kind, "warning-diamond"), 24)
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


def _route_shields(c, mp, avoid=(), h=14, ox=0, oy=0, clip=(0, 0, W, H), block=()):
    """The real shield on each route, where the map's renderer placed them (none over the event)."""
    for a in mp.meta.get("shields") or []:
        x, y = a["x"] + ox, a["y"] + oy
        if any((x - ax) ** 2 + (y - ay) ** 2 < 16 ** 2 for ax, ay in avoid):
            continue
        wd = sh.width(a["num"], h, a["kind"])
        if any(bx0 - wd / 2 - 2 <= x <= bx1 + wd / 2 + 2 and by0 - h / 2 - 2 <= y <= by1 + h / 2 + 2 for bx0, by0, bx1, by1 in block):
            continue
        if clip[0] <= x - wd / 2 and x + wd / 2 <= clip[2] and clip[1] <= y - h / 2 and y + h / 2 <= clip[3]:
            sh.draw(c, int(x - wd / 2), int(y - h / 2), a["num"], h, kind=a["kind"])


def _cam(s, item):
    cam = item.get("cam")
    if not cam or not s.data_dir:
        return None
    try:
        return np.fromfile(os.path.join(s.data_dir, "drivebc", f"cam_{cam['id']}.bin"), dtype=np.uint8).reshape(cam["h"], cam["w"], 3)
    except Exception:
        return None


# ================================================================================================ themes
# The road is the graphic: real shields large, plain side panels (with the hazard icon), and maps where the only
# addition is the affected road itself — the OSM ways the event is on (matched by the hub) — drawn in alternating
# alert colours that run along it, so it stands out without anything covering it.

def _lines(item):
    """The affected road as [[lon, lat], ...] lines: the matched OSM road, else DriveBC's own geometry."""
    g = (item.get("geometry") or {}).get("points")
    return item.get("road_lines") or ([g] if g else [])


def _hl_px(mp, item, ox=0, oy=0):
    return [[(mp.project(la, lo)[0] + ox, mp.project(la, lo)[1] + oy) for lo, la in ln] for ln in _lines(item)]


def _hl_avoid(mp, item, ox=0, oy=0):
    """Points along the highlighted road that route shields must keep clear of."""
    pts = [p for ln in _hl_px(mp, item, ox, oy) for p in ln]
    return pts[::max(1, len(pts) // 40)] + [_centre(pts)] if pts else []


def _hl_centre(mp, item, ox=0, oy=0):
    return _centre([p for ln in _hl_px(mp, item, ox, oy) for p in ln])


def _alt(item):
    """The second colour of the highlight's alternating pattern."""
    return (255, 255, 255) if item.get("closed") else (255, 230, 120)


_HL = {}


def _hl_mask(mp, item):
    """Bool mask (map pixels) of the affected road. The hub rasterised it exactly as the map draws its roads
    (item["hl"][map name]); without that, DriveBC's own geometry is drawn as a 1-px line."""
    name = mp.meta.get("name")
    pix = (item.get("hl") or {}).get(name)
    key = (name, mp.meta.get("sig"), item.get("id"), len(pix or []))
    m = _HL.get(key)
    if m is None:
        m = np.zeros((mp.h, mp.w), bool)
        if pix:
            a = np.asarray(pix, dtype=np.int32).reshape(-1, 2)
            ok = (a[:, 0] >= 0) & (a[:, 0] < mp.w) & (a[:, 1] >= 0) & (a[:, 1] < mp.h)
            m[a[ok, 1], a[ok, 0]] = True
        else:
            from PIL import Image, ImageDraw
            img = Image.new("L", (mp.w, mp.h), 0)
            d = ImageDraw.Draw(img)
            for ln in _lines(item):
                px = [mp.project(la, lo) for lo, la in ln]
                if len(px) > 1:
                    d.line(px, fill=255, width=1)
                elif px:
                    d.point(px, fill=255)
            m = np.asarray(img) > 0
        if len(_HL) > 24:
            _HL.clear()
        _HL[key] = m
    return m


_DIR = {}


def _hl_dir(m, item):
    """The road's main direction (unit vector) from its pixels, so the stripes run across it, not along it."""
    key = (item.get("id"), int(m.sum()), m.shape)
    if key not in _DIR:
        ys, xs = np.nonzero(m)
        if len(xs) < 2:
            _DIR[key] = (0.7071, 0.7071)
        else:
            cov = np.cov(np.stack([xs - xs.mean(), ys - ys.mean()]))
            w, v = np.linalg.eigh(cov)
            ux, uy = v[:, int(np.argmax(w))]
            _DIR[key] = (float(ux), float(uy))
        if len(_DIR) > 64:
            _DIR.clear()
    return _DIR.get(key, (0.7071, 0.7071))


def _grow(m, r):
    """Square dilation by r pixels (even on every side, so a widened road stays centred on the road)."""
    out = m.copy()
    for _ in range(r):
        p = np.pad(out, 1)
        out = (p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
               | p[:-2, :-2] | p[:-2, 2:] | p[2:, :-2] | p[2:, 2:])
    return out


_GEOM = {}


def _hl_geom(mp, item, width):
    """Cached per event and map: the widened road's pixels, its dark edge, and each pixel's position along the
    road's direction (for the stripes) — so a frame only recolours pixels."""
    base = _hl_mask(mp, item)
    key = (mp.meta.get("name"), mp.meta.get("sig"), item.get("id"), int(base.sum()), width)
    g = _GEOM.get(key)
    if g is None:
        r = max(0, width // 2)
        body = _grow(base, r)
        edge = _grow(body, 1) & ~body
        by, bx = np.nonzero(body)
        ey, ex = np.nonzero(edge)
        ux, uy = _hl_dir(base, item)
        g = (bx, by, ex, ey, bx * ux + by * uy)
        if len(_GEOM) > 24:
            _GEOM.clear()
        _GEOM[key] = g
    return g


def highlight(c, mp, item, now, ox=0, oy=0, width=3, animate=True, clip=(0, 0, W, H)):
    """The affected road, on the map's own road pixels: widened evenly by ``width // 2`` px each side, a dark
    edge round it, and the alert colour alternating with a bright second colour in stripes across the road that
    move along it (a closure strobes the whole stretch on top)."""
    bx, by, ex, ey, t = _hl_geom(mp, item, width)
    if not len(bx):
        return
    x0, y0, x1, y1 = max(0, clip[0]), max(0, clip[1]), min(W, clip[2]), min(H, clip[3])
    cx, cy = ex + ox, ey + oy
    ok = (cx >= x0) & (cx < x1) & (cy >= y0) & (cy < y1)
    c.buf[cy[ok], cx[ok]] = 0
    cx, cy = bx + ox, by + oy
    ok = (cx >= x0) & (cx < x1) & (cy >= y0) & (cy < y1)
    cx, cy = cx[ok], cy[ok]
    col, alt = np.array(col_of(item), np.uint8), np.array(_alt(item), np.uint8)
    if animate and item.get("closed") and k.blink(now, 1.6, 0.18):
        c.buf[cy, cx] = alt
        return
    ph = now * 12 if animate else 0.0
    on = (np.floor((t[ok] + ph) / 4).astype(np.int64) % 2) == 0
    c.buf[cy[on], cx[on]] = col
    c.buf[cy[~on], cx[~on]] = alt


def area_of(item):
    """The city or area an event is in (DriveBC's closest landmark, else "In Surrey" from its location text);
    none for bridges, which span two."""
    if re.search(r"bridge", item.get("road") or "", re.I):
        return ""
    lm = (item.get("landmark") or "").strip()
    if lm:
        return lm.replace(" - City", " (City)").replace(" - District", " (District)")
    m = re.search(r"\bIn ([A-Z][\w .'-]+?)(?:\n|$| at | between )", item.get("where") or "")
    return m.group(1).strip() if m else ""


def _panel(c, item, x, w, h_shield=56, big=True):
    """Shield, hazard icon, road, direction, what, the text: the side panel of the map themes."""
    col = col_of(item)
    k.box(c, x, 0, w, H, (10, 11, 15))
    k.box(c, x, 0, w, 3, col)
    y = 8
    if not item.get("highways"):
        h_shield = 30                                      # a street blade: the name itself, compact
    bw = hero(c, x + 8, y, item, h_shield, w - 50)
    big_icon(c, item, x + w - 32, y + 2, col)
    y += h_shield + 6
    if item.get("highways"):                               # (a street blade already names the road)
        y += k.fit_text(c, item.get("road") or "", x + 8, y, w - 14, 34, ("h18", "h16", "lutb12", "hvb10"), WHITE) + 3
    if area_of(item):
        y += k.fit_text(c, area_of(item), x + 8, y, w - 14, 16, ("lutb14", "lutb12", "hvb10"), (255, 225, 150)) + 2
    if item.get("direction"):
        k.text(c, k.font("lutb12"), x + 8, y, (200, 205, 220), item["direction"])
        y += 15
    k.chip(c, k.font("m13b" if big else "m10b"), x + 8, y, word_of(item), col, BLACK, 3, 1)
    y += 17 if big else 14
    lines_, _ = k.fit(_desc(item), w - 16, H - y - 14,
                      [{"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}, {"body": "s8", "small": "s8"}])
    k.draw_fit(c, lines_, x + 8, y)
    k.text(c, k.font("s8"), x + 8, H - 10, (120, 125, 140), f"DriveBC · {_updated(item)}")


def _map(s, c, item, now, el):
    """The map zoomed to the event, the affected road highlighted; shield, icon and details in a side column."""
    mp = _evt_map(s, item)
    pw = 170
    hx, hy = _hl_centre(mp, item) if mp else (W / 2, H / 2)
    left = hx > mp.w / 2 if mp else True                 # the panel goes on the side away from the road
    px_, mx = (0, pw) if left else (W - pw, 0)
    mw = W - pw
    sx = int(max(0, min((mp.w if mp else W) - mw, hx - mw / 2)))

    def static():
        if mp:
            mp.blit(c, mx, 0, sx, 0, mw, H)
            rect = _minimap_rect(s, mp, item, sx, mx, mw)
            _route_shields(c, mp, avoid=_hl_avoid(mp, item, mx - sx), h=22, ox=mx - sx, clip=(mx, 0, mx + mw, H),
                           block=[rect] if rect else [])
            _minimap(s, c, mp, item, sx, mx, mw)
        _panel(c, item, px_, pw)
        k.box(c, pw if left else W - pw - 1, 0, 1, H, (50, 56, 70))

    s.static(c, ("map", item["id"], item.get("updated")), static)
    if mp:
        highlight(c, mp, item, now, ox=mx - sx, width=4 if mp.km_per_px() < 0.06 else 3, clip=(mx, 0, mx + mw, H))


def _minimap_rect(s, mp, item, sx, mx, mw):
    """Where the minimap goes: the map corner farthest from the affected road (x0, y0, x1, y1)."""
    mini = k.Map.load(s.data_dir, "roads_metro_mini")
    if not mini:
        return None
    hx, hy = _hl_centre(mp, item, mx - sx)
    m = 4
    corners = [(mx + m, m), (mx + mw - mini.w - m, m), (mx + m, H - mini.h - m), (mx + mw - mini.w - m, H - mini.h - m)]
    x, y = max(corners, key=lambda p: (p[0] + mini.w / 2 - hx) ** 2 + (p[1] + mini.h / 2 - hy) ** 2)
    return (x - 2, y - 2, x + mini.w + 2, y + mini.h + 2)


def _minimap(s, c, mp, item, sx, mx, mw):
    """The whole Metro area small, in the map corner farthest from the affected road, with a box round the
    part the big map shows."""
    mini = k.Map.load(s.data_dir, "roads_metro_mini")
    rect = _minimap_rect(s, mp, item, sx, mx, mw)
    if not mini or not rect:
        return
    x, y = rect[0] + 2, rect[1] + 2
    k.box(c, x - 2, y - 2, mini.w + 4, mini.h + 4, (0, 0, 0))
    mini.blit(c, x, y)
    k.frame(c, x - 1, y - 1, mini.w + 2, mini.h + 2, (90, 95, 110))
    # the big map's visible area, as a box on the minimap
    la0, lo0 = mp.unproject(sx, 0)
    la1, lo1 = mp.unproject(sx + mw, H)
    ax, ay = mini.project(la0, lo0)
    bx, by = mini.project(la1, lo1)
    ax, bx = sorted((max(0, min(mini.w - 1, int(round(ax)))), max(0, min(mini.w - 1, int(round(bx))))))
    ay, by = sorted((max(0, min(mini.h - 1, int(round(ay)))), max(0, min(mini.h - 1, int(round(by))))))
    bw, bh = max(3, bx - ax + 1), max(3, by - ay + 1)
    if bw <= 3 and bh <= 3:                                  # zoomed in further than the minimap's pixels: a dot
        ax, ay = int((ax + bx) / 2) - 1, int((ay + by) / 2) - 1
    k.frame(c, x + ax, y + ay, bw, bh, WHITE)


def _hero(s, c, item, now, el):
    """The shield as the headline: a 64-px shield, the road and what's wrong large; the map beside."""
    col = col_of(item)
    mp = _evt_map(s, item)
    lw = 262
    mw = W - lw
    hx, hy = _hl_centre(mp, item) if mp else (0, 0)
    sx = int(max(0, min((mp.w if mp else W) - mw, hx - mw / 2)))

    def static():
        k.box(c, 0, 0, lw, H, (8, 9, 13))
        bw = hero(c, 10, 10, item, 64, lw - 60)
        x = 10 + bw + 12
        big_icon(c, item, lw - 34, 12, col)
        k.fit_text(c, item.get("road") or "", x, 10, lw - x - 40, 40, ("h20", "h18", "h16", "lutb12"), WHITE)
        ar = area_of(item)
        if ar:
            k.text(c, k.font("lutb14"), x, 52, (255, 225, 150), ar)
        k.text(c, k.font("lutb12"), x, 68 if ar else 54, (200, 205, 220), item.get("direction") or "")
        y = 84
        f = k.pick(("h20", "h18", "h16"), word_of(item), lw - 24)
        k.box(c, 10, y, k.tw(f, word_of(item)) + 12, f.h + 6, col)
        k.text(c, f, 16, y + 3, BLACK, word_of(item))
        y += f.h + 12
        lines_, _ = k.fit(_desc(item), lw - 20, H - y - 12,
                          [{"body": "lutb14", "small": "lut12"}, {"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}])
        k.draw_fit(c, lines_, 10, y)
        k.text(c, k.font("s8"), 10, H - 10, (120, 125, 140), f"DriveBC · {_updated(item)}")
        if mp:
            mp.blit(c, lw, 0, sx, 0, mw, H)
            _route_shields(c, mp, avoid=_hl_avoid(mp, item, lw - sx), h=14, ox=lw - sx, clip=(lw, 0, W, H))
        k.box(c, lw, 0, 1, H, col)

    s.static(c, ("hero", item["id"], item.get("updated")), static)
    if mp:
        highlight(c, mp, item, now, ox=lw - sx, width=4 if mp.km_per_px() < 0.06 else 3, clip=(lw + 1, 0, W, H))


def _vms(s, c, item, now, el):
    """An overhead variable-message sign; the big shield, icon and details beneath."""
    lines = sign_lines(item)
    col = col_of(item)
    sy, sh_ = 6, 98

    def static():
        k.box(c, 0, 0, W, H, (5, 6, 8))
        k.box(c, 24, sy, W - 48, sh_, (44, 46, 50))
        k.frame(c, 24, sy, W - 48, sh_, (80, 82, 88))
        k.box(c, 32, sy + 6, W - 64, sh_ - 12, BLACK)
        y0 = sy + sh_ + 8
        bw = hero(c, 8, y0, item, 56, 190)
        x = 8 + bw + 12
        big_icon(c, item, W - 32, y0, col)
        k.fit_text(c, item.get("road") or "", x, y0, W - x - 40, 22, ("h18", "h16", "lutb12"), WHITE)
        dline = " · ".join(v for v in (area_of(item), item.get("direction") or "", word_of(item)) if v)
        k.text(c, k.font("lutb12"), x, y0 + 22, col, dline)
        lines_, _ = k.fit(_desc(item, (220, 220, 225), (150, 160, 180)), W - x - 8, H - (y0 + 38) - 1,
                          [{"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}, {"body": "s8", "small": "s8"}])
        k.draw_fit(c, lines_, x, y0 + 38)

    s.static(c, ("vms", item["id"], item.get("updated")), static)
    fw = W - 64
    for i, ln in enumerate(lines):
        f = k.font("m20b") if k.tw(k.font("m20b"), ln) <= fw - 12 else k.font("m16b")
        y = sy + 10 + i * 29 + (20 - f.h) // 2
        lit = not (i == 1 and (item.get("closed") or item.get("severity") == "MAJOR")) or k.blink(now, 1.0, 0.72)
        k.text_c(c, f, 32 + fw // 2, y, AMB if lit else AMB_DD, ln)
    if item.get("closed") or item.get("severity") == "MAJOR":
        on = k.blink(now, 0.8)
        for bx, by, ph in ((13, sy + 25, on), (13, sy + sh_ - 25, not on), (W - 14, sy + 25, not on), (W - 14, sy + sh_ - 25, on)):
            beacon(c, bx, by, ph, 3)


def _overview(s, c, item, now, el):
    """Metro Vancouver with every current event's road highlighted; the list (shields, icons) beside."""
    mp = k.Map.load(s.data_dir, "roads_metro")
    items = s.queue or [item]
    lw = 164

    def static():
        if mp:
            mp.blit(c, 0, 0, 0, 0, W - lw, H)
            _route_shields(c, mp, avoid=[p for it in items for p in _hl_avoid(mp, it)], h=12, clip=(0, 0, W - lw, H))
            for it in items:
                if it is not item:
                    highlight(c, mp, it, 0, width=2, animate=False, clip=(0, 0, W - lw, H))
        k.box(c, W - lw, 0, lw, H, (10, 11, 16))
        k.box(c, W - lw, 0, 1, H, (50, 56, 70))
        k.text(c, k.font("lutb12"), W - lw + 6, 4, WHITE, "METRO TRAFFIC")
        y = 22
        for it in items[:6]:
            on = it is item
            if on:
                k.box(c, W - lw + 1, y - 3, lw - 1, 28, (30, 32, 44))
            k.box(c, W - lw + 1, y - 3, 3, 28, col_of(it))
            hw_ = hero(c, W - lw + 8, y, it, 22, 60)
            tx = W - lw + 8 + hw_ + 6
            k.text(c, k.pick(("hvb10", "s8"), it.get("road") or "", W - tx - 30), tx, y + 1, WHITE if on else (190, 190, 200), it.get("road") or "")
            k.text(c, k.font("s8"), tx, y + 12, col_of(it), " · ".join(v for v in (word_of(it), area_of(it)) if v))
            big_icon(c, it, W - 28, y, col_of(it))
            y += 30

    s.static(c, ("ov", item["id"], len(items)), static)
    if mp:
        highlight(c, mp, item, now, width=2, clip=(0, 0, W - lw, H))


def _camera(s, c, item, now, el):
    """The nearest DriveBC camera full height; shield, icon and details beside."""
    img = _cam(s, item)
    cam = item.get("cam") or {}
    iw = 300 if img is None else 336
    pw = W - iw
    mp = _evt_map(s, item) if img is None else None
    hx, hy = _hl_centre(mp, item) if mp else (0, 0)
    sx = int(max(0, min((mp.w if mp else W) - iw, hx - iw / 2)))

    def static():
        if img is not None:
            c.Blit(img, 0, 0)
            k.shade(c, 0, H - 15, iw, 15, 0.3)
            k.fit_text(c, cam.get("caption") or cam.get("name") or "", 4, H - 13, iw - 70, 12, ("hv10", "s8"), (230, 230, 235))
            k.text_r(c, k.font("s8"), iw - 4, H - 11, (200, 200, 210), f"{cam.get('km', '?')} km away")
        elif mp:
            mp.blit(c, 0, 0, sx, 0, iw, H)
            _route_shields(c, mp, avoid=_hl_avoid(mp, item, -sx), h=14, ox=-sx, clip=(0, 0, iw, H))
        _panel(c, item, iw, pw, 40 if pw < 130 else 48, big=False)

    s.static(c, ("cam", item["id"], item.get("updated"), cam.get("at")), static)
    if img is not None:
        k.box(c, 4, 4, 40, 13, BLACK)
        k.disc(c, 11, 10, 3, (255, 40, 40) if k.blink(now, 1.2) else (90, 0, 0))
        k.text(c, k.font("s8"), 17, 6, WHITE, "LIVE")
    elif mp:
        highlight(c, mp, item, now, ox=-sx, width=4 if mp.km_per_px() < 0.06 else 3, clip=(0, 0, iw, H))


def _board(s, c, item, now, el):
    """Every current event in rows with shields and icons; the one in focus expanded below."""
    items = s.queue or [item]
    cur = items.index(item) if item in items else 0
    row_h, top = 28, 20
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
            k.box(c, 0, y, 4, row_h - 2, col_of(it))
            hw_ = hero(c, 10, y + 1, it, 24, 130)
            fx = 10 + hw_ + 10
            name = " ".join(v for v in (it.get("road") or "", DIR.get((it.get("direction") or "").lower(), "")) if v)
            if area_of(it):
                name = f"{name}, {area_of(it)}"
            k.text(c, k.font("lutb12"), fx, y + 7, WHITE if on else (190, 190, 200), name)
            k.text(c, k.font("m10b"), fx + k.tw(k.font("lutb12"), name) + 10, y + 8, col_of(it), word_of(it))
            big_icon(c, it, W - 76, y + 1, col_of(it))
            k.text_r(c, k.font("hv10"), W - 6, y + 8, (150, 155, 170), k.day_hhmm(it.get("updated")))
        y = top + rows * row_h + 2
        k.box(c, 0, y, W, 1, col)
        lines_, _ = k.fit(_desc(item), W - 16, H - y - 4,
                          [{"body": "lut14", "small": "lut12"}, {"body": "lut12", "small": "hv10"}, {"body": "hv10", "small": "s8"}])
        k.draw_fit(c, lines_, 8, y + 6)

    s.static(c, ("board", item["id"], len(items), item.get("updated")), static)


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
        geometry={"type": "LineString", "points": [[-122.8135, 49.2190], [-122.8099, 49.21251], [-122.8053, 49.2040]]})},
}

THEMES = {
    "map": {"label": "Incident map", "hint": "The map zoomed to the event, the affected road highlighted; shield, icon and details beside",
            "draw": _map, "badge": (W - 50, H - 12)},
    "hero": {"label": "Shield headline", "hint": "A 64-px shield, the road and what is wrong in large type; the map beside",
             "draw": _hero, "badge": (W - 50, H - 12)},
    "vms": {"label": "Overhead sign", "hint": "An amber variable-message sign; the big shield, icon and details beneath",
            "draw": _vms, "badge": (W - 96, H - 12)},
    "overview": {"label": "Metro overview", "hint": "Every current event's road highlighted on the Metro map, the list beside",
                 "draw": _overview, "badge": (4, H - 14)},
    "camera": {"label": "Highway camera", "hint": "The nearest DriveBC camera full height (or the map), shield and details beside",
               "draw": _camera, "badge": (4, 20)},
    "board": {"label": "Traffic desk", "hint": "Every current event in rows with shields; the one in focus expanded below",
              "draw": _board, "badge": (230, 3)},
}


class Screen(k.BCScreen):
    PANEL, DATA, SETTINGS = "road_alert", "drivebc", "road_alerts"
    THEMES, SAMPLES, DEFAULT_THEME = THEMES, SAMPLES, "map"

    def sample(self, key):
        it = super().sample(key)
        d = self.data()
        it.setdefault("road_lines", (d.get("sample_lines") or {}).get(key) or [])
        it.setdefault("hl", (d.get("sample_hl") or {}).get(key) or {})
        return it

    def dwell(self, item):
        return max(float(self.settings.get("dwell_sec") or 20), min(45.0, 10 + len((item.get("description") or "").split()) / 3))

    def _queue(self, entry):
        if entry.get("sample") == "_all":                     # list previews: every sample at once
            return [self.sample(key) for key in self.SAMPLES]
        return super()._queue(entry)


def render_preview(theme, sample=None, at=4.0, settings=None, data=None, data_dir=None):
    return k.render_preview(Screen, theme, sample, at, settings, data, data_dir)
