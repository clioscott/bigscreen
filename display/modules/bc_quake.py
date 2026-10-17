"""Earthquake screen for south-west BC (and big ones further out): what happened, where, how hard it shook.

Data: data/quakes.json from hub/jobs/quakes.py — {"items": [{kind: "quake"|"eew", mag, mag_type, place_short, lat,
lon, depth, time, felt, cdi, alert, tsunami, dist_km, mmi_home, setting, cities: [{name, km, mmi}], source}],
"recent": [[lat, lon, mag, t, depth], …], "home": [lon, lat]}.

North American seismology looks, unlike the Japan EEW screen:

  pnsn      a seismic-network map: the epicentre, 30 days of background quakes, distance rings round home
  shakemap  the estimated intensity field (Atkinson & Wald 2007) in USGS ShakeMap colours, with a city table
  report    an earthquake report card: magnitude, place, time, depth, setting, felt reports, city intensities
  drop      Canada's earthquake early warning (from Alert Ready): DROP · COVER · HOLD ON (always used for warnings)
"""

from __future__ import annotations

import math
import time

import numpy as np

from display.modules import bc_kit as k
from display.modules.bc_kit import BLACK, H, W, WHITE, Para

ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
MMI_COL = [(0, 0, 0), (255, 255, 255), (191, 204, 255), (160, 230, 255), (128, 255, 255), (122, 255, 147), (255, 255, 0),
           (255, 200, 0), (255, 145, 0), (255, 0, 0), (200, 0, 0)]
MMI_WORD = ["", "Not felt", "Weak", "Weak", "Light", "Moderate", "Strong", "Very strong", "Severe", "Violent", "Extreme"]
HOME = (-123.0918, 49.1442)


def mmi_i(v):
    return int(max(1, min(10, round(float(v or 1)))))


def mag_col(m):
    m = float(m or 0)
    return (120, 200, 255) if m < 3 else (255, 220, 60) if m < 4.5 else (255, 140, 0) if m < 6 else (255, 50, 40)


def mmi_aw07(mag, dist, depth):
    rh = np.sqrt(dist ** 2 + depth ** 2)
    R = np.sqrt(rh ** 2 + 196.0)
    B = np.maximum(0.0, np.log10(R / 50.0))
    m = mag - 6.0
    v = 12.27 + 2.270 * m + 0.1304 * m * m - 1.30 * np.log10(R) - 0.0007070 * R + 1.95 * B - 0.577 * mag * np.log10(R)
    return np.clip(v, 1.0, 10.0)


def _home(s):
    return tuple(s.data().get("home") or HOME)


def _pick_map(s, item):
    for name in ("quake_swbc", "quake_casc"):
        mp = k.Map.load(s.data_dir, name)
        if mp and item.get("lat") is not None and mp.inside(item["lat"], item["lon"], -12):
            return mp
    mp = k.Map.load(s.data_dir, "tsunami_pacific")              # a big one far away: the North Pacific
    if mp and item.get("lat") is not None and mp.inside(item["lat"], item["lon"], -6):
        return mp
    return k.Map.load(s.data_dir, "quake_casc")


def _mag_text(item):
    m = item.get("mag")
    return f"{float(m):.1f}" if m is not None else "?"


def _when(item):
    return f"{k.day_hhmm(item.get('time'))}  ·  {k.ago(item.get('time'))}"


def _place(item):
    return item.get("place_short") or item.get("place") or "Unknown location"


def _star(c, x, y, r, col):
    for a in range(5):
        t = a * 2 * math.pi / 5 - math.pi / 2
        k.line(c, x, y, x + r * math.cos(t), y + r * math.sin(t), col)
        t2 = t + math.pi / 5
        k.line(c, x + r * math.cos(t), y + r * math.sin(t), x + r * 0.45 * math.cos(t2), y + r * 0.45 * math.sin(t2), col)
        k.line(c, x + r * 0.45 * math.cos(t2), y + r * 0.45 * math.sin(t2), x + r * math.cos(t + 2 * math.pi / 5),
               y + r * math.sin(t + 2 * math.pi / 5), col)
    k.disc(c, x, y, max(1, r // 3), col)


def _recent(s, c, mp, item, ox=0, oy=0, w=W):
    for la, lo, m, t, dep in s.data().get("recent") or []:
        if item.get("lat") is not None and abs(t - (item.get("time") or 0)) < 60:
            continue
        x, y = mp.project(la, lo)
        if 0 <= x < w and 0 <= y < H:
            col = (130, 170, 210) if dep < 30 else (170, 140, 220)
            r = 0 if m < 2.0 else 1 if m < 3.5 else 2
            k.disc(c, x + ox, y + oy, r, col) if r else k.box(c, x + ox, y + oy, 1, 1, col)


VP, VS = 6.5, 3.6                     # km/s — the crustal P and S speeds the Japan EEW screen also uses
FRESH = 300                           # seconds after origin during which the true wavefronts are drawn


def s_arrival(item):
    """Epoch seconds when the S wave reaches home (None without a location and an origin time)."""
    if item.get("lat") is None or item.get("dist_km") is None or not item.get("time"):
        return None
    hyp = math.hypot(float(item["dist_km"]), float(item.get("depth") or 10))
    return float(item["time"]) + hyp / VS


def _waves(c, mp, item, now, col, clip=(0, 0, W, H)):
    """Waves spreading from the epicentre: the true P and S fronts while the quake is fresh, else a ripple."""
    ex, ey = mp.project(item["lat"], item["lon"])
    el = now - float(item.get("time") or now)
    kpp = mp.km_per_px()
    if 0 <= el <= FRESH:
        dep = float(item.get("depth") or 10)
        for v, wc in ((VP, (90, 160, 255)), (VS, (255, 70, 40))):
            r2 = (v * el) ** 2 - dep ** 2
            if r2 > 0:
                r = math.sqrt(r2) / kpp
                if r < 1200:
                    k.circle_clip(c, ex, ey, r, wc, clip)
                    k.circle_clip(c, ex, ey, r + 1, k.dim(wc, 0.5), clip)
    else:
        for i in range(3):
            r = ((now * 18) + i * 16) % 48 + 4
            k.circle(c, ex, ey, r, k.dim(col, max(0.15, 1 - r / 52)))
    return ex, ey


def countdown(c, item, now, x, y, w, big=True):
    """S-wave countdown to home (Japan EEW style) while it is still coming; 'shaking now' just after."""
    t = s_arrival(item)
    if t is None:
        return 0
    left = t - now
    if left > 0:
        k.text(c, k.font("hvb10"), x, y, (255, 200, 120), "S WAVE REACHES HOME IN")
        dw, dh = (16, 30) if big else (10, 18)
        txt = f"{int(math.ceil(left))}"
        k.seven_seg(c, x, y + 12, txt, dw, dh, 3 if big else 2, on=(255, 60, 40), off=(40, 10, 10))
        k.text(c, k.font("lutb12"), x + len(txt) * (dw + 4) + 4, y + 12 + dh - 12, (255, 120, 90), "s")
        return 12 + dh + 2
    if left > -60:
        if k.blink(now, 0.6):
            k.box(c, x, y, w, 20, (200, 0, 0))
            k.text(c, k.font("h16"), x + 4, y + 2, WHITE, "SHAKING NOW")
        return 22
    return 0


def _rings(c, mp, home, col=(60, 70, 90), ox=0, labels=True):
    """Home on the map (the house; distances are in the panels, not as rings round it)."""
    hx, hy = mp.project(home[1], home[0])
    k.icon_outlined(c, "house", int(hx + ox) - 8, int(hy) - 7, (230, 230, 240))
    return
    hx, hy = mp.project(home[1], home[0])
    f = k.font("s8")
    for km in (50, 100, 200, 400):
        r = km / mp.km_per_px()
        if r < 14 or r > 900:
            continue
        k.circle(c, hx + ox, hy, r, col)
        if labels and 0 <= hy - r - 9 < H:
            k.text_c(c, f, hx + ox, hy - r - 9, k.dim(col, 1.6), f"{km} km")
    k.icon_outlined(c, "house", int(hx + ox) - 8, int(hy) - 7, (230, 230, 240))


def _intensity_here(c, item, x, y, w, f=None):
    """'Shaking here' with the estimated intensity (roman numeral on its ShakeMap colour); stacked when narrow.
    Returns the height used."""
    i = mmi_i(item.get("mmi_home"))
    f = f or k.font("lutb12")
    t = f"{ROMAN[i]} {MMI_WORD[i]}"
    k.icon(c, "house", x, y, (200, 200, 210))
    k.text(c, k.font("hv10"), x + 20, y + 3, (190, 190, 200), "Shaking here")
    if 20 + k.tw(k.font("hv10"), "Shaking here") + 8 + k.tw(f, t) + 6 <= w:
        k.chip(c, f, x + w - k.tw(f, t) - 6, y, t, MMI_COL[i], BLACK, 3, 1)
        return 16
    k.chip(c, f, x + 20, y + 16, t, MMI_COL[i], BLACK, 3, 1)
    return 18 + f.h


# ------------------------------------------------------------------------------------------------ live stations
LIVE_TTL = 6.0                        # seconds a /bc_quake_obs push stays on the wall


def _live(s, now=None):
    """The latest station readings pushed by the hub (hub/bc/seismo.py), or None when stale."""
    lv = getattr(s, "live", None)
    if not lv or (now if now is not None else s._clock) - s.live_at > LIVE_TTL:
        return None
    return lv


def int_col(v):
    """Instrumental intensity → colour: dim blues below II (instruments only), ShakeMap colours above."""
    v = float(v)
    if v < 1.5:
        return k.mix((22, 30, 52), (100, 125, 170), max(0.0, (v + 2.0) / 3.5))
    lo = max(2, int(min(9, math.floor(v))))
    return k.mix(MMI_COL[lo], MMI_COL[min(10, lo + 1)], max(0.0, min(1.0, v - lo)))


def _stations(c, mp, lv, ox=0, clip=(0, 0, W, H)):
    """Station dots coloured by the shaking they measure now (triggered ones bigger, ringed white)."""
    x0, y0, x1, y1 = clip
    for meta, v in sorted(zip(lv.get("stations") or [], lv.get("st") or []), key=lambda p: p[1][0] if p[1] else -9):
        x, y = mp.project(meta[2], meta[3])
        x, y = int(round(x + ox)), int(round(y))
        if not (x0 + 4 <= x < x1 - 4 and y0 + 4 <= y < y1 - 4):
            continue
        if v is None:
            k.box(c, x - 1, y - 1, 3, 3, BLACK)
            k.box(c, x, y, 1, 1, (70, 70, 80))                      # no data
            continue
        r = 3 if v[2] or v[0] >= 2.5 else 2
        k.disc(c, x, y, r + 1, WHITE if v[2] else BLACK)
        k.disc(c, x, y, r, int_col(v[0]))


def _peak(lv):
    """(peak MMI, station name) measured this episode, or None."""
    best = None
    for meta, v in zip(lv.get("stations") or [], lv.get("st") or []):
        if v and (best is None or v[1] > best[0]):
            best = (v[1], meta[1])
    return best


def _trace(c, lv, x, y, w, h, label=True):
    """The seismograph of the nearest broadband station: 40 s of 0.1-s min/max, newest on the right."""
    tr = lv.get("trace") if lv else None
    k.box(c, x, y, w, h, (4, 10, 6))
    k.box(c, x, y, w, 1, (30, 60, 40))
    mid = y + 1 + (h - 1) // 2
    k.box(c, x, mid, w, 1, (18, 40, 26))
    if not tr:
        k.text(c, k.font("s8"), x + 3, y + 3, (80, 110, 90), "seismograph: no data")
        return
    mm = tr.get("mm") or []
    n = len(mm)
    half = (h - 3) / 2.0
    for col in range(w):
        a = int(col * n / w)
        seg = [v for v in mm[a:max(a + 1, int((col + 1) * n / w))] if v]
        if not seg:
            continue
        lo, hi = min(v[0] for v in seg), max(v[1] for v in seg)
        ya, yb = int(round(mid - hi / 100.0 * half)), int(round(mid - lo / 100.0 * half))
        k.box(c, x + col, ya, 1, max(1, yb - ya + 1), (90, 240, 130))
    if label:
        f = k.font("s8")
        k.text(c, f, x + 2, y + 2, (150, 220, 170), tr.get("sta") or "")
        full = tr.get("full") or 0
        k.text_r(c, f, x + w - 2, y + 2, (110, 170, 130), f"±{full:.0f} µm/s" if full >= 10 else f"±{full:.1f} µm/s")


def _replay_badge(c, item, pos):
    if not item.get("replay"):
        return
    f = k.font("s8")
    x, y = pos
    k.box(c, x, y, k.tw(f, "REPLAY") + 6, f.h + 2, (0, 90, 120))
    k.text(c, f, x + 3, y + 1, (200, 245, 255), "REPLAY")


def _live_caption(c, lv):
    pk = _peak(lv)
    cap = "Live: CNSN + PNSN stations via EarthScope"
    if pk and pk[0] >= 1.5:
        cap += f"  ·  strongest {ROMAN[mmi_i(pk[0])]} at {pk[1]}"
    k.shade(c, 0, H - 12, k.tw(k.font("s8"), cap) + 8, 12, 0.4)
    k.text(c, k.font("s8"), 4, H - 10, (150, 165, 190), cap)


def _detected(s, c, item, now, el):
    """Stations triggered, nothing in the catalogues yet: the live network, the trace, what was measured."""
    mp = k.Map.load(s.data_dir, "quake_swbc")
    pw = 160
    px_ = W - pw
    lv = _live(s, now) or s.state.get("last_live") or {}
    if lv:
        s.state["last_live"] = lv

    def static():
        if mp:
            mp.blit(c, 0, 0, 0, 0, W, H)
            _recent(s, c, mp, {})
            _rings(c, mp, _home(s))
        k.shade(c, px_, 0, pw, H, 0.2)
        k.box(c, px_, 0, 1, H, (60, 70, 90))

    s.static(c, ("detect", item["id"]), static)
    k.box(c, px_ + 4, 5, pw - 8, 20, (200, 0, 0) if k.blink(now, 0.7) else (110, 0, 0))
    k.text_c(c, k.font("lutb12"), px_ + pw // 2, 9, WHITE, "SHAKING DETECTED")
    d = lv.get("detect") or item
    y = 30
    for line, col in ((f"First at {d.get('first') or '?'}", (230, 230, 235)),
                      (f"{d.get('stations') or 0} stations triggered", (190, 195, 210)),
                      (f"{k.hhmm(item.get('time'), True)}  ·  {k.ago(item.get('time'))}", (190, 195, 210))):
        k.text(c, k.pick(("hvb10", "hv10", "s8"), line, pw - 12), px_ + 6, y, col, line)
        y += 13
    k.fit_text(c, "Location and magnitude to follow from USGS / NRCan", px_ + 6, y + 2, pw - 12, 22, ("hv10", "s8"),
               (150, 170, 200))
    _trace(c, lv, px_ + 4, 96, pw - 8, 50)
    if mp:
        if d.get("first_lat") is not None:
            fx, fy = mp.project(d["first_lat"], d["first_lon"])
            for i in range(2):
                r = ((now * 20) + i * 18) % 36 + 4
                k.circle_clip(c, fx, fy, r, k.dim((255, 80, 60), max(0.2, 1 - r / 40)), (0, 0, px_, H))
        if lv:
            _stations(c, mp, lv, clip=(0, 0, px_, H))
    pk = _peak(lv) if lv else None
    if pk:
        felt = pk[0] >= 1.5
        t = f"{ROMAN[mmi_i(pk[0])]} {MMI_WORD[mmi_i(pk[0])]}" if felt else "Instruments only"
        k.text(c, k.font("hv10"), px_ + 6, 152, (190, 190, 200), f"Strongest: {pk[1]}")
        k.chip(c, k.font("hvb10"), px_ + 6, 166, t, int_col(pk[0]) if felt else (60, 70, 100), BLACK if felt else WHITE, 3, 1)
    if lv:
        _live_caption(c, lv)
    _replay_badge(c, item, (px_ - 44, 4))


# ------------------------------------------------------------------------------------------------ pnsn
def _pnsn(s, c, item, now, el):
    if item.get("kind") == "detect":
        return _detected(s, c, item, now, el)
    mp = _pick_map(s, item)
    pw = 160
    px_ = W - pw
    col = mag_col(item.get("mag"))
    ex, ey = mp.project(item["lat"], item["lon"]) if mp else (W / 2, H / 2)
    lv = _live(s, now)

    def static():
        if mp:
            mp.blit(c, 0, 0, 0, 0, W, H)
            _recent(s, c, mp, item)
            _rings(c, mp, _home(s))
        k.shade(c, px_, 0, pw, H, 0.2)
        k.box(c, px_, 0, 1, H, (60, 70, 90))
        # magnitude, big
        mt = _mag_text(item)
        k.text(c, k.font("h13"), px_ + 8, 6, (170, 180, 200), "M")
        k.seven_seg(c, px_ + 22, 6, mt, 18, 34, 3, on=col, off=(30, 30, 36))
        k.text(c, k.font("hv10"), px_ + 22 + len(mt) * 22 + 2, 32, (150, 160, 180), item.get("mag_type") or "")
        y = 46
        if lv:                                            # live: room for the seismograph under the facts
            y += k.fit_text(c, _place(item), px_ + 8, y, pw - 14, 22, ("lutb12", "hvb10", "s8"), WHITE) + 3
            line = f"{_when(item)}  ·  {item.get('depth', 0):.0f} km deep"
            if k.tw(k.font("s8"), line) > pw - 14:
                line = _when(item)
            k.text(c, k.pick(("hv10", "s8"), line, pw - 14), px_ + 8, y, (190, 195, 210), line)
            _intensity_here(c, item, px_ + 4, H - 20, pw - 6, k.font("hvb10"))
            return
        y += k.fit_text(c, _place(item), px_ + 8, y, pw - 14, 30, ("lutb12", "hvb10", "s8"), WHITE) + 4
        for line in (_when(item), f"Depth {item.get('depth', 0):.0f} km"):
            k.text(c, k.pick(("hv10", "s8"), line, pw - 14), px_ + 8, y, (190, 195, 210), line)
            y += 12
        if item.get("felt"):
            k.text(c, k.font("hv10"), px_ + 8, y, (255, 220, 120), f"Felt by {item['felt']:,} people (DYFI)")
            y += 12
        y += 3
        y += k.fit_text(c, item.get("setting") or "", px_ + 8, y, pw - 14, H - y - 26, ("hv10", "s8"), (150, 170, 200)) + 4
        _intensity_here(c, item, px_ + 4, H - 20, pw - 6, k.font("hvb10"))
        k.text(c, k.font("s8"), 4, H - 10, (120, 130, 150), f"{item.get('source', '')} · dots: last 30 days")

    s.static(c, ("pnsn", item["id"], bool(lv)), static)
    if mp:
        _waves(c, mp, item, now, col, (0, 0, px_, H))
        if lv:
            _stations(c, mp, lv, clip=(0, 0, px_, H))
        _star(c, ex, ey, 7, col if k.blink(now, 0.8, 0.7) else WHITE)
    if lv:
        _trace(c, lv, px_ + 4, 84, pw - 8, 36)
        _live_caption(c, lv)
    t = s_arrival(item)
    if t is not None and -60 < t - now:
        k.box(c, px_ + 2, H - 70, pw - 4, 48, (20, 6, 6))
        countdown(c, item, now, px_ + 8, H - 66, pw - 16)
    _replay_badge(c, item, (px_ - 44, 4))


# ------------------------------------------------------------------------------------------------ shakemap
def _shake_field(mp, item):
    m = mp.meta
    xs = m["merc"][0] + (np.arange(mp.w) + 0.5) * m["mpp"]
    ys = m["merc"][3] - (np.arange(mp.h) + 0.5) * m["mpp"]
    lon = np.degrees(xs / k.R)[None, :]
    lat = np.degrees(2 * np.arctan(np.exp(ys / k.R)) - np.pi / 2)[:, None]
    p1, l1 = math.radians(item["lat"]), math.radians(item["lon"])
    la, lo = np.radians(lat), np.radians(lon)
    a = np.sin((la - p1) / 2) ** 2 + np.cos(la) * math.cos(p1) * np.sin((lo - l1) / 2) ** 2
    d = 6371.0 * 2 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    return np.rint(mmi_aw07(float(item.get("mag") or 0), d, float(item.get("depth") or 10))).astype(np.int8)


def _shakemap(s, c, item, now, el):
    mp = _pick_map(s, item)
    tw_ = 150
    col = mag_col(item.get("mag"))

    def static():
        if mp:
            mp.blit(c, 0, 0, 0, 0, W, H)
            f = _shake_field(mp, item)
            for i in range(2, 11):
                sel = f == i
                if sel.any():
                    k.tint(c, sel, MMI_COL[i], 0.42)
            edges = np.zeros_like(f, bool)
            edges[:, 1:] |= f[:, 1:] != f[:, :-1]
            edges[1:, :] |= f[1:, :] != f[:-1, :]
            k.paint(c, edges & (f >= 2), (230, 230, 230))
            _rings(c, mp, _home(s), (0, 0, 0), labels=False)
        # title strip
        t = f"M{_mag_text(item)}  {_place(item)}"
        ft = k.pick(("h16", "lutb12", "hvb10"), t, W - tw_ - 16)
        k.shade(c, 0, 0, W - tw_, ft.h + 8, 0.25)
        k.text(c, ft, 6, 4, WHITE, t)
        k.text(c, k.font("hv10"), 6, ft.h + 10, (220, 220, 230), f"{_when(item)} · depth {item.get('depth', 0):.0f} km")
        # legend
        lx, ly = 6, H - 14
        for i in range(2, 11):
            k.box(c, lx, ly, 21, 10, MMI_COL[i])
            k.text_c(c, k.font("s8"), lx + 10, ly + 1, BLACK, ROMAN[i])
            lx += 22
        k.text(c, k.font("s8"), lx + 4, ly + 1, (220, 220, 230), "MMI")
        # city table
        x0 = W - tw_
        k.box(c, x0, 0, tw_, H, (8, 9, 14))
        k.box(c, x0, 0, 1, H, (60, 70, 90))
        k.text(c, k.font("lutb12"), x0 + 6, 4, WHITE, "Estimated shaking")
        y = 22
        _intensity_here(c, item, x0 + 4, y, tw_ - 6, k.font("hvb10"))
        y += 22
        for cty in (item.get("cities") or [])[:7]:
            i = mmi_i(cty["mmi"])
            k.box(c, x0 + 6, y + 1, 26, 12, MMI_COL[i])
            k.text_c(c, k.font("hvb10"), x0 + 19, y + 2, BLACK, ROMAN[i])
            k.text(c, k.font("hv10"), x0 + 38, y + 2, (220, 220, 230), cty["name"])
            k.text_r(c, k.font("hv10"), W - 6, y + 2, (150, 155, 170), f"{cty['km']} km")
            y += 15
        k.fit_text(c, "Estimate from magnitude and distance, not instruments", x0 + 6, y + 2, tw_ - 10, H - y - 2,
                   ("s8",), (110, 115, 130))

    s.static(c, ("shake", item["id"]), static)
    if mp:
        ex, ey = _waves(c, mp, item, now, col, (0, 0, W - tw_, H))
        _star(c, ex, ey, 7, col if k.blink(now, 0.8, 0.7) else WHITE)


# ------------------------------------------------------------------------------------------------ report
def _report(s, c, item, now, el):
    col = mag_col(item.get("mag"))

    def static():
        k.box(c, 0, 0, W, 20, (26, 30, 40))
        k.icon(c, "pulse", 6, 2, col)
        k.text(c, k.font("lutb12"), 28, 4, WHITE, "EARTHQUAKE REPORT")
        k.text_r(c, k.font("hv10"), W - 6, 5, (170, 175, 190), item.get("source") or "")
        # magnitude block
        k.box(c, 0, 20, 112, H - 20, (14, 16, 22))
        mt = _mag_text(item)
        dw = 24 if len(mt) <= 3 else 19
        k.seven_seg(c, 10, 32, mt, dw, 46, 4, on=col, off=(36, 36, 44))
        k.text(c, k.font("lutb12"), 10, 86, (190, 195, 210), f"Magnitude {item.get('mag_type') or ''}".strip())
        y = 108 + _intensity_here(c, item, 4, 108, 104, k.font("hvb10")) + 6
        if item.get("felt"):
            k.text(c, k.font("hvb10"), 8, y, (255, 220, 120), f"{item['felt']:,} felt it")
            k.text(c, k.font("s8"), 8, y + 12, (150, 155, 170), "USGS Did You Feel It?")
            y += 26
        if item.get("alert"):
            ac = {"green": (60, 200, 90), "yellow": (255, 220, 0), "orange": (255, 140, 0), "red": (255, 40, 40)}.get(item["alert"], WHITE)
            k.chip(c, k.font("m10b"), 8, y, f"PAGER {item['alert'].upper()}", ac, BLACK)
            y += 16
        if item.get("tsunami"):
            k.chip(c, k.font("m10b"), 8, y, "TSUNAMI FLAG", (80, 170, 255), BLACK)
        # place, time, depth, setting
        x0 = 122
        f = k.pick(("h20", "h18", "h16", "lutb12"), _place(item), 200) if len(_place(item)) < 30 else None
        if f:
            k.text(c, f, x0, 26, WHITE, _place(item))
            y = 26 + f.h + 4
        else:
            y = 26 + k.fit_text(c, _place(item), x0, 26, 200, 40, ("h16", "lutb12", "hvb10"), WHITE) + 4
        for lab, val in (("Time", _when(item)), ("Depth", f"{item.get('depth', 0):.0f} km"),
                         ("Location", f"{item['lat']:.2f}°N {abs(item['lon']):.2f}°W" if item.get("lat") is not None else "")):
            k.text(c, k.font("hvb10"), x0, y, (150, 160, 180), lab)
            k.text(c, k.pick(("lut12", "hv10"), val, 200 - 52), x0 + 52, y - 1, (230, 230, 235), val)
            y += 15
        y += 4
        k.fit_text(c, item.get("setting") or "", x0, y, 200, H - y - 4, ("lut12", "hv10", "s8"), (150, 175, 210))
        # city intensities
        x1 = 330
        k.box(c, x1 - 6, 20, 1, H - 20, (40, 46, 60))
        k.text(c, k.font("hvb10"), x1, 24, (150, 160, 180), "EST. SHAKING")
        y = 38
        for cty in (item.get("cities") or [])[:9]:
            i = mmi_i(cty["mmi"])
            k.box(c, x1, y, 24, 13, MMI_COL[i])
            k.text_c(c, k.font("hvb10"), x1 + 12, y + 1, BLACK, ROMAN[i])
            k.text(c, k.font("hv10"), x1 + 30, y + 2, (220, 220, 230), cty["name"])
            k.text_r(c, k.font("s8"), W - 4, y + 3, (140, 145, 160), f"{cty['km']}")
            y += 16

    s.static(c, ("rep", item["id"]), static)


# ------------------------------------------------------------------------------------------------ drop (early warning)
def _drop(s, c, item, now, el):
    """Canada's early warning. With an epicentre: the waves on the map, a big S-wave countdown to home and the
    expected shaking — like the Japan EEW screen; without one: the warning text with DROP · COVER · HOLD ON."""
    if item.get("lat") is not None:
        return _drop_live(s, c, item, now, el)
    on = k.blink(now, 0.6)

    def static():
        k.box(c, 0, 0, W, 34, (200, 0, 0))
        k.icon(c, "pulse", 8, 9, WHITE)
        k.text(c, k.font("h24"), 30, 5, WHITE, "EARTHQUAKE")
        k.text_r(c, k.font("h18r"), W - 8, 9, (255, 210, 210), "SÉISME")
        bw = (W - 24) // 3
        for i, (t, sub) in enumerate((("DROP", "to your hands and knees"), ("COVER", "your head and neck"), ("HOLD ON", "until the shaking stops"))):
            x = 6 + i * (bw + 6)
            k.box(c, x, 40, bw, 44, (255, 200, 0))
            k.text_c(c, k.font("h24"), x + bw // 2, 44, BLACK, t)
            k.text_c(c, k.pick(("hvb10", "s8"), sub, bw - 6), x + bw // 2, 70, (60, 40, 0), sub)
        paras = [Para(item.get("headline") or "Earthquake early warning", "head", WHITE, 0),
                 Para(item.get("description") or "", "body", (220, 220, 225), 2),
                 Para(item.get("instruction") or "", "body", (255, 220, 120), 1)]
        lines, _ = k.fit(paras, W - 16, H - 92 - 18,
                         [{"head": "h16", "body": "lut12"}, {"head": "lutb12", "body": "hv10"}, {"head": "hvb10", "body": "s8"}])
        k.draw_fit(c, lines, 8, 92)
        area = " · ".join(item.get("areas") or [])
        k.text(c, k.pick(("hv10", "s8"), area, W - 120), 8, H - 13, (200, 200, 210), area)
        k.text_r(c, k.font("hv10"), W - 8, H - 13, (200, 200, 210), f"NRCan · {k.hhmm(item.get('time'), True)}")

    s.static(c, ("drop", item["id"]), static)
    if not on:
        k.box(c, 0, 34, W, 2, WHITE)
    else:
        c.buf[0:34, :, 1:] = 0


def _drop_live(s, c, item, now, el):
    mp = _pick_map(s, item)
    mw = 236
    col = mag_col(item.get("mag"))
    x0 = mw + 6
    t_s = s_arrival(item)

    def static():
        if mp:
            ex, ey = mp.project(item["lat"], item["lon"])
            hx, hy = mp.project(_home(s)[1], _home(s)[0])
            sx = int(max(0, min(mp.w - mw, (ex + hx) / 2 - mw / 2)))
            s.state["sx"] = sx
            mp.blit(c, 0, 0, sx, 0, mw, H)
        k.box(c, mw, 0, W - mw, H, (14, 0, 0))
        k.box(c, mw, 0, 2, H, (220, 0, 0))
        k.text(c, k.font("h20"), x0, 3, WHITE, "EARTHQUAKE")
        k.text(c, k.font("hvb10"), x0, 25, (255, 190, 190), "EARLY WARNING · NRCan")
        bits = [f"M{item['mag']:.1f}" if item.get("mag") else "", f"{item['dist_km']:.0f} km away" if item.get("dist_km") else ""]
        k.text(c, k.font("lutb12"), x0, 38, (255, 220, 200), "  ·  ".join(b for b in bits if b))
        if item.get("mmi_home"):
            i = mmi_i(item["mmi_home"])
            t = f"{ROMAN[i]} {MMI_WORD[i]}"
            k.icon(c, "house", x0, 54, (230, 200, 200))
            k.text(c, k.font("hvb10"), x0 + 20, 57, (230, 200, 200), "expect")
            k.chip(c, k.font("hvb10"), x0 + 62, 55, t, MMI_COL[i], BLACK, 3, 1)
        y = H - 40
        bw = (W - x0 - 12) // 3
        for j, t in enumerate(("DROP", "COVER", "HOLD ON")):
            bx = x0 + j * (bw + 4)
            k.box(c, bx, y, bw, 22, (255, 200, 0))
            k.text_c(c, k.pick(("h16", "lutb12", "hvb10"), t, bw - 4), bx + bw // 2, y + 4, BLACK, t)
        k.fit_text(c, " · ".join(item.get("areas") or []), x0, H - 14, W - x0 - 6, 12, ("hv10", "s8"), (220, 190, 190))

    s.static(c, ("dropl", item["id"]), static)
    if mp:
        sx = s.state.get("sx", 0)
        ex, ey = mp.project(item["lat"], item["lon"])
        tmp_el = now - float(item.get("time") or now)
        dep = float(item.get("depth") or 10)
        for v, wc in ((VP, (90, 160, 255)), (VS, (255, 70, 40))):
            r2 = (v * tmp_el) ** 2 - dep ** 2
            if r2 > 0:
                k.circle_clip(c, ex - sx, ey, math.sqrt(r2) / mp.km_per_px(), wc, (0, 0, mw, H))
        lv = _live(s, now)
        if lv:
            _stations(c, mp, lv, ox=-sx, clip=(0, 0, mw, H - 30))
        _star(c, ex - sx, ey, 6, col if k.blink(now, 0.6) else WHITE)
        hx, hy = mp.project(_home(s)[1], _home(s)[0])
        k.icon_outlined(c, "house", int(hx - sx) - 8, int(hy) - 7, WHITE)
        if lv:
            _trace(c, lv, 0, H - 30, mw, 30)
    _replay_badge(c, item, (4, 4))
    # the countdown
    y = 74
    if t_s is not None and t_s - now > 0:
        left = int(math.ceil(t_s - now))
        k.text(c, k.font("hvb10"), x0, y, (255, 200, 120), "S WAVE REACHES HOME IN")
        txt = str(left)
        k.seven_seg(c, x0, y + 13, txt, 22, 58, 4, on=(255, 50, 30), off=(50, 8, 8))
        k.text(c, k.font("h20"), x0 + len(txt) * 28 + 6, y + 48, (255, 120, 90), "s")
    elif t_s is not None and t_s - now > -90:
        if k.blink(now, 0.5):
            k.box(c, x0, y + 6, W - x0 - 6, 50, (220, 0, 0))
        k.text_c(c, k.font("h24"), x0 + (W - x0) // 2, y + 18, WHITE, "SHAKING NOW")
    else:
        k.fit_text(c, item.get("headline") or "", x0, y, W - x0 - 6, 70, ("h16", "lutb12"), WHITE)


# ------------------------------------------------------------------------------------------------ samples
SAMPLES = {
    "victoria": {"label": "M4.8 in-slab quake near Victoria", "item": {
        "kind": "quake", "mag": 4.8, "mag_type": "Mw", "place_short": "18 km NE of Victoria", "lat": 48.55, "lon": -123.22,
        "depth": 52, "time_rel": -1260, "felt": 1820, "cdi": 4.6, "alert": "green", "tsunami": False, "source": "USGS + NRCan",
        "setting": "Deep, inside the subducting Juan de Fuca plate (an in-slab quake)", "mmi_home": 3.4,
        "cities": [{"name": "Victoria", "km": 18, "mmi": 4.4}, {"name": "Port Angeles", "km": 51, "mmi": 3.9},
                   {"name": "Nanaimo", "km": 80, "mmi": 3.4}, {"name": "Vancouver", "km": 86, "mmi": 3.3},
                   {"name": "Surrey", "km": 89, "mmi": 3.3}, {"name": "Bellingham", "km": 92, "mmi": 3.2},
                   {"name": "Seattle", "km": 113, "mmi": 3.0}, {"name": "Abbotsford", "km": 126, "mmi": 2.9}]}},
    "georgia": {"label": "M3.1 under the Strait of Georgia", "item": {
        "kind": "quake", "mag": 3.1, "mag_type": "ML", "place_short": "12 km W of Tsawwassen", "lat": 49.03, "lon": -123.25,
        "depth": 14, "time_rel": -600, "felt": 140, "source": "NRCan", "mmi_home": 3.1,
        "setting": "Shallow, in the crust of the North American plate",
        "cities": [{"name": "Vancouver", "km": 29, "mmi": 3.1}, {"name": "Surrey", "km": 33, "mmi": 3.0},
                   {"name": "Victoria", "km": 69, "mmi": 2.4}, {"name": "Bellingham", "km": 58, "mmi": 2.5},
                   {"name": "Nanaimo", "km": 59, "mmi": 2.5}, {"name": "Abbotsford", "km": 69, "mmi": 2.4}]}},
    "puget": {"label": "M6.8 deep under Puget Sound", "item": {
        "kind": "quake", "mag": 6.8, "mag_type": "Mw", "place_short": "17 km NE of Olympia, WA", "lat": 47.15, "lon": -122.73,
        "depth": 52, "time_rel": -420, "felt": 14200, "cdi": 7.2, "alert": "yellow", "tsunami": False, "source": "USGS",
        "setting": "Deep, inside the subducting Juan de Fuca plate (an in-slab quake)", "mmi_home": 4.6,
        "cities": [{"name": "Olympia", "km": 17, "mmi": 7.3}, {"name": "Tacoma", "km": 36, "mmi": 6.8},
                   {"name": "Seattle", "km": 63, "mmi": 6.1}, {"name": "Port Angeles", "km": 120, "mmi": 5.3},
                   {"name": "Victoria", "km": 150, "mmi": 5.0}, {"name": "Bellingham", "km": 180, "mmi": 4.8},
                   {"name": "Vancouver", "km": 237, "mmi": 4.4}, {"name": "Surrey", "km": 228, "mmi": 4.5}]}},
    "offshore": {"label": "M6.2 off Vancouver Island", "item": {
        "kind": "quake", "mag": 6.2, "mag_type": "Mww", "place_short": "190 km W of Tofino", "lat": 49.12, "lon": -128.55,
        "depth": 10, "time_rel": -2400, "felt": 210, "alert": "green", "tsunami": True, "source": "USGS + NRCan",
        "setting": "Offshore, along the Juan de Fuca and Explorer plate boundaries", "mmi_home": 2.6,
        "cities": [{"name": "Tofino", "km": 190, "mmi": 4.0}, {"name": "Port Alberni", "km": 270, "mmi": 3.4},
                   {"name": "Port Hardy", "km": 220, "mmi": 3.7}, {"name": "Courtenay", "km": 270, "mmi": 3.4},
                   {"name": "Nanaimo", "km": 335, "mmi": 3.0}, {"name": "Victoria", "km": 380, "mmi": 2.8}]}},
    "eew": {"label": "Early warning (Alert Ready)", "item": {
        "kind": "eew", "time_rel": -6, "sender": "Natural Resources Canada", "severity": "Severe",
        "lat": 48.9, "lon": -125.9, "depth": 20, "mag": 6.8, "dist_km": 201, "mmi_home": 5.1, "origin_known": True,
        "headline": "Earthquake early warning: strong shaking expected",
        "description": "An earthquake has been detected off the west coast of Vancouver Island. Strong shaking is expected "
                       "in the next seconds in Greater Victoria and Metro Vancouver.",
        "instruction": "Drop, cover and hold on. If you are driving, pull over and stop. Stay away from windows.",
        "areas": ["Greater Victoria", "Metro Vancouver", "Fraser Valley"]}},
}

THEMES = {
    "pnsn": {"label": "Network map", "hint": "Epicentre, 30 days of background quakes and distance rings round home; facts panel",
             "draw": _pnsn, "badge": (W - 160 - 50, 4)},
    "shakemap": {"label": "ShakeMap", "hint": "Estimated intensity field in ShakeMap colours, with a city table",
                 "draw": _shakemap, "badge": (W - 150 - 50, H - 30)},
    "report": {"label": "Earthquake report", "hint": "Report card: magnitude, place, time, depth, setting, felt reports, city intensities",
               "draw": _report, "badge": (230, 4)},
    "drop": {"label": "Early warning", "hint": "DROP · COVER · HOLD ON with the warning text (always used for NRCan warnings)",
             "draw": _drop, "badge": (200, 10)},
}


class Screen(k.BCScreen):
    PANEL, DATA, SETTINGS = "quake_alert", "quakes", "bc_quakes"
    THEMES, SAMPLES, DEFAULT_THEME = THEMES, SAMPLES, "pnsn"

    live, live_at, _clock = None, 0.0, 0.0
    LABEL = "EARTHQUAKE"

    def extra_pages(self, item):
        return [] if item.get("kind") in ("eew", "detect") else ["shakemap", "report"]

    def half_spec(self, item, theme=None):
        eew = item.get("kind") == "eew"
        col = (255, 40, 40) if eew else mag_col(item.get("mag"))
        theme = theme if theme in THEMES else self.DEFAULT_THEME

        def hero_(c, x, y, mw, mh):
            mt = _mag_text(item)
            k.text(c, k.font("hvb10"), x, y, (170, 180, 200), "M")
            k.seven_seg(c, x + 9, y + 2, mt, 12, 26, 3, on=col, off=(30, 30, 36))
            return 9 + len(mt) * 15, 30

        def map_(c, x, y, w, h, now):
            if item.get("lat") is None:
                return
            mp = _pick_map(self, item)
            if not mp:
                return
            ex, ey = mp.project(item["lat"], item["lon"])
            sx, sy = k.map_crop(c, mp, x, y, w, h, ex, ey)
            if theme == "shakemap":
                c.Blit(_half_shake(self, mp, item, sx, sy, w, h), x, y)
            if theme == "pnsn":
                for la, lo, m, t, dep in self.data().get("recent") or []:
                    if abs(t - (item.get("time") or 0)) < 60:
                        continue
                    qx, qy = mp.project(la, lo)
                    if 0 <= qx - sx < w and 0 <= qy - sy < h:
                        qc = (130, 170, 210) if dep < 30 else (170, 140, 220)
                        k.disc(c, x + qx - sx, y + qy - sy, 1, qc) if m >= 3.5 else k.box(c, x + qx - sx, y + qy - sy, 1, 1, qc)
            hx, hy = mp.project(_home(self)[1], _home(self)[0])
            if 0 <= hx - sx < w and 0 <= hy - sy < h:
                k.icon_outlined(c, "house", int(x + hx - sx) - 8, int(y + hy - sy) - 7, (230, 230, 240))
            px, py = x + ex - sx, y + ey - sy
            for i in range(3):
                r = ((now * 18) + i * 14) % 42 + 4
                k.circle_clip(c, px, py, r, k.dim(col, max(0.15, 1 - r / 46)), (x, y, x + w, y + h))
            _star(c, px, py, 6, col if k.blink(now, 0.8, 0.7) else WHITE)

        if eew:
            spec = {"label": "EARLY WARNING", "col": col, "chip": ("DROP COVER HOLD ON", (255, 200, 0)), "hero": None,
                    "title": item.get("headline") or "Earthquake early warning",
                    "lines": [(" · ".join(item.get("areas") or []), (230, 200, 200))],
                    "body": [Para(item.get("description") or "", "body", (220, 220, 225)),
                             Para(item.get("instruction") or "", "head", (255, 220, 120))], "map": map_}
        else:
            i = mmi_i(item.get("mmi_home"))
            spec = {"label": self.LABEL, "col": col, "chip": (f"{ROMAN[i]} {MMI_WORD[i].upper()}", MMI_COL[i]), "hero": hero_,
                    "title": _place(item),
                    "lines": [(_when(item), (200, 205, 220)), (f"Depth {float(item.get('depth') or 0):.0f} km", (200, 205, 220)),
                              (f"Felt by {item['felt']:,} (DYFI)" if item.get("felt") else "", (255, 220, 120))],
                    "body": [Para(item.get("setting") or "", "body", (180, 200, 230)),
                             Para("Estimated shaking: " + ", ".join(f"{cy['name']} {ROMAN[mmi_i(cy['mmi'])]}" for cy in (item.get("cities") or [])[:6]),
                                  "body", (220, 220, 225))], "map": map_}
        return HALF[theme](self, item, spec, col)
    def set_obs(self, body):
        """POST /bc_quake_obs from the hub: station readings once a second while a quake is fresh."""
        self.live, self.live_at = body, time.time()

    def all_items(self):
        items = super().all_items()
        it = (_live(self, time.time()) or {}).get("item")
        if it and it.get("id") and not any(i.get("id") == it["id"] for i in items):
            items = [it] + items                          # a detection (or replay) the catalogues haven't got
        return items

    def theme_for(self, item, entry):
        if item.get("kind") == "detect":
            return "pnsn"
        if item.get("kind") == "eew":
            return "drop"
        tid = super().theme_for(item, entry)
        return "report" if tid == "drop" else tid             # the warning screen only fits warnings

    def dwell(self, item):
        if item.get("kind") == "eew":
            t = s_arrival(item)
            return max(40.0, (t - item.get("time", 0)) + 60) if t else 40.0
        base = max(float(self.settings.get("dwell_sec") or 25), 20.0)
        t = float(item.get("time") or self.t0)
        if item.get("kind") == "detect" or (_live(self) and 0 <= self._clock - t < 300):
            return max(base, t + 150 - self.t0)           # live readings: stay until the waves have passed
        return base


# ------------------------------------------------------------------------------------------------ half-width looks
def _half_shake(s, mp, item, sx, sy, w, h):
    """The map crop with the estimated intensity field in ShakeMap colours (drawn once per item and crop)."""
    def draw(cv):
        mp.blit(cv, 0, 0, sx, sy, min(w, mp.w - sx), min(h, mp.h - sy))
        f = _shake_field(mp, item)[sy:sy + h, sx:sx + w]
        for i in range(2, 11):
            sel = f == i
            if sel.any():
                k.tint(cv, sel, MMI_COL[i], 0.42)
        e = np.zeros_like(f, bool)
        e[:, 1:] |= f[:, 1:] != f[:, :-1]
        e[1:, :] |= f[1:, :] != f[:-1, :]
        k.paint(cv, e & (f >= 2), (230, 230, 230))
    return k.half_layer(s, ("shake", item.get("id"), sx, sy, w, h), w, h, draw)


def _cities(item, n=7):
    def page(c, x, y, w, h, now, el):
        k.text(c, k.font("lutb12"), x, y, WHITE, "Estimated shaking")
        ty = y + 15
        ty += _intensity_here(c, item, x, ty, w, k.font("hvb10")) + 3
        for cty in (item.get("cities") or [])[:n]:
            if ty > y + h - 13:
                break
            i = mmi_i(cty["mmi"])
            k.box(c, x, ty + 1, 26, 12, MMI_COL[i])
            k.text_c(c, k.font("hvb10"), x + 13, ty + 2, BLACK, ROMAN[i])
            k.text(c, k.font("hv10"), x + 32, ty + 2, (220, 220, 230), cty["name"])
            k.text_r(c, k.font("hv10"), x + w, ty + 2, (150, 155, 170), f"{cty['km']} km")
            ty += 14
    return page


def _half_pnsn(s, item, spec, col):
    """The network map first: the epicentre among 30 days of background quakes, a fact strip under it."""
    base = spec["map"]

    def map_(c, x, y, w, h, now):
        base(c, x, y, w, h - 14, now)
        k.box(c, x, y + h - 14, w, 14, (12, 16, 26))
        k.text(c, k.font("hvb10"), x + 4, y + h - 12, col, f"M{_mag_text(item)}")
        k.fit_text(c, f"{_place(item)} · {float(item.get('depth') or 0):.0f} km deep", x + 34, y + h - 12, w - 38, 11,
                   ("hv10", "s8"), (220, 225, 235))

    spec.update(map=map_, label="PNSN · EARTHQUAKE", look={"order": ("map", "summary", "text"), "head_bg": (12, 16, 26),
                                                           "bg": (6, 8, 14)})
    return spec


def _half_shakemap(s, item, spec, col):
    """ShakeMap colours: the intensity at home as the headline, the coloured field on the map, a city table."""
    i = mmi_i(item.get("mmi_home"))

    def summary(c, x, y, w, h, now, el):
        k.box(c, x, y, 52, 46, MMI_COL[i])
        k.text_c(c, k.font("h24"), x + 26, y + 4, BLACK, ROMAN[i] or "–")
        k.text_c(c, k.font("s8"), x + 26, y + 34, BLACK, "AT HOME")
        tx = x + 58
        ty = y + k.fit_text(c, MMI_WORD[i] + " shaking", tx, y, w - 58, 18, ("h16", "lutb12", "hvb10"), MMI_COL[i]) + 2
        ty += k.fit_text(c, f"M{_mag_text(item)}  {_place(item)}", tx, ty, w - 58, 28, ("lutb12", "hvb10", "s8"), WHITE) + 2
        k.fit_text(c, _when(item), tx, ty, w - 58, 12, ("hv10", "s8"), (190, 195, 210))
        lx, ly = x, y + h - 12
        cw = max(10, min(22, w // 9))
        for j in range(2, 11):
            k.box(c, lx, ly, cw - 1, 10, MMI_COL[j])
            k.text_c(c, k.font("s8"), lx + cw // 2, ly + 1, BLACK, ROMAN[j])
            lx += cw

    spec["chip"] = ("MMI", (230, 230, 230))
    spec["body"] = spec["body"][:1] if item.get("kind") != "eew" else spec["body"]   # (the cities have their own page)
    spec.update(summary=summary, custom={"cities": _cities(item)}, label="SHAKEMAP",
                look={"order": ("summary", "map", "cities", "text"), "bg": (8, 9, 14), "head_bg": (8, 9, 14),
                      "rule": (60, 70, 90)})
    return spec


def _half_report(s, item, spec, col):
    """The report card: a seven-segment magnitude, the place, time and depth; the cities after."""
    def summary(c, x, y, w, h, now, el):
        mt = _mag_text(item)
        k.box(c, x - 4, y - 2, 70, h + 5, (14, 16, 22))
        k.seven_seg(c, x + 1, y + 4, mt, 16 if len(mt) <= 3 else 13, 32, 3, on=col, off=(36, 36, 44))
        k.text(c, k.font("hvb10"), x, y + 42, (190, 195, 210), f"Mag {item.get('mag_type') or ''}".strip())
        if item.get("felt"):
            k.text(c, k.font("hvb10"), x, y + 58, (255, 220, 120), f"{item['felt']:,}")
            k.text(c, k.font("s8"), x, y + 70, (150, 155, 170), "felt it")
        tx = x + 72
        ty = y + k.fit_text(c, _place(item), tx, y, w - 72, 36, ("h16", "lutb12", "hvb10"), WHITE) + 3
        for lab, val in (("Time", k.day_hhmm(item.get("time"))), ("Depth", f"{float(item.get('depth') or 0):.0f} km"),
                         ("Ago", k.ago(item.get("time")))):
            k.text(c, k.font("hvb10"), tx, ty, (150, 160, 180), lab)
            k.text(c, k.pick(("lut12", "hv10"), val, w - 72 - 40), tx + 40, ty - 1, (230, 230, 235), val)
            ty += 14
        _intensity_here(c, item, tx, ty + 2, w - 72, k.font("hvb10"))

    spec["hero"] = None
    spec["body"] = spec["body"][:1] if item.get("kind") != "eew" else spec["body"]   # (the cities have their own page)
    spec.update(summary=summary, custom={"cities": _cities(item)}, label="EARTHQUAKE REPORT",
                look={"order": ("summary", "cities", "text", "map"), "head_bg": (26, 30, 40), "rule": col})
    return spec


def _half_drop(s, item, spec, col):
    """The early warning: a red EARTHQUAKE slab, DROP · COVER · HOLD ON in yellow, the S-wave countdown."""
    def summary(c, x, y, w, h, now, el):
        bw = (w - 4) // 3
        for i, t in enumerate(("DROP", "COVER", "HOLD ON")):
            bx = x + i * (bw + 2)
            k.box(c, bx, y, bw, 24, (255, 200, 0))
            k.text_c(c, k.pick(("h16", "lutb12", "hvb10"), t, bw - 4), bx + bw // 2, y + 5, BLACK, t)
        ty = y + 28
        used = countdown(c, item, now, x, ty, w, big=False) if item.get("lat") is not None else 0
        ty += used + 2 if used else 0
        k.fit_text(c, item.get("headline") or "Earthquake early warning", x, ty, w, y + h - ty - 12, ("lutb12", "hvb10", "s8"), WHITE)
        k.fit_text(c, " · ".join(item.get("areas") or []), x, y + h - 11, w, 11, ("hv10", "s8"), (230, 200, 200))

    spec.update(summary=summary, label="EARTHQUAKE", chip=None,
                look={"head": "slab", "head_bg": (200, 0, 0), "head_fg": WHITE, "pulse": True, "head_h": 15, "label_font": "h13",
                      "bg": BLACK, "border": "pulse", "border_col": (255, 200, 0)})
    return spec


HALF = {"pnsn": _half_pnsn, "shakemap": _half_shakemap, "report": _half_report, "drop": _half_drop}


def render_preview(theme, sample=None, at=4.0, settings=None, data=None, data_dir=None):
    if theme == "drop" and not sample:
        sample = "eew"
    return k.render_preview(Screen, theme, sample, at, settings, data, data_dir)
