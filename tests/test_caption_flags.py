"""Caption flags: where a webcam is (common/flagplaces.py), where the row of flags goes, and the config they take."""

import json
from datetime import datetime

import numpy as np
import pytest
from PIL import Image

from common import flagplaces
from display.modules import caption_flags as cf
from display.modules import webcam_caption as cap


@pytest.mark.parametrize("cam, want", [
    ({"place": "Toronto, ON", "tz": "America/Toronto"}, ("ca", "ON")),
    ({"place": "Los Angeles, CA", "tz": "America/Los_Angeles"}, ("us", "CA")),      # California, not Canada
    ({"place": "Boston, MA", "tz": "America/New_York"}, ("us", "MA")),
    ({"place": "Dublin, Ireland", "tz": "Europe/London"}, ("ie", "")),               # the place beats the zone
    ({"place": "White Rock", "tz": "America/Vancouver"}, ("ca", "BC")),               # no place code: the zone
    ({"place": "Osaka", "tz": "Asia/Tokyo"}, ("jp", "27")),
    ({"place": "Shibuya-ku", "tz": "Asia/Tokyo", "lat": 35.66, "lon": 139.70}, ("jp", "13")),
    ({"place": "Somewhere", "tz": "Asia/Tokyo", "lat": 43.06, "lon": 141.35}, ("jp", "")),   # outside central Tokyo
    ({"place": "Kilpisjärvi, Finland", "tz": "Europe/Helsinki"}, ("fi", "")),
    ({"place": "Halifax", "tz": "America/Halifax", "country": "ca", "region": "pe"}, ("ca", "PE")),   # set by hand
    ({"place": "Berlin, DE", "tz": "Europe/Berlin"}, ("de", "")),                    # a country code, not Delaware
    ({"place": "Amsterdam, NL", "tz": "Europe/Amsterdam"}, ("", "")),                # not Newfoundland
    ({"place": "Lima, PE", "tz": "America/Lima"}, ("", "")),                         # not Prince Edward Island
    ({"place": "Indianapolis, IN", "tz": "America/Indiana/Indianapolis"}, ("us", "IN")),
    ({"place": "Sapporo", "tz": "Asia/Tokyo", "region": "1"}, ("jp", "01")),          # prefectures are two digits
    ({"label": "International Space Station", "space": "iss"}, ("", "")),
    ({"place": "", "tz": ""}, ("", "")),
])
def test_where_a_webcam_is(cam, want):
    assert flagplaces.locate(cam) == want


def test_default_ids_only_lists_flags_that_exist():
    cam = {"place": "Peggys Cove, NS", "tz": "America/Halifax"}
    assert flagplaces.default_ids(cam) == ["ca-ns"]                  # the province, not Canada
    assert flagplaces.default_ids(cam, {"ca"}) == ["ca"]             # no Nova Scotia flag: Canada's instead


def _entry(name, group, country, region=""):
    return {"name": name, "group": group, "country": country, "region": region}


INDEX = {"ca": _entry("Canada", "country", "ca"), "ca-bc": _entry("British Columbia", "province", "ca", "BC"),
         "ca-on": _entry("Ontario", "province", "ca", "ON"),
         "ca-bc-north-vancouver": _entry("North Vancouver (City)", "city", "ca", "BC"),
         "ca-bc-north-vancouver-district": _entry("North Vancouver (District)", "city", "ca", "BC"),
         "ca-bc-white-rock": _entry("White Rock", "city", "ca", "BC"),
         "ca-on-niagara-falls-logo": _entry("Niagara Falls (logo)", "city", "ca", "ON"),
         "ca-on-niagara-falls": _entry("Niagara Falls", "city", "ca", "ON"),
         "us": _entry("United States", "country", "us"), "us-ma": _entry("Massachusetts", "state", "us", "MA"),
         "us-nv": _entry("Nevada", "state", "us", "NV"), "us-ma-boston": _entry("Boston", "city", "us", "MA"),
         "jp": _entry("Japan", "country", "jp"), "jp-13": _entry("Tokyo", "prefecture", "jp", "13"),
         "jp-27": _entry("Osaka", "prefecture", "jp", "27"), "jp-13-koto": _entry("Kōtō", "city", "jp", "13"),
         "jp-13-shibuya": _entry("Shibuya", "city", "jp", "13"),
         "jp-13-shibuya-emblem": _entry("Shibuya", "emblem", "jp", "13"),
         "jp-27-osaka-city": _entry("Osaka", "city", "jp", "27"),
         "ie": _entry("Ireland", "country", "ie"), "ie-dublin": _entry("Dublin", "city", "ie"),
         "fi": _entry("Finland", "country", "fi"), "gb": _entry("United Kingdom", "country", "gb"),
         "gb-london": _entry("London", "city", "gb")}


@pytest.mark.parametrize("cam, want", [
    ({"place": "White Rock", "tz": "America/Vancouver"}, ["ca-bc", "ca-bc-white-rock"]),
    ({"place": "North Vancouver, BC", "tz": "America/Vancouver"}, ["ca-bc", "ca-bc-north-vancouver"]),
    ({"place": "Niagara Falls, ON", "tz": "America/Toronto"}, ["ca-on", "ca-on-niagara-falls"]),   # not the logo
    ({"place": "Main/Terminal", "tz": "America/Vancouver"}, ["ca-bc"]),          # no city flag: the province alone
    ({"place": "Boston, MA", "tz": "America/New_York"}, ["us-ma", "us-ma-boston"]),
    ({"place": "Las Vegas, NV", "tz": "America/Los_Angeles"}, ["us-nv"]),
    ({"place": "Shibuya-ku", "tz": "Asia/Tokyo"}, ["jp-13", "jp-13-shibuya"]),   # the city flag, not the emblem
    ({"place": "Koto-ku", "tz": "Asia/Tokyo"}, ["jp-13", "jp-13-koto"]),
    ({"place": "Osaka", "tz": "Asia/Tokyo"}, ["jp-27", "jp-27-osaka-city"]),
    ({"place": "Tokyo Station", "tz": "Asia/Tokyo"}, ["jp-13"]),
    ({"place": "Somewhere", "tz": "Asia/Tokyo", "lat": 43.06, "lon": 141.35}, ["jp"]),   # prefecture unknown
    ({"place": "Dublin, Ireland", "tz": "Europe/London"}, ["ie", "ie-dublin"]),
    ({"place": "Kilpisjärvi, Finland", "tz": "Europe/Helsinki"}, ["fi"]),
    ({"place": "London, UK", "tz": "Europe/London"}, ["gb"]),                    # elsewhere: the country alone
])
def test_automatic_flags(cam, want):
    assert flagplaces.default_ids(cam, INDEX) == want


@pytest.fixture
def flag_set(tmp_path, monkeypatch):
    """Two flags: a 23 px country (solid red, 30 wide) and an 18 px province (solid blue, 26 wide)."""
    def png(name, w, h, rgb):
        a = np.zeros((h, w, 4), np.uint8)
        a[..., :3], a[..., 3] = rgb, 255
        Image.fromarray(a).save(tmp_path / name)
    png("ca_23.png", 30, 23, (255, 0, 0))
    png("ca-on_18.png", 26, 18, (0, 0, 255))
    png("ca-on_23.png", 32, 23, (0, 0, 200))
    (tmp_path / "index.json").write_text(json.dumps({"version": 1, "flags": {
        "ca": {"name": "Canada", "group": "country", "country": "ca", "region": "", "default": 23, "files": {"23": "ca_23.png"}},
        "ca-on": {"name": "Ontario", "group": "province", "country": "ca", "region": "ON", "default": 18,
                  "files": {"18": "ca-on_18.png", "23": "ca-on_23.png"}}}}))
    monkeypatch.setattr(cf, "FLAG_DIRS", [str(tmp_path)])
    cf._index.update(path=None, mtime=None, flags={})
    cap._layers.clear()
    return tmp_path


def _font(path):
    from display.gfx import graphics
    f = graphics.Font()
    f.LoadFont(cap.FONTS.get(path, path))
    return f


def _draw(overlay, cam=None):
    from display.gfx import Canvas
    c = Canvas(448, 192)
    c.buf[:] = 60
    # solid, so the colours are exact; Canada and Ontario at their own sizes in the top left unless a test says
    # otherwise ("ids": None: the automatic ones)
    overlay = {**overlay, "flags": {"opacity": 1, "ids": ["ca", "ca-on"], "size": "auto", "corner": "tl",
                                    **overlay.get("flags", {})}}
    cam = {"label": "Toronto Skyline", "place": "Toronto, ON", "tz": "America/Toronto", "overlay": overlay, **(cam or {})}
    box = cap.draw_caption(c, (0, 0, 448, 192), cam, 1.0, _font, now_local=datetime.now().astimezone())
    return c.buf, box


def _where(buf, rgb):
    ys, xs = np.nonzero((buf == rgb).all(axis=2))
    return (ys.min(), ys.max(), xs.min(), xs.max()) if ys.size else None


def test_automatic_flags_default_to_18px_bottom_right(flag_set):
    buf, _ = _draw({"corner": "tl", "flags": dict(cf.DEFAULT_FLAGS, opacity=1)})
    assert _where(buf, (255, 0, 0)) is None                 # Ontario only, no Canada
    blue = _where(buf, (0, 0, 255))                         # its 18 px version
    assert blue[1] - blue[0] == 17
    assert blue[1] >= 186 and blue[3] >= 440                # the bottom-right corner


def test_a_webcam_with_no_flag_settings_gets_the_bottom_right():
    from display.modules import caption_flags
    o = cap.overlay_of({"overlay": {"corner": "tl"}})
    assert caption_flags.flags_of(o)["corner"] == "br" and caption_flags.flags_of(o)["size"] == 18
    assert caption_flags.flags_of(cap.overlay_of({}))["corner"] == "br"


def test_automatic_flags_sit_on_the_caption(flag_set):
    buf, box = _draw({"corner": "bl", "flags": {"corner": "auto"}})
    red, blue = _where(buf, (255, 0, 0)), _where(buf, (0, 0, 255))
    assert red and blue                                     # Canada at 23 px, Ontario at 18 px
    assert red[1] - red[0] == 22 and blue[1] - blue[0] == 17
    assert red[1] == blue[1]                                # bottoms lined up
    assert red[2] < blue[2]                                 # country first
    capt = _where(buf, (255, 255, 255))                     # the caption's text, below the flags
    assert red[1] < capt[0]


def test_top_corner_puts_them_under_the_caption(flag_set):
    buf, _ = _draw({"corner": "tr", "flags": {"corner": "auto"}})
    red = _where(buf, (255, 0, 0))
    capt = _where(buf, (255, 255, 255))
    assert red[0] > capt[0] and red[3] > 400                 # below the caption, on the right


def test_own_corner_and_chosen_flags_and_size(flag_set):
    buf, _ = _draw({"corner": "bl", "flags": {"corner": "tr", "ids": ["ca-on"], "size": 23}})
    assert _where(buf, (255, 0, 0)) is None                 # only the chosen flag
    dark = _where(buf, (0, 0, 200))                         # Ontario's 23 px version
    assert dark[0] <= 4 and dark[3] >= 440


def test_flags_off_or_unknown_place_show_nothing(flag_set):
    buf, _ = _draw({"flags": {"on": False}})
    assert _where(buf, (255, 0, 0)) is None
    buf, _ = _draw({"flags": {"ids": None}}, {"place": "Somewhere", "tz": ""})
    assert _where(buf, (255, 0, 0)) is None


def test_opacity_lets_the_video_show_through(flag_set):
    buf, _ = _draw({"corner": "bl", "flags": {"opacity": 0.5}})
    assert _where(buf, (255, 0, 0)) is None                 # never solid red
    assert _where(buf, (158, 30, 30))                       # half red, half the frame's grey (60)


@pytest.mark.parametrize("shape, shown", [("full", True), ("tall", True), ("left", False), ("right", False),
                                          ("wall", False)])
def test_flags_only_at_full_width_and_above_the_clock(flag_set, shape, shown):
    cam = {"place": "Toronto, ON", "tz": "America/Toronto", "overlay": {"flags": {"ids": ["ca-on"]}},
           "overlays": {"side": {"flags": {"ids": ["ca-on"]}}}}
    view = cap.for_shape(cam, shape)
    assert bool(cf.ids_for(view, cap.overlay_of(view))) is shown


def test_no_flag_set_draws_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "FLAG_DIRS", [str(tmp_path / "none")])
    cf._index.update(path=None, mtime=None, flags={})
    cap._layers.clear()
    assert cf.ids_for({"place": "Toronto, ON"}, cap.overlay_of({})) == []


def test_flag_settings_are_validated():
    from hub.config import ConfigError, _validate_overlay
    _validate_overlay("x", {"flags": {"on": True, "ids": ["ca", "ca-on"], "corner": "tr", "size": 18}})
    _validate_overlay("x", {"flags": {"ids": None, "opacity": 0.35, "corner": "left"}})
    for bad in ({"ids": "ca"}, {"ids": ["CA!"]}, {"corner": "middle"}, {"size": 20}, {"on": "yes"}, {"colour": 1},
                {"ids": ["ca"] * 7}, {"corner": "above"}, {"opacity": 0}, {"opacity": 1.5}, {"opacity": True}):
        with pytest.raises(ConfigError):
            _validate_overlay("x", {"flags": bad})


def test_right_of_the_caption_lines_up_with_its_bottom(flag_set):
    buf, box = _draw({"corner": "bl", "flags": {"corner": "right"}})
    red = _where(buf, (255, 0, 0))
    capt = _where(buf, (255, 255, 255))
    assert red[2] > box[2] - 70 and red[2] > capt[3]        # to the right of the caption's text
    assert abs(red[1] - (box[3] - 2)) <= 1                  # bottoms together (inside the 1 px outline)


def test_left_of_a_caption_against_the_left_edge_goes_above_it(flag_set):
    buf, _ = _draw({"corner": "bl", "flags": {"corner": "left"}})
    red = _where(buf, (255, 0, 0))
    capt = _where(buf, (255, 255, 255))
    assert red[1] < capt[0]                                 # no room on the left: stacked above instead


def test_left_of_a_caption_on_the_right(flag_set):
    buf, box = _draw({"corner": "tr", "flags": {"corner": "left"}})
    blue = _where(buf, (0, 0, 255))
    capt = _where(buf, (255, 255, 255))
    assert blue[3] < capt[2]                                # left of the caption's text
    assert _where(buf, (255, 0, 0))[0] <= 4                 # tops together, at the top


def test_beside_keeps_clear_of_a_wide_japanese_caption(flag_set):
    jp = {"on": True, "text": "歌舞伎町一番街のとても長い日本語のキャプション"}
    buf, box = _draw({"corner": "bl", "jp_caption": jp, "flags": {"corner": "right"}})
    red = _where(buf, (255, 0, 0))
    _, plain = _draw({"corner": "bl", "flags": {"corner": "right"}})
    assert red[2] > plain[2]                                # pushed past the Japanese caption's right edge
