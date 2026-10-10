"""Radar archive, rate colouring, and rolling precipitation accumulation.

GeoMet only keeps 3 h of radar, so the hub archives every 6-minute frame
itself (decoded to numbers) for as long as any accumulation window needs —
up to 48 h. The archive is kept per map area at full panel width; half-width
views are the centre crop of the same grid.

Each archived frame stores:  rain mm/h (float16), snow cm/h (float16),
precipitation-type class (uint8, 255 = none).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import numpy as np

from hub import paths
from hub.weather import wms
from hub.weather.geo import View, _hex

ARCHIVE = paths.CACHE_DIR / "radar"
STEP = timedelta(minutes=6)

# Default palettes: (threshold, colour) — a pixel gets the colour of the highest threshold it reaches.
PALETTES = {
    # live rate (TV style)
    "rain_rate":  [(0.1, "#7ee87e"), (0.5, "#2ecc40"), (2, "#118a1e"), (5, "#f5e000"), (10, "#ff9a00"),
                   (25, "#ff2a00"), (50, "#c8003c"), (100, "#ff00ff")],                                   # mm/h
    "snow_rate":  [(0.05, "#e6f2ff"), (0.2, "#b4d7ff"), (0.5, "#78b0ff"), (1, "#3d7cf0"), (2, "#1f45c8"),
                   (4, "#7a3cd6")],                                                                       # cm/h
    "mixed_rate": [(0.1, "#ffb3e6"), (1, "#ff66cc"), (5, "#d12fa0")],
    "fzra_rate":  [(0.1, "#ff9090"), (1, "#ff3b3b"), (5, "#b30000")],
    # accumulated totals
    "rain_total": [(0.5, "#b6f0b6"), (2, "#5fd35f"), (5, "#1f9e2f"), (10, "#0e6b1c"), (15, "#e8e83a"),
                   (25, "#ffb000"), (40, "#ff5a00"), (60, "#e00000"), (90, "#b0006e"), (130, "#ff66ff")],  # mm
    "snow_total": [(0.2, "#ffffff"), (1, "#d6ecff"), (3, "#9cccff"), (6, "#5a9cff"), (10, "#2a62e6"),
                   (20, "#1430a8"), (35, "#5a2db4")],                                                     # cm
}


def palette(cfg_palettes, name):
    p = (cfg_palettes or {}).get(name) or PALETTES[name]
    return [(float(t), c) for t, c in p]


def colorize(values, pal, opacity=1.0, fade_first=0.55):
    """values (h, w) → RGBA uint8 using banded thresholds. The lowest band is drawn at `fade_first`
    opacity so drizzle/flurries don't hide the map."""
    h, w = values.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    for i, (t, c) in enumerate(pal):
        m = values >= t
        out[m, :3] = _hex(c)
        out[m, 3] = int(255 * opacity * (fade_first if i == 0 else 1.0))
    return out


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------
def archive_view(area, full_w, h):
    return View(area, full_w, h)


def _dir(view):
    d = ARCHIVE / view.key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fname(t):
    return t.strftime("%Y%m%dT%H%M") + ".npz"


def _parse_fname(name):
    return datetime.strptime(name[:13], "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)


def update_archive(view, keep_hours, log=print):
    """Fetch any frames GeoMet has that we don't; prune frames older than keep_hours. Returns (added, errors)."""
    d = _dir(view)
    have = {p.name for p in d.glob("*.npz")}
    times = [t for t in wms.layer_times(wms.RAIN[0]) if t >= datetime.now(timezone.utc) - timedelta(hours=keep_hours)]
    added, errors = 0, []
    for t in times:
        if _fname(t) in have:
            continue
        try:
            rain = wms.decoder("rain").decode(wms.get_map(wms.RAIN[0], view, t, style=wms.RAIN[1]))
        except wms.FrameGap:
            continue
        except Exception as e:
            errors.append(str(e)[:160])
            continue
        try:
            snow = wms.decoder("snow").decode(wms.get_map(wms.SNOW[0], view, t, style=wms.SNOW[1]))
        except Exception:
            snow = np.zeros_like(rain)
        try:
            ptype = wms.decoder("ptype").decode(wms.get_map(wms.PTYPE[0], view, t, style=wms.PTYPE[1]))
        except Exception:
            ptype = np.full(rain.shape, wms.PT_NONE, dtype=np.uint8)
        tmp = d / (_fname(t) + ".tmp.npz")
        np.savez_compressed(tmp, rain=rain.astype(np.float16), snow=snow.astype(np.float16), ptype=ptype)
        tmp.replace(d / _fname(t))
        added += 1
    cutoff = datetime.now(timezone.utc) - timedelta(hours=keep_hours + 0.5)
    for p in d.glob("*.npz"):
        try:
            if _parse_fname(p.name) < cutoff:
                p.unlink()
        except ValueError:
            p.unlink(missing_ok=True)
    return added, errors


def nowcast(view, after, crop=None, log=print):
    """ECCC radar extrapolation frames valid after ``after``: [(t, rain, snow, ptype)].

    Cached per nowcast issue (cache/radar/<view>/nowcast/<issue>/), so each issue is fetched once.
    The nowcast has no precipitation type, so snow is typed as snow where it's the only signal."""
    times = wms.layer_times(wms.NOWCAST_RAIN[0])
    if not times:
        return []
    issue = times[0].strftime("%Y%m%dT%H%M")
    base = _dir(view) / "nowcast"
    d = base / issue
    d.mkdir(parents=True, exist_ok=True)
    for old in base.iterdir():
        if old.is_dir() and old.name != issue and old.name < issue:
            for p in old.glob("*"):
                p.unlink(missing_ok=True)
            old.rmdir()
    snow_cls = wms.PTYPE_CLASSES.index("snow_moderate")
    out = []
    for t in times:
        if t <= after:
            continue
        p = d / _fname(t)
        if p.exists():
            z = np.load(p)
            rain, snow = z["rain"].astype(np.float32), z["snow"].astype(np.float32)
        else:
            try:
                rain = wms.decoder("nc_rain").decode(wms.get_map(wms.NOWCAST_RAIN[0], view, t, style=wms.NOWCAST_RAIN[1]))
                try:
                    snow = wms.decoder("nc_snow").decode(wms.get_map(wms.NOWCAST_SNOW[0], view, t, style=wms.NOWCAST_SNOW[1]))
                except Exception:
                    snow = np.zeros_like(rain)
            except wms.FrameGap:
                continue
            except Exception as e:
                log(f"  nowcast {t:%H:%M}: {e}")
                continue
            tmp = d / (_fname(t) + ".tmp.npz")
            np.savez_compressed(tmp, rain=rain.astype(np.float16), snow=snow.astype(np.float16))
            tmp.replace(p)
        ptype = np.where((snow > 0) & (rain <= 0), snow_cls, wms.PT_NONE).astype(np.uint8)
        if crop:
            x0, x1 = crop
            rain, snow, ptype = rain[:, x0:x1], snow[:, x0:x1], ptype[:, x0:x1]
        out.append((t, rain, snow, ptype))
    return out


def lightning_layer(rgba, colour="#ffee33"):
    """Any lightning pixel → one bright, opaque colour (the WMS style's pastel classes vanish on LEDs)."""
    out = np.zeros(rgba.shape[:2] + (4,), dtype=np.uint8)
    m = rgba[..., 3] > 40
    out[m, :3] = _hex(colour)
    out[m, 3] = 255
    return out


def archive_times(view):
    return sorted(_parse_fname(p.name) for p in _dir(view).glob("*.npz") if not p.name.endswith(".tmp.npz"))


def load_frame(view, t, crop=None):
    z = np.load(_dir(view) / _fname(t))
    rain, snow, ptype = z["rain"].astype(np.float32), z["snow"].astype(np.float32), z["ptype"]
    if crop:
        x0, x1 = crop
        rain, snow, ptype = rain[:, x0:x1], snow[:, x0:x1], ptype[:, x0:x1]
    return rain, snow, ptype


GROUP = np.array([{"rain": 0, "freezing": 1, "mixed": 2, "snow": 3}[wms.ptype_group(n)]
                  for n in wms.PTYPE_CLASSES] + [0] * (256 - len(wms.PTYPE_CLASSES)), dtype=np.uint8)
GROUP[wms.PT_NONE] = 0          # no type data → treat as rain


def rate_layer(rain, snow, ptype, cfg_palettes=None):
    """TV-style live precipitation: colour by type, intensity by rate. Always opaque, full colour,
    so it contrasts with the basemap underneath."""
    g = GROUP[ptype]
    out = np.zeros(rain.shape + (4,), dtype=np.uint8)
    for code, values, pal in ((0, rain, "rain_rate"), (1, rain, "fzra_rate"), (2, rain, "mixed_rate"),
                              (3, snow, "snow_rate")):
        layer = colorize(np.where(g == code, values, 0), palette(cfg_palettes, pal), 1.0, fade_first=1.0)
        m = layer[..., 3] > 0
        out[m] = layer[m]
    return out


def accumulate(view, times, crop=None, max_gap_min=15):
    """Running totals over `times` (sorted). Yields (t, rain_mm, snow_cm) after each frame.

    Each frame's rate counts for the interval since the previous frame (capped at
    max_gap_min so a GeoMet outage doesn't smear one frame over hours).
    Mixed precipitation adds half to each total; freezing rain/hail count as rain.
    """
    rain_tot = snow_tot = None
    prev = None
    for t in times:
        rain, snow, ptype = load_frame(view, t, crop)
        if rain_tot is None:
            rain_tot = np.zeros_like(rain)
            snow_tot = np.zeros_like(rain)
        dt_h = (min((t - prev).total_seconds() / 60, max_gap_min) if prev else 6) / 60.0
        prev = t
        g = GROUP[ptype]
        rain_tot += np.where(g <= 1, rain, np.where(g == 2, 0.5 * rain, 0)) * dt_h
        snow_tot += np.where(g == 3, snow, np.where(g == 2, 0.5 * snow, 0)) * dt_h
        yield t, rain_tot, snow_tot


def accumulation_layer(rain_mm, snow_cm, cfg_palettes=None, snow_on_top_cm=0.5):
    """Rain colours, with snow colours drawn on top wherever the snow total passes the threshold.
    Opaque and full colour (never see-through)."""
    rain_rgba = colorize(rain_mm, palette(cfg_palettes, "rain_total"), 1.0, fade_first=1.0)
    snow_pal = palette(cfg_palettes, "snow_total")
    snow_rgba = colorize(np.where(snow_cm >= snow_on_top_cm, snow_cm, 0), snow_pal, 1.0, fade_first=1.0)
    m = snow_rgba[..., 3] > 0
    rain_rgba[m] = snow_rgba[m]
    return rain_rgba
