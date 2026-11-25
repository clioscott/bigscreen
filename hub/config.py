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
    _validate_iss_map(mods.get("iss_map"))
    _validate_sats_overhead(mods.get("sats_overhead"))
    _validate_bridges(mods.get("bridges"))
    _validate_timezones(mods.get("timezones"))


def _validate_timezones(tz):
    """modules.timezones (display/modules/tzmap.py)."""
    if not tz:
        return
    if not isinstance(tz, dict):
        raise ConfigError("timezones must be an object")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    for key, lo, hi in (("warn_days", 0, 60), ("page_sec", 3, 120), ("fill", 0.15, 1)):
        v = tz.get(key)
        if v is not None and (not num(v) or not lo <= v <= hi):
            raise ConfigError(f"timezones.{key} must be {lo}–{hi}")
    v = tz.get("refresh_interval_sec")
    if v is not None and (not num(v) or v < 600):
        raise ConfigError("timezones.refresh_interval_sec must be at least 600")
    ct = tz.get("city_times")
    if ct is not None and not isinstance(ct, bool) and ct not in ("off", "after", "below"):
        raise ConfigError("timezones.city_times must be off, after or below")
    for key, ok in (("warn_pages", ("both", "upcoming")), ("time_format", ("12h", "24h")),
                    ("map_labels", ("auto", "on", "off"))):
        if tz.get(key) is not None and tz[key] not in ok:
            raise ConfigError(f"timezones.{key} must be one of {', '.join(ok)}")
    if tz.get("home_zone") is not None and (not isinstance(tz["home_zone"], str) or "/" not in tz["home_zone"]):
        raise ConfigError("timezones.home_zone must be a zone like America/Vancouver")
    mh = tz.get("map_label_hide")
    if mh is not None and (not isinstance(mh, list) or not all(isinstance(k, str) and re.fullmatch(r"(CA|US):-?\d+", k)
                                                                for k in mh)):
        raise ConfigError("timezones.map_label_hide must be a list like [\"CA:-240\"]")
    ml = tz.get("map_label_lines")
    if ml is not None:
        if not isinstance(ml, list) or len(ml) > 6 or not all(isinstance(ln, dict) for ln in ml):
            raise ConfigError("timezones.map_label_lines must be a list of at most 6 lines")
        for ln in ml:
            if ln.get("kind") not in ("time", "home", "utc", "zone", "name", "day"):
                raise ConfigError("timezones.map_label_lines: kind must be time, home, utc, zone, name or day")
            col = ln.get("color")
            if col is not None and col != "zone" and not (isinstance(col, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", col)):
                raise ConfigError("timezones.map_label_lines: color must be \"zone\" or #rrggbb")
            for key in ("prefix", "home", "same", "style"):
                if ln.get(key) is not None and (not isinstance(ln[key], str) or len(ln[key]) > 12):
                    raise ConfigError(f"timezones.map_label_lines: {key} must be text of at most 12 characters")
            if ln.get("kind") == "zone" and (not isinstance(ln.get("zone"), str) or "/" not in ln["zone"]):
                raise ConfigError("timezones.map_label_lines: an offset from a zone needs a zone like Europe/London")
    for key in ("title", "upcoming_label", "now_label", "test_label"):
        if tz.get(key) is not None and (not isinstance(tz[key], str) or len(tz[key]) > 30):
            raise ConfigError(f"timezones.{key} must be text of at most 30 characters")
    if tz.get("legend_rows") is not None and tz["legend_rows"] not in ("traditional", "offset"):
        raise ConfigError("timezones.legend_rows must be traditional or offset")
    if tz.get("label_style") is not None and tz["label_style"] not in ("name", "abbr"):
        raise ConfigError("timezones.label_style must be name or abbr")
    lh = tz.get("list_hide")
    if lh is not None and (not isinstance(lh, list) or not all(isinstance(z, str) for z in lh)):
        raise ConfigError("timezones.list_hide must be a list of zone names")
    for key in ("names", "offset_names", "places", "colors", "country_labels", "country_short", "row_names",
                "rule_labels"):
        d = tz.get(key)
        if d is None:
            continue
        if not isinstance(d, dict) or not all(isinstance(v, str) and len(v) <= 30 for v in d.values()):
            raise ConfigError(f"timezones.{key} must map names to short text")
    for k, v in (tz.get("colors") or {}).items():
        if not (len(v) == 7 and v.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in v[1:])):
            raise ConfigError(f"timezones colour for {k} must be #rrggbb")
    v = tz.get("canada_boost")
    if v is not None and (not num(v) or not 0.5 <= v <= 2):
        raise ConfigError("timezones.canada_boost must be 0.5–2")
    for k in list(tz.get("colors") or {}) + list(tz.get("row_names") or {}) + \
            [k.split(":")[-1] for k in tz.get("offset_names") or {}]:
        try:
            int(k)
        except ValueError:
            raise ConfigError(f"timezones: {k!r} isn't an offset in minutes")
    cities = tz.get("cities")
    if cities is not None:
        if not isinstance(cities, list) or len(cities) > 60:
            raise ConfigError("timezones.cities must be a list of at most 60 cities")
        for i, c in enumerate(cities):
            if not isinstance(c, dict) or not str(c.get("name", "")).strip():
                raise ConfigError(f"timezones.cities[{i}] needs a name")
            if not (num(c.get("lat")) and num(c.get("lon")) and -90 <= c["lat"] <= 90 and -180 <= c["lon"] <= 180):
                raise ConfigError(f"city {c['name']}: latitude and longitude please")


def _validate_rows(name, rows):
    """overlay "rows": [{"items": [{"k": section | "name:N" | "live" | "shield" | "sep", "size"?, "s"? (a separator's
    kind), "pad"?, "spill"?}, …], "wrap"?}, …] (display/modules/webcam_caption.py rows_of)."""
    if rows is None:
        return
    from display.modules.webcam_caption import MAX_ROWS, NAME_LINES, SECTIONS, SEP_KINDS, SIZES
    bad = f"webcam {name}: caption rows must be up to {MAX_ROWS} rows of pieces"
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ConfigError(bad)
    kinds = set(SECTIONS) | {"sep", "live", "shield"} | {f"name:{i}" for i in range(2, NAME_LINES + 1)}
    for r in rows:
        if not isinstance(r, dict) or not isinstance(r.get("items"), list) or len(r["items"]) > 40                 or not isinstance(r.get("wrap", False), bool):
            raise ConfigError(bad)
        for it in r["items"]:
            if not isinstance(it, dict) or it.get("k") not in kinds or it.get("size", "small") not in SIZES                     or (it.get("k") == "sep" and it.get("s") not in SEP_KINDS)                     or not all(isinstance(it.get(f, False), bool) for f in ("pad", "spill")):
                raise ConfigError(f"webcam {name}: caption piece {str(it)[:60]} isn't one the caption knows")


def _validate_map_extras(name, ex):
    """The ISS map's extras (display/modules/issmap.py EXTRAS): each {on, …its settings}."""
    from display.modules.issmap import EXTRAS, FLOW_SPEED, SHADOW, TAG_PLACES, TAG_STYLES, TELEMETRY_ROWS
    bad = lambda what: ConfigError(f"webcam {name}: map extras: {what}")
    if not isinstance(ex, dict):
        raise bad("must be an object")
    choices = {("sunlit", "shadow"): tuple(SHADOW), ("flow", "speed"): tuple(FLOW_SPEED),
               ("flow", "direction"): ("both", "down", "up"), ("telemetry", "corner"): ("tl", "tr"),
               ("docked", "corner"): ("tl", "tr"), ("grid", "lines"): ("grid", "special", "both"),
               ("signal", "place"): TAG_PLACES, ("signal", "style"): TAG_STYLES}
    ranges = {("lights", "strength"): (0.1, 1.0), ("aurora", "strength"): (0.1, 1.0), ("ahead", "minutes"): (5, 90),
              ("trail", "minutes"): (5, 95), ("night", "darkness"): (0.0, 0.95), ("grid", "step"): (10, 90),
              ("footprint", "elevation"): (0, 45), ("launches", "count"): (1, 6)}
    for k, v in ex.items():
        if k not in EXTRAS:
            raise bad(f"unknown extra {k!r}")
        if not isinstance(v, dict) or any(s not in EXTRAS[k] for s in v):
            raise bad(f"{k} takes {', '.join(EXTRAS[k])}")
        for s, val in v.items():
            d = EXTRAS[k][s]
            if (k, s) in choices:
                if val not in choices[(k, s)]:
                    raise bad(f"{k} {s} must be one of {', '.join(choices[(k, s)])}")
            elif (k, s) in ranges:
                lo, hi = ranges[(k, s)]
                if isinstance(val, bool) or not isinstance(val, (int, float)) or not lo <= val <= hi:
                    raise bad(f"{k} {s} must be {lo}-{hi}")
            elif k == "telemetry" and s == "rows":
                if not isinstance(val, list) or any(r not in TELEMETRY_ROWS for r in val) or len(set(val)) != len(val):
                    raise bad(f"telemetry rows must be some of {', '.join(TELEMETRY_ROWS)}")
            elif isinstance(d, bool) and not isinstance(val, bool):
                raise bad(f"{k} {s} must be true or false")


def _validate_overlays(name, per):
    """A webcam's captions of their own for sizes on the wall (webcam_caption.for_shape): {size: overlay}."""
    if per is None:
        return
    from display.modules.webcam_caption import SIZES_ON_WALL
    if not isinstance(per, dict) or any(k not in SIZES_ON_WALL for k in per):
        raise ConfigError(f"webcam {name}: captions per size are for {', '.join(SIZES_ON_WALL)}")
    for size, ov in per.items():
        if not isinstance(ov, dict):
            raise ConfigError(f"webcam {name}: the {size} caption must be an object")
        _validate_overlay(f"{name} ({size})", ov)


def _validate_overlay(name, ov):
    """A webcam's caption settings (display/modules/webcam_caption.py); the display also parses them defensively."""
    if ov is None:
        return
    if not isinstance(ov, dict):
        raise ConfigError(f"webcam {name}: overlay must be an object")
    from display.modules.webcam_caption import FONTS, JP_FACES, LAYOUTS, SECTIONS, SEPARATORS
    old_styles = ("bar", "outline", "dot", "lower_third", "split", "postcard", "chips", "clock", "signage", "ticker",
                  "stack", "underline", "custom")   # retired with rows (2026-11): still accepted, drawn as tag
    choices = {"style": LAYOUTS + old_styles, "corner": ("tl", "tc", "tr", "bl", "bc", "br"), "font": tuple(FONTS),
               "headline": ("name", "place", "custom", "time"), "lines": ("auto", "wrap", "stacked", "inline"),
               "separator": tuple(SEPARATORS), "when": ("always", "intro", "periodic"), "time_format": ("24h", "12h"),
               "container": ("box", "strip", "outline", "board"), "taken_format": ("time", "relative", "both"),
               "text_align": ("auto", "left", "center", "right"), "jp_font": ("auto",) + tuple(FONTS),
               "jp_face": ("auto",) + tuple(JP_FACES)}
    for key, allowed in choices.items():
        if key in ov and ov[key] not in allowed:
            raise ConfigError(f"webcam {name}: caption {key} must be one of {', '.join(allowed)}")
    if "box_w" in ov and ov["box_w"] not in ("auto", "full") and not (
            isinstance(ov["box_w"], (int, float)) and not isinstance(ov["box_w"], bool) and 0 <= ov["box_w"] <= 448):
        raise ConfigError(f"webcam {name}: caption width must be auto, full or 0-448 px")
    for key, lo, hi in (("opacity", 0, 1), ("show_sec", 3, 600), ("every_min", 0.5, 120), ("margin", -1, 32),
                        ("pad", 0, 10)):
        if key in ov:
            try:
                ok = lo <= float(ov[key]) <= hi
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ConfigError(f"webcam {name}: caption {key} must be between {lo} and {hi}")
    mm = ov.get("minimap")
    if mm is not None and not (isinstance(mm, dict) and isinstance(mm.get("on", False), bool)
                               and mm.get("corner", "tr") in ("tl", "tr", "bl", "br")):
        raise ConfigError(f"webcam {name}: the small map needs on (true or false) and a corner (tl, tr, bl, br)")
    jc = ov.get("jp_caption")
    if jc is not None:                                     # the separate Japanese caption
        corners = ("auto", "tl", "tc", "tr", "bl", "bc", "br")
        if not isinstance(jc, dict) or not isinstance(jc.get("text", ""), str) or len(jc.get("text", "")) > 400 \
                or jc.get("face", "k8x12") not in JP_FACES or jc.get("corner", "auto") not in corners \
                or not isinstance(jc.get("size", 12), (int, float)) or isinstance(jc.get("size", 12), bool) \
                or not 1 <= jc.get("size", 12) <= 64:
            raise ConfigError(f"webcam {name}: the Japanese caption needs text (up to 400 characters), a Japanese "
                              "font, a size and a position")
    fl = ov.get("flags")
    if fl is not None:                                     # the row of flags (display/modules/caption_flags.py)
        ids = fl.get("ids") if isinstance(fl, dict) else None
        if not isinstance(fl, dict) or not set(fl) <= {"on", "ids", "corner", "size", "opacity"} \
                or not isinstance(fl.get("on", True), bool) \
                or fl.get("corner", "auto") not in ("auto", "tl", "tc", "tr", "bl", "bc", "br", "left", "right") \
                or fl.get("size", "auto") not in ("auto", 18, 23) \
                or isinstance(fl.get("opacity", 1), bool) or not isinstance(fl.get("opacity", 1), (int, float)) \
                or not 0.1 <= fl.get("opacity", 1) <= 1 \
                or not (ids is None or (isinstance(ids, list) and len(ids) <= 6 and all(
                    isinstance(i, str) and re.fullmatch(r"[a-z0-9-]{2,40}", i) for i in ids))):
            raise ConfigError(f"webcam {name}: flags need on (true or false), up to 6 flags (or none for automatic), "
                              "a position, a size (auto, 18 or 23) and an opacity of 0.1-1")
    show = ov.get("show", {})
    if not isinstance(show, dict) or any(k not in SECTIONS for k in show):
        raise ConfigError(f"webcam {name}: caption show has an unknown section")
    order = ov.get("order", [])
    if not isinstance(order, list):
        raise ConfigError(f"webcam {name}: caption order must be a list")
    _validate_rows(name, ov.get("rows"))
    shield = ov.get("shield", {})
    from display.modules.webcam_caption import SHIELD_KINDS, SHIELD_MAX, SHIELD_MIN, SHIELD_PLACES, SHIELD_STYLES
    size = shield.get("size", "box") if isinstance(shield, dict) else None
    if not isinstance(shield, dict) or shield.get("kind", "auto") not in SHIELD_KINDS \
            or shield.get("place", "inside") not in SHIELD_PLACES or shield.get("style", "pixel") not in SHIELD_STYLES \
            or not isinstance(shield.get("route", ""), (str, int)) or isinstance(shield.get("route", ""), bool) \
            or len(str(shield.get("route", ""))) > 8 \
            or not (size == "box" or (isinstance(size, int) and not isinstance(size, bool)
                                      and SHIELD_MIN <= size <= SHIELD_MAX)):
        raise ConfigError(f"webcam {name}: caption shield must be "
                          f"{{route, kind, style: pixel|real, place: inside|left|right|line, "
                          f"size: box|{SHIELD_MIN}-{SHIELD_MAX}}}")


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


def _validate_iss_map(m, name="Space Map"):
    """modules.iss_map, the Space Map (display/modules/iss_panel.py): a label, a caption (overlay) and the map's
    settings: views per size, link looks, relays and ground stations, labels and names, satellite layers, panels."""
    if m is None:
        return
    if not isinstance(m, dict):
        raise ConfigError(f"{name}: must be an object")
    if not isinstance(m.get("label", ""), str):
        raise ConfigError(f"{name}: label must be text")
    v = m.get("sats_interval_sec")
    if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v < 300):
        raise ConfigError(f"{name}: sats_interval_sec must be at least 300")
    _validate_overlay(name, m.get("overlay"))
    _validate_overlays(name, m.get("overlays"))
    mp = m.get("map")
    if mp is None:
        return
    _validate_space_map(mp, name)


def _validate_sats_overhead(m):
    """modules.sats_overhead, Satellites Overhead (the Space Map held over home): the Space Map's settings, plus home
    ([lat, lon], or none: the weather alerts' home) and map.overhead (issmap.OVERHEAD_DEFAULTS)."""
    if m is None:
        return
    _validate_iss_map(m, "Satellites Overhead")
    h = m.get("home")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    if h is not None and not (isinstance(h, list) and len(h) == 2 and all(map(num, h))
                              and -80 <= h[0] <= 80 and -180 <= h[1] <= 180):
        raise ConfigError("Satellites Overhead: home must be [latitude, longitude] (or left out)")


SPACE_MAP_KEYS = {"when_down", "fixed", "extras", "views", "links", "relays", "ground", "labels", "names", "sats",
                  "panels", "units", "icons", "video", "overhead"}


def _validate_space_map(mp, name="Space Map"):
    from common.satlayers import LAYERS
    from display.modules import issmap, maplabels, mappanels, mapsats
    bad = lambda what: ConfigError(f"{name}: {what}")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    colour = lambda v: isinstance(v, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", v)
    if not isinstance(mp, dict) or not set(mp) <= SPACE_MAP_KEYS or not isinstance(mp.get("fixed", False), bool):
        raise bad(f"map takes {', '.join(sorted(SPACE_MAP_KEYS))}")
    if mp.get("extras") is not None:
        _validate_map_extras("Space Map", mp["extras"])
    views = mp.get("views")
    if views is not None:
        from display.modules.webcam_caption import SIZES_ON_WALL
        if not isinstance(views, dict) or any(k not in SIZES_ON_WALL for k in views):
            raise bad(f"views are per size: {', '.join(SIZES_ON_WALL)}")
        for size, vw in views.items():
            if not isinstance(vw, dict) or not set(vw) <= set(issmap.VIEW):
                raise bad(f"the {size} view takes {', '.join(issmap.VIEW)}")
            for k, (lo, hi) in issmap.VIEW_RANGES.items():
                x = vw.get(k)
                if x is not None and (not num(x) or not lo <= x <= hi):
                    raise bad(f"the {size} view's {k} must be {lo}-{hi}")
            if not isinstance(vw.get("clear", True), bool):
                raise bad(f"the {size} view's clear must be true or false")
    lk = mp.get("links")
    if lk is not None:
        if not isinstance(lk, dict) or any(k not in issmap.NETS for k in lk):
            raise bad(f"links are per network: {', '.join(issmap.NETS)}")
        for net, st in lk.items():
            if not isinstance(st, dict) or not set(st) <= set(issmap.LINK_DEFAULTS[net]):
                raise bad(f"{net} links take {', '.join(issmap.LINK_DEFAULTS[net])}")
            if any(k in st and not colour(st[k]) for k in ("up", "down")):
                raise bad(f"{net} link colours must be like #ff8800")
            if st.get("style", "dots") not in issmap.LINK_STYLES or st.get("speed", "normal") not in issmap.FLOW_SPEED \
                    or st.get("direction", "both") not in ("both", "down", "up") \
                    or not (num(st.get("line", 1.0)) and 0 <= st.get("line", 1.0) <= 1):
                raise bad(f"{net} links: style {'/'.join(issmap.LINK_STYLES)}, speed, direction, line 0-1")
    oh = mp.get("overhead")
    if oh is not None:
        d = issmap.OVERHEAD_DEFAULTS
        lo_km, hi_km = issmap.OVERHEAD_KM
        r_km = oh.get("radius_km", 0) if isinstance(oh, dict) else 0
        if (not isinstance(oh, dict) or not set(oh) <= set(d) or oh.get("layer", d["layer"]) not in LAYERS
                or any(not isinstance(oh.get(b, True), bool) for b in ("ring", "home"))
                or not (num(oh.get("min_el", 10)) and 0 <= oh.get("min_el", 10) <= 60)
                or not (num(oh.get("outside", 0.3)) and 0 <= oh.get("outside", 0.3) <= 1)
                or not (num(oh.get("margin", 1.08)) and 1 <= oh.get("margin", 1.08) <= 2)
                or not (num(r_km) and (r_km == 0 or lo_km <= r_km <= hi_km))):
            raise bad(f"overhead takes {', '.join(d)}: layer one of the satellite layers, min_el 0-60°, "
                      f"outside 0-1, margin 1-2, radius_km 0 (fit the ring) or {lo_km}-{hi_km}, ring and home true or false")
    if mp.get("relays", "always") not in issmap.RELAY_SHOW:
        raise bad(f"relays must be {' or '.join(issmap.RELAY_SHOW)}")
    if mp.get("ground", "always") not in ("always", "linked", "hidden"):
        raise bad("ground stations must be always, linked or hidden")
    if mp.get("icons", "auto") not in ("auto", "small", "big"):
        raise bad("icons must be auto, small or big")
    labels = mp.get("labels")
    if labels is not None:
        if not isinstance(labels, dict) or any(k not in maplabels.KINDS for k in labels):
            raise bad(f"labels are for {', '.join(maplabels.KINDS)}")
        for kind, L in labels.items():
            d = maplabels.LABEL_DEFAULTS[kind]
            if not isinstance(L, dict) or not set(L) <= set(d):
                raise bad(f"{kind} labels take {', '.join(d)}")
            if L.get("pos", "auto") not in maplabels.PLACES or ("colour" in L and not colour(L["colour"])) \
                    or L.get("edge", "outline") not in maplabels.EDGES \
                    or any(not isinstance(L[k], bool) for k in L if isinstance(d[k], bool)):
                raise bad(f"{kind} labels: pos {'/'.join(maplabels.PLACES)}, edge {'/'.join(maplabels.EDGES)}, "
                          "a colour like #ff8800, the rest true/false")
            if kind == "iss" and (not (isinstance(L.get("flag", "globe"), bool) or L.get("flag", "globe") in maplabels.ISS_FLAGS)
                                  or any(L.get(k, "label") not in maplabels.FLAG_AT for k in ("us_at", "ru_at"))
                                  or not (isinstance(L.get("text", "ISS"), str) and len(L.get("text", "ISS")) <= 40)):
                raise bad(f"the ISS's label: flag {'/'.join(maplabels.ISS_FLAGS)}, us_at and ru_at "
                          f"{'/'.join(maplabels.FLAG_AT)}, text up to 40 letters")
    names = mp.get("names")
    if names is not None and (not isinstance(names, dict) or len(names) > 500
                              or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 40 for k, v in names.items())):
        raise bad("names: your own name (up to 40 letters) for each object")
    sats = mp.get("sats")
    if sats is not None:
        if not isinstance(sats, dict) or any(k not in LAYERS for k in sats):
            raise bad(f"satellite layers are {', '.join(LAYERS)}")
        for k, st in sats.items():
            if not isinstance(st, dict) or not set(st) <= set(mapsats.SAT_DEFAULTS):
                raise bad(f"{k} takes {', '.join(mapsats.SAT_DEFAULTS)}")
            if st.get("mark", "auto") not in mapsats.MARKS or (st.get("colour") is not None and not colour(st["colour"])) \
                    or any(not isinstance(st.get(b, False), bool) for b in ("on", "label", "links")) \
                    or not (num(st.get("near_km", 0)) and 0 <= st.get("near_km", 0) <= 20000) \
                    or not (num(st.get("brightness", 0.7)) and 0.1 <= st.get("brightness", 0.7) <= 1) \
                    or not (isinstance(st.get("tracks", 0), int) and not isinstance(st.get("tracks", 0), bool)
                            and 0 <= st.get("tracks", 0) <= mapsats.TRACKS_MAX) \
                    or st.get("speed", "normal") not in issmap.FLOW_SPEED:
                raise bad(f"{k}: mark {'/'.join(mapsats.MARKS)}, a colour like #ff8800, brightness 0.1-1, near_km 0-20000, "
                          f"tracks 0-{mapsats.TRACKS_MAX}")
    units = mp.get("units")
    if units is not None and (not isinstance(units, dict) or any(k not in mappanels.UNITS or v not in mappanels.UNITS[k]
                                                                for k, v in units.items())):
        raise bad("units: " + "; ".join(f"{k} {'/'.join(v)}" for k, v in mappanels.UNITS.items()))
    vid = mp.get("video")
    if vid is not None:
        if not isinstance(vid, dict) or not set(vid) <= set(mappanels.VIDEO_DEFAULTS) \
                or any(not isinstance(vid.get(b, True), bool) for b in ("on", "border", "tag")) \
                or vid.get("pos", "br") not in mappanels.POSITIONS \
                or not (num(vid.get("width", 96)) and mappanels.VIDEO_W[0] <= vid.get("width", 96) <= mappanels.VIDEO_W[1]) \
                or not (vid.get("cam") is None or isinstance(vid.get("cam"), str)):
            raise bad(f"video: on, cam (a webcam id), pos (one of the nine places), width "
                      f"{mappanels.VIDEO_W[0]}-{mappanels.VIDEO_W[1]} px, border and tag (true or false)")
    panels = mp.get("panels")
    if panels is not None:
        if not isinstance(panels, list) or len(panels) > 40:
            raise bad("panels must be a list (up to 40)")
        for i, p in enumerate(panels):
            if not isinstance(p, dict) or not set(p) <= set(mappanels.PANEL_DEFAULTS) | {"id"}:
                raise bad(f"panel {i + 1} takes {', '.join(mappanels.PANEL_DEFAULTS)}")
            f = p.get("fields", [])
            if not isinstance(f, list) or any(x not in mappanels.FIELDS for x in f) or len(set(f)) != len(f):
                raise bad(f"panel {i + 1}: fields must be some of the known ones, each once")
            if p.get("pos", "tr") not in mappanels.POSITIONS or p.get("layout", "rows") not in mappanels.LAYOUTS \
                    or p.get("font", "tiny") not in mappanels.PANEL_FONTS \
                    or not isinstance(p.get("title", ""), str) or len(p.get("title", "")) > 30 \
                    or any(not isinstance(p.get(b, True), bool) for b in ("on", "labels", "flags")) \
                    or not (num(p.get("opacity", 0.7)) and 0 <= p.get("opacity", 0.7) <= 1) \
                    or any(k in p and not colour(p[k]) for k in ("colour", "accent")) \
                    or p.get("crew_names", "full") not in mappanels.CREW_NAMES \
                    or p.get("when", "always") not in mappanels.WHEN \
                    or not isinstance(p.get("sparks", []), list) or any(k not in mappanels.SERIES_OF for k in p.get("sparks", [])) \
                    or not (num(p.get("spark_min", 90)) and mappanels.SPARK_MIN[0] <= p.get("spark_min", 90) <= mappanels.SPARK_MIN[1]) \
                    or not (num(p.get("spark_w", 30)) and mappanels.SPARK_W[0] <= p.get("spark_w", 30) <= mappanels.SPARK_W[1]) \
                    or ("spark_colour" in p and not colour(p["spark_colour"])):
                raise bad(f"panel {i + 1}: place, layout, font, title (up to 30), opacity 0-1, colours like #ff8800, "
                          f"when always/eva, sparklines for {', '.join(mappanels.SERIES_OF)} over "
                          f"{mappanels.SPARK_MIN[0]}-{mappanels.SPARK_MIN[1]} min, {mappanels.SPARK_W[0]}-{mappanels.SPARK_W[1]} px wide")


def _validate_webcams(wc):
    """modules.webcams (hub/webcams.py, display/modules/webcam.py)."""
    if not wc:
        return
    from hub.webcams import SourceError, check_source
    cams = wc.get("cams") or {}
    if not isinstance(cams, dict):
        raise ConfigError("webcams.cams must be an object")
    # the list the rotation's Webcams entry plays: [{"cam", "sec", "on"}], in order
    playlist = wc.get("playlist")
    if playlist is not None:
        if not isinstance(playlist, list):
            raise ConfigError("webcams.playlist must be a list")
        for i, item in enumerate(playlist):
            if not isinstance(item, dict) or not isinstance(item.get("cam"), str):
                raise ConfigError(f"webcams.playlist[{i}] needs a webcam")
            try:
                ok = 5 <= float(item.get("sec", 60)) <= 3600
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ConfigError(f"webcam {item['cam']} in the list: seconds must be 5-3600")
    grid = wc.get("grid")
    if grid is not None:
        if not isinstance(grid, dict) or not isinstance(grid.get("cams", []), list):
            raise ConfigError("webcams.grid needs a list of cameras")
        for i, gc in enumerate(grid.get("cams", [])):
            if not isinstance(gc, dict):
                raise ConfigError(f"camera grid #{i + 1} must be an object")
            try:
                kind, _ = check_source((gc or {}).get("source"))
            except SourceError as e:
                raise ConfigError(f"camera grid #{i + 1}: {e}")
            if kind not in ("drivebc", "image"):
                raise ConfigError(f"camera grid #{i + 1}: the grid shows still cameras only")
        near = grid.get("near", {})
        if not isinstance(near, dict):
            raise ConfigError("webcams.grid.near must be an object")
        if near.get("on"):
            try:
                ok = -90 <= float(near["lat"]) <= 90 and -180 <= float(near["lon"]) <= 180                     and 0.05 <= float(near.get("radius", 1)) <= 50
            except (KeyError, TypeError, ValueError):
                ok = False
            if not ok:
                raise ConfigError("nearby cameras: a latitude, longitude and a radius of 0.05-50 please")
        for key, allowed in (("unit", ("km", "mi")), ("kinds", ("city", "all"))):
            if key in near and near[key] not in allowed:
                raise ConfigError(f"nearby cameras: {key} must be one of {', '.join(allowed)}")
        lab = grid.get("label", {})
        from display.modules.cam_names import PARTS
        if not isinstance(lab, dict):
            raise ConfigError("webcams.grid.label must be an object")
        lines = lab.get("lines", [])
        if not isinstance(lines, list) or len(lines) > 3 or not all(
                isinstance(ln, list) and all(p in PARTS for p in ln) for ln in lines):
            raise ConfigError(f"camera grid labels: up to 3 lines of {', '.join(PARTS)}")
        for key, allowed in (("at", ("bottom", "top")), ("dir", ("tag", "line", "off")), ("dir_style", ("short", "full")),
                             ("size", ("tiny", "small"))):
            if key in lab and lab[key] not in allowed:
                raise ConfigError(f"camera grid labels: {key} must be one of {', '.join(allowed)}")
        if grid.get("cols") not in (None, "auto") and not (isinstance(grid["cols"], int) and 1 <= grid["cols"] <= 10):
            raise ConfigError("webcams.grid.cols must be auto or 1-10")
        if "group_views" in grid and not isinstance(grid["group_views"], bool):
            raise ConfigError("webcams.grid.group_views must be true or false")
        for key, lo, hi in (("interval_ms", 50, 5000), ("hold_sec", 0, 30), ("rows", 1, 4), ("view_ms", 200, 10000)):
            v = grid.get(key)
            if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not lo <= v <= hi):
                raise ConfigError(f"webcams.grid.{key} must be {lo}-{hi}")
    per = wc.get("per_turn", 1)
    if per != "all" and (not isinstance(per, int) or isinstance(per, bool) or not 1 <= per <= 50):
        raise ConfigError('webcams.per_turn must be 1-50 or "all"')
    if not isinstance(wc.get("from_top", True), bool):
        raise ConfigError("webcams.from_top must be true or false")
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
        city = cam.get("wx_city")
        if city is not None:                               # the city the weather comes from (panel: city search)
            try:
                ok = isinstance(city, dict) and isinstance(city.get("name"), str) and city["name"].strip() \
                    and -90 <= float(city["lat"]) <= 90 and -180 <= float(city["lon"]) <= 180
            except (KeyError, TypeError, ValueError):
                ok = False
            if not ok:
                raise ConfigError(f"webcam {name}: the weather city needs a name, latitude and longitude")
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
        hours = cam.get("hours")
        if hours is not None:
            import re as _re
            ok = isinstance(hours, dict) and all(isinstance(hours.get(k), str) and _re.fullmatch(
                r"([01]?\d|2[0-3]):[0-5]\d|24:00", hours[k]) for k in ("from", "to"))
            if not ok:
                raise ConfigError(f'webcam {name}: hours must be {{"from": "HH:MM", "to": "HH:MM"}}')
        if not isinstance(cam.get("daylight_only", True), bool):
            raise ConfigError(f"webcam {name}: daylight_only must be true or false")
        if not isinstance(cam.get("full_only", False), bool):
            raise ConfigError(f"webcam {name}: full_only must be true or false")
        for key, pat in (("country", r"[A-Za-z]{2}"), ("region", r"[A-Za-z0-9]{1,3}")):
            v = cam.get(key)                               # where the webcam is (the caption's automatic flags)
            if v not in (None, "") and not (isinstance(v, str) and re.fullmatch(pat, v)):
                raise ConfigError(f"webcam {name}: {key} must be a code like "
                                  + ('"ca" or "jp"' if key == "country" else '"BC", "MA" or "13"'))
        if cam.get("space") not in (None, "iss"):
            raise ConfigError(f'webcam {name}: space must be "iss" (the only place in space with webcams)')
        mp = cam.get("map")                                # the ISS map (display/modules/issmap.py)
        if mp is not None and not (isinstance(mp, dict) and set(mp) <= {"when_down", "fixed", "extras"}
                                   and all(isinstance(mp.get(k, False), bool) for k in ("when_down", "fixed"))):
            raise ConfigError(f"webcam {name}: map takes when_down and fixed (each true or false) and extras")
        if isinstance(mp, dict) and mp.get("extras") is not None:
            _validate_map_extras(name, mp["extras"])
        q = cam.get("quality")
        if q not in (None, "auto", "max") and not (isinstance(q, int) and not isinstance(q, bool) and 144 <= q <= 4320):
            raise ConfigError(f'webcam {name}: quality must be "auto", "max" or a height (720, 1080, 2160…)')
        refresh = (cam.get("source") or {}).get("refresh_sec")
        if refresh not in (None, ""):
            try:
                ok = 20 <= float(refresh) <= 3600
            except (TypeError, ValueError):
                ok = False
            if not ok:
                raise ConfigError(f"webcam {name}: fetch every must be 20-3600 s")
        _validate_overlay(name, cam.get("overlay"))
        _validate_overlays(name, cam.get("overlays"))
        crops = cam.get("crops") or {}
        if not isinstance(crops, dict):
            raise ConfigError(f"webcam {name}: crops must be an object")
        for shape, crop in crops.items():
            if shape not in ("left", "right", "full", "tall", "wall"):
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
