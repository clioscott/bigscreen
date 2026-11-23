"""NASA's live ISS telemetry (the public "ISS Live" feed) for the ISS map: whether mission control has the station's
signal (AOS) or not (LOS), and a few readings from the station.

The feed is public on Lightstreamer's server (adapter set ISSLIVE; item names from NASA's PUI list, e.g.
Lightstreamer's ISSLive example's PUIList.xml). This daemon thread (started by ``hub.main``) keeps one streaming
session open while a webcam is in space, and keeps the newest value of each item and the history of the signal
(when it changed). The display asks for them through ``GET /api/iss/live`` (hub/app.py) every few seconds while
its ISS map is up; the signal is looked up for the moment the video shows (space.LAG behind), like the orbit.

Signal: TIME_000001's status class is 24 while the ground has the signal (as ISS Mimic and NASA's own page read
it); anything else, or nothing from the feed for STALE_SEC, is LOS / unknown.
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
}
AOS_CLASS = "24"
STALE_SEC = 30.0
HISTORY_SEC = 3600.0

state = {"connected": False, "since": None, "error": None, "updates": 0, "last": None}
values: dict = {}                  # name -> {"v": raw, "cal": calibrated text, "at": when}
signal = deque()                   # [(t, 1 | 0)] whenever it changed
_lock = threading.Lock()


def log(msg):
    print(f"[iss-live] {msg}\n", end="", flush=True)


def wanted() -> bool:
    """A webcam is in space."""
    try:
        from hub import config
        cams = ((config.load().get("modules") or {}).get("webcams") or {}).get("cams") or {}
        return any(isinstance(c, dict) and c.get("space") for c in cams.values())
    except Exception:
        return False


def snapshot() -> dict:
    """What /api/iss/live serves: the signal's changes over the last hour, the newest readings, the feed's state."""
    now = time.time()
    with _lock:
        sig = [list(x) for x in signal]
        vals = {k: dict(v) for k, v in values.items()}
    fresh = state["last"] is not None and now - state["last"] < STALE_SEC
    return {"at": now, "connected": bool(state["connected"] and fresh), "signal": sig, "values": vals,
            "error": state["error"]}


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
    threading.Thread(target=_run, name="iss-live", daemon=True).start()
