"""Satellites Overhead (display/modules/iss_panel.py with key="sats_overhead"): the Space Map held over home, zoomed out
to the horizon of one layer, with what home can't see dimmed and the trellis only between satellites it can see."""

import json
import math
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from display.modules import iss_panel, issmap, mappanels, mapsats

T0 = 1_790_000_000
HOME = (45.0, -100.0)          # (a made-up home)


@pytest.fixture(autouse=True)
def _no_hub(monkeypatch):
    monkeypatch.setattr(issmap, "live_from", lambda hub, keys=None: None)


def _track(lat=0.0, lon=60.0):
    return {"track": [[T0 - 3000 + i * 10, lat, lon, 1] for i in range(700)]}


def _starlink(tmp_path, pts, pairs=()):
    """A starlink layer of satellites at fixed (lat, lon) points, with mesh pairs."""
    d = tmp_path / "issmap"
    d.mkdir(exist_ok=True)
    la = np.array([[p[0] * 100 for p in pts]] * 2, "<i2")
    lo = np.array([[p[1] * 100 for p in pts]] * 2, "<i2")
    pr = np.asarray(pairs, "<u2").reshape(-1, 2)
    head = struct.pack("<4sIHIdf", b"SAT2", len(pts), 2, len(pr), float(T0 - 100), 600.0)
    (d / "sats_starlink.bin").write_bytes(head + la.tobytes() + lo.tobytes() + pr.tobytes())
    (d / "sats_starlink.json").write_text(json.dumps({"names": [f"STARLINK-{i}" for i in range(len(pts))]}))
    mapsats._files.clear()


def _view(w, h, ov, home=HOME):
    return issmap.OverheadView(w, h, **issmap.overhead_view(home, w, h, ov)["_overhead"])


@pytest.mark.parametrize("w,h", [(448, 175), (448, 192), (295, 130)])
def test_the_view_puts_home_in_the_middle_and_the_whole_horizon_ring_on_the_map(w, h):
    ov = issmap.overhead_of({})
    v = _view(w, h, ov)
    left = v.left(None)
    hx, hy = issmap._screen(v, left, float(v.wx(HOME[1])), float(v.y(HOME[0])))
    assert abs(hx - w / 2) <= 1 and abs(hy - h / 2) <= 1
    rho = issmap.footprint_deg(550, 10)
    la, lo = issmap._circle(HOME[0], HOME[1], rho, 72)
    xs = (v.wx(lo) - left) % v.W
    ys = v.y(la)
    assert ys.min() >= 0 and ys.max() <= h and xs.min() >= 0 and xs.max() <= w
    assert max(xs.max() - xs.min(), ys.max() - ys.min()) >= min(w, h) * 0.75   # (zoomed in as far as it fits:
    #                                                                             Mercator's north half is taller)
    assert abs((xs.max() - xs.min()) - (ys.max() - ys.min())) <= 0.12 * min(w, h)  # round, not squashed


def test_the_ring_is_sized_for_the_layer_picked():
    scale = lambda layer: issmap.overhead_view(HOME, 448, 175, issmap.overhead_of({"overhead": {"layer": layer}}))
    assert scale("starlink")["_overhead"]["km_per_px"] * 2 < scale("gps")["_overhead"]["km_per_px"]


@pytest.mark.parametrize("km", [25, 300, 2500])
def test_the_zoom_shows_that_far_from_home_up_and_down(km):
    v = _view(448, 175, issmap.overhead_of({"overhead": {"radius_km": km}}))
    lat = lambda y: math.degrees(2 * math.atan(math.exp(v.m0 + ((v.h - 1) / 2 - y) / v.k)) - math.pi / 2)
    north, south = (math.radians(lat(-0.5) - HOME[0]) * 6371, math.radians(HOME[0] - lat(v.h - 0.5)) * 6371)
    assert abs(north - km) / km < 0.01 and south >= km          # (Mercator: the poleward edge is the nearer)


def test_zoomed_far_in_the_map_shades_only_the_zone():
    v = _view(448, 192, issmap.overhead_of({"overhead": {"radius_km": 10}}))
    assert v.W > 100_000                                   # (a world this many px round...)
    world, mask = issmap.shaded(v, True, T0, ex=issmap.extras_of(None, True))
    assert world.shape == (192, 448, 3) and mask.shape == (192, 448)   # (...but only the zone is drawn)


def test_the_view_lines_up_with_the_hubs_mercator_pixel_for_pixel():
    from hub.weather.geo import View as GeoView
    for km in (0, 40, 900):
        v = _view(448, 175, issmap.overhead_of({"overhead": {"radius_km": km}}))
        g = GeoView({"center": [v.lat0, v.centre_lon], "km_per_px": v.km_per_px}, v.w, v.h)
        lats = np.array([HOME[0], HOME[0] + 1.3, HOME[0] - 0.7])
        lons = np.array([HOME[1], HOME[1] - 2.1, HOME[1] + 0.4])
        gx, gy = g.project_many(lons, lats)                # (hub: pixel i spans i..i+1; the Pi: centred on i)
        px, py = v.wx(lons) - v.left(), v.y(lats)
        assert np.abs(gx - 0.5 - px).max() < 0.02 and np.abs(gy - 0.5 - py).max() < 0.02


def _ov_file(tmp_path, v, lines, lights=None):
    d = tmp_path / "issmap"
    d.mkdir(exist_ok=True)
    head = b"OVB1" + v.w.to_bytes(2, "big") + v.h.to_bytes(2, "big") + bytes([lights is not None])
    body = lines.tobytes() + (b"" if lights is None else lights.tobytes())
    (d / f"{issmap.overhead_name(v)}.bin").write_bytes(head + body)
    issmap._obases.clear()


def test_the_hubs_basemap_replaces_the_natural_earth_lines_for_its_own_view_only(tmp_path, monkeypatch):
    monkeypatch.setattr(issmap, "DATA_DIR", str(tmp_path))
    v = _view(448, 175, issmap.overhead_of({"overhead": {"radius_km": 200}}))
    lines = np.zeros((175, 448), np.uint8)
    lines[10, 10], lines[20, 20], lines[30, 30] = issmap.OV_COAST, issmap.OV_COUNTRY, issmap.OV_PROVINCE
    _ov_file(tmp_path, v, lines)
    img, _m = issmap.base(v, True, ob=issmap.overhead_base(v))
    assert tuple(img[10, 10]) == issmap.COAST and tuple(img[20, 20]) == issmap.BORDER
    assert tuple(img[30, 30]) == issmap.PROVINCE and tuple(img[40, 40]) == issmap.OCEAN
    img, _m = issmap.base(v, True, borders=True, ob=issmap.overhead_base(v), provinces=False)
    assert tuple(img[30, 30]) == issmap.OCEAN and tuple(img[20, 20]) == issmap.BORDER
    img, _m = issmap.base(v, True, borders=False, ob=issmap.overhead_base(v))
    assert tuple(img[20, 20]) == issmap.OCEAN and tuple(img[10, 10]) == issmap.COAST
    other = _view(448, 175, issmap.overhead_of({"overhead": {"radius_km": 210}}))   # (another zoom: not its map)
    assert issmap.overhead_base(other) is None


def test_the_hubs_city_lights_are_used_at_the_views_own_pixels(tmp_path, monkeypatch):
    monkeypatch.setattr(issmap, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(issmap, "grid", lambda name: np.zeros((360, 720), np.uint8) if name == "lights" else None)
    v = _view(448, 175, issmap.overhead_of({"overhead": {"radius_km": 200}}))
    lights = np.zeros((175, 448), np.uint8)
    lights[50:52, 100:102] = 255                           # (a town, 2 px)
    _ov_file(tmp_path, v, np.zeros((175, 448), np.uint8), lights)
    ex = issmap.extras_of(None, True)
    night = next(t for t in range(T0, T0 + 86400, 3600)    # (an hour when home is in the dark)
                 if issmap.daylight(v, t)[87, 224, 0] < 0.5)
    world, _m = issmap.shaded(v, True, night, ex=ex)
    assert world[50, 100].astype(int).sum() > world[60, 100].astype(int).sum() + 100


def test_satellites_past_the_horizon_are_dim_and_the_trellis_joins_only_ones_home_sees(tmp_path):
    near = [(HOME[0] + 2, HOME[1]), (HOME[0] - 2, HOME[1] + 2)]
    far = [(HOME[0] + 14, HOME[1] + 30)]                    # on the map, but well past Starlink's horizon
    _starlink(tmp_path, near + far, pairs=[(0, 1), (1, 2)])
    ov = issmap.overhead_of({})
    v = _view(448, 175, ov)
    left = v.left(None)
    img = np.zeros((175, 448, 3), np.uint8)
    paths, up = [], {}
    ms = {"sats": {"starlink": {"on": True, "mark": "dot", "links": True, "near_km": 0, "brightness": 1.0,
                                "colour": "#ffffff"}}}
    mapsats.draw(img, v, left, T0, ms, str(tmp_path), (0.0, 60.0), (0, 0), paths, [], {}, (200, 200, 200), [],
                 observer=(HOME[0], HOME[1], 10.0, 0.3), up=up)
    assert up == {"starlink": 2}
    px = lambda p: img[int(round(float(v.y(p[0])))), int(round((float(v.wx(p[1])) - left) % v.W))]
    assert px(near[0]).max() == 255 and 60 <= px(far[0]).max() <= 90           # (0.3 as bright)
    segs = [p for p in paths if p["kind"] == "segs"]
    assert len(segs) == 1 and len(segs[0]["x0"]) == 1                          # only the link between the two seen
    # outside 0: not drawn at all
    img[:] = 0
    mapsats.draw(img, v, left, T0, ms, str(tmp_path), None, (0, 0), [], [], {}, (200, 200, 200), [],
                 observer=(HOME[0], HOME[1], 10.0, 0.0))
    assert px(far[0]).max() == 0


def test_the_panel_draws_over_home_with_its_own_panels_and_hides_a_distant_iss(tmp_path, monkeypatch):
    _starlink(tmp_path, [(HOME[0] + 1, HOME[1] + 1)])
    (tmp_path / "issmap" / "overhead.json").write_text(json.dumps({"home": list(HOME)}))
    (tmp_path / "iss.json").write_text(json.dumps(_track(lat=-30.0, lon=60.0)))
    monkeypatch.setattr("time.time", lambda: T0 + 5)
    cfg = SimpleNamespace(modules={"sats_overhead": {"label": "Satellites overhead",
                                                     "map": {"sats": {"starlink": {"on": True}}}}})
    p = iss_panel.IssMapPanel(cfg, str(tmp_path), key="sats_overhead")
    assert p._home(T0) == HOME
    from display.gfx import Canvas
    c = Canvas(448, 192)
    assert p.draw(c, (0, 0, 448, 175), {"panel": "sats_overhead_tall"}, True) is None
    assert c.buf[:175].any()
    mp, view = iss_panel.overhead_mapset(p.cam, HOME, 448, 175)
    img, _paths = issmap.render(448, 175, _track(lat=-30.0, lon=60.0), T0 + 5, True, True, None, None, None, mp, view)
    tl = np.asarray(issmap.PAL["A"])                                           # the ISS's panels' colour: nowhere
    assert not (img == tl).all(axis=2).any()
    assert [q["fields"] for q in mappanels.panels_of(mp)] == [["sats_up"]]
    ctx = lambda up: {"data": {}, "live": None, "t": T0, "units": {}, "flags": True, "sats_up": up}
    assert mappanels.rows_for("sats_up", ctx({"starlink": 1}))[0][1] == "1"
    assert mappanels.rows_for("sats_up_total", ctx({"starlink": 3, "gps": 9})) == [("OVERHEAD", "12")]
    assert mappanels.rows_for("sats_up", ctx(None)) == []                         # (the Space Map has no horizon)


def test_it_borrows_the_space_maps_look_until_saved_and_uses_its_own_home_when_set():
    mods = {"iss_map": {"label": "ISS", "overlay": {"corner": "bl", "rows": [{"items": [{"k": "live"}, {"k": "name"}]}]},
                        "map": {"sats": {"gps": {"on": True}}, "panels": [{"fields": ["crew"]}], "views": {"tall": {"zoom": 2}}}}}
    s = iss_panel.settings_of(mods, "sats_overhead")
    assert s["label"] == iss_panel.OVERHEAD_LABEL and s["overlay"]["corner"] == "bl"
    assert s["overlay"]["rows"] == [{"items": [{"k": "name"}]}]
    assert s["map"]["sats"] == {"gps": {"on": True}} and "panels" not in s["map"] and "views" not in s["map"]
    assert iss_panel.home_of({"home": [10, 20]}, "/nowhere") == (10.0, 20.0)
    assert iss_panel.home_of({}, "/nowhere") is None


def test_the_hub_checks_its_settings_and_fetches_layers_for_either_map():
    from hub import config
    from hub.jobs import sats
    config._validate_sats_overhead({"label": "Up", "home": [49.1, -123.1],
                                    "map": {"overhead": {"layer": "kuiper", "min_el": 15, "outside": 0, "ring": False,
                                                         "radius_km": 400}}})
    for bad in ({"home": [99, 0]}, {"home": "here"}, {"map": {"overhead": {"layer": "nope"}}},
                {"map": {"overhead": {"min_el": 90}}}, {"map": {"overhead": {"zoom": 3}}},
                {"map": {"overhead": {"radius_km": 5}}}, {"map": {"overhead": {"radius_km": 9000}}},
                {"map": {"overhead": {"radius_km": "far"}}}):
        with pytest.raises(config.ConfigError):
            config._validate_sats_overhead(bad)
    cfg = {"modules": {"iss_map": {"map": {"sats": {"gps": {"on": True}}}},
                       "sats_overhead": {"map": {"sats": {"starlink": {"on": True}}}}}}
    assert sats.wanted(cfg) == ["starlink", "gps"]
    assert sats.wanted({"modules": {"iss_map": {"map": {"sats": {"gps": {"on": True}}}}}}) == ["gps"]


@pytest.mark.parametrize("layer", ["gps", "goes"])
def test_high_orbits_zoom_out_no_further_than_the_world_across_the_zone(layer):
    v = _view(448, 175, issmap.overhead_of({"overhead": {"layer": layer}}))
    assert v.W == 448 and v.left() == v.cx0
    assert abs(float(v.y(HOME[0])) - 87) < 0.01                       # (home still in the middle)
    assert _view(448, 175, issmap.overhead_of({"overhead": {"radius_km": 8000}})).W == 448


def test_it_still_draws_the_sky_without_the_iss_track(tmp_path):
    _starlink(tmp_path, [(HOME[0] + 1, HOME[1] + 1)])
    issmap.DATA_DIR = str(tmp_path)
    mp, view = iss_panel.overhead_mapset({"map": {"sats": {"starlink": {"on": True, "mark": "dot", "colour": "#ffffff",
                                                                         "brightness": 1.0}}}}, HOME, 448, 175)
    img, _paths = issmap.render(448, 175, None, T0, True, True, None, None, None, mp, view)
    ring = np.asarray(issmap.HORIZON)
    assert (np.abs(img.astype(int) - ring).sum(axis=2) < 60).any()             # the ring is there
    assert (img == 255).all(axis=2).any()                                       # and the satellite
