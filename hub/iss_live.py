"""NASA's live ISS telemetry (the public "ISS Live" feed) for the ISS map: whether mission control has the station's
signal (AOS) or not (LOS), and a few readings from the station.

The feed is public on Lightstreamer's server (adapter set ISSLIVE; item names from NASA's PUI list, e.g.
Lightstreamer's ISSLive example's PUIList.xml). This daemon thread (started by ``hub.main``) keeps one streaming
session open while a webcam is in space, and keeps the newest value of each item and the history of the signal
(when it changed). The display asks for them through ``GET /api/iss/live`` (hub/app.py) every few seconds while
its ISS map is up; the signal is looked up for the moment the video shows (space.LAG behind), like the orbit.

Signal: TIME_000001's status class is 24 while the ground has the signal (as ISS Mimic and NASA's own page read
it); anything else is LOS. During LOS the feed goes quiet (no updates, only the session's keepalives): the last
signal stands for as long as the session is alive (heard from within STALE_SEC, keepalives included).
"""

from __future__ import annotations

import threading
import time
from collections import deque

import requests

BASE = "https://push.lightstreamer.com/lightstreamer"
CID = "mgQkwtwdysogQz2BJ4Ji kOj2Bg"          # Lightstreamer's public client id for its demo feeds
UA = {"User-Agent": "BigScreen/2 (home LED wall; ISS map)"}
ITEMS = {
    "TIME_000001": "signal",
    "USLAB000058": "cabin_p", "USLAB000059": "cabin_t", "USLAB000053": "o2", "USLAB000055": "co2",
    "USLAB000005": "gyros", "Z1000013": "ku_tx", "Z1000014": "ku_el", "NODE3000009": "water",
    "S0000004": "sarj_p", "S0000003": "sarj_s", "AIRLOCK000054": "airlock_p", "USLAB000081": "maneuver",
    # (the Space Map's panels: display/modules/mappanels.py; each checked against the live feed 2026-11-23)
    "USLAB000032": "pos_x", "USLAB000033": "pos_y", "USLAB000034": "pos_z",          # km (J2000)
    "USLAB000035": "vel_x", "USLAB000036": "vel_y", "USLAB000037": "vel_z",          # m/s
    "USLAB000039": "mass", "USLAB000086": "mode", "USLAB000012": "att_mode", "USLAB000013": "nav",
    "USLAB000102": "gmt", "NODE3000005": "urine", "NODE3000008": "waste", "NODE3000004": "upa",
    "AIRLOCK000049": "crewlock",
    "S4000001": "v_1a", "P4000001": "v_2a", "S6000004": "v_1b", "P6000004": "v_2b",
    "S4000002": "a_1a", "P4000002": "a_2a", "S6000005": "a_1b", "P6000005": "a_2b",
    "S4000007": "beta_1a", "P4000007": "beta_2a", "S6000008": "beta_1b", "P6000007": "beta_2b",
    "S4000008": "beta_3a", "P4000008": "beta_4a",
    "RUSSEG000001": "ru_mode", "RUSSEG000024": "ru_thrusters", "RUSSEG000025": "ru_att",
}
AOS_CLASS = "24"
STALE_SEC = 30.0
HISTORY_SEC = 3600.0

state = {"connected": False, "since": None, "error": None, "updates": 0, "last": None, "heard": None}
values: dict = {}                  # name -> {"v": raw, "cal": calibrated text, "at": when}
signal = deque()                   # [(t, 1 | 0)] whenever it changed
_lock = threading.Lock()

# the readings' recent past, for the Space Map's sparklines: one sample per SERIES_STEP, SERIES_SEC of them, of every
# numeric reading plus a few worked out from them (altitude, speed, array voltage and current)
SERIES_SEC = 3 * 3600
SERIES_STEP = 20.0
series: dict = {}                  # name -> deque[(t, value)]
R_EARTH = 6378.137
# a spacewalk: the crewlock pumped down (below EVA_MMHG for EVA_HOLD s) until it's back up; kept across restarts
EVA_MMHG = 100.0
EVA_HOLD = 60.0
eva = {"on": False, "since": None, "low_since": None, "last": None}


def log(msg):
    print(f"[iss-live] {msg}\n", end="", flush=True)


def wanted() -> bool:
    """The ISS is on the wall somewhere (hub/jobs/iss.py wanted: a webcam in space, or the ISS map)."""
    try:
        from hub import config
        from hub.jobs.iss import wanted as iss_wanted
        return iss_wanted(config.load())
    except Exception:
        return False


def snapshot() -> dict:
    """What /api/iss/live serves: the signal's changes over the last hour, the newest readings, the feed's state."""
    now = time.time()
    with _lock:
        sig = [list(x) for x in signal]
        vals = {k: dict(v) for k, v in values.items()}
    fresh = state["heard"] is not None and now - state["heard"] < STALE_SEC     # (keepalives count: LOS is quiet)
    return {"at": now, "connected": bool(state["connected"] and fresh), "signal": sig, "values": vals,
            "error": state["error"], "eva": {k: eva[k] for k in ("on", "since", "last")}}


def decode(rest, prev):
    """A TLCP update's fields (``a|b|c``) over the previous values: empty = unchanged, ``#`` = null (None), ``$`` =
    empty text, ``^N`` = the next N unchanged; anything else percent-encoded."""
    from urllib.parse import unquote
    out, i = [], 0
    for f in rest.split("|"):
        if f.startswith("^") and f[1:].isdigit():
            for _ in range(int(f[1:])):
                out.append(prev[i] if i < len(prev) else None)
                i += 1
            continue
        out.append(prev[i] if f == "" and i < len(prev) else None if f in ("#", "") else "" if f == "$" else unquote(f))
        i += 1
    return out


def _note(name, val, cls, cal, now):
    with _lock:
        if name == "signal":
            if not cls:
                return
            aos = 1 if cls == AOS_CLASS else 0
            if not signal or signal[-1][1] != aos:
                signal.append((now, aos))
            while len(signal) > 1 and signal[1][0] < now - HISTORY_SEC:
                signal.popleft()
            return
        values[name] = {"v": val, "cal": cal, "at": now}
        _sample(name, val, now)
        if name == "crewlock":
            _eva(val, now)


def _num(name):
    try:
        return float(values[name]["v"])
    except (KeyError, TypeError, ValueError):
        return None


def _put(name, v, now):
    q = series.get(name)
    if q is None:
        q = series[name] = deque()
    if q and now - q[-1][0] < SERIES_STEP:
        q[-1] = (q[-1][0], v)                         # (the newest value in this step)
    else:
        q.append((now, v))
    while q and q[0][0] < now - SERIES_SEC:
        q.popleft()


def _sample(name, val, now):
    """Keep a reading (and what's worked out from it) in its series (under the lock)."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return
    if v != v:
        return
    _put(name, v, now)
    if name.startswith("pos_"):
        p = [_num(k) for k in ("pos_x", "pos_y", "pos_z")]
        if None not in p:
            _put("alt", (p[0] ** 2 + p[1] ** 2 + p[2] ** 2) ** 0.5 - R_EARTH, now)
    elif name.startswith("vel_"):
        p = [_num(k) for k in ("vel_x", "vel_y", "vel_z")]
        if None not in p:
            _put("speed", (p[0] ** 2 + p[1] ** 2 + p[2] ** 2) ** 0.5 / 1000.0, now)          # km/s
    elif name[:2] in ("v_", "a_"):
        got = [_num(name[:2] + k) for k in ("1a", "2a", "1b", "2b")]
        got = [g for g in got if g is not None]
        if got:
            _put("v_avg" if name[0] == "v" else "a_sum", sum(got) / len(got) if name[0] == "v" else sum(got), now)


def _eva(val, now):
    """The crewlock's pressure: a spacewalk starts once it's been below EVA_MMHG for EVA_HOLD s (from when it went
    below) and ends when it's back up (under the lock)."""
    try:
        p = float(val)
    except (TypeError, ValueError):
        return
    changed = False
    if p < EVA_MMHG:
        if eva["low_since"] is None:
            eva["low_since"] = now
        if not eva["on"] and now - eva["low_since"] >= EVA_HOLD:
            eva.update(on=True, since=eva["low_since"])
            changed = True
    else:
        if eva["on"]:
            eva.update(on=False, last={"start": eva["since"], "end": now})
            changed = True
        eva["low_since"] = None
    if changed:
        _save_eva()


def _eva_path():
    from hub import paths
    return paths.CACHE_DIR / "iss_eva.json"


def _save_eva():
    try:
        import json
        _eva_path().write_text(json.dumps(eva))
    except Exception:
        pass


def _load_eva():
    try:
        import json
        got = json.loads(_eva_path().read_text())
        if isinstance(got, dict):
            eva.update({k: got.get(k) for k in eva})
    except Exception:
        pass


def history(keys=None) -> dict:
    """What /api/iss/history serves: each reading's samples over the last SERIES_SEC ([[t, value], …], rounded)."""
    with _lock:
        out = {k: [[round(t), round(v, 3)] for t, v in q] for k, q in series.items() if keys is None or k in keys}
    return {"at": time.time(), "step": SERIES_STEP, "series": out}


def _session():
    """One streaming session: subscribe to ITEMS and read updates until the stream ends."""
    r = requests.post(BASE + "/create_session.txt?LS_protocol=TLCP-2.5.0",
                      data={"LS_adapter_set": "ISSLIVE", "LS_cid": CID, "LS_content_length": "100000000",
                            "LS_keepalive_millis": "5000"},
                      stream=True, timeout=(15, 60), headers=UA)
    try:
        r.raise_for_status()
        lines = r.iter_lines(decode_unicode=True)
        sess = None
        for ln in lines:
            if ln.startswith("CONOK"):
                sess = ln.split(",")[1]
                break
            if ln.startswith(("CONERR", "END")):
                raise RuntimeError(ln[:120])
        if not sess:
            raise RuntimeError("no session")
        keys = list(ITEMS)
        for i, item in enumerate(keys, 1):
            c = requests.post(BASE + "/control.txt?LS_protocol=TLCP-2.5.0&LS_session=" + sess,
                              data={"LS_op": "add", "LS_subId": str(i), "LS_group": item,
                                    "LS_schema": "Value Status.Class CalibratedData", "LS_mode": "MERGE",
                                    "LS_snapshot": "true", "LS_reqId": str(i)}, timeout=15, headers=UA)
            if not c.text.startswith("REQOK"):
                log(f"{item} refused: {c.text.strip()[:80]}")
        state.update(connected=True, since=time.time(), error=None)
        log("connected")
        last_check = time.time()
        prev = {}
        for ln in lines:
            now = time.time()
            state["heard"] = now
            if ln.startswith("U,"):
                _u, sid, _item, rest = ln.split(",", 3)
                k = int(sid) - 1
                f = prev[k] = (decode(rest, prev.get(k, [])) + [None, None, None])[:3]
                if 0 <= k < len(keys):
                    _note(ITEMS[keys[k]], f[0], f[1], f[2], now)
                    state["updates"] += 1
                    state["last"] = now
            elif ln.startswith(("LOOP", "END", "ERROR", "CONERR")):
                return                                    # the server wants a new session
            if now - last_check > 60:
                last_check = now
                if not wanted():
                    return
    finally:
        state["connected"] = False
        r.close()


def _run():
    wait = 5.0
    while True:
        if not wanted():
            time.sleep(60)
            continue
        t0 = time.time()
        try:
            _session()
            wait = 5.0 if time.time() - t0 > 120 else min(300.0, wait * 2)   # (one ended at once: back off)
        except Exception as e:
            state.update(connected=False, error=f"{time.strftime('%H:%M:%S')} {str(e)[:160]}")
            log(f"error: {e}")
            wait = min(300.0, wait * 2)
        time.sleep(wait)


def start():
    _load_eva()
    threading.Thread(target=_run, name="iss-live", daemon=True).start()
