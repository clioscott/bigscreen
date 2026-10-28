"""The Webcams list (one rotation entry plays the webcams in the Webcams page's order) and the camera grid."""

import numpy as np

from display.gfx import Canvas
from display.modules import webcam as wm


class _Cfg:
    def __init__(self, webcams):
        self.modules = {"webcams": webcams}


def _panel(webcams, monkeypatch, clock):
    monkeypatch.setattr(wm.time, "time", lambda: clock[0])
    monkeypatch.setitem(wm._list_pos, "i", -1)
    p = wm.WebcamPanel(_Cfg(webcams))
    drawn = []
    p._draw_cam = lambda c, zone, entry, transition: drawn.append(entry["cam"])
    return p, drawn


def test_playlist_keeps_order_and_skips_off_and_missing():
    s = {"cams": {"a": {}, "b": {}, "c": {}},
         "playlist": [{"cam": "c", "sec": 20}, {"cam": "a", "on": False}, {"cam": "gone"}, {"cam": "b"}]}
    assert wm.playlist_of(s) == [("c", 20.0), ("b", 60.0)]
    assert wm.playlist_of({"cams": {"x": {}, "y": {}}}) == [("x", 60.0), ("y", 60.0)]   # no list yet: every webcam


def test_each_turn_plays_the_next_webcams_then_ends(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {"a": {}, "b": {}, "c": {}}, "per_turn": 2,
                       "playlist": [{"cam": "a", "sec": 10}, {"cam": "b", "sec": 10}, {"cam": "c", "sec": 10}]},
                      monkeypatch, clock)
    c = Canvas(448, 130)
    entry = {"panel": "webcam_full", "_play": 1}
    assert p.draw(c, (0, 0, 448, 130), entry, True) is None
    clock[0] += 10
    assert p.draw(c, (0, 0, 448, 130), entry, False) is None
    clock[0] += 10
    assert p.draw(c, (0, 0, 448, 130), entry, False) == "done"
    assert drawn == ["a", "b"]
    p.draw(c, (0, 0, 448, 130), {**entry, "_play": 2}, True)          # the next turn carries on with c
    assert drawn[-1] == "c"


def test_grid_reveals_one_square_at_a_time_in_reading_order():
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": str(i), "source": {}} for i in range(10)],
                                        "interval_ms": 200, "hold_sec": 1, "labels": False})
    sz, gx, gy = g.geometry((0, 0, 448, 130))
    assert sz == 64 and gy == 0
    tiles = [np.full((sz, sz, 3), i * 20 + 10, np.uint8) for i in range(10)]
    px = lambda c, k: int(c.buf[gy + (k // 4) * (sz + 1) + 5, gx + (k % 4) * (sz + 1) + 5, 0])
    c = Canvas(448, 130)
    assert g.draw_tiles(c, tiles, sz, gx, gy, 0.5) is None              # three in: top row, left to right
    assert [px(c, k) for k in range(4)] == [10, 30, 50, 0]
    c = Canvas(448, 130)
    g.draw_tiles(c, tiles, sz, gx, gy, 1.85)                             # ten in: 9 and 10 replaced the first two
    assert [px(c, k) for k in range(8)] == [170, 190, 50, 70, 90, 110, 130, 150]
    assert g.draw_tiles(Canvas(448, 130), tiles, sz, gx, gy, 2.9) is None    # holding
    assert g.draw_tiles(Canvas(448, 130), tiles, sz, gx, gy, 3.1) == "done"


def test_grid_tiles_survive_the_wire():
    a = np.arange(8 * 8 * 3, dtype=np.uint8).reshape(8, 8, 3)
    back = wm.unpack_grid(wm.pack_grid([a, None, a], 8))
    assert back[1] is None and np.array_equal(back[0], a) and np.array_equal(back[2], a)


def _cfg(webcams):
    return {"system": {}, "layout": {}, "modules": {"webcams": webcams}}


def test_list_grid_and_new_caption_settings_validate():
    import pytest

    from hub import config
    cam = {"label": "A", "source": {"type": "image", "url": "https://x.example/a.jpg"},
           "crops": {"tall": {"x": 0, "y": 0, "w": 1}},
           "overlay": {"text_align": "center", "box_w": "full", "margin": 4, "pad": 3, "font": "xlarge",
                       "style": "underline", "show": {"wind": True, "hilo": True}}}
    grid = {"cams": [{"name": "B", "source": {"type": "image", "url": "https://x.example/b.jpg"}}], "interval_ms": 200}
    config.validate(_cfg({"cams": {"a": cam}, "playlist": [{"cam": "a", "sec": 20, "on": True}], "per_turn": 2,
                          "grid": grid}))
    for bad in ({"playlist": [{"cam": "a", "sec": 1}]}, {"per_turn": 0},
                {"grid": {"cams": [{"source": {"type": "youtube", "url": "https://youtube.com/x"}}]}},
                {"grid": {**grid, "interval_ms": 10}}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg({"cams": {"a": cam}, **bad}))
    with pytest.raises(config.ConfigError):
        config.validate(_cfg({"cams": {"a": {**cam, "overlay": {"text_align": "middle"}}}}))


def test_weather_extras_in_the_caption():
    import time

    from display.modules.webcam_caption import DEFAULT_OVERLAY, Content
    ov = {**DEFAULT_OVERLAY, "show": {**DEFAULT_OVERLAY["show"], "weather": True, "conditions": True, "feels": True,
                                      "wind": True, "humidity": True, "hilo": True}}
    wx = {"temp": 8.4, "code": 2, "feels": 5.2, "humidity": 87, "wind": 18, "wind_dir": 300, "gust": 40,
          "high": 11.2, "low": 3.6, "at": time.time()}
    s = Content({"label": "A", "tz": "America/Vancouver"}, ov, wx=wx).sections
    assert (s["weather"], s["conditions"], s["feels"], s["wind"], s["humidity"], s["hilo"]) == \
        ("8°C", "Partly cloudy", "feels 5°", "NW 18 km/h gusts 40", "87% RH", "H 11° L 4°")
    assert Content({"label": "A"}, ov, wx={**wx, "wind": 0.4}).sections["wind"] == "calm"


def test_hub_grid_tiles_are_square_and_in_order(monkeypatch):
    from hub import app as hub_app
    cams = [{"name": str(i), "source": {"type": "image", "url": f"https://x.example/{i}.jpg"}} for i in range(3)]
    monkeypatch.setattr(hub_app, "_grid_cams", lambda: cams)
    frames = {f"https://x.example/{i}.jpg": np.full((360, 640, 3), 40 * i + 20, np.uint8) for i in (0, 2)}

    def still(src):
        return frames.get(src["url"])
    monkeypatch.setattr(hub_app, "_grid_still", still)
    tiles = wm.unpack_grid(hub_app.webcams_grid_bin(size=32).body)
    assert [t is None for t in tiles] == [False, True, False]
    assert tiles[0].shape == (32, 32, 3) and int(tiles[2][5, 5, 0]) == 100
