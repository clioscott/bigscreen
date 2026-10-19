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
from hub import config, paths, pi, sync, webcams
from hub.jobs import news as news_job
from hub.util import read_json

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
                         ("webcam_weather", "webcams")):
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
            _webcams["list"], _webcams["at"] = got, time.time()
        except Exception as e:
            if not _webcams["list"]:
                raise HTTPException(502, f"DriveBC webcams: {e}")
    return _webcams["list"]


@app.get("/api/webcams/traffic")
def webcams_traffic():
    """DriveBC traffic cameras for the Webcams page, by bridge or tunnel and by city (hub/traffic.py)."""
    from hub import traffic
    from hub.jobs import drivebc
    return {**traffic.build(_dbc_list(), drivebc.fold), "fetched": _webcams["at"]}


_dbc_thumbs: dict = {}               # camera id -> (fetched at, jpeg)
_dbc_thumb_failed: dict = {}         # camera id -> when its fetch failed (not retried for a minute)
_dbc_thumb_slots = threading.Semaphore(6)   # DriveBC fetches at once (a city tab asks for ~100 thumbnails)


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
        im = Image.fromarray(frame)
        out = io.BytesIO()
        im.resize((320, round(im.height * 320 / im.width))).save(out, "JPEG", quality=80)
        if len(_dbc_thumbs) > 400:
            _dbc_thumbs.clear()
        hit = _dbc_thumbs[cid] = (time.time(), out.getvalue())
    return Response(hit[1], media_type="image/jpeg", headers={"Cache-Control": "max-age=60"})


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
    """{"side"?, "action": "next"|"prev"|"stop"|"hold"|"release"}"""
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
    """Pixel size of each place a webcam can play (display/main.py Screen: ticker 44 px + 1, bottom strip 17)."""
    L = (config.load().get("layout") or {})
    w, h = L.get("width", 448), L.get("height", 192)
    main_h = h - 17 - (L.get("top_bar_height", 44) + 1)
    cal_w = int(w * L.get("calendar_split_ratio", 0.66))
    return {"left": [cal_w, main_h], "right": [w - cal_w - 1, main_h], "full": [w, main_h], "wall": [w, h]}


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
