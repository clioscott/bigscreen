"""Control panel HTTP API + static UI (served on hub.panel_port, default 8800)."""

import asyncio
import io
import os
import re
import threading
import time
from pathlib import Path

import requests
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from common import logring
from hub import config, paths, pi, sync, traffic, webcams
from hub.jobs import news as news_job
from hub.util import atomic_write_json, read_json

# the hub's own previews of webcam captions draw real highway signs from its data dir (the Pi reads its synced copy)
from display.modules import webcam_caption as _webcam_caption  # noqa: E402
_webcam_caption.SIGN_DIRS.append(str(paths.DATA_DIR / "shields"))

WEB_DIR = Path(__file__).parent / "web"
ICON_NAME = re.compile(r"^[A-Za-z0-9._=^-]+\.png$")
DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

app = FastAPI(title="BigScreen hub")
scheduler = None          # set by hub.main


def _sched():
    if scheduler is None:
        raise HTTPException(503, "scheduler not running")
    return scheduler


@app.exception_handler(config.ConfigError)
def _config_error(_req: Request, exc: config.ConfigError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


# --- UI ----------------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.middleware("http")
async def _revalidate_static(request: Request, call_next):
    """The panel is plain ES modules: make browsers revalidate them (cheap 304s via ETag) so a deploy shows up
    on the next reload instead of whenever the heuristic cache expires."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/ui/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# --- config ------------------------------------------------------------------
@app.get("/api/config")
def get_config():
    return config.load()


@app.put("/api/config")
def put_config(payload: dict = Body(...)):
    cfg = payload.get("config")
    reason = str(payload.get("reason", ""))[:120]
    before = config.load()
    saved = config.save(cfg, reason=reason)
    # Re-run fetchers whose inputs changed so the panel shows fresh results right away.
    for job, section in (("finance", "finance"), ("news", "headlines"), ("calendar", "calendar"),
                         ("weather", "weather"), ("naad", "naad_alerts"), ("drivebc", "road_alerts"),
                         ("quakes", "bc_quakes"), ("tsunami", "tsunami"), ("wildfire", "wildfire"),
                         ("webcam_weather", "webcams"), ("shield_signs", "webcams")):
        if before["modules"].get(section) != saved["modules"].get(section):
            _sched().run_now(job)
    sync.push_data()          # config.json rides along; the v2 display hot-reloads it
    return {"config": saved, "display_in_sync": pi.display_in_sync(saved)[0]}


@app.get("/api/config/backups")
def backups():
    return config.list_backups()


@app.post("/api/config/restore")
def restore(payload: dict = Body(...)):
    name = payload.get("file", "")
    if not re.fullmatch(r"config-[\w-]+\.json", name):
        raise HTTPException(400, "bad backup name")
    old = read_json(paths.BACKUP_DIR / name)
    if old is None:
        raise HTTPException(404, "backup not found")
    return {"config": config.save(old, reason=f"restored {name}")}


# --- weather alerts --------------------------------------------------------------
@app.get("/api/alerts")
def alerts_now():
    """Current alerts plus the alert screen's design catalogue (themes, palettes, type pairings, fonts)."""
    from display.gfx import ASSETS_DIR
    from display.modules.alert_themes import BORDERS, FAMILIES, HAZARD_PALETTE, PALETTES, REVEALS, SEVERITY, STRUCTURES
    from display.modules.weather_alert import THEME_LIST
    data = read_json(paths.DATA_DIR / "alerts.json", {}) or {}
    status = ((config.load().get("hub") or {}).get("fonts") or {}).get("status") or {}
    fonts = sorted(p for p, v in status.items() if v == "approved" and not p.split("/")[-1].startswith("jp-")
                   and (Path(ASSETS_DIR) / "fonts" / p).exists())       # override pickers: approved Latin fonts only
    hexc = lambda c: "#%02x%02x%02x" % c if isinstance(c, tuple) else None
    return {**data, "themes": THEME_LIST, "structures": STRUCTURES, "borders": BORDERS, "reveals": REVEALS,
            "palettes": {k: {"name": v["name"], "swatch": [hexc(v["bg"]), hexc(v["strong"]), hexc(v["amount"]), hexc(v["advice"])]}
                         for k, v in PALETTES.items()},
            "families": {k: v["name"] for k, v in FAMILIES.items()},
            "hazards": [{"id": h, "label": l, "hint": e, "colour": hexc(HAZARD_PALETTE.get(h, HAZARD_PALETTE["general"])[0])}
                        for h, l, e in __import__("display.modules.alert_brief", fromlist=["x"]).HAZARD_INFO],
            "levels": {k: hexc(v) for k, v in SEVERITY.items()},
            "samples": {k: {"name": v["name"], "type": v["type"]} for k, v in
                        __import__("display.modules.alert_samples", fromlist=["x"]).SAMPLES.items()},
            "fonts": fonts}


def _alert_settings(s: str):
    """Saved alert settings with the editor's unsaved overrides (JSON in ?s=) on top."""
    import json as _json
    settings = {**(config.load()["modules"].get("weather_alerts") or {})}
    if s:
        try:
            over = _json.loads(s)
            if isinstance(over, dict):
                settings.update(over)
        except ValueError:
            raise HTTPException(400, "s must be JSON")
    return settings


@app.get("/api/alerts/icon/{hazard}.png")
def alerts_icon(hazard: str, scale: int = 3, colour: str = "ffffff"):
    """The wall's 24-px hazard icon (pixel_icons), enlarged by whole pixels for the control panel."""
    import numpy as np
    from display.modules import pixel_icons
    rgb = tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4)) if len(colour) == 6 else (255, 255, 255)
    m = pixel_icons.mask(pixel_icons.hazard(hazard), 24)
    rgba = np.zeros(m.shape + (4,), np.uint8)
    rgba[m] = rgb + (255,)
    k = max(1, min(6, scale))
    bio = io.BytesIO()
    Image.fromarray(rgba, "RGBA").resize((m.shape[1] * k, m.shape[0] * k), Image.NEAREST).save(bio, "PNG")
    return Response(bio.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/alerts/preview/{theme}.png")
def alerts_preview(theme: str, at: float = 4.0, scale: int = 2, real: int = 0, s: str = ""):
    """A frame of the full-screen alert at ``at`` seconds, rendered with the display's own code."""
    from display.modules.alert_themes import THEMES
    from display.modules.weather_alert import render_preview
    if theme not in THEMES:
        raise HTTPException(404, "unknown theme")
    data = dict(read_json(paths.DATA_DIR / "alerts.json", {}) or {})
    if not real:
        data["alerts"] = []                      # the test alert (its map comes from sample_map)
    settings = dict(_alert_settings(s), _data_dir=str(paths.DATA_DIR))
    frame = render_preview(theme, data, max(0.0, at), settings)
    k = max(1, min(4, scale))
    img = Image.fromarray(frame).resize((frame.shape[1] * k, frame.shape[0] * k), Image.NEAREST)
    bio = io.BytesIO()
    img.save(bio, "PNG", compress_level=1)
    return Response(bio.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=60"})


@app.get("/api/alerts/timeline")
def alerts_timeline(real: int = 0, s: str = ""):
    """When each page of the alert screen starts and how long it holds (for the preview scrubber)."""
    from display.modules.weather_alert import timeline
    data = dict(read_json(paths.DATA_DIR / "alerts.json", {}) or {})
    if not real:
        data["alerts"] = []
    return timeline(dict(_alert_settings(s), _data_dir=str(paths.DATA_DIR)), data)


# --- Japan earthquake early warning ----------------------------------------------
@app.get("/api/quake")
def quake_state():
    from hub import quake
    from display.modules.quake import THEMES
    return {"settings": quake.settings(), "connected": quake.state["connected"],
            "last_message": quake.state["last_message"], "events": quake.state["events"][:10],
            "errors": quake.state["errors"], "pushed": quake.state["pushed"], "obs_at": quake.state.get("obs_at"),
            "scenarios": {k: v["label"] for k, v in quake.SCENARIOS.items()},
            "themes": {k: {"label": v["label"], "hint": v["hint"], "type": v.get("type", "")} for k, v in THEMES.items()}}


@app.post("/api/quake/test/{name}")
def quake_test(name: str, theme: str = ""):
    from hub import quake
    ev = quake.test(name, theme or None)
    return {"ok": True, "event": ev, "pushed": quake.state["pushed"]}


_quake_obs_cache = {}


def _quake_obs(name, sc, sec):
    """Simulated station readings ``sec`` seconds into a scenario (cached: the preview replays a minute of them)."""
    key = (name, sec)
    if key not in _quake_obs_cache:
        from common import kmoni
        if len(_quake_obs_cache) > 800:
            _quake_obs_cache.clear()
        ev0 = {"origin": 0.0, "lat": sc["lat"], "lon": sc["lon"], "depth": sc["depth"], "mag": sc["mag"]}
        _quake_obs_cache[key] = kmoni.encode(kmoni.synthetic(ev0, float(sec)))
    return _quake_obs_cache[key]


@app.get("/api/quake/preview.png")
def quake_preview(name: str = "tokyo_bay", at: float = 20, scale: int = 2, theme: str = "kmoni"):
    """A frame of the EEW screen for a test scenario ``at`` seconds after origin, in a theme."""
    import time as _t
    from hub import quake
    from display.gfx import Canvas
    from display.modules.quake import QuakeScreen
    quake.ensure_map()
    sc = quake.SCENARIOS.get(name) or quake.SCENARIOS["tokyo_bay"]
    now = _t.time()
    ev = {"id": "preview", "origin": now - at, "lat": sc["lat"], "lon": sc["lon"], "depth": sc["depth"], "mag": sc["mag"],
          "hypocenter": sc["hypocenter"], "max": sc["max"], "warn": True, "serial": 1, "test": True, "theme": theme}
    scr = QuakeScreen(str(paths.DATA_DIR), {"theme": theme})
    scr.set_event(ev)
    for sec in range(max(0, int(at) - 60), int(at) + 1):     # a minute of history for the graphs and traces
        scr.set_obs(_quake_obs(name, sc, sec), now - (at - sec))
    c = Canvas(448, 192)
    scr.draw(c, (0, 0, 448, 192), {}, True, now=now)
    k = max(1, min(4, scale))
    img = Image.fromarray(c.buf).resize((448 * k, 192 * k), Image.NEAREST)
    bio = io.BytesIO()
    img.save(bio, "PNG")
    return Response(bio.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


# --- BC alert screens (Alert Ready, DriveBC, earthquakes, tsunami, wildfire) --------------
# src → (display module, job, config section)
BC_SOURCES = {"naad": ("bc_naad", "naad", "naad_alerts"), "roads": ("bc_roads", "drivebc", "road_alerts"),
              "quakes": ("bc_quake", "quakes", "bc_quakes"), "tsunami": ("bc_tsunami", "tsunami", "tsunami"),
              "wildfire": ("bc_fire", "wildfire", "wildfire")}


def _bc(src):
    if src not in BC_SOURCES:
        raise HTTPException(404, "unknown source")
    import importlib
    modname, job, section = BC_SOURCES[src]
    return importlib.import_module(f"display.modules.{modname}"), importlib.import_module(f"hub.jobs.{job}"), job, section


def _slim(v, depth=0):
    """Data for the panel without the big geometry arrays."""
    if isinstance(v, dict):
        return {k: (f"[{len(x)}]" if k in ("rings", "polygons", "points", "recent", "wlo", "wlp", "allow") and isinstance(x, list)
                    else _slim(x, depth + 1)) for k, x in v.items()}
    if isinstance(v, list):
        return [_slim(x, depth + 1) for x in v[:60]]
    return v


@app.get("/api/bc/{src}")
def bc_state(src: str):
    mod, job_mod, job, section = _bc(src)
    from hub.bc import common
    cfg = config.load()
    data = read_json(paths.DATA_DIR / f"{mod.Screen.DATA}.json", {}) or {}
    out = {"settings": common.settings(cfg, section, job_mod.DEFAULTS), "defaults": job_mod.DEFAULTS, "section": section,
           "panel": mod.Screen.PANEL, "data": _slim(data),
           "themes": {k: {"label": v["label"], "hint": v["hint"]} for k, v in mod.THEMES.items()},
           "samples": {k: v["label"] for k, v in mod.SAMPLES.items()},
           "job": _sched().snapshot().get(job)}
    if src == "naad":
        from hub.bc import naad_listener
        out["listener"] = {k: v for k, v in naad_listener.state.items()}
        out["areas"] = {k: v["label"] for k, v in job_mod.AREAS.items()}
    if src == "wildfire":
        out["regions"] = {k: v["label"] for k, v in job_mod.REGIONS.items()}
    if src == "quakes":
        from hub.bc import seismo
        out["seismo"] = {k: v for k, v in seismo.state.items() if k != "sens"}
        out["replays"] = {k: v["label"] for k, v in seismo.REPLAYS.items()}
    return out


@app.post("/api/bc/quakes/replay")
def bc_quake_replay(payload: dict = Body(default={})):
    """Replay a recorded quake through the live-seismometer pipeline (it takes over the wall at its origin)."""
    from hub.bc import seismo
    r = seismo.replay(payload.get("name") or "", payload.get("as") or "quake")
    return {"ok": True, "origin_in_sec": r["origin_in_sec"], "records": r["records"], "item": r["item"]["id"]}


@app.get("/api/bc/{src}/full_pages")
def bc_full_pages(src: str, sample: str = "", theme: str = ""):
    """The full-wall pages of a sample's first showing (its theme, then the extra pages such as the camera), for the
    preview's page buttons: name, theme and how long each holds."""
    mod, job_mod, job, section = _bc(src)
    from display.modules import bc_kit as k
    from hub.bc import common
    settings = common.settings(config.load(), section, job_mod.DEFAULTS)
    theme = theme if theme in mod.THEMES else (settings.get("theme") if settings.get("theme") in mod.THEMES else mod.Screen.DEFAULT_THEME)
    sample = sample if sample in mod.SAMPLES else next(iter(mod.SAMPLES))
    data = read_json(paths.DATA_DIR / f"{mod.Screen.DATA}.json", {}) or {}
    ids = k.full_pages(mod.Screen, theme, sample, data, str(paths.DATA_DIR), settings)
    scr = mod.Screen(settings, k.StaticLoader({mod.Screen.DATA: data}, str(paths.DATA_DIR)), str(paths.DATA_DIR))
    hold = round(float(scr.dwell(scr.sample(sample))), 1)
    out = [{"name": mod.THEMES[t]["label"], "theme": t, "hold": hold} for t in ids]
    return {"pages": out, "total": round(sum(p["hold"] for p in out), 1)}


_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
_PANEL_TILES = {"esri_dark": (_ESRI.format("World_Dark_Gray_Base"), "image/jpeg"),
                "esri_dark_labels": (_ESRI.format("World_Dark_Gray_Reference"), "image/png")}


@app.get("/api/tiles/{style}/{z}/{x}/{y}.png")
def panel_tile(style: str, z: int, x: int, y: int):
    """Basemap tiles for the panel's maps, fetched by the hub and cached on disk."""
    from hub.weather import geo
    if style not in _PANEL_TILES or not (0 <= z <= 16 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise HTTPException(404, "no such tile")
    url, mime = _PANEL_TILES[style]
    path = geo.TILE_CACHE / f"panel_{style}" / str(z) / str(x) / f"{y}"
    if not path.exists():
        try:
            r = geo._session.get(url.format(z=z, x=x, y=y), timeout=20)
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(502, f"tile: {e}")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_bytes(r.content)
        os.replace(tmp, path)                            # never a half-written tile in the cache
    return Response(path.read_bytes(), media_type=mime, headers={"Cache-Control": "max-age=604800"})


_webcams = {"at": 0.0, "list": [], "tried": 0.0}


def _dbc_list():
    """DriveBC's camera list, cached 10 min (a minute between failed tries)."""
    from hub.bc import common
    from hub.jobs import drivebc
    if time.time() - _webcams["at"] > 600 and time.time() - _webcams["tried"] > 60:
        _webcams["tried"] = time.time()
        try:
            got = common.session.get(f"{drivebc.API}/webcams/", timeout=20).json()
            if not isinstance(got, list):
                raise ValueError("unexpected reply")
            _webcams["list"], _webcams["at"] = traffic.fix_all(got), time.time()   # directions checked by eye
        except Exception as e:
            if not _webcams["list"]:
                raise HTTPException(502, f"DriveBC webcams: {e}")
    return _webcams["list"]


_city = {"at": {}, "lists": {}, "tried": 0.0, "urls": {}, "loaded": False}   # per city: crawled at, its views
_city_lock = threading.Lock()                # one crawl at a time
CITY_CACHE = paths.CACHE_DIR / "city_cams.json"
COV_CACHE = paths.CACHE_DIR / "cov_cams.json"   # before Richmond joined: Vancouver's views only


def _set_lists(lists):
    """Swap in new lists, the thumbnail index first (so a thumbnail for a camera just listed never 404s)."""
    _city["urls"] = {c["id"]: c["source"]["url"] for cams in lists.values() for c in cams}
    _city["lists"] = lists


def _stale_cities(now):
    return [c for c in traffic.CITY_SOURCES
            if not _city["lists"].get(c) or now - (_city["at"].get(c) or 0) > 86400]


CITY_KEEP_SEC = 7 * 86400           # a camera whose page stopped answering stays listed this long (it's probably just down)


def _keep_recent(got, old, old_at):
    """A city's new views, plus the old ones this crawl didn't find but that were seen within CITY_KEEP_SEC (a
    page that failed today, a camera that's down): they stay in the lists and on the map. Each view notes when it
    was last seen."""
    now = time.time()
    got = [{**v, "seen": now} for v in got]
    have = {v["id"] for v in got}
    kept = [v for v in old if v.get("id") not in have and now - (v.get("seen") or old_at) < CITY_KEEP_SEC]
    return got + kept


def _city_refresh(cities):
    from hub.bc import common
    get = lambda u: common.session.get(u, timeout=20).text
    try:
        lists, at, changed = dict(_city["lists"]), dict(_city["at"]), False
        for city in cities:
            old = lists.get(city) or []
            try:
                got = traffic.CITY_SOURCES[city](get)
            except Exception as e:
                print(f"{city} cameras: {e}", flush=True)
                continue
            if got and len(got) >= 0.8 * len(old):             # a crawl that lost many pages keeps the old list
                lists[city], at[city], changed = _keep_recent(got, old, at.get(city) or 0), time.time(), True
                _set_lists(dict(lists))                         # each city shows as soon as it's in
            else:
                print(f"{city} cameras: only {len(got)} views, keeping {len(old)}", flush=True)
        if changed:
            # never drop a city that's saved: one this crawl didn't get (or that wasn't loaded) keeps its saved list
            saved = read_json(CITY_CACHE, {}) or {}
            for city, cams in (saved.get("lists") or {}).items():
                if cams and not lists.get(city):
                    lists[city] = cams
                    at.setdefault(city, (saved.get("at") or {}).get(city) or 0)
            _set_lists(lists)
            _city["at"] = at
            atomic_write_json(CITY_CACHE, {"at": at, "lists": lists})
    except Exception as e:
        print(f"City cameras: {e}", flush=True)
    finally:
        _city_lock.release()


def _city_lists():
    """{city: its own camera views} (Vancouver's and Richmond's, hub/traffic.py), each re-crawled daily in the
    background and kept on disk; a city missing from the cache is crawled at once."""
    if not _city["loaded"]:
        _city["loaded"] = True
        saved = read_json(CITY_CACHE, {}) or {}
        if not saved and CITY_CACHE.exists():                # being replaced this moment: read it again
            time.sleep(0.5)
            saved = read_json(CITY_CACHE, {}) or {}
        if not saved and not CITY_CACHE.exists() and COV_CACHE.exists():
            old = read_json(COV_CACHE, {}) or {}
            saved = {"at": {}, "lists": {"Vancouver": old.get("list") or []}}
        at = saved.get("at")
        _city["at"] = at if isinstance(at, dict) else {}
        _set_lists(saved.get("lists") or {})
    now = time.time()
    stale = _stale_cities(now)
    if stale and now - _city["tried"] > 3600 and _city_lock.acquire(blocking=False):
        _city["tried"] = now                                 # a failed crawl is retried an hour later, not per request
        threading.Thread(target=_city_refresh, args=(stale,), name="city-cams", daemon=True).start()
    return _city["lists"]


@app.get("/api/webcams/traffic")
def webcams_traffic():
    """Traffic cameras for the Webcams page: DriveBC's by bridge or tunnel and by city, with the cities' own
    intersection cameras (Vancouver, Richmond, Surrey, Langley, SFU on Burnaby Mountain) in their tabs (hub/traffic.py)."""
    from hub.jobs import drivebc
    d = traffic.build(_dbc_list(), drivebc.fold)
    extra = 0
    for city, cams in _city_lists().items():
        if not cams:
            continue
        tab = next((c for c in d["cities"] if c["city"] == city), None)
        if tab is None:
            tab = {"city": city, "cams": []}
            d["cities"].insert(0, tab)
        tab["cams"] = sorted(tab["cams"] + cams, key=lambda x: x["name"].lower())
        extra += len(cams)
    rank = {n: i for i, n in enumerate(traffic.HOME)}
    d["cities"].sort(key=lambda c: (rank.get(c["city"], len(traffic.HOME)), c["city"] == "Elsewhere", c["city"]))
    return {**d, "count": d["count"] + extra, "fetched": _webcams["at"],
            "city_cams": {c: len(v) for c, v in _city["lists"].items()}, "crawling": _city_lock.locked()}


_dbc_thumbs: dict = {}               # camera id -> (fetched at, jpeg); ~15 kB each, oldest dropped past THUMBS_MAX
THUMBS_MAX = 2600                    # Vancouver ~860, Richmond ~600, Surrey ~680 city cameras, plus DriveBC
_dbc_thumb_failed: dict = {}         # camera id -> when its fetch failed (not retried for a minute)
_dbc_thumb_slots = threading.Semaphore(6)   # DriveBC fetches at once (a city tab asks for ~100 thumbnails)


def _thumb_jpeg(frame):
    im = Image.fromarray(frame)
    out = io.BytesIO()
    im.resize((320, round(im.height * 320 / im.width))).save(out, "JPEG", quality=80)
    return out.getvalue()


@app.get("/api/webcams/cov/{stem}.jpg")
def webcams_cov_thumb(stem: str):
    """Older thumbnail path for City of Vancouver cameras (lists cached before Richmond joined)."""
    return webcams_city_thumb("cov-" + stem)


@app.get("/api/webcams/city/{key}.jpg")
def webcams_city_thumb(key: str):
    """A city camera's latest image (Vancouver "cov-…", Richmond "rmd-…", Surrey "sry-…", Langley "tol-…", SFU "sfu-…"), 320 px wide, cached a minute. Only
    cameras on the cities' own lists: never an arbitrary URL."""
    _city_lists()
    url = _city["urls"].get(key)
    if not url:
        raise HTTPException(404, "no such city camera")
    hit = _dbc_thumbs.get(key)
    if not hit or time.time() - hit[0] > 60:
        if time.time() - _dbc_thumb_failed.get(key, 0) < 60:
            raise HTTPException(404, f"{key} isn't answering")
        if not _dbc_thumb_slots.acquire(timeout=20):
            raise HTTPException(503, "busy")
        try:
            frame, _ = webcams.fetch_still("image", url)
        except Exception as e:
            _dbc_thumb_failed[key] = time.time()
            raise HTTPException(502, f"{key}: {e}")
        finally:
            _dbc_thumb_slots.release()
        while len(_dbc_thumbs) >= THUMBS_MAX:
            _dbc_thumbs.pop(next(iter(_dbc_thumbs)))       # dicts keep insertion order: the oldest goes
        _dbc_thumbs.pop(key, None)
        hit = _dbc_thumbs[key] = (time.time(), _thumb_jpeg(frame))
    return Response(hit[1], media_type="image/jpeg", headers={"Cache-Control": "max-age=60"})


@app.get("/api/webcams/drivebc/{cid}.jpg")
def webcams_drivebc_thumb(cid: int):
    """A DriveBC camera's latest image, 320 px wide, cached a minute (the page's thumbnails)."""
    hit = _dbc_thumbs.get(cid)
    if not hit or time.time() - hit[0] > 60:
        if time.time() - _dbc_thumb_failed.get(cid, 0) < 60:
            raise HTTPException(404, f"DriveBC camera {cid} isn't answering")
        if not _dbc_thumb_slots.acquire(timeout=20):
            raise HTTPException(503, "busy")
        try:
            frame, _ = webcams.fetch_still("drivebc", webcams.DRIVEBC_IMAGE.format(id=cid), meta=False)
        except Exception as e:
            _dbc_thumb_failed[cid] = time.time()
            raise HTTPException(502, f"DriveBC camera {cid}: {e}")
        finally:
            _dbc_thumb_slots.release()
        while len(_dbc_thumbs) >= THUMBS_MAX:
            _dbc_thumbs.pop(next(iter(_dbc_thumbs)))       # dicts keep insertion order: the oldest goes
        _dbc_thumbs.pop(cid, None)
        hit = _dbc_thumbs[cid] = (time.time(), _thumb_jpeg(frame))
    return Response(hit[1], media_type="image/jpeg", headers={"Cache-Control": "max-age=60"})


_grid_cache: dict = {}               # (source, w, h, crop) -> (fetched at, tile or None): the finished tiles, a minute each
GRID_STILL_SEC = 60.0
GRID_CACHE_MAX = 4000                # tiles kept (~15 KB each at full width; the stills themselves are never kept)
GRID_MAX = 1000                      # cameras a grid fetches (the wall's and the panel's preview alike)


GRID_FROZEN_SEC = 2 * 3600          # a still that hasn't changed for this long is a broken camera
GRID_DARK, GRID_FLAT = 12.0, 4.0     # mean level below this: black; spread below this: one flat colour (a placeholder)


def grid_usable(frame, taken=None, now=None):
    """Is this still worth a tile? Not when it's black, one flat colour (an "offline" card), or frozen for hours.
    Night pictures are dark but not black: street lights and headlights keep them well above GRID_DARK."""
    import numpy as np
    if frame is None:
        return False
    if taken is not None and (now or time.time()) - taken > GRID_FROZEN_SEC:
        return False
    sample = frame[::6, ::6].astype(np.float32)
    return float(sample.mean()) >= GRID_DARK and float(sample.std()) >= GRID_FLAT


def _grid_still(src):
    """A grid camera's latest still, or None when it isn't a still or is black, blank or frozen (raises when the
    fetch fails)."""
    try:
        kind, url = webcams.check_source(src)
    except webcams.SourceError:
        return None
    if kind not in webcams.STILLS:
        return None                                        # the grid shows stills only
    frame, taken = webcams.fetch_still(kind, url, meta=False)
    return frame if grid_usable(frame, taken) else None


def _grid_tile(cam, w, h):
    """A grid camera's still as a w×h tile (cached a minute), or None when it's down or unusable. Only the tiles are
    cached: a full-size still is ~0.7 MB, and a grid can have hundreds of cameras."""
    cam = cam or {}
    crop = cam.get("crop")
    key = (_src_key(cam.get("source")), w, h, tuple(sorted(crop.items())) if isinstance(crop, dict) else None)
    hit = _grid_cache.get(key)
    if hit and time.time() - hit[0] < GRID_STILL_SEC:
        return hit[1]
    try:
        frame = _grid_still(cam.get("source"))
        tile = None if frame is None else webcams.render(frame, w, h, cam.get("crop"))
    except Exception:
        tile = hit[1] if hit else None                     # a hiccup: the last picture will do
    if len(_grid_cache) >= GRID_CACHE_MAX:
        old = time.time() - GRID_STILL_SEC
        for k in [k for k, v in list(_grid_cache.items()) if v[0] < old]:
            _grid_cache.pop(k, None)
        if len(_grid_cache) >= GRID_CACHE_MAX:
            _grid_cache.clear()
    _grid_cache[key] = (time.time(), tile)
    return tile


GRID_FETCH_SEC = 10.0                # cameras that haven't answered by then are left out of this build (their stills
                                     # keep loading into the cache, so the next build has them)


def _grid_tiles(cams, w, h, budget=GRID_FETCH_SEC):
    """Each grid camera's still as a w×h tile (a centred crop), fetched 12 at a time; None where it's down, unusable
    or hasn't answered within ``budget`` seconds."""
    from concurrent.futures import ThreadPoolExecutor, wait

    ex = ThreadPoolExecutor(12)
    futs = [ex.submit(_grid_tile, c, w, h) for c in cams]
    wait(futs, timeout=budget)
    ex.shutdown(wait=False)                                 # stragglers finish into the tile cache
    return [f.result() if f.done() and not f.exception() else None for f in futs]


def _still_ok(cam):
    try:
        return isinstance(cam, dict) and webcams.check_source(cam.get("source"))[0] in webcams.STILLS
    except webcams.SourceError:
        return False


def _src_key(src):
    src = src or {}
    return (src.get("type"), src.get("id") if src.get("type") == "drivebc" else src.get("url"))


MI_KM = 1.609344


def _near_matches(near):
    """Every camera within the radius of the point, nearest first: the cities' intersection cameras, and DriveBC's
    highway ones too when near["kinds"] is "all". [{"name", "source", "thumb", "km", "lat", "lon", "credit"}]"""
    try:
        lat, lon = float(near["lat"]), float(near["lon"])
        km = float(near.get("radius") or 1) * (MI_KM if near.get("unit") == "mi" else 1.0)
    except (KeyError, TypeError, ValueError):
        return []
    pool = [c for cams in _city_lists().values() for c in cams]
    if near.get("kinds") == "all":
        from hub.jobs import drivebc
        try:
            pool += [{**c, "source": {"type": "drivebc", "id": c["id"]}, "credit": "DriveBC"}
                     for city in traffic.build(_dbc_list(), drivebc.fold)["cities"] for c in city["cams"]]
        except HTTPException:
            pass                                               # DriveBC is down: the cities' cameras still count
    out = []
    for c in pool:
        if c.get("lat") is None or not c.get("source"):
            continue
        d = traffic._km((lat, lon), (float(c["lat"]), float(c["lon"])))
        if d <= km:
            out.append((d, {"name": c.get("name") or "", "source": c["source"], "thumb": c.get("thumb") or "",
                            "km": round(d, 2), "lat": c["lat"], "lon": c["lon"], "credit": c.get("credit") or ""}))
    out.sort(key=lambda t: t[0])
    return [c for _, c in out]


def _grid_list(g):
    """The grid's cameras: the ones added by hand (in their order), then every nearby one not already there
    (nearest first) when the grid's "near" is on. Still cameras only, at most GRID_MAX."""
    g = g if isinstance(g, dict) else {}
    cams = [c for c in (g.get("cams") if isinstance(g.get("cams"), list) else []) if _still_ok(c)]
    near = g.get("near") if isinstance(g.get("near"), dict) else {}
    if near.get("on"):
        have = {_src_key(c["source"]) for c in cams}
        cams += [c for c in _near_matches(near) if _src_key(c["source"]) not in have]
    return cams[:GRID_MAX]


_grid_ready: dict = {}               # (w, h, the cameras) -> [built at, reply bytes, refreshing]: the last tiles, kept ready
GRID_READY_SEC = 50.0                # older than this: served at once, and rebuilt in the background for next time
GRID_READY_MAX = 300.0               # older than this: too old to show, built afresh before answering
_grid_ready_lock = threading.Lock()


def _grid_build(cams, w, h):
    """The reply bytes: only the cameras with a usable picture (down, black, blank and frozen ones are left out)."""
    from display.modules.webcam import pack_grid
    pairs = [(t, str(c.get("name") or "")) for t, c in zip(_grid_tiles(cams, w, h), cams) if t is not None]
    return pack_grid([t for t, _ in pairs], w, h, [n for _, n in pairs])


def _grid_reply(g, w, h):
    """Every tile at once. The first time a grid is asked for, it's built now; after that the last build is
    answered at once (the wall never waits on the cameras) and rebuilt in the background when it's getting old;
    one more than a few minutes old is rebuilt before answering."""
    w, h = max(8, min(448, int(w or 64))), max(8, min(192, int(h or 64)))
    cams = _grid_list(g)
    key = (w, h, tuple(_src_key(c.get("source")) + (str(c.get("name") or ""),) for c in cams))
    with _grid_ready_lock:
        hit = _grid_ready.get(key)
        if hit is not None and time.time() - hit[0] > GRID_READY_MAX and not hit[2]:
            hit = None
        stale = hit is not None and time.time() - hit[0] > GRID_READY_SEC and not hit[2]
        if stale:
            hit[2] = True
    if hit is None:
        body = _grid_build(cams, w, h)
        with _grid_ready_lock:
            if len(_grid_ready) > 16:
                _grid_ready.clear()
            _grid_ready[key] = [time.time(), body, False]
        return Response(body, media_type="application/octet-stream")
    if stale:
        def rebuild():
            try:
                body = _grid_build(cams, w, h)
                with _grid_ready_lock:
                    _grid_ready[key] = [time.time(), body, False]
            except Exception as e:
                print(f"camera grid: {e}", flush=True)
                hit[2] = False
        threading.Thread(target=rebuild, name="grid-tiles", daemon=True).start()
    return Response(hit[1], media_type="application/octet-stream")


@app.get("/api/webcams/grid.bin")
def webcams_grid_bin(w: int = 64, h: int = 64):
    """The camera grid's tiles for the wall (display/modules/webcam.py CameraGrid), from the saved config."""
    return _grid_reply(((config.load().get("modules") or {}).get("webcams") or {}).get("grid"), w, h)


@app.post("/api/webcams/grid.bin")
def webcams_grid_bin_for(payload: dict = Body(default={})):
    """The tiles for the grid the wall sends ({"w", "h", "grid": {"cams", "near"}}): its own config's grid, which may
    be an unsaved preview the hub's saved config doesn't have yet. The reply names every camera (nearby ones come
    from the hub's camera lists, which the wall doesn't have)."""
    return _grid_reply(payload.get("grid"), payload.get("w"), payload.get("h"))


@app.get("/api/webcams/home")
def webcams_home():
    """The wall's home point (the weather alerts' home), for the grid's "near" setting."""
    from hub.bc import common
    lon, lat = common.home(config.load())
    return {"lat": lat, "lon": lon}


@app.get("/api/webcams/near")
def webcams_near(lat: float, lon: float, radius: float = 1.0, unit: str = "km", kinds: str = "city"):
    """The cameras a grid's "near" setting would add, nearest first (the panel lists them)."""
    radius = max(0.05, min(50.0, radius))
    got = _near_matches({"lat": lat, "lon": lon, "radius": radius, "unit": unit, "kinds": kinds})
    return {"count": len(got), "cams": got[:GRID_MAX], "capped": len(got) > GRID_MAX, "max": GRID_MAX}


@app.post("/api/webcams/grid/preview.png")
def webcams_grid_preview(payload: dict = Body(default={})):
    """The camera grid as the wall would show it once every tile has filled: {"grid", "shape", "scale"}."""
    from display.gfx import Canvas
    from display.modules.webcam import CameraGrid
    zones = _webcam_zones()
    shape = payload.get("shape") if payload.get("shape") in zones else "full"
    g = payload.get("grid") if isinstance(payload.get("grid"), dict) else {}
    w, h = zones[shape]
    cams = _grid_list(g)
    grid = CameraGrid("http://127.0.0.1:1", {**g, "cams": cams})
    geo = grid.geometry((0, 0, w, h))
    tiles = _grid_tiles(cams, geo[0], geo[1])
    c = Canvas(w, h)
    if tiles:
        grid.draw_tiles(c, tiles, geo, len(tiles) * grid.interval + 1)
    scale = max(1, min(6, int(payload.get("scale") or 3)))
    im = Image.fromarray(c.buf.copy()).resize((w * scale, h * scale), Image.NEAREST)
    out = io.BytesIO()
    im.save(out, "PNG")
    return Response(out.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/bc/roads/cameras")
def bc_road_cameras():
    """Every DriveBC camera in and around the DriveBC screen's area (where it is, which way it looks), what is on
    the roads, and which camera each event and sample uses — for the panel's camera map."""
    from hub.bc import common
    from hub.jobs import drivebc
    _dbc_list()
    st = common.settings(config.load(), "road_alerts", drivebc.DEFAULTS)
    lo0, la0, lo1, la1 = [float(v) for v in st.get("bbox") or drivebc.DEFAULTS["bbox"]]
    m = 0.08
    cams = []
    for c in _webcams["list"]:
        lo, la = ((c.get("location") or {}).get("coordinates") or [None, None])[:2]
        if lo is None or not (lo0 - m <= lo <= lo1 + m and la0 - m / 2 <= la <= la1 + m / 2):
            continue
        cams.append({"id": c["id"], "name": drivebc.fold(c.get("name") or ""), "caption": drivebc.fold(c.get("caption") or ""),
                     "lat": la, "lon": lo, "orientation": c.get("orientation") or "", "highway": c.get("highway_display") or "",
                     "on": bool(c.get("is_on", True)) and not c.get("marked_stale"), "delayed": bool(c.get("marked_delayed")),
                     "updated": c.get("last_update_modified"),
                     "image": "https://www.drivebc.ca" + ((c.get("links") or {}).get("imageDisplay") or f"/images/{c['id']}.jpg")})
    data = read_json(paths.DATA_DIR / "drivebc.json", {}) or {}
    from display.modules.bc_roads import SAMPLES, word_of

    def event(it, label):
        pts = (it.get("geometry") or {}).get("points") or []
        return {"label": label, "kind": word_of(it), "closed": bool(it.get("closed")), "points": pts,
                "cam": (it.get("cam") or {}).get("id"), "km": (it.get("cam") or {}).get("km")}
    events = [event(it, f"{it.get('road')} {it.get('direction') or ''}".strip()) for it in data.get("items") or []]
    samples = [dict(event(dict(v["item"], cam=(data.get("sample_cams") or {}).get(k_)), v["label"]), sample=k_)
               for k_, v in SAMPLES.items()]
    return {"cameras": cams, "events": events, "samples": samples, "bbox": [lo0, la0, lo1, la1],
            "fetched": _webcams["at"]}


@app.get("/api/bc/{src}/preview.png")
def bc_preview(src: str, theme: str = "", sample: str = "", at: float = 4.0, scale: int = 2, real: int = 0, s: str = "",
               page: int = -1):
    """A frame of the screen ``at`` seconds into an item — a sample, or the first real item with real=1; ``page`` =
    which page of the sample's first showing (see full_pages)."""
    import json as _json
    mod, job_mod, job, section = _bc(src)
    from hub.bc import common
    settings = common.settings(config.load(), section, job_mod.DEFAULTS)
    if s:
        try:
            settings.update(_json.loads(s))
        except ValueError:
            raise HTTPException(400, "s must be JSON")
    theme = theme if theme in mod.THEMES else (settings.get("theme") if settings.get("theme") in mod.THEMES else mod.Screen.DEFAULT_THEME)
    data = read_json(paths.DATA_DIR / f"{mod.Screen.DATA}.json", {}) or {}
    smp = None if real else (sample if sample in mod.SAMPLES else next(iter(mod.SAMPLES)))
    kw = {"page": page} if page >= 0 and smp and src == "roads" else {}
    frame = mod.render_preview(theme, smp, max(0.0, at), settings, data, str(paths.DATA_DIR), **kw)
    k = max(1, min(4, scale))
    img = Image.fromarray(frame).resize((frame.shape[1] * k, frame.shape[0] * k), Image.NEAREST)
    bio = io.BytesIO()
    img.save(bio, "PNG", compress_level=1)
    return Response(bio.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


_HALF_ZONE = (225, 41, 223, 134)
_HALF_NAMES = {"summary": "Summary", "map": "Map", "lead": "All events", "camera": "Camera", "cities": "Cities", "ocean": "Pacific"}


def _half_panel(src, sample, theme, at=2.0):
    """A half-width panel on a sample, laid out (its pages built) as the wall would at ``at`` seconds before now."""
    from display.gfx import Canvas
    from display.modules import bc_kit as k
    if src == "weather":
        from display.modules.alert_samples import SAMPLES
        from display.modules.weather_alert import AlertScreen
        from display.modules.weather_alert_half import WeatherAlerts
        data = read_json(paths.DATA_DIR / "alerts.json", {}) or {}
        scr = WeatherAlerts(AlertScreen({}, k.StaticLoader({"alerts": data}, str(paths.DATA_DIR))), str(paths.DATA_DIR))
        sample = sample if sample in SAMPLES else next(iter(SAMPLES))
    else:
        mod, job_mod, job, section = _bc(src)
        data = read_json(paths.DATA_DIR / f"{mod.Screen.DATA}.json", {}) or {}
        scr = mod.Screen({}, k.StaticLoader({mod.Screen.DATA: data}, str(paths.DATA_DIR)), str(paths.DATA_DIR))
        sample = sample if sample in mod.SAMPLES else next(iter(mod.SAMPLES))
    hp = k.HalfPanel(scr)
    c = Canvas(448, 192)
    now = time.time()
    entry = {"sample": sample, "page_sec": 10 ** 6}
    if theme:
        entry["theme"] = theme
    hp.draw(c, _HALF_ZONE, entry, True, now=now - at)
    return hp, c, entry, now


@app.get("/api/bc/{src}/half_pages")
def bc_half_pages(src: str, sample: str = "", theme: str = ""):
    """The pages of the half-width version (for the preview's page buttons): name and how long each holds."""
    hp, _, _, _ = _half_panel(src, sample, theme)
    out, n_text = [], 0
    for pg in hp.pages:
        kind = pg[0]
        if kind == "text":
            n_text += 1
            name = f"Text {n_text}"
        else:
            name = _HALF_NAMES.get(kind, kind.replace("_", " ").title())
        out.append({"name": name, "kind": kind, "hold": round(float(hp._dwell(pg)), 1)})
    if sum(1 for p in out if p["kind"] == "text") == 1:
        for p in out:
            if p["kind"] == "text":
                p["name"] = "Text"
    return {"pages": out, "total": round(sum(p["hold"] for p in out), 1)}


@app.get("/api/bc/{src}/half.png")
def bc_half(src: str, sample: str = "", page: int = 0, at: float = 2.0, scale: int = 2, theme: str = ""):
    """The half-width version (right zone) of a sample in a theme's look, one of its pages, ``at`` seconds into it;
    ``src`` "weather" = the ECCC alerts (weather themes t01…)."""
    hp, c, entry, now = _half_panel(src, sample, theme, at)
    zone = _HALF_ZONE
    if hp.pages:
        hp.idx, hp.t0 = page % len(hp.pages), now - at
        c.buf[:] = 0
        hp.draw(c, zone, entry, False, now=now)
    frame = c.buf[zone[1]:zone[1] + zone[3], zone[0]:zone[0] + zone[2]]
    kk = max(1, min(4, scale))
    img = Image.fromarray(frame).resize((frame.shape[1] * kk, frame.shape[0] * kk), Image.NEAREST)
    bio = io.BytesIO()
    img.save(bio, "PNG", compress_level=1)
    return Response(bio.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store", "X-Pages": str(len(hp.pages))})


@app.post("/api/bc/{src}/test")
def bc_test(src: str, payload: dict = Body(default={})):
    """Play a sample (or the real items with real=true) on the wall once, in a theme, then back to the rotation."""
    mod, _, _, _ = _bc(src)
    entry = {"panel": mod.Screen.PANEL + ("_half" if payload.get("half") else ""), "advance": "cycles", "cycles": 1, "max_sec": 300}
    if payload.get("theme"):
        entry["theme"] = payload["theme"]                # (the half-width version is drawn in that theme's look)
    if not payload.get("real"):
        entry["sample"] = payload.get("sample") or next(iter(mod.SAMPLES))
        entry["pages"] = True                            # a test shows the first-showing pages too
    return _display_call("/play", {"side": "right", "entry": entry, "hold": False})


@app.post("/api/bc/{src}/run")
def bc_run(src: str):
    _, _, job, _ = _bc(src)
    _sched().run_now(job)
    return {"ok": True}


# --- font audit -------------------------------------------------------------
_font_png = {}


@app.get("/api/fonts")
def fonts_list():
    from hub import fonts
    return {"fonts": fonts.catalog(), "samples": {"latin": fonts.LATIN_SAMPLE, "jp": fonts.JP_SAMPLE}}


@app.get("/api/fonts/preview.png")
def fonts_preview(path: str, text: str = "", jp: str = "", scale: int = 2, fg: str = "ffffff", width: int = 448, height: int = 0):
    """``path`` (under assets/fonts) rendering the samples, wrapped to the wall's width."""
    from hub import fonts
    full = Path(fonts.ROOT) / path
    if ".." in path or not full.is_file() or full.suffix != ".bdf":
        raise HTTPException(404, "no such font")
    key = (path, full.stat().st_mtime, text, jp, scale, fg, width, height)
    png = _font_png.get(key)
    if png is None:
        try:
            col = tuple(int(fg[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            col = (255, 255, 255)
        png = fonts.render_png(path, text or fonts.LATIN_SAMPLE, jp, scale, max(64, min(448, width)), col, height or None)
        if len(_font_png) > 1500:
            _font_png.clear()
        _font_png[key] = png
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/shields/preview.png")
def shield_preview(route: str, kind: str = "auto", h: int = 24, scale: int = 2, style: str = "pixel"):
    """A highway shield as the wall draws it, for the caption's shield picker: the pixel art
    (display/modules/shields.py), or with style=real the route's real sign (hub/shield_signs.py, fetched and rendered
    the first time it's asked for, so it's ready for the wall; 404 when Commons has no sign for the route)."""
    from display.gfx import Canvas
    from display.modules import shields, webcam_caption
    from display.modules.webcam_caption import SHIELD_KINDS, SHIELD_MAX, SHIELD_MIN
    route = route.strip()[:8]
    if not shields.has_number(route) or kind not in SHIELD_KINDS or style not in webcam_caption.SHIELD_STYLES:
        raise HTTPException(400, "a route with a number, a known kind and style needed")
    h, scale = max(SHIELD_MIN, min(SHIELD_MAX, h)), max(1, min(4, scale))
    kd = None if kind == "auto" else kind
    if style == "real":
        from hub import shield_signs
        try:
            ok = shield_signs.ensure(route, kd)
        except TimeoutError as e:                           # another sign is being fetched: asked again later
            raise HTTPException(503, str(e)) from e
        except Exception as e:                              # noqa: BLE001 (Commons down, resvg missing…)
            raise HTTPException(502, f"couldn't get the sign: {e}") from e
        sign = webcam_caption.real_sign(route, kd, h) if ok else None
        if sign is None:
            raise HTTPException(404, "no real sign for this route")
        img = Image.fromarray(sign)                         # (h, w, 4) uint8: RGBA
    else:
        w = shields.width(route, h, kd)
        c = Canvas(w, h)
        c.buf[:] = (255, 0, 255)                            # a colour no sign uses: what's still magenta is clear
        shields.draw(c, 0, 0, route, h=h, kind=kd)
        img = Image.fromarray(c.buf).convert("RGBA")
        clear = (c.buf == (255, 0, 255)).all(axis=2)
        img.putalpha(Image.fromarray(((~clear) * 255).astype("uint8")))
    w = img.width
    bio = io.BytesIO()
    img.resize((w * scale, h * scale), Image.NEAREST).save(bio, "PNG")
    return Response(bio.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.post("/api/fonts/wall")
def fonts_wall(payload: dict = Body(...)):
    """Show one font (or up to 12 stacked) on the wall until "Back to rotation"."""
    body = {"side": "right", "panel": "font_specimen", "duration_sec": float(payload.get("duration_sec") or 600),
            "fonts": list(payload.get("fonts") or [])[:12], "text": payload.get("text") or "", "jp": payload.get("jp") or ""}
    return _display_call("/override", body)


# --- live consoles ------------------------------------------------------------
@app.get("/api/logs")
def logs(hub: int = 0, display: int = 0):
    """New console lines since each cursor, for the server (hub) and the Pi (display), plus running jobs."""
    try:
        r = requests.get(pi.display_url(f"/logs?after={max(0, display)}"), timeout=2)
        disp = r.json() if r.ok else {"error": f"display answered {r.status_code}"}
    except Exception as e:
        disp = {"error": f"display unreachable: {str(e)[:120]}"}
    jobs = _sched().snapshot()
    running = []
    for k, v in jobs.items():
        if v.get("running"):
            pr = v.get("progress") or {}
            running.append({"name": k, "label": v["label"], "duration_last": v.get("duration"),
                            "started": pr.get("started"), "done": pr.get("done"), "total": pr.get("total"),
                            "step": pr.get("label", "")})
    return {"now": time.time(), "hub": logring.ring.since(max(0, hub)), "display": disp, "running": running}


# --- status ------------------------------------------------------------------
def _git_head():
    """The commit this hub process runs (read once: a deploy restarts the hub)."""
    import subprocess
    try:
        out = subprocess.run(["git", "-C", str(paths.REPO_DIR), "log", "-1", "--format=%H%x00%s%x00%ct"],
                             capture_output=True, text=True, timeout=5).stdout.strip().split("\0")
        return {"commit": out[0], "subject": out[1], "committed_at": int(out[2])}
    except (OSError, IndexError, ValueError, subprocess.SubprocessError):
        return {}


RUNNING = _git_head()


@app.get("/api/status")
def status():
    cfg = config.load()
    in_sync, applied = pi.display_in_sync(cfg)
    return {
        "now": time.time(),
        "jobs": _sched().snapshot(),
        "sync": sync.last_result,
        "config_revision": cfg.get("revision"),
        "display": {"in_sync": in_sync, "applied_revision": applied.get("revision"),
                    "applied_at": applied.get("at")},
        "code": {"running": RUNNING, "last_deploy": read_json(paths.STATE_DIR / "deploy.json", {}) or {}},
    }


@app.post("/api/jobs/{name}/run")
def run_job(name: str):
    try:
        _sched().run_now(name)
    except KeyError:
        raise HTTPException(404, f"unknown job {name}")
    return {"ok": True}


# --- display (Pi) ------------------------------------------------------------
@app.post("/api/display/apply")
def display_apply():
    return pi.apply_to_display(restart=True)


@app.get("/api/display/status")
def display_status():
    return pi.display_status()


@app.get("/api/display/frame.png")
def display_frame(scale: int = 2):
    try:
        r = requests.get(pi.display_url(f"/frame.png?scale={max(1, min(6, scale))}"), timeout=4)
    except Exception as e:
        raise HTTPException(502, f"display unreachable: {e}")
    if r.status_code != 200 or r.headers.get("content-type") != "image/png":
        raise HTTPException(404, "live mirror needs the v2 display")
    return Response(r.content, media_type="image/png", headers={"Cache-Control": "no-store"})


def _display_call(path, payload=None):
    try:
        code, body = pi.display_post(path, payload)
    except Exception as e:
        raise HTTPException(502, f"display unreachable: {e}")
    if code >= 400:
        raise HTTPException(code, body.get("error") or str(body))
    return body


@app.post("/api/display/override")
def display_override(payload: dict = Body(...)):
    return _display_call("/override", payload)


@app.post("/api/display/override/clear")
def display_override_clear(payload: dict = Body(default={})):
    return _display_call("/override/clear", payload)


@app.post("/api/display/play")
def display_play(payload: dict = Body(...)):
    """{"side", "entry": {rotation entry}, "hold": false, "ttl_sec"}: show an entry now, once or held."""
    return _display_call("/play", payload)


@app.post("/api/display/rotation")
def display_rotation(payload: dict = Body(...)):
    """{"side"?, "action": "next"|"prev"|"stop"|"hold"|"release"}, or {"side", "action": "goto", "index", "entry"}:
    jump the rotation to that entry (it plays now and the rotation carries on after it)."""
    return _display_call("/rotation", payload)


@app.post("/api/display/preview")
def display_preview(payload: dict = Body(...)):
    """{"overlay": {partial config}, "ttl_sec": 120} — shown on the display until kept, reverted, or expired."""
    overlay = payload.get("overlay")
    if not isinstance(overlay, dict):
        raise HTTPException(400, "overlay must be an object")
    cams = (((overlay.get("modules") or {}).get("webcams") or {}).get("cams") or {})
    shown = {cid: cam for cid, cam in (cams.items() if isinstance(cams, dict) else []) if isinstance(cam, dict) and _wants_wx(cam)}
    from hub.jobs import webcam_weather as job
    missing = {cid: job.point(cam) for cid, cam in shown.items() if job.point(cam) and not _webcam_wx(cid, cam)}
    if missing:
        _fetch_wx({pt: pt for pt in set(missing.values())}, timeout=8)   # one request for all of them
    for cid, cam in shown.items():
        wx = _webcam_wx(cid, cam)
        if wx:
            cam["_wx"] = wx                                # previews only: never saved (display/modules/webcam.py)
    return _display_call("/preview", {"overlay": overlay, "ttl_sec": int(payload.get("ttl_sec", 120))})


@app.post("/api/display/preview/clear")
def display_preview_clear():
    return _display_call("/preview/clear")


@app.post("/api/display/headlines/jump")
def display_headlines_jump(payload: dict = Body(default={})):
    """{"delta": 1|-1} or {"index": n}: show another headline category now (if headlines
    are off-screen: that one page, then the rotation resumes; or pinned with "hold_sec")."""
    return _display_call("/headlines/jump", payload)


# --- webcams (live video; hub/webcams.py decodes, the Pi only blits) ------------
def _webcam_zones():
    """Pixel size of each place a webcam can play (display/main.py Screen: ticker 44 px + 1, bottom strip 17).
    "tall": full width over the ticker too, down to the clock strip."""
    L = (config.load().get("layout") or {})
    w, h = L.get("width", 448), L.get("height", 192)
    main_h = h - 17 - (L.get("top_bar_height", 44) + 1)
    cal_w = int(w * L.get("calendar_split_ratio", 0.66))
    return {"left": [cal_w, main_h], "right": [w - cal_w - 1, main_h], "full": [w, main_h], "tall": [w, h - 17],
            "wall": [w, h]}


def _webcam_feed(src, fps):
    try:
        return webcams.feed(src, fps)
    except webcams.SourceError as e:
        raise HTTPException(503 if "already decoding" in str(e) else 400, str(e))


_wx_cache: dict = {}                 # (lat, lon) -> weather fetched for previews of unsaved locations
_wx_failed: dict = {}                # (lat, lon) -> when a fetch failed (not retried for a minute)
_wx_latest: dict = {}                # webcam id -> the location last asked about (typing fetches only the last)
_wx_pending: set = set()
WX_TTL, WX_RETRY, WX_SETTLE = 900.0, 60.0, 1.0


def _wants_wx(cam):
    show = ((cam or {}).get("overlay") or {}).get("show") or {}
    return bool(show.get("weather") or show.get("sun"))


def _webcam_wx(cam_id, cam, block=False):
    """The weather a caption preview should show: the job's data when it's for this location, else a recent fetch
    for it. Never waits unless block: a missing location is fetched in the background (after WX_SETTLE, and only if
    it's still the location asked about, so typing coordinates doesn't fetch every keystroke) and turns up on the
    preview's next refresh. None without a location, or while fetching."""
    from hub.jobs import webcam_weather as job
    pt = job.point(cam or {})
    if not pt:
        return None
    saved = (read_json(paths.DATA_DIR / "webcam_weather.json", {}) or {}).get(str(cam_id or ""))
    if saved and (saved.get("lat"), saved.get("lon")) in (pt, (None, None)):
        return saved
    hit = _wx_cache.get(pt)
    if hit and time.time() - hit.get("at", 0) < WX_TTL:
        return hit
    if time.time() - _wx_failed.get(pt, 0) < WX_RETRY:
        return None
    if block:
        _fetch_wx({pt: pt}, timeout=8)
        return _wx_cache.get(pt)
    _wx_latest[str(cam_id or "")] = pt
    if pt not in _wx_pending:
        _wx_pending.add(pt)

        def later():
            try:
                time.sleep(WX_SETTLE)
                if _wx_latest.get(str(cam_id or "")) == pt:
                    _fetch_wx({pt: pt}, timeout=8)
            finally:
                _wx_pending.discard(pt)
        threading.Thread(target=later, name="webcam-wx", daemon=True).start()
    return None


def _fetch_wx(points, timeout=8):
    """Fetch points into the cache in one request; failures are remembered for WX_RETRY."""
    from hub.jobs import webcam_weather as job
    try:
        got = job.fetch(points, timeout=timeout)
    except Exception:
        for pt in points.values():
            _wx_failed[pt] = time.time()
        return
    if len(_wx_cache) > 64:
        _wx_cache.clear()
    for pt in points.values():
        if pt in got:
            _wx_cache[pt] = got[pt]


@app.get("/api/webcams/status")
def webcams_status():
    return {"feeds": webcams.describe(), "zones": _webcam_zones(), "max_feeds": webcams.MAX_FEEDS,
            "js_runtime": bool(webcams.deno_path())}


@app.post("/api/webcams/probe")
def webcams_probe(payload: dict = Body(...)):
    """{"source": {...}} → {"ok", "title", "error"}: can the hub open this source? (YouTube: is it live?)"""
    try:
        kind, url = webcams.check_source(payload.get("source"))
        if kind in webcams.STILLS:
            try:
                frame, at = webcams.fetch_still(kind, url)
            except webcams.SourceError:
                raise
            except Exception as e:
                return {"ok": False, "error": f"couldn't fetch the image: {e}"[:300]}
            age = int((time.time() - at) // 60)
            return {"ok": True, "title": f"still image, {frame.shape[1]}×{frame.shape[0]}, taken {age} min ago"}
        _media, title = webcams.resolve(kind, url, fresh=True)
        return {"ok": True, "title": title}
    except webcams.SourceError as e:
        return {"ok": False, "error": str(e)}


async def _webcam_frames(f, make, fps=None):
    """Yield make(frame) for the feed's frames while the client stays (it holds the feed open). Woken by the feed
    on every frame (no polling, so the even timing survives), taking every n-th when the feed runs faster than fps."""
    loop = asyncio.get_running_loop()
    ev = asyncio.Event()
    f.attach()
    f.add_waiter(loop, ev)
    try:
        seq, t0 = 0, time.time()
        while True:
            ev.clear()
            step = max(1, round(max(1, f.fps) / fps)) if fps else 1
            if f.frame is not None and (seq == 0 or f.seq >= seq + step):
                seq, frame = f.seq, f.frame
                yield await asyncio.to_thread(make, frame, f)
                continue
            if f.state == "stopped":
                return
            if f.frame is None and time.time() - t0 > 60:
                return                                       # never came up; the client retries if it still cares
            try:
                await asyncio.wait_for(ev.wait(), 1.0)
            except asyncio.TimeoutError:
                pass
    finally:
        f.remove_waiter(loop, ev)
        f.detach()


def _num(v, default, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


@app.get("/api/webcams/frames")
async def webcams_frames(type: str, w: int, h: int, url: str = "", id: int | None = None, refresh: int | None = None,
                         fps: int = 15, x: float | None = None,
                         y: float | None = None, cw: float | None = None, b: float = 1.0, c: float = 1.0,
                         s: float = 1.0):
    """The Pi's feed: raw RGB frames already cropped and scaled to its zone (display/modules/webcam.py reads
    them), each behind a 12-byte header (hub/webcams.py pack)."""
    if not (8 <= w <= 448 and 8 <= h <= 192):
        raise HTTPException(400, "w/h out of range")
    f = _webcam_feed({"type": type, "url": url, "id": id, "refresh_sec": refresh}, fps)
    crop = {"x": x or 0, "y": y or 0, "w": cw} if cw else None
    colour = {"brightness": _num(b, 1, 0.2, 3), "contrast": _num(c, 1, 0.2, 3), "saturation": _num(s, 1, 0, 3)}

    def make(frame, feed):
        age = time.time() - (feed.image_t or feed.frame_t)      # stills: the image's age, for the caption
        return webcams.pack(webcams.render(frame, w, h, crop, colour), age * 1000)

    fps = max(1, min(30, fps))                                # the feed may decode faster for another viewer
    return StreamingResponse(_webcam_frames(f, make, fps=fps), media_type="application/octet-stream",
                             headers={"Cache-Control": "no-store"})


@app.get("/api/webcams/warm")
def webcams_warm(type: str, url: str = "", id: int | None = None, refresh: int | None = None, fps: int = 15):
    """Start (or keep alive) a webcam's feed before its screen comes up, so the screen opens on a picture instead of
    "loading". The display asks shortly before (display/modules/webcam.py warm); a feed outlives its last ask or viewer
    by GRACE_SEC. Never fails loudly: the screen itself reports a source that won't come up."""
    try:
        f = webcams.feed({"type": type, "url": url, "id": id, "refresh_sec": refresh}, max(1, min(30, fps)))
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}
    return {"ok": True, "state": f.state}


@app.get("/api/webcams/snapshot.jpg")
def webcams_snapshot(type: str, url: str = "", id: int | None = None):
    """A current still from any source (hub/webcams.py snapshot). Never waits for a grab: 503 + Retry-After while
    one is being made, 404 when the source failed recently."""
    try:
        jpg, state = webcams.snapshot({"type": type, "url": url, "id": id})
    except webcams.SourceError as e:
        raise HTTPException(400, str(e))
    if jpg is None:
        if state == "pending":
            return JSONResponse({"detail": "grabbing a frame"}, status_code=503, headers={"Retry-After": "4"})
        raise HTTPException(404, state)
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Snapshot": state})


_yt_thumbs: dict = {}               # video id -> (fetched at, jpeg bytes)
YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


@app.get("/api/webcams/thumb/{vid}.jpg")
def webcams_thumb(vid: str):
    """A YouTube live stream's current thumbnail, fetched by the hub (cached 10 min) for the panel's gallery."""
    if not YT_ID.match(vid):
        raise HTTPException(400, "bad video id")
    hit = _yt_thumbs.get(vid)
    if not hit or time.time() - hit[0] > 600:
        try:
            r = requests.get(f"https://i.ytimg.com/vi/{vid}/mqdefault_live.jpg", timeout=6)
            r.raise_for_status()
        except Exception as e:
            raise HTTPException(502, f"no thumbnail: {e}")
        if len(_yt_thumbs) > 200:
            _yt_thumbs.clear()
        hit = _yt_thumbs[vid] = (time.time(), r.content)
    return Response(hit[1], media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})


MJPEG = "multipart/x-mixed-replace; boundary=frame"


def _part(data, kind):
    return b"--frame\r\nContent-Type: " + kind + b"\r\nContent-Length: " + str(len(data)).encode() + b"\r\n\r\n" \
        + data + b"\r\n"


@app.get("/api/webcams/source.mjpeg")
async def webcams_source(type: str, url: str = "", id: int | None = None, refresh: int | None = None, fps: int = 15):
    """The whole source picture (what crops are cut from) as an MJPEG stream for the panel's crop editor."""
    f = _webcam_feed({"type": type, "url": url, "id": id, "refresh_sec": refresh}, fps)

    def make(frame, _feed):
        out = io.BytesIO()
        Image.fromarray(frame).save(out, "JPEG", quality=80)
        return _part(out.getvalue(), b"image/jpeg")

    return StreamingResponse(_webcam_frames(f, make, fps=8), media_type=MJPEG,
                             headers={"Cache-Control": "no-store"})


_webcam_previews: dict = {}          # token -> {"cam", "zone", "until"}


@app.post("/api/webcams/preview")
def webcams_preview_set(payload: dict = Body(...)):
    """{"token"?, "cam": {draft webcam}, "shape": "left|right|full|wall"} → {"token"}. The panel posts every edit
    to the same token; the open preview stream picks it up on its next frame."""
    cam, shape = payload.get("cam"), payload.get("shape", "full")
    zones = _webcam_zones()
    if not isinstance(cam, dict) or shape not in zones:
        raise HTTPException(400, "needs cam and shape")
    try:
        webcams.check_source(cam.get("source"))
    except webcams.SourceError as e:
        raise HTTPException(400, str(e))
    now = time.time()
    for k in [k for k, v in _webcam_previews.items() if v["until"] < now]:
        del _webcam_previews[k]
    token = str(payload.get("token") or "") or os.urandom(6).hex()
    if not re.fullmatch(r"[0-9a-f]{12}", token):
        raise HTTPException(400, "bad token")
    wx = _webcam_wx(payload.get("id"), cam) if _wants_wx(cam) else None
    _webcam_previews[token] = {"cam": cam, "id": str(payload.get("id") or ""), "wx": wx, "shape": shape, "zone": zones[shape],
                               "until": now + 3600,
                               "since": _webcam_previews.get(token, {}).get("since", now)}
    from display.modules import webcam as wc
    try:
        still = (cam.get("source") or {}).get("type") in webcams.STILLS
        dropped = wc.caption_dropped(cam, tuple(zones[shape]), wx, {"taken": time.time()} if still else None)
    except Exception:
        dropped = []
    return {"token": token, "zone": zones[shape], "dropped": dropped, "has_weather": bool(wx)}


@app.get("/api/webcams/preview/{token}.mjpeg")
async def webcams_preview(token: str):
    """What the wall would show for the token's draft webcam (crop, colour and caption), pixel for pixel,
    as a stream of PNGs (show it with image-rendering: pixelated)."""
    from display.modules import webcam as wc
    p = _webcam_previews.get(token)
    if not p:
        raise HTTPException(404, "unknown preview")
    cam = p["cam"]
    f = _webcam_feed(cam.get("source"), cam.get("fps", 15))
    t0 = time.time()

    def make(frame, _feed):
        cur = _webcam_previews.get(token) or p
        cam_now, (w, h) = cur["cam"], cur["zone"]
        q = wc.stream_query(cam_now, cur["shape"], w, h)
        crop = {"x": q.get("x", 0), "y": q.get("y", 0), "w": q["cw"]} if "cw" in q else None
        colour = {"brightness": q.get("b", 1), "contrast": q.get("c", 1), "saturation": q.get("s", 1)}
        if (cam_now.get("source") or {}) != (cam.get("source") or {}):
            frame = None                                     # source changed: the panel reopens the stream
        if time.time() - cur.get("wx_at", 0) > 20 and _wants_wx(cam_now):   # pick up fetched / newer weather
            cur["wx"], cur["wx_at"] = _webcam_wx(cur.get("id"), cam_now), time.time()
        wx = cur.get("wx")
        extra = {"taken": round(_feed.image_t)} if getattr(_feed, "image_t", None) else None
        img = wc.render_preview(cam_now, None if frame is None else webcams.render(frame, w, h, crop, colour),
                                (w, h), time.time() - t0, wx=wx, extra=extra)
        out = io.BytesIO()
        Image.fromarray(img).save(out, "PNG", compress_level=1)
        return _part(out.getvalue(), b"image/png")

    return StreamingResponse(_webcam_frames(f, make, fps=10), media_type=MJPEG,
                             headers={"Cache-Control": "no-store"})


# --- bridges now (DriveBC stills, hub/jobs/bridges.py) ---------------------------------------------------------
_dbc_cams = {"at": 0.0, "list": []}
METRO = (48.95, 49.45, -123.35, -122.15)        # lat min/max, lon min/max: Metro Vancouver and the border


@app.get("/api/bridges/state")
def bridges_state():
    """The bridges in use (config, or the job's defaults) and what the last run found."""
    from hub.jobs import bridges as job
    return {"cams": job.cams_of(config.load()), "defaults": job.DEFAULT_CAMS,
            "data": read_json(paths.DATA_DIR / "bridges.json", {}) or {}}


@app.get("/api/bridges/cameras")
def bridges_cameras():
    """DriveBC cameras in Metro Vancouver to pick from (cached 10 min)."""
    if time.time() - _dbc_cams["at"] > 600:
        try:
            raw = requests.get("https://www.drivebc.ca/api/webcams/", timeout=40).json()
        except Exception as e:
            if not _dbc_cams["list"]:
                raise HTTPException(502, f"DriveBC: {e}")
            raw = None
        if raw is not None:
            out = []
            for c in raw if isinstance(raw, list) else raw.get("results", []):
                lon, lat = ((c.get("location") or {}).get("coordinates") or [None, None])[:2]
                if lat is None or not (METRO[0] <= lat <= METRO[1] and METRO[2] <= lon <= METRO[3]):
                    continue
                out.append({"id": c.get("id"), "name": c.get("name") or "", "caption": c.get("caption") or "",
                            "on": bool(c.get("is_on")) and not c.get("marked_stale")})
            _dbc_cams.update(at=time.time(), list=sorted(out, key=lambda c: c["name"].lower()))
    return _dbc_cams["list"]


@app.get("/api/bridges/preview.png")
def bridges_preview(shape: str = "full", page: int = 0, scale: int = 3):
    """What the wall shows for a Bridges now entry, from the last job run."""
    from display.modules import bridges as mod
    zones = _webcam_zones()
    if shape not in zones:
        raise HTTPException(400, "shape must be left, right, full or wall")
    panel = {"full": "bridges_full", "wall": "bridges_wall"}.get(shape, "bridges")
    img = mod.render_preview(str(paths.DATA_DIR), tuple(zones[shape]), panel, elapsed=page * mod.PAGE_SEC + 0.5)
    im = Image.fromarray(img)
    s = max(1, min(6, scale))
    out = io.BytesIO()
    im.resize((im.width * s, im.height * s), Image.NEAREST).save(out, "PNG")
    return Response(out.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


# --- time zone map (hub/jobs/timezones.py, display/modules/tzmap.py) ------------------------------------------------
@app.get("/api/timezones/state")
def timezones_state():
    """Defaults, the rules in use, every zone's offset now, the coming clock changes and recent rule updates."""
    from display.modules import tzmap as mod
    data = read_json(paths.DATA_DIR / "tzmap.json", {}) or {}
    zones = [z["id"] for z in data.get("zones") or []]
    now = time.time()
    st = mod.state_at(data, now) if data.get("states") else None
    s = mod.settings((config.load().get("modules") or {}).get("timezones"))
    ccs = [z.get("cc", "") for z in data.get("zones") or []]
    listed = lambda i: ccs[i] in mod.COUNTRIES                  # Canada and the US (Mexico is on the map only)
    out_changes = []
    for c in data.get("changes") or []:
        after = mod.state_at(data, c["end"])
        before = mod.state_at(data, c["start"] - 1)
        out_changes.append({"start": c["start"], "end": c["end"], "words": mod.change_words(c),
                            "changed": [{"zone": zones[i], "from": before["offs"][i], "to": after["offs"][i]}
                                        for i in c["changed"] if listed(i)],
                            "new": [zones[i] for i in c["new"] if listed(i)],
                            "groups": [{"cc": g["cc"], "off": g["off"], "name": g["name"], "places": g["places"]}
                                       for g in mod.groups_of(data, after, s) if g["cc"] in mod.COUNTRIES]})
    return {"defaults": {**mod.DEFAULTS, "places": mod.DEFAULT_PLACES},
            "zones": [{"id": z, "cc": ccs[i], "off": st["offs"][i], "abbr": st["abbrs"][i]}
                      for i, z in enumerate(zones)] if st else [],
            "groups": [{"cc": g["cc"], "off": g["off"], "name": g["name"], "places": g["places"]}
                       for g in mod.groups_of(data, st, s) if g["cc"] in mod.COUNTRIES] if st else [],
            "changes": out_changes, "rules": data.get("rules") or {}, "boundaries": data.get("boundaries"),
            "rule_updates": data.get("rule_updates") or [], "generated_at": data.get("generated_at"),
            "phase": [k for k, _, _ in mod.pages_at(data, now, s)] if st else [], "zones_px": _webcam_zones()}


@app.get("/api/timezones/preview.png")
def timezones_preview(shape: str = "wall", as_of: float = 0, page: int = 0, scale: int = 3, s: str = ""):
    """What the wall shows for a time zone map entry; ``as_of`` (epoch s) previews another moment (a test),
    ``s`` = the draft settings as JSON."""
    import json as _json
    from display.modules import tzmap as mod
    zones = _webcam_zones()
    if shape not in zones:
        raise HTTPException(400, "shape must be left, right, full or wall")
    settings = (config.load().get("modules") or {}).get("timezones") or {}
    if s:
        try:
            settings = _json.loads(s)
        except ValueError:
            raise HTTPException(400, "s must be JSON")
    img = mod.render_preview(str(paths.DATA_DIR), tuple(zones[shape]), settings, as_of=as_of or None, page=page)
    im = Image.fromarray(img)
    k = max(1, min(6, scale))
    out = io.BytesIO()
    im.resize((im.width * k, im.height * k), Image.NEAREST).save(out, "PNG", compress_level=1)
    return Response(out.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


# --- finance -----------------------------------------------------------------
@app.get("/api/finance/data")
def finance_data():
    return read_json(paths.DATA_DIR / "finance.json", {}) or {}


@app.get("/api/finance/search")
def finance_search(q: str):
    try:
        r = requests.get("https://query2.finance.yahoo.com/v1/finance/search",
                         params={"q": q, "quotesCount": 10, "newsCount": 0, "listsCount": 0},
                         headers={"User-Agent": "Mozilla/5.0 BigScreen/2.0"}, timeout=8)
        quotes = r.json().get("quotes", [])
    except Exception as e:
        raise HTTPException(502, f"Yahoo search failed: {e}")
    return [{"symbol": x.get("symbol"), "name": x.get("shortname") or x.get("longname") or "",
             "exchange": x.get("exchDisp") or x.get("exchange") or "", "type": x.get("typeDisp") or ""}
            for x in quotes if x.get("symbol")]


@app.get("/api/finance/icons")
def list_icons():
    names = {p.name for d in (paths.STOCK_ICONS_DIR, paths.CUSTOM_STOCK_ICONS_DIR) for p in d.glob("*.png")}
    return sorted(names)


@app.get("/api/finance/icons/{name}")
def get_icon(name: str):
    path = _icon_path(name)
    if not path.exists():
        path = paths.STOCK_ICONS_DIR / path.name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.post("/api/finance/icons/{name}")
async def upload_icon(name: str, request: Request):
    path = _icon_path(name)
    _save_icon(await request.body(), path)
    sync.push_data()
    return {"ok": True, "icon": path.name}


@app.post("/api/finance/icons/{name}/from-domain")
def icon_from_domain(name: str, payload: dict = Body(...)):
    domain = str(payload.get("domain", "")).strip().lower()
    domain = re.sub(r"^https?://", "", domain).split("/")[0]
    if not DOMAIN.match(domain):
        raise HTTPException(400, "enter a domain like amd.com")
    r = requests.get("https://www.google.com/s2/favicons", params={"domain": domain, "sz": 64}, timeout=8)
    if r.status_code != 200:
        raise HTTPException(502, f"favicon fetch failed (HTTP {r.status_code})")
    path = _icon_path(name)
    _save_icon(r.content, path)
    sync.push_data()
    return {"ok": True, "icon": path.name}


def _icon_path(name):
    if not ICON_NAME.match(name):
        raise HTTPException(400, "icon names are <something>.png")
    return paths.CUSTOM_STOCK_ICONS_DIR / name


def _save_icon(data, path):
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise HTTPException(400, "not an image")
    img = img.convert("RGBA")
    img.thumbnail((64, 64), Image.LANCZOS)
    img.save(path, "PNG")


# --- headlines ---------------------------------------------------------------
@app.get("/api/headlines/data")
def headlines_data():
    return read_json(paths.DATA_DIR / "headlines.json", {}) or {}


@app.get("/api/headlines/feed-status")
def feed_status():
    return read_json(paths.CACHE_DIR / "news_feed_status.json", {}) or {}


@app.post("/api/headlines/test-feed")
def test_feed(payload: dict = Body(...)):
    url = str(payload.get("url", ""))
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http(s)://")
    s = requests.Session()
    s.headers["User-Agent"] = news_job.UA
    items, st = news_job.fetch_feed(s, {"url": url, "name": payload.get("name") or ""})
    st["sample"] = [{"title": i["title"], "domain": i["domain"], "source": i["source"],
                     "created_utc": i["created_utc"]} for i in items[:5]]
    return st


# --- weather maps ------------------------------------------------------------
WMS_SUGGESTIONS = [
    ("GOES-West_1km_DayVis-NightIR", "GOES-West day visible / night infrared", "past"),
    ("GOES-West_1km_NaturalColor", "GOES-West natural colour (daytime)", "past"),
    ("GOES-West_1km_DayCloudType-NightMicrophysics", "GOES-West cloud type / night microphysics", "past"),
    ("GOES-West_1km_SnowFog-NightMicrophysics", "GOES-West snow & fog", "past"),
    ("GOES-West_1km_SmokeABIband1-SWIR", "GOES-West smoke", "past"),
    ("GOES-East_1km_DayVis-NightIR", "GOES-East day visible / night infrared", "past"),
    ("Lightning_2.5km_Density", "Lightning density (10 min)", "past"),
    ("Radar_1km_RainPrecipRate-Extrapolation", "Radar nowcast: rain (+72 min)", "forecast"),
    ("Radar_1km_SnowPrecipRate-Extrapolation", "Radar nowcast: snow (+72 min)", "forecast"),
    ("HRDPS.CONTINENTAL_PR", "HRDPS forecast precipitation (+48 h)", "forecast"),
    ("HRDPS.CONTINENTAL_TT", "HRDPS forecast temperature (+48 h)", "forecast"),
    ("HRDPS.CONTINENTAL_NT", "HRDPS forecast cloud cover (+48 h)", "forecast"),
    ("RAQDPS.Sfc_PM2.5-WildfireSmokePlume", "Wildfire smoke forecast, surface PM2.5 (+72 h)", "forecast"),
]


@app.get("/api/maps/catalog")
def maps_catalog():
    from hub.weather import carto, geo, radar, tracks
    cfg = config.load()
    half_w, full_w, h = tracks.zone_sizes(cfg)
    return {
        "basemaps": [{"id": k, "label": v[0]} for k, v in geo.TILE_SOURCES.items()],
        "carto_layers": [{"key": k, "label": v["label"], "color": v["color"], "opacity": v["opacity"],
                          "width": v["width"], "min_km_px": v["min_km_px"], "max_km_px": v["max_km_px"]}
                         for k, v in carto.LAYERS.items()],
        "carto_order": carto.DEFAULT_LAYER_ORDER,
        "wms_layers": [{"layer": a, "label": b, "time": c} for a, b, c in WMS_SUGGESTIONS],
        "palettes": {k: [[t, c] for t, c in v] for k, v in radar.PALETTES.items()},
        "sizes": {"half": [half_w, h], "full": [full_w, h]},
    }


@app.get("/api/maps/state")
def maps_state():
    from hub.weather import tracks
    state = read_json(paths.CACHE_DIR / "maps_state.json", {}) or {}
    metas = {}
    for p in tracks.ANIM_DIR.glob("*.json"):
        m = read_json(p, {}) or {}
        metas[p.stem] = {k: m.get(k) for k in ("n", "w", "h", "fps", "hold_last_sec", "loops", "times",
                                               "generated_at", "kind", "label")}
    return {"tracks": state, "packs": metas}


@app.get("/api/maps/track/{tid}/frame/{i}.png")
def maps_frame(tid: str, i: int, scale: int = 2):
    import numpy as np
    from hub.weather import tracks
    if not re.fullmatch(r"[\w-]+", tid):
        raise HTTPException(400)
    meta = read_json(tracks.ANIM_DIR / f"{tid}.json")
    if not meta:
        raise HTTPException(404, "no pack yet")
    n, h, w = meta["n"], meta["h"], meta["w"]
    i = max(0, min(n - 1, i))
    frames = np.memmap(tracks.ANIM_DIR / f"{tid}.bin", dtype=np.uint8, mode="r", shape=(n, h, w, 3))
    img = Image.fromarray(np.array(frames[i]))
    scale = max(1, min(4, scale))
    if scale > 1:
        img = img.resize((w * scale, h * scale), Image.NEAREST)
    bio = io.BytesIO()
    img.save(bio, "PNG", compress_level=1)
    return Response(bio.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=60"})


@app.post("/api/maps/preview-area")
def maps_preview_area(payload: dict = Body(...)):
    """Basemap + cartography for an unsaved area (and optional track basemap), for live editing."""
    from hub.weather import carto, geo, tracks
    cfg = config.load()
    area = dict(payload.get("area") or {}, id=payload.get("area_id") or "preview")
    try:
        if area.get("fit"):
            [float(v) for v in area["fit"]["nw"] + area["fit"]["se"]]
        else:
            float(area["km_per_px"]), float(area["center"][0]), float(area["center"][1])
    except (KeyError, TypeError, ValueError, IndexError):
        raise HTTPException(400, "area needs center [lat, lon] + km_per_px, or fit corners")
    view, _, _ = tracks.views_for(area, payload.get("size", "half"), cfg)
    bm = payload.get("basemap") or {"source": "esri_imagery", "brightness": 0.5}
    base = geo.render_basemap(view, bm)
    if bm.get("water") or bm.get("land"):
        base = carto.tint_water(base, view, bm.get("water"), bm.get("land"), float(bm.get("tint_strength", 1.0)),
                                float(area.get("min_feature_px", 3)))
    mfp = float(area.get("min_feature_px", 3))
    img = geo.alpha_over(base, carto.render_overlay(view, area.get("carto") or {}, mfp, group="under"))
    img = geo.alpha_over(img, carto.render_overlay(view, area.get("carto") or {}, mfp, group="over"))
    out = Image.fromarray(img)
    out = out.resize((view.w * 2, view.h * 2), Image.NEAREST)
    bio = io.BytesIO()
    out.save(bio, "PNG")
    import base64
    return {"png": "data:image/png;base64," + base64.b64encode(bio.getvalue()).decode(),
            "layers": carto.describe_layers(view.km_per_px, area.get("carto") or {}),
            "bbox": [round(v, 4) for v in view.lonlat], "km_per_px": round(view.km_per_px, 3),
            "center": [round(view.center[0], 4), round(view.center[1], 4)],
            "width_km": round(view.km_per_px * view.w), "height_km": round(view.km_per_px * view.h)}


@app.post("/api/maps/render/{tid}")
def maps_render(tid: str):
    """Force a track to re-render on the next maps job run, and run it now."""
    from hub.util import atomic_write_json
    p = paths.CACHE_DIR / "maps_state.json"
    st = read_json(p, {}) or {}
    if tid != "all":
        st.pop(tid, None)
    else:
        st = {}
    atomic_write_json(p, st, indent=1)
    _sched().run_now("maps")
    return {"ok": True}


@app.get("/api/favicon/{domain}")
def favicon(domain: str):
    if not DOMAIN.match(domain):
        raise HTTPException(400)
    path = paths.ICONS_DIR / f"{domain}_16.png"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
