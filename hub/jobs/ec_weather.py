# Copied verbatim from v1 (server/fetchers/weather.py); used as a parsing library by hub/jobs/weather.py.
"""EC Weather Fetcher — citypage XML + SWOB real-time observations.

Fetches live weather data from Environment Canada's public feeds and
transforms it into the dict shape expected by the weather panels renderer
(same keys as ``common.SAMPLE``).

Data sources
~~~~~~~~~~~~
* **Citypage XML** — forecast + current conditions summary

  EC MSC Datamart new URL format (as of ~2024):
  ``https://dd.weather.gc.ca/today/citypage_weather/{PROV}/{HH}/``
  ``{YYYYMMDD}T{HHmmss.sss}Z_MSC_CitypageWeather_{SiteCode}_{L}.xml``

  The fetcher discovers the latest file by listing the hourly directory,
  trying the current UTC hour then falling back to previous hours.
  Station s0000141 = Vancouver, BC.

* **SWOB-ML** — Surface Weather OBservation real-time minute-level data
  ``https://dd.weather.gc.ca/observations/swob-ml/latest/CYVR-AUTO-swob.xml``
  (YVR airport auto station)

Refresh cadence: every 15 minutes (configurable).
"""

from __future__ import annotations

import math
import re
import threading
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# EC MSC Datamart — new timestamped-file layout (old /xml/ path returns 404)
# Base URL for the "today" hourly directories:
#   {CITYPAGE_BASE}/{PROVINCE}/{HH}/{timestamp}_MSC_CitypageWeather_{STATION}_{LANG}.xml
CITYPAGE_BASE     = "https://dd.weather.gc.ca/today/citypage_weather"
CITYPAGE_PROVINCE = "BC"
CITYPAGE_STATION  = "s0000141"   # Vancouver
CITYPAGE_LANG     = "en"

SWOB_URL = (
    "https://dd.weather.gc.ca/observations/swob-ml/latest/CYVR-AUTO-swob.xml"
)
# SWOB provides individual per-minute files for the last ~2 hours.
# We fetch the directory listing then grab the latest 60+ files for
# minutely_history and hourly_history roll-ups.
SWOB_LISTING_URL = (
    "https://dd.weather.gc.ca/observations/swob-ml/"
    "partners/observations/dms/metric/CYVR/"
)

DEFAULT_CITYPAGE_REFRESH_SEC = 900   # 15 min — citypage XML updates roughly hourly
DEFAULT_SWOB_REFRESH_SEC     = 120   # 2 min  — SWOB auto station publishes ~every minute
DEFAULT_REFRESH_SEC = DEFAULT_CITYPAGE_REFRESH_SEC  # kept for back-compat

# SWOB-ML XML namespace
SWOB_NS = {"om": "http://www.opengis.net/om/1.0",
           "gml": "http://www.opengis.net/gml",
           "xlink": "http://www.w3.org/1999/xlink",
           "swob": "http://dms.ec.gc.ca/schema/point-observation/2.1"}

# Timezone for Vancouver
_PT = timezone(timedelta(hours=-7))   # PDT; we'll auto-detect below

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _int(val: Any, default: int = 0) -> int:
    if val is None:
        return default
    try:
        return int(round(float(val)))
    except (ValueError, TypeError):
        return default

def _text(el: ET.Element | None, default: str = "") -> str:
    if el is None:
        return default
    return (el.text or "").strip() or default


def _hour_label(h: int) -> str:
    """0→12A, 1→1A, 12→12P, 13→1P, etc."""
    if h == 0:
        return "12A"
    elif h < 12:
        return f"{h}A"
    elif h == 12:
        return "12P"
    else:
        return f"{h - 12}P"


def _wind_dir_deg_to_text(deg: float | None) -> str:
    """Convert degrees to compass text (N, NNE, NE, ...)."""
    if deg is None:
        return "Calm"
    dirs = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
    idx = int((deg + 11.25) / 22.5) % 16
    return dirs[idx]


def _pressure_trend(change_kpa: float | None) -> str:
    if change_kpa is None:
        return "steady"
    if change_kpa > 0.02:
        return "rising"
    if change_kpa < -0.02:
        return "falling"
    return "steady"


def _ec_icon_to_code(icon_str: str | None) -> int:
    """EC icon codes from citypage are strings like '06', '30'. Return int."""
    if not icon_str:
        return 0
    try:
        return int(icon_str)
    except ValueError:
        return 0


def _pop_text_to_pct(pop_str: str | None) -> int:
    """Parse POP string like '60' or '' into int percent."""
    if not pop_str:
        return 0
    try:
        return int(pop_str)
    except ValueError:
        return 0


def _lop_from_pop(pop: int) -> str:
    """Derive Likelihood of Precipitation text from POP%."""
    if pop >= 70:
        return "High"
    elif pop >= 30:
        return "Moderate"
    elif pop >= 10:
        return "Slight"
    return "Nil"


# Map day-of-week abbreviations to dow_idx (Monday=1 .. Sunday=7)
_DOW_MAP = {
    "Mon": 1, "Monday": 1,
    "Tue": 2, "Tuesday": 2,
    "Wed": 3, "Wednesday": 3,
    "Thu": 4, "Thursday": 4,
    "Fri": 5, "Friday": 5,
    "Sat": 6, "Saturday": 6,
    "Sun": 7, "Sunday": 7,
}

# ---------------------------------------------------------------------------
# Citypage XML parser
# ---------------------------------------------------------------------------

def _parse_citypage(xml_bytes: bytes) -> dict:
    """Parse EC citypage XML into the weather dict shape."""
    root = ET.fromstring(xml_bytes)
    data: dict = {}

    # --- Current conditions ---
    cc = root.find(".//currentConditions")
    if cc is not None:
        station_el = cc.find("station")
        station_code = station_el.get("code", "CYVR") if station_el is not None else "CYVR"

        # Temperature
        temp_el = cc.find("temperature")
        temp_c = _float(_text(temp_el), 0.0)

        # Wind
        wind_speed_el = cc.find("wind/speed")
        wind_gust_el = cc.find("wind/gust")
        wind_dir_el = cc.find("wind/direction")
        wind_kmh = _float(_text(wind_speed_el), 0)
        wind_gust = _float(_text(wind_gust_el))
        wind_dir_text = wind_dir_el.text.strip() if wind_dir_el is not None and wind_dir_el.text else "Calm"

        # Wind direction in degrees (if available)
        wind_bearing_el = cc.find("wind/bearing")
        wind_dir_deg = _float(wind_bearing_el.text if wind_bearing_el is not None else None)

        # Humidity
        rh_el = cc.find("relativeHumidity")
        rh_pct = _int(_text(rh_el), 50)

        # Dew point
        dp_el = cc.find("dewpoint")
        dew_point = _float(_text(dp_el))

        # Pressure
        pressure_el = cc.find("pressure")
        pressure_kpa = _float(_text(pressure_el), 101.3)
        pressure_trend_attr = pressure_el.get("tendency", "steady") if pressure_el is not None else "steady"
        pressure_change_el = cc.find("pressure")
        pressure_change = _float(pressure_change_el.get("change", "0") if pressure_change_el is not None else "0")

        # Visibility
        vis_el = cc.find("visibility")
        visibility_km = _float(_text(vis_el), 24)

        # Humidex / Wind chill
        humidex_el = cc.find("humidex")
        windchill_el = cc.find("windChill")
        humidex_c = _float(_text(humidex_el)) if humidex_el is not None and _text(humidex_el) else None
        wind_chill_c = _float(_text(windchill_el)) if windchill_el is not None and _text(windchill_el) else None

        # Feels like
        if wind_chill_c is not None:
            feels_c = wind_chill_c
        elif humidex_c is not None:
            feels_c = humidex_c
        else:
            feels_c = temp_c

        # Condition text + icon
        condition_el = cc.find("condition")
        weather_text = _text(condition_el, "N/A")
        icon_el = cc.find("iconCode")
        icon_code = _ec_icon_to_code(_text(icon_el))

        # Observation time
        obs_dt_el = cc.find("dateTime[@zone='PDT']") or cc.find("dateTime[@zone='PST']") or cc.find("dateTime[@name='observation']")
        obs_local = ""
        obs_utc = ""
        is_night = False
        if obs_dt_el is not None:
            hour_el = obs_dt_el.find("hour")
            min_el = obs_dt_el.find("minute")
            h = _int(_text(hour_el))
            m = _int(_text(min_el))
            obs_local = f"{h:02d}:{m:02d}"
            is_night = h < 6 or h >= 21
        # UTC observation
        obs_utc_el = cc.find("dateTime[@zone='UTC']")
        if obs_utc_el is not None:
            h_u = _int(_text(obs_utc_el.find("hour")))
            m_u = _int(_text(obs_utc_el.find("minute")))
            obs_utc = f"{h_u:02d}:{m_u:02d}Z"

        data["current"] = {
            "location":        "Vancouver",
            "station":         station_code,
            "icon_code":       icon_code,
            "is_night":        is_night,
            "weather_text":    weather_text,
            "weather_long":    "",    # filled from forecast below
            "temp_c":          temp_c,
            "feels_c":         feels_c,
            "wind_chill_c":    wind_chill_c,
            "humidex_c":       humidex_c,
            "rh_pct":          rh_pct,
            "dew_point_c":     dew_point,
            "wind_kmh":        _int(wind_kmh),
            "wind_gust_kmh":   _int(wind_gust),
            "wind_dir_deg":    _int(wind_dir_deg) if wind_dir_deg else 0,
            "wind_dir_text":   wind_dir_text,
            "pressure_kpa":    pressure_kpa,
            "pressure_trend":  pressure_trend_attr,
            "pressure_change": pressure_change,
            "visibility_km":   _int(visibility_km),
            "uv_index":        0,
            "uv_category":     "N/A",
            "aqhi":            0,
            "aqhi_category":   "N/A",
            "cloud_cover_pct": 0,
            "obs_local":       obs_local,
            "obs_utc":         obs_utc,
            "sunrise":         "",
            "sunset":          "",
            "normal_high":     0,
            "normal_low":      0,
        }
    else:
        # Minimal fallback
        data["current"] = {
            "location": "Vancouver", "station": "CYVR",
            "icon_code": 0, "is_night": False,
            "weather_text": "N/A", "weather_long": "",
            "temp_c": 0, "feels_c": 0,
            "wind_chill_c": None, "humidex_c": None,
            "rh_pct": 0, "dew_point_c": 0,
            "wind_kmh": 0, "wind_gust_kmh": 0,
            "wind_dir_deg": 0, "wind_dir_text": "Calm",
            "pressure_kpa": 101.3, "pressure_trend": "steady",
            "pressure_change": 0, "visibility_km": 0,
            "uv_index": 0, "uv_category": "N/A",
            "aqhi": 0, "aqhi_category": "N/A",
            "cloud_cover_pct": 0,
            "obs_local": "", "obs_utc": "",
            "sunrise": "", "sunset": "",
            "normal_high": 0, "normal_low": 0,
        }

    # --- Sunrise / Sunset ---
    rise_set = root.find(".//riseSet")
    if rise_set is not None:
        # There are multiple dateTime elements; find local ones
        for dt_el in rise_set.findall("dateTime"):
            zone = dt_el.get("zone", "")
            name = dt_el.get("name", "")
            if zone in ("PDT", "PST"):
                h = _int(_text(dt_el.find("hour")))
                m = _int(_text(dt_el.find("minute")))
                ts = f"{h:02d}:{m:02d}"
                if name == "sunrise":
                    data["current"]["sunrise"] = ts
                elif name == "sunset":
                    data["current"]["sunset"] = ts

    # --- Regional normals ---
    normals = root.find(".//regionalNormals")
    if normals is not None:
        for temp_el in normals.findall("temperature"):
            cls = temp_el.get("class", "")
            if cls == "high":
                data["current"]["normal_high"] = _int(_text(temp_el))
            elif cls == "low":
                data["current"]["normal_low"] = _int(_text(temp_el))

    # --- Hourly forecast ---
    hourly_group = root.find(".//hourlyForecastGroup")
    hourly: list[dict] = []
    if hourly_group is not None:
        for hf in hourly_group.findall("hourlyForecast"):
            utc_str = hf.get("dateTimeUTC", "")
            # Parse UTC string → local hour
            ts_h = 0
            if utc_str:
                try:
                    utc_dt = datetime.strptime(utc_str, "%Y%m%d%H%M")
                    utc_dt = utc_dt.replace(tzinfo=timezone.utc)
                    local_dt = utc_dt.astimezone(_PT)
                    ts_h = local_dt.hour
                except Exception:
                    pass

            temp_el = hf.find("temperature")
            icon_el = hf.find("iconCode")
            pop_el = hf.find("lop")  # likelihood of precip
            wind_speed_el = hf.find("wind/speed")
            wind_dir_el = hf.find("wind/direction")

            pop_pct = _pop_text_to_pct(_text(pop_el))
            lop_text = _text(hf.find("lop"), "")
            if not lop_text or lop_text.isdigit():
                lop_text = _lop_from_pop(pop_pct)

            hourly.append({
                "hour":     _hour_label(ts_h),
                "ts_h":     ts_h,
                "temp_c":   _int(_text(temp_el)),
                "icon_code": _ec_icon_to_code(_text(icon_el)),
                "pop_pct":  pop_pct,
                "wind_kmh": _int(_text(wind_speed_el)),
                "wind_dir": _text(wind_dir_el, "Calm"),
                "lop":      lop_text,
            })
    # Pad to 24 if EC provides fewer
    while len(hourly) < 24:
        hourly.append({
            "hour": "", "ts_h": 0, "temp_c": 0, "icon_code": 0,
            "pop_pct": 0, "wind_kmh": 0, "wind_dir": "Calm", "lop": "Nil",
        })
    data["hourly"] = hourly[:24]

    # --- Daily forecast ---
    forecast_group = root.find(".//forecastGroup")
    daily: list[dict] = []
    if forecast_group is not None:
        for fc in forecast_group.findall("forecast"):
            period_el = fc.find("period")
            period_name = _text(period_el, "")

            # Skip "night" periods — we only want day forecasts
            # EC gives: "Monday", "Monday night", "Tuesday", ...
            if "night" in period_name.lower():
                continue

            # Abbreviation
            short = period_name[:3] if period_name else ""
            dow_idx = _DOW_MAP.get(period_name, _DOW_MAP.get(short, 0))

            # Temperatures — EC has separate high/low in <temperatures>
            temps = fc.find("temperatures")
            hi = None
            lo = None
            if temps is not None:
                for t_el in temps.findall("temperature"):
                    cls = t_el.get("class", "")
                    if cls == "high":
                        hi = _int(_text(t_el))
                    elif cls == "low":
                        lo = _int(_text(t_el))
            if hi is None:
                # Single temp entry
                temp_el = fc.find("temperatures/temperature")
                hi = _int(_text(temp_el))
            if lo is None:
                lo = hi - 6  # rough guess

            icon_el = fc.find("abbreviatedForecast/iconCode")
            text_el = fc.find("abbreviatedForecast/textSummary")
            pop_el = fc.find("abbreviatedForecast/pop")

            # Extended text
            long_el = fc.find("textSummary")
            long_text = _text(long_el, "")

            # Wind
            wind_summary = fc.find("winds/textSummary")
            wind_text = _text(wind_summary, "")
            wind_kmh = 0
            wind_dir = ""
            # Try to extract from wind text like "west 25 km/h"
            wm = re.search(r'(\w+)\s+(\d+)\s*km', wind_text, re.IGNORECASE)
            if wm:
                wind_dir = wm.group(1).upper()[:2]
                wind_kmh = _int(wm.group(2))

            # UV
            uv_el = fc.find("uv/index")
            uv_val = _int(_text(uv_el))

            # Precip
            snow_el = fc.find("precipitation/accumulation/amount[@units='cm']")
            rain_el = fc.find("precipitation/accumulation/amount[@units='mm']")

            pop_pct = _pop_text_to_pct(_text(pop_el))

            daily_name = period_name
            if len(daily) == 0:
                daily_name = "Today"

            daily.append({
                "name":     daily_name,
                "short":    short,
                "dow_idx":  dow_idx,
                "hi":       hi,
                "lo":       lo,
                "icon_code": _ec_icon_to_code(_text(icon_el)),
                "text":     _text(text_el, ""),
                "pop_pct":  pop_pct,
                "wind_kmh": wind_kmh,
                "wind_dir": wind_dir,
                "uv":       uv_val,
                "snow_cm":  _int(_text(snow_el)),
                "rain_mm":  _int(_text(rain_el)),
                "long":     long_text,
            })

    # Ensure at least 7 days
    while len(daily) < 7:
        daily.append({
            "name": "", "short": "", "dow_idx": 0,
            "hi": 0, "lo": 0, "icon_code": 0, "text": "",
            "pop_pct": 0, "wind_kmh": 0, "wind_dir": "",
            "uv": 0, "snow_cm": 0, "rain_mm": 0, "long": "",
        })
    data["daily"] = daily[:7]

    # Fill weather_long from today's forecast if available
    if daily and daily[0].get("long"):
        data["current"]["weather_long"] = daily[0]["long"]

    # --- UV index (from forecast if current doesn't have one) ---
    if daily and daily[0].get("uv"):
        data["current"]["uv_index"] = daily[0]["uv"]
        uv = daily[0]["uv"]
        if uv >= 11:
            data["current"]["uv_category"] = "Extreme"
        elif uv >= 8:
            data["current"]["uv_category"] = "Very High"
        elif uv >= 6:
            data["current"]["uv_category"] = "High"
        elif uv >= 3:
            data["current"]["uv_category"] = "Moderate"
        else:
            data["current"]["uv_category"] = "Low"

    # --- Warnings / Alerts ---
    alerts: list[dict] = []
    warnings_el = root.find(".//warnings")
    if warnings_el is not None:
        for event in warnings_el.findall("event"):
            etype = event.get("type", "")
            epriority = event.get("priority", "")
            edesc = event.get("description", "")

            # Risk color based on priority
            if epriority == "urgent":
                risk_color = "#FF0000"
                alert_type = "warning"
            elif epriority == "high":
                risk_color = "#FF8C00"
                alert_type = "warning"
            else:
                risk_color = "#FFFF00"
                alert_type = "watch"

            # Date issued
            issued_el = event.find("dateTime[@zone='PDT']") or event.find("dateTime[@zone='PST']")
            issued_str = ""
            if issued_el is not None:
                txt = issued_el.find("textSummary")
                issued_str = _text(txt, "")

            alerts.append({
                "alert_code":     etype,
                "alert_name_en":  edesc,
                "alert_short_en": edesc.split()[0] if edesc else etype,
                "alert_type":     alert_type,
                "status_en":      "in effect",
                "risk_colour_en": risk_color,
                "issued":         issued_str,
                "expires":        "",
                "feature_name":   "Metro Vancouver",
                "impact_en":      "",
                "alert_text_en":  "",
                "confidence_en":  "",
            })
    data["alerts"] = alerts

    # --- Placeholder fields not available from citypage ---
    data["minutely"] = [0.0] * 60
    data["minutely_history"] = [0.0] * 60
    data["hourly_history"] = []

    return data


# ---------------------------------------------------------------------------
# SWOB-ML parser — real-time observations from CYVR auto station
# ---------------------------------------------------------------------------

def _fetch_swob(session: requests.Session, url: str = SWOB_URL) -> dict | None:
    """Fetch the latest SWOB-ML auto observation from CYVR.

    Returns a dict with real-time obs values, or None on failure.
    """
    try:
        resp = session.get(url, timeout=15)
        if resp.status_code != 200:
            return None
        return _parse_swob(resp.content)
    except Exception:
        return None


def _parse_swob(xml_bytes: bytes) -> dict:
    """Parse SWOB-ML XML and extract key observation fields.

    SWOB-ML uses a complex OGC schema. The observation values live under:
      //om:result/swob:elements/swob:element[@name="..."][@value="..."]
    """
    root = ET.fromstring(xml_bytes)

    # The namespace map varies slightly between files.  Build it from
    # the document root to be safe.
    ns = {}
    # Always set our known fallbacks
    ns.update(SWOB_NS)

    # Extract all element name→value pairs
    vals: dict[str, str] = {}
    # Try multiple XPath patterns since the namespace can shift
    for el in root.iter():
        tag = el.tag
        if tag.endswith("}element") or tag == "element":
            name = el.get("name", "")
            value = el.get("value", "")
            if name and value:
                vals[name] = value

    if not vals:
        # Fallback: try without namespace
        for el in root.findall(".//{http://dms.ec.gc.ca/schema/point-observation/2.1}element"):
            name = el.get("name", "")
            value = el.get("value", "")
            if name and value:
                vals[name] = value

    if not vals:
        return {}

    # Map SWOB element names to our fields
    obs: dict = {}

    # Temperature
    for key in ("air_temp", "air_temperature", "sfc_temp"):
        if key in vals:
            obs["temp_c"] = _float(vals[key])
            break

    # Dew point
    for key in ("dwpt_temp", "dew_point_temp", "dwpt"):
        if key in vals:
            obs["dew_point_c"] = _float(vals[key])
            break

    # Relative humidity
    for key in ("rel_hum", "relative_humidity"):
        if key in vals:
            obs["rh_pct"] = _int(vals[key])
            break

    # Wind speed (km/h)
    for key in ("avg_wnd_spd_10m_mt58-60", "avg_wnd_spd_10m_pst10mnt",
                "mean_wnd_spd_10m_mt58-60", "avg_wnd_spd_10m", "wnd_spd"):
        if key in vals:
            obs["wind_kmh"] = _int(_float(vals[key]) * 3.6)  # m/s → km/h
            break

    # Wind gust
    for key in ("max_wnd_spd_10m_pst10mnt", "max_wnd_gst_spd_10m",
                "pk_wnd_spd_10m", "gust_wnd_spd"):
        if key in vals:
            obs["wind_gust_kmh"] = _int(_float(vals[key]) * 3.6)
            break

    # Wind direction (degrees)
    for key in ("avg_wnd_dir_10m_mt58-60", "avg_wnd_dir_10m_pst10mnt",
                "avg_wnd_dir_10m", "wnd_dir"):
        if key in vals:
            deg = _float(vals[key])
            obs["wind_dir_deg"] = _int(deg)
            obs["wind_dir_text"] = _wind_dir_deg_to_text(deg)
            break

    # Pressure (kPa)
    for key in ("stn_pres", "mslp", "sfc_pres"):
        if key in vals:
            obs["pressure_kpa"] = round(_float(vals[key]) / 10.0, 2)  # hPa→kPa
            break

    # Visibility (km) — SWOB gives metres
    for key in ("visibility", "vis"):
        if key in vals:
            obs["visibility_km"] = _int(_float(vals[key]) / 1000.0)
            break

    # Precipitation (1-minute or 10-minute rate mm/h)
    for key in ("pcpn_amt_pst1hr", "tot_precip_pst1hr"):
        if key in vals:
            obs["precip_1h_mm"] = _float(vals[key])
            break

    # Cloud cover (oktas → percent)
    for key in ("tot_cld_amt", "cld_amt"):
        if key in vals:
            oktas = _int(vals[key])
            obs["cloud_cover_pct"] = min(100, oktas * 12.5)
            break

    # Humidex
    if "humidex" in vals:
        obs["humidex_c"] = _float(vals["humidex"])

    # Wind chill
    for key in ("wnd_chill", "wind_chill"):
        if key in vals:
            obs["wind_chill_c"] = _float(vals[key])
            break

    # Observation time — try to find from the data identification section
    for key in ("date_tm", "obs_date_tm"):
        if key in vals:
            obs["obs_utc_str"] = vals[key]
            break

    return obs


def _merge_swob_into_current(data: dict, swob: dict) -> None:
    """Overlay real-time SWOB observations onto the citypage current block.

    SWOB is typically more current than citypage (updates every minute vs
    every hour), so its values take precedence for fields it provides.
    """
    cur = data.get("current")
    if not cur or not swob:
        return

    # Direct overrides — SWOB is fresher
    if "temp_c" in swob:
        cur["temp_c"] = round(swob["temp_c"], 1)
    if "dew_point_c" in swob:
        cur["dew_point_c"] = round(swob["dew_point_c"], 1)
    if "rh_pct" in swob:
        cur["rh_pct"] = swob["rh_pct"]
    if "wind_kmh" in swob:
        cur["wind_kmh"] = swob["wind_kmh"]
    if "wind_gust_kmh" in swob:
        cur["wind_gust_kmh"] = swob["wind_gust_kmh"]
    if "wind_dir_deg" in swob:
        cur["wind_dir_deg"] = swob["wind_dir_deg"]
    if "wind_dir_text" in swob:
        cur["wind_dir_text"] = swob["wind_dir_text"]
    if "pressure_kpa" in swob:
        cur["pressure_kpa"] = swob["pressure_kpa"]
    if "visibility_km" in swob:
        cur["visibility_km"] = swob["visibility_km"]
    if "cloud_cover_pct" in swob:
        cur["cloud_cover_pct"] = _int(swob["cloud_cover_pct"])
    if "humidex_c" in swob:
        cur["humidex_c"] = round(swob["humidex_c"], 1)
    if "wind_chill_c" in swob:
        cur["wind_chill_c"] = round(swob["wind_chill_c"], 1)

    # Recalculate feels_like
    if swob.get("wind_chill_c") is not None:
        cur["feels_c"] = round(swob["wind_chill_c"], 1)
    elif swob.get("humidex_c") is not None:
        cur["feels_c"] = round(swob["humidex_c"], 1)
    elif "temp_c" in swob:
        cur["feels_c"] = round(swob["temp_c"], 1)

    # Update the displayed observation timestamp from SWOB so the "as of"
    # time reflects the real-time observation rather than the (hourly) citypage.
    if "obs_utc_str" in swob:
        try:
            dt = datetime.fromisoformat(swob["obs_utc_str"].replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone(_PT)
            cur["obs_local"] = f"{local_dt.hour:02d}:{local_dt.minute:02d}"
            cur["obs_utc"]   = dt.strftime("%H:%MZ")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Top-level fetch
# ---------------------------------------------------------------------------

def _make_citypage_re(station: str, lang: str) -> re.Pattern:
    return re.compile(
        r'(\d{8}T[\d.]+Z_MSC_CitypageWeather_'
        + re.escape(station)
        + r'_'
        + re.escape(lang)
        + r'\.xml)'
    )

# Pre-built regex for the defaults
_CITYPAGE_FILE_RE = _make_citypage_re(CITYPAGE_STATION, CITYPAGE_LANG)


def _resolve_citypage_url(
    session: requests.Session,
    verbose: bool = False,
    province: str = CITYPAGE_PROVINCE,
    station: str = CITYPAGE_STATION,
    lang: str = CITYPAGE_LANG,
) -> str | None:
    """Discover the latest citypage XML URL by listing EC's today/ directory.

    EC changed the URL format: files are now timestamped and placed in hourly
    subdirectories.  We try the current UTC hour, then fall back up to 3 hours
    earlier in case the current-hour directory hasn't been published yet.

    Returns the full URL of the latest matching file, or None on failure.
    """
    file_re = (
        _CITYPAGE_FILE_RE
        if (station == CITYPAGE_STATION and lang == CITYPAGE_LANG)
        else _make_citypage_re(station, lang)
    )
    now_utc = datetime.now(timezone.utc)
    for delta_h in range(4):   # try current hour, then -1h, -2h, -3h
        check_dt = now_utc - timedelta(hours=delta_h)
        hh = f"{check_dt.hour:02d}"
        dir_url = f"{CITYPAGE_BASE}/{province}/{hh}/"
        if verbose:
            print(f"  Trying citypage dir: {dir_url}", flush=True)
        try:
            resp = session.get(dir_url, timeout=10)
            if resp.status_code != 200:
                if verbose:
                    print(f"  Dir HTTP {resp.status_code}", flush=True)
                continue
            matches = file_re.findall(resp.text)
            if not matches:
                if verbose:
                    print(f"  No matching files in dir (station={station})", flush=True)
                continue
            # Filenames are ISO-timestamped; lexicographic sort gives latest last
            latest = sorted(matches)[-1]
            url = f"{dir_url}{latest}"
            if verbose:
                print(f"  Resolved: {latest}", flush=True)
            return url
        except Exception as e:
            if verbose:
                print(f"  Dir error: {e}", flush=True)
            continue
    return None


def fetch_weather_once(
    verbose: bool = False,
    province: str = CITYPAGE_PROVINCE,
    station: str = CITYPAGE_STATION,
    swob_url: str = SWOB_URL,
) -> dict | None:
    """Run one complete weather fetch. Returns the weather dict or None."""
    session = requests.Session()
    session.headers.update({"User-Agent": "BigScreenCalendar/1.0"})

    # 1. Citypage XML — discover the current file URL dynamically
    if verbose:
        print("  Fetching citypage XML...", flush=True)
    try:
        citypage_url = _resolve_citypage_url(
            session, verbose=verbose, province=province, station=station
        )
        if citypage_url is None:
            if verbose:
                print("  Citypage: could not resolve URL for any recent hour", flush=True)
            return None
        resp = session.get(citypage_url, timeout=20)
        if resp.status_code != 200:
            if verbose:
                print(f"  Citypage HTTP {resp.status_code}", flush=True)
            return None
        data = _parse_citypage(resp.content)
    except Exception as e:
        if verbose:
            print(f"  Citypage error: {e}", flush=True)
        return None

    # 2. SWOB real-time observations
    if verbose:
        print("  Fetching SWOB real-time obs...", flush=True)
    try:
        swob = _fetch_swob(session, url=swob_url)
        if swob:
            _merge_swob_into_current(data, swob)
            if verbose:
                print(f"  SWOB: {len(swob)} fields merged", flush=True)
        else:
            if verbose:
                print("  SWOB: no data (non-fatal)", flush=True)
    except Exception as e:
        if verbose:
            print(f"  SWOB error (non-fatal): {e}", flush=True)

    # Stamp
    data["_fetched_utc"] = time.time()
    data["_source"] = "ec_citypage+swob"

    return data


# ---------------------------------------------------------------------------
# Thread worker (same pattern as headlines.py)
# ---------------------------------------------------------------------------

def _weather_thread_worker(
    queue,
    citypage_refresh_sec: int,
    swob_refresh_sec: int,
    province: str,
    station: str,
    swob_url: str,
):
    """Fetch loop running in a daemon thread.

    Two independent cadences:
    * Citypage XML  — fetched every ``citypage_refresh_sec`` (default 15 min).
      The EC file only updates roughly hourly so there is no point polling faster.
    * SWOB real-time obs — fetched every ``swob_refresh_sec`` (default 2 min).
      The YVR auto station publishes a new observation approximately every minute
      so this keeps current conditions (temp, wind, pressure, humidity) fresh.

    The two fetches share a persistent requests.Session and the loop sleeps
    in short increments (``_POLL_INTERVAL_SEC``) so both cadences are served
    promptly without spawning separate threads.
    """

    _POLL_INTERVAL_SEC = 30   # how often the loop wakes to check both timers

    def put_log(msg):
        queue.put({"module": "weather", "type": "log", "msg": msg})

    session = requests.Session()
    session.headers.update({"User-Agent": "BigScreenCalendar/1.0"})

    last_citypage_fetch = 0.0   # force immediate fetch on first wake
    last_swob_fetch     = 0.0
    current_data: dict | None = None
    citypage_count = 0

    while True:
        now = time.time()
        did_update = False

        # ── Citypage XML refresh (slower cadence) ────────────────────────────
        if now - last_citypage_fetch >= citypage_refresh_sec:
            citypage_count += 1
            put_log(f"Fetching citypage XML (#{citypage_count})")
            try:
                citypage_url = _resolve_citypage_url(
                    session, province=province, station=station
                )
                if citypage_url:
                    resp = session.get(citypage_url, timeout=20)
                    if resp.status_code == 200:
                        current_data = _parse_citypage(resp.content)
                        current_data["_fetched_utc"] = time.time()
                        current_data["_source"] = "ec_citypage+swob"
                        last_citypage_fetch = now
                        did_update = True
                        put_log("Citypage updated")
                    else:
                        put_log(f"WARNING: Citypage HTTP {resp.status_code}")
                        last_citypage_fetch = now  # back off; don't hammer on 404
                else:
                    put_log("WARNING: Could not resolve citypage URL")
                    last_citypage_fetch = now
            except Exception as e:
                put_log(f"Citypage error: {e}")
                traceback.print_exc()
                last_citypage_fetch = now  # back off on exception too

        # ── SWOB real-time refresh (faster cadence) ──────────────────────────
        # Only run once we have citypage base data to merge into.
        if current_data is not None and (now - last_swob_fetch >= swob_refresh_sec):
            try:
                swob = _fetch_swob(session, url=swob_url)
                if swob:
                    _merge_swob_into_current(current_data, swob)
                    current_data["_fetched_utc"] = time.time()
                    last_swob_fetch = now
                    did_update = True
                    temp = current_data.get("current", {}).get("temp_c", "?")
                    obs  = current_data.get("current", {}).get("obs_local", "")
                    put_log(f"SWOB updated: {temp}°C @ {obs}")
                else:
                    put_log("WARNING: SWOB fetch returned no data")
                    last_swob_fetch = now  # back off; don't hammer
            except Exception as e:
                put_log(f"SWOB error (non-fatal): {e}")
                last_swob_fetch = now

        # Push to queue whenever either source produced fresh data.
        if did_update and current_data:
            queue.put({"module": "weather", "data": current_data})

        time.sleep(_POLL_INTERVAL_SEC)


def start_weather_thread(config: dict, queue) -> threading.Thread:
    """Start the weather fetcher in a daemon thread."""
    wcfg = config.get("modules", {}).get("weather", {})
    # "refresh_interval_sec" is the legacy single-cadence key; map it to citypage.
    citypage_refresh_sec = wcfg.get(
        "citypage_refresh_interval_sec",
        wcfg.get("refresh_interval_sec", DEFAULT_CITYPAGE_REFRESH_SEC),
    )
    swob_refresh_sec = wcfg.get("swob_refresh_interval_sec", DEFAULT_SWOB_REFRESH_SEC)
    province = wcfg.get("citypage_province", CITYPAGE_PROVINCE)
    station  = wcfg.get("citypage_station",  CITYPAGE_STATION)
    swob_url = wcfg.get("swob_url",          SWOB_URL)
    t = threading.Thread(
        target=_weather_thread_worker,
        args=(queue, citypage_refresh_sec, swob_refresh_sec, province, station, swob_url),
        daemon=True,
    )
    t.start()
    return t
