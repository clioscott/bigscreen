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
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": "x", "source": {}}] * 2, "interval_ms": 200, "hold_sec": 0})
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
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": "x", "source": {}}] * 2, "interval_ms": 200, "hold_sec": 0})
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


def test_the_grid_has_room_for_hundreds_of_cameras():
    from hub import app as hub_app
    cams = [{"name": str(i), "source": {"type": "image", "url": f"https://x.example/{i}.jpg"}} for i in range(700)]
    assert len(hub_app._grid_list({"cams": cams})) == 700


def test_a_refresh_mid_reveal_does_not_cut_the_grid_short(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(wm.time, "time", lambda: clock[0])
    g = wm.CameraGrid("http://hub:1", {"cams": [{"name": "x", "source": {}}] * 10, "interval_ms": 200, "hold_sec": 0})
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
