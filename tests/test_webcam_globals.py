"""Settings made once for every webcam (common/webcam_globals.py) and where the wall and the hub use them."""

import pytest

from common import webcam_globals as wg

SRC = {"type": "youtube", "url": "https://www.youtube.com/watch?v=abcdefghijk"}


def _cfg(**groups):
    cams = {
        "a": {"label": "A", "source": SRC, "colour": {"sharpen": 0, "brightness": 1.3},
              "overlay": {"corner": "bl", "font": "medium", "custom_text": "mine", "flags": {"on": True, "ids": ["ca"], "corner": "br"}},
              "overlays": {"tall": {"corner": "tr", "custom_text": "tall"}}},
        "b": {"label": "B", "source": SRC, "quality": "max", "own": ["colour", "quality", "sec"],
              "colour": {"sharpen": 0.5}, "overlay": {"corner": "tl"}},
    }
    return {"cams": cams, "playlist": [{"cam": "a", "sec": 40}, {"cam": "b", "sec": 90}], "global": groups}


def test_nothing_on_leaves_every_webcam_as_it_is():
    cfg = _cfg(colour={"on": False, "sharpen": 0.3})
    assert wg.cams_of(cfg)["a"] is cfg["cams"]["a"]           # the same object: caches keyed on it keep working
    assert wg.cams_of({"cams": cfg["cams"]}) is cfg["cams"]
    assert wg.effective(cfg["cams"]["a"], {}) is cfg["cams"]["a"]


def test_a_group_replaces_only_its_own_values():
    cfg = _cfg(colour={"on": True, "sharpen": 0.3, "contrast": 1.2})
    a = wg.cams_of(cfg)["a"]
    assert a["colour"] == {"sharpen": 0.3, "brightness": 1.3, "contrast": 1.2}     # brightness wasn't set for all
    assert cfg["cams"]["a"]["colour"] == {"sharpen": 0, "brightness": 1.3}          # nothing stored is changed
    assert a["overlay"] is cfg["cams"]["a"]["overlay"]


def test_a_webcam_can_keep_its_own():
    cfg = _cfg(colour={"on": True, "sharpen": 0.3}, quality={"on": True, "up_to": 1440})
    b = wg.cams_of(cfg)["b"]
    assert b is cfg["cams"]["b"] and b["quality"] == "max" and b["colour"] == {"sharpen": 0.5}
    assert wg.applies(cfg["global"], "colour", cfg["cams"]["a"]) and not wg.applies(cfg["global"], "colour", cfg["cams"]["b"])


def test_caption_and_flags_move_but_keep_their_content():
    cfg = _cfg(caption={"on": True, "corner": "br", "font": "large", "opacity": 0.4},
               flags={"on": True, "show": True, "corner": "tl", "size": 23, "opacity": 0.8})
    a = wg.cams_of(cfg)["a"]
    assert a["overlay"]["corner"] == "br" and a["overlay"]["font"] == "large" and a["overlay"]["opacity"] == 0.4
    assert a["overlay"]["custom_text"] == "mine"                                       # what it says stays its own
    assert a["overlay"]["flags"] == {"on": True, "ids": ["ca"], "corner": "tl", "size": 23, "opacity": 0.8}
    assert a["overlays"]["tall"]["corner"] == "br" and a["overlays"]["tall"]["custom_text"] == "tall"   # a size's own caption too
    off = wg.cams_of(_cfg(flags={"on": True, "show": False}))["a"]
    assert off["overlay"]["flags"]["on"] is False and off["overlay"]["flags"]["ids"] == ["ca"]


def test_the_caption_the_wall_draws_follows():
    from display.modules import webcam_caption
    cfg = _cfg(caption={"on": True, "corner": "tr", "uppercase": True})
    view = webcam_caption.for_shape(wg.cams_of(cfg)["a"], "full")
    ov = webcam_caption.overlay_of(view)
    assert ov["corner"] == "tr" and ov["uppercase"] is True
    tall = webcam_caption.overlay_of(webcam_caption.for_shape(wg.cams_of(cfg)["a"], "tall"))
    assert tall["corner"] == "tr"


def test_seconds_in_the_list():
    from display.modules import webcam
    cfg = _cfg(sec={"on": True, "value": 25})
    assert webcam.playlist_of(cfg) == [("a", 25.0), ("b", 90.0)]                       # b keeps its own
    assert webcam.playlist_of(_cfg(sec={"on": False, "value": 25})) == [("a", 40.0), ("b", 90.0)]
    assert webcam.playlist_of(_cfg()) == [("a", 40.0), ("b", 90.0)]


def test_the_stream_query_carries_the_global_colour():
    from display.modules import webcam
    cfg = _cfg(colour={"on": True, "sharpen": 0.3, "saturation": 1.5})
    q = webcam.stream_query(wg.cams_of(cfg)["a"], "full", 448, 134)
    assert q["sh"] == 0.3 and q["s"] == 1.5 and q["b"] == 1.3


def test_resolution_up_to_a_limit():
    from hub import webcams as wc
    cfg = _cfg(quality={"on": True, "up_to": 1440})
    assert wg.cams_of(cfg)["a"]["quality"] == "upto1440"
    assert wc.cap_of("upto1440") == 1440 and wc.cap_of("max") == 0 and wc.cap_of(1080) == 0 and wc.cap_of("uptox") == 0
    fmts = [{"url": "u", "w": w, "h": h, "fps": 30.0, "vcodec": "avc1", "tbr": 1, "audio": False, "dash": False}
            for w, h in ((1280, 720), (1920, 1080), (3840, 2160))]
    wants = [(448, 175, None)]
    pick = lambda q: wc.choose_format(fmts, wants, hw=True, quality=q)["h"]
    assert pick("upto1440") == 1080                 # no 1440p on this stream: its best below, never the 4K above
    assert pick("upto2160") == 2160 and pick("upto1080") == 1080
    assert pick(None) == 1080 and pick("max") == 2160                                  # unchanged
    assert wc.forced("upto1440")
    assert wc.quality_of(["upto1440", None]) == "upto1440"
    assert wc.quality_of(["upto1440", "max"]) == "max"
    assert wc.quality_of(["upto1080", 2160]) == 2160      # a webcam on the same stream that asks for more gets it
    assert wc.quality_of([None, "auto"]) == "auto" and wc.quality_of([720, 1080]) == 1080


def test_config_accepts_and_checks_the_settings():
    from hub import config
    cams = {"a": {"label": "A", "source": SRC, "fps": 30, "own": ["colour"]}}
    ok = {"cams": cams, "global": {"colour": {"on": True, "sharpen": 0.2}, "sec": {"on": True, "value": 30},
                                   "caption": {"on": False, "corner": "bl"}, "flags": {"on": True, "show": False},
                                   "quality": {"on": True, "up_to": 2160}}}
    config._validate_webcams(ok)
    for bad in ({"colour": {"on": True, "sharpen": 9}}, {"sec": {"on": True, "value": 1}}, {"quality": {"on": True, "up_to": 999}},
                {"nonsense": {"on": True}}, {"colour": {"on": "yes"}}, {"colour": "on"}):
        with pytest.raises(config.ConfigError):
            config._validate_webcams({"cams": cams, "global": bad})
    with pytest.raises(config.ConfigError):
        config._validate_webcams({"cams": {"a": {**cams["a"], "own": ["everything"]}}})
    for bad in ({"caption": {"on": True, "font": ["x"]}}, {"flags": {"on": True, "corner": {"a": 1}}},
                {"sec": {"on": True, "value": None}}):
        with pytest.raises(config.ConfigError):
            config._validate_webcams({"cams": cams, "global": bad})
    with pytest.raises(config.ConfigError):
        config._validate_webcams({"cams": {"a": {**cams["a"], "own": [{}]}}})
    config._validate_webcams({"cams": cams, "global": {"sec": {"on": False, "value": None}}})      # off: not needed


def test_the_space_maps_small_video_follows_too():
    from display.modules import iss_panel
    cfg = _cfg(colour={"on": True, "sharpen": 0.3})
    panel = iss_panel.IssMapPanel.__new__(iss_panel.IssMapPanel)
    import inspect
    assert "webcam_globals.cams_of" in inspect.getsource(iss_panel.IssMapPanel.__init__)
    assert wg.cams_of(cfg)["a"]["colour"]["sharpen"] == 0.3 and panel is not None
