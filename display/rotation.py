"""The rotation for one side of the wall.

Entries (``modules.layout_cycle.<side>``)::

    {"panel": "map", "track": "precip_radar",          what to show (plus panel fields: track, weather_screen, …)
     "rules": [{"if": "precip", "within_h": 3}],       when it may come up (display/rules.py); all must hold
     "advance": "cycles", "cycles": 2, "max_sec": 600, ends after N complete passes (capped), or
     "dwell_sec": 30,                                  … after this many seconds on screen
     "priority": "next",                               when its rules become true, it plays next (after the
                                                       current screen), then the rotation carries on where it was
     "enabled": false}                                 kept in the list but never shown

How it plays:
  * Rules are checked when the next screen is picked. A screen that started plays to its end, even if its
    rules stop holding meanwhile.
  * Time on screen only counts while the side is visible (a full-width or full-wall screen from the other side
    pauses this side rather than letting its screens run out unseen).
  * A preview (``play``) shows any entry now. Played once, it runs by the entry's own rules and then the rotation
    resumes: the screen it interrupted starts again. With ``hold`` it repeats until stopped or ``ttl_sec`` passes.
  * Config reloads keep the position and the screen that's playing.
"""

from __future__ import annotations

import threading
import time

from display.rules import legacy_rules

DEFAULT_DWELL = 30.0
DEFAULT_CAP = 1800.0


def entry_key(e: dict):
    return (e.get("panel"), e.get("track"), e.get("weather_screen"))


def normalise(raw) -> dict | None:
    if not isinstance(raw, dict) or not (raw.get("panel") or "").strip():
        return None
    e = {k: v for k, v in raw.items() if k not in ("when", "allowed_days", "allowed_hours")}
    e["panel"] = raw["panel"].strip()
    e["rules"] = legacy_rules(raw)
    if e["panel"] == "weather_alert":
        e.setdefault("priority", "next")      # an alert that starts plays next ("priority": "" turns that off)
    return e


class Rotation:
    def __init__(self, side: str, entries, env=None, passes_for=None):
        self.side = side
        self.env = env                       # display.rules.Env (None: every rule holds)
        self.passes_for = passes_for         # entry -> default passes (a map's own loop count)
        self.entries: list[dict] = [e for e in map(normalise, entries or []) if e]
        self.pos = -1                        # last entry picked in order (priority jumps and previews don't move it)
        self.cur = None                      # the play on screen: {entry, idx, source, id, visible, passes, started}
        self.preview = None                  # {hold, until, resume}
        self.pending: list[int] = []         # priority entries waiting to play next
        self._prio_seen: dict = {}           # key -> rules held last check
        self._prio_t = 0.0
        self._idle_t = 0.0
        self._last_t = None
        self._visible = True
        self._plays = 0
        self._lock = threading.RLock()

    # -- picking ----------------------------------------------------------------------
    def _allows(self, e):
        return e.get("enabled", True) is not False and (self.env is None or self.env.allows(e))

    def _start(self, entry, idx, source, now):
        self._plays += 1
        self.cur = {"entry": entry, "idx": idx, "source": source, "id": self._plays,
                    "visible": 0.0, "passes": 0, "started": now}

    def _watch_priority(self, now):
        if now - self._prio_t < 1.0:
            return
        self._prio_t = now
        for i, e in enumerate(self.entries):
            if e.get("priority") != "next":
                continue
            k = entry_key(e)
            ok = self._allows(e)
            if ok and not self._prio_seen.get(k) and i not in self.pending:
                cur_e = self.cur["entry"] if self.cur else None
                if not (cur_e and entry_key(cur_e) == k):
                    self.pending.append(i)
            self._prio_seen[k] = ok

    def _pick(self, now):
        while self.pending:
            i = self.pending.pop(0)
            if i < len(self.entries) and self._allows(self.entries[i]):
                self._start(self.entries[i], i, "priority", now)
                return
        if now < self._idle_t:
            return
        n = len(self.entries)
        for step in range(1, n + 1):
            i = (self.pos + step) % n
            if self._allows(self.entries[i]):
                self.pos = i
                self._start(self.entries[i], i, "rotation", now)
                return
        self._idle_t = now + 1.0             # nothing may show: look again in a second

    def current(self, now: float) -> dict | None:
        """The entry to draw now (with ``_play``: an id that changes whenever a new play starts)."""
        with self._lock:
            if self.preview and self.preview["until"] and now >= self.preview["until"]:
                self._end_preview(now)
            self._watch_priority(now)
            if self.cur is None:
                self._pick(now)
            if self.cur is None:
                return None
            return {**self.cur["entry"], "_play": self.cur["id"]}

    # -- progress ------------------------------------------------------------------------
    def need(self, e) -> int:
        n = e.get("cycles")
        if n is None and self.passes_for:
            n = self.passes_for(e)
        try:
            return max(1, int(n or 1))
        except (TypeError, ValueError):
            return 1

    def _limit(self, e):
        key = "max_sec" if e.get("advance") == "cycles" else "dwell_sec"
        try:
            v = float(e.get(key) or 0)
        except (TypeError, ValueError):
            v = 0
        return v if v > 0 else (DEFAULT_CAP if key == "max_sec" else DEFAULT_DWELL)

    def _done(self, c):
        e = c["entry"]
        if c["visible"] >= self._limit(e):
            return True
        return e.get("advance") == "cycles" and c["passes"] >= self.need(e)

    def report(self, result, visible: bool, now: float):
        """After drawing: ``result`` is the panel's return ("done" = finished one pass)."""
        with self._lock:
            dt = 0.0 if self._last_t is None else max(0.0, min(0.5, now - self._last_t))
            self._last_t = now
            self._visible = visible
            c = self.cur
            if c is None:
                return
            if visible:
                c["visible"] += dt
                if result == "done":
                    c["passes"] += 1
            if self.preview and c["source"] == "preview":
                if not self.preview["hold"] and self._done(c):
                    self._end_preview(now)
            elif self._done(c):
                self.cur = None

    # -- previews and controls ----------------------------------------------------------------
    def play(self, entry: dict, hold=False, ttl_sec=None, now=None):
        now = now or time.time()
        e = normalise(entry)
        if not e:
            raise ValueError("entry needs a panel")
        with self._lock:
            resume = self.preview["resume"] if self.preview else (
                self.cur if self.cur and self.cur["source"] != "preview" else None)
            self.preview = {"hold": bool(hold), "until": (now + float(ttl_sec)) if ttl_sec else 0.0, "resume": resume}
            self._start(e, None, "preview", now)

    def _end_preview(self, now):
        pv, self.preview = self.preview, None
        self.cur = None
        r = pv and pv["resume"]
        if r is not None:                    # restart the screen the preview cut into
            self._start(r["entry"], r["idx"], r["source"], now)

    def stop(self):
        with self._lock:
            if self.preview:
                self._end_preview(time.time())

    def hold(self, on: bool, ttl_sec=3600):
        """Hold: keep what's on screen (a rotation screen becomes a held preview). Release: let it finish."""
        with self._lock:
            now = time.time()
            if self.preview:
                self.preview["hold"] = bool(on)
                if on and ttl_sec:
                    self.preview["until"] = now + float(ttl_sec)
            elif on and self.cur:
                self.cur["source"] = "preview"
                self.preview = {"hold": True, "until": now + float(ttl_sec) if ttl_sec else 0.0, "resume": None}

    def next(self):
        with self._lock:
            if self.preview:
                self._end_preview(time.time())
                if self.cur is not None and self.cur["source"] != "preview":
                    self.cur = None          # skip the resumed screen too: Next means the next one
            else:
                self.cur = None
            self._idle_t = 0.0

    def prev(self):
        with self._lock:
            now = time.time()
            self.preview = None
            n = len(self.entries)
            base = self.cur["idx"] if (self.cur and self.cur["idx"] is not None) else self.pos
            for step in range(1, n + 1):
                i = (base - step) % n
                if self._allows(self.entries[i]):
                    self.pos = i
                    self._start(self.entries[i], i, "rotation", now)
                    return
            self.cur = None

    def skip_unavailable(self):
        """The panel can't be drawn here (not built yet): move on without counting it as shown."""
        with self._lock:
            if self.preview and self.cur and self.cur["source"] == "preview":
                self._end_preview(time.time())
            else:
                self.cur = None
                self._idle_t = 0.0

    # -- config ---------------------------------------------------------------------------
    def reload(self, entries):
        new = [e for e in map(normalise, entries or []) if e]
        with self._lock:
            keys = [entry_key(e) for e in new]
            old_pos = self.entries[self.pos] if 0 <= self.pos < len(self.entries) else None
            self.pos = keys.index(entry_key(old_pos)) if old_pos and entry_key(old_pos) in keys else min(self.pos, len(new) - 1)
            for c in (self.cur, (self.preview or {}).get("resume")):
                if c and c["source"] != "preview":
                    k = entry_key(c["entry"])
                    if k in keys:
                        c["idx"] = keys.index(k)
                        c["entry"] = new[c["idx"]]          # new settings (cycles, dwell…) apply to the play
                    elif c is self.cur:
                        self.cur = None
                    else:
                        self.preview["resume"] = None
            self.pending = [keys.index(entry_key(self.entries[i])) for i in self.pending
                            if i < len(self.entries) and entry_key(self.entries[i]) in keys]
            self.entries = new
            self._idle_t = 0.0

    # -- status ------------------------------------------------------------------------------
    def describe(self, now=None) -> dict:
        now = now or time.time()
        with self._lock:
            c, pv = self.cur, self.preview
            out = {"side": self.side, "cycle": [dict(e) for e in self.entries], "pos": self.pos, "pending": list(self.pending),
                   "visible": self._visible}
            try:
                out["eligible"] = [self._allows(e) for e in self.entries]
            except Exception:
                out["eligible"] = None
            if c:
                e = c["entry"]
                out["playing"] = {"entry": dict(e), "idx": c["idx"], "source": c["source"],
                                  "elapsed": round(c["visible"], 1), "passes": c["passes"],
                                  "need": self.need(e) if e.get("advance") == "cycles" else None,
                                  "limit": self._limit(e)}
            out["current_idx"] = c["idx"] if c and c["source"] != "preview" else None
            if pv:
                out["preview"] = {"hold": pv["hold"], "remaining_sec": max(0.0, pv["until"] - now) if pv["until"] else None,
                                  "resumes": dict(pv["resume"]["entry"]) if pv["resume"] else None}
                if c:      # older pages read "override"
                    out["override"] = {"panel": c["entry"]["panel"], "remaining_sec": out["preview"]["remaining_sec"]}
            else:
                out["override"] = None
            return out
