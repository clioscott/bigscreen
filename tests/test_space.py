"""A webcam in space: the ISS's orbit (hub/jobs/iss.py), its caption sections (display/modules/space.py) and the
pixel flags drawn inside text (display/modules/flags.py, display/gfx.py COLOR_GLYPHS)."""

import math

import numpy as np
import pytest

from display import gfx
from display.modules import space
from display.modules.flags import flag_char
from hub.jobs import iss


def _data(t0):
    track = [[t0 + i * 10, -17.0 + i * 0.5, 100.0 + i * 0.5, 1 if i < 30 else 0] for i in range(120)]
    return {"track": track, "events": iss.events(track),
            "over": [[t0, "Over the South Pacific"], [t0 + 600, "Over Chile"]],
            "tz": [[t0, "Pacific/Auckland"]],
            "crew": {"count": 7, "by": [["ru", 3], ["us", 3], ["ca", 1]]},
            "mission": {"name": "Expedition 75", "start": t0 - 119.5 * 86400}}


def test_a_webcam_in_space_says_what_its_over_its_next_sunset_the_time_below_and_who_is_aboard():
    t0 = 1_795_000_000
    d = _data(t0)
    show = {k: True for k in space.KEYS}
    out = space.sections(d, {"time_format": "24h", "crew_flags": True, "mission_days": True}, show, now=t0 + 100 + space.LAG)
    assert out["over"] == "Over the South Pacific"
    assert out["orbit"] == "Sunset in 3m20s"                               # the shadow at t0 + 300, seen at t0 + 100
    assert out["below"].endswith(" below") and len(out["below"]) == len("00:00 below")
    assert out["crew"] == f"3{flag_char('ru')} 3{flag_char('us')} 1{flag_char('ca')}"   # just the counts and flags
    assert out["mission"] == "Expedition 75 · day 120"
    lat, lon = space.position(d["track"], t0 + 105)
    assert abs(lat - (-17.0 + 10.5 * 0.5)) < 1e-9 and abs(lon - (100.0 + 10.5 * 0.5)) < 1e-9
    assert out["coords"] == "12.00°S 105.00°E"                             # (t0 + 100: 10 points in)
    plain = space.sections(d, {"crew_flags": False}, show, now=t0 + 700 + space.LAG)
    assert plain["over"] == "Over Chile" and plain["crew"] == "7 aboard" and plain["mission"] == "Expedition 75"


def test_coordinates_go_smoothly_across_the_date_line():
    track = [[0, 0.0, 179.5, 1], [10, 0.5, -179.5, 1]]
    lat, lon = space.position(track, 5)
    assert abs(lon - 180.0) < 1e-9 or abs(lon + 180.0) < 1e-9


def test_old_iss_data_shows_nothing_rather_than_stale_numbers():
    d = _data(1_795_000_000)
    out = space.sections(d, {}, {k: True for k in space.KEYS}, now=1_795_000_000 + 4 * 3600)
    assert all(v == "" for v in out.values())


def test_flags_are_drawn_in_their_colours_inside_text_and_take_their_room():
    f = gfx.Font()
    f.LoadFont("fonts/alert/6x10.bdf")
    c = gfx.Canvas(40, 12)
    text = "3" + flag_char("ru") + "1"
    w = gfx.DrawText(c, f, 0, 9, gfx.Color(255, 255, 255), text)
    assert w == f.CharacterWidth(ord("3")) + 10 + f.CharacterWidth(ord("1"))   # 9 px and a gap
    x = f.CharacterWidth(ord("3"))
    flag = c.buf[9 - 6:9, x:x + 9]
    assert tuple(flag[0, 0]) == (225, 225, 225) and tuple(flag[5, 0]) == (215, 30, 40)   # white over red: Russia
    assert flag_char("zz") == ""
    assert flag_char("eu") and flag_char(iss.PAD_FLAG["gf"]) == flag_char("eu")   # launches from Kourou get one


def test_the_orbit_ends_up_in_sunlight_or_shadow_and_over_the_right_place():
    assert iss.sunlit((7000.0, 0, 0), (1.0, 0, 0)) is True                  # between the Earth and the Sun
    assert iss.sunlit((-7000.0, 0, 0), (1.0, 0, 0)) is False                # behind the Earth
    assert iss.sunlit((-7000.0, 7000.0, 0), (1.0, 0, 0)) is True            # beside it, past the shadow
    assert iss.events([[0, 0, 0, 1], [10, 0, 0, 0], [20, 0, 0, 1]]) == [[10, "sunset"], [20, "sunrise"]]
    assert iss.sea("SOUTH PACIFIC OCEAN") == "the South Pacific" and iss.sea("Bay of Bengal") == "the Bay of Bengal"


def test_the_iss_track_is_where_a_real_orbit_puts_it():
    pytest.importorskip("sgp4")
    l1 = "1 25544U 98067A   26325.84919917  .00010277  00000+0  19075-3 0  9997"
    l2 = "2 25544  51.6320 239.8703 0007384  51.4158 308.7491 15.49540305596195"
    epoch = 1795304537.0                                                     # 2026 day 325.849
    tr = iss.track(l1, l2, epoch, horizon=5600, step=60)
    assert len(tr) > 90 and all(abs(r[1]) <= 52.0 for r in tr)              # 51.6 deg orbit: ~51.8 on the ground
    ev = iss.events(tr)
    assert 1 <= sum(1 for e in ev if e[1] == "sunset") <= 2                  # about one sunset a 92-minute orbit


def test_the_iss_file_already_covers_what_the_wall_shows_when_it_lands(monkeypatch, tmp_path):
    from hub import paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(iss, "elements", lambda: ("l1", "l2"))
    monkeypatch.setattr(iss, "track", lambda l1, l2, t0, horizon=iss.HORIZON, step=iss.STEP:
                        [[t0 + i * 10, 0.0, 0.0, 1] for i in range(int(horizon // 10))])
    monkeypatch.setattr(iss, "Below", lambda: type("B", (), {"name": lambda s, a, b: "Over x", "zone": lambda s, a, b: "UTC"})())
    monkeypatch.setattr(iss, "crew", lambda: (None, None))
    import json
    import time
    iss.run({"modules": {"webcams": {"cams": {"x": {"space": "iss"}}}}})
    d = json.loads((tmp_path / "iss.json").read_text())
    out = space.sections(d, {}, {k: True for k in space.KEYS}, now=time.time())
    assert out["over"] == "Over x" and out["coords"]                        # (the video is 35 s old)


def test_the_countdown_and_the_coordinates_are_written_as_chosen():
    assert space.countdown(1090) == "18m10s" and space.countdown(4020) == "1h07m" and space.countdown(-5) == "0m00s"
    assert space.coords(-12.3456, -145.6789) == "12.35°S 145.68°W"
    assert space.coords(-12.3456, -145.6789, "signed", 3) == "-12.346°, -145.679°"
    assert space.coords(12.35, 145.68, "dms", 0) == "12°21'00\"N 145°40'48\"E"
    assert space.coords(12.35, 145.68, "nsew", 0) == "12°N 146°E"


def test_a_frozen_or_plain_picture_counts_as_down_for_webcams_that_ask():
    from display.modules import webcam as wm
    rng = np.random.default_rng(4)
    slate = rng.integers(0, 255, (134, 448, 3), np.uint8)                 # a still (NASA's loss-of-signal card)
    live = np.roll(slate, 3, axis=1)                                      # the picture moving
    sub = lambda f: f[::4, ::4].astype(np.int16)
    assert wm.is_frozen(sub(slate), sub(slate)) and not wm.is_frozen(sub(live), sub(slate))
    assert wm.is_flat(np.full((134, 448, 3), (20, 40, 160), np.uint8)) and not wm.is_flat(slate)
    assert wm.skips_plain({"space": "iss"}) and not wm.skips_plain({}) and not wm.skips_plain({"space": "iss", "skip_plain": False})
    st = wm.Stream.__new__(wm.Stream)
    st.skip_plain = True
    assert st.blank(slate) is False and st.blank(slate) is True             # a second later, unchanged: frozen
    st.skip_plain = False
    st._last_sub = None
    assert st.blank(slate) is False and st.blank(slate) is False            # (only for webcams that ask)


def test_a_webcam_in_space_without_its_map_is_skipped_unless_its_whole_turn_is_in_daylight(monkeypatch):
    from display.modules import webcam as wm
    t0 = 1_795_000_000
    d = _data(t0)                                                        # lit for t0 .. t0 + 290, then shadow
    assert space.in_daylight(d, t0, t0 + 200) is True
    assert space.in_daylight(d, t0 + 250, t0 + 330) is False            # sunset during the turn
    assert space.in_daylight(d, t0 - 50, t0 + 10) is None               # (before the track: unknown)
    p = wm.WebcamPanel.__new__(wm.WebcamPanel)
    nomap = {"when_down": False}                                         # (with its map, it plays the map instead)
    p.cams = {"iss": {"space": "iss", "map": nomap}, "off": {"space": "iss", "daylight_only": False, "map": nomap},
              "earth": {}, "mapped": {"space": "iss"}}
    p.playlist = [("iss", 40), ("off", 40), ("earth", 40)]
    p._bad = {}
    monkeypatch.setattr(p, "_health", lambda now: {})
    monkeypatch.setattr(p, "_space", lambda now: d)
    day, night = t0 + 100 + space.LAG, t0 + 400 + space.LAG
    assert not p.is_down("iss", day) and p.is_down("iss", night)
    assert not p.is_down("off", night) and not p.is_down("earth", night)
    assert not p.is_down("mapped", night)
    monkeypatch.setattr(p, "_space", lambda now: None)                   # no orbit data: never hidden for it
    assert not p.is_down("iss", night)


def test_every_space_country_has_a_flag():
    from display.modules import flags
    for code in ("au", "nz", "kr", "kp", "ir", "ua", "pk", "mx", "ar", "tw", "vn", "za"):
        assert flag_char(code), code
    assert all(len(a) == flags.H and all(len(r) == flags.W for r in a) for a in flags.ART.values())
    assert iss.PAD_FLAG["mh"] == "us"                       # Kwajalein (Pegasus, once Falcon 1)
