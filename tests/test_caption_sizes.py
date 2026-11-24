"""A caption of its own per size on the wall (webcam_caption.for_shape, cams.<id>.overlays)."""

import pytest

from display.modules import webcam_caption as cap

CAM = {"label": "Toronto Skyline", "place": "Toronto, ON", "tz": "America/Toronto",
       "overlay": {"font": "large", "flags": {"size": 23}},
       "overlays": {"side": {"font": "small", "flags": {"size": 18, "opacity": 0.5}}}}


def test_each_size_gets_its_own_caption_or_the_shared_one():
    for shape in ("left", "right"):                            # the two sides share one
        assert cap.overlay_of(cap.for_shape(CAM, shape))["font"] == "small"
    for shape in ("full", "tall", "wall", None):
        assert cap.overlay_of(cap.for_shape(CAM, shape))["font"] == "large"
    assert cap.for_shape({"label": "x"}, "left") == {"label": "x", "_size": "side"}
    assert cap.for_shape({"label": "x"}, None) == {"label": "x"}


def test_the_sized_view_is_cached_until_the_settings_change():
    a = cap.for_shape(CAM, "left")
    assert cap.for_shape(CAM, "right") is a                    # same object: the caption's caches keep hitting
    cam2 = {**CAM, "overlays": {"side": {"font": "medium"}}}
    assert cap.overlay_of(cap.for_shape(cam2, "left"))["font"] == "medium"


def test_the_whole_cam_stays_but_the_caption():
    v = cap.for_shape(CAM, "left")
    assert v["label"] == CAM["label"] and v["overlay"] is CAM["overlays"]["side"]


def test_sizes_are_validated():
    from hub.config import ConfigError, _validate_overlays
    _validate_overlays("x", None)
    _validate_overlays("x", {"side": {"font": "small"}, "wall": {}})
    for bad in ([], {"left": {}}, {"side": "small"}, {"side": {"flags": {"size": 12}}}):
        with pytest.raises(ConfigError):
            _validate_overlays("x", bad)
