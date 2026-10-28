"""The rotation for one side of the wall.

Entries (``modules.layout_cycle.<side>``)::

    {"panel": "map", "track": "precip_radar",          what to show (plus panel fields: track, weather_screen, …)
     "rules": [{"if": "precip", "within_h": 3}],       when it may come up (display/rules.py); all must hold
     "advance": "cycles", "cycles": 2, "max_sec": 600, ends after N complete passes (capped), or
     "dwell_sec": 30,                                  … after this many seconds on screen
     "priority": "next",                               when its rules become true, it plays next (after the
                                                       current screen), then the rotation carries on where it was
     "variants": [{"panel": "webcam",                  plays as another panel (shape) while these rules hold:
                   "rules": [{"if": "hours", …}],      the first variant whose rules hold wins, else ``panel``;
                   "max_sec": 300}],                   any other field overrides the entry's while it plays so
     "enabled": false}                                 kept in the list but never shown

How it plays:
  * Rules are checked when the next screen is picked. A screen that started plays to its end, even if its
    rules stop holding meanwhile.
  * Time on screen only counts while the side is visible (a full-width or full-wall screen from the other side
    pauses this side rather than letting its screens run out unseen).
  * A preview (``play``) shows any entry now. Played once, it runs by the entry's own rules and then the rotation
    resumes: the screen it interrupted starts again. With ``hold`` it repeats until stopped or ``ttl_sec`` passes.
  * A jump (``goto``) plays an entry now as part of the rotation (even if its rules don't hold): the rotation then
    carries on with the entry after it.
  * Config reloads keep the position and the screen that's playing.
  * A panel that can't show its entry (a webcam that won't come up) returns "skip": the screen ends at once.
"""

from __future__ import annotations

import threading
import time

from display.rules import legacy_rules

DEFAULT_DWELL = 30.0
WARM_AHEAD = 20.0          # this close to the end of a screen, the next one may get its source ready (webcams)
DEFAULT_CAP = 1800.0


def entry_key(e: dict):
    b = e.get("_base") if isinstance(e.get("_base"), dict) else e          # a shaped play: the entry it came from
    return (b.get("panel"), b.get("track"), b.get("weather_screen"), b.get("cam"))


_NOT_OVERRIDDEN = ("panel", "rules", "variants", "enabled", "priority", "cam", "track", "weather_screen")


def variants_of(e: dict) -> list:
    """An entry's other shapes, cleaned: [{"panel", "rules", …overrides}] (anything malformed is dropped)."""
    raw = e.get("variants")
    out = []
    for v in raw if isinstance(raw, list) else []:
        if isinstance(v, dict) and isinstance(v.get("panel"), str) and v["panel"].strip():
            rules = v.get("rules")
            extra = {k: x for k, x in v.items() if k not in _NOT_OVERRIDDEN and not str(k).startswith("_")}
            out.append({**extra, "panel": v["panel"].strip(),
                        "rules": [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []})
    return out


def normalise(raw) -> dict | None:
    if not isinstance(raw, dict) or not (raw.get("panel") or "").strip():
        return None
    e = {k: v for k, v in raw.items()
         if k not in ("when", "allowed_days", "allowed_hours") and not str(k).startswith("_")}   # _play, _base…
    e["panel"] = raw["panel"].strip()
    e["rules"] = legacy_rules(raw)
    if e["panel"] in ("weather_alert", "naad_alert", "road_alert", "quake_alert", "tsunami_alert", "fire_alert"):
        e.setdefault("priority", "next")      # an alert that starts plays next ("priority": "" turns that off)
    return e


def _shape_key(e):
    """An entry at the size it plays: each size of a screen keeps its own pass length."""
    return entry_key(e), e.get("panel")


def _as_variant(entry, v):
    """The entry played as variant v: v's panel and overrides; ``_base`` keeps the entry as configured."""
    return {**entry, **{k: x for k, x in v.items() if k != "rules"}, "_shape_of": entry["panel"], "_base": entry}


def _same_size(entry, was):
    """After a config reload: the new settings for a play already on screen, at the size it started with."""
    if not was.get("_shape_of"):
        return entry
    v = next((v for v in variants_of(entry) if v["panel"] == was["panel"]), None)
    return _as_variant(entry, v or {"panel": was["panel"]})         # variant since removed: keep its size till it ends


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
        self._prio_tok: dict = {}            # key -> the new-alert token last check (see Env.priority_token)
        self._full_keys: set = set()         # keys queued by a new alert: they play their own (full) size
        self._pass_len: dict = {}            # (key, size) -> seconds its last real pass took (upcoming: when it ends)
        self._prio_t = 0.0
        self._idle_t = 0.0
        self._last_t = None
        self._visible = True
        self._plays = 0
        self._lock = threading.RLock()

    # -- picking ----------------------------------------------------------------------
    def _allows(self, e):
        return e.get("enabled", True) is not False and (self.env is None or self.env.allows(e))

    def _shaped(self, entry):
        """The entry as it plays now: the first variant whose rules hold gives the panel (e.g. half width during
        business hours), else its own. Decided when a screen starts, like every rule."""
        if isinstance(entry.get("_base"), dict):          # restarting a screen (after a preview): decide afresh
            entry = entry["_base"]
        for v in variants_of(entry):
            try:
                ok = self.env is None or self.env.allows({**entry, "rules": v["rules"]})
            except Exception as ex:
                print(f"[rotation] variant rules: {ex}")
                ok = False
            if ok:
                return _as_variant(entry, v)
        return entry

    def _start(self, entry, idx, source, now, own_size=False):
        if own_size:                                     # a new alert's jump: full size whatever the variants say
            entry = entry["_base"] if isinstance(entry.get("_base"), dict) else entry
        else:
            entry = self._shaped(entry)
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
            cur_e = self.cur["entry"] if self.cur else None
            playing = bool(cur_e and entry_key(cur_e) == k)
            if ok and not self._prio_seen.get(k) and i not in self.pending and not playing \
                    and not self._shaped(e).get("_shape_of"):        # jumps are for its own (full) size
                self.pending.append(i)
            self._prio_seen[k] = ok
            # a new alert (never shown in full) also jumps the queue when the entry was already eligible
            tok = None
            if ok and self.env is not None and hasattr(self.env, "priority_token"):
                try:
                    tok = self.env.priority_token(e)
                except Exception as ex:
                    print(f"[rotation] priority token: {ex}")
            if tok and tok != self._prio_tok.get(k):
                if playing:
                    continue                 # keep the old token: it triggers again once this pass ends
                if i not in self.pending:
                    self.pending.append(i)
                self._full_keys.add(k)       # a new alert: full size, even if the alert list hasn't caught up yet
            self._prio_tok[k] = tok

    def _pick(self, now):
        while self.pending:
            i = self.pending.pop(0)
            if i < len(self.entries) and self._allows(self.entries[i]):
                k = entry_key(self.entries[i])
                self._start(self.entries[i], i, "priority", now, own_size=k in self._full_keys)
                self._full_keys.discard(k)
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

    def upcoming(self, now: float, ahead: float = WARM_AHEAD):
        """The entry that should play after the current screen, once that screen is near its end (under ``ahead``
        seconds left, by its time limit or, for one that runs by passes, by how long its passes have taken); else
        None. For getting the next screen's source ready (a webcam's feed)."""
        with self._lock:
            c = self.cur
            if c is None or self.preview:
                return None
            e = c["entry"]
            left = self._limit(e) - c["visible"]
            if e.get("advance") == "cycles" and c["passes"] >= 1:     # passes so far tell how long the rest takes
                done_at = c.get("pass_at", c["visible"])
                left = min(left, done_at / c["passes"] * (self.need(e) - c["passes"]) - (c["visible"] - done_at))
            elif e.get("advance") == "cycles" and _shape_key(e) in self._pass_len:  # first pass: as long as last time
                left = min(left, self._pass_len[_shape_key(e)] * self.need(e) - c["visible"])
            if left > ahead:
                return None
            for i in self.pending:
                if i < len(self.entries) and self._allows(self.entries[i]):
                    return self.entries[i]
            n = len(self.entries)
            for step in range(1, n + 1):
                i = (self.pos + step) % n
                if self._allows(self.entries[i]):
                    return self.entries[i]
            return None

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
                    took = c["visible"] - c.get("pass_at", 0.0)
                    c["passes"] += 1
                    c["pass_at"] = c["visible"]      # screen time when that pass ended (upcoming estimates from it)
                    if took >= 1.0:                  # remembered for next time ("nothing to show" passes aren't)
                        self._pass_len[_shape_key(c["entry"])] = took
            if visible and result == "skip":
                if self.preview and c["source"] == "preview":
                    self._end_preview(now)
                else:
                    self.cur = None
                return
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

    def goto(self, index, entry=None, now=None) -> int:
        """Jump the rotation to an entry: it plays now (once, even if its rules don't hold or it's switched off) and
        the rotation carries on after it. ``entry`` (as the panel last saw it) guards against a list edited since:
        the entry at ``index`` must match it, else the nearest one that does is used. Returns the index played."""
        now = now or time.time()
        with self._lock:
            n = len(self.entries)
            keys = [entry_key(e) for e in self.entries]
            try:
                i = int(index)
            except (TypeError, ValueError):
                i = -1
            if isinstance(entry, dict) and entry.get("panel"):
                want = entry_key(normalise(entry) or entry)
                if not (0 <= i < n and keys[i] == want):
                    found = [j for j, k in enumerate(keys) if k == want]
                    if not found:
                        raise LookupError("that entry isn't in the rotation (save the config first?)")
                    i = min(found, key=lambda j: abs(j - i))
            if not 0 <= i < n:
                raise LookupError(f"no entry {index} in the {self.side} rotation")
            self.preview = None
            self.pending = [j for j in self.pending if j != i]
            self.pos = i
            self._idle_t = 0.0
            self._start(self.entries[i], i, "rotation", now)
            return i

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
                        c["entry"] = _same_size(new[c["idx"]], c["entry"])   # new settings, same size
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
