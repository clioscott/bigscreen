"""The Space Map (the ISS map as its own entry): its view per size, the panels in nine places, labels that keep off
each other, the satellite layers, China's links, and the settings the hub accepts."""

import json
import struct
import time

import numpy as np
import pytest

from display.modules import issmap, maplabels, mappanels, mapsats

T0 = 1_790_000_000


def _track(lon0=0.0, lat=0.0, step=10, n=700, dlon=0.6):
    return {"track": [[T0 + i * step, lat, (lon0 + i * dlon + 180) % 360 - 180, 1] for i in range(n)]}


@pytest.fixture(autouse=True)
def _no_hub(monkeypatch):
    monkeypatch.setattr(issmap, "live_from", lambda hub, keys=None: None)


def test_the_automatic_view_reaches_north_past_the_russian_stations_and_keeps_the_band_off_the_caption():
    for w, h, rb in ((448, 175, 18), (448, 192, 18), (448, 134, 18)):
        v = issmap.View(w, h, reserve=(0, rb))
        assert float(v.y(issmap.TOP_MIN)) >= -0.5                      # 66°N is on the map (not cut off)
        assert float(v.y(-issmap.ISS_LAT)) <= h - rb - issmap.BAND_MARGIN + 0.5


def test_zoomed_in_the_view_follows_the_iss_up_and_down():
    a = issmap.View(448, 175, opts={"zoom": 2.5}, iss_lat=40.0)
    b = issmap.View(448, 175, opts={"zoom": 2.5}, iss_lat=-40.0)
    assert a.W == round(448 * 2.5) and a.key == b.key                  # one raster of the whole world
    assert abs(float(a.y(40.0)) - 175 / 2) < 2 and abs(float(b.y(-40.0)) - 175 / 2) < 2


def test_a_set_latitude_sits_in_the_middle_and_stretch_changes_rows_per_degree():
    v = issmap.View(448, 175, opts={"lat": 30.0, "stretch": 1.5})
    assert abs(float(v.y(30.0)) - 87.5) < 1.5
    assert abs(v.ys - 448 / 360 * 1.5) < 1e-6


def test_the_view_is_read_per_size():
    cam = {"map": {"views": {"tall": {"zoom": 2}, "wall": {"lat": 10}}}}
    assert issmap.view_of(cam, "tall")["zoom"] == 2 and issmap.view_of(cam, "wall")["lat"] == 10
    assert issmap.view_of(cam, "left") == issmap.VIEW


def _ctx(**kw):
    d = {"people": [{"name": "Pyotr Dubrov", "flag": "ru", "craft": "Soyuz MS-29", "launched": T0 - 86400 * 10,
                     "iss": True, "role": "Commander"},
                    {"name": "Zhu Yangzhu", "flag": "cn", "craft": "Shenzhou 23", "launched": T0 - 86400 * 5,
                     "iss": False, "role": "Commander"}],
         "spacewx": {"kp": 5.3, "wind": 410, "bt": 5, "bz": -3, "xray": 2.3e-6},
         "mission": {"name": "Expedition 75", "start": T0 - 86400 * 3}}
    return {"data": d, "live": None, "t": T0, "units": mappanels.UNIT_DEFAULTS, "flags": False, **kw}


def test_crew_names_full_last_role_or_just_flags():
    got = {m: mappanels.rows_for("crew", _ctx(crew_names=m)) for m in mappanels.CREW_NAMES}
    assert got["full"] == [("PYOTR DUBROV", "10d")] and got["last"] == [("DUBROV", "10d")]
    assert got["role"] == [("CMDR. DUBROV", "10d")] and got["flags"][0][1] is None
    assert mappanels.rows_for("tg_crew", _ctx(crew_names="last")) == [("ZHU", "5d")]   # (family name first)


def test_fields_without_data_drop_out_and_space_weather_reads_right():
    assert mappanels.rows_for("cabin_p", _ctx()) == []                     # (no live feed)
    assert mappanels.rows_for("kp", _ctx())[0][1].startswith("5.3 STORM G1")
    assert mappanels.rows_for("xray", _ctx())[0][1] == "C2.3"
    assert mappanels.rows_for("expedition", _ctx()) == [("EXPEDITION 75", "DAY 4")]


def test_panels_in_one_column_never_overlap():
    panels = [{"pos": p, "fields": ["kp", "wind", "bz", "xray", "expedition"], "on": True}
              for p in ("tl", "ml", "bl", "tl", "bl")]
    placed = mappanels.layout(mappanels.panels_of({"panels": panels}), _ctx(), 448, 175, 0, 18)
    boxes = mappanels.boxes(placed)
    assert 3 <= len(boxes) <= 5                                       # (what has no room is left out)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert a[3] <= b[1] or b[3] <= a[1] or a[2] <= b[0] or b[2] <= a[0]


def test_labels_keep_off_what_is_already_there():
    items = [{"text": "TDRS 7", "anchor": (100, 50), "half": (4, 2), "pos": "auto", "prio": 3, "rgb": (255, 255, 255)}]
    (it, x, y, _t, bw), = maplabels.place(items, [(104, 40, 160, 60)], 448, 175)   # something right of it
    assert x + bw <= 104 or y + maplabels.LH <= 40 or y >= 60
    fixed = [{**items[0], "pos": "below"}]
    (_it, x, y, _t, _bw), = maplabels.place(fixed, [], 448, 175)
    assert y > 50


def _sats_file(tmp_path, lat0, lon0, lon1):
    d = tmp_path / "issmap"
    d.mkdir()
    la = np.array([[lat0 * 100], [lat0 * 100]], "<i2")
    lo = np.array([[lon0 * 100], [lon1 * 100]], "<i2")
    head = struct.pack("<4sIHIdf", b"SAT2", 1, 2, 0, float(T0), 30.0)
    (d / "sats_gps.bin").write_bytes(head + la.tobytes() + lo.tobytes())
    (d / "sats_gps.json").write_text(json.dumps({"names": ["GPS BIIR-5  (PRN 22)"]}))


def test_satellites_are_interpolated_across_the_date_line(tmp_path):
    _sats_file(tmp_path, 10.0, 179.0, -179.0)
    mapsats._files.clear()
    f = mapsats.load("gps", str(tmp_path))
    lat, lon, ok = mapsats.positions(f, T0 + 15)
    assert ok[0] and abs(lat[0] - 10) < 0.01 and abs(abs(lon[0]) - 180) < 0.01
    assert mapsats.positions(f, T0 + 100) is None                          # (past the file: nothing, not old)
    assert mapsats._short_name("GPS BIIR-5  (PRN 22)") == "PRN 22"


def test_tiangong_talks_to_a_chinese_station_or_through_tianlian():
    assert issmap.cn_links((40.0, 110.0))[0]["via"] in issmap.CN_GROUND
    relay = [{"name": "Tianlian 2-02", "net": "tianlian", "lat": 0.8, "lon": 76.9}]
    got = issmap.cn_links((-20.0, 70.0), relay)
    assert got and got[0]["via"] == "Tianlian 2-02" and len(got[0]["pts"]) == 3


def test_the_whole_space_map_draws_with_everything_on(tmp_path, monkeypatch):
    _sats_file(tmp_path, 10.0, 20.0, 21.0)
    mapsats._files.clear()
    monkeypatch.setattr(issmap, "DATA_DIR", str(tmp_path))
    ex = issmap.extras_of({"map": {"extras": {k: {"on": True} for k in issmap.EXTRAS}}}, space_map=True)
    ms = {"sats": {"gps": {"on": True, "links": True, "label": True}}, "relays": "linked", "ground": "linked",
          "labels": {k: {"name": True, "flag": True} for k in maplabels.KINDS},
          "panels": [{"pos": p, "fields": list(mappanels.FIELDS)[:6]} for p in mappanels.POSITIONS],
          "links": {"nasa": {"style": "comet"}, "ru": {"style": "pulse"}}}
    data = {**_track(lon0=40.0, lat=-30.0), "css": [[T0 + i * 20, 20.0, (i * 1.2) % 360 - 180, 1] for i in range(350)],
            "launches": [{"name": "X", "rocket": "Falcon 9", "net": T0 + 7200, "lat": 28.5, "lon": -80.6, "flag": "us"}]}
    live = {"at": T0 + 3040, "connected": True, "signal": [[T0, 1]]}
    img, paths = issmap.render(448, 175, data, T0 + 3000, extras=ex, live=live, mapset=ms,
                               view=issmap.VIEW, reserve=(0, 18), avoid=(0, 157, 300, 175))
    assert img.shape == (175, 448, 3) and paths
    buf = img.copy()
    issmap.flow(buf, paths, time.time(), ex, True)
    assert (buf != img).any()


def test_the_hub_checks_the_space_map_settings():
    from hub import config
    config._validate_space_map({"views": {"tall": {"zoom": 2, "lat": None}}, "relays": "linked",
                                "panels": [{"pos": "mc", "fields": ["kp"], "crew_names": "role"}],
                                "sats": {"starlink": {"on": True, "brightness": 0.3, "links": True, "near_km": 2000}},
                                "labels": {"ground": {"flag": True, "pos": "below"}}, "names": {"TDRS 13": "TDRS-M"},
                                "links": {"cn": {"up": "#112233", "style": "comet"}}, "units": {"speed": "mph"}})
    for bad in ({"views": {"tall": {"zoom": 9}}}, {"panels": [{"pos": "middle"}]}, {"panels": [{"fields": ["nope"]}]},
                {"sats": {"pluto": {}}}, {"links": {"nasa": {"style": "lasers"}}}, {"labels": {"iss": {"pos": "up"}}},
                {"relays": "sometimes"}, {"panels": [{"crew_names": "nick"}]}, {"nope": 1},
                {"panels": [{"sparks": ["kp"]}]}, {"panels": [{"spark_min": 999}]}, {"panels": [{"when": "night"}]}):
        with pytest.raises(config.ConfigError):
            config._validate_space_map(bad)


def test_the_hub_keeps_each_readings_past_and_works_out_altitude(monkeypatch):
    from collections import deque
    from hub import iss_live
    monkeypatch.setattr(iss_live, "values", {})
    monkeypatch.setattr(iss_live, "series", {})
    for i in range(10):
        t = 1000.0 + i * 25
        for k, v in (("pos_x", 6800.0), ("pos_y", 0.0), ("pos_z", 0.0), ("co2", 2.4 + i / 100)):
            iss_live._note(k, str(v), "24", None, t)
    h = iss_live.history(["co2", "alt"])["series"]
    assert len(h["co2"]) == 10 and abs(h["alt"][-1][1] - (6800 - iss_live.R_EARTH)) < 0.01
    assert isinstance(iss_live.series["co2"], deque)


def test_a_spacewalk_starts_when_the_crewlock_stays_pumped_down_and_ends_when_it_is_back_up(monkeypatch):
    from hub import iss_live
    monkeypatch.setattr(iss_live, "eva", {"on": False, "since": None, "low_since": None, "last": None})
    monkeypatch.setattr(iss_live, "_save_eva", lambda: None)
    iss_live._eva("756", 0)
    iss_live._eva("3", 100)
    iss_live._eva("0", 130)                                                # (not yet a minute)
    assert not iss_live.eva["on"]
    iss_live._eva("0", 170)
    assert iss_live.eva["on"] and iss_live.eva["since"] == 100
    iss_live._eva("750", 100 + 6 * 3600)
    assert not iss_live.eva["on"] and iss_live.eva["last"] == {"start": 100, "end": 100 + 6 * 3600}


def test_sparklines_and_spacewalk_only_panels():
    pts = [[T0 - 3600 + i * 20, 2.4 + (i % 7) / 50] for i in range(181)]
    live = {"values": {"co2": {"v": "2.5"}, "crewlock": {"v": "0"}}, "eva": {"on": True, "since": T0 - 3700},
            "history": {"series": {"co2": pts}}}
    ctx = {**_ctx(), "live": live}
    p = mappanels.panels_of({"panels": [{"pos": "tr", "fields": ["co2", "eva"], "sparks": ["co2"], "spark_w": 24,
                                         "spark_min": 30}]})[0]
    m = mappanels.measure(p, ctx, 300)
    assert m["sw"] == 24 and m["sers"][0] is not None and m["lines"][1][1].startswith("1h")
    img = np.zeros((40, 200, 3), np.uint8)
    mappanels.draw(img, [(p, m, 0, 0)])
    assert (img == (255, 214, 90)).all(axis=2).sum() > 10                  # the line is drawn
    only = mappanels.panels_of({"panels": [{"pos": "tc", "fields": ["crewlock"], "when": "eva"}]})
    assert mappanels.layout(only, ctx, 448, 175)
    quiet = {**ctx, "live": {**live, "eva": {"on": False}}}
    assert mappanels.layout(only, quiet, 448, 175) == []
    assert mappanels.history_keys({"panels": [{"fields": ["co2", "power_a"], "sparks": ["co2", "power_a"]}]}) == {"co2", "a_sum"}


def test_the_live_video_gets_a_place_and_the_panels_stack_after_it():
    ms = {"video": {"on": True, "pos": "tr", "width": 80}, "_video_up": True,
          "panels": [{"pos": "tr", "fields": ["kp"]}]}
    img, paths = issmap.render(448, 175, _track(), T0 + 3000, mapset=ms, view=issmap.VIEW, live={"values": {}})
    box = issmap.video_box(paths)
    assert box and box["w"] == 82 and box["h"] == 47 and box["x"] == 448 - 82 and box["y"] == 0
    frame = np.full((45, 80, 3), 77, np.uint8)
    buf = img.copy()
    issmap.draw_video(buf, box, frame)
    assert (buf[30, 448 - 40] == 77).all()
    gone = issmap.render(448, 175, _track(), T0 + 3000, mapset={**ms, "_video_up": False}, view=issmap.VIEW)[1]
    assert issmap.video_box(gone) is None                                  # (no picture: no room kept for it)


def test_the_space_map_video_shows_only_a_usable_picture(monkeypatch):
    from types import SimpleNamespace
    from display.modules import iss_panel, webcam
    opened = []

    class FakeStream:
        def __init__(self, hub, q):
            self.query, self.alive, self.video = q, True, True
            opened.append(q)

        def used(self, hold=0.0):
            pass

        def close(self):
            self.alive = False

        def current(self, now=None):
            return np.zeros((54, 96, 3), np.uint8)

    monkeypatch.setattr(webcam, "Stream", FakeStream)
    cfg = SimpleNamespace(modules={"webcams": {"cams": {"iss": {"space": "iss", "source": {"type": "youtube", "url": "u"}}}},
                                   "iss_map": {"map": {"video": {"on": True}}}})
    p = iss_panel.IssMapPanel(cfg)
    data = {"track": [[time.time() - 600 + i * 10, 0, 0, 1] for i in range(200)]}
    monkeypatch.setattr(webcam.WebcamPanel, "_unusable", staticmethod(lambda st, now: True))
    assert p._video(data, time.time()) is None and opened                  # (open, but nothing to show yet)
    monkeypatch.setattr(webcam.WebcamPanel, "_unusable", staticmethod(lambda st, now: False))
    assert p._video(data, time.time()).shape == (54, 96, 3)
    dark = {"track": [[time.time() - 600 + i * 10, 0, 0, 0] for i in range(200)]}
    assert p._video(dark, time.time()) is None and p.stream is None        # (night side: closed)


def test_durations_have_lower_case_units_drawn_dimmer():
    assert [mappanels._countdown(x) for x in (42, 245, 5420, 2 * 86400 + 3 * 3600)] == ["42s", "4m05s", "1h30m20s", "2d3h"]
    img = np.zeros((8, 80, 3), np.uint8)
    mappanels.draw_value(img, 0, 0, "1h30m", (255, 255, 255), (0, 255, 0))
    assert (img == (255, 255, 255)).all(axis=2).any() and (img == (0, 255, 0)).all(axis=2).any()
