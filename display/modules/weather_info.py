"""Weather information screens (current conditions, hourly, forecast, warnings).

Thin driver around v1's design screens (display/modules/weather_panels), which
draw from the hub's weather.json in the v1 citypage format.
"""

from __future__ import annotations

import os
import time

from display.gfx import Color, graphics
from display.modules.weather_panels.common import PALETTES
from display.modules.weather_panels.designs_full import FULL_DESIGNS
from display.modules.weather_panels.designs_half import HALF_DESIGNS

SCREENS = {"current": "screen_current", "hourly": "screen_hourly", "forecast": "screen_forecast",
           "warning": "screen_warning", "minutely": "screen_minutely"}


class WeatherInfoPanel:
    def __init__(self, cfg, loader, full=False):
        self.loader = loader
        self.designs = FULL_DESIGNS if full else HALF_DESIGNS

        def font(name):
            f = graphics.Font()
            p = cfg.get_font_path(name)
            if p and os.path.exists(p):
                f.LoadFont(p)
            return f

        self.fonts = {"tiny": font("small"), "small": font("top_bar"), "med": font("headlines_title"),
                      "big": font("clock")}
        self.dim = Color(150, 150, 150)

    def draw(self, c, zone, entry, transition):
        x, y, w, h = zone
        data = self.loader.get_data("weather")
        if not data or "current" not in data:
            graphics.DrawText(c, self.fonts["small"], x + 4, y + 20, self.dim, "Loading weather data...")
            return None
        design = self.designs[int(entry.get("weather_design_idx", 0)) % len(self.designs)]
        method = getattr(design, SCREENS.get(entry.get("weather_screen", "current"), "screen_current"), None)
        if method is None:
            return "done"
        palette = PALETTES.get(getattr(design, "palette", "cool"), PALETTES["cool"])
        method(c, x, y, w, h, self.fonts, palette, data, time.time())
        return None

    def has_alert(self):
        data = self.loader.get_data("weather") or {}
        return bool(data.get("alerts") or data.get("warnings"))
