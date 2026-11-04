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
"""

from __future__ import annotations

import time
from datetime import datetime

from display.modules.bc_kit import font, text, text_r, tw

W, H = 448, 175
VIEWS = ("senate", "house", "governor")
TITLES = {"senate": "SENATE", "house": "HOUSE", "governor": "GOVERNORS"}
SHORT = {"senate": "SEN", "house": "HOUSE", "governor": "GOV"}
DEFAULT_CYCLE_SEC = 20.0

GRID = {
    "AK": (0, 0), "ME": (0, 10),
    "VT": (1, 9), "NH": (1, 10),
    "WA": (2, 0), "ID": (2, 1), "MT": (2, 2), "ND": (2, 3), "MN": (2, 4), "IL": (2, 5), "WI": (2, 6),
    "MI": (2, 7), "NY": (2, 8), "RI": (2, 9), "MA": (2, 10),
    "OR": (3, 0), "NV": (3, 1), "WY": (3, 2), "SD": (3, 3), "IA": (3, 4), "IN": (3, 5), "OH": (3, 6),
    "PA": (3, 7), "NJ": (3, 8), "CT": (3, 9),
    "CA": (4, 0), "UT": (4, 1), "CO": (4, 2), "NE": (4, 3), "MO": (4, 4), "KY": (4, 5), "WV": (4, 6),
    "VA": (4, 7), "MD": (4, 8), "DE": (4, 9),
    "AZ": (5, 1), "NM": (5, 2), "KS": (5, 3), "AR": (5, 4), "TN": (5, 5), "NC": (5, 6), "SC": (5, 7), "DC": (5, 8),
    "OK": (6, 3), "LA": (6, 4), "MS": (6, 5), "AL": (6, 6), "GA": (6, 7),
    "HI": (7, 0), "TX": (7, 3), "FL": (7, 8),
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


def share(race, party):
    return next((c[1] for c in race.get("c") or [] if c[0] == party), None)


class ElectionScreen:
    def __init__(self, cfg_mod: dict, loader):
        self.cfg = cfg_mod or {}
        self.loader = loader
        self.view_i = 0
        self.view_t = None
        self._cache_key = None
        self._frame = None
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
        return result

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
        text(c, self.f_t, tx, OY + 25 if cycling else OY + 17, YEL if stale else (90, 90, 90), src)
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
            y = self.seat_bar(c, y, ("> " if hl else "") + label, b, right) + 1
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
        for k, r in rows:
            if y > H - 8:
                break
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
