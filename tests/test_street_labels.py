"""DriveBC map street labels: short names, placed only beside straight E–W / N–S roads where they fit."""

import numpy as np

from hub.bc import trafficmap as tm


def test_short_names():
    assert tm.short_name("152 Street") == "152 St"
    assert tm.short_name("96 Avenue") == "96 Ave"
    assert tm.short_name("King George Boulevard") == "King George"
    assert tm.short_name("East Hastings Street") == "E Hastings St"
    assert tm.short_name("North Road") == "North Rd"
    assert tm.short_name("Number 3 Road") == "No 3 Rd"
    assert tm.short_name("Scott Road-120 Street") == "Scott Rd"
    assert tm.short_name("Mount Seymour Parkway") == "Mt Seymour Pkwy"
    assert tm.short_name("Highway 17") == ""                     # the shield names it
    assert tm.short_name("Trans-Canada Highway") == ""
    assert tm.short_name("Dewdney Trunk Road") == ""             # too long for a label


def _labels(lines, w=448, h=192, extra=()):
    roads = np.zeros((h, w), bool)
    named = {}
    for name, pts in lines:
        px = np.asarray(pts, dtype=float) + 0.5
        named[name] = [tm.ORDER.index("primary"), [px]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            n = max(abs(x1 - x0), abs(y1 - y0)) + 1
            roads[np.round(np.linspace(y0, y1, n)).astype(int), np.round(np.linspace(x0, x1, n)).astype(int)] = True
    for x, y in extra:
        roads[y, x] = True
    return tm.street_labels(w, h, named, roads, np.zeros((h, w), bool), [])


def test_a_horizontal_road_gets_a_label_beside_it():
    out = _labels([("96 Avenue", [(20, 100), (420, 100)])])
    assert [l["t"] for l in out] == ["96 Ave"]
    b = out[0]["cands"][0]
    assert b["v"] == 0 and b["y"] + b["h"] == 99                  # 1 px above the road


def test_a_vertical_road_gets_a_turned_label():
    out = _labels([("152 Street", [(200, 5), (200, 185)])])
    b = out[0]["cands"][0]
    assert b["v"] == 1 and b["w"] < b["h"] and b["x"] + b["w"] == 199


def test_a_diagonal_road_gets_a_label_in_its_angle():
    out = _labels([("Kingsway", [(20, 20), (200, 180)])])
    b = out[0]["cands"][0]
    assert b["v"] == 0
    box = np.zeros((192, 448), bool)
    box[b["y"]:b["y"] + b["h"], b["x"]:b["x"] + b["w"]] = True
    xs = np.arange(20, 201)
    road = np.zeros_like(box)
    road[np.round(20 + (xs - 20) * 160 / 180).astype(int), xs] = True
    from scipy import ndimage
    assert not (box & ndimage.binary_dilation(road, np.ones((3, 3), bool))).any()     # 1 px clear of the road
    assert (box & ndimage.binary_dilation(road, np.ones((5, 5), bool))).any()         # but right beside it


def test_a_diagonal_road_crowded_by_others_gets_no_label():
    near = [(x, y) for x in range(0, 448, 6) for y in range(0, 192)]          # a street every 6 px
    assert _labels([("Kingsway", [(20, 20), (200, 180)])], extra=near) == []


def test_no_label_where_it_does_not_fit_or_would_touch_another_road():
    assert _labels([("96 Avenue", [(100, 100), (122, 100)])]) == []          # too short for the name
    crossings = [(x, y) for x in range(24, 420, 12) for y in range(80, 120)]   # a street every 12 px
    assert _labels([("96 Avenue", [(20, 100), (420, 100)])], extra=crossings) == []
