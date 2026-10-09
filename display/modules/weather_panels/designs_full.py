"""Full-zone weather screens (448 x 138).

Single data-dense design. No theme system — hardcoded colors matching
the half-zone design. With double the width (448 vs 224), the full-zone
layouts use side-by-side sections for more information density.

Screens:
  - Current Conditions (left: icon+temp+condition, right: all stats)
  - Hourly Forecast (3 columns × 6 rows = 18 hours)
  - 7-Day Forecast (wider table with more room for condition text)
  - Weather Warning (full-width text wrapping)
  - Radar/Satellite (delegated to radar.py, bigger map)
"""

from __future__ import annotations

import math
import time

from display.gfx import graphics

from .common import (
    _C,
    hline, vline, rect_filled,
    hline_dotted, vline_dotted,
    draw_text, draw_text_centered, draw_text_right, text_width,
    truncate_to_width, wrap_to_width,
    draw_icon,
    fmt_temp,
    fmt_temp_precise,
    get_animation_track,
)
from .radar import render_radar_track
from .designs_base import DesignBase
from .designs_half import (
    WHITE, GRAY, DARK_GRAY, DIM_GRAY, BLUE, WARM, COOL, GREEN, RED, BLACK,
    _RADAR_PALETTE, _temp_color, _pop_color, _rgb_from_hex,
)


def _track_title(track: str) -> str:
    cfg = get_animation_track(track)
    if cfg and cfg.get("display_name"):
        return cfg["display_name"].upper()
    return track.replace("_", " ").upper()


# ===========================================================================
# THE SINGLE FULL-ZONE DESIGN
# ===========================================================================
class WeatherDesignFull(DesignBase):
    """Full-zone data-dense weather display (448 x 138).
    Uses the extra width for side-by-side layouts."""
    name = "weather-full"
    palette = "cool"
    zone_size = 448

    # ------------------------------------------------------------------
    # CURRENT CONDITIONS — full width
    # ------------------------------------------------------------------
    # Left half (224px): icon + temp + condition + 24h comparison
    # Right half (224px): all data rows, two columns of stats
    # ------------------------------------------------------------------
    @staticmethod
    def screen_current(canvas, x, y, w, h, fonts, palette, data, now):
        cur = data["current"]
        mid = x + w // 2   # center divider

        # ═══ LEFT HALF ═══

        # 48x48 icon on black background (we have room for bigger icon)
        icon_x, icon_y = x + 4, y + 4
        icon_sz = 48
        rect_filled(canvas, icon_x, icon_y, icon_sz, icon_sz, BLACK)
        draw_icon(canvas, cur["icon_code"], icon_x, icon_y, icon_sz, now,
                  cur.get("is_night", False))

        # Big temperature (right of icon) — precise to 1-2 decimals
        tx = x + 58
        draw_text(canvas, fonts["big"], tx, y + 18,
                  _temp_color(cur["temp_c"]), fmt_temp_precise(cur["temp_c"]))

        # Feels-like
        feels_x = tx + text_width(fonts["big"], fmt_temp_precise(cur["temp_c"])) + 4
        draw_text(canvas, fonts["tiny"], feels_x, y + 18, GRAY,
                  f"feels {fmt_temp(cur['feels_c'])}")

        # 24h comparison
        hist = data.get("hourly_history", [])
        if hist and hist[0].get("temp_c") is not None and cur["temp_c"] is not None:
            delta = cur["temp_c"] - hist[0]["temp_c"]
            if abs(delta) >= 0.5:
                sign = "+" if delta > 0 else ""
                delta_str = f"{sign}{delta:.0f}{chr(176)} vs 24h ago"
                delta_col = WARM if delta > 0 else COOL
            else:
                delta_str = "same as 24h ago"
                delta_col = GRAY
            draw_text(canvas, fonts["tiny"], tx, y + 28, delta_col, delta_str)

        # Condition text — the left column runs from y+58 down to the footer
        # at y+h-2, giving ~78px = 10 lines of tiny text with no overlap.
        cond = cur.get("weather_long") or cur.get("weather_text") or ""
        cond_max_w = mid - x - 8
        cond_lines = wrap_to_width(fonts["tiny"], cond, cond_max_w, max_lines=10)
        for i, ln in enumerate(cond_lines):
            draw_text(canvas, fonts["tiny"], x + 4, y + 58 + i * 7, BLUE, ln)

        # As-of timestamp at bottom-left
        draw_text(canvas, fonts["tiny"], x + 4, y + h - 2, GRAY,
                  f"As of {cur['obs_local']}  {cur.get('station', '')}")

        # ═══ VERTICAL DIVIDER ═══
        vline_dotted(canvas, mid - 1, y + 2, h - 4, DARK_GRAY, 1, 1)

        # ═══ RIGHT HALF — dense stats ═══
        rx = mid + 4          # right column start
        rw = w - (rx - x) - 4   # available width
        ry = y + 8           # first row baseline

        # Today's forecast data
        today_d = data["daily"][0] if data.get("daily") else {}

        rows = []

        # Wind
        wind_str = f"Wind {cur['wind_dir_text']} {cur['wind_kmh']}km/h"
        if cur.get("wind_gust_kmh"):
            wind_str += f" gust {cur['wind_gust_kmh']}"
        rows.append((wind_str, WHITE))

        # Pressure
        trend_ch = {"rising": "^", "falling": "v", "steady": "-"}.get(
            cur.get("pressure_trend", ""), "")
        rows.append((f"Pressure {cur['pressure_kpa']:.1f} kPa {trend_ch}  {cur.get('pressure_trend', '')}", WHITE))

        # Precip
        pop = today_d.get("pop_pct", 0) or 0
        rain = today_d.get("rain_mm", 0) or 0
        snow = today_d.get("snow_cm", 0) or 0
        parts = [f"Precip {pop}%"]
        if rain > 0:
            parts.append(f"rain {rain}mm")
        if snow > 0:
            parts.append(f"snow {snow}cm")
        rows.append((" ".join(parts), _pop_color(pop)))

        # Humidity + Dewpoint
        rows.append((f"RH {cur['rh_pct']}%  Dewpoint {fmt_temp(cur['dew_point_c'])}", GRAY))

        # Visibility
        rows.append((f"Visibility {cur['visibility_km']}km  Cloud {cur.get('cloud_cover_pct', '--')}%", GRAY))

        # UV
        uv_str = f"UV {cur['uv_index']}"
        uv_cat = cur.get("uv_category", "")
        if uv_cat:
            uv_str += f" {uv_cat}"
        aqhi_str = f"AQHI {cur['aqhi']}"
        aqhi_cat = cur.get("aqhi_category", "")
        if aqhi_cat:
            aqhi_str += f" {aqhi_cat}"
        rows.append((f"{uv_str}  {aqhi_str}", WHITE))

        # Sunrise / Sunset
        rows.append((f"Sunrise {cur['sunrise']}  Sunset {cur['sunset']}", GRAY))

        # Normals
        norm_h = cur.get("normal_high")
        norm_l = cur.get("normal_low")
        if norm_h is not None:
            norm_str = f"Normal H{fmt_temp(norm_h)} L{fmt_temp(norm_l)}"
            if today_d.get("hi") is not None:
                norm_str += f"  Today H{fmt_temp(today_d['hi'])} L{fmt_temp(today_d['lo'])}"
            rows.append((norm_str, GRAY))

        # Draw all rows
        for i, (text_str, color) in enumerate(rows):
            row_baseline = ry + i * 8
            if row_baseline > y + h - 4:
                break
            draw_text(canvas, fonts["tiny"], rx, row_baseline, color,
                      truncate_to_width(fonts["tiny"], text_str, rw))

    # ------------------------------------------------------------------
    # HOURLY FORECAST — 3 columns × 6 rows = 18 hours
    # ------------------------------------------------------------------
    @staticmethod
    def screen_hourly(canvas, x, y, w, h, fonts, palette, data, now):
        cur = data["current"]
        hourly = data.get("hourly", [])[:18]
        if not hourly:
            draw_text(canvas, fonts["small"], x + 4, y + 20, GRAY, "No hourly data")
            return

        # Header
        draw_text(canvas, fonts["small"], x + 2, y + 8, WHITE, "HOURLY FORECAST")
        draw_text_right(canvas, fonts["tiny"], x + w - 2, y + 8, GRAY,
                        f"as of {cur['obs_local']}")
        hline(canvas, x + 2, y + 11, w - 4, DARK_GRAY)

        # 3 columns
        col_count = 3
        col_w = w // col_count   # ~149px each
        rows_per_col = 6
        row_h = 7

        # Column headers in each column
        for col in range(col_count):
            cx = x + col * col_w
            draw_text(canvas, fonts["tiny"], cx + 2, y + 18, GRAY, "HR")
            draw_text(canvas, fonts["tiny"], cx + 18, y + 18, GRAY, "TMP")
            draw_text(canvas, fonts["tiny"], cx + 38, y + 18, GRAY, "POP")
            draw_text(canvas, fonts["tiny"], cx + 58, y + 18, GRAY, "WIND")
            draw_text(canvas, fonts["tiny"], cx + 90, y + 18, GRAY, "COND")
        hline_dotted(canvas, x + 2, y + 20, w - 4, DARK_GRAY, 1, 1)

        start_y = y + 22
        n = len(hourly)

        for col in range(col_count):
            cx = x + col * col_w
            if col > 0:
                vline_dotted(canvas, cx - 1, y + 13, h - 17, DARK_GRAY, 1, 1)

            for row in range(rows_per_col):
                idx = col * rows_per_col + row
                if idx >= n:
                    break
                hr = hourly[idx]
                ry = start_y + row * row_h
                if ry + 6 > y + h:
                    break

                # Hour
                draw_text(canvas, fonts["tiny"], cx + 2, ry + 5, GRAY,
                          f"{hr.get('hour', '?'):>3s}")
                # Temp
                draw_text(canvas, fonts["tiny"], cx + 18, ry + 5,
                          _temp_color(hr.get("temp_c")), fmt_temp(hr.get("temp_c")))
                # POP
                pop = hr.get("pop_pct", 0) or 0
                if pop > 0:
                    draw_text(canvas, fonts["tiny"], cx + 38, ry + 5,
                              _pop_color(pop), f"{pop}%")
                # Wind
                wind_dir = hr.get("wind_dir", "")
                wind_spd = hr.get("wind_kmh", 0) or 0
                if wind_spd > 0:
                    draw_text(canvas, fonts["tiny"], cx + 58, ry + 5, GRAY,
                              f"{wind_dir}{wind_spd}")
                # Condition (LOP)
                lop = hr.get("lop", "")
                if lop and lop != "Nil":
                    draw_text(canvas, fonts["tiny"], cx + 90, ry + 5, BLUE,
                              truncate_to_width(fonts["tiny"], lop, col_w - 94))

    # ------------------------------------------------------------------
    # 7-DAY FORECAST — wide table with full condition text
    # ------------------------------------------------------------------
    @staticmethod
    def screen_forecast(canvas, x, y, w, h, fonts, palette, data, now):
        cur = data["current"]
        days = data.get("daily", [])[:7]
        if not days:
            draw_text(canvas, fonts["small"], x + 4, y + 20, GRAY, "No forecast data")
            return

        # Header
        draw_text(canvas, fonts["small"], x + 2, y + 8, WHITE, "7-DAY FORECAST")
        draw_text_right(canvas, fonts["tiny"], x + w - 2, y + 8, GRAY,
                        cur.get("location", "").upper())
        hline(canvas, x + 2, y + 11, w - 4, DARK_GRAY)

        # Column headers
        chy = y + 18
        draw_text(canvas, fonts["tiny"], x + 2, chy, GRAY, "DAY")
        draw_text(canvas, fonts["tiny"], x + 30, chy, GRAY, "HI")
        draw_text(canvas, fonts["tiny"], x + 52, chy, GRAY, "LO")
        draw_text(canvas, fonts["tiny"], x + 74, chy, GRAY, "POP")
        draw_text(canvas, fonts["tiny"], x + 100, chy, GRAY, "WIND")
        draw_text(canvas, fonts["tiny"], x + 140, chy, GRAY, "RAIN")
        draw_text(canvas, fonts["tiny"], x + 174, chy, GRAY, "CONDITION")
        hline_dotted(canvas, x + 2, y + 20, w - 4, DARK_GRAY, 1, 1)

        n = len(days)
        avail = h - 24
        row_h = min(16, avail // n)
        icon_sz = min(14, row_h - 2)
        use_icon = icon_sz >= 10

        for i, d in enumerate(days):
            ry = y + 22 + i * row_h
            baseline = ry + row_h - 3
            if baseline > y + h - 2:
                break

            # Day
            name = d.get("short", d.get("name", "?"))[:3].upper()
            draw_text(canvas, fonts["tiny"], x + 2, baseline, WHITE, name)

            # Icon
            if use_icon:
                draw_icon(canvas, d.get("icon_code", 0),
                          x + 17, ry + (row_h - icon_sz) // 2, icon_sz, now)

            # Hi
            draw_text(canvas, fonts["tiny"], x + 30, baseline,
                      _temp_color(d.get("hi")), fmt_temp(d.get("hi")))
            # Lo
            draw_text(canvas, fonts["tiny"], x + 52, baseline,
                      GRAY, fmt_temp(d.get("lo")))
            # POP
            pop = d.get("pop_pct", 0) or 0
            draw_text(canvas, fonts["tiny"], x + 74, baseline,
                      _pop_color(pop), f"{pop}%")
            # Wind
            wind_str = f"{d.get('wind_dir', '')}{d.get('wind_kmh', '')}"
            draw_text(canvas, fonts["tiny"], x + 100, baseline, GRAY, wind_str)
            # Rain/Snow
            rain_mm = d.get("rain_mm", 0) or 0
            snow_cm = d.get("snow_cm", 0) or 0
            if rain_mm > 0:
                draw_text(canvas, fonts["tiny"], x + 140, baseline, COOL, f"{rain_mm}mm")
            elif snow_cm > 0:
                draw_text(canvas, fonts["tiny"], x + 140, baseline, COOL, f"{snow_cm}cm")
            # Condition text (use remaining width)
            cond = d.get("text", "")
            cond_max = w - 178
            if cond_max > 0:
                draw_text(canvas, fonts["tiny"], x + 174, baseline, BLUE,
                          truncate_to_width(fonts["tiny"], cond, cond_max))

            if i < n - 1:
                hline_dotted(canvas, x + 2, ry + row_h, w - 4, DARK_GRAY, 1, 2)

    # ------------------------------------------------------------------
    # WEATHER WARNING — full-width
    # ------------------------------------------------------------------
    @staticmethod
    def screen_warning(canvas, x, y, w, h, fonts, palette, data, now):
        alerts = data.get("alerts") or []
        if not alerts:
            draw_text(canvas, fonts["small"], x + 2, y + 8, WHITE, "ALERTS")
            hline(canvas, x + 2, y + 11, w - 4, DARK_GRAY)
            draw_text_centered(canvas, fonts["small"], x + w // 2, y + h // 2,
                               GREEN, "NO ACTIVE ALERTS")
            draw_text_centered(canvas, fonts["tiny"], x + w // 2, y + h // 2 + 12,
                               GRAY, data["current"]["location"].upper())
            return

        a = alerts[0]
        risk_rgb = _rgb_from_hex(a.get("risk_colour_en"))
        risk_col = _C(*risk_rgb) if risk_rgb else RED

        rect_filled(canvas, x + 1, y + 1, w - 2, 12, risk_col)
        draw_text_centered(canvas, fonts["small"], x + w // 2, y + 10,
                           BLACK, a.get("alert_name_en", "ALERT").upper())

        draw_text(canvas, fonts["tiny"], x + 4, y + 22, WHITE,
                  truncate_to_width(fonts["tiny"], a.get("feature_name", ""), w - 8))
        draw_text(canvas, fonts["tiny"], x + 4, y + 30, GRAY,
                  f"Issued {a.get('issued', '?')}  Exp {a.get('expires', '?')}")

        body = a.get("alert_text_en", "")
        lines = wrap_to_width(fonts["tiny"], body, w - 8, max_lines=14)
        for i, ln in enumerate(lines):
            ty = y + 40 + i * 7
            if ty + 6 > y + h:
                break
            draw_text(canvas, fonts["tiny"], x + 4, ty, WHITE, ln)

    # ------------------------------------------------------------------
    # RADAR / SATELLITE
    # ------------------------------------------------------------------
    @staticmethod
    def screen_radar(canvas, x, y, w, h, fonts, palette, data, now, track: str = "local", **kwargs):
        anim_elapsed = kwargs.pop('anim_elapsed', None)
        preset_base = kwargs.pop('preset_base', None)
        draw_text(canvas, fonts["small"], x + 2, y + 8, WHITE, _track_title(track))
        hline(canvas, x + 2, y + 11, w - 4, DARK_GRAY)
        render_radar_track(canvas, x + 2, y + 13, w - 4, h - 16,
                           track=track, palette=_RADAR_PALETTE, show_pin=True,
                           anim_elapsed=anim_elapsed,
                           preset_base=preset_base,
                           **{k: v for k, v in kwargs.items() if k == "layer_overrides"})

    # ------------------------------------------------------------------
    # MINUTELY — stub (removed from cycle)
    # ------------------------------------------------------------------
    @staticmethod
    def screen_minutely(canvas, x, y, w, h, fonts, palette, data, now):
        pass


# ===========================================================================
# REGISTRY — single design
# ===========================================================================

FULL_DESIGNS = [
    WeatherDesignFull,
]
