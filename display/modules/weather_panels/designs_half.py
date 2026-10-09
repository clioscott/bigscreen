"""Half-zone weather screens (224 x 138).

Single data-dense design. No theme system — hardcoded colors matching
the calendar/headline panel style (white text, gray labels, colored
accents). Every screen focuses on showing CORRECT INFORMATION with
no wasted space.

Screens:
  - Current Conditions (full detail)
  - Hourly Forecast (next 18h tabular)
  - 5-7 Day Forecast (tabular)
  - Weather Warning
  - Radar/Satellite (delegated to radar.py)
"""

from __future__ import annotations

import math
import time
from datetime import datetime

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
    ElementPlacer,
    get_animation_track,
)
from .radar import render_radar_track
from .designs_base import DesignBase


# ---------------------------------------------------------------------------
# Hardcoded colors — matches calendar/headline panel style
# ---------------------------------------------------------------------------
WHITE      = _C(255, 255, 255)    # primary text
GRAY       = _C(150, 150, 150)    # labels, secondary text
DARK_GRAY  = _C(80, 80, 80)      # borders, dividers
DIM_GRAY   = _C(100, 100, 100)   # very subtle text
BLUE       = _C(95, 198, 255)    # accent (precip, condition text)
WARM       = _C(255, 184, 77)    # warm temps, warnings
COOL       = _C(158, 200, 255)   # cool temps
GREEN      = _C(136, 240, 140)   # good / no alerts
RED        = _C(255, 90, 80)     # alerts, bad
BLACK      = _C(0, 0, 0)         # icon background

# A minimal palette dict so radar.py's render_radar_track still works
# (it reads palette["fg"], palette["accent"], etc. for pin/legend colors).
_RADAR_PALETTE = {
    "fg":      WHITE,
    "label":   GRAY,
    "accent":  BLUE,
    "warm":    WARM,
    "cool":    COOL,
    "good":    GREEN,
    "bad":     RED,
    "muted":   DIM_GRAY,
    "frame":   DARK_GRAY,
    "bg_tile": BLACK,
}


def _temp_color(t):
    """Color a temperature value: warm if >=22, cool if <=2, white otherwise."""
    if t is None:
        return GRAY
    if t >= 22:
        return WARM
    if t <= 2:
        return COOL
    return WHITE


def _pop_color(p):
    """Color a POP% value: blue if >=60, white if >=30, gray otherwise."""
    if p is None:
        return GRAY
    if p >= 60:
        return BLUE
    if p >= 30:
        return WHITE
    return DIM_GRAY


def _track_title(track: str) -> str:
    """Display title for a radar/satellite track."""
    cfg = get_animation_track(track)
    if cfg and cfg.get("display_name"):
        return cfg["display_name"].upper()
    return track.replace("_", " ").upper()


def _rgb_from_hex(s: str | None):
    if not s or not isinstance(s, str):
        return None
    s = s.strip().lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            return None
    return None


# ===========================================================================
# THE SINGLE DESIGN — data-dense, functional, no decorative elements
# ===========================================================================
class WeatherDesign(DesignBase):
    """Data-dense weather display. Every pixel shows information.

    Colors: white text, gray labels, blue accents, warm/cool temp colors.
    No borders, no frames — just clean data with thin divider lines.
    """
    name = "weather"
    palette = "cool"       # not used — we hardcode colors
    zone_size = 224

    # ------------------------------------------------------------------
    # CURRENT CONDITIONS
    # ------------------------------------------------------------------
    # Layout (224 x 138):
    #   Row 0-1:   [36x36 icon on black] [BIG TEMP]  [feels X°]
    #   Row 2:     [icon area cont.]     [+X° vs 24h ago]
    #   Row 3-8:   [icon area cont.]     [condition text, up to 6 lines]
    #   Row 9:     ─── divider ───  (y+62–79, slides with line count)
    #   Row 8:     Wind W 18km/h G32          101.3kPa ↑
    #   Row 9:     Precip 20% · rain 4mm
    #   Row 10:    RH 78%  Dew 9°  Vis 24km
    #   Row 11:    UV 3 Moderate  AQHI 2 Low
    #   Row 12:    SR 06:14  SS 20:32  Cloud 65%
    #   Row 13:    Normal H14° L7°     Today H14° L8°
    #   Row 14:    ─── divider ───  (y+128)
    #   Row 15:    As of 10:34                    CYVR
    # ------------------------------------------------------------------
    @staticmethod
    def screen_current(canvas, x, y, w, h, fonts, palette, data, now):
        cur = data["current"]

        # ── 36x36 icon on solid black ──
        icon_x, icon_y = x + 2, y + 2
        icon_sz = 36
        rect_filled(canvas, icon_x, icon_y, icon_sz, icon_sz, BLACK)
        draw_icon(canvas, cur["icon_code"], icon_x, icon_y, icon_sz, now,
                  cur.get("is_night", False))

        # ── Temperature (big font, right of icon) — precise to 1-2 decimals ──
        tx = x + 42      # 2 + 36 + 4px gap
        draw_text(canvas, fonts["big"], tx, y + 14,
                  _temp_color(cur["temp_c"]), fmt_temp_precise(cur["temp_c"]))

        # ── Feels-like ──
        feels_x = tx + text_width(fonts["big"], fmt_temp_precise(cur["temp_c"])) + 4
        draw_text(canvas, fonts["tiny"], feels_x, y + 14,
                  GRAY, f"feels {fmt_temp(cur['feels_c'])}")

        # ── 24h comparison ──
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
            draw_text(canvas, fonts["tiny"], tx, y + 25, delta_col, delta_str)

        # ── Condition text (right of icon, up to 6 lines) ──
        # 6 lines × 7px = 42px; with a 4px gap the divider lands at y+79 max,
        # leaving div+7+5×8 = div+47 for 6 data rows, which still clears the
        # footer divider at y+h-10 with a comfortable margin.
        cond = cur.get("weather_long") or cur.get("weather_text") or ""
        cond_max_w = w - 44
        cond_lines = wrap_to_width(fonts["tiny"], cond, cond_max_w, max_lines=6)
        for i, ln in enumerate(cond_lines):
            draw_text(canvas, fonts["tiny"], tx, y + 33 + i * 7, BLUE, ln)

        # ── Divider — slides down to fit however many condition lines were drawn ──
        div_y = max(y + 62, y + 33 + len(cond_lines) * 7 + 4)
        hline(canvas, x + 2, div_y, w - 4, DARK_GRAY)

        # ── Data rows (7px spacing = tiny font height + 1px) ──
        row_y = div_y + 7   # first data row baseline

        # Row 1: Wind + Pressure
        wind_str = f"Wind {cur['wind_dir_text']} {cur['wind_kmh']}km/h"
        if cur.get("wind_gust_kmh"):
            wind_str += f" G{cur['wind_gust_kmh']}"
        draw_text(canvas, fonts["tiny"], x + 2, row_y, WHITE, wind_str)
        trend_ch = {"rising": "^", "falling": "v", "steady": "-"}.get(
            cur.get("pressure_trend", ""), "")
        pres_str = f"{cur['pressure_kpa']:.1f}kPa{trend_ch}"
        draw_text_right(canvas, fonts["tiny"], x + w - 2, row_y, WHITE, pres_str)
        row_y += 8

        # Row 2: Precip
        today_d = data["daily"][0] if data.get("daily") else {}
        pop = today_d.get("pop_pct", 0) or 0
        rain = today_d.get("rain_mm", 0) or 0
        snow = today_d.get("snow_cm", 0) or 0
        parts = [f"Precip {pop}%"]
        if rain > 0:
            parts.append(f"rain {rain}mm")
        if snow > 0:
            parts.append(f"snow {snow}cm")
        draw_text(canvas, fonts["tiny"], x + 2, row_y,
                  _pop_color(pop), " ".join(parts))
        row_y += 8

        # Row 3: Humidity + Dewpoint + Visibility
        draw_text(canvas, fonts["tiny"], x + 2, row_y, GRAY,
                  f"RH {cur['rh_pct']}%  Dew {fmt_temp(cur['dew_point_c'])}  Vis {cur['visibility_km']}km")
        row_y += 8

        # Row 4: UV + AQHI
        uv_str = f"UV {cur['uv_index']}"
        uv_cat = cur.get("uv_category", "")
        if uv_cat:
            uv_str += f" {uv_cat}"
        aqhi_str = f"AQHI {cur['aqhi']}"
        aqhi_cat = cur.get("aqhi_category", "")
        if aqhi_cat:
            aqhi_str += f" {aqhi_cat}"
        draw_text(canvas, fonts["tiny"], x + 2, row_y, WHITE,
                  f"{uv_str}  {aqhi_str}")
        row_y += 8

        # Row 5: Sunrise / Sunset / Cloud
        draw_text(canvas, fonts["tiny"], x + 2, row_y, GRAY,
                  f"SR {cur['sunrise']}  SS {cur['sunset']}  Cloud {cur.get('cloud_cover_pct', '--')}%")
        row_y += 8

        # Row 6: Normals vs Today
        norm_h = cur.get("normal_high")
        norm_l = cur.get("normal_low")
        if norm_h is not None and norm_l is not None:
            draw_text(canvas, fonts["tiny"], x + 2, row_y, GRAY,
                      f"Normal H{fmt_temp(norm_h)} L{fmt_temp(norm_l)}")
        if today_d.get("hi") is not None:
            draw_text_right(canvas, fonts["tiny"], x + w - 2, row_y, WHITE,
                            f"Today H{fmt_temp(today_d['hi'])} L{fmt_temp(today_d['lo'])}")
        row_y += 8

        # ── Bottom divider + footer ──
        footer_div_y = y + h - 10
        hline(canvas, x + 2, footer_div_y, w - 4, DARK_GRAY)
        draw_text(canvas, fonts["tiny"], x + 2, y + h - 2, GRAY,
                  f"As of {cur['obs_local']}")
        draw_text_right(canvas, fonts["tiny"], x + w - 2, y + h - 2, GRAY,
                        cur.get("station", ""))

    # ------------------------------------------------------------------
    # HOURLY FORECAST — next 18 hours, 2-column tabular
    # ------------------------------------------------------------------
    # Layout (224 x 138):
    #   Header: "HOURLY FORECAST"          "as of HH:MM"
    #   ─── divider ───
    #   Col header: HR  TEMP POP WIND  |  HR  TEMP POP WIND
    #   9 rows per column × 7px = 63px
    #   Total: ~10 + 7 + 63 = 80px (fits in 138)
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

        # Column headers
        col_w = w // 2    # 112px per column
        for col in range(2):
            cx = x + col * col_w
            draw_text(canvas, fonts["tiny"], cx + 2, y + 18, GRAY, "HR")
            draw_text(canvas, fonts["tiny"], cx + 18, y + 18, GRAY, "TMP")
            draw_text(canvas, fonts["tiny"], cx + 38, y + 18, GRAY, "POP")
            draw_text(canvas, fonts["tiny"], cx + 58, y + 18, GRAY, "WIND")
        hline_dotted(canvas, x + 2, y + 20, w - 4, DARK_GRAY, 1, 1)

        # Data rows: 2 columns × 9 rows
        n = len(hourly)
        rows_per_col = 9
        row_h = 7

        # Check we have room — available height below col headers
        start_y = y + 22

        for col in range(2):
            cx = x + col * col_w
            if col > 0:
                vline_dotted(canvas, cx - 1, y + 13, h - 17, DARK_GRAY, 1, 1)

            for row in range(rows_per_col):
                idx = col * rows_per_col + row
                if idx >= n:
                    break
                hr = hourly[idx]
                ry = start_y + row * row_h

                # Bail if we'd draw off-screen
                if ry + 6 > y + h:
                    break

                # Hour label
                hour_str = hr.get("hour", "?")
                draw_text(canvas, fonts["tiny"], cx + 2, ry + 5,
                          GRAY, f"{hour_str:>3s}")

                # Temperature
                temp = hr.get("temp_c")
                draw_text(canvas, fonts["tiny"], cx + 18, ry + 5,
                          _temp_color(temp), fmt_temp(temp))

                # POP%
                pop = hr.get("pop_pct", 0) or 0
                if pop > 0:
                    draw_text(canvas, fonts["tiny"], cx + 38, ry + 5,
                              _pop_color(pop), f"{pop}%")

                # Wind
                wind_dir = hr.get("wind_dir", "")
                wind_spd = hr.get("wind_kmh", 0) or 0
                if wind_spd > 0:
                    wind_str = f"{wind_dir}{wind_spd}"
                    draw_text(canvas, fonts["tiny"], cx + 58, ry + 5,
                              GRAY, truncate_to_width(fonts["tiny"], wind_str, col_w - 62))

    # ------------------------------------------------------------------
    # 7-DAY FORECAST — tabular, one row per day
    # ------------------------------------------------------------------
    # Layout (224 x 138):
    #   Header: "7-DAY FORECAST"          "RICHMOND"
    #   ─── divider ───
    #   Col header: DAY       HI  LO  POP  CONDITION
    #   7 rows × 16px = 112px
    #   Total: ~10 + 6 + 112 = 128px (fits in 138)
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

        # Column headers (tiny font)
        chy = y + 18
        draw_text(canvas, fonts["tiny"], x + 2, chy, GRAY, "DAY")
        draw_text(canvas, fonts["tiny"], x + 30, chy, GRAY, "HI")
        draw_text(canvas, fonts["tiny"], x + 50, chy, GRAY, "LO")
        draw_text(canvas, fonts["tiny"], x + 70, chy, GRAY, "POP")
        draw_text(canvas, fonts["tiny"], x + 92, chy, GRAY, "CONDITION")
        hline_dotted(canvas, x + 2, y + 20, w - 4, DARK_GRAY, 1, 1)

        # Data rows
        n = len(days)
        # Compute row height to fill available space
        avail = h - 24  # below col headers
        row_h = min(16, avail // n)
        icon_sz = min(14, row_h - 2)
        use_icon = icon_sz >= 10

        for i, d in enumerate(days):
            ry = y + 22 + i * row_h
            baseline = ry + row_h - 3   # text baseline within row

            # Bail if off-screen
            if baseline > y + h - 2:
                break

            # Day name (3 chars)
            name = d.get("short", d.get("name", "?"))[:3].upper()
            draw_text(canvas, fonts["tiny"], x + 2, baseline, WHITE, name)

            # Icon
            if use_icon:
                draw_icon(canvas, d.get("icon_code", 0),
                          x + 17, ry + (row_h - icon_sz) // 2, icon_sz, now)

            # Hi temp
            draw_text(canvas, fonts["tiny"], x + 30, baseline,
                      _temp_color(d.get("hi")), fmt_temp(d.get("hi")))

            # Lo temp
            draw_text(canvas, fonts["tiny"], x + 50, baseline,
                      GRAY, fmt_temp(d.get("lo")))

            # POP%
            pop = d.get("pop_pct", 0) or 0
            draw_text(canvas, fonts["tiny"], x + 70, baseline,
                      _pop_color(pop), f"{pop}%")

            # Condition text
            cond = d.get("text", "")
            # Max width: from x+92 to (x+w-4) but leave room for precip amount
            rain_mm = d.get("rain_mm", 0) or 0
            snow_cm = d.get("snow_cm", 0) or 0
            has_amt = rain_mm > 0 or snow_cm > 0
            cond_end = w - 28 if has_amt else w - 4
            cond_max = cond_end - 92
            if cond_max > 0:
                draw_text(canvas, fonts["tiny"], x + 92, baseline,
                          BLUE, truncate_to_width(fonts["tiny"], cond, cond_max))

            # Rain/snow amount on far right
            if has_amt:
                amt = f"{rain_mm}mm" if rain_mm > 0 else f"{snow_cm}cm"
                draw_text_right(canvas, fonts["tiny"], x + w - 2, baseline,
                                COOL, amt)

            # Row separator
            if i < n - 1:
                hline_dotted(canvas, x + 2, ry + row_h, w - 4, DARK_GRAY, 1, 2)

    # ------------------------------------------------------------------
    # WEATHER WARNING
    # ------------------------------------------------------------------
    @staticmethod
    def screen_warning(canvas, x, y, w, h, fonts, palette, data, now):
        alerts = data.get("alerts") or []

        if not alerts:
            # No active alerts
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

        # Alert header bar (filled background)
        rect_filled(canvas, x + 1, y + 1, w - 2, 12, risk_col)
        draw_text_centered(canvas, fonts["small"], x + w // 2, y + 10,
                           BLACK, a.get("alert_name_en", "ALERT").upper())

        # Region
        draw_text(canvas, fonts["tiny"], x + 2, y + 22, WHITE,
                  truncate_to_width(fonts["tiny"],
                                    a.get("feature_name", ""), w - 4))

        # Issued / Expires
        draw_text(canvas, fonts["tiny"], x + 2, y + 30, GRAY,
                  f"Issued {a.get('issued', '?')}  Exp {a.get('expires', '?')}")

        # Body text (wrapped, fill remaining space)
        body = a.get("alert_text_en", "")
        lines = wrap_to_width(fonts["tiny"], body, w - 4, max_lines=14)
        for i, ln in enumerate(lines):
            ty = y + 40 + i * 7
            if ty + 6 > y + h:
                break
            draw_text(canvas, fonts["tiny"], x + 2, ty, WHITE, ln)

    # ------------------------------------------------------------------
    # RADAR / SATELLITE — delegates to radar.py
    # ------------------------------------------------------------------
    @staticmethod
    def screen_radar(canvas, x, y, w, h, fonts, palette, data, now, track: str = "local", **kwargs):
        anim_elapsed = kwargs.pop('anim_elapsed', None)
        preset_base = kwargs.pop('preset_base', None)
        # Minimal header — maximize map area
        draw_text(canvas, fonts["small"], x + 2, y + 8, WHITE, _track_title(track))
        hline(canvas, x + 2, y + 11, w - 4, DARK_GRAY)
        render_radar_track(canvas, x + 2, y + 13, w - 4, h - 16,
                           track=track, palette=_RADAR_PALETTE, show_pin=True,
                           anim_elapsed=anim_elapsed,
                           preset_base=preset_base,
                           **{k: v for k, v in kwargs.items() if k == "layer_overrides"})

    # ------------------------------------------------------------------
    # MINUTELY — kept as stub since it's removed from the screen cycle
    # but the base class defines it. Does nothing if somehow called.
    # ------------------------------------------------------------------
    @staticmethod
    def screen_minutely(canvas, x, y, w, h, fonts, palette, data, now):
        pass


# ===========================================================================
# REGISTRY — single design, no cycling
# ===========================================================================

HALF_DESIGNS = [
    WeatherDesign,
]
