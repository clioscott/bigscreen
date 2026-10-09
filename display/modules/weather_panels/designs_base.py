"""DesignBase abstract class.

Every design in this package subclasses ``DesignBase`` and implements the
six screen renderers. The renderer contract is identical across half /
full sizes -- the design itself decides whether (w, h) is a half or full
zone by inspecting ``w`` (224 vs 448).

Each screen method has the same signature so the renderer's dispatch
table stays uniform:

    def screen_X(canvas, x, y, w, h, fonts, palette, sample, now): ...

``palette`` is the resolved dict from PALETTES (not the key).
``sample`` is the SAMPLE fixture from common.py for offline preview, or
the live data passed in when wired up to the server later.
``now`` is a ``time.time()`` float (used for icon GIF animation phase).

A design's class-level ``palette`` attribute names the default palette;
the renderer can also override per-screen palettes (e.g., warning screen
forces ``palette = "storm"``).
"""

from __future__ import annotations


class DesignBase:
    """Abstract design with 6 screens. Subclasses set ``name``, ``palette``,
    and ``zone_size`` (224 or 448) and implement the screen methods."""
    name: str = ""
    palette: str = "cool"
    zone_size: int = 224     # 224 (half) or 448 (full)

    @staticmethod
    def screen_current(canvas, x, y, w, h, fonts, palette, sample, now): ...

    @staticmethod
    def screen_forecast(canvas, x, y, w, h, fonts, palette, sample, now): ...

    @staticmethod
    def screen_hourly(canvas, x, y, w, h, fonts, palette, sample, now): ...

    @staticmethod
    def screen_minutely(canvas, x, y, w, h, fonts, palette, sample, now): ...

    @staticmethod
    def screen_radar(canvas, x, y, w, h, fonts, palette, sample, now,
                     track: str = "local", **kwargs): ...

    @staticmethod
    def screen_warning(canvas, x, y, w, h, fonts, palette, sample, now): ...


# Legacy export kept for any caller that imported it.
SCREEN_METHODS = (
    "screen_current",
    "screen_forecast",
    "screen_hourly",
    "screen_minutely",
    "screen_radar",
    "screen_warning",
)
