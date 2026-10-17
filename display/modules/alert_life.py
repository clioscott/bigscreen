"""The life of an alert on the wall, shared by every alert screen (ECCC weather, Alert Ready, DriveBC, earthquakes,
tsunami, wildfire).

    new   received, not yet shown in full: its screen jumps the queue (rotation priority) and plays every page
    full  shown in full fewer than ``full_showings`` times and for less than ``full_minutes``: the full-wall screen
          comes round in the rotation like any other screen (main page only)
    half  after that, while still in effect: the half-width version in the right zone, so the ticker and clock stay

Settings (config ``modules.alert_lifecycle``): ``full_showings`` (3), ``full_minutes`` (30), ``half`` (true; false
keeps every alert full-wall for as long as it lasts).

What has been shown is kept in tmpfs (/dev/shm/bigscreen-state/alert_life.json) so a display restart doesn't
replay everything as new; entries for alerts gone for a day are dropped.
"""

from __future__ import annotations

import json
import os
import threading
import time

PATH = os.environ.get("BIGSCREEN_LIFE_FILE", "/dev/shm/bigscreen-state/alert_life.json")
DEFAULTS = {"full_showings": 3, "full_minutes": 30, "half": True}

_lock = threading.Lock()
_state = None
_settings = dict(DEFAULTS)
_dirty = False


def configure(settings):
    _settings.clear()
    _settings.update(DEFAULTS)
    _settings.update({k: v for k, v in (settings or {}).items() if k in DEFAULTS})


def settings():
    return dict(_settings)


def _load():
    global _state
    if _state is None:
        try:
            _state = json.load(open(PATH))
        except Exception:
            _state = {}
    return _state


def _save():
    global _dirty
    try:
        os.makedirs(os.path.dirname(PATH), exist_ok=True)
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f)
        os.replace(tmp, PATH)
        _dirty = False
    except Exception as e:
        print(f"[alert-life] save: {e}")


def _key(source, aid):
    return f"{source}|{aid}"


def phase(source, aid, now=None):
    now = now or time.time()
    with _lock:
        st = _load().get(_key(source, aid))
    if not st or not st.get("n"):
        return "new"
    if not _settings.get("half", True):
        return "full"
    if st["n"] >= int(_settings.get("full_showings") or 3) or now - st["first"] >= float(_settings.get("full_minutes") or 30) * 60:
        return "half"
    return "full"


def in_phase(source, aid, want, now=None):
    """``want``: "new", "full" (new or full), "half", or None/"any"."""
    if not want or want == "any":
        return True
    p = phase(source, aid, now)
    return p in ("new", "full") if want == "full" else p == want


def shown(source, ids, now=None):
    """A full showing of these alerts finished."""
    now = now or time.time()
    with _lock:
        st = _load()
        for aid in ids:
            e = st.setdefault(_key(source, aid), {"n": 0, "first": now})
            e["n"] = e.get("n", 0) + 1
            e["last"] = now
        for k in [k for k, v in st.items() if now - v.get("last", v.get("first", now)) > 86400]:
            st.pop(k, None)
        _save()


def seen_alive(source, ids, now=None):
    """Touch alerts still in effect so their record isn't pruned while they last."""
    now = now or time.time()
    with _lock:
        st = _load()
        for aid in ids:
            e = st.get(_key(source, aid))
            if e:
                e["last"] = max(e.get("last", 0), now - 1)


def new_ids(source, ids):
    return sorted(a for a in ids if phase(source, a) == "new")
