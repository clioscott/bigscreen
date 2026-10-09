"""v1 radar playback was replaced by display/modules/weather_maps.py (server-rendered packs).

The v1 design classes still reference render_radar_track for their screen_radar
method; it's never used by v2's cycle (map entries use the "map" panel instead).
"""


def render_radar_track(canvas, x, y, w, h, *args, **kwargs):
    return None
