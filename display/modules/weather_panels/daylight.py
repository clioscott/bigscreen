"""Daylight bar utility.

Renders a horizontal strip whose width represents the 1440 minutes of a
day (left = 00:00, right = 23:59). Each pixel column's color is the
expected sky color at that time, computed from solar elevation at the
Richmond, BC reference coordinates and modulated by the hourly forecast
(cloud cover, precipitation type).

Below the bar (or above, optionally), a precipitation underline shows
when rain or snow is expected:

  * Rain: blue dotted underline, density proportional to POP %
  * Snow: white checker underline, density proportional to POP %

Only stdlib + the rgbmatrix.graphics primitives are required at runtime.

Public API
----------
    sky_color(minute_of_day, sunrise_min, sunset_min, weather_state)
        -> graphics.Color

    draw_daylight_bar(canvas, x, y, w, h, hourly, sunrise_hm, sunset_hm,
                      palette, *, show_now_marker=True, now_minute=None,
                      precip_underline=True)
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Iterable, Tuple

from display.gfx import graphics

from .common import (
    RICHMOND_LAT, RICHMOND_LON,
    _C, color_to_rgb, lerp_color, hm_to_minutes,
    hline, vline, vline_dotted,
)


# ---------------------------------------------------------------------------
# Solar geometry
# ---------------------------------------------------------------------------
# Simple, well-documented solar elevation formula (NOAA solar calculator
# simplified). Inputs: latitude, longitude (deg), date, time-of-day in
# minutes (local apparent). Output: solar elevation in degrees.

def _julian_day(dt: datetime) -> float:
    """Convert a UTC datetime to Julian Day. dt must be naive UTC."""
    y = dt.year
    m = dt.month
    d = dt.day + (dt.hour + (dt.minute + dt.second / 60.0) / 60.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + b - 1524.5


def solar_elevation(lat_deg: float, lon_deg: float, dt_utc: datetime) -> float:
    """Return solar elevation in degrees for given UTC datetime."""
    jd = _julian_day(dt_utc)
    n = jd - 2451545.0
    # Mean longitude
    L = (280.460 + 0.9856474 * n) % 360.0
    # Mean anomaly
    g = math.radians((357.528 + 0.9856003 * n) % 360.0)
    # Ecliptic longitude
    lam = math.radians(L + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g))
    # Obliquity of the ecliptic
    eps = math.radians(23.439 - 0.0000004 * n)
    # Right ascension and declination
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    # Greenwich mean sidereal time (hours)
    gmst = (18.697374558 + 24.06570982441908 * n) % 24.0
    # Local sidereal time
    lst = (gmst * 15.0 + lon_deg) % 360.0
    # Hour angle
    ha = math.radians(lst - math.degrees(ra))
    # Elevation
    lat = math.radians(lat_deg)
    sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
    sin_alt = max(-1.0, min(1.0, sin_alt))
    return math.degrees(math.asin(sin_alt))


# ---------------------------------------------------------------------------
# Sky color from solar elevation
# ---------------------------------------------------------------------------
# We map solar elevation to a 9-stop ramp, then modulate by weather. The
# color stops below were tuned by eye on an LED matrix to read distinctly
# even at 138-pixel zone height.

# (elevation deg, R, G, B). Linear interpolation between stops.
_SKY_STOPS = [
    # Deep night
    (-90.0,   3,   5,  18),
    (-18.0,   8,  12,  28),     # Astronomical twilight begins
    (-12.0,  18,  30,  60),     # Nautical twilight
    ( -6.0,  60,  60, 110),     # Civil twilight starts
    ( -2.0, 200, 110,  80),     # Sunrise-magenta band
    (  0.0, 255, 160,  90),     # Horizon: warm orange
    (  3.0, 255, 200, 130),     # Just after sunrise: gold
    ( 12.0, 170, 215, 250),     # Morning blue
    ( 30.0, 110, 185, 240),     # Bright daytime blue
    ( 60.0,  80, 170, 240),     # Midday peak
]


def _interp_stops(stops: list[tuple], x: float) -> Tuple[int, int, int]:
    """Linear interpolate (x_key, r, g, b) stops."""
    if x <= stops[0][0]:
        _, r, g, b = stops[0]
        return r, g, b
    if x >= stops[-1][0]:
        _, r, g, b = stops[-1]
        return r, g, b
    for i in range(len(stops) - 1):
        x0 = stops[i][0]
        x1 = stops[i + 1][0]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            r = stops[i][1] + (stops[i + 1][1] - stops[i][1]) * t
            g = stops[i][2] + (stops[i + 1][2] - stops[i][2]) * t
            b = stops[i][3] + (stops[i + 1][3] - stops[i][3]) * t
            return int(round(r)), int(round(g)), int(round(b))
    _, r, g, b = stops[-1]
    return r, g, b


def base_sky_rgb(elevation_deg: float) -> Tuple[int, int, int]:
    """Sky RGB from solar elevation only (clear-sky model)."""
    return _interp_stops(_SKY_STOPS, elevation_deg)


# ---------------------------------------------------------------------------
# Weather modulation
# ---------------------------------------------------------------------------
# weather_state is a dict with keys ``cloud_pct`` (0-100), ``precip_kind``
# (None | 'rain' | 'snow' | 'mix'), ``precip_intensity`` (0-1).
# Modulation rules:
#   * cloud_pct 0-20: no change
#   * cloud_pct 20-60: desaturate toward warm grey + slight darken
#   * cloud_pct 60-100: desaturate toward cool grey + heavier darken
#   * precip_kind 'rain': further darken + add subtle blue cast
#   * precip_kind 'snow': lighten slightly + add white speckle (drawn
#     separately by the bar renderer)

def _to_grey(rgb: Tuple[int, int, int], amount: float) -> Tuple[int, int, int]:
    r, g, b = rgb
    grey = int(0.30 * r + 0.59 * g + 0.11 * b)
    return (
        int(round(r + (grey - r) * amount)),
        int(round(g + (grey - g) * amount)),
        int(round(b + (grey - b) * amount)),
    )


def _scale_rgb(rgb: Tuple[int, int, int], factor: float) -> Tuple[int, int, int]:
    r, g, b = rgb
    return (
        max(0, min(255, int(round(r * factor)))),
        max(0, min(255, int(round(g * factor)))),
        max(0, min(255, int(round(b * factor)))),
    )


def modulated_sky_rgb(elevation_deg: float,
                      cloud_pct: float = 0.0,
                      precip_kind: str | None = None,
                      precip_intensity: float = 0.0) -> Tuple[int, int, int]:
    """Sky color modulated by weather conditions."""
    base = base_sky_rgb(elevation_deg)
    # Cloud desaturation
    cloud_pct = max(0.0, min(100.0, cloud_pct))
    if cloud_pct > 20:
        # cloud amount mapped to 0..1 desat factor over 20-100%
        amt = (cloud_pct - 20.0) / 80.0   # 0..1
        # heavy overcast biases toward cool grey (slightly bluish), partly cloudy stays warm
        if cloud_pct > 60:
            base = _to_grey(base, 0.4 + 0.4 * amt)
            # slight darken
            base = _scale_rgb(base, 0.85 - 0.15 * amt)
        else:
            base = _to_grey(base, 0.25 * amt)
            base = _scale_rgb(base, 0.95 - 0.10 * amt)
    if precip_kind in ("rain", "mix"):
        # darken + cool the color
        f = 0.55 + 0.25 * (1.0 - precip_intensity)
        r, g, b = _scale_rgb(base, f)
        # blue tint
        b = min(255, int(b + 30 * precip_intensity))
        base = (r, g, b)
    elif precip_kind == "snow":
        # lighten toward white slightly; speckle handled outside
        r, g, b = base
        f = 0.55 + 0.20 * (1.0 - precip_intensity)
        r, g, b = _scale_rgb((r, g, b), f)
        # cool cast
        r = max(0, r - int(20 * precip_intensity))
        b = min(255, b + int(15 * precip_intensity))
        base = (r, g, b)
    return base


def sky_color_at_minute(minute_of_day: int,
                        ref_date: datetime,
                        cloud_pct: float = 0.0,
                        precip_kind: str | None = None,
                        precip_intensity: float = 0.0,
                        lat: float = RICHMOND_LAT,
                        lon: float = RICHMOND_LON):
    """Returns a graphics.Color for the given minute-of-day on the given
    UTC reference date, modulated by the weather state."""
    minute_of_day = max(0, min(1439, int(minute_of_day)))
    # Compose the UTC datetime for ``minute_of_day`` LOCAL TIME.
    # Richmond is UTC-7 (PDT) or UTC-8 (PST). Approximate: use the
    # ref_date's UTC offset is unknown so we treat the minute as
    # local-time and convert to UTC by hand using a fixed offset that
    # the daylight savings-aware caller is expected to provide. Default
    # to PDT (UTC-7) since most of the year and Richmond's climate
    # observation timestamps in the inventory used this offset.
    # The caller can pre-shift `ref_date` to the right UTC instant if
    # needed for fine-grained accuracy.
    h = minute_of_day // 60
    m = minute_of_day % 60
    # Local minute -> UTC minute:
    # We approximate with a fixed -7h offset (PDT). For the daylight
    # bar, an hour-level error in twilight transition is invisible to
    # the eye on a 200-pixel-wide bar.
    utc_minutes = h * 60 + m + 7 * 60
    days = utc_minutes // (24 * 60)
    utc_minutes = utc_minutes % (24 * 60)
    dt = ref_date.replace(hour=utc_minutes // 60,
                          minute=utc_minutes % 60,
                          second=0, microsecond=0)
    # If we crossed midnight UTC, increment day
    if days:
        from datetime import timedelta
        dt = dt + timedelta(days=int(days))
    elev = solar_elevation(lat, lon, dt)
    r, g, b = modulated_sky_rgb(elev, cloud_pct, precip_kind, precip_intensity)
    return _C(r, g, b)


# ---------------------------------------------------------------------------
# Bar rendering
# ---------------------------------------------------------------------------

def _hourly_state(hourly: list[dict], h: int) -> dict:
    """Find the hourly entry matching hour-of-day ``h``. Returns a
    weather state dict for use by ``modulated_sky_rgb``."""
    found = None
    for entry in hourly:
        if entry.get("ts_h") == h:
            found = entry
            break
    if found is None:
        return {"cloud_pct": 0, "precip_kind": None, "precip_intensity": 0.0}
    pop = float(found.get("pop_pct", 0))
    icon = int(found.get("icon_code", 0))
    # Map EC icon code to (cloud_pct, precip_kind, intensity)
    cloud_pct = 0
    precip = None
    intensity = 0.0
    # Sunny
    if icon in (0, 30):
        cloud_pct = 5
    elif icon in (1, 31):
        cloud_pct = 25
    elif icon in (2, 22, 32):
        cloud_pct = 50
    elif icon in (3, 33):
        cloud_pct = 70
    elif icon in (4,):     # broken cloud / mostly cloudy
        cloud_pct = 75
    elif icon in (10,):
        cloud_pct = 100
    # Rain family
    if icon in (6, 12, 13, 36):
        cloud_pct = max(cloud_pct, 80)
        precip = "rain"
        intensity = max(intensity, 0.6 if icon == 13 else 0.4)
    if icon in (9, 19, 39, 46):
        cloud_pct = 95
        precip = "rain"
        intensity = 0.8
    # Drizzle
    if icon in (28,):
        cloud_pct = max(cloud_pct, 80)
        precip = "rain"
        intensity = 0.3
    # Mixed precip
    if icon in (7, 14, 15, 27, 37):
        cloud_pct = max(cloud_pct, 85)
        precip = "mix"
        intensity = 0.5
    # Snow family
    if icon in (8, 16, 17, 18, 25, 38, 40):
        cloud_pct = 95
        precip = "snow"
        intensity = 0.6 if icon in (16, 17, 38) else 0.8
    # Fog/haze
    if icon in (23, 24):
        cloud_pct = 80
    # POP overrides intensity if no precip type was detected from icon
    if precip is None and pop >= 50:
        precip = "rain"
        intensity = pop / 100.0
    if precip and pop:
        intensity = min(1.0, intensity * (0.4 + 0.6 * pop / 100.0))
    return {
        "cloud_pct": cloud_pct,
        "precip_kind": precip,
        "precip_intensity": intensity,
        "pop": pop,
    }


def draw_daylight_bar(canvas, x: int, y: int, w: int, h: int,
                      *,
                      hourly: list[dict],
                      sunrise_hm: str,
                      sunset_hm: str,
                      palette: dict,
                      show_now_marker: bool = True,
                      now_minute: int | None = None,
                      precip_underline: bool = True,
                      ref_date: datetime | None = None,
                      lat: float = RICHMOND_LAT,
                      lon: float = RICHMOND_LON) -> None:
    """Draw a daylight gradient bar at (x, y) of width ``w`` and height ``h``.

    Each pixel column maps to an exact minute-of-day; sky color comes from
    ``modulated_sky_rgb`` using the matching hourly forecast state. A
    1-pixel "now" tick marks the current local time. If ``precip_underline``
    is True, a 1-2 pixel underline below the bar shows rain/snow per hour.
    """
    if w <= 0 or h <= 0:
        return

    if ref_date is None:
        ref_date = datetime.utcnow().replace(microsecond=0)

    # Pre-compute weather state per hour-of-day so we can interpolate.
    # Banding came from each hour-bin sharing one cloud/precip state; we
    # interpolate between adjacent hours so every minute (and therefore
    # every pixel column) gets a unique blended state.
    hour_state = {hh: _hourly_state(hourly, hh) for hh in range(24)}

    minutes_per_px = 1440.0 / float(w)

    # Per-pixel column rendering with continuous weather interpolation.
    for col in range(w):
        m = (col + 0.5) * minutes_per_px        # float minute-of-day (sub-minute precision)
        m_int = int(m)
        m_int = max(0, min(1439, m_int))

        # ----- Smooth interpolated weather state -----
        # The hourly forecast entry timestamped HH represents the
        # conditions AT that hour. We treat the value as anchored at
        # minute HH:30 (mid-hour) so transitions span :30 of one hour
        # to :30 of the next, giving the smoothest visual gradient.
        anchor_hh = int(m_int // 60)
        if (m_int % 60) < 30:
            h_low = (anchor_hh - 1) % 24
            h_high = anchor_hh
            f = ((m_int % 60) + 30) / 60.0
        else:
            h_low = anchor_hh
            h_high = (anchor_hh + 1) % 24
            f = ((m_int % 60) - 30) / 60.0
        f = max(0.0, min(1.0, f))

        s_low = hour_state.get(h_low, {"cloud_pct": 0, "precip_kind": None, "precip_intensity": 0.0})
        s_high = hour_state.get(h_high, {"cloud_pct": 0, "precip_kind": None, "precip_intensity": 0.0})

        cloud_pct = s_low["cloud_pct"] * (1.0 - f) + s_high["cloud_pct"] * f
        # Precip intensity blends; precip_kind picks the dominant side.
        # If kinds differ, we crossfade so the colour shift is smooth
        # even though the categorical kind is discrete (the modulator
        # gives similar dark/cool colours for rain vs mix anyway).
        i_low = s_low["precip_intensity"] * (1.0 - f)
        i_high = s_high["precip_intensity"] * f
        if i_low + i_high < 0.01:
            precip_kind = None
            precip_intensity = 0.0
        elif i_low >= i_high:
            precip_kind = s_low["precip_kind"] or s_high["precip_kind"]
            precip_intensity = min(1.0, i_low + i_high)
        else:
            precip_kind = s_high["precip_kind"] or s_low["precip_kind"]
            precip_intensity = min(1.0, i_low + i_high)

        # ----- Per-pixel solar elevation -----
        # Build a UTC datetime for the sub-minute represented by this
        # column. Sub-minute precision gives smoother twilight bands.
        utc_min_f = m + 7 * 60.0   # local PDT -> UTC (fractional minutes)
        utc_total_secs = int(round(utc_min_f * 60.0))
        days_ahead, sec_in_day = divmod(utc_total_secs, 24 * 3600)
        h_utc, rem = divmod(sec_in_day, 3600)
        m_utc, s_utc = divmod(rem, 60)
        dt = ref_date.replace(hour=h_utc, minute=m_utc, second=s_utc, microsecond=0)
        if days_ahead:
            from datetime import timedelta
            dt = dt + timedelta(days=int(days_ahead))
        elev = solar_elevation(lat, lon, dt)

        r, g, b = modulated_sky_rgb(elev,
                                    cloud_pct=cloud_pct,
                                    precip_kind=precip_kind,
                                    precip_intensity=precip_intensity)

        # ----- Ordered dither to break up any residual color banding -----
        # The 9-stop sky ramp can leave faint horizontal bands at stop
        # crossings. We add ±1 to each channel based on a stable 4x4
        # Bayer threshold so the bar shows micro-noise instead of
        # discrete bands -- imperceptible per pixel, smooth in aggregate.
        bayer = ((col & 1) ^ ((col >> 1) & 1))   # cheap 1-bit dither
        if bayer:
            r = min(255, r + 1)
            g = min(255, g + 1)
            b = min(255, b + 1)

        for yy in range(h):
            canvas.SetPixel(x + col, y + yy, r, g, b)

        # Snow speckle: scatter white pixels in 20-30% of column rows
        st = {"precip_kind": precip_kind, "precip_intensity": precip_intensity}
        if st["precip_kind"] == "snow" and h >= 4:
            density = 0.20 + 0.10 * st["precip_intensity"]
            # Deterministic per-column seed
            seed = (col * 2654435761) & 0xFFFFFFFF
            for yy in range(h):
                # LCG step
                seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
                u = (seed >> 8) & 0xFFFFFF
                if (u / 0xFFFFFF) < density:
                    canvas.SetPixel(x + col, y + yy, 240, 248, 255)

    # Sunrise / sunset markers (thin vertical line in palette frame)
    sr_min = hm_to_minutes(sunrise_hm)
    ss_min = hm_to_minutes(sunset_hm)
    if 0 <= sr_min <= 1439:
        col = int(round(sr_min / minutes_per_px))
        if 0 <= col < w:
            for yy in range(h):
                canvas.SetPixel(x + col, y + yy, 255, 220, 120)
    if 0 <= ss_min <= 1439:
        col = int(round(ss_min / minutes_per_px))
        if 0 <= col < w:
            for yy in range(h):
                canvas.SetPixel(x + col, y + yy, 255, 130, 90)

    # NOW marker - bright red vertical line through the bar with notches
    # extending 1px above and 1px below. Position scrolls left to right
    # over the day as time advances.
    if show_now_marker:
        if now_minute is None:
            try:
                lt = time.localtime()
                now_minute = lt.tm_hour * 60 + lt.tm_min
            except Exception:
                now_minute = 720
        col = int(round(now_minute / minutes_per_px))
        col = max(0, min(w - 1, col))
        # Vertical red hairline through the full height of the bar.
        for yy in range(h):
            canvas.SetPixel(x + col, y + yy, 255, 40, 40)
        # 1-pixel notches above and below to make the marker pop. We do
        # NOT call canvas.height() to bounds-check — designs draw inside
        # their own zone and the matrix library tolerates out-of-bounds
        # SetPixel calls (no-op).
        try:
            canvas.SetPixel(x + col, y - 1, 255, 40, 40)
            canvas.SetPixel(x + col, y + h, 255, 40, 40)
        except Exception:
            pass

    # Precip underline (drawn just below the bar, 1px thick).
    # No canvas.height() check needed — out-of-bounds SetPixel is safe.
    if precip_underline:
        underline_y = y + h
        for col in range(w):
            m = int((col + 0.5) * minutes_per_px)
            hh = m // 60
            st = hour_state.get(hh)
            if not st:
                continue
            kind = st.get("precip_kind")
            inten = st.get("precip_intensity", 0.0)
            if kind == "rain" or kind == "mix":
                # blue dotted: density 1/2 at low intensity, fully filled at high
                period = 4 if inten < 0.4 else (3 if inten < 0.7 else 2)
                if (col % period) == 0:
                    canvas.SetPixel(x + col, underline_y, 80, 140, 240)
            elif kind == "snow":
                # white checker: alternating 2-pixel groups
                if (col // 2) % 2 == 0:
                    canvas.SetPixel(x + col, underline_y, 240, 248, 255)


# ---------------------------------------------------------------------------
# Convenience helpers for designs
# ---------------------------------------------------------------------------

def label_daylight_bar(canvas, font, x: int, y: int, w: int, h: int, palette: dict,
                       sunrise_hm: str, sunset_hm: str, label_color=None) -> None:
    """Draw small "12A 6A 12P 6P 12A" tick labels above the daylight bar."""
    if label_color is None:
        label_color = palette.get("label", _C(120, 120, 120))
    from .common import draw_text, draw_text_centered, text_width
    # Tick positions: 0, 6h, 12h, 18h, 24h
    ticks = [(0, "12a"), (360, "6a"), (720, "12p"), (1080, "6p")]
    for m, label in ticks:
        col = int(round(m / 1440.0 * w))
        draw_text_centered(canvas, font, x + col, y, label_color, label)


def make_weather_state_from_current(cur: dict) -> dict:
    """Map a CURRENT-conditions dict into the state dict used by the bar
    when no hourly data is available."""
    icon = int(cur.get("icon_code", 0))
    cloud_pct = float(cur.get("cloud_cover_pct", 0))
    precip = None
    intensity = 0.0
    if icon in (6, 12, 13, 19, 28, 36, 39, 46):
        precip = "rain"
        intensity = 0.5
    elif icon in (8, 16, 17, 18, 25, 38, 40):
        precip = "snow"
        intensity = 0.5
    elif icon in (7, 14, 15, 27, 37):
        precip = "mix"
        intensity = 0.5
    return {
        "cloud_pct": cloud_pct,
        "precip_kind": precip,
        "precip_intensity": intensity,
    }
