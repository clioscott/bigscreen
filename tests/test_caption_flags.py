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
    assert flagplaces.default_ids(cam) == ["ca", "ca-ns"]
    assert flagplaces.default_ids(cam, {"ca"}) == ["ca"]


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
    cam = {"label": "Toronto Skyline", "place": "Toronto, ON", "tz": "America/Toronto", "overlay": overlay, **(cam or {})}
    box = cap.draw_caption(c, (0, 0, 448, 192), cam, 1.0, _font, now_local=datetime.now().astimezone())
    return c.buf, box


def _where(buf, rgb):
    ys, xs = np.nonzero((buf == rgb).all(axis=2))
    return (ys.min(), ys.max(), xs.min(), xs.max()) if ys.size else None


def test_automatic_flags_sit_on_the_caption(flag_set):
    buf, box = _draw({"corner": "bl"})
    red, blue = _where(buf, (255, 0, 0)), _where(buf, (0, 0, 255))
    assert red and blue                                     # Canada at 23 px, Ontario at 18 px
    assert red[1] - red[0] == 22 and blue[1] - blue[0] == 17
    assert red[1] == blue[1]                                # bottoms lined up
    assert red[2] < blue[2]                                 # country first
    capt = _where(buf, (255, 255, 255))                     # the caption's text, below the flags
    assert red[1] < capt[0]


def test_top_corner_puts_them_under_the_caption(flag_set):
    buf, _ = _draw({"corner": "tr"})
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
    buf, _ = _draw({}, {"place": "Somewhere", "tz": ""})
    assert _where(buf, (255, 0, 0)) is None


def test_no_flag_set_draws_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(cf, "FLAG_DIRS", [str(tmp_path / "none")])
    cf._index.update(path=None, mtime=None, flags={})
    cap._layers.clear()
    assert cf.ids_for({"place": "Toronto, ON"}, cap.overlay_of({})) == []


def test_flag_settings_are_validated():
    from hub.config import ConfigError, _validate_overlay
    _validate_overlay("x", {"flags": {"on": True, "ids": ["ca", "ca-on"], "corner": "tr", "size": 18}})
    _validate_overlay("x", {"flags": {"ids": None}})
    for bad in ({"ids": "ca"}, {"ids": ["CA!"]}, {"corner": "middle"}, {"size": 20}, {"on": "yes"}, {"colour": 1},
                {"ids": ["ca"] * 7}):
        with pytest.raises(ConfigError):
            _validate_overlay("x", {"flags": bad})
