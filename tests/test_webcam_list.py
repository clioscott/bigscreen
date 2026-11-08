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
    p._draw_cam = lambda c, zone, entry, transition, **kw: drawn.append(entry["cam"])
    p._preroll = lambda cid, shape, zone: _Ready()                     # the next webcam's stream: has a picture
    p._warm_hub = lambda cid: None
    return p, drawn


class _Ready:
    black_since, error = None, ""

    def ready(self):
        return True

    def down(self, now=None):
        return False


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


def test_grid_fills_the_frame_edge_to_edge():
    g = wm.CameraGrid("http://hub:1", {"cams": []})
    for zone, cols in (((0, 0, 448, 130), 6), ((0, 0, 448, 175), 5), ((0, 0, 448, 192), 4), ((296, 45, 152, 130), 2)):
        tw, th, xs, ys = g.geometry(zone)
        x, y, w, h = zone
        assert len(xs) == cols and len(ys) == 2
        assert xs[0] == x and xs[-1] + tw == x + w and ys[0] == y and ys[-1] + th == y + h      # flush with every edge
        assert all(1 <= b - (a + tw) <= 2 for a, b in zip(xs, xs[1:]))                       # 1-2 px gaps
    tw, th, xs, ys = wm.CameraGrid("http://hub:1", {"rows": 3, "cols": 5}).geometry((0, 0, 448, 130))
    assert (len(xs), len(ys)) == (5, 3) and xs[-1] + tw == 448 and ys[-1] + th == 130


def test_grid_reveals_one_tile_at_a_time_in_reading_order():
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": str(i), "source": {}} for i in range(10)], "cols": 4,
                                        "interval_ms": 200, "hold_sec": 1, "labels": False})
    geo = tw, th, xs, ys = g.geometry((0, 0, 448, 130))
    tiles = [np.full((th, tw, 3), i * 20 + 10, np.uint8) for i in range(10)]
    px = lambda c, k: int(c.buf[ys[k // 4] + 5, xs[k % 4] + 5, 0])
    c = Canvas(448, 130)
    assert g.draw_tiles(c, tiles, geo, 0.5) is None                      # three in: top row, left to right
    assert [px(c, k) for k in range(4)] == [10, 30, 50, 0]
    c = Canvas(448, 130)
    g.draw_tiles(c, tiles, geo, 1.85)                                     # ten in: 9 and 10 replaced the first two
    assert [px(c, k) for k in range(8)] == [170, 190, 50, 70, 90, 110, 130, 150]
    assert g.draw_tiles(Canvas(448, 130), tiles, geo, 2.9) is None       # holding
    assert g.draw_tiles(Canvas(448, 130), tiles, geo, 3.1) == "done"


def test_grid_tiles_survive_the_wire():
    a = np.arange(6 * 8 * 3, dtype=np.uint8).reshape(6, 8, 3)
    back, names = wm.unpack_grid(wm.pack_grid([a, None, a], 8, 6, ["x", "y", "Rd & Ave"]))
    assert names == ["x", "y", "Rd & Ave"] and back[1] is None and np.array_equal(back[0], a) and np.array_equal(back[2], a)


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
                {"grid": {**grid, "interval_ms": 10}}, {"grid": {**grid, "rows": 5}}, {"grid": {**grid, "cols": 0}}):
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


def test_hub_grid_tiles_are_cropped_to_size_and_in_order(monkeypatch):
    from hub import app as hub_app
    cams = [{"name": str(i), "source": {"type": "image", "url": f"https://x.example/{i}.jpg"}} for i in range(3)]
    monkeypatch.setattr(hub_app.config, "load", lambda: {"modules": {"webcams": {"grid": {"cams": cams}}}})
    monkeypatch.setattr(hub_app, "_grid_ready", {})
    monkeypatch.setattr(hub_app, "_grid_cache", {})
    frames = {f"https://x.example/{i}.jpg": np.full((360, 640, 3), 40 * i + 20, np.uint8) for i in (0, 2)}

    def still(src):
        return frames.get(src["url"])
    monkeypatch.setattr(hub_app, "_grid_still", still)
    tiles, names = wm.unpack_grid(hub_app.webcams_grid_bin(w=40, h=32).body)
    assert names == ["0", "2"] and len(tiles) == 2                          # camera 1 is down: left out
    assert tiles[0].shape == (32, 40, 3) and int(tiles[1][5, 5, 0]) == 100


def test_both_halves_can_play_the_list_and_the_grid_at_once(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {k: {} for k in "abcd"}, "playlist": [{"cam": k, "sec": 10} for k in "abcd"]},
                      monkeypatch, clock)
    c = Canvas(448, 192)
    left, right = (0, 45, 295, 130), (296, 45, 152, 130)
    for _ in range(3):                                     # both sides drawn every frame: neither resets the other
        p.draw(c, left, {"panel": "webcam", "_play": 1}, False)
        p.draw(c, right, {"panel": "webcam", "_play": 1}, False)
    assert drawn == ["a", "b"] * 3
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": str(i), "source": {}} for i in range(2)], "interval_ms": 200, "hold_sec": 0})
    for z in (left, right):
        tw, th = g.geometry(z)[:2]
        g.tiles[(tw, th)] = {"at": clock[0], "batch": ([np.ones((th, tw, 3), np.uint8)] * 2, None), "busy": False}
    assert g.draw(c, left, {"_play": 1}, True) is None and g.draw(c, right, {"_play": 1}, True) is None
    clock[0] += 0.5
    assert g.draw(c, left, {"_play": 1}, False) == "done" and g.draw(c, right, {"_play": 1}, False) == "done"


def test_another_pass_replays(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {"a": {}, "b": {}}, "playlist": [{"cam": "a", "sec": 10}, {"cam": "b", "sec": 10}]},
                      monkeypatch, clock)
    c, z, e = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    p.draw(c, z, e, True)
    clock[0] += 10
    assert p.draw(c, z, e, False) == "done"
    assert p.draw(c, z, e, False) is None and drawn[-1] == "b"       # the same play, a second pass: the next webcam
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": str(i), "source": {}} for i in range(2)], "interval_ms": 200, "hold_sec": 0})
    tw, th = g.geometry(z)[:2]
    g.tiles[(tw, th)] = {"at": clock[0], "batch": ([np.ones((th, tw, 3), np.uint8)] * 2, None), "busy": False}
    g.draw(c, z, e, True)
    clock[0] += 0.5
    assert g.draw(c, z, e, False) == "done"
    assert g.draw(c, z, e, False) is None                            # the reveal starts over


def test_camera_names_split_into_streets_and_direction():
    from display.modules import cam_names as cn
    cases = {
        "Cambie St and W 30th Av - North": (["Cambie St", "W 30th Ave"], "N"),
        "No. 4 Road at Westminster Highway - Eastbound": (["No. 4 Rd", "Westminster Hwy"], "EB"),
        "26 Ave & King George Blvd - Camera 2": (["26 Ave", "King George Blvd"], "Cam 2"),
        "26 Ave & King George Blvd - Camera 1": (["26 Ave", "King George Blvd"], ""),
        "200 St & 72 Ave - looking north": (["200 St", "72 Ave"], "N"),
        "Hazelbridge Way at Cambie Road - Southbound Southbound": (["Hazelbridge Way", "Cambie Rd"], "SB"),
        "Massey Tunnel · looking south": (["Massey Tunnel"], "S"),
        "Gaglardi Way intersection": (["Gaglardi Way intersection"], ""),
    }
    for name, (lines, tag) in cases.items():
        assert cn.label_lines(name) == lines and cn.tag_text(name) == tag, name
    full = "No. 4 Road at Westminster Highway - Eastbound"
    assert cn.label_lines(full, [["street1", "street2"], ["dir"]], short=False, dir_style="full") == \
        ["No. 4 Road Westminster Highway", "Eastbound"]
    assert cn.tag_text("200 St & 72 Ave - looking north", "full") == "Looking north"


def test_grid_labels_follow_the_settings():
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": "No. 4 Road at Westminster Highway - Eastbound", "source": {}}]})
    assert g._labels_for(73) == [(["No. 4 Rd", "Westminster Hwy"], "EB")]
    g = wm.CameraGrid("http://hub:1", {"cams": g.cams, "label": {"lines": [["street1", "dir"]], "dir": "line"}})
    assert g._labels_for(73) == [(["No. 4 Rd EB"], "")]
    g = wm.CameraGrid("http://hub:1", {"cams": g.cams, "label": {"dir": "off"}})
    assert g._labels_for(73) == [(["No. 4 Rd", "Westminster Hwy"], "")]
    c = Canvas(448, 130)                                   # drawn: names at the bottom, the tag top right
    g = wm.CameraGrid("http://hub:1", {"cams": g.cams})
    g._label(c, 0, 0, 73, 64, g._labels_for(73)[0])
    assert c.buf[:7, 60:73].any() and not c.buf[:7, :40].any() and c.buf[50:64, :40].any()


def test_hub_crops_the_cameras_the_wall_sends(monkeypatch):
    from hub import app as hub_app
    monkeypatch.setattr(hub_app, "_grid_still", lambda src: np.full((90, 160, 3), 50, np.uint8))
    monkeypatch.setattr(hub_app, "_grid_ready", {})
    monkeypatch.setattr(hub_app, "_grid_cache", {})
    cams = [{"name": "A", "source": {"type": "image", "url": "https://x.example/a.jpg"}},
            {"name": "Y", "source": {"type": "youtube", "url": "https://www.youtube.com/watch?v=x"}},   # not a still: left out
            {"name": "B", "source": {"type": "image", "url": "https://x.example/b.jpg"}}]
    tiles, names = wm.unpack_grid(hub_app.webcams_grid_bin_for({"w": 40, "h": 30, "grid": {"cams": cams}}).body)
    assert names == ["A", "B"] and tiles[0].shape == (30, 40, 3)


def test_nearby_cameras_join_the_grid_nearest_first(monkeypatch):
    from hub import app as hub_app
    city = lambda name, lat, lon: {"name": name, "lat": lat, "lon": lon, "credit": "City of Richmond",
                                   "source": {"type": "image", "url": f"https://x.example/{name}.jpg"}}
    monkeypatch.setattr(hub_app, "_city_lists", lambda: {"Richmond": [
        city("far", 49.20, -123.10), city("near", 49.1667, -123.1336), city("nearer", 49.1666, -123.1337)]})
    near = {"on": True, "lat": 49.1666, "lon": -123.1336, "radius": 1, "unit": "km"}
    picked = {"name": "picked", "source": {"type": "image", "url": "https://x.example/picked.jpg"}}
    got = hub_app._grid_list({"cams": [picked, {**picked}], "near": near})
    assert [c["name"] for c in got] == ["picked", "picked", "nearer", "near"]                  # far is ~4 km away
    assert [c["name"] for c in hub_app._grid_list({"cams": [city("near", 49.1667, -123.1336)], "near": near})] ==         ["near", "nearer"]                                                                  # not added twice
    assert len(hub_app._grid_list({"near": {**near, "radius": 3, "unit": "mi"}})) == 3
    assert hub_app._grid_list({"near": {**near, "on": False}}) == []


def test_down_cameras_are_skipped_not_shown_empty():
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": str(i), "source": {}} for i in range(3)], "cols": 3,
                                        "rows": 1, "interval_ms": 200, "hold_sec": 0, "labels": False})
    geo = tw, th, xs, ys = g.geometry((0, 0, 300, 100))
    tiles = [np.full((th, tw, 3), 10, np.uint8), None, np.full((th, tw, 3), 30, np.uint8)]
    c = Canvas(300, 100)
    g.draw_tiles(c, tiles, geo, 0.25)                                     # two in: cameras 1 and 3 (2 is down)
    assert [int(c.buf[5, x + 5, 0]) for x in xs] == [10, 30, 0]
    assert g.draw_tiles(Canvas(300, 100), tiles, geo, 0.45) == "done"
    assert g.draw_tiles(Canvas(300, 100), [None, None, None], geo, 0) == "skip"


def test_grid_fetches_its_tiles_before_its_turn(monkeypatch):
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": "a", "source": {"type": "image", "url": "https://x/a.jpg"}}]})
    asked = []
    monkeypatch.setattr(g, "_load", asked.append)
    g.warm((0, 0, 448, 175))                                              # above the clock: 5 columns of 2 rows
    assert asked == [g.geometry((0, 0, 448, 175))[:2]]


def test_a_camera_whose_page_fails_stays_listed_for_a_week(monkeypatch):
    from hub import app as hub_app
    now = 1_800_000_000.0
    monkeypatch.setattr(hub_app.time, "time", lambda: now)
    old = [{"id": "a", "seen": now - 3600}, {"id": "b", "seen": now - 8 * 86400}, {"id": "c"}]
    got = hub_app._keep_recent([{"id": "d"}], old, old_at=now - 86400)
    assert [v["id"] for v in got] == ["d", "a", "c"] and got[0]["seen"] == now      # b: gone more than a week


def test_black_blank_and_frozen_stills_are_left_out():
    from hub import app as hub_app
    rng = np.random.default_rng(1)
    street = rng.integers(0, 120, (90, 160, 3)).astype(np.uint8)            # a dark night street, with detail
    assert hub_app.grid_usable(street, taken=1000.0, now=1000.0 + 600)
    assert not hub_app.grid_usable(np.zeros((90, 160, 3), np.uint8))                     # black
    assert not hub_app.grid_usable(np.full((90, 160, 3), 128, np.uint8))                 # one flat grey card
    assert not hub_app.grid_usable(street, taken=1000.0, now=1000.0 + 3 * 3600)          # frozen for hours
    assert not hub_app.grid_usable(None)


def test_the_hub_answers_from_ready_tiles(monkeypatch):
    from hub import app as hub_app
    monkeypatch.setattr(hub_app, "_grid_ready", {})
    built = []
    monkeypatch.setattr(hub_app, "_grid_build", lambda cams, w, h: built.append(1) or b"tiles%d" % len(built))
    monkeypatch.setattr(hub_app.threading, "Thread", lambda target, **k: type("T", (), {"start": lambda s: target()})())
    clock = [1000.0]
    monkeypatch.setattr(hub_app.time, "time", lambda: clock[0])
    g = {"cams": [{"name": "a", "source": {"type": "image", "url": "https://x.example/a.jpg"}}]}
    assert hub_app._grid_reply(g, 40, 30).body == b"tiles1"                  # first time: built now
    clock[0] += 10
    assert hub_app._grid_reply(g, 40, 30).body == b"tiles1" and len(built) == 1    # fresh: no rebuild
    clock[0] += 60
    assert hub_app._grid_reply(g, 40, 30).body == b"tiles1" and len(built) == 2    # served, rebuilt behind
    assert hub_app._grid_reply(g, 40, 30).body == b"tiles2"
    clock[0] += 600
    assert hub_app._grid_reply(g, 40, 30).body == b"tiles3"                  # too old to show: built first


def test_cameras_that_do_not_answer_in_time_are_left_out(monkeypatch):
    import time as _t

    from hub import app as hub_app
    ok = np.full((90, 160, 3), 60, np.uint8)
    monkeypatch.setattr(hub_app, "_grid_cache", {})
    monkeypatch.setattr(hub_app, "_grid_still", lambda src: (_t.sleep(1.0), ok)[1] if src["url"].endswith("slow.jpg") else ok)
    cams = [{"source": {"type": "image", "url": f"https://x.example/{n}.jpg"}} for n in ("a", "slow", "b")]
    t0 = _t.time()
    tiles = hub_app._grid_tiles(cams, 40, 30, budget=0.3)
    assert [t is None for t in tiles] == [False, True, False] and _t.time() - t0 < 0.9


def test_the_hub_keeps_tiles_not_stills_and_a_hiccup_keeps_the_last_one(monkeypatch):
    from hub import app as hub_app
    monkeypatch.setattr(hub_app, "_grid_cache", {})
    clock, fetched, fail = [1000.0], [], [False]
    monkeypatch.setattr(hub_app.time, "time", lambda: clock[0])

    def still(src):
        fetched.append(src["url"])
        if fail[0]:
            raise OSError("timed out")
        return np.full((360, 640, 3), 70, np.uint8)
    monkeypatch.setattr(hub_app, "_grid_still", still)
    cam = {"source": {"type": "image", "url": "https://x.example/a.jpg"}}
    assert hub_app._grid_tile(cam, 40, 30).shape == (30, 40, 3)
    assert hub_app._grid_tile(cam, 40, 30) is not None and len(fetched) == 1          # cached: not fetched again
    assert all(v[1] is None or v[1].shape == (30, 40, 3) for v in hub_app._grid_cache.values())
    clock[0] += 61
    fail[0] = True
    assert hub_app._grid_tile(cam, 40, 30) is not None and len(fetched) == 2          # failed: the last picture


def test_the_grid_has_room_for_every_city_camera_and_waits_long_enough_for_them():
    from hub import app as hub_app
    cams = [{"name": str(i), "source": {"type": "image", "url": f"https://x.example/{i}.jpg"}} for i in range(2500)]
    assert len(hub_app._grid_list({"cams": cams})) == 2500
    assert hub_app._grid_budget(100) == 10 and hub_app._grid_budget(2300) == 46 and hub_app._grid_budget(9000) == 60


def test_a_full_tile_cache_drops_its_oldest_not_everything(monkeypatch):
    from hub import app as hub_app
    monkeypatch.setattr(hub_app, "_grid_cache", {})
    monkeypatch.setattr(hub_app, "GRID_CACHE_MAX", 8)
    clock = [1000.0]
    monkeypatch.setattr(hub_app.time, "time", lambda: clock[0])
    monkeypatch.setattr(hub_app, "_grid_still", lambda src: np.full((90, 160, 3), 60, np.uint8))
    cam = lambda i: {"source": {"type": "image", "url": f"https://x.example/{i}.jpg"}}
    for i in range(9):
        clock[0] += 1
        hub_app._grid_tile(cam(i), 40, 30)
    assert len(hub_app._grid_cache) == 7                              # 8 full: the 2 oldest went, the 9th came in
    assert hub_app._grid_key(cam(0), 40, 30) not in hub_app._grid_cache and hub_app._grid_key(cam(8), 40, 30) in hub_app._grid_cache


def test_a_refresh_mid_reveal_does_not_cut_the_grid_short(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(wm.time, "time", lambda: clock[0])
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": str(i), "source": {}} for i in range(10)], "interval_ms": 200, "hold_sec": 0})
    monkeypatch.setattr(g, "_load", lambda size: None)
    c, z = Canvas(448, 130), (0, 0, 448, 130)
    tw, th = g.geometry(z)[:2]
    g.tiles[(tw, th)] = {"at": clock[0], "batch": ([np.ones((th, tw, 3), np.uint8)] * 10, None), "busy": False}
    assert g.draw(c, z, {"_play": 1}, True) is None
    clock[0] += 0.5
    g.tiles[(tw, th)] = {"at": clock[0], "batch": ([np.ones((th, tw, 3), np.uint8)] * 2, None), "busy": False}  # 8 went down
    clock[0] += 0.5
    assert g.draw(c, z, {"_play": 1}, False) is None                  # still the first batch: 10 cameras, 2 s
    clock[0] += 1.1
    assert g.draw(c, z, {"_play": 1}, False) == "done"


def test_a_camera_late_for_a_build_shows_its_recent_tile_and_is_not_fetched_twice(monkeypatch):
    import threading as _th
    import time as _t

    from hub import app as hub_app
    monkeypatch.setattr(hub_app, "_grid_cache", {})
    monkeypatch.setattr(hub_app, "_grid_fetching", set())
    ok, gate, calls = np.full((90, 160, 3), 60, np.uint8), _th.Event(), []

    def still(src):
        calls.append(src["url"])
        if src["url"].endswith("slow.jpg"):
            gate.wait(2)
        return ok
    monkeypatch.setattr(hub_app, "_grid_still", still)
    cams = [{"source": {"type": "image", "url": f"https://x.example/{n}.jpg"}} for n in ("a", "slow")]
    slow = cams[1]
    hub_app._grid_cache[hub_app._grid_key(slow, 40, 30)] = (_t.time() - 120, np.full((30, 40, 3), 9, np.uint8))
    tiles = hub_app._grid_tiles(cams, 40, 30, budget=0.2)
    assert int(tiles[1][0, 0, 0]) == 9                                # late: its tile from two minutes ago
    assert hub_app._grid_tile(slow, 40, 30)[0, 0, 0] == 9 and calls.count("https://x.example/slow.jpg") == 1
    gate.set()                                                        # (a second build waits on the first fetch)


def test_ready_replies_are_kept_within_a_size_budget(monkeypatch):
    from hub import app as hub_app
    monkeypatch.setattr(hub_app, "_grid_ready", {})
    monkeypatch.setattr(hub_app, "GRID_READY_BYTES", 25)
    clock = [1000.0]
    monkeypatch.setattr(hub_app.time, "time", lambda: clock[0])
    for k in "abc":
        clock[0] += 1
        hub_app._grid_keep(k, b"x" * 10)
    assert sorted(hub_app._grid_ready) == ["b", "c"]                  # the oldest went
    hub_app._grid_keep("big", b"x" * 100)
    assert list(hub_app._grid_ready) == ["big"]                       # the newest always stays


def test_grid_labels_follow_a_new_list_even_at_a_reused_address():
    g = wm.CameraGrid("http://hub:1", {"cams": []})
    a = ["Main St & 1 Ave"]
    first = g._labels_for(73, a)
    key = next(iter(g._labels))
    g._labels[key] = (["something else"], first)                      # as if a's id now belonged to another list
    assert g._labels_for(73, a) == first and g._labels[key][0] is a   # worked out again for the real list


def test_an_intersections_views_share_one_place():
    from display.modules.cam_names import place_key
    k = place_key("Alderbridge Way at Lansdowne Road - Eastbound")
    assert k == place_key("Alderbridge Way at Lansdowne Road - SB2") == place_key("Lansdowne Rd & Alderbridge Way - Camera 2")
    assert k != place_key("Alderbridge Way at Westminster Highway - Eastbound")
    assert place_key("Massey Tunnel · looking south") == place_key("Massey Tunnel · looking north")   # one street
    assert place_key("Cambie Bridge - North") != place_key("Burrard St Bridge - North")
    assert place_key("Granville St.and W 41st Av - North") == place_key("Granville St and W 41st Av - South")
    assert place_key("8100 Block at Cook Road - Eastbound") == place_key("8100 blk at Cook Rd - SB2")
    assert place_key("Holland Commons & King George Blvd - Camera 1") == place_key("Holland Cmns & King George Blvd - Panorama")


def test_a_grouped_tile_loops_through_its_views():
    names = ["A St at B Rd - Northbound", "C St at D Rd - Eastbound", "A St at B Rd - Southbound",
             "E St at F Rd - Westbound", "E St at F Rd - Eastbound"]
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": n, "source": {}} for n in names], "cols": 5, "rows": 2,
                                        "interval_ms": 200, "view_ms": 1000, "hold_sec": 0, "labels": False})
    geo = tw, th, xs, ys = g.geometry((0, 0, 300, 100))           # 10 tiles: each lives 2 s, time for 2 views of 1 s
    tiles = [np.full((th, tw, 3), 10 * (i + 1), np.uint8) for i in range(5)]
    at = lambda t: (lambda c: (g.draw_tiles(c, tiles, geo, t), [int(c.buf[5, x + 5, 0]) for x in xs[:3]])[1])(Canvas(300, 100))
    assert at(0.5) == [10, 20, 40]                       # three places, each on its first view
    assert at(1.1) == [30, 20, 40]                       # A's tile has moved to its next view (C has only one)
    assert at(1.3) == [30, 20, 40] and at(1.5) == [30, 20, 50]   # E landed at 0.4 s: its second view from 1.4 s
    assert at(2.1) == [10, 20, 50]                       # A back to its first view
    assert g.draw_tiles(Canvas(300, 100), tiles, geo, 2.3) is None      # E hasn't shown both views yet (till 2.4 s)
    assert g.draw_tiles(Canvas(300, 100), tiles, geo, 2.5) == "done"
    flat = wm.CameraGrid("http://hub:1", {"cams": g.cams, "group_views": False, "cols": 3, "rows": 1, "hold_sec": 0})
    assert len(flat._places(tiles, flat.names, tw, th)) == 5


def test_every_view_shows_before_its_tile_is_taken_over_and_the_last_screen_finishes_its_loops():
    names = ["A St at B Rd - Northbound", "A St at B Rd - Southbound", "A St at B Rd - Eastbound", "A St at B Rd - Westbound",
             "C St at D Rd - North", "E St at F Rd - North", "G St at H Rd - North"]
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": n, "source": {}} for n in names], "cols": 2, "rows": 1,
                                        "interval_ms": 200, "view_ms": 1000, "hold_sec": 0, "labels": False})
    geo = tw, th, xs, ys = g.geometry((0, 0, 200, 100))
    tiles = [np.full((th, tw, 3), 10 * (i + 1), np.uint8) for i in range(7)]
    seen = set()
    for f in range(0, 80):                               # A's tile lives 2 × 200 ms: its 4 views get 100 ms each
        c = Canvas(200, 100)
        g.draw_tiles(c, tiles, geo, f / 200)
        seen.add(int(c.buf[5, xs[0] + 5, 0])) if f / 200 < 0.4 else None
    assert {10, 20, 30, 40} <= seen
    late = wm.CameraGrid("http://hub:1", {"cams": [{"name": n, "source": {}} for n in names[:5]], "cols": 4, "rows": 1,
                                           "interval_ms": 200, "view_ms": 1000, "hold_sec": 0, "labels": False})
    geo = late.geometry((0, 0, 400, 100))                # A (4 views, 200 ms each in a tile that lives 800 ms), then C
    t4 = [np.full((geo[1], geo[0], 3), 9, np.uint8)] * 5
    assert late.draw_tiles(Canvas(400, 100), t4, geo, 0.7) is None      # both in at 0.4 s, A still looping (till 0.8 s)
    assert late.draw_tiles(Canvas(400, 100), t4, geo, 0.85) == "done"


class _Slow(_Ready):
    """A prerolled stream that isn't ready yet (frames, but late)."""
    frame = np.zeros((1, 1, 3), np.uint8)

    def __init__(self):
        self.has = False

    def ready(self):
        return self.has


def test_the_current_webcam_stays_up_until_the_next_has_a_picture(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {"a": {}, "b": {}}, "per_turn": 2,
                       "playlist": [{"cam": "a", "sec": 20}, {"cam": "b", "sec": 20}]}, monkeypatch, clock)
    slow, warmed = _Slow(), []
    p._preroll = lambda cid, shape, zone: slow
    p._warm_hub = lambda cid: warmed.append(cid)
    c, z, entry = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    p.draw(c, z, entry, True)
    clock[0] += 6                                                       # 14 s to go: the hub has started b's feed
    p.draw(c, z, entry, False)
    assert set(warmed) == {"b"}
    clock[0] += 16                                                      # due, but b has no picture yet: a stays
    p.draw(c, z, entry, False)
    assert drawn[-1] == "a"
    slow.has = True                                                     # b's frames are in: cut to it
    p.draw(c, z, entry, False)
    assert drawn[-1] == "b"


def test_the_next_webcam_comes_up_after_a_while_even_without_a_picture(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {"a": {}, "b": {}}, "per_turn": 2,
                       "playlist": [{"cam": "a", "sec": 20}, {"cam": "b", "sec": 20}]}, monkeypatch, clock)
    p._preroll = lambda cid, shape, zone: _Slow()
    c, z, entry = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    p.draw(c, z, entry, True)
    clock[0] += 20
    p.draw(c, z, entry, False)                                          # due: b has frames but isn't ready
    assert drawn[-1] == "a"
    clock[0] += wm.HOLD_MAX_SEC
    p.draw(c, z, entry, False)
    assert drawn[-1] == "b"


def test_webcams_the_hub_found_down_are_skipped(monkeypatch, tmp_path):
    import json
    clock = [1000.0]
    (tmp_path / "webcam_health.json").write_text(json.dumps({"b": {"up": False, "why": "black", "since": 900}}))
    p, drawn = _panel({"cams": {k: {} for k in "abc"}, "per_turn": 2,
                       "playlist": [{"cam": k, "sec": 10} for k in "abc"]}, monkeypatch, clock)
    p.data_dir = str(tmp_path)
    c, z, entry = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    p.draw(c, z, entry, True)
    clock[0] += 10
    p.draw(c, z, entry, False)
    assert drawn == ["a", "c"]
    assert wm._list_pos["i"] == 2


def test_a_prerolled_webcam_that_is_black_is_passed_over(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {k: {} for k in "abc"}, "per_turn": 3,
                       "playlist": [{"cam": k, "sec": 10} for k in "abc"]}, monkeypatch, clock)

    class Black(_Ready):
        black_since = 0.0

        def ready(self):
            return False

        def down(self, now=None):
            return True
    p._preroll = lambda cid, shape, zone: Black() if cid == "b" else _Ready()
    c, z, entry = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    p.draw(c, z, entry, True)
    clock[0] += 10
    p.draw(c, z, entry, False)
    assert drawn == ["a", "c"] and p.is_down("b")


def test_a_stream_that_goes_black_is_down():
    from hub import webcams
    night = np.zeros((90, 160, 3), np.uint8)
    night[40:44, ::8] = 230                                             # a dark street with its lights on
    assert not wm.is_black(night) and not webcams.is_black(night)
    assert wm.is_black(np.full((90, 160, 3), 12, np.uint8)) and webcams.is_black(np.zeros((90, 160, 3), np.uint8))


def test_health_job_remembers_since_when_a_webcam_is_down(monkeypatch):
    from hub.jobs import webcam_health as job
    first = job.update({}, {"a": (True, ""), "b": (False, "black")}, 100.0)
    assert first["b"] == {"up": False, "why": "black", "since": 100.0, "checked": 100.0}
    later = job.update(first, {"a": (True, ""), "b": (False, "no picture")}, 400.0)
    assert later["b"]["since"] == 100.0 and later["a"]["since"] == 100.0 and later["b"]["checked"] == 400.0
    back = job.update(later, {"a": (True, ""), "b": (True, "")}, 700.0)
    assert back["b"] == {"up": True, "why": "", "since": 700.0, "checked": 700.0}

    from hub import webcams
    monkeypatch.setattr(webcams, "grab_frame", lambda kind, url, timeout=None: np.zeros((20, 20, 3), np.uint8))
    assert job.check({"source": {"type": "stream", "url": "https://x.example/live.m3u8"}}) == (False, "black")
    assert job.check({"source": {"type": "youtube", "url": "https://youtu.be/x"}}) is None   # never looked up here
    assert job.check({"source": {"type": "nope"}})[0] is False


def test_caption_time_follows_the_location_when_the_webcam_has_no_zone():
    import time

    from display.modules.webcam_caption import DEFAULT_OVERLAY, Content
    wx = {"temp": 3, "code": 0, "tz": "Asia/Tokyo", "at": time.time()}
    ct = Content({"label": "A"}, DEFAULT_OVERLAY, wx=wx)
    assert str(ct.tz) == "Asia/Tokyo" and ct.sections["weather"] == "3°C" and ct.icon is None   # just the temperature
    assert Content({"label": "A"}, {**DEFAULT_OVERLAY, "temp_icon": True}, wx=wx).icon == "sun"      # icon: an option
    assert DEFAULT_OVERLAY["corner"] == "bl" and DEFAULT_OVERLAY["separator"] == "space"


def test_a_turn_still_ends_when_only_the_webcam_on_screen_is_up(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {k: {} for k in "abc"}, "per_turn": 3,
                       "playlist": [{"cam": k, "sec": 10} for k in "abc"]}, monkeypatch, clock)
    p.health = {"b": {"up": False}, "c": {"up": False}}
    p._health = lambda now: p.health
    c, z, entry = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    results = [p.draw(c, z, entry, True)]
    for _ in range(4):
        clock[0] += 10
        results.append(p.draw(c, z, entry, False))
    assert "done" in results and set(drawn) == {"a"}                    # a's three slots, then the rotation moves on


def test_the_two_halves_never_line_up_the_same_webcam(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {k: {} for k in "abcd"}, "per_turn": 4,
                       "playlist": [{"cam": k, "sec": 10} for k in "abcd"]}, monkeypatch, clock)
    left, right = (0, 45, 295, 130), (296, 45, 152, 130)
    c, entry = Canvas(448, 192), {"panel": "webcam", "_play": 1}
    p.draw(c, left, entry, True)
    p.draw(c, right, entry, True)
    for _ in range(4):
        (l, r) = p._turns[left], p._turns[right]
        assert l["cam"] != r["cam"]                                     # never both on one webcam
        assert l["next"] is None or l["next"] != r["cam"]               # nor lining up what the other shows
        assert r["next"] is None or r["next"] not in (l["cam"], l["next"])
        clock[0] += 10
        p.draw(c, left, entry, False)
        p.draw(c, right, entry, False)


def test_a_next_webcam_that_never_sends_a_picture_is_passed_over(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {k: {} for k in "abc"}, "per_turn": 3,
                       "playlist": [{"cam": k, "sec": 10} for k in "abc"]}, monkeypatch, clock)

    class Dead(_Slow):
        frame = None
    p._preroll = lambda cid, shape, zone: Dead() if cid == "b" else _Ready()
    c, z, entry = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    p.draw(c, z, entry, True)
    clock[0] += 10
    p.draw(c, z, entry, False)                                          # b is due but has nothing: a stays
    clock[0] += wm.HOLD_MAX_SEC
    p.draw(c, z, entry, False)                                          # b passed over, still on a
    p.draw(c, z, entry, False)                                          # c has a picture: on to it
    assert drawn[-1] == "c" and "b" not in drawn and p.is_down("b")


def test_health_job_writes_what_it_checked_in_time(monkeypatch):
    import time as _t

    from hub.jobs import webcam_health as job
    monkeypatch.setattr(job, "check", lambda cam: (_t.sleep(cam["s"]), (True, ""))[1])
    got = job.check_all({"fast": {"s": 0}, "slow": {"s": 5}}, budget=0.5)
    assert got == {"fast": (True, "")}
    kept = job.update({"slow": {"up": False, "since": 1}, "gone": {"up": True}}, got, 10.0, keep={"fast", "slow"})
    assert kept["slow"] == {"up": False, "since": 1} and "gone" not in kept and kept["fast"]["up"]


def test_captions_draw_japanese_with_an_approved_font_of_their_size():
    from display.gfx import graphics
    from display.modules import webcam_caption as cap
    for size, jp in (("small", "jp-misaki_gothic"), ("medium", "jp-mplus10r"), ("large", "jp-kaname12b"),
                     ("xlarge", "jp-shnm16b")):
        f = graphics.Font()
        f.LoadFont(cap.FONTS[size])
        assert f.CharacterWidth(0x6E0B) == -1                               # 渋: the Latin font lacks it
        cap.with_japanese(f)
        assert f.CharacterWidth(0x6E0B) > 0 and f.fallback is cap._jp_fonts[next(p for p in cap._jp_fonts if jp in p)]
        assert cap._tw(f, "渋谷 Tokyo") > cap._tw(f, " Tokyo")
    plain = graphics.Font()
    plain.LoadFont(cap.FONTS["small"])
    assert plain.fallback is None                                           # other screens' fonts are untouched
    img = wm.render_preview({"label": "渋谷スクランブル交差点", "overlay": {"show": {"place": False, "time": False}}},
                            np.zeros((130, 448, 3), np.uint8), (448, 130), elapsed=3)
    box = img[100:130, :200]
    assert (box.max(axis=-1) > 200).sum() > 150                             # the kanji and kana are drawn


def test_japanese_text_size_changes_only_the_japanese():
    from display.gfx import graphics
    from display.modules import webcam_caption as cap
    small = graphics.Font()
    small.LoadFont(cap.FONTS["small"])
    cap.with_japanese(small)
    big = cap.jp_sized(small, 20)                                         # Japanese as tall as 10x20
    assert big.glyphs is small.glyphs and "kappa20b" in next(p for p, f in cap._jp_fonts.items() if f is big.fallback)
    assert big.height >= 20 and big.CharacterWidth(ord("K")) == small.CharacterWidth(ord("K"))
    assert cap.jp_sized(small, 20) is big and cap.jp_sized(small, 8) is small    # cached; same size: the font itself
    frame = np.zeros((130, 448, 3), np.uint8)
    latin = {"label": "Kabukicho Ichibangai", "place": "Tokyo, Japan", "tz": "Asia/Tokyo"}
    for style in ("tag", "postcard", "custom"):
        cap._memo.clear(), cap._layers.clear()
        a = wm.render_preview({**latin, "overlay": {"style": style}}, frame, (448, 130), elapsed=3)
        cap._memo.clear(), cap._layers.clear()
        b = wm.render_preview({**latin, "overlay": {"style": style, "jp_font": "xlarge"}}, frame, (448, 130), elapsed=3)
        assert (a == b).all()                                            # no Japanese: nothing changes
    jp = {**latin, "label": "Kabukicho 歌舞伎町"}
    cap._memo.clear(), cap._layers.clear()
    a = wm.render_preview({**jp, "overlay": {}}, frame, (448, 130), elapsed=3)
    cap._memo.clear(), cap._layers.clear()
    b = wm.render_preview({**jp, "overlay": {"jp_font": "xlarge"}}, frame, (448, 130), elapsed=3)
    rows = lambda img: np.flatnonzero(img.max(axis=(1, 2)) > 0)
    assert len(rows(b)) > len(rows(a))                                   # the caption grew to fit the bigger kanji


def test_japanese_text_size_validates():
    import pytest

    from hub import config
    cam = {"label": "A", "source": {"type": "image", "url": "https://x.example/a.jpg"}, "overlay": {"jp_font": "large"}}
    config.validate(_cfg({"cams": {"a": cam}}))
    with pytest.raises(config.ConfigError):
        config.validate(_cfg({"cams": {"a": {**cam, "overlay": {"jp_font": "huge"}}}}))


def test_weather_icon_beside_the_caption_box():
    import time

    from display.modules import webcam_caption as cap
    wx = {"temp": 9, "code": 61, "is_day": 1, "tz": "Asia/Tokyo", "at": time.time()}
    frame = np.full((130, 448, 3), 90, np.uint8)
    cam = {"label": "Kabukicho", "place": "Tokyo, Japan"}

    def render(**ov):
        cap._memo.clear(), cap._layers.clear()
        return wm.render_preview({**cam, "overlay": ov}, frame, (448, 130), elapsed=3, wx=wx)
    plain, beside = render(), render(wx_beside=True)
    diff = np.flatnonzero((plain != beside).any(axis=(0, 2)))
    box_right = np.flatnonzero((plain != frame).any(axis=(0, 2))).max()
    assert diff.min() > box_right + 1 and diff.max() - diff.min() <= 16      # icon + outline, a gap right of the box
    assert ((beside[:, diff] == 0).all(axis=-1)).any()                        # the black outline
    right = render(wx_beside=True, corner="br")
    box_left = np.flatnonzero((render(corner="br") != frame).any(axis=(0, 2))).min()
    assert np.flatnonzero((render(corner="br") != right).any(axis=(0, 2))).max() < box_left   # caption on the right: icon on its left
    assert cap.Content(cam, cap.overlay_of({"overlay": {"wx_beside": True}})).side_icon is None   # no weather: no icon


def test_a_chosen_japanese_font_and_its_sizes():
    import os

    from display.modules import webcam_caption as cap
    for key, (name, sizes) in cap.JP_FACES.items():
        assert sizes == sorted(sizes) and all(os.path.exists(os.path.join("assets", p)) for _, p in sizes), key
    assert cap._jp_path(12, "shnm").endswith("shnm12.bdf") and cap._jp_path(13, "shnm").endswith("shnm14.bdf") and cap._jp_path(16, "shnm").endswith("shnm16.bdf")
    assert cap._jp_path(8, "ayu").endswith("ayu18.bdf")                       # too small for the family: its smallest
    frame = np.zeros((130, 448, 3), np.uint8)
    cam = {"label": "Kabukicho 歌舞伎町"}
    cap._memo.clear(), cap._layers.clear()
    a = wm.render_preview({**cam, "overlay": {}}, frame, (448, 130), elapsed=3)
    cap._memo.clear(), cap._layers.clear()
    b = wm.render_preview({**cam, "overlay": {"jp_face": "shnm_maru"}}, frame, (448, 130), elapsed=3)   # (Fusion 8 is Misaki)
    assert not (a == b).all()


def test_a_name_on_several_lines():
    from display.modules import webcam_caption as cap
    cam = {"label": "Kabukicho Ichibangai\n 歌舞伎町一番街 \n\n", "place": "Tokyo"}
    ct = cap.Content(cam, cap.overlay_of({}))
    assert ct.title == "Kabukicho Ichibangai" and ct.name_more == ["歌舞伎町一番街"] and cap.has_cjk(ct)
    assert ct.bits()[0] == ("name2", "歌舞伎町一番街")
    one = cap.Content(cam, cap.overlay_of({"overlay": {"headline": "place"}}))          # the name as a detail
    assert one.name_more == [] and one.sections["name"] == "Kabukicho Ichibangai 歌舞伎町一番街"
    frame = np.zeros((130, 448, 3), np.uint8)
    rows = lambda img: np.flatnonzero(img.max(axis=(1, 2)) > 0)
    cap._memo.clear(), cap._layers.clear()
    two = wm.render_preview(cam, frame, (448, 130), elapsed=3)
    cap._memo.clear(), cap._layers.clear()
    single = wm.render_preview({**cam, "label": "Kabukicho Ichibangai"}, frame, (448, 130), elapsed=3)
    assert len(rows(two)) > len(rows(single))                            # the box grew by the second line


def test_the_box_can_grow_round_the_weather_icon():
    import time

    from display.modules import webcam_caption as cap
    wx = {"temp": 9, "code": 61, "is_day": 1, "at": time.time()}
    frame = np.full((130, 448, 3), 120, np.uint8)
    cam = {"label": "Kabukicho", "place": "Tokyo, Japan", "tz": "Asia/Tokyo"}

    def render(**ov):
        cap._memo.clear(), cap._layers.clear()
        return wm.render_preview({**cam, "overlay": {"wx_beside": True, "opacity": 1.0, **ov}}, frame, (448, 130),
                                 elapsed=3, wx=wx)
    out, inside = render(), render(wx_beside_box=True)
    touched = lambda img: np.flatnonzero((img != frame).any(axis=(0, 2)))
    assert touched(inside).max() > touched(out).max() - 2              # the box reaches past the icon
    row = inside[-6]                                                    # a row through the box, below the icon
    right = touched(inside).max()
    seg = row[touched(inside)[0]:right + 1]
    assert not (seg == 120).all(axis=-1).any()                         # box all the way to the icon: no frame shows
    cap._memo.clear(), cap._layers.clear()
    seg = render()[-6][touched(out)[0]:touched(out).max() + 1]
    assert (seg == 120).all(axis=-1).any()                              # (outside: the frame shows before the icon)


def test_a_separate_japanese_caption():
    import pytest

    from display.modules import webcam_caption as cap
    from hub import config
    frame = np.full((130, 448, 3), 120, np.uint8)
    cam = {"label": "Kabukicho", "place": "Tokyo", "tz": "Asia/Tokyo"}

    def render(**ov):
        cap._memo.clear(), cap._layers.clear()
        return wm.render_preview({**cam, "overlay": {"opacity": 1.0, **ov}}, frame, (448, 130), elapsed=3)
    rows = lambda img: np.flatnonzero((img != frame).any(axis=(1, 2)))
    plain = render()
    jp = {"on": True, "text": "歌舞伎町\n東京", "face": "k8x12", "size": 12}
    stacked = render(jp_caption=jp)
    assert rows(stacked)[0] < rows(plain)[0] - 2 * 13                 # two 12 px lines above the caption
    assert (render(jp_caption={**jp, "on": False}) == plain).all()     # off: nothing
    assert (render(jp_caption={**jp, "text": " \n "}) == plain).all()  # no text: nothing
    corner = render(jp_caption={**jp, "corner": "tr"})
    assert rows(corner)[0] <= 3 and np.flatnonzero((corner[:30] != frame[:30]).any(axis=(0, 2)))[0] > 300
    alone = render(style="none", jp_caption=jp)
    assert rows(alone).size and not (alone[-20:] != frame[-20:]).all()   # only the Japanese box, at the caption's corner
    big = render(jp_caption={**jp, "face": "jiskan", "size": 24})
    assert rows(big)[0] < rows(stacked)[0]                             # its own size
    base = {"system": {}, "layout": {}, "modules": {"webcams": {"cams": {"a": {
        "label": "A", "source": {"type": "image", "url": "https://x.example/a.jpg"}, "overlay": {"jp_caption": jp}}}}}}
    config.validate(base)
    for bad in ({"face": "comic"}, {"corner": "middle"}, {"size": 0}, {"text": "x" * 401}):
        cfg = {**base, "modules": {"webcams": {"cams": {"a": {**base["modules"]["webcams"]["cams"]["a"],
                                                            "overlay": {"jp_caption": {**jp, **bad}}}}}}}
        with pytest.raises(config.ConfigError):
            config.validate(cfg)


def test_the_place_beside_the_name():
    from display.modules import webcam_caption as cap
    frame = np.zeros((130, 448, 3), np.uint8)
    cam = {"label": "Shibuya Crossing", "place": "Tokyo, Japan", "tz": "Asia/Tokyo"}

    def render(**ov):
        cap._memo.clear(), cap._layers.clear()
        return wm.render_preview({**cam, "overlay": {"font": "large", "opacity": 1.0, "lines": "stacked", **ov}}, frame,
                                 (448, 130), elapsed=3)
    lines = lambda img: np.flatnonzero((img.max(axis=(1, 2)) > 0))
    below, beside = render(), render(place_beside=True)
    assert len(lines(beside)) < len(lines(below))                      # one line fewer: the place moved up
    width = lambda img: np.flatnonzero((img.max(axis=(0, 2)) > 0)).max()
    assert width(beside) > width(below)                                # …onto the name's line
    long = {**cam, "place": "A place with a name far too long to sit beside the headline on this wall"}
    cap._memo.clear(), cap._layers.clear()
    a = wm.render_preview({**long, "overlay": {"font": "large", "place_beside": True}}, frame, (448, 130), elapsed=3)
    cap._memo.clear(), cap._layers.clear()
    b = wm.render_preview({**long, "overlay": {"font": "large"}}, frame, (448, 130), elapsed=3)
    assert (a == b).all()                                               # too long: it stays on its own line


def test_a_youtube_bot_check_never_makes_a_webcam_down(monkeypatch):
    import time

    from hub.jobs import webcam_health as job
    now = time.time()
    p, drawn = _panel({"cams": {k: {} for k in "abcd"}, "playlist": [{"cam": k} for k in "abcd"]}, monkeypatch, [now])
    p.health = {"a": {"up": False, "why": "[youtube] x: Sign in to confirm you’re not a bot.", "since": 1, "checked": now},
                "b": {"up": False, "why": "YouTube is limiting the hub (bot check); trying again in 4 min", "by": "feed",
                      "since": 1, "checked": now},
                "c": {"up": False, "why": "black", "by": "feed", "since": 1, "checked": now - 3600},
                "d": {"up": False, "why": "black", "by": "feed", "since": 1, "checked": now}}
    p._health = lambda t: p.health
    assert [p.is_down(k, now) for k in "abcd"] == [False, False, False, True]   # c: old enough to try again
    kept = job.update(p.health, {}, now, keep={"a", "b", "c", "d"})
    assert set(kept) == {"c", "d"}                                       # the bot-check "downs" are dropped


def test_note_health_ignores_bot_checks(monkeypatch):
    from hub import webcams
    monkeypatch.setattr(webcams, "_health_seen", {})
    started = []
    monkeypatch.setattr(webcams.threading, "Thread", lambda **k: started.append(k) or type("T", (), {"start": lambda s: None})())
    webcams.note_health("youtube", "https://youtu.be/abcdefghijk", False, "YouTube is limiting the hub (bot check)")
    assert started == []


def test_playback_buffer_evens_out_bursty_frames(monkeypatch):
    st = wm.Stream.__new__(wm.Stream)                                   # no reader thread: frames fed by hand
    st.fps, st.video, st.frame, st.frame_t = 30, True, None, 0.0
    import collections
    st._q = collections.deque(maxlen=200)
    st._play_t0, st._play_k, st._play_pos, st._play_last, st._shown = None, 0, 0.0, 0.0, None
    st._played, st._buffer_since = True, 0.0
    frames = [np.full((2, 2, 3), i % 256, np.uint8) for i in range(400)]
    shown, t, fed = [], 0.0, 0
    while t < 8.0:
        while fed < len(frames) and fed / 30.0 <= t - (0.3 if int(t * 2) % 2 else 0.0):   # arrives in bursts
            st._q.append(frames[fed]); st.frame = frames[fed]; fed += 1
        fr = st.current(t)
        if not shown or fr is not shown[-1][1]:
            shown.append((t, fr))
        t += 0.02                                                       # a 50 Hz loop
    steady = [s for s in shown if s[0] > 3.0]
    gaps = [round(b[0] - a[0], 3) for a, b in zip(steady, steady[1:])]
    assert max(gaps) <= 0.041 and len(steady) > 100                    # every frame, one or two loops each
    assert int(shown[0][1][0, 0, 0]) == 0                               # a picture from the start


def test_stills_show_the_newest_and_the_buffer_holds_when_dry():
    import collections
    st = wm.Stream.__new__(wm.Stream)
    st.fps, st.video, st.frame = 1, False, np.zeros((2, 2, 3), np.uint8)
    assert st.current(0.0) is st.frame                                  # a still: the newest, no buffering
    st.fps, st.video = 10, True
    st._q = collections.deque(np.full((2, 2, 3), i, np.uint8) for i in range(10))
    st._play_t0, st._play_k, st._play_pos, st._play_last, st._shown = None, 0, 0.0, 0.0, None
    st._played, st._buffer_since = True, 0.0
    t = 0.0
    while t < 3.0:                                                      # 1 s of frames, played by a 50 Hz loop
        held = st.current(t)
        t += 0.02
    assert st._play_t0 is None and st.current(t) is held                # ran dry: holds the picture while it fills


def test_the_hub_sends_every_frame_in_order(monkeypatch):
    import asyncio

    from hub import app as hub_app
    from hub import webcams
    f = webcams.Feed.__new__(webcams.Feed)
    import collections
    import threading
    f.kind, f.url, f.fps, f.seq, f.state, f.frame = "youtube", "u", 30, 0, "live", None
    f._lock = threading.Lock()
    f._cond = threading.Condition(f._lock)
    f._waiters = set()
    f.recent = collections.deque(maxlen=webcams.RECENT_FRAMES)
    for i in range(1, 21):                                              # 20 frames published before the viewer reads
        f.seq, f.frame = i, i
        f.recent.append((i, i))
    f.viewers, f.last_view = 0, 0.0

    async def read(n):
        gen = hub_app._webcam_frames(f, lambda frame, feed: frame, fps=30)
        out = [await gen.__anext__() for _ in range(n)]
        await gen.aclose()
        return out
    got = asyncio.run(read(1))
    assert got == [20]                                                  # it starts at the newest
    f2 = asyncio.run(read(1))
    assert f.after(15) == (16, 16) and f.after(20) is None and f.after(-500) == (1, 1)


def test_a_burst_after_a_stall_is_played_not_skipped():
    import collections
    st = wm.Stream.__new__(wm.Stream)
    st.fps, st.video, st.frame = 30, True, None
    st._q = collections.deque(maxlen=int(30 * wm.PLAY_MAX_SEC) + 4)
    st._play_t0, st._play_k, st._play_pos, st._play_last, st._shown = None, 0, 0.0, 0.0, None
    st._played, st._buffer_since = False, 0.0
    frames = [np.full((1, 1, 3), i % 256, np.uint8) for i in range(30 * 60)]
    fed, shown, last, t = 0, 0, None, 0.0
    while t < 50.0:
        # real time, except a 4 s stall at 20 s, then everything that was held comes at once
        upto = int(t * 30) if not 20.0 <= t < 24.0 else 20 * 30
        while fed < min(upto, len(frames)):
            st._q.append(frames[fed]); fed += 1
        fr = st.current(t)
        if fr is not None and fr is not last:
            shown += 1
            last = fr
        t += 0.02
    assert fed - shown - len(st._q) == 0                                # nothing thrown away
    assert st._play_t0 is not None                                      # and it never ran dry (6 s of buffer)


def test_the_walls_stream_gets_frames_as_they_are_decoded():
    import asyncio
    import collections
    import threading

    from hub import app as hub_app
    from hub import webcams
    f = webcams.Feed.__new__(webcams.Feed)
    f.kind, f.url, f.fps, f.seq, f.state, f.frame = "youtube", "u", 30, 0, "live", "paced"
    f._lock = threading.Lock()
    f._cond = threading.Condition(f._lock)
    f._waiters = set()
    f.decoded = collections.deque(maxlen=200)
    f.viewers, f.last_view = 0, 0.0
    f.dseq = 0
    for i in range(1, 11):                                              # 10 decoded, none paced out yet
        f.dseq = i
        f.decoded.append((i, i))

    async def read(n):
        gen = hub_app._webcam_frames(f, lambda frame, feed: frame, fps=30, direct=True)
        out = [await gen.__anext__() for _ in range(n)]
        await gen.aclose()
        return out
    assert asyncio.run(read(10)) == list(range(1, 11))                  # all of them, in order, from the oldest kept


def _fresh_stream(fps=30):
    import collections
    st = wm.Stream.__new__(wm.Stream)
    st.fps, st.video, st.frame, st.frame_t, st.black_since, st.error = fps, True, None, 0.0, None, ""
    st._q = collections.deque(maxlen=int(fps * wm.PLAY_MAX_SEC) + 4)
    st._play_t0, st._play_k, st._play_pos, st._play_last, st._shown = None, 0, 0.0, 0.0, None
    st._played, st._buffer_since = False, 0.0
    return st


def test_video_starts_only_with_a_full_buffer():
    st = _fresh_stream()
    frames = [np.full((1, 1, 3), i % 256, np.uint8) for i in range(400)]
    for fr in frames[:90]:                                              # 3 s in: not enough to start smoothly
        st._q.append(fr)
    st.frame, st.frame_t = frames[89], 1e12
    assert st.current(10.0) is frames[0] and st.buffering() and not st.ready()
    for fr in frames[90:180]:                                           # 6 s: plays
        st._q.append(fr)
    st.current(10.1)
    assert not st.buffering() and st.ready()
    pre = _fresh_stream()                                               # prerolled: full before it comes up
    for fr in frames[:300]:
        pre._q.append(fr)
    pre.frame, pre.frame_t = frames[299], 1e12
    assert pre.ready()
    pre.current(0.0)
    assert not pre.buffering() and len(pre._q) <= int(30 * wm.PLAY_BUFFER_SEC)   # starts at the buffer's depth


def test_a_webcam_played_by_hand_says_it_is_loading(monkeypatch):
    import time as _t
    clock = [1000.0]
    monkeypatch.setattr(wm.time, "time", lambda: clock[0])
    p = wm.WebcamPanel(_Cfg({"cams": {"a": {"label": "A"}}}))
    st = _fresh_stream()
    frame = np.full((130, 448, 3), 200, np.uint8)
    st.frame, st.frame_t, st._buffer_since = frame, clock[0], clock[0]
    st._q.append(frame)
    p._stream = lambda cam, shape, w, h: st
    c = Canvas(448, 130)
    assert p.draw(c, (0, 0, 448, 130), {"panel": "webcam_full", "cam": "a"}, True) is None
    assert int(c.buf[5, 5, 0]) < 120                                    # the still, dimmed under "Loading…"
    clock[0] += wm.GIVE_UP_SEC + 1                                      # it never fills
    st.frame_t = clock[0]
    assert p.draw(c, (0, 0, 448, 130), {"panel": "webcam_full", "cam": "a"}, False) == "skip"


def test_tall_borrows_the_full_width_crop():
    cam = {"crops": {"full": {"x": 0.2, "y": 0.3, "w": 0.5}}}
    t = wm.crop_for(cam, "tall", 448, 175)
    assert t["w"] == 0.5 and t["x"] == 0.2                              # same width and left edge…
    h_full, h_tall = 0.5 * (16 / 9) / (448 / 134), 0.5 * (16 / 9) / (448 / 175)
    assert abs((t["y"] + h_tall / 2) - (0.3 + h_full / 2)) < 1e-4       # …same centre, taller
    whole = wm.crop_for({"crops": {"full": {"x": 0, "y": 0.9, "w": 1.0}}}, "tall", 448, 175)
    assert 0 <= whole["y"] <= 1 - 1.0 * (16 / 9) / (448 / 175) + 1e-4   # kept inside the frame
    own = {"x": 0.1, "y": 0.1, "w": 0.4}
    assert wm.crop_for({"crops": {"full": cam["crops"]["full"], "tall": own}}, "tall", 448, 175) is own
    assert wm.crop_for(cam, "left", 224, 134) is None                   # only tall borrows
    q = wm.stream_query({**cam, "source": {"type": "youtube", "url": "https://youtu.be/x"}}, "tall", 448, 175)
    assert q["cw"] == 0.5 and q["x"] == 0.2


def test_short_turns_prepare_several_webcams_at_once(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {k: {} for k in "abcdef"}, "per_turn": 6,
                       "playlist": [{"cam": k, "sec": 5} for k in "abcdef"]}, monkeypatch, clock)
    streams, warmed = [], []
    p._preroll = lambda cid, shape, zone: streams.append(cid) or _Ready()
    p._warm_hub = lambda cid: warmed.append(cid)
    c, z, entry = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    p.draw(c, z, entry, True)                                           # a on screen, 5 s each
    assert streams == ["b", "c"] and warmed == ["d"]                    # two buffering here, one more at the hub
    switches = []
    for _ in range(4):
        clock[0] += 5
        p.draw(c, z, entry, False)
        switches.append((round(clock[0] - 1000), drawn[-1]))
    assert switches == [(5, "b"), (10, "c"), (15, "d"), (20, "e")]       # on time, every 5 s


def test_long_turns_still_prepare_just_the_next_one(monkeypatch):
    clock = [1000.0]
    p, drawn = _panel({"cams": {k: {} for k in "abcd"}, "per_turn": 4,
                       "playlist": [{"cam": k, "sec": 60} for k in "abcd"]}, monkeypatch, clock)
    streams, warmed = [], []
    p._preroll = lambda cid, shape, zone: streams.append(cid) or _Ready()
    p._warm_hub = lambda cid: warmed.append(cid)
    c, z, entry = Canvas(448, 130), (0, 0, 448, 130), {"panel": "webcam_full", "_play": 1}
    p.draw(c, z, entry, True)
    assert streams == [] and warmed == []                               # 60 s to go: nothing yet
    clock[0] += 35                                                      # 25 s to go: the hub starts b
    p.draw(c, z, entry, False)
    assert warmed == ["b"] and streams == []
    clock[0] += 15                                                      # 10 s to go: the Pi buffers b
    p.draw(c, z, entry, False)
    assert streams == ["b"]
