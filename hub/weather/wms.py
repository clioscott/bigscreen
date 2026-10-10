"""MSC GeoMet WMS access: time steps, GetMap frames, and decoding radar legends to numbers.

GeoMet doesn't offer numeric radar (WCS is disabled for radar layers), but its
radar styles are smooth colour ramps with published legends. Sampling the
legend's colour bar gives a colour → rate table; each map pixel maps to the
nearest legend colour (tested: worst match 6/441 → unambiguous). Precipitation
type uses a discrete 12-class legend.
"""

from __future__ import annotations

import io
import json
import re
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import requests
from PIL import Image

from hub import paths
from hub.util import atomic_write_json, read_json

WMS = "https://geo.weather.gc.ca/geomet"
UA = "BigScreen/2.0 (personal LED weather display; ~1 request per layer per frame)"
_s = requests.Session()
_s.headers["User-Agent"] = UA                 # MSC policy: meaningful UA, no no-cache headers

RAIN = ("RADAR_1KM_RRAI", "Radar-Rain_14colors",
        [0.1, 1, 2, 4, 8, 12, 16, 24, 32, 50, 64, 100, 125, 200])            # mm/h
SNOW = ("RADAR_1KM_RSNO", "Radar-Snow_14colors",
        [0.1, 0.2, 0.3, 0.5, 0.75, 1, 1.5, 2, 3, 4, 5, 7.5, 10, 20])        # cm/h
PTYPE = ("Radar_1km_SfcPrecipType", "SfcPrecipType_Dis")
# Radar nowcast (extrapolation, +72 min every 6 min): same styles and legends as the observed radar.
NOWCAST_RAIN = ("Radar_1km_RainPrecipRate-Extrapolation", RAIN[1], RAIN[2])
NOWCAST_SNOW = ("Radar_1km_SnowPrecipRate-Extrapolation", SNOW[1], SNOW[2])
LIGHTNING = "Lightning_2.5km_Density"                                   # flashes, 10-min steps, last 3 h
# Legend order top→bottom on GeoMet's SfcPrecipType legend.
PTYPE_CLASSES = ["fzra_heavy", "fzra_light", "hail_rain", "rain_heavy", "rain_moderate", "rain_light",
                 "mixed_heavy", "mixed_moderate", "mixed_light", "snow_heavy", "snow_moderate", "snow_light"]
PT_NONE = 255


def ptype_group(name):
    if name.startswith("snow"):
        return "snow"
    if name.startswith("mixed"):
        return "mixed"
    if name.startswith("fzra"):
        return "freezing"
    return "rain"


# ---------------------------------------------------------------------------
# time dimension
# ---------------------------------------------------------------------------
def _parse_iso(s):
    return datetime.strptime(s.strip().replace(".000", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _parse_duration(s):
    m = re.fullmatch(r"P(?:T(?:(\d+)H)?(?:(\d+)M)?)?", s.strip())
    if not m:
        m2 = re.fullmatch(r"PT?(\d+)D", s.strip())
        return timedelta(days=int(m2.group(1))) if m2 else None
    return timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2) or 0))


def layer_times(layer, max_age=120):
    """All valid times for a layer, from the layer-specific GetCapabilities (cached briefly)."""
    cache = paths.CACHE_DIR / "wms_times.json"
    c = read_json(cache, {}) or {}
    hit = c.get(layer)
    if hit and time.time() - hit["at"] < max_age:
        return [_parse_iso(t) for t in hit["times"]]
    r = _s.get(WMS, params={"service": "WMS", "version": "1.3.0", "request": "GetCapabilities", "layer": layer},
               timeout=30)
    r.raise_for_status()
    m = re.search(r'<Dimension name="time"[^>]*>([^<]*)<', r.text)
    times = []
    if m:
        for chunk in m.group(1).split(","):
            parts = chunk.strip().split("/")
            if len(parts) == 3:
                t0, t1, step = _parse_iso(parts[0]), _parse_iso(parts[1]), _parse_duration(parts[2])
                if step:
                    t = t0
                    while t <= t1 and len(times) < 2000:
                        times.append(t)
                        t += step
            elif parts[0]:
                times.append(_parse_iso(parts[0]))
    c[layer] = {"at": time.time(), "times": [t.strftime("%Y-%m-%dT%H:%M:%SZ") for t in times]}
    atomic_write_json(cache, c)
    return times


# ---------------------------------------------------------------------------
# GetMap
# ---------------------------------------------------------------------------
class FrameGap(Exception):
    """GeoMet advertises a time it can't serve (happens occasionally)."""


def get_map(layer, view, when=None, style="", scale=1, retries=2):
    """RGBA (h, w, 4) uint8 for the view's mercator bbox. scale>1 requests a larger image and downsamples."""
    w, h = view.w * scale, view.h * scale
    params = {"service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": layer, "styles": style,
              "crs": "EPSG:3857", "bbox": ",".join(f"{v:.2f}" for v in view.merc), "width": w, "height": h,
              "format": "image/png", "transparent": "true"}
    if when is not None:
        params["time"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    err = None
    for attempt in range(retries + 1):
        try:
            r = _s.get(WMS, params=params, timeout=40)
            if r.content[:8] == b"\x89PNG\r\n\x1a\n":
                img = Image.open(io.BytesIO(r.content)).convert("RGBA")
                if scale > 1:
                    img = img.resize((view.w, view.h), Image.LANCZOS)
                return np.asarray(img)
            text = r.text[:600]
            if "time" in text.lower() and ("outside" in text.lower() or "dehors" in text.lower()):
                raise FrameGap(text[:200])
            err = f"WMS error: {re.sub(r'<[^>]+>', ' ', text)[:200]}"
        except FrameGap:
            raise
        except Exception as e:
            err = str(e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{layer} {when}: {err}")


# ---------------------------------------------------------------------------
# legend decoding
# ---------------------------------------------------------------------------
def _legend(layer, style):
    cache = paths.CACHE_DIR / f"legend_{layer}_{style}.png"
    if cache.exists() and time.time() - cache.stat().st_mtime < 7 * 86400:
        return Image.open(cache).convert("RGB")
    r = _s.get(WMS, params={"service": "WMS", "version": "1.3.0", "request": "GetLegendGraphic", "layer": layer,
                            "style": style, "format": "image/png", "sld_version": "1.1.0"}, timeout=30)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    img.save(cache)
    return img


class RampDecoder:
    """Colour → value for GeoMet's continuous radar ramps (14colors styles)."""

    def __init__(self, layer, style, stops):
        a = np.asarray(_legend(layer, style)).astype(np.int32)
        h, w, _ = a.shape
        colourful = np.ptp(a, axis=2) > 25                       # bar pixels vs white/black/grey frame
        col = int(np.argmax(colourful[:, : w // 3].sum(axis=0)))
        rows = np.flatnonzero(colourful[:, col])
        top, bot = rows.min(), rows.max()
        # the bar ends in an "over" triangle; ticks span the rectangular part only
        width_at = np.array([colourful[y, : w // 3].sum() for y in range(top, bot + 1)])
        full = width_at.max()
        rect_top = top + int(np.flatnonzero(width_at >= full - 1)[0])
        ys = np.arange(top, bot + 1)
        frac = (bot - ys) / max(1, bot - rect_top)
        tick = np.linspace(0, 1, len(stops))
        self.values = np.where(frac <= 1.0, np.interp(frac, tick, stops), stops[-1] * 1.25).astype(np.float32)
        self.colours = a[ys, col].astype(np.int32)

    def decode(self, rgba):
        """(h, w) float32 rate; 0 where transparent."""
        h, w, _ = rgba.shape
        out = np.zeros((h, w), dtype=np.float32)
        on = rgba[..., 3] > 0
        if not on.any():
            return out
        px = rgba[on][:, :3].astype(np.int32)
        uniq, inv = np.unique(px, axis=0, return_inverse=True)
        d = ((uniq[:, None, :] - self.colours[None, :, :]) ** 2).sum(-1)
        out[on] = self.values[d.argmin(1)][inv.ravel()]
        return out


class ClassDecoder:
    """Colour → class index for GeoMet's discrete precipitation-type legend."""

    def __init__(self, layer, style, n):
        a = np.asarray(_legend(layer, style)).astype(np.int32)
        h, w, _ = a.shape
        colourful = (np.ptp(a, axis=2) > 20) | ((a.sum(2) < 600) & (a.sum(2) > 60))
        col = int(np.argmax(colourful[:, : w // 3].sum(axis=0)))
        ys = np.flatnonzero(colourful[:, col])
        # n equal-height swatches between the first and last coloured row
        top, bot = ys.min(), ys.max()
        centres = top + (np.arange(n) + 0.5) * (bot - top + 1) / n
        self.colours = np.array([a[int(c), col] for c in centres], dtype=np.int32)

    def decode(self, rgba):
        h, w, _ = rgba.shape
        out = np.full((h, w), PT_NONE, dtype=np.uint8)
        on = rgba[..., 3] > 0
        if on.any():
            px = rgba[on][:, :3].astype(np.int32)
            uniq, inv = np.unique(px, axis=0, return_inverse=True)
            d = ((uniq[:, None, :] - self.colours[None, :, :]) ** 2).sum(-1)
            out[on] = d.argmin(1).astype(np.uint8)[inv.ravel()]
        return out


_decoders = {}


def decoder(kind):
    if kind not in _decoders:
        _decoders[kind] = {"rain": lambda: RampDecoder(*RAIN), "snow": lambda: RampDecoder(*SNOW),
                           "nc_rain": lambda: RampDecoder(*NOWCAST_RAIN), "nc_snow": lambda: RampDecoder(*NOWCAST_SNOW),
                           "ptype": lambda: ClassDecoder(*PTYPE, len(PTYPE_CLASSES))}[kind]()
    return _decoders[kind]


def point_value(layer, lon, lat, when=None):
    """GetFeatureInfo numeric value at one point (used to validate the decoders)."""
    params = {"service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo", "layers": layer,
              "query_layers": layer, "crs": "EPSG:4326", "bbox": f"{lat - 0.01},{lon - 0.01},{lat + 0.01},{lon + 0.01}",
              "width": 3, "height": 3, "i": 1, "j": 1, "info_format": "application/json"}
    if when is not None:
        params["time"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _s.get(WMS, params=params, timeout=20)
    feats = r.json().get("features") or []
    return feats[0]["properties"] if feats else None
