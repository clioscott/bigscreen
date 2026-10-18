"""Keep the README current: render every screen into docs/media and rewrite the README's generated sections.

    python -m tools.readme media [group …]   # on the server, hub venv: renders docs/media from samples only
    python -m tools.readme text              # README generated sections + assets/fonts/CREDITS.md
    python -m tools.readme check             # exit 1 if `text` would change anything (CI runs this)
    python -m tools.readme all               # media, then text

Groups: wall, alerts, half, bc, eew, maps, panel (default: all of them).

`media` never touches real personal data. It builds a sandbox state dir (~/bigscreen-state/cache/readme-sandbox)
with a scrubbed copy of the config, synthetic calendar/client-call events and synthetic tickers, plus the public
data the screens need (weather, radar packs, basemaps). BC and alert screens render their built-in samples
(labelled SAMPLE). The wall and control-panel shots come from an emulated display and a second hub instance
running on that sandbox (ports 8798/8799); the panel shots need Playwright (see docs/README-media.md).

Run it whenever a screen's look changes, then commit docs/media, README.md and assets/fonts/CREDITS.md.
`text` and `check` need only the repo (numpy + Pillow), so CI can verify the generated sections.
"""

from __future__ import annotations

import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "docs" / "media"
MANIFEST = MEDIA / "manifest.json"
README = ROOT / "README.md"
CREDITS = ROOT / "assets" / "fonts" / "CREDITS.md"
REAL_STATE = Path(os.environ.get("BIGSCREEN_REAL_STATE", Path.home() / "bigscreen-state"))
SANDBOX = REAL_STATE / "cache" / "readme-sandbox"
EMU_PORT, HUB_PORT = 8799, 8798
SCALE = 2
GROUPS = ("wall", "alerts", "half", "bc", "eew", "maps", "panel")

BC = {"naad": ("bc_naad", "Alert Ready"), "roads": ("bc_roads", "DriveBC"), "quakes": ("bc_quake", "Earthquakes"),
      "tsunami": ("bc_tsunami", "Tsunami"), "wildfire": ("bc_fire", "Wildfire")}


# ---- output helpers -------------------------------------------------------------------------------
class Manifest:
    """docs/media/manifest.json: what each file shows, read back by `text` to build the gallery."""

    def __init__(self):
        try:
            self.items = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []
        self.touched = set()

    def add(self, group, file, title, caption="", order=0):
        self.items = [i for i in self.items if i["file"] != file]
        self.items.append({"group": group, "file": file, "title": title, "caption": caption, "order": order})
        self.touched.add(group)

    def drop_untouched(self, groups):
        """Forget (and delete) files of re-rendered groups that this run didn't produce again."""
        keep, made = [], {i["file"] for i in self.items if i.get("_new")}
        for i in self.items:
            if i["group"] in groups and not i.get("_new"):
                (MEDIA / i["file"]).unlink(missing_ok=True)
            else:
                keep.append(i)
        self.items = keep

    def save(self):
        self.items.sort(key=lambda i: (GROUPS.index(i["group"]) if i["group"] in GROUPS else 99, i["order"], i["file"]))
        MEDIA.mkdir(parents=True, exist_ok=True)
        out = [{k: v for k, v in i.items() if not k.startswith("_")} for i in self.items]
        _write_if_changed(MANIFEST, (json.dumps(out, indent=1, ensure_ascii=False) + "\n").encode("utf-8"))


def _write_if_changed(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def _img(frame, scale=SCALE):
    from PIL import Image
    img = Image.fromarray(frame[..., :3].copy())
    return img.resize((img.width * scale, img.height * scale), Image.NEAREST) if scale > 1 else img


def save_png(man, group, name, frame, title, caption="", order=0, scale=SCALE):
    bio = io.BytesIO()
    _img(frame, scale).save(bio, "PNG", optimize=True)
    _write_if_changed(MEDIA / name, bio.getvalue())
    man.add(group, name, title, caption, order)
    man.items[-1]["_new"] = True


def save_gif(man, group, name, frames, fps, title, caption="", order=0, scale=SCALE):
    """An animated GIF; identical consecutive frames merge into one longer frame."""
    import numpy as np
    from PIL import Image
    merged = []
    for f in frames:
        if merged and np.array_equal(merged[-1][0], f):
            merged[-1][1] += 1
        else:
            merged.append([f, 1])
    imgs = [_img(f, scale).convert("P", palette=Image.ADAPTIVE, colors=255) for f, _ in merged]
    durs = [max(20, int(round(1000 * n / fps))) for _, n in merged]
    bio = io.BytesIO()
    imgs[0].save(bio, "GIF", save_all=True, append_images=imgs[1:], duration=durs, loop=0, optimize=True, disposal=1)
    _write_if_changed(MEDIA / name, bio.getvalue())
    man.add(group, name, title, caption, order)
    man.items[-1]["_new"] = True


def log(msg):
    print(f"[readme] {msg}", flush=True)


# ---- sandbox state --------------------------------------------------------------------------------
SAMPLE_TICKERS = {"SPY": 571.2, "AAPL": 228.4, "MSFT": 431.9, "NVDA": 134.6, "GOOGL": 165.3, "TSLA": 251.7,
                  "AMD": 152.8, "BTC-USD": 64250.0, "ETH-USD": 2510.0}

SAMPLE_EVENTS = [  # (start offset h, length min, title, colour)
    (-3.0, 30, "Team standup", (210, 70, 90)), (-1.5, 60, "Design review", (28, 85, 87)),
    (-0.5, 30, "Lunch with Sam", (102, 73, 48)), (0.25, 45, "Dentist", (0, 0, 100)),
    (1.5, 60, "Riverside Dental - Consult", (0, 0, 100)), (2.5, 30, "Harbour Legal - Review", (0, 0, 100)),
    (3.25, 60, "Northwind LLP - Export", (0, 0, 100)), (4.5, 90, "Soccer practice", (48, 92, 76)),
    (6.0, 60, "Book club", (90, 90, 90)), (-2.25, 45, "Maple & Co - Consult", (0, 0, 100)),
    (-4.0, 30, "Cedar Accounting - Review", (0, 0, 100)), (24, 0, "Recycling day", (0, 0, 100)),
]


def _iso(dt):
    return dt.astimezone().isoformat(timespec="seconds")


def sample_calendar(now=None):
    now = (now or datetime.now().astimezone()).replace(second=0, microsecond=0)
    base = now.replace(minute=(now.minute // 15) * 15)
    out = []
    for i, (off, mins, title, colour) in enumerate(SAMPLE_EVENTS):
        st = base + timedelta(hours=off)
        if mins:
            start, end = {"dateTime": _iso(st)}, {"dateTime": _iso(st + timedelta(minutes=mins))}
        else:
            start, end = {"date": st.date().isoformat()}, {"date": (st.date() + timedelta(days=1)).isoformat()}
        out.append({"kind": "calendar#event", "id": f"sample{i}", "status": "confirmed", "summary": title,
                    "summary_clean": title, "start": start, "end": end, "eventType": "default",
                    "_source_calendar": "sample", "_event_color": list(colour)})
    return out


def sample_finance():
    rnd, out = random.Random(7), {}
    for sym, price in SAMPLE_TICKERS.items():
        walk, v = [], 1.0
        for _ in range(84):
            v *= 1 + rnd.gauss(0, 0.004)
            walk.append(v)
        lo, hi = min(walk), max(walk)
        prev = price / walk[-1] * walk[0]
        out[sym] = {"price": price, "change": (price / prev - 1) * 100, "prev_close": prev,
                    "history": [{"c": (w - lo) / ((hi - lo) or 1)} for w in walk], "min_v": price * lo / walk[-1],
                    "max_v": price * hi / walk[-1], "rng": price * (hi - lo) / walk[-1],
                    "chart_type": "crypto" if "-" in sym else "stock", "day_high": price * hi / walk[-1],
                    "day_low": price * lo / walk[-1], "_display": {"title": sym, "width": 170, "icon": f"{sym}.png"},
                    "_updated": time.time()}
    return out


def scrub_config(cfg):
    """The live config minus anything personal: calendars, title rules, tickers; points the hub at the emulator."""
    cfg = json.loads(json.dumps(cfg))
    m = cfg.setdefault("modules", {})
    cal = m.setdefault("calendar", {})
    cal["calendars"] = []
    cal["event_processing"] = {"client_call_title_suffixes": [" - Consult", " - Export", " - Review"],
                               "exclude_events_containing": [], "suppress_alerts_containing": [],
                               "background_events_containing": [], "event_color_rules": [], "title_replacements": []}
    fin = m.get("finance") or {}
    for k in ("symbols", "tickers", "watchlist", "holdings"):
        if k in fin:
            fin[k] = list(SAMPLE_TICKERS)
    m.setdefault("override_server", {})["port"] = EMU_PORT
    hub = cfg.setdefault("hub", {})
    cfg["hub"] = {"display_mode": "v2", "panel_port": HUB_PORT, "pi": {"host": "127.0.0.1", "user": "nobody"},
                  "fonts": hub.get("fonts", {})}
    return cfg


def build_sandbox():
    """~/bigscreen-state/cache/readme-sandbox: a state dir the hub and the emulator can run on."""
    real_data, data = REAL_STATE / "data", SANDBOX / "data"
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    data.mkdir(parents=True)
    for d in ("cache", "secrets", "config-backups"):
        (SANDBOX / d).mkdir()
    public = ["weather.json", "headlines.json", "alertdata.json", "alertextra.json", "panel_offsets.json",
              "anim", "alertmaps", "alertpages", "bcmaps", "drivebc", "quake", "icons"]
    for name in public:
        src = real_data / name
        if src.is_dir():
            shutil.copytree(src, data / name)
        elif src.exists():
            shutil.copy2(src, data / name)
    w = json.loads((data / "weather.json").read_text()) if (data / "weather.json").exists() else {}
    w["alerts"] = []                                       # alert screens use their samples instead
    (data / "weather.json").write_text(json.dumps(w))
    (data / "alerts.json").write_text(json.dumps({"alerts": []}))
    (data / "calendar.json").write_text(json.dumps(sample_calendar()))
    (data / "finance.json").write_text(json.dumps(sample_finance()))
    for p in ("maps_state.json",):
        if (REAL_STATE / "cache" / p).exists():
            shutil.copy2(REAL_STATE / "cache" / p, SANDBOX / "cache" / p)
    cfg = scrub_config(json.loads((REAL_STATE / "config.json").read_text()))
    (SANDBOX / "config.json").write_text(json.dumps(cfg, indent=1))
    (data / "config.json").write_text(json.dumps(cfg, indent=1))
    (SANDBOX / "status.json").write_text("{}")
    log(f"sandbox at {SANDBOX}")
    return cfg


# ---- renderers --------------------------------------------------------------------------------------
def _frames(fn, start, stop, fps):
    n = max(1, int(round((stop - start) * fps)))
    return [fn(start + i / fps) for i in range(n)]


def render_alerts(man):
    from display.modules import alert_themes
    from display.modules.weather_alert import render_preview, timeline
    data_dir = str(SANDBOX / "data")
    settings = {"_data_dir": data_dir}
    seen = set()
    for i, (tid, th) in enumerate(alert_themes.THEMES.items()):
        f = render_preview(tid, {"alerts": []}, 7.0, settings)
        save_png(man, "alerts", f"alert-{tid}.png", f, th["name"], th["description"], i)
        if th["structure"] not in seen:                   # one animation per layout structure
            seen.add(th["structure"])
            total = timeline(dict(settings, theme=tid, theme_mode="fixed"), {"alerts": []})["total"]
            frames = _frames(lambda t: render_preview(tid, {"alerts": []}, t, settings), 0.0, min(total, 10.0), 6)
            save_gif(man, "alerts", f"alert-{tid}.gif", frames, 6, th["name"], th["description"], i)
    log(f"alerts: {len(alert_themes.THEMES)} themes")


def _half_weather(theme, page, at):
    from display.gfx import Canvas
    from display.modules import bc_kit as k
    from display.modules.alert_samples import SAMPLES
    from display.modules.weather_alert import AlertScreen
    from display.modules.weather_alert_half import WeatherAlerts
    data_dir = str(SANDBOX / "data")
    scr = WeatherAlerts(AlertScreen({}, k.StaticLoader({"alerts": {"alerts": []}}, data_dir)), data_dir)
    hp = k.HalfPanel(scr)
    zone = (225, 41, 223, 134)
    c = Canvas(448, 192)
    entry = {"sample": next(iter(SAMPLES)), "page_sec": 10 ** 6, "theme": theme}
    now = time.time()
    hp.draw(c, zone, entry, True, now=now - at)
    n = len(hp.pages)
    if n:
        hp.idx, hp.t0 = page % n, now - at
        c.buf[:] = 0
        hp.draw(c, zone, entry, False, now=now)
    return c.buf[zone[1]:zone[1] + zone[3], zone[0]:zone[0] + zone[2]].copy(), n


def render_half(man):
    import importlib
    from display.modules import alert_themes, bc_kit
    data_dir = str(SANDBOX / "data")
    for i, tid in enumerate(alert_themes.THEMES):
        f, n = _half_weather(tid, 0, 3.0)
        save_png(man, "half", f"half-weather-{tid}.png", f, f"Weather alert · {alert_themes.THEMES[tid]['name']}", "", i)
    first = next(iter(alert_themes.THEMES))
    _, n = _half_weather(first, 0, 3.0)
    frames = []
    for p in range(n):
        frames += [_half_weather(first, p, t)[0] for t in (1.0, 2.5, 4.0, 5.5)]
    if frames:
        save_gif(man, "half", "half-weather.gif", frames, 1.5, "Weather alert, half width", "Every page in turn", -1)
    for j, (src, (modname, label)) in enumerate(BC.items()):
        mod = importlib.import_module(f"display.modules.{modname}")
        f0, n = bc_kit.render_half(mod.Screen, None, 3.0, {}, {}, data_dir, page=0)
        frames = []
        for p in range(n):
            f, _ = bc_kit.render_half(mod.Screen, None, 3.0, {}, {}, data_dir, page=p)
            save_png(man, "half", f"half-{src}-p{p}.png", f[41:175, 225:448], f"{label} · page {p + 1}", "", 100 + j * 10 + p)
            frames += [f[41:175, 225:448]] * 3
        if frames:
            save_gif(man, "half", f"half-{src}.gif", frames, 1.0, f"{label}, half width", "", 100 + j * 10 - 1)
    log("half-width done")


def render_bc(man):
    import importlib
    data_dir = str(SANDBOX / "data")
    for j, (src, (modname, label)) in enumerate(BC.items()):
        mod = importlib.import_module(f"display.modules.{modname}")
        for i, (tid, th) in enumerate(mod.THEMES.items()):
            f = mod.render_preview(tid, None, 6.0, {}, {}, data_dir)
            save_png(man, "bc", f"bc-{src}-{tid}.png", f, f"{label} · {th['label']}", th.get("hint", ""), j * 100 + i + 1)
        tid = mod.Screen.DEFAULT_THEME
        frames = _frames(lambda t: mod.render_preview(tid, None, t, {}, {}, data_dir), 0.0, 9.0, 5)
        save_gif(man, "bc", f"bc-{src}.gif", frames, 5, label, mod.THEMES[tid]["label"], j * 100)
    log("BC screens done")


def render_eew(man):
    from display.gfx import Canvas
    from display.modules.quake import THEMES, QuakeScreen
    from hub import quake
    from common import kmoni
    quake.ensure_map()
    sc = quake.SCENARIOS["tokyo_bay"]
    data_dir = str(SANDBOX / "data")
    cache = {}

    def obs(sec):
        if sec not in cache:
            ev0 = {"origin": 0.0, "lat": sc["lat"], "lon": sc["lon"], "depth": sc["depth"], "mag": sc["mag"]}
            cache[sec] = kmoni.encode(kmoni.synthetic(ev0, float(sec)))
        return cache[sec]

    def frame(theme, at):
        now = time.time()
        ev = {"id": "preview", "origin": now - at, "lat": sc["lat"], "lon": sc["lon"], "depth": sc["depth"],
              "mag": sc["mag"], "hypocenter": sc["hypocenter"], "max": sc["max"], "warn": True, "serial": 1,
              "test": True, "theme": theme}
        scr = QuakeScreen(data_dir, {"theme": theme})
        scr.set_event(ev)
        for sec in range(max(0, int(at) - 60), int(at) + 1):
            scr.set_obs(obs(sec), now - (at - sec))
        c = Canvas(448, 192)
        scr.draw(c, (0, 0, 448, 192), {}, True, now=now)
        return c.buf.copy()

    for i, (tid, th) in enumerate(THEMES.items()):
        save_png(man, "eew", f"eew-{tid}.png", frame(tid, 20), th["label"], th["hint"], i + 1)
    first = next(iter(THEMES))
    save_gif(man, "eew", "eew.gif", [frame(first, 4 + t / 2) for t in range(40)], 4, THEMES[first]["label"],
             "Tokyo Bay test scenario, 4–24 s after origin", 0)
    log(f"EEW: {len(THEMES)} themes")


def render_maps(man):
    import numpy as np
    anim = SANDBOX / "data" / "anim"
    for i, meta_p in enumerate(sorted(anim.glob("*.json"))):
        meta = json.loads(meta_p.read_text())
        n, h, w = meta["n"], meta["h"], meta["w"]
        frames = np.memmap(meta_p.with_suffix(".bin"), dtype=np.uint8, mode="r", shape=(n, h, w, 3))
        step = max(1, n // 48)
        pick = [np.array(frames[k]) for k in range(0, n, step)] + [np.array(frames[n - 1])] * 6
        label = meta.get("label") or meta_p.stem.replace("_", " ")
        save_gif(man, "maps", f"map-{meta_p.stem}.gif", pick, min(12, max(4, meta.get("fps", 8))), label,
                 f"{n} frames", i)
    log("maps done")


def _wait(url, timeout=60):
    import requests
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if requests.get(url, timeout=2).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{url} didn't come up")


def _spawn(args, env_extra):
    env = dict(os.environ, BIGSCREEN_STATE=str(SANDBOX), BIGSCREEN_DATA_DIR=str(SANDBOX / "data"),
               BIGSCREEN_NO_PI="1", PYTHONPATH=str(ROOT), **env_extra)
    return subprocess.Popen([sys.executable] + args, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


WALL_SCENES = [  # (file, title, left entry, right entry, seconds)
    ("wall-calendar-weather", "Calendar + weather", {"panel": "calendar"}, {"panel": "weather"}, 8),
    ("wall-calls-headlines", "Client calls + headlines", {"panel": "client_calls_upcoming"}, {"panel": "headlines"}, 10),
    ("wall-calendar-radar", "Calendar + radar", {"panel": "calendar"}, {"panel": "map", "track": "precip_radar"}, 10),
    ("wall-past-totals", "Past calls + rain totals", {"panel": "client_calls_past"}, {"panel": "map", "track": "precip_totals"}, 10),
]


def render_wall(man):
    import numpy as np
    import requests
    from PIL import Image
    emu = _spawn(["-m", "display.main", "--emulate", "--port", str(EMU_PORT)], {})
    try:
        base = f"http://127.0.0.1:{EMU_PORT}"
        _wait(base + "/status")
        time.sleep(4)
        for i, (name, title, left, right, secs) in enumerate(WALL_SCENES):
            for side, entry in (("left", left), ("right", right)):
                r = requests.post(base + "/play", json={"side": side, "entry": entry, "hold": True, "ttl_sec": 600}, timeout=5)
                if not r.ok:
                    log(f"wall: {name}: {r.text[:120]}")
            time.sleep(1.5)
            frames = []
            for _ in range(int(secs * 4)):
                png = requests.get(base + "/frame.png?scale=1", timeout=5).content
                frames.append(np.array(Image.open(io.BytesIO(png)).convert("RGB")))
                time.sleep(0.25)
            save_png(man, "wall", f"{name}.png", frames[len(frames) // 2], title, "", i)
            save_gif(man, "wall", f"{name}.gif", frames, 4, title, "", i)
        requests.post(base + "/rotation", json={"action": "release"}, timeout=5)
    finally:
        emu.terminate()
        emu.wait(10)
    log("wall done")


PANEL_PAGES = [("overview", "Overview"), ("rotation", "Rotation"), ("maps", "Weather maps"), ("alerts", "Weather alerts"),
               ("naad", "Alert Ready"), ("roads", "DriveBC"), ("quakes", "Earthquakes"), ("quake", "Japan EEW"),
               ("fonts", "Fonts"), ("colours", "Colour scales"), ("display", "Display")]


def render_panel(man):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log("panel: Playwright isn't installed in this venv; skipping (see docs/README-media.md)")
        man.touched.discard("panel")
        return
    emu = _spawn(["-m", "display.main", "--emulate", "--port", str(EMU_PORT)], {})
    hub = _spawn(["-m", "uvicorn", "hub.app:app", "--host", "127.0.0.1", "--port", str(HUB_PORT)], {})
    try:
        _wait(f"http://127.0.0.1:{HUB_PORT}/")
        _wait(f"http://127.0.0.1:{EMU_PORT}/status")
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1, color_scheme="dark")
            for i, (route, title) in enumerate(PANEL_PAGES):
                pg.goto(f"http://127.0.0.1:{HUB_PORT}/#/{route}")
                pg.wait_for_load_state("load")
                pg.wait_for_timeout(4000)
                png = pg.screenshot(type="png")
                _write_if_changed(MEDIA / f"panel-{route}.png", png)
                man.add("panel", f"panel-{route}.png", title, "", i)
                man.items[-1]["_new"] = True
            b.close()
    finally:
        for p in (hub, emu):
            p.terminate()
            p.wait(10)
    log("panel done")


def media(groups):
    groups = [g for g in (groups or GROUPS)]
    build_sandbox()
    os.environ["BIGSCREEN_STATE"] = str(SANDBOX)          # hub.paths → the sandbox from here on
    os.environ["BIGSCREEN_NO_PI"] = "1"
    sys.path.insert(0, str(ROOT))
    man = Manifest()
    for g in groups:
        t0 = time.time()
        try:
            {"wall": render_wall, "alerts": render_alerts, "half": render_half, "bc": render_bc, "eew": render_eew,
             "maps": render_maps, "panel": render_panel}[g](man)
        except Exception as e:                            # keep what the other groups made
            import traceback
            traceback.print_exc()
            log(f"{g}: FAILED ({e}); its previous media are kept")
            man.touched.discard(g)
            continue
        log(f"{g}: {time.time() - t0:.0f} s")
    man.drop_untouched(man.touched)
    man.save()


# ---- generated text -------------------------------------------------------------------------------
def _section(text, name, body):
    pat = re.compile(rf"(<!-- gen:{name} -->\n).*?(<!-- /gen:{name} -->)", re.S)
    if not pat.search(text):
        raise SystemExit(f"README.md has no <!-- gen:{name} --> … <!-- /gen:{name} --> block")
    return pat.sub(lambda m: m.group(1) + body.rstrip() + "\n" + m.group(2), text)


def _media_items():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _grid(items, cols=3, width=None):
    if not items:
        return "_(not rendered yet — run `python -m tools.readme media` on the server)_\n"
    rows, out = [items[i:i + cols] for i in range(0, len(items), cols)], []
    out.append("<table>")
    for row in rows:
        out.append("<tr>" + "".join(
            f'<td align="center" valign="top"><img src="docs/media/{i["file"]}" alt="{_esc(i["title"])}"'
            + (f' width="{width}"' if width else "") + f'><br><sub><b>{_esc(i["title"])}</b>'
            + (f'<br>{_esc(i["caption"])}' if i.get("caption") else "") + "</sub></td>" for i in row) + "</tr>")
    out.append("</table>")
    return "\n".join(out) + "\n"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def gallery():
    items = _media_items()
    by = lambda g, ext=None: [i for i in items if i["group"] == g and (ext is None or i["file"].endswith(ext))]
    out = []
    out.append("### The wall\n")
    out.append(_grid(by("wall", ".gif"), cols=1))
    out.append("### Weather maps\n\nRadar, rolling rain/snow totals and satellite loops, rendered on the server and "
               "played back frame-exact on the Pi.\n")
    out.append(_grid(by("maps"), cols=2))
    out.append("### Weather alerts (ECCC)\n\nOne animation per layout, then every theme.\n")
    out.append(_grid(by("alerts", ".gif"), cols=2))
    out.append("<details><summary>All alert themes</summary>\n\n" + _grid(by("alerts", ".png"), cols=3) + "\n</details>\n")
    out.append("### BC alert screens\n")
    out.append(_grid(by("bc", ".gif"), cols=2))
    out.append("<details><summary>Every BC theme</summary>\n\n" + _grid(by("bc", ".png"), cols=3) + "\n</details>\n")
    out.append("### Half-width alerts\n\nAfter a new alert's full-wall showings it moves to the right half of the wall.\n")
    out.append(_grid(by("half", ".gif"), cols=3))
    out.append("<details><summary>Every half-width theme and page</summary>\n\n" + _grid(by("half", ".png"), cols=4)
               + "\n</details>\n")
    out.append("### Japan earthquake early warning\n")
    out.append(_grid(by("eew", ".gif"), cols=1))
    out.append("<details><summary>All EEW themes</summary>\n\n" + _grid(by("eew", ".png"), cols=3) + "\n</details>\n")
    out.append("### Control panel\n")
    out.append(_grid(by("panel"), cols=2))
    return "\n".join(out)


def features():
    import importlib
    sys.path.insert(0, str(ROOT))
    from display.modules import alert_themes, quake
    lines = [f"- **{len(alert_themes.THEMES)} weather-alert themes** built from {len(alert_themes.STRUCTURES)} layouts, "
             f"{len(alert_themes.FAMILIES)} font families and {len(alert_themes.PALETTES)} palettes."]
    for src, (modname, label) in BC.items():
        mod = importlib.import_module(f"display.modules.{modname}")
        lines.append(f"- **{label}**: {len(mod.THEMES)} themes ({', '.join(t['label'] for t in mod.THEMES.values())}), "
                     f"{len(mod.SAMPLES)} built-in samples.")
    lines.append(f"- **Japan EEW**: {len(quake.THEMES)} themes ({', '.join(t['label'] for t in quake.THEMES.values())}).")
    jobs = []
    for p in sorted((ROOT / "hub" / "jobs").glob("*.py")):
        if p.stem.startswith("_") or p.stem in ("calendar_service", "ec_weather"):     # libraries, not jobs
            continue
        doc = re.search(r'^"""(.*?)(?:\n|""")', p.read_text(encoding="utf-8"), re.S)
        what = re.sub(r"^[\w ]+ job: ", "", doc.group(1).strip().rstrip(".")) if doc else ""
        jobs.append(f"  - `{p.stem}`: {what[:1].upper() + what[1:]}")
    lines.append(f"- **{len(jobs)} fetch jobs** on the hub, each a short-lived subprocess with a timeout:")
    lines += jobs
    n_fonts = len(list((ROOT / "assets" / "fonts").rglob("*.bdf")))
    lines.append(f"- **{n_fonts} bitmap fonts**, always drawn at native size ([credits](assets/fonts/CREDITS.md)).")
    return "\n".join(lines) + "\n"


UNCONFIRMED = {"alert/nokia-small.bdf", "alert/nokia-small-bold.bdf", "nokia-small.bdf", "knj10.bdf", "knj10B.bdf",
               "candidates/boxxy-bold.bdf", "candidates/lokaltog-fixed-10r-iso10646.bdf", "candidates/haxormedium-12.bdf",
               "candidates/haxormedium-13.bdf", "candidates/knxt.bdf"}


def credits_md():
    cat = json.loads((ROOT / "assets" / "fonts" / "catalog.json").read_text(encoding="utf-8"))
    files = sorted(p.relative_to(ROOT / "assets" / "fonts").as_posix() for p in (ROOT / "assets" / "fonts").rglob("*.bdf"))
    out = ["# Font credits", "",
           "Generated by `python -m tools.readme text` from `catalog.json` and each file's BDF `COPYRIGHT` property.",
           "Every font keeps its own licence; see [THIRD_PARTY.md](../../THIRD_PARTY.md).", "",
           "| File | Family | Origin | Licence | Copyright |", "|---|---|---|---|---|"]
    for f in files:
        m = cat.get(f, {})
        head = (ROOT / "assets" / "fonts" / f).read_text(encoding="latin-1", errors="replace")[:8000]
        cp = re.search(r'^COPYRIGHT "?(.*?)"?\s*$', head, re.M)
        lic = m.get("licence", "") + (" — *unconfirmed*" if f in UNCONFIRMED else "")
        cell = lambda s: str(s).replace("|", "\\|").strip()
        out.append(f"| `{f}` | {cell(m.get('family', ''))} | {cell(m.get('origin', ''))} | {cell(lic)} | "
                   f"{cell(cp.group(1)[:90] if cp else '')} |")
    return "\n".join(out) + "\n"


def text(write=True):
    changed = []
    readme = README.read_text(encoding="utf-8")
    new = _section(_section(readme, "features", features()), "gallery", gallery())
    targets = [(README, new), (CREDITS, credits_md())]
    for path, body in targets:
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != body:
            changed.append(path.relative_to(ROOT).as_posix())
            if write:
                path.write_text(body, encoding="utf-8", newline="\n")
    return changed


def main(argv):
    cmd = argv[0] if argv else "text"
    if cmd == "media":
        media(argv[1:])
    elif cmd == "text":
        print("\n".join(text()) or "up to date")
    elif cmd == "check":
        stale = text(write=False)
        if stale:
            print("Generated sections are stale: " + ", ".join(stale) + "\nRun: python -m tools.readme text")
            return 1
        print("README generated sections are current")
    elif cmd == "all":
        media(argv[1:])
        print("\n".join(text()) or "up to date")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
