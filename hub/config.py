"""The single config file, owned by the hub.

Every save bumps `revision`, writes a timestamped backup, and is then pushed to
the Pi (see hub/pi.py). The top-level keys `system`, `fonts`, `colors`,
`layout`, `modules` keep the v1 shape so the v1 display can read the same file
while the v2 renderer is being built.
"""

import copy
import re
import threading
import time
from datetime import datetime

from hub import paths
from hub.util import atomic_write_json, read_json

MAX_BACKUPS = 100

_lock = threading.Lock()


class ConfigError(ValueError):
    pass


def load():
    cfg = read_json(paths.CONFIG_PATH)
    if cfg is None:
        raise ConfigError(f"No config at {paths.CONFIG_PATH} — run tools/migrate_v1.py first")
    return cfg


def save(cfg, reason=""):
    """Validate, back up, and atomically write. Returns the saved config."""
    validate(cfg)
    with _lock:
        current = read_json(paths.CONFIG_PATH, {}) or {}
        cfg = copy.deepcopy(cfg)
        cfg["revision"] = int(current.get("revision", 0)) + 1
        cfg["saved_at"] = time.time()
        if reason:
            cfg["saved_reason"] = reason
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        atomic_write_json(paths.BACKUP_DIR / f"config-{stamp}-r{cfg['revision']}.json", cfg, indent=2)
        atomic_write_json(paths.CONFIG_PATH, cfg, indent=2)
        publish(cfg)
        _prune_backups()
    return cfg


def publish(cfg=None):
    """Copy the config into data/ so it rides the normal rsync to the Pi (the v2 display hot-reloads it)."""
    atomic_write_json(paths.DATA_DIR / "config.json", cfg if cfg is not None else load())


def _prune_backups():
    backups = sorted(paths.BACKUP_DIR.glob("config-*.json"))
    for old in backups[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)


def list_backups():
    out = []
    for p in sorted(paths.BACKUP_DIR.glob("config-*.json"), reverse=True):
        d = read_json(p, {}) or {}
        out.append({"file": p.name, "revision": d.get("revision"), "saved_at": d.get("saved_at"),
                    "reason": d.get("saved_reason", "")})
    return out


def validate(cfg):
    if not isinstance(cfg, dict):
        raise ConfigError("config must be an object")
    for key in ("system", "layout", "modules"):
        if not isinstance(cfg.get(key), dict):
            raise ConfigError(f"missing section: {key}")
    mods = cfg["modules"]

    fin = mods.get("finance", {})
    seen = set()
    for i, t in enumerate(fin.get("symbols", [])):
        if not isinstance(t, dict) or not str(t.get("symbol", "")).strip():
            raise ConfigError(f"finance.symbols[{i}] needs a symbol")
        sym = t["symbol"].strip().upper()
        if sym in seen:
            raise ConfigError(f"duplicate ticker {sym}")
        seen.add(sym)
        w = t.get("width")
        if w is not None and (not isinstance(w, int) or not 20 <= w <= 448):
            raise ConfigError(f"{sym}: width must be empty or 20–448 px")

    news = mods.get("headlines", {})
    names = set()
    for i, c in enumerate(news.get("categories", [])):
        name = str(c.get("name", "")).strip()
        if not name:
            raise ConfigError(f"headlines.categories[{i}] needs a name")
        if name in names:
            raise ConfigError(f"duplicate category {name}")
        names.add(name)
        for f in c.get("feeds", []):
            if not str(f.get("url", "")).startswith(("http://", "https://")):
                raise ConfigError(f"{name}: feed URL must start with http(s)://")


    wm = mods.get("weather_maps")
    if wm:
        areas = wm.get("areas") or {}
        for aid, a in areas.items():
            if a.get("fit"):
                try:
                    (n, w) = [float(v) for v in a["fit"]["nw"]]
                    (s_, e) = [float(v) for v in a["fit"]["se"]]
                except (KeyError, TypeError, ValueError):
                    raise ConfigError(f"map area {aid}: fit needs nw and se [lat, lon] corners")
                if not (n > s_ and e > w):
                    raise ConfigError(f"map area {aid}: NW corner must be north-west of the SE corner")
                continue
            try:
                lat, lon = float(a["center"][0]), float(a["center"][1])
                kpp = float(a["km_per_px"])
            except (KeyError, TypeError, ValueError, IndexError):
                raise ConfigError(f"map area {aid}: needs center [lat, lon] and km_per_px")
            if not (-80 <= lat <= 80 and -180 <= lon <= 180):
                raise ConfigError(f"map area {aid}: centre out of range")
            if not 0.1 <= kpp <= 100:
                raise ConfigError(f"map area {aid}: km per pixel must be 0.1–100")
        for tid, t in (wm.get("tracks") or {}).items():
            if not re.fullmatch(r"[A-Za-z0-9_-]+", tid):
                raise ConfigError(f"track id {tid!r}: letters, digits, _ and - only")
            if t.get("area") not in areas:
                raise ConfigError(f"track {tid}: unknown area {t.get('area')!r}")
            if t.get("size", "half") not in ("half", "full"):
                raise ConfigError(f"track {tid}: size must be half or full")
            if t.get("kind", "loop") in ("accumulation", "forecast") and not 0.5 <= float(t.get("window_hours", 24)) <= 48:
                raise ConfigError(f"track {tid}: accumulation window must be 0.5–48 h")
            if not 1 <= float(t.get("fps", 8)) <= 30:
                raise ConfigError(f"track {tid}: fps must be 1–30")

    _validate_webcams(mods.get("webcams"))
    _validate_bridges(mods.get("bridges"))


def _validate_overlay(name, ov):
    """A webcam's caption settings (display/modules/webcam_caption.py); the display also parses them defensively."""
    if ov is None:
        return
    if not isinstance(ov, dict):
        raise ConfigError(f"webcam {name}: overlay must be an object")
    from display.modules.webcam_caption import FONTS, LAYOUTS, SECTIONS, SEPARATORS
    choices = {"style": LAYOUTS, "corner": ("tl", "tc", "tr", "bl", "bc", "br"), "font": tuple(FONTS),
               "headline": ("name", "place", "custom", "time"), "lines": ("auto", "wrap", "stacked", "inline"),
               "separator": tuple(SEPARATORS), "when": ("always", "intro", "periodic"), "time_format": ("24h", "12h"),
               "container": ("box", "strip", "outline", "board"), "taken_format": ("time", "relative", "both")}
    for key, allowed in choices.items():
        if key in ov and ov[key] not in allowed:
            raise ConfigError(f"webcam {name}: caption {key} must be one of {', '.join(allowed)}")
    for key, lo, hi in (("opacity", 0, 1), ("show_sec", 3, 600), ("every_min", 0.5, 120)):
        if key in ov:
            try:
                ok = lo <= float(ov[key]) <= hi
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ConfigError(f"webcam {name}: caption {key} must be between {lo} and {hi}")
    show = ov.get("show", {})
    if not isinstance(show, dict) or any(k not in SECTIONS for k in show):
        raise ConfigError(f"webcam {name}: caption show has an unknown section")
    order = ov.get("order", [])
    if not isinstance(order, list):
        raise ConfigError(f"webcam {name}: caption order must be a list")
    from display.modules.webcam_caption import FIELDS, LINE_SIZES
    slots = ov.get("slots", {})
    if not isinstance(slots, dict):
        raise ConfigError(f"webcam {name}: caption slots must be an object")
    for kind, slot in slots.items():
        ok = kind in FIELDS and isinstance(slot, list) and len(slot) in (2, 3) \
            and all(isinstance(v, int) and not isinstance(v, bool) for v in slot[:2]) \
            and 1 <= slot[0] <= 3 and 1 <= slot[1] <= 4 and (len(slot) == 2 or slot[2] in ("left", "right"))
        if not ok:
            raise ConfigError(f"webcam {name}: caption slot for {kind} must be [column 1-3, line 1-4, left|right]")
    sizes = ov.get("line_sizes", {})
    if not isinstance(sizes, dict) or any(v not in LINE_SIZES or not re.fullmatch(r"[1-3]\.[1-4]", str(key))
                                          for key, v in sizes.items()):
        raise ConfigError(f"webcam {name}: caption line sizes must be headline, detail or big")
    cols = ov.get("columns", [])
    if not isinstance(cols, list) or any(not isinstance(c, dict) or c.get("align", "left") not in ("left", "center", "right")
                                         for c in cols):
        raise ConfigError(f"webcam {name}: caption columns must be [{{align: left|center|right}}, …]")


def _validate_bridges(br):
    """modules.bridges (hub/jobs/bridges.py)."""
    if not br:
        return
    ps = br.get("page_sec")
    if ps is not None and (isinstance(ps, bool) or not isinstance(ps, (int, float)) or not 2 <= ps <= 60):
        raise ConfigError("bridges.page_sec must be 2–60 seconds")
    cams = br.get("cams")
    if cams is None:
        return
    if not isinstance(cams, list) or len(cams) > 12:
        raise ConfigError("bridges.cams must be a list of at most 12 bridges")
    for i, c in enumerate(cams):
        if not isinstance(c, dict) or not str(c.get("label", "")).strip():
            raise ConfigError(f"bridges.cams[{i}] needs a label")
        ids = c.get("ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(x, int) and not isinstance(x, bool) and x > 0
                                                            for x in ids):
            raise ConfigError(f"bridge {c['label']}: pick a DriveBC camera")


def _validate_webcams(wc):
    """modules.webcams (hub/webcams.py, display/modules/webcam.py)."""
    if not wc:
        return
    from hub.webcams import SourceError, check_source
    cams = wc.get("cams") or {}
    if not isinstance(cams, dict):
        raise ConfigError("webcams.cams must be an object")
    for cid, cam in cams.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+", cid):
            raise ConfigError(f"webcam id {cid!r}: letters, digits, _ and - only")
        if not isinstance(cam, dict):
            raise ConfigError(f"webcam {cid}: must be an object")
        name = cam.get("label") or cid
        try:
            check_source(cam.get("source"))
        except SourceError as e:
            raise ConfigError(f"webcam {name}: {e}")
        for key, lim in (("lat", 90), ("lon", 180)):
            v = cam.get(key)
            if v not in (None, ""):
                try:
                    ok = -lim <= float(v) <= lim
                except (TypeError, ValueError):
                    ok = False
                if not ok:
                    raise ConfigError(f"webcam {name}: {key} must be a number between -{lim} and {lim}")
        try:
            fps_ok = 1 <= float(cam.get("fps", 15)) <= 30
        except (TypeError, ValueError):
            fps_ok = False
        if not fps_ok:
            raise ConfigError(f"webcam {name}: frame rate must be 1–30")
        refresh = (cam.get("source") or {}).get("refresh_sec")
        if refresh not in (None, ""):
            try:
                ok = 20 <= float(refresh) <= 3600
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ConfigError(f"webcam {name}: fetch every must be 20-3600 s")
        _validate_overlay(name, cam.get("overlay"))
        crops = cam.get("crops") or {}
        if not isinstance(crops, dict):
            raise ConfigError(f"webcam {name}: crops must be an object")
        for shape, crop in crops.items():
            if shape not in ("left", "right", "full", "wall"):
                raise ConfigError(f"webcam {name}: unknown crop {shape!r}")
            try:
                ok = 0 <= float(crop["x"]) <= 1 and 0 <= float(crop["y"]) <= 1 and 0.02 <= float(crop["w"]) <= 1
            except (KeyError, TypeError, ValueError):
                ok = False
            if not ok:
                raise ConfigError(f"webcam {name}: the {shape} crop needs x, y and w between 0 and 1")


def get_path(cfg, dotted, default=None):
    cur = cfg
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur
