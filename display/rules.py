"""Rotation rules: when an entry may come up.

An entry's ``rules`` is a list; all of them must hold. Each rule is ``{"if": <condition>, "not": bool, ...params}``::

    {"if": "calendar_events"}                                 the calendar has events on screen
    {"if": "alert", "min_level": "watch", "hazards": ["rain"], "home": true}
                                                              an ECCC alert is in effect (optionally: at least this
                                                              level, of these weather types, naming the home city)
    {"if": "forecast_warning"}                                the city forecast lists a warning
    {"if": "precip", "within_h": 3, "pop": 40}                rain/snow now (within_h 0) or likely within N hours
    {"if": "dark"}                                            the sun is down (current conditions say night)
    {"if": "night_mode"}                                      inside the dimmed night-mode hours
    {"if": "hours", "from": "07:00", "to": "22:00"}           local time in [from, to) (wraps past midnight)
    {"if": "days", "days": [0, 1, 2, 3, 4]}                   weekday (Mon = 0)
    {"if": "fresh", "max_age_min": 30}                        the entry's own data is no older than this

``not: true`` inverts a rule ("doesn't show if …"). Rules are checked when the rotation picks the next screen,
never mid-screen: a screen that started always plays to its end.

Legacy ``when`` values (v1 config) map onto rules via ``legacy_rules``.
"""

from __future__ import annotations

import time
from datetime import datetime

LEVELS = ("statement", "advisory", "watch", "warning")

LEGACY = {
    "always": [],
    "calendar_has_events": [{"if": "calendar_events"}],
    "no_events": [{"if": "calendar_events", "not": True}],
    "weather_has_alert": [{"if": "forecast_warning"}],
    "weather_alert_active": [{"if": "alert"}],
}

# the data file each panel draws from, for "fresh"
PANEL_DATA = {"calendar": "calendar", "client_calls_upcoming": "calendar", "client_calls_past": "calendar",
              "headlines": "headlines", "weather": "weather", "weather_full": "weather", "weather_alert": "alerts"}

_PRECIP_WORDS = ("rain", "drizzle", "shower", "snow", "flurr", "sleet", "hail", "pellet", "thunder")


def legacy_rules(raw: dict) -> list:
    """Rules for an entry, converting v1-style ``when`` / ``allowed_days`` / ``allowed_hours``."""
    if isinstance(raw.get("rules"), list):
        return [r for r in raw["rules"] if isinstance(r, dict) and r.get("if")]
    out = [dict(r) for r in LEGACY.get((raw.get("when") or "always").strip(), [])]
    if raw.get("allowed_days"):
        out.append({"if": "days", "days": list(raw["allowed_days"])})
    ah = raw.get("allowed_hours")
    if isinstance(ah, dict) and ah.get("from") and ah.get("to"):
        out.append({"if": "hours", "from": ah["from"], "to": ah["to"]})
    return out


def _hm(s, default):
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return default


def _in_window(start, end, now_min):
    return start <= now_min < end if start < end else (now_min >= start or now_min < end)


class Env:
    """What the rules look at, gathered lazily and cached briefly (rules run a few times a second at most)."""

    def __init__(self, screen):
        self.s = screen
        self._cache = {}

    def _get(self, key, fn, ttl=1.0):
        hit = self._cache.get(key)
        now = time.time()
        if hit and now - hit[0] < ttl:
            return hit[1]
        v = fn()
        self._cache[key] = (now, v)
        return v

    # -- facts -------------------------------------------------------------------
    def calendar_events(self):
        return bool(getattr(self.s.calendar, "has_visible_events", True))

    def alerts(self):
        def build():
            from display.modules import alert_brief
            out = []
            for a in self.s.alert_screen.alerts():
                out.append({"level": a.get("type") or "statement", "hazard": alert_brief.hazard_of(a),
                            "home": alert_brief.mentions(a, self.s.alert_screen.home)})
            return out
        return self._get("alerts", build, 5.0)

    def weather(self):
        return (self.s.loader.get_data("weather") if self.s.loader else None) or {}

    def data_age(self, entry):
        """Seconds since the entry's data was written (None if it has none)."""
        panel = entry.get("panel", "")
        if panel.startswith("map"):
            maps = getattr(self.s, "maps", None)
            mt = (getattr(maps, "_mtimes", {}) or {}).get(entry.get("track")) if maps else None
            return time.time() - max(mt) if mt else None
        name = PANEL_DATA.get(panel)
        mt = self.s.loader.get_mtime(name) if (name and self.s.loader) else None
        return time.time() - mt if mt else None

    # -- evaluation ------------------------------------------------------------------
    def check(self, rule: dict, entry: dict) -> bool:
        try:
            ok = self._check(rule, entry)
        except Exception as e:                      # a bad rule never blanks the wall
            print(f"[rules] {rule}: {e}")
            ok = True
        return (not ok) if rule.get("not") else ok

    def _check(self, r, entry):
        cond = r.get("if")
        if cond == "calendar_events":
            return self.calendar_events()
        if cond == "alert":
            lo = LEVELS.index(r["min_level"]) if r.get("min_level") in LEVELS else 0
            hz = set(r.get("hazards") or [])
            return any((LEVELS.index(a["level"]) if a["level"] in LEVELS else 0) >= lo
                       and (not hz or a["hazard"] in hz) and (a["home"] or not r.get("home"))
                       for a in self.alerts())
        if cond == "forecast_warning":
            w = self.weather()
            return bool(w.get("alerts") or w.get("warnings"))
        if cond == "precip":
            w = self.weather()
            within = float(r.get("within_h") or 0)
            cur = (w.get("current") or {})
            if any(k in f"{cur.get('weather_text', '')} {cur.get('weather_long', '')}".lower() for k in _PRECIP_WORDS):
                return True
            if within <= 0:
                return False
            pop = float(r.get("pop") or 40)
            return any((h.get("pop_pct") or 0) >= pop for h in (w.get("hourly") or [])[:max(1, int(round(within)))])
        if cond == "dark":
            return bool((self.weather().get("current") or {}).get("is_night"))
        if cond == "night_mode":
            nm = self.s.cfg.system.get("night_mode", {}) or {}
            if not nm.get("enabled"):
                return False
            now = datetime.now()
            return _in_window(_hm(nm.get("start_time"), 22 * 60), _hm(nm.get("end_time"), 6 * 60), now.hour * 60 + now.minute)
        if cond == "hours":
            now = datetime.now()
            return _in_window(_hm(r.get("from"), 0), _hm(r.get("to"), 24 * 60), now.hour * 60 + now.minute)
        if cond == "days":
            return datetime.now().weekday() in [int(d) for d in (r.get("days") or [])]
        if cond == "fresh":
            age = self.data_age(entry)
            return age is not None and age <= float(r.get("max_age_min") or 30) * 60
        return True

    def allows(self, entry: dict) -> bool:
        return all(self.check(r, entry) for r in entry.get("rules") or [])
