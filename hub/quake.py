"""Japan earthquake early warning (EEW): live listener, filtering, and push to the display.

Sources (free, push over WebSocket — what hobby EEW viewers use):
  * Wolfx  wss://ws-api.wolfx.jp/jma_eew   JMA EEW (forecast and warning), every serial update
  * P2PQuake wss://api.p2pquake.net/v2/ws  JMA earthquake information (551: hypocentre + observed
                                            intensities by place) and EEW detections (556)

The listener runs as a small daemon thread inside the hub (one asyncio loop, two sockets, automatic
reconnect). An event that passes the thresholds (max intensity, or the predicted intensity at Tokyo /
Yokohama, or any EEW *warning*) is written to data/quake/event.json and pushed straight to the
display (POST /quake), so the wall reacts within about a second of the JMA bulletin.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from common import quake_calc as qc
from hub import config, paths, pi, sync
from hub.util import atomic_write_json, read_json

OUT = paths.DATA_DIR / "quake"
JST = timezone(timedelta(hours=9))
WOLFX = "wss://ws-api.wolfx.jp/jma_eew"
P2P = "wss://api.p2pquake.net/v2/ws"
P2P_SCALE = {10: "1", 20: "2", 30: "3", 40: "4", 45: "5-", 46: "5-", 50: "5+", 55: "6-", 60: "6+", 70: "7"}

state = {"connected": {}, "last_message": {}, "events": [], "errors": [], "pushed": None}
_lock = threading.Lock()


def settings():
    return (config.load()["modules"].get("japan_eew") or {})


def log(msg):
    print(f"[quake] {msg}\n", end="", flush=True)


# ---------------------------------------------------------------------------- map of Japan (rendered once)
MAP_W, MAP_H = 270, 192
AREA = {"id": "japan", "label": "Japan", "fit": {"nw": [45.9, 127.6], "se": [30.4, 146.4]},
        "carto": {"rivers": {"enabled": False}, "major_highways": {"enabled": False}, "minor_highways": {"enabled": False},
                  "river_banks": {"enabled": False}, "lakes": {"enabled": False}}}


def ensure_map():
    """data/quake/japan.bin (+ .json with the projection) — dark basemap, coast, prefecture lines."""
    meta_p = OUT / "japan.json"
    if meta_p.exists() and (OUT / "japan.bin").exists():
        return read_json(meta_p, {})
    import numpy as np
    from hub.weather import carto
    from hub.weather.geo import View, alpha_over, render_basemap
    view = View(AREA, MAP_W, MAP_H, fit_width=MAP_W)
    base = render_basemap(view, {"source": "esri_dark", "brightness": 0.9, "contrast": 1.2})
    img = alpha_over(base, carto.render_overlay(view, AREA["carto"], 2.0, group="over"))
    OUT.mkdir(parents=True, exist_ok=True)
    np.ascontiguousarray(img, dtype=np.uint8).tofile(OUT / "japan.bin")
    meta = {"w": MAP_W, "h": MAP_H, "merc": list(view.merc), "mpp": view.mpp}
    atomic_write_json(meta_p, meta)
    return meta


# ---------------------------------------------------------------------------- parsing
def _jst(s):
    try:
        return datetime.strptime(s, "%Y/%m/%d %H:%M:%S").replace(tzinfo=JST).timestamp()
    except (TypeError, ValueError):
        return time.time()


def from_wolfx(d):
    return {
        "source": "wolfx", "id": str(d.get("EventID")), "serial": d.get("Serial"), "title": d.get("Title") or "緊急地震速報",
        "origin": _jst(d.get("OriginTime")), "announced": _jst(d.get("AnnouncedTime")),
        "lat": float(d.get("Latitude") or 0), "lon": float(d.get("Longitude") or 0), "depth": float(d.get("Depth") or 10),
        "mag": float(d.get("Magnitude") or d.get("Magunitude") or 0), "hypocenter": d.get("Hypocenter") or "",
        "max": qc.class_from_text(d.get("MaxIntensity")), "warn": bool(d.get("isWarn")), "final": bool(d.get("isFinal")),
        "cancel": bool(d.get("isCancel")), "training": bool(d.get("isTraining")), "assumption": bool(d.get("isAssumption")),
        "sea": bool(d.get("isSea")),
        "areas": [{"name": a.get("Chiiki"), "class": qc.class_from_text(a.get("Shindo1")), "type": a.get("Type"),
                   "arrive": bool(a.get("Arrive"))} for a in (d.get("WarnArea") or [])],
    }


def from_p2p_info(d):
    """551: observed intensities (by place) after the shaking."""
    eq = d.get("earthquake") or {}
    hy = eq.get("hypocenter") or {}
    points = {}
    for p in d.get("points") or []:
        cls = P2P_SCALE.get(p.get("scale"), "0")
        pref = p.get("pref") or ""
        if qc.class_rank(cls) > qc.class_rank(points.get(pref, "0")):
            points[pref] = cls
    return {
        "source": "p2p", "id": d.get("id"), "title": "震度情報", "origin": _jst(eq.get("time")), "announced": time.time(),
        "lat": float(hy.get("latitude") or 0), "lon": float(hy.get("longitude") or 0), "depth": float(hy.get("depth") or 10),
        "mag": float(hy.get("magnitude") or 0), "hypocenter": hy.get("name") or "",
        "max": P2P_SCALE.get(eq.get("maxScale"), "0"), "warn": False, "final": True, "cancel": False, "training": False,
        "assumption": False, "observed": points,
    }


def should_show(ev, st):
    if ev.get("training") and not st.get("show_training"):
        return False
    if ev.get("cancel"):
        return True                                                         # tell the wall it's off
    if ev.get("warn"):
        return True
    if ev["lat"] == 0 and ev["lon"] == 0:
        return False
    if qc.class_rank(ev["max"]) >= qc.class_rank(st.get("min_class") or "4"):
        return True
    sites = qc.site_forecast(ev, time.time())
    best = max((v["class"] for v in sites.values()), key=qc.class_rank)
    return qc.class_rank(best) >= qc.class_rank(st.get("tokyo_class") or "3")


def handle(ev, force=False):
    st = settings()
    with _lock:
        state["events"] = ([ev] + [e for e in state["events"] if e.get("id") != ev.get("id")])[:20]
    if not force and (st.get("enabled") is not True or not should_show(ev, st)):
        return False
    ensure_map()
    OUT.mkdir(parents=True, exist_ok=True)
    atomic_write_json(OUT / "event.json", ev)
    dur = float(st.get("duration_sec") or 150)
    try:
        code, body = pi.display_post("/quake", {"event": ev, "duration_sec": dur}, timeout=3)
        state["pushed"] = {"at": time.time(), "id": ev.get("id"), "ok": code < 400, "reply": body}
    except Exception as e:
        state["pushed"] = {"at": time.time(), "id": ev.get("id"), "ok": False, "reply": str(e)[:200]}
    sync.push_data()
    log(f"pushed {ev.get('title')} {ev.get('hypocenter')} M{ev.get('mag')} max {ev.get('max')}")
    return True


# ---------------------------------------------------------------------------- listener
async def _listen(name, url, parse):
    import websockets
    backoff = 2
    while True:
        try:
            async with websockets.connect(url, ping_interval=30, ping_timeout=30, open_timeout=20,
                                          user_agent_header="BigScreen/2.0 (personal display)") as ws:
                state["connected"][name] = True
                backoff = 2
                log(f"{name} connected")
                async for raw in ws:
                    state["last_message"][name] = time.time()
                    try:
                        d = json.loads(raw)
                    except ValueError:
                        continue
                    ev = parse(d)
                    if ev:
                        handle(ev)
        except Exception as e:
            state["connected"][name] = False
            with _lock:
                state["errors"] = ([f"{datetime.now():%H:%M:%S} {name}: {str(e)[:120]}"] + state["errors"])[:10]
        await asyncio.sleep(backoff)
        backoff = min(120, backoff * 2)


def _parse_wolfx(d):
    if d.get("type") in ("heartbeat", "pong") or "EventID" not in d:
        return None
    return from_wolfx(d)


def _parse_p2p(d):
    if d.get("code") == 551:
        return from_p2p_info(d)
    return None                       # 556 EEW detections duplicate Wolfx's JMA EEW


def start():
    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.gather(_listen("wolfx", WOLFX, _parse_wolfx), _listen("p2pquake", P2P, _parse_p2p)))
    threading.Thread(target=run, name="quake-listener", daemon=True).start()


# ---------------------------------------------------------------------------- test scenarios
SCENARIOS = {
    "tohoku": {"label": "Tōhoku 2011 (M9.0, off Miyagi)", "hypocenter": "三陸沖", "lat": 38.1, "lon": 142.9, "depth": 24,
               "mag": 9.0, "max": "7"},
    "noto": {"label": "Noto 2024 (M7.6, Ishikawa)", "hypocenter": "石川県能登地方", "lat": 37.5, "lon": 137.27, "depth": 10,
             "mag": 7.6, "max": "7"},
    "tokyo_bay": {"label": "Tokyo Bay (M6.0, simulated)", "hypocenter": "東京湾", "lat": 35.5, "lon": 139.8, "depth": 50,
                  "mag": 6.0, "max": "5+"},
    "chiba": {"label": "Chiba (M5.0, simulated)", "hypocenter": "千葉県北西部", "lat": 35.7, "lon": 140.1, "depth": 60,
              "mag": 5.0, "max": "4"},
}


def test(name):
    sc = SCENARIOS.get(name) or SCENARIOS["tokyo_bay"]
    now = time.time()
    ev = {"source": "test", "id": f"test-{name}-{int(now)}", "serial": 1, "title": "緊急地震速報（警報）",
          "origin": now - 2.0, "announced": now, "lat": sc["lat"], "lon": sc["lon"], "depth": sc["depth"], "mag": sc["mag"],
          "hypocenter": sc["hypocenter"], "max": sc["max"], "warn": qc.class_rank(sc["max"]) >= qc.class_rank("5-"),
          "final": False, "cancel": False, "training": False, "assumption": False, "test": True, "areas": []}
    handle(ev, force=True)
    return ev
