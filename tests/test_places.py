"""Reference places on the alert maps: home when it fits, else the biggest nearby city, else the nearest place."""

import math

import numpy as np

from common import places

HOME = (49.1442, -123.0918)


def _ring(lat, lon, rkm, n=14):
    return [(lat + rkm / 111 * math.sin(2 * math.pi * i / n), lon + rkm / 72 * math.cos(2 * math.pi * i / n))
            for i in range(n + 1)]


def _kpp(rkm):
    return max(0.05, 2 * rkm * 1.6 / 140)


def test_home_when_it_fits():
    _, ref = places.zoom_area(_ring(49.31, -122.74, 2), 170, 150, HOME, _kpp(2))
    assert ref["home"]


def test_city_when_home_is_too_far():
    area, ref = places.zoom_area(_ring(49.93, -119.52, 4), 170, 150, HOME, _kpp(4))
    assert ref["name"] == "Kelowna" and not ref["home"]
    assert area["km_per_px"] <= _kpp(4) * places.MAX_ZOOM_OUT + 1e-3


def test_nearby_small_town_beats_a_bigger_one_needing_a_big_zoom_out():
    _, ref = places.zoom_area(_ring(50.75, -121.9, 5), 170, 150, HOME, _kpp(5))
    assert ref["name"] == "Lillooet"


def test_bigger_tier_wins_when_it_fits():
    cost = {(49.888, -119.496): 9.0, (49.8, -119.5): 1.0}
    pick = places.reference(lambda p: cost.get(p), None, (49.85, -119.5))
    assert pick["name"] == "Kelowna"


def test_nearest_place_when_nothing_fits():
    pick = places.reference(lambda p: None, HOME, (49.31, -122.74))
    assert pick["home"] and pick["name"] == "Vancouver"
    pick = places.reference(lambda p: None, HOME, (49.40, -121.40))
    assert pick["name"] == "Hope"


def test_frame_crop_keeps_area_and_reference_in_window():
    from display.modules import bc_kit as k
    from hub.weather.geo import View
    v = View({"fit": {"nw": [51.0, -125.4], "se": [48.3, -118.6]}}, 448, 192)
    mp = k.Map(np.zeros((v.h, v.w, 3), np.uint8), {"merc": list(v.merc), "mpp": v.mpp, "km_per_px": v.km_per_px})
    w, h = 223, 100
    sx, sy, ref = k.frame_crop(mp, w, h, [(49.90, -119.58)], HOME)
    assert ref["name"] == "Kelowna" or ref["home"]
    for la, lo in ((49.90, -119.58), (ref["lat"], ref["lon"])):
        x, y = mp.project(la, lo)
        assert 0 <= x - sx < w and 0 <= y - sy < h
    assert k.frame_crop(mp, w, h, [(49.90, -119.58)], HOME, key="x") == k.frame_crop(mp, w, h, [], HOME, key="x")
