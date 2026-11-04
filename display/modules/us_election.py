"""US Election screen: a tile map of the states with live results, beside the balance of power and key races.

Everything above the clock strip (448 x 175, a "tall" panel): the ticker gives way, the clock / status strip stays.

    map (286 px, 11 x 8 tiles of 25 x 21)              | sidebar (156 px)
      one tile per state for the office on screen:     |   Senate / House / governors seat bars
      polls open → closing time; counting → D and R %  |   key races of the office on screen:
      and % in on the leader's tint; called → solid    |   state (district) · D|R bar · D % · R % · % in
                                                       |
Rotation entry fields: ``election_view`` = "cycle" (Senate → House → governors, ``cycle_sec`` each; one pass is
the full cycle) or "senate" | "house" | "governor" (one pass = ``cycle_sec``). Data: data/election.json from
hub/jobs/election.py. The frame is drawn once per data update / view change and copied after that.

Live feedback, drawn over that frame each tick (small regions only, so it costs next to nothing on the Pi):
  * LIVE dot pulsing by the title (white for a moment when data arrives; STALE in yellow when it's old) and
    "12s ago" after the source; a scan line sweeps down the map whenever a fetch lands
  * a tile, key-race row or seat count whose numbers changed flashes a white outline / highlight that fades
    (yellow when a state's polls close); changes in an office not on screen flash when it comes up
  * a race called: its tile strobes in the party colour, and a CALLED banner names it in the header
  * news in the status strip (between the clock and the date, while this screen is up): calls with the winner,
    shares and % in; polls closing; seat counts moving. Each line scrolls through once (``ticker`` / ``draw_ticker``)
"""

from __future__ import annotations

import math
import time
from collections import deque
from datetime import datetime

import numpy as np

from display.modules.bc_kit import font, text, text_r, tw

W, H = 448, 175
VIEWS = ("senate", "house", "governor")
TITLES = {"senate": "SENATE", "house": "HOUSE", "governor": "GOVERNORS"}
SHORT = {"senate": "SEN", "house": "HOUSE", "governor": "GOV"}
DEFAULT_CYCLE_SEC = 20.0

GRID = {
    "ME": (0, 10),
    "AK": (1, 0), "VT": (1, 9), "NH": (1, 10),
    "WA": (2, 0), "ID": (2, 1), "MT": (2, 2), "ND": (2, 3), "MN": (2, 4), "IL": (2, 5), "WI": (2, 6),
    "MI": (2, 7), "NY": (2, 8), "RI": (2, 9), "MA": (2, 10),
    "OR": (3, 0), "NV": (3, 1), "WY": (3, 2), "SD": (3, 3), "IA": (3, 4), "IN": (3, 5), "OH": (3, 6),
    "PA": (3, 7), "NJ": (3, 8), "CT": (3, 9),
    "CA": (4, 0), "UT": (4, 1), "CO": (4, 2), "NE": (4, 3), "MO": (4, 4), "KY": (4, 5), "WV": (4, 6),
    "VA": (4, 7), "MD": (4, 8), "DE": (4, 9),
    "HI": (5, 0), "AZ": (5, 1), "NM": (5, 2), "KS": (5, 3), "AR": (5, 4), "TN": (5, 5), "NC": (5, 6), "SC": (5, 7), "DC": (5, 8),
    "OK": (6, 3), "LA": (6, 4), "MS": (6, 5), "AL": (6, 6), "GA": (6, 7),
    "TX": (7, 3), "FL": (7, 8),
}
TW, TH, OX, OY = 26, 22, 1, 0          # tile pitch (25 x 21 tiles, 1-px gaps)
SX = 292                                # sidebar x

DEM, REP, IND = (30, 100, 255), (235, 35, 35), (190, 90, 230)
PARTY = {"D": DEM, "R": REP, "I": IND}
WHITE, GREY, YEL = (255, 255, 255), (120, 120, 120), (255, 200, 40)
NO_RACE, OPEN, WAITING, RULE = (10, 10, 10), (28, 28, 28), (40, 40, 40), (40, 40, 40)
LIGHT_D, LIGHT_R = (120, 170, 255), (255, 110, 110)


def dim(c, k):
    return tuple(int(v * k) for v in c)


def fill(c, x, y, w, h, col):
    if w > 0 and h > 0:
        c.buf[max(0, y):y + h, max(0, x):x + w] = col


def outline(c, x, y, w, h, col):
    fill(c, x, y, w, 1, col)
    fill(c, x, y + h - 1, w, 1, col)
    fill(c, x, y, 1, h, col)
    fill(c, x + w - 1, y, 1, h, col)


def check(c, x, y, col):
    """A 7 x 5 tick."""
    for dx, dy in ((0, 2), (1, 3), (2, 4), (3, 3), (4, 2), (5, 1), (6, 0)):
        if 0 <= y + dy < c.buf.shape[0] and 0 <= x + dx < c.buf.shape[1]:
            c.buf[y + dy, x + dx] = col


def clock(ts):
    """Local time like 7:30 (the Pi runs in Vancouver)."""
    if not ts:
        return ""
    d = datetime.fromtimestamp(ts)
    return f"{d.hour % 12 or 12}:{d.minute:02d}"


FLASH_SEC = {"update": 2.5, "close": 3.0, "call": 6.0}
STROBE_SEC = 2.4                    # a call strobes this long, then fades like an update
SWEEP_SEC = 0.8
BANNER_SEC = 4.0


def blend(c, x, y, w, h, col, k, only_dark=False):
    """Mix ``col`` into a rectangle by ``k`` (0..1); ``only_dark`` = just the black background (behind text)."""
    if k <= 0 or w <= 0 or h <= 0:
        return
    y0, x0 = max(0, y), max(0, x)
    sub = c.buf[y0:y + h, x0:x + w]
    if not sub.size:
        return
    mixed = (sub * (1 - k) + np.array(col, dtype=np.float32) * k).astype(np.uint8)
    if only_dark:
        m = sub.max(axis=2) < 24
        sub[m] = mixed[m]
    else:
        sub[:] = mixed


def blend_outline(c, x, y, w, h, col, k):
    blend(c, x, y, w, 1, col, k)
    blend(c, x, y + h - 1, w, 1, col, k)
    blend(c, x, y + 1, 1, h - 2, col, k)
    blend(c, x + w - 1, y + 1, 1, h - 2, col, k)


def signatures(data, view):
    """What a change looks like, per tile / key race: id -> (phase, % in, shares, winner)."""
    office = (data or {}).get(view) or {}
    out = {}
    for k, r in (office.get("states") or {}).items():
        if view == "house":
            out[k] = (r.get("ph"), r.get("pin"), tuple(sorted((r.get("won") or {}).items())),
                      tuple(sorted((r.get("lead") or {}).items())), None)
        else:
            out[k] = (r.get("ph"), r.get("pin"), tuple(round(c[1]) for c in r.get("c") or []), r.get("w"))
    for k, r in (office.get("races") or {}).items():
        out[k] = (r.get("ph"), r.get("pin"), tuple(round(c[1]) for c in r.get("c") or []), r.get("w"))
    return out


STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "D.C.", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky",
    "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
OFFICE_NAMES = {"senate": "Senate", "house": "House", "governor": "Governor"}
TICKER_PX_PER_SEC = 45.0
TICKER_BG = (0, 0, 40)


def race_of(data, view, k):
    office = (data or {}).get(view) or {}
    return (office.get("races") or {}).get(k) or (office.get("states") or {}).get(k)


def call_news(data, view, k):
    """[(text, colour), ...] for a race just called."""
    r = race_of(data, view, k) or {}
    w = r.get("w")
    st = k.split("-")[0]
    where = f"{STATE_NAMES.get(st, st)} {OFFICE_NAMES[view]}" if "-" not in k else f"{k} House"
    win = next((c for c in r.get("c") or [] if c[3]), None)
    segs = [("CALLED", YEL), ("  " + where + "  ", WHITE)]
    if win:
        segs.append((f"{win[2]} ({win[0]})", PARTY.get(w, IND)))
    elif w:
        segs.append(({"D": "Democrat", "R": "Republican"}.get(w, "Independent"), PARTY.get(w, IND)))
    d, rr = share(r, "D"), share(r, "R")
    if (r.get("pin") or 0) >= 1 and d is not None and rr is not None:
        segs.append((f"  {round(d)}-{round(rr)} with {round(r['pin'])}% in", GREY))
    elif (r.get("pin") or 0) < 1:
        segs.append(("  projected at poll close", GREY))
    return segs


def share(race, party):
    return next((c[1] for c in race.get("c") or [] if c[0] == party), None)


def _zone(buf):
    """The screen's zone of the wall's canvas as a canvas of its own (a numpy view), for overlays."""
    from display.gfx import Canvas
    z = Canvas.__new__(Canvas)
    z.buf, z.height, z.width = buf, buf.shape[0], buf.shape[1]
    return z


class ElectionScreen:
    def __init__(self, cfg_mod: dict, loader):
        self.cfg = cfg_mod or {}
        self.loader = loader
        self.view_i = 0
        self.view_t = None
        self._cache_key = None
        self._frame = None
        # live feedback
        self._seen = None                      # updated_utc of the data last compared
        self._sigs = {}                        # view -> {id: signature}
        self._flash = {v: {} for v in VIEWS}   # view -> {id: [kind, t0 | None (starts when the view shows)]}
        self._seat_flash = {}                  # office -> t0
        self._bop = {}
        self._banners = deque(maxlen=8)        # new calls: (what, who, party colour)
        self._banner = None                    # the one on screen: (what, who, colour, t0)
        self._data_t = None                    # when the last fetch landed
        self._row_pos, self._seat_pos = {}, {}
        self._src_end, self._src_y, self._live_x = 0, 0, 0
        self._news = deque(maxlen=24)          # status-strip lines waiting: [(text, colour), ...]
        self._tick = None                      # the line scrolling: (strip pixels, t0)
        self._data = None
        self.f_ab = font("alert/5x7.bdf")
        self.f_t = font("alert/tom-thumb.bdf")
        self.f_title = font("h13")
        self.f_num = font("m13b")

    # -- rotation -------------------------------------------------------------------------------------------------
    def draw(self, c, zone, entry, transition, now=None):
        now = time.time() if now is None else now
        entry = entry or {}
        mode = entry.get("election_view") or self.cfg.get("view") or "cycle"
        cycle = float(entry.get("cycle_sec") or self.cfg.get("cycle_sec") or DEFAULT_CYCLE_SEC)
        result = None
        if transition or self.view_t is None:
            self.view_i, self.view_t = 0, now
        elif now - self.view_t >= cycle:
            self.view_t = now
            if mode == "cycle":
                self.view_i = (self.view_i + 1) % len(VIEWS)
                result = "done" if self.view_i == 0 else None
            else:
                result = "done"
        view = VIEWS[self.view_i] if mode == "cycle" else (mode if mode in VIEWS else "senate")
        data = self.loader.get_data("election") if self.loader else None
        x, y, w, h = zone
        fresh = (data or {}).get("map_utc") or (data or {}).get("updated_utc") or now
        stale = bool(data) and now - float(fresh) > 600
        key = (id(data), (data or {}).get("updated_utc"), view, mode == "cycle", stale, int(now // 60))
        if key != self._cache_key or self._frame is None:
            from display.gfx import Canvas
            cv = Canvas(W, H)
            self.render(cv, data, view, mode == "cycle", stale)
            self._frame, self._cache_key = cv.buf, key
        hh, ww = min(h, H), min(w, W)
        c.buf[y:y + hh, x:x + ww] = self._frame[:hh, :ww]
        if data and not data.get("disabled") and data.get(view):
            self.track(data, now)
            self.overlay(_zone(c.buf[y:y + hh, x:x + ww]), view, now, stale)
        return result

    # -- live feedback ----------------------------------------------------------------------------------------------
    def track(self, data, now):
        """Compare a new fetch with the last one: what changed flashes, new calls go to the banner."""
        stamp = data.get("updated_utc")
        if stamp == self._seen:
            return
        first = self._seen is None
        self._seen, self._data_t, self._data = stamp, now, data
        closed = []
        for v in VIEWS:
            new = signatures(data, v)
            old = self._sigs.get(v)
            self._sigs[v] = new
            if first or old is None:
                continue
            for k, sig in new.items():
                was = old.get(k)
                if was is None or was == sig:
                    continue
                if sig[3] and sig[3] != was[3]:
                    kind = "call"
                    who = {"D": "DEM", "R": "REP"}.get(sig[3], "IND")
                    self._banners.append((f"{k} {SHORT[v]}", who, PARTY.get(sig[3], IND)))
                    self._news.append(call_news(data, v, k))
                elif was[0] == "open" and sig[0] != "open":
                    kind = "close"
                    if "-" not in k and k not in closed:
                        closed.append(k)
                else:
                    kind = "update"
                cur = self._flash[v].get(k)
                if not (cur and cur[0] == "call" and kind != "call"):     # a call isn't cut short by an update
                    self._flash[v][k] = [kind, None]
        if closed:
            self._news.append([("POLLS CLOSED", YEL), ("  " + ", ".join(STATE_NAMES.get(k, k) for k in sorted(closed)), WHITE)])
        for office, b in (data.get("bop") or {}).items():
            sig = (b.get("d"), b.get("r"), b.get("i"))
            if not first and self._bop.get(office) not in (None, sig):
                self._seat_flash[office] = now
                label = {"senate": "SENATE", "house": "HOUSE", "governor": "GOVERNORS"}[office] if office in OFFICE_NAMES else office
                segs = [(label, YEL), (f"  D {b.get('d')}", DEM), (f"  R {b.get('r')}", REP)]
                if b.get("i"):
                    segs.append((f"  I {b['i']}", IND))
                need = b.get("need")
                if need and not b.get("called_only"):
                    for p, n in (("D", b.get("d") or 0), ("R", b.get("r") or 0)):
                        if n >= need:
                            segs.append((f"  {'Democrats' if p == 'D' else 'Republicans'} win control", PARTY[p]))
                    segs.append((f"  ({need} for majority)", GREY))
                elif b.get("called_only"):
                    segs.append((f"  of {b.get('total')} called", GREY))
                self._news.append(segs)
            self._bop[office] = sig

    # -- the status strip ----------------------------------------------------------------------------------------
    def ticker_active(self):
        return self._tick is not None or bool(self._news)

    def draw_ticker(self, c, x, y, w, h, now=None):
        """Scroll the waiting news lines through the status strip, one after another; False when none is left."""
        now = time.time() if now is None else now
        if self._tick is None:
            if not self._news:
                return False
            self._tick = (self._strip(self._news.popleft(), h), now)
        strip, t0 = self._tick
        off = int((now - t0) * TICKER_PX_PER_SEC)
        sw = strip.shape[1]
        if off > sw + w:                       # scrolled right through: next line, or back to the status text
            self._tick = None
            return self.draw_ticker(c, x, y, w, h, now) if self._news else False
        c.buf[y:y + h, x:x + w] = TICKER_BG
        # the strip enters at the right edge and leaves at the left
        sx = x + w - off
        a0, a1 = max(0, x - sx), min(sw, x + w - sx)
        if a1 > a0:
            c.buf[y:y + h, sx + a0:sx + a1] = strip[:h, a0:a1]
        return True

    def _strip(self, segs, h):
        from display.gfx import Canvas
        f = font("m13b")
        width = sum(tw(f, t) for t, _ in segs) + 12
        cv = Canvas(width, h)
        cv.buf[:] = TICKER_BG
        xx = 6
        for t, col in segs:
            xx += text(cv, f, xx, max(0, (h - f.h) // 2), col, t)
        return cv.buf

    def overlay(self, c, view, now, stale):
        # changed tiles and key-race rows of the office on screen
        flashes = self._flash[view]
        rows = self._row_pos.get(view) or {}
        for k in list(flashes):
            kind, t0 = flashes[k]
            if t0 is None:
                t0 = flashes[k][1] = now
            age = now - t0
            dur = FLASH_SEC[kind]
            if age > dur:
                del flashes[k]
                continue
            fade = 1 - age / dur
            col = YEL if kind == "close" else WHITE
            if k in GRID:
                row, colm = GRID[k]
                tx, ty = OX + colm * TW, OY + row * TH
                if kind == "call" and age < STROBE_SEC:
                    on = int(age / 0.2) % 2 == 0
                    win = (self._sigs.get(view) or {}).get(k, (None,) * 4)[3]
                    if on:
                        blend(c, tx, ty, TW - 1, TH - 1, PARTY.get(win, IND), 0.85)
                    blend_outline(c, tx - 1, ty - 1, TW + 1, TH + 1, WHITE, 1.0 if on else 0.5)
                else:
                    blend_outline(c, tx, ty, TW - 1, TH - 1, col, fade)
                    blend_outline(c, tx + 1, ty + 1, TW - 3, TH - 3, col, fade * 0.5)
            if k in rows:
                ry = rows[k]
                blend(c, SX - 2, ry - 1, W - SX + 1, 9, col, 0.16 * fade, only_dark=True)
                blend(c, SX - 4, ry - 1, 2, 9, col, fade)
        # seat counts that moved
        for office, t0 in list(self._seat_flash.items()):
            age = now - t0
            if age > FLASH_SEC["update"]:
                del self._seat_flash[office]
                continue
            pos = self._seat_pos.get(office)
            if pos:
                k = 1 - age / FLASH_SEC["update"]
                blend(c, SX - 2, pos[0] - 1, W - SX + 1, pos[1] - pos[0] + 1, WHITE, 0.12 * k, only_dark=True)
                blend(c, SX - 4, pos[0] - 1, 2, pos[1] - pos[0] + 1, WHITE, k)
        # a scan line down the map each time a fetch lands
        if self._data_t is not None and now - self._data_t < SWEEP_SEC:
            sy = int(H * (now - self._data_t) / SWEEP_SEC)
            for i, k in enumerate((0.55, 0.3, 0.15)):
                blend(c, 0, sy - i, SX - 5, 1, (180, 220, 255), k)
        # LIVE dot and "12s ago"
        lx, ly = self._live_x, OY + 3
        if stale:
            text(c, self.f_t, lx, ly, YEL, "STALE")
        else:
            fresh = self._data_t is not None and now - self._data_t < 1.0
            k = 1.0 if fresh else 0.45 + 0.55 * (0.5 + 0.5 * math.sin(now * 2 * math.pi / 1.6))
            fill(c, lx, ly, 5, 5, WHITE if fresh else dim((255, 40, 40), k))
            text(c, self.f_t, lx + 8, ly, (255, 80, 80), "LIVE")
        if self._data_t is not None:
            ago = int(now - self._data_t)
            text(c, self.f_t, self._src_end + 4, self._src_y, (120, 120, 120),
                 "now" if ago < 2 else f"{ago}s ago" if ago < 120 else f"{ago // 60}m ago")
        # CALLED banner, one call at a time
        if self._banner and now - self._banner[3] > BANNER_SEC:
            self._banner = None
        if self._banner is None and self._banners:
            self._banner = (*self._banners.popleft(), now)
        if self._banner:
            what, who, col, t0 = self._banner
            age = now - t0
            bx, by = OX + TW + 3, OY + 34
            if age > 1.0 or int(age / 0.15) % 2 == 0:
                fill(c, bx - 1, by - 1, tw(self.f_t, "CALLED") + 4, 8, YEL)
                text(c, self.f_t, bx + 1, by, (0, 0, 0), "CALLED")
                x2 = bx + tw(self.f_t, "CALLED") + 7
                x2 += text(c, self.f_ab, x2, by - 1, WHITE, what) + 6
                text(c, self.f_ab, x2, by - 1, col, who)
            if self._banners:
                text_r(c, self.f_t, SX - 10, by, GREY, f"+{len(self._banners)}")

    # -- frame ----------------------------------------------------------------------------------------------------
    def render(self, c, data, view, cycling, stale=False):
        if not data or data.get("disabled") or not data.get(view):
            text(c, self.f_title, 8, 8, WHITE, "US ELECTION")
            text(c, self.f_ab, 8, 28, GREY, "US election results are off" if (data or {}).get("disabled")
                 else "Waiting for results...")
            return
        office = data[view]
        states = office.get("states") or {}
        for st, (row, col) in GRID.items():
            x, y = OX + col * TW, OY + row * TH
            if view == "house":
                self.house_tile(c, st, states.get(st), x, y)
            else:
                self.tile(c, st, states.get(st), x, y)
        # title block in the empty top band (row 0 / 1, columns 1 - 8)
        tx = OX + TW + 3
        text(c, self.f_title, tx, OY, WHITE, TITLES[view])
        if cycling:
            vx = tx
            for v in VIEWS:
                text(c, self.f_t, vx, OY + 17, WHITE if v == view else (70, 70, 70), SHORT[v])
                vx += tw(self.f_t, SHORT[v]) + 6
        as_of = data.get("map_utc") or data.get("updated_utc")
        src = f"{data.get('source') or 'AP'} {clock(as_of)}"
        if (data.get("source") or "").startswith("civicAPI"):    # NBC down: the map is from its last update
            src = f"map {clock(as_of)}, seats civicAPI"
            stale = True
        self._src_y = OY + 25 if cycling else OY + 17
        self._src_end = tx + text(c, self.f_t, tx, self._src_y, YEL if stale else (90, 90, 90), src)
        self._live_x = tx + tw(self.f_title, TITLES[view]) + 8
        self.legend(c)
        self.sidebar(c, data, view)

    def tile(self, c, st, r, x, y, w=TW - 1, h=TH - 1):
        f_ab, f_t = self.f_ab, self.f_t
        if r is None:
            fill(c, x, y, w, h, NO_RACE)
            text(c, f_ab, x + 2, y + 1, (55, 55, 55), st)
            return
        ph = r.get("ph")
        if ph == "open":
            fill(c, x, y, w, h, OPEN)
            text(c, f_ab, x + 2, y + 1, (190, 190, 190), st)
            text(c, f_t, x + 2, y + 13, YEL, clock(r.get("close")))
            return
        if ph == "waiting":
            fill(c, x, y, w, h, WAITING)
            text(c, f_ab, x + 2, y + 1, WHITE, st)
            text(c, f_t, x + 2, y + 13, GREY, "0%")
            return
        lead = r.get("w") or r.get("lead") or "I"
        pc = PARTY.get(lead, IND)
        called = ph == "called"
        if called:
            fill(c, x, y, w, h, dim(pc, 0.85))
            check(c, x + w - 9, y + 2, WHITE)
        else:
            fill(c, x, y, w, h, dim(pc, 0.22))
            outline(c, x, y, w, h, dim(pc, 0.6))
        text(c, f_ab, x + 2, y + 1, WHITE, st)
        if called and (r.get("pin") or 0) < 1:      # projected at poll close, no votes yet
            text(c, f_t, x + 2, y + 13, WHITE, {"D": "DEM", "R": "REP"}.get(lead, "IND"))
            return
        d, rr = share(r, "D"), share(r, "R")
        if d is None or rr is None:                  # an independent in a two-way race (Nebraska)
            other = next((k for k in r.get("c") or [] if k[0] not in ("D", "R")), None)
            if d is None and other:
                d = other[1]
        ind = share(r, "D") is None and d is not None      # the left number is an independent's (Nebraska)
        dcol, rcol = (WHITE, WHITE) if called else ((220, 160, 255) if ind else LIGHT_D, LIGHT_R)
        text(c, f_t, x + 2, y + 9, dcol, "--" if d is None else f"{round(d)}")
        text_r(c, f_t, x + w - 2, y + 9, rcol, "--" if rr is None else f"{round(rr)}")
        text(c, f_t, x + 2, y + 15, WHITE if called else GREY, f"{round(r.get('pin') or 0)}%")

    def house_tile(self, c, st, s, x, y, w=TW - 1, h=TH - 1):
        """House: seats won by each party (the bright numbers), the state's tint the side ahead in won + leading."""
        f_ab, f_t = self.f_ab, self.f_t
        if s is None:
            fill(c, x, y, w, h, NO_RACE)
            text(c, f_ab, x + 2, y + 1, (55, 55, 55), st)
            return
        ph = s.get("ph")
        seats = s.get("seats") or 0
        if ph in ("open", "waiting"):
            fill(c, x, y, w, h, OPEN if ph == "open" else WAITING)
            text(c, f_ab, x + 2, y + 1, (190, 190, 190) if ph == "open" else WHITE, st)
            text_r(c, f_t, x + w - 1, y + 2, GREY, str(seats))
            text(c, f_t, x + 2, y + 13, YEL if ph == "open" else GREY, clock(s.get("close")) if ph == "open" else "0%")
            return
        won, lead = s.get("won") or {}, s.get("lead") or {}
        dw, rw = won.get("D", 0), won.get("R", 0)
        ahead = {p: won.get(p, 0) + lead.get(p, 0) for p in ("D", "R", "I")}
        side = max(ahead, key=ahead.get)
        pc = PARTY[side]
        if ph == "called":
            fill(c, x, y, w, h, dim(pc, 0.85) if dw != rw else (70, 70, 70))
        else:
            fill(c, x, y, w, h, dim(pc, 0.22) if ahead["D"] != ahead["R"] else (34, 34, 34))
            outline(c, x, y, w, h, dim(pc, 0.6) if ahead["D"] != ahead["R"] else (70, 70, 70))
        text(c, f_ab, x + 2, y + 1, WHITE, st)
        text_r(c, f_t, x + w - 1, y + 2, WHITE if ph == "called" else GREY, str(seats))
        hot = ph == "called"
        text(c, f_t, x + 2, y + 9, WHITE if hot else LIGHT_D, str(dw))
        text_r(c, f_t, x + w - 2, y + 9, WHITE if hot else LIGHT_R, str(rw))
        text(c, f_t, x + 2, y + 15, WHITE if hot else GREY, f"{round(s.get('pin') or 0)}%")

    def legend(self, c):
        """Bottom-left corner (row 6, columns 0 - 2)."""
        f = self.f_t
        x0, y0 = OX + 2, OY + 6 * TH + 4
        items = ((dim(DEM, 0.85), "called"), (dim(DEM, 0.22), "ahead"), (OPEN, "open"), (WAITING, "0 votes"))
        for i, (col, lab) in enumerate(items):
            x, y = x0 + (i % 2) * 38, y0 + (i // 2) * 8
            fill(c, x, y, 5, 5, col)
            if lab == "ahead":
                outline(c, x, y, 5, 5, dim(DEM, 0.6))
            text(c, f, x + 7, y, GREY, lab)

    def seat_bar(self, c, y, label, b, right_label):
        x, w = SX, W - SX - 3
        f_t, f_n = self.f_t, self.f_num
        text(c, f_t, x, y, WHITE, label)
        text_r(c, f_t, x + w, y, GREY, right_label)
        by = y + 8
        d, r, total = b.get("d", 0), b.get("r", 0), max(1, b.get("total") or 1)
        text(c, f_n, x, by, DEM, str(d))
        text_r(c, f_n, x + w, by, REP, str(r))
        bx, bw = x + 22, w - 44
        yb = by + 4
        fill(c, bx, yb, bw, 5, (35, 35, 35))
        fill(c, bx, yb, round(bw * d / total), 5, DEM)
        rw = round(bw * r / total)
        fill(c, bx + bw - rw, yb, rw, 5, REP)
        if b.get("need") and not b.get("called_only"):
            mx = bx + round(bw * b["need"] / total)
            fill(c, mx, yb - 2, 1, 9, WHITE)
        return by + f_n.h + 2

    def sidebar(self, c, data, view):
        fill(c, SX - 4, 0, 1, H, RULE)
        bop = data.get("bop") or {}
        y = 1
        for v, label in (("senate", "SENATE"), ("house", "HOUSE"), ("governor", "GOVERNORS")):
            b = bop.get(v)
            if not b:
                continue
            right = f"{b['total']} races" if b.get("called_only") else f"{b.get('need')} for majority"
            hl = v == view
            y0 = y
            y = self.seat_bar(c, y, ("> " if hl else "") + label, b, right) + 1
            self._seat_pos[v] = (y0, y - 2)
        fill(c, SX, y, W - SX - 3, 1, RULE)
        y += 3
        office = data.get(view) or {}
        text(c, self.f_t, SX, y, YEL, f"KEY {TITLES[view]} RACES" if view != "governor" else "KEY GOVERNOR RACES")
        y += 8
        if view == "house":
            rows = [(k, (office.get("races") or {}).get(k)) for k in office.get("key") or []]
        else:
            rows = [(k, (office.get("states") or {}).get(k)) for k in office.get("key") or []]
        rows = [(k, r) for k, r in rows if r]
        # finished races drop to the bottom: the close and uncounted ones are the story
        order = {"counting": 0, "waiting": 1, "open": 2, "called": 3}
        rows.sort(key=lambda kr: order.get(kr[1].get("ph"), 4))
        # room left: the closest other races still counting (statewide offices only; districts have no map)
        if view != "house" and len(rows) < (H - y) // 9:
            seen = {k for k, _ in rows}
            close = sorted(((k, r) for k, r in (office.get("states") or {}).items()
                            if k not in seen and r.get("ph") == "counting"),
                           key=lambda kr: abs((share(kr[1], "D") or 0) - (share(kr[1], "R") or 0)))
            rows += close
        pos = self._row_pos[view] = {}
        for k, r in rows:
            if y > H - 8:
                break
            pos[k] = y
            self.key_row(c, y, k, r)
            y += 9

    def key_row(self, c, y, k, r):
        f_ab, f_t = self.f_ab, self.f_t
        x0, x1 = SX, W - 3
        text(c, f_ab, x0, y, WHITE, k)
        bx = x0 + 32
        ph = r.get("ph")
        if ph == "open":
            text(c, f_t, bx, y + 1, GREY, "polls close")
            text_r(c, f_ab, x1, y, YEL, clock(r.get("close")))
            return
        if ph == "waiting":
            text(c, f_t, bx, y + 1, GREY, "no votes yet")
            return
        d, rr = share(r, "D"), share(r, "R")
        left = DEM
        if d is None:
            other = next((k for k in r.get("c") or [] if k[0] not in ("D", "R")), None)
            d = other[1] if other else None
            left = IND
        if ph == "called" and (r.get("pin") or 0) < 1:
            w = r.get("w")
            text(c, f_t, bx, y + 1, PARTY.get(w, IND), {"D": "DEM", "R": "REP"}.get(w, "IND") + " projected")
            check(c, x1 - 7, y + 1, WHITE)
            return
        # D | R bar, then the two shares and % in
        bw = 46
        d0, r0 = d or 0, rr or 0
        tot = max(1e-6, d0 + r0)
        dw = round(bw * d0 / tot)
        fill(c, bx, y + 1, dw, 5, left)
        fill(c, bx + dw, y + 1, bw - dw, 5, REP)
        fill(c, bx + bw // 2, y, 1, 7, WHITE)
        nx = bx + bw + 5
        w = r.get("w")
        text(c, f_t, nx, y + 1, left, "--" if d is None else f"{d:.1f}")
        text(c, f_t, nx + 24, y + 1, REP, "--" if rr is None else f"{rr:.1f}")
        if w:
            check(c, x1 - 7, y + 1, PARTY.get(w, IND))
        else:
            text_r(c, f_t, x1, y + 1, GREY, f"{round(r.get('pin') or 0)}%")
