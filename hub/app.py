"""Control panel HTTP API + static UI (served on hub.panel_port, default 8800)."""

import io
import re
import time
from pathlib import Path

import requests
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image

from common import logring
from hub import config, paths, pi, sync
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
                         ("weather", "weather")):
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
    return _display_call("/preview", {"overlay": overlay, "ttl_sec": int(payload.get("ttl_sec", 120))})


@app.post("/api/display/preview/clear")
def display_preview_clear():
    return _display_call("/preview/clear")


@app.post("/api/display/headlines/jump")
def display_headlines_jump(payload: dict = Body(default={})):
    """{"delta": 1|-1} or {"index": n}: show another headline category now (pins headlines if off-screen)."""
    return _display_call("/headlines/jump", payload)


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
