"""HRDPS model forecasts as numbers (MSC GeoMet WCS), resampled onto a map view.

Unlike radar, model layers are served as real float coverages over WCS, so no
legend decoding is needed. Precipitation forecasts use two HRDPS fields, both
accumulated since the model run started:

    HRDPS.CONTINENTAL_PR  total precipitation [mm]
    HRDPS.CONTINENTAL_SN  snow, as liquid equivalent [mm]  (≈ cm of snow at 10:1)

"Accumulated from now" is then PR(t) - PR(first frame). Fields are cached per
model run (cache/forecast/<run>/), so a run is only downloaded once (~2 WCS
requests per forecast hour), well within MSC's usage policy.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import numpy as np

from hub import paths
from hub.weather import wms

CACHE = paths.CACHE_DIR / "forecast"
PR, SN = "HRDPS.CONTINENTAL_PR", "HRDPS.CONTINENTAL_SN"


def run_times(layer=PR):
    """Forecast valid times of the latest model run (hourly)."""
    return wms.layer_times(layer, max_age=600)


def _bbox(view, pad=0.15):
    lo0, la0, lo1, la1 = view.lonlat
    return min(la0, la1) - pad, max(la0, la1) + pad, min(lo0, lo1) - pad, max(lo0, lo1) + pad


def _fetch(layer, when, view):
    """(grid, (lat0, lat1, lon0, lon1)) float32 field from WCS, regular lat/lon grid (north-up)."""
    import tifffile
    s, n, w, e = _bbox(view)
    params = {"service": "WCS", "version": "2.0.1", "request": "GetCoverage", "coverageid": layer,
              "subsettingcrs": "EPSG:4326", "outputcrs": "EPSG:4326",
              "subset": [f"lat({s:.4f},{n:.4f})", f"long({w:.4f},{e:.4f})", f'time("{when:%Y-%m-%dT%H:%M:%SZ}")'],
              "format": "image/tiff"}
    r = wms._s.get(wms.WMS, params=params, timeout=60)
    if r.content[:2] not in (b"II", b"MM"):
        raise RuntimeError(f"WCS {layer} {when:%H:%M}: {r.text[:200]}")
    with tifffile.TiffFile(io.BytesIO(r.content)) as tf:
        page = tf.pages[0]
        a = page.asarray().astype(np.float32)
        tags = page.tags
        scale = tags.get("ModelPixelScaleTag")
        tie = tags.get("ModelTiepointTag")
        if scale is not None and tie is not None:
            sx, sy = scale.value[0], scale.value[1]
            x0, y0 = tie.value[3], tie.value[4]
            lon0, lat1 = x0, y0
            lon1, lat0 = x0 + sx * a.shape[1], y0 - sy * a.shape[0]
        else:
            lat0, lat1, lon0, lon1 = s, n, w, e
    a = np.where(np.isfinite(a) & (a > -1e6), a, 0.0)
    return a, (lat0, lat1, lon0, lon1)


def _sample(grid, extent, view):
    """Bilinear sample of a north-up lat/lon grid at every pixel centre of the view."""
    lat0, lat1, lon0, lon1 = extent
    lon, lat = view.lonlat_grid()
    gh, gw = grid.shape
    fx = (lon - lon0) / (lon1 - lon0) * gw - 0.5
    fy = (lat1 - lat) / (lat1 - lat0) * gh - 0.5
    x0 = np.clip(np.floor(fx).astype(int), 0, gw - 1)
    y0 = np.clip(np.floor(fy).astype(int), 0, gh - 1)
    x1, y1 = np.clip(x0 + 1, 0, gw - 1), np.clip(y0 + 1, 0, gh - 1)
    ax, ay = np.clip(fx - x0, 0, 1), np.clip(fy - y0, 0, 1)
    top = grid[y0, x0] * (1 - ax) + grid[y0, x1] * ax
    bot = grid[y1, x0] * (1 - ax) + grid[y1, x1] * ax
    return (top * (1 - ay) + bot * ay).astype(np.float32)


def field(layer, when, view, run_id):
    """Field for one valid time on the view's pixel grid, cached per model run."""
    d = CACHE / run_id / view.key
    p = d / f"{layer}_{when:%Y%m%dT%H%M}.npy"
    if p.exists():
        return np.load(p)
    grid, ext = _fetch(layer, when, view)
    out = _sample(grid, ext, view)
    d.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npy")
    np.save(tmp, out.astype(np.float16))
    tmp.replace(p)
    return out.astype(np.float16)


def precip_forecast(view, hours, step_h=1, log=print):
    """Yields (t, rain_mm, snow_cm) accumulated from the first frame (≈ now) to each forecast hour."""
    times = run_times(PR)
    if not times:
        return
    run_id = times[0].strftime("%Y%m%dT%H")              # first valid hour identifies the run
    now = datetime.now(timezone.utc)
    start = next((t for t in times if t >= now - timedelta(minutes=30)), None)
    if start is None:
        return
    sel = [t for t in times if start <= t <= start + timedelta(hours=hours)]
    sel = sel[::max(1, int(step_h))] + ([sel[-1]] if sel and (len(sel) - 1) % max(1, int(step_h)) else [])
    base_pr = field(PR, start, view, run_id).astype(np.float32)
    base_sn = field(SN, start, view, run_id).astype(np.float32)
    for t in sel:
        pr = field(PR, t, view, run_id).astype(np.float32) - base_pr
        sn = field(SN, t, view, run_id).astype(np.float32) - base_sn
        pr, sn = np.maximum(pr, 0), np.maximum(sn, 0)
        yield t, np.maximum(pr - sn, 0), sn                 # snow liquid mm ≈ snow cm (10:1)


def prune(keep_runs=2):
    if not CACHE.exists():
        return
    runs = sorted(p for p in CACHE.iterdir() if p.is_dir())
    import shutil
    for p in runs[:-keep_runs]:
        shutil.rmtree(p, ignore_errors=True)
