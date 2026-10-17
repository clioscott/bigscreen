"""Live seismometers for the BC earthquake screen: EarthScope's SeedLink ring server → per-station shaking.

What the Japan screen gets from NIED's Kyoshin Monitor, built here from raw waveforms: ~34 vertical
accelerometers (CN = NRCan's CNSN, UW = PNSN) within ~270 km of home stream from
``rtserve.iris.washington.edu:18000`` (free, no login, 1–5 s behind real time), plus the Queen Elizabeth Park
broadband seismometer for the seismograph trace. This daemon thread (started by ``hub.main``) keeps one
SeedLink connection open and once a second turns each station's last second into:

  * PGA (cm/s², vertical × 1.5 as a horizontal proxy) → an instrumental intensity on the MMI scale
    (Worden et al. 2012, continuous, < 1 when nobody would feel it),
  * a trigger when that second is 4× the station's own background (median of the minute before).

Four or more stations triggering within 20 s is a *detection*: the quakes job is asked to run every 30 s for
the next five minutes (the catalogues have it after 1–5 minutes), and the wall is taken over when stations near
home measured felt shaking. While a quake is fresh (a catalogue item < 5 min old, a warning, a detection or a
replay) the readings are pushed to the display once a second (POST /bc_quake_obs), like ``hub.quake._monitor``
for Japan. Nothing heavy reaches the Pi: ~3 kB of JSON a second, only during a quake.

Replays feed a past quake's records (FDSN dataselect, cached) through the same code, shifted to now.
"""

from __future__ import annotations

import json
import math
import statistics
import threading
import time
from collections import deque

import numpy as np

from hub import paths
from hub.bc import common, mseed

HOST, PORT = "rtserve.iris.washington.edu", 18000
STATION_WS = "https://service.iris.edu/fdsnws/station/1/query"
DATASELECT = "https://service.iris.edu/fdsnws/dataselect/1/query"
UA = "BigScreen/2.0 (personal LED display in Vancouver; ~35 channels)"
# (net, station, location, accelerometer channel, lat, lon, name) — picked for spread and low latency, 2026-10
STATIONS = [
    ("CN", "QEPB", "", "HNZ", 49.242, -123.114, "Queen Elizabeth Park"), ("CN", "BOIB", "", "HNZ", 49.39, -123.379, "Bowen Island"),
    ("CN", "GOBB", "", "HNZ", 48.949, -123.51, "Galiano Island"), ("UW", "HFMN", "", "HNZ", 48.885, -122.701, "Ferndale"),
    ("UW", "ORCA", "", "HNZ", 48.708, -122.909, "Orcas Island"), ("UW", "LARL", "", "HNZ", 48.859, -122.484, "Bellingham"),
    ("UW", "LUMI", "", "ENZ", 48.72, -122.706, "Lummi Island"), ("UW", "RNWY", "", "ENZ", 48.612, -123.126, "San Juan Island"),
    ("UW", "PASS", "", "ENZ", 48.998, -122.085, "Maple Falls"), ("CN", "SHB", "", "HNZ", 49.599, -123.877, "Sechelt"),
    ("CN", "VGZ", "", "HNZ", 48.413, -123.325, "Victoria"), ("CN", "SYMB", "", "HNZ", 48.559, -123.799, "Survey Mountain"),
    ("CN", "CLRS", "", "HNZ", 48.82, -124.131, "Cowichan Lake"), ("CN", "GHNB", "", "HNZ", 49.306, -124.236, "Nanoose"),
    ("UW", "SMSH", "", "HNZ", 48.586, -122.226, "Sedro-Woolley"), ("UW", "SVOH", "", "HNZ", 48.289, -122.633, "Mount Vernon"),
    ("UW", "SHUK", "", "ENZ", 48.853, -121.677, "Mt Baker"), ("CN", "WSLR", "", "HNZ", 50.127, -122.921, "Whistler"),
    ("CN", "TXDB", "", "HNZ", 49.698, -124.438, "Texada"), ("CN", "PTRF", "", "HNZ", 48.544, -124.413, "Port Renfrew"),
    ("UW", "PCOL", "", "HNZ", 48.1, -123.414, "Port Angeles"), ("UW", "LKCAV", "", "HNZ", 48.31, -121.987, "Lake Cavanaugh"),
    ("CN", "HOPB", "", "HNZ", 49.387, -121.421, "Hope"), ("CN", "PABB", "", "HNZ", 49.219, -124.811, "Port Alberni"),
    ("UW", "CHIMA", "", "ENZ", 48.011, -122.781, "Chimacum"), ("UW", "BRUIN", "", "HNZ", 48.252, -124.259, "Clallam Bay"),
    ("UW", "DART", "", "ENZ", 48.243, -121.604, "Darrington"), ("CN", "BFSB", "", "HNZ", 48.834, -125.136, "Bamfield"),
    ("UW", "LYNC", "", "HNZ", 47.826, -122.294, "Lynnwood"), ("UW", "COYL", "", "ENZ", 47.697, -122.788, "Quilcene"),
    ("CN", "LLLB", "", "HNZ", 50.609, -121.882, "Lillooet"), ("CN", "CBB", "", "HNZ", 50.032, -125.365, "Campbell River"),
    ("CN", "SHPB", "", "HNZ", 49.348, -126.26, "Sharp Point"), ("UW", "TORK", "", "HNZ", 47.087, -122.757, "Lacey"),
]
TRACE = ("CN", "QEPB", "", "HHZ")                     # the seismograph: broadband velocity, 11 km from home
DEFAULTS = {"enabled": True, "detect_stations": 4, "detect_window_sec": 20, "detect_takeover_mmi": 3.0,
            "detect_home_km": 80}
H_FACTOR = 1.5                                         # vertical → horizontal PGA (typical ratio for local quakes)

state = {"connected": False, "since": None, "hello": None, "records": 0, "last_record": None, "errors": [],
         "pushing": None, "pushed_at": None, "push_error": None, "detections": [], "replay": None, "sens": {}}
_notify = None


def log(msg):
    print(f"[seismo] {msg}\n", end="", flush=True)


def _err(msg):
    state["errors"] = ([f"{time.strftime('%H:%M:%S')} {msg[:160]}"] + state["errors"])[:10]


def settings():
    from hub import config
    cfg = config.load()
    return common.settings(cfg, "bc_quakes", {"live": DEFAULTS})["live"]


def mmi_from_pga(pga_cms2):
    """Worden et al. (2012) PGA → MMI, continuous (values under I mean instruments only)."""
    lp = math.log10(max(pga_cms2, 1e-4))
    v = 1.78 + 1.55 * lp if lp <= 1.57 else -1.60 + 3.70 * lp
    return max(-2.0, min(10.0, v))


# ------------------------------------------------------------------------------------------------ sensitivities
def sensitivities(force=False):
    """counts per (m/s² | m/s) for every channel we use, from the FDSN station service (cached for a week)."""
    cached = common.cache("seismo_sens", None)
    if cached and not force and time.time() - cached.get("at", 0) < 7 * 86400:
        return cached["sens"]
    sens = {}
    nets = sorted({s[0] for s in STATIONS})
    for net in nets:
        stas = ",".join(s[1] for s in STATIONS if s[0] == net)
        r = common.session.get(STATION_WS, params={"net": net, "sta": stas, "cha": "HNZ,ENZ,HHZ", "level": "channel",
                                                   "format": "text", "endafter": time.strftime("%Y-%m-%d")}, timeout=60)
        r.raise_for_status()
        for line in r.text.splitlines()[1:]:
            f = line.split("|")
            try:
                sens[f"{f[0]}.{f[1]}.{f[2]}.{f[3]}"] = float(f[11])
            except (IndexError, ValueError):
                continue
    common.save_cache("seismo_sens", {"at": time.time(), "sens": sens})
    log(f"sensitivities for {len(sens)} channels")
    return sens


# ------------------------------------------------------------------------------------------------ processing
class Chan:
    """One channel: de-duplicated samples → per-second peak |a| (cm/s²) or, for the trace, 0.1-s min/max."""

    HP_TAU = 1.0 / (2 * math.pi * 0.1)                 # one-pole high-pass at 0.1 Hz (drops the DC offset)

    def __init__(self, sid, scale, trace=False):
        self.sid, self.scale, self.trace = sid, scale, trace
        self.t_end = None
        self.x_prev = self.y_prev = 0.0
        self.warm_until = 0.0
        self.sec = {}                                     # data second → peak |a| cm/s²
        self.bins = {}                                    # trace: 0.1-s bin → [min, max] (µm/s)

    def feed(self, r):
        if not self.scale or len(r.data) == 0:
            return
        dt = 1.0 / r.rate
        if self.t_end is not None and r.end <= self.t_end + 0.5 * dt:
            return                                        # duplicate (the ring server sends CN records twice)
        if self.t_end is None or abs(r.start - self.t_end) > 1.5 * dt:
            self.x_prev = float(r.data[0])                # gap or first record: restart the filter
            self.y_prev = 0.0
            self.warm_until = r.start + 15.0
        a = self.HP_TAU / (self.HP_TAU + dt)
        x = r.data
        y = np.empty(len(x))
        xp, yp = self.x_prev, self.y_prev
        for i in range(len(x)):                         # ~150 samples per record, in Python: fine at this rate
            yp = a * (yp + x[i] - xp)
            xp = x[i]
            y[i] = yp
        self.x_prev, self.y_prev = xp, yp
        self.t_end = r.end
        t = r.start + np.arange(len(y)) * dt
        keep = t >= self.warm_until
        if not keep.any():
            return
        t, y = t[keep], y[keep] / self.scale
        if self.trace:
            b = np.floor(t * 10).astype(np.int64)
            for k in np.unique(b):
                v = y[b == k] * 1e6                       # µm/s
                lo, hi = float(v.min()), float(v.max())
                old = self.bins.get(int(k))
                self.bins[int(k)] = [min(lo, old[0]), max(hi, old[1])] if old else [lo, hi]
            cut = int(t[-1] * 10) - 900
            for k in [k for k in self.bins if k < cut]:
                del self.bins[k]
            return
        s = np.floor(t).astype(np.int64)
        v = np.abs(y) * 100.0 * H_FACTOR                  # cm/s², horizontal proxy
        for k in np.unique(s):
            m = float(v[s == k].max())
            self.sec[int(k)] = max(m, self.sec.get(int(k), 0.0))
        cut = int(t[-1]) - 900
        for k in [k for k in self.sec if k < cut]:
            del self.sec[k]


class Network:
    """All stations' channels plus triggers and detections. One for the live feed, one per replay."""

    def __init__(self, sens, clock=time.time):
        self.clock = clock
        self.chans = {}
        self.meta = []
        for net, sta, loc, cha, la, lo, name in STATIONS:
            sid = f"{net}.{sta}.{loc}.{cha}"
            self.chans[sid] = Chan(sid, sens.get(sid))
            self.meta.append({"id": sid, "sta": sta, "name": name, "lat": la, "lon": lo,
                              "km": round(common.km(common.HOME_LONLAT[1], common.HOME_LONLAT[0], la, lo))})
        tsid = ".".join(TRACE)
        self.trace = Chan(tsid, sens.get(tsid), trace=True)
        self.trig = {}                                    # sid → first trigger second (current episode)
        self.peak = {}                                    # sid → peak MMI since the episode began
        self.detection = None
        self.episode_start = None

    def feed(self, r):
        sid = r.sid
        if sid == self.trace.sid:
            self.trace.feed(r)
        elif sid in self.chans:
            self.chans[sid].feed(r)

    def level(self, ch, now):
        """(MMI now, PGA now, triggered?) for the newest complete second at least 1 s old."""
        if not ch.sec:
            return None
        s = max(k for k in ch.sec if k <= now - 1) if any(k <= now - 1 for k in ch.sec) else None
        if s is None or now - s > 20:
            return None                                   # stale: off the map
        pga = max(ch.sec.get(s, 0.0), ch.sec.get(s - 1, 0.0))
        prev = [ch.sec[k] for k in range(s - 62, s - 2) if k in ch.sec]
        base = statistics.median(prev) if len(prev) >= 20 else None
        trig = base is not None and pga >= max(4.0 * base, 0.15)
        return mmi_from_pga(pga), pga, trig, s

    def step(self, st):
        """Once a second: levels, triggers, detection. Returns the payload for the display."""
        now = self.clock()
        out, ntrig = [], 0
        for m in self.meta:
            lv = self.level(self.chans[m["id"]], now)
            if lv is None:
                out.append(None)
                continue
            mmi, pga, trig, s = lv
            if trig:
                ntrig += 1
                self.trig.setdefault(m["id"], s)
            if self.trig.get(m["id"]) is not None:
                self.peak[m["id"]] = max(self.peak.get(m["id"], -2.0), mmi)
            out.append([round(mmi, 1), round(self.peak.get(m["id"], mmi), 1), 1 if trig else 0])
        # an episode: triggers within the window; it ends after 90 s with nothing new triggering
        win = float(st.get("detect_window_sec") or 20)
        if self.trig:
            if self.detection is None:
                # lone triggers (traffic, a door slamming by a school sensor) fade out after the window
                self.trig = {k: s for k, s in self.trig.items() if now - s <= win + 2}
                self.peak = {k: v for k, v in self.peak.items() if k in self.trig}
                if len(self.trig) >= int(st.get("detect_stations") or 4):
                    self._detect(min(self.trig.values()))
            elif now - max(self.trig.values()) > 90 and ntrig == 0:
                self.trig, self.peak = {}, {}
                self.detection = None
        if self.detection:
            d = self.detection
            d["stations"] = len(self.trig)
            pk = [(v, k) for k, v in self.peak.items()]
            if pk:
                v, k = max(pk)
                d["max_mmi"], d["max_at"] = round(v, 1), next(m["name"] for m in self.meta if m["id"] == k)
            near = [self.peak[m["id"]] for m in self.meta if m["id"] in self.peak and m["km"] <= float(st.get("detect_home_km") or 80)]
            d["near_mmi"] = round(max(near), 1) if near else None
        return {"t": now, "st": out, "trace": self.trace_payload(now), "detect": self.detection}

    def _detect(self, first):
        order = sorted(self.trig.items(), key=lambda kv: kv[1])
        m0 = next(m for m in self.meta if m["id"] == order[0][0])
        self.detection = {"id": f"detect-{int(first)}", "time": float(first), "first": m0["name"],
                          "first_lat": m0["lat"], "first_lon": m0["lon"], "stations": len(self.trig)}
        log(f"detection: {len(self.trig)} stations, first {m0['sta']} at {time.strftime('%H:%M:%S', time.localtime(first))}")

    def trace_payload(self, now):
        b = self.trace.bins
        if not b:
            return None
        k1 = max(b)
        if now - k1 / 10 > 20:
            return None
        ks = range(k1 - 399, k1 + 1)                      # 40 s at 0.1 s
        mm = [b.get(k) for k in ks]
        peak = max((max(abs(v[0]), abs(v[1])) for v in mm if v), default=0.0)
        full = max(5.0, peak)                             # quiet: background noise stays small
        q = [[int(round(v[0] / full * 100)), int(round(v[1] / full * 100))] if v else None for v in mm]
        return {"sta": TRACE[1], "name": "Queen Elizabeth Park", "t1": (k1 + 1) / 10, "dt": 0.1, "mm": q,
                "full": round(full, 1), "unit": "µm/s"}


# ------------------------------------------------------------------------------------------------ live thread
_net = None
_replay = {"net": None, "item": None, "until": 0.0, "label": None}
_lock = threading.Lock()


def _reader():
    global _net
    backoff = 5
    while True:
        try:
            if _net is None:
                _net = Network(sensitivities())
                state["sens"] = {k: bool(c.scale) for k, c in _net.chans.items()}
            streams = {}
            for net, sta, loc, cha, *_ in STATIONS:
                streams.setdefault((net, sta), []).append(f"{loc}{cha}" if loc else cha)
            streams.setdefault(TRACE[:2], []).append(TRACE[3])
            sl = mseed.SeedLink(HOST, PORT, [(n, s, sels) for (n, s), sels in streams.items()], ua=UA)
            hello, ok = sl.connect()
            state.update(connected=True, since=time.time(), hello=hello)
            log(f"connected to {HOST}: {ok}/{len(streams)} stations accepted")
            backoff = 5
            for r in sl.records():
                with _lock:
                    _net.feed(r)
                state["records"] += 1
                state["last_record"] = time.time()
        except Exception as e:
            state["connected"] = False
            _err(f"seedlink: {e}")
            log(f"seedlink: {e}; retry in {backoff}s")
        time.sleep(backoff)
        backoff = min(300, backoff * 2)


def _fresh_item():
    """A catalogue quake or warning less than five minutes old in data/quakes.json (the job's output)."""
    try:
        d = json.loads((paths.DATA_DIR / "quakes.json").read_text())
    except Exception:
        return None
    now = time.time()
    for it in d.get("items") or []:
        if it.get("time") and 0 <= now - float(it["time"]) < 300:
            return it
    return None


def _play(item_id):
    from hub import pi
    if __import__("os").environ.get("BIGSCREEN_NO_PI"):
        return log(f"play {item_id} (skipped: BIGSCREEN_NO_PI)")
    code, body = pi.display_post("/play", {"side": "right", "hold": False, "entry": {
        "panel": "quake_alert", "item": item_id, "advance": "cycles", "cycles": 1, "max_sec": 600}}, timeout=4)
    log(f"play {item_id}: {code} {body}")


def _monitor():
    from hub import pi
    fresh, fresh_at = None, 0.0
    st, st_at = DEFAULTS, 0.0
    detect_runs, taken = {}, set()
    while True:
        t0 = time.time()
        later = []                                        # wall takeovers, sent after the readings they show
        try:
            if t0 - st_at > 10:
                st, st_at = settings(), t0
            if t0 - fresh_at > 5:
                fresh, fresh_at = _fresh_item(), t0
            rp = _replay
            if rp["net"] is not None and t0 < rp["until"]:
                with _lock:
                    body = rp["net"].step(st)
                item = rp["item"] if rp["item"] and t0 >= rp["item_at"] else None
                if item is None and body["detect"] and not rp["item"]:
                    item = dict(body["detect"], kind="detect", replay=True, live=True, id=f"{rp['id']}-detect",
                                time=body["detect"]["time"] + rp["shift"])
                body["replay"], body["item"] = rp["label"], item
                if item and not rp["played"]:
                    rp["played"] = True
                    later.append(lambda i=item["id"]: _play(i))
                why = "replay"
            elif _net is not None:
                with _lock:
                    body = _net.step(st)
                d = body["detect"]
                if d and d["id"] not in detect_runs:
                    detect_runs[d["id"]] = t0
                    state["detections"] = ([dict(d)] + state["detections"])[:10]
                if d and d["id"] not in taken and _detect_wanted(d, st):
                    taken.add(d["id"])
                    later.append(lambda i=d["id"]: common.takeover("quake_alert", [i], "quakes_takeover", log=log,
                                                                   entry_extra={"item": i}))
                if d and _notify and int(t0 - detect_runs[d["id"]]) % 30 == 0 and t0 - detect_runs[d["id"]] < 300:
                    _notify(["quakes"])                   # the catalogues get it within minutes
                if d and not fresh:                       # nothing in the catalogues yet: the detection is the item
                    body["item"] = dict(d, kind="detect", live=True)
                why = "detection" if d else "quake" if fresh else None
            else:
                why = None
            if rp["net"] is not None and t0 >= rp["until"]:
                _replay.update(net=None, item=None)
                state["replay"] = None
            state["pushing"] = why
            if why and st.get("enabled") is not False and not __import__("os").environ.get("BIGSCREEN_NO_PI"):
                code, _ = pi.display_post("/bc_quake_obs", {**body, "stations": _meta()}, timeout=2)
                state["pushed_at"], state["push_error"] = t0, None if code < 400 else f"HTTP {code}"
            for fn in later:
                fn()
        except Exception as e:
            state["push_error"] = str(e)[:160]
        time.sleep(max(0.2, 1.0 - (time.time() - t0)))


def _meta():
    return [[m["sta"], m["name"], m["lat"], m["lon"], m["km"]] for m in (_net.meta if _net else Network({}).meta)]


def _detect_wanted(d, st):
    """Take the wall over for a detection felt near home (or any detection when the threshold is 0)? Checked
    every second of the episode, as the shaking reaches more stations."""
    thr = float(st.get("detect_takeover_mmi") or 0)
    return not thr or (d.get("near_mmi") or -9) >= thr


def start(notify=None):
    global _notify
    _notify = notify
    threading.Thread(target=_reader, name="seismo-reader", daemon=True).start()
    threading.Thread(target=_monitor, name="seismo-monitor", daemon=True).start()


# ------------------------------------------------------------------------------------------------ replays
REPLAYS = {
    "squamish2025": {"label": "M4.8 near Squamish, 21 Feb 2025", "origin": "2025-02-21T21:26:32.8",
                     "lat": 49.6928, "lon": -123.5454, "depth": 10.0, "mag": 4.8, "mag_type": "mww",
                     "place": "28 km W of Squamish"},
    "orcas2025": {"label": "M4.5 Orcas Island, 3 Mar 2025", "origin": "2025-03-03T13:02:37.85",
                  "lat": 48.6112, "lon": -122.8052, "depth": 15.8, "mag": 4.5, "mag_type": "ml",
                  "place": "Orcas Island, WA"},
}


def _fetch_replay(name, ev):
    from datetime import datetime, timezone
    p = paths.CACHE_DIR / "seismo_replay" / f"{name}.mseed"
    if p.exists():
        return p.read_bytes()
    t0 = datetime.fromisoformat(ev["origin"]).replace(tzinfo=timezone.utc).timestamp()
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    lines = [f"{n} {s} {loc or '--'} {c} {iso(t0 - 120)} {iso(t0 + 240)}" for n, s, loc, c, *_ in STATIONS]
    lines.append(f"{TRACE[0]} {TRACE[1]} -- {TRACE[3]} {iso(t0 - 120)} {iso(t0 + 240)}")
    r = common.session.post(DATASELECT, data="\n".join(lines), timeout=180)
    r.raise_for_status()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(r.content)
    return r.content


def replay(name, as_="quake", speed=1.0):
    """Play a recorded quake through the live pipeline, shifted to now: origin = now + 30 s lead-in of
    background. ``as_`` = "quake" (catalogue item from the start), "eew" (a warning 8 s after origin) or
    "detect" (no item: the stations alone)."""
    from datetime import datetime, timezone
    ev = REPLAYS.get(name) or next(iter(REPLAYS.values()))
    recs = sorted(mseed.read_file(_fetch_replay(name, ev)), key=lambda r: r.end)
    origin = datetime.fromisoformat(ev["origin"]).replace(tzinfo=timezone.utc).timestamp()
    lead = 75.0                                           # background before the quake (the trigger needs a minute)
    wall0 = time.time()
    shift = wall0 + lead - origin                         # data time + shift = wall time
    clock = {"t": wall0}
    net = Network(sensitivities(), clock=lambda: clock["t"] - shift)
    for r in recs:                                        # pre-roll: data up to the start of the lead-in
        if r.end <= origin - lead:
            net.feed(r)
    pending = deque(r for r in recs if r.end > origin - lead)

    def feeder():
        while pending and _replay["net"] is net:
            clock["t"] = time.time()
            with _lock:
                while pending and pending[0].end + 1.5 <= clock["t"] - shift:   # ~1.5 s like the live feed
                    net.feed(pending.popleft())
            time.sleep(0.1)

    from hub.jobs import quakes as qj
    item = qj.enrich({"id": f"replay-{name}-{int(wall0)}", "source": "replay", "mag": ev["mag"], "mag_type": ev["mag_type"],
                      "place": ev["place"], "lat": ev["lat"], "lon": ev["lon"], "depth": ev["depth"],
                      "time": origin + shift, "felt": 0}, common.HOME_LONLAT)
    item.update(kind="eew" if as_ == "eew" else "quake", replay=True, live=True)
    if as_ == "eew":
        item.update(headline="Earthquake early warning (replay)", areas=["Metro Vancouver"], origin_known=True)
    _replay.update(net=net, item=None if as_ == "detect" else item, until=wall0 + lead + 180, label=ev["label"],
                   item_at=wall0 + lead + (8 if as_ == "eew" else 0), id=item["id"], played=False, shift=shift)
    state["replay"] = {"name": name, "as": as_, "label": ev["label"], "started": wall0, "origin_wall": wall0 + lead}
    threading.Thread(target=feeder, name="seismo-replay", daemon=True).start()
    if _monitor_needed():
        threading.Thread(target=_monitor, name="seismo-monitor", daemon=True).start()
    return {"item": item, "origin_in_sec": lead, "records": len(recs)}


def _monitor_needed():
    return not any(t.name == "seismo-monitor" for t in threading.enumerate())
