"""Weather Panels preview package.

A second-generation preview module for evaluating weather panel designs on
the LED matrix. Unlike ``weather_layout_preview`` (3 screens, 10 designs),
this package supports SIX screen types per design and brings two new
visualization primitives:

  * ``daylight_bar`` -- a 1440-minute-mapped strip whose per-pixel sky
    color is computed from real solar elevation at Richmond BC, modulated
    by the hourly forecast (overcast desaturates, rain darkens, snow
    speckles white).

  * ``radar_map`` -- pulls a real Environment Canada radar PNG (XAM
    Aldergrove) and traces it down to LED-matrix-friendly pixel art at
    two zoom levels (SW BC wide / Metro Vancouver tight).

See ``renderer.py`` for the renderer entry-point used by ``main.py``.
"""

from .renderer import WeatherPanelsRenderer

__all__ = ["WeatherPanelsRenderer"]
