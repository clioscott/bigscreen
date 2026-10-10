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
    from display.modules.alert_themes import BORDERS, FAMILIES, PALETTES, REVEALS, STRUCTURES
    from display.modules.weather_alert import THEME_LIST
    data = read_json(paths.DATA_DIR / "alerts.json", {}) or {}
    fonts = sorted(p.name for p in (Path(ASSETS_DIR) / "fonts" / "alert").glob("*.bdf"))
    hexc = lambda c: "#%02x%02x%02x" % c if isinstance(c, tuple) else None
    return {**data, "themes": THEME_LIST, "structures": STRUCTURES, "borders": BORDERS, "reveals": REVEALS,
            "palettes": {k: {"name": v["name"], "swatch": [hexc(v["bg"]), hexc(v["strong"]), hexc(v["amount"]), hexc(v["advice"])]}
                         for k, v in PALETTES.items()},
            "families": {k: v["name"] for k, v in FAMILIES.items()},
            "hazards": [{"id": h, "label": l, "hint": e} for h, l, e in __import__("display.modules.alert_brief", fromlist=["x"]).HAZARD_INFO],
            "fonts": ["alert/" + f for f in fonts]}


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


@app.get("/api/alerts/preview/{theme}.png")
def alerts_preview(theme: str, at: float = 4.0, scale: int = 2, real: int = 0, s: str = ""):
    """A frame of the full-screen alert at ``at`` seconds, rendered with the display's own code."""
    from display.modules.alert_themes import THEMES
    from display.modules.weather_alert import render_preview
    if theme not in THEMES:
        raise HTTPException(404, "unknown theme")
    data = read_json(paths.DATA_DIR / "alerts.json", {}) if real else None
    frame = render_preview(theme, data if data and data.get("alerts") else None, max(0.0, at), _alert_settings(s))
    k = max(1, min(4, scale))
    img = Image.fromarray(frame).resize((frame.shape[1] * k, frame.shape[0] * k), Image.NEAREST)
    bio = io.BytesIO()
    img.save(bio, "PNG", compress_level=1)
    return Response(bio.getvalue(), media_type="image/png", headers={"Cache-Control": "max-age=60"})


@app.get("/api/alerts/timeline")
def alerts_timeline(real: int = 0, s: str = ""):
    """When each page of the alert screen starts and how long it holds (for the preview scrubber)."""
    from display.modules.weather_alert import timeline
    data = read_json(paths.DATA_DIR / "alerts.json", {}) if real else None
    return timeline(_alert_settings(s), data if data and data.get("alerts") else None)


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
