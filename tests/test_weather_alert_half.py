"""The weather alert's half-width version: the facts and essentials it shows and where its map looks."""
import numpy as np

from display.modules import alert_brief
from display.modules.alert_samples import SAMPLES
from display.modules.weather_alert_half import _compass, _window, essentials


def test_compass_points():
    assert _compass(0, -10) == "N"
    assert _compass(10, 0) == "E"
    assert _compass(-10, 10) == "SW"


def test_essentials_are_short_and_whole_sentences():
    long_text = ("An air quality warning is in effect for the area.\n\n" +
                 " ".join(f"Stay indoors where the air is filtered number {i}." for i in range(20)))
    b = alert_brief.brief({"name": "air quality warning", "text": long_text})
    paras = essentials(b)
    assert paras[0]["style"] == "lead"
    advice = [p for p in paras if p["style"] == "advice"]
    assert advice and advice[0]["text"].count(".") <= 2       # two sentences at most, never cut
    assert sum(len(p["text"].split()) for p in paras) < 60


def test_essentials_of_every_sample():
    for s in SAMPLES.values():
        paras = essentials(alert_brief.brief(s))
        assert paras and paras[0]["style"] == "lead"


def _zone(mask, home):
    ys, xs = np.nonzero(mask)
    return {"img": np.zeros(mask.shape + (3,), np.uint8), "mask": mask, "home": home,
            "bbox": (xs.min(), ys.min(), xs.max(), ys.max()), "centre": (xs.mean(), ys.mean())}


def test_map_window_shows_a_far_area_not_home():
    mask = np.zeros((192, 448), bool)
    mask[150:190, 0:40] = True                                 # a small area in the far corner
    sx, sy = _window(_zone(mask, (300, 40)), 223, 120, home=False)
    assert sx <= 0 + 8 and sy + 120 >= 190 - 8                 # the whole area is in the window


def test_map_window_keeps_home_with_a_nearby_area():
    mask = np.zeros((192, 448), bool)
    mask[60:100, 200:260] = True
    sx, sy = _window(_zone(mask, (230, 120)), 223, 120, home=False)
    assert sx <= 200 and sx + 223 >= 260 and sy <= 60 and sy + 120 >= 120
