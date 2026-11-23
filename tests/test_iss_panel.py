"""The ISS map as its own rotation entry (display/modules/iss_panel.py): its settings (modules.iss_map, or the ISS
webcam's until saved), drawing in every size, and the config check."""

import time
from types import SimpleNamespace

import pytest

from display.gfx import Canvas
from display.modules import iss_panel, issmap


@pytest.fixture(autouse=True)
def _no_hub(monkeypatch):
    monkeypatch.setattr(issmap, "live_from", lambda hub: None)


def test_until_saved_the_iss_webcam_lends_its_caption_and_map():
    mods = {"webcams": {"cams": {"a": {"label": "Street"},
                                 "iss": {"space": "iss", "label": "ISS", "overlay": {"corner": "tr"}, "map": {"fixed": True}}}}}
    s = iss_panel.settings_of(mods)
    assert s["label"] == "ISS" and s["overlay"]["corner"] == "tr" and s["map"]["fixed"] and s["space"] == "iss"
    own = iss_panel.settings_of({**mods, "iss_map": {"label": "Station", "map": {"fixed": False, "when_down": False}}})
    assert own["label"] == "Station" and not own["map"]["fixed"] and own["map"]["when_down"] is True
    assert iss_panel.settings_of({})["label"] == "International Space Station"


@pytest.mark.parametrize("zone", [(0, 0, 448, 192), (0, 45, 448, 130), (0, 0, 448, 175), (296, 45, 152, 130)])
def test_it_draws_the_map_and_caption_in_every_size(zone):
    now = time.time()
    data = {"track": [[now - 6000 + i * 10, 20.0, (i * 0.6) % 360 - 180, 1] for i in range(1300)]}
    p = iss_panel.IssMapPanel(SimpleNamespace(modules={"iss_map": {"label": "International Space Station"}}))
    p._iss, p._checked = data, now + 1e9
    c = Canvas(448, 192)
    assert p.draw(c, zone, {"panel": "iss_map_full"}, True) is None
    x, y, w, h = zone
    assert c.buf[y:y + h, x:x + w].any() and not c.buf[:, :x].any()
    gold = (c.buf == issmap.PAL["G"]).all(axis=2)
    assert gold.any()                                                   # the ISS is on it


def test_the_iss_map_settings_are_checked_in_the_config():
    from hub import config
    config._validate_iss_map({"label": "ISS", "overlay": {"corner": "tr"}, "map": {"fixed": True, "extras": {"flow": {"on": False}}}})
    for bad in ("x", {"label": 3}, {"map": {"fixed": "yes"}}, {"map": {"extras": {"nope": {}}}}, {"overlay": {"corner": "middle"}}):
        with pytest.raises(config.ConfigError):
            config._validate_iss_map(bad)
