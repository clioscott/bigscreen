"""The ISS maps (display/modules/issmap.py) and where they play (display/modules/webcam.py): centred on the ISS or
a fixed world with lines carried across the date line, the link to a ground station or through a relay, the small
map keeping off the caption, and the ISS's turn showing the map while its video is unusable."""

import time
from types import SimpleNamespace

import numpy as np
import pytest

from display.modules import issmap, webcam

T0 = 1_790_000_000


def _track(lon0=0.0, lat=0.0, step=10, n=700, dlon=0.6):
    """A made-up track: eastward along a latitude, dlon degrees every step seconds."""
    return {"track": [[T0 + i * step, lat, (lon0 + i * dlon + 180) % 360 - 180, 1] for i in range(n)]}


def _iss_x(img):
    """Columns holding the ISS icon's gold centre pixel."""
    return np.flatnonzero((img == issmap.PAL["G"]).all(axis=2).any(axis=0))


def test_link_goes_straight_down_near_a_station_and_through_a_relay_over_open_ocean():
    assert issmap.link((33.0, -105.0)) == ("ground", "White Sands")
    kind, relay, term = issmap.link((-40.0, -130.0))                   # the South Pacific
    assert kind == "relay" and relay == "TDRS West" and term in issmap.TDRS_GROUND


def test_the_map_stays_centred_on_the_iss():
    data = _track()
    for t in (T0 + 3000, T0 + 5000):
        img = issmap.render(448, 192, data, t)
        xs = _iss_x(img)
        assert xs.size and abs(xs.mean() - 224) <= 1


def test_a_fixed_world_lets_the_iss_cross_it_and_carries_its_lines_over_the_date_line():
    data = _track(lon0=-30.0, lat=-40.0)
    t = next(r[0] for r in data["track"] if r[2] >= 175)               # just short of the date line
    img = issmap.render(448, 192, data, t, full=True, fixed=True)
    assert _iss_x(img).mean() > 430                                     # at the right-hand edge, not the middle
    red = (img == issmap.LINK).all(axis=2)
    cols = np.flatnonzero(red.any(axis=0))
    assert cols.min() < 20 and cols.max() > 430                         # the link to TDRS West goes off the right
    assert not red[:, 60:400].any()                                     # … and on from the left, never the long way


def test_the_small_map_has_no_stations_relays_or_links():
    img = issmap.render(96, 48, _track(lon0=-110.0, lat=33.0), T0 + 600, full=False)
    for k in ("B", "K", "D"):
        assert not (img == issmap.PAL[k]).all(axis=2).any()
    assert not (img == issmap.LINK).all(axis=2).any()
    assert (img == issmap.PAL["A"]).all(axis=2).any()                   # the tiny ISS


def test_the_small_map_goes_above_a_caption_in_its_corner_and_below_one_on_top():
    w, h = 448, 175
    x, y, sw, sh = issmap.small_box(w, h, "tr")
    assert (x + sw, y) == (w - 4, 4)
    cap = (300, 140, 444, 172)                                          # a caption bottom right
    x, y, sw, sh = issmap.small_box(w, h, "br", cap)
    assert y + sh <= cap[1] - 1
    cap = (4, 2, 200, 30)                                               # top left
    x, y, sw, sh = issmap.small_box(w, h, "tl", cap)
    assert y >= cap[3] + 1
    x, y, sw, sh = issmap.small_box(w, h, "tl", (300, 140, 444, 172))   # someone else's corner: stays put
    assert (x, y) == (4, 4)


def test_blank_on_screen_goes_by_when_the_frames_show():
    st = webcam.Stream.__new__(webcam.Stream)
    st.in_step, st.black_since = True, 100.0
    st._marks = __import__("collections").deque([(100.0, False), (110.0, True), (111.0, True), (120.0, False)])
    assert not st.blank_on_screen(112.0)                                # blank on screen for 2 s
    assert st.blank_on_screen(116.0)
    assert not st.blank_on_screen(121.0)                                # the picture is back on screen


def _panel(cam):
    cfg = SimpleNamespace(modules={"webcams": {"cams": {"iss": cam}, "playlist": [{"cam": "iss", "sec": 60}]}})
    p = webcam.WebcamPanel(cfg)
    p._iss = _track()
    p._iss_checked = time.time() + 1e9
    return p


def test_the_iss_with_its_map_is_never_down_and_without_it_skips_the_night():
    now = time.time()
    night = {"track": [[now - 600 + i * 10, 0.0, 0.0, 0] for i in range(200)]}
    p = _panel({"space": "iss", "source": {"type": "youtube", "url": "x"}})
    p._iss = night
    p._health_checked = now + 1e9
    assert not p.is_down("iss", now)
    p = _panel({"space": "iss", "map": {"when_down": False}, "source": {"type": "youtube", "url": "x"}})
    p._iss = night
    p._health_checked = now + 1e9
    assert p.is_down("iss", now)


def test_the_full_map_plays_in_the_turn_while_there_is_no_picture(monkeypatch):
    from display.gfx import Canvas
    cam = {"space": "iss", "label": "ISS", "source": {"type": "youtube", "url": "x"}, "overlay": {"style": "none"}}
    p = _panel(cam)
    fake = SimpleNamespace(showable=lambda now=None: False, buffering=lambda: True, blank_on_screen=lambda now=None: False,
                           black_since=None, in_step=False, error="", frame_t=0.0)
    monkeypatch.setattr(p, "_stream", lambda *a, **k: fake)
    p._iss = {"track": [[time.time() - 6000 + i * 10, 10.0, (i * 0.6) % 360 - 180, 1] for i in range(1300)]}
    c = Canvas(448, 130)
    assert p._draw_cam(c, (0, 0, 448, 130), {"panel": "webcam_full", "cam": "iss"}, True, black_skips=True) is None
    assert (c.buf == issmap.OCEAN).all(axis=2).sum() > 1000 or (c.buf[..., 2] > c.buf[..., 0]).mean() > 0.5


def test_map_settings_are_checked_in_the_config():
    from hub import config
    config._validate_overlay("x", {"minimap": {"on": True, "corner": "bl"}})
    with pytest.raises(config.ConfigError):
        config._validate_overlay("x", {"minimap": {"on": True, "corner": "tc"}})


def _fake_stream(**kw):
    d = dict(showable=lambda now=None: True, buffering=lambda: False, blank_on_screen=lambda now=None: False,
             black_since=None, in_step=True, overdue=lambda now=None: 0.0, error="", frame_t=time.time(),
             current=lambda now=None: np.zeros((130, 448, 3), np.uint8))
    d.update(kw)
    return SimpleNamespace(**d)


@pytest.mark.parametrize("why", ["blank arrived early", "stalled"])
def test_in_the_list_a_blank_or_stalled_iss_shows_its_map_instead_of_ending_the_turn(monkeypatch, why):
    from display.gfx import Canvas
    now = time.time()
    cam = {"space": "iss", "label": "ISS", "source": {"type": "youtube", "url": "x"}, "overlay": {"style": "none"}}
    p = _panel(cam)
    p._iss = {"track": [[now - 6000 + i * 10, 10.0, (i * 0.6) % 360 - 180, 1] for i in range(1300)]}
    if why == "stalled":
        st = _fake_stream(overdue=lambda now=None: webcam.GIVE_UP_SEC + 5)
    else:                                              # black by arrival (BLACK_SEC ago), not yet on screen
        st = _fake_stream(black_since=now - webcam.BLACK_SEC - 1)
    monkeypatch.setattr(p, "_stream", lambda *a, **k: st)
    c = Canvas(448, 130)
    r = p._draw_cam(c, (0, 0, 448, 130), {"panel": "webcam_full", "cam": "iss"}, True, black_skips=True)
    assert r is None and not p._bad
    if why == "stalled":
        assert c.buf.any()                             # the map, not the frozen (here black) frame


def test_the_small_map_stays_inside_small_zones_or_is_left_out():
    from display.gfx import Canvas
    c = Canvas(448, 192)
    issmap.draw_small(c, (296, 100, 40, 20), _track(), T0 + 600)
    out = c.buf.copy()
    out[100:120, 296:336] = 0
    assert not out.any()
    for w, h in ((152, 130), (60, 40), (448, 192)):
        x, y, sw, sh = issmap.small_box(w, h, "br")
        assert sw == 0 or (x >= 1 and y >= 1 and x + sw < w and y + sh < h)
    assert issmap.render(0, 0, _track(), T0).shape == (0, 0, 3)


@pytest.mark.parametrize("w,h", [(448, 192), (448, 175), (448, 134), (448, 130), (295, 130), (152, 130)])
@pytest.mark.parametrize("reserve", [(0, 34), (0, 50), (30, 0)])
def test_the_iss_band_stays_clear_of_the_caption(w, h, reserve):
    for follow in (True, False):
        v = issmap.View(w, h, follow=follow, reserve=reserve)
        top, bot = float(v.y(issmap.ISS_LAT)), float(v.y(-issmap.ISS_LAT))
        assert top - 3 >= reserve[0] and bot + 3 <= h - reserve[1]
        assert v.y0 + v.mh >= h - 1 or v.bot == -90.0                       # the map runs on under the caption


def test_the_iss_is_never_drawn_under_the_caption():
    from display.gfx import Canvas
    cam = {"space": "iss", "label": "International Space Station", "overlay": {"font": "medium"}}
    data = {"track": [[time.time() - 6000 + i * 10, -51.5 + 103 * abs(((i / 553) % 2) - 1), (i * 0.6) % 360 - 180, 1]
                      for i in range(1300)]}
    img = webcam.render_preview(cam, None, (448, 134), elapsed=30, extra={"space": data}, show_map=True)
    from display.modules.webcam_caption import draw_caption
    box = draw_caption(Canvas(448, 134), (0, 0, 448, 134), cam, 30, webcam._preview_font, extra={"space": data})
    assert box
    gold = (img == issmap.PAL["G"]).all(axis=2)
    ys = np.flatnonzero(gold.any(axis=1))
    assert ys.size and ys.max() < box[1]                                   # the ISS is above the caption box
