"""Control panel HTTP API + static UI (served on hub.panel_port, default 8800)."""

import io
import re
import time
from pathlib import Path

import requests
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

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
    return sorted(p.name for p in paths.STOCK_ICONS_DIR.glob("*.png"))


@app.get("/api/finance/icons/{name}")
def get_icon(name: str):
    path = _icon_path(name)
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "no-cache"})


@app.post("/api/finance/icons/{name}")
async def upload_icon(name: str, request: Request):
    path = _icon_path(name)
    _save_icon(await request.body(), path)
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
    return {"ok": True, "icon": path.name}


def _icon_path(name):
    if not ICON_NAME.match(name):
        raise HTTPException(400, "icon names are <something>.png")
    return paths.STOCK_ICONS_DIR / name


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


@app.get("/api/favicon/{domain}")
def favicon(domain: str):
    if not DOMAIN.match(domain):
        raise HTTPException(400)
    path = paths.ICONS_DIR / f"{domain}_16.png"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path)
