"""Time zone map: schedules and clock changes, the warning pages, the rasters and the display in every shape."""

import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from display.modules import tzmap as mod
from hub import timezones as tz
from hub.jobs import timezones as job

UTC = timezone.utc
NOV1 = datetime(2026, 11, 1, 9, tzinfo=UTC).timestamp()


def test_schedule_finds_transitions_to_the_minute():
    ny = ZoneInfo("America/New_York")
    t0 = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    s = tz.schedule(ny, t0, datetime(2027, 1, 1, tzinfo=UTC).timestamp())
    assert [r[1] for r in s] == [-300, -240, -300]
    assert s[1][0] == datetime(2026, 3, 8, 7, tzinfo=UTC).timestamp()          # 2 am EST
    assert s[2][0] == datetime(2026, 11, 1, 6, tzinfo=UTC).timestamp()         # 2 am EDT
    assert s[1][2] == "EDT"


def _sched(*rows):
    return [list(r) for r in rows]


def _fake_scheds(t0):
    # zone A changes with the US; zone B stops changing (stays at its summer offset)
    return {"A": _sched((t0, -420, "PDT"), (NOV1, -480, "PST"), (NOV1 + 130 * 86400, -420, "PDT")),
            "B": _sched((t0, -420, "PDT"), (NOV1 + 3600, -420, "MST"))}


def test_states_and_changes_group_one_night():
    t0 = NOV1 - 10 * 86400
    sc = _fake_scheds(t0)
    states = job.build_states(sc, ["A", "B"], t0)
    assert [s["offs"] for s in states] == [[-420, -420], [-480, -420], [-480, -420], [-420, -420]]

    class Z:                                 # zone B's rules are new this year; A's aren't
        def __init__(self, new):
            self.new = new
    orig = tz._offset
    tz._offset = lambda z, t: (-420 if z.new and t > NOV1 else -480, "")
    try:
        changes = job.build_changes(states, [Z(False), Z(True)])
    finally:
        tz._offset = orig
    assert len(changes) == 2
    assert changes[0]["start"] == NOV1 and changes[0]["end"] == NOV1 + 3600
    assert changes[0]["changed"] == [0] and changes[0]["new"] == [1]


def test_rule_diff_names_the_zone_and_moment():
    t0 = NOV1 - 86400
    old = {"A": _sched((t0, -420, "PDT"), (NOV1, -480, "PST"))}
    new = {"A": _sched((t0, -420, "PDT"), (NOV1, -420, "MST")), "C": _sched((t0, -300, "EST"))}
    d = tz.rule_diff(old, new, t0)
    assert {"zone": "A", "at": NOV1, "old": -480, "new": -420, "what": "changed"} in d
    assert any(x["zone"] == "C" and x["what"] == "added" for x in d)
    assert tz.rule_diff(old, old, t0) == []


def _data(changes_at=NOV1):
    return {"zones": [{"id": "America/Vancouver"}, {"id": "America/Los_Angeles"}],
            "states": [{"from": changes_at - 86400 * 30, "offs": [-420, -420], "abbrs": ["PDT", "PDT"]},
                       {"from": changes_at, "offs": [-420, -480], "abbrs": ["MST", "PST"]}],
            "changes": [{"start": changes_at, "end": changes_at, "changed": [1], "new": [0]}]}


def test_warning_pages_before_a_change_and_none_after():
    s = mod.settings({})
    d = _data()
    kinds = lambda t: [k for k, _, _ in mod.pages_at(d, t, s)]
    assert kinds(NOV1 - 20 * 86400) == ["now"]                                 # outside the 14 days
    assert kinds(NOV1 - 3 * 86400) == ["upcoming", "now"]
    assert kinds(NOV1 + 60) == ["now"]                                         # the label goes away at the change
    up = mod.pages_at(d, NOV1 - 3 * 86400, s)[0][1]
    assert up["offs"] == [-420, -480]                                          # the upcoming page shows after
    assert [k for k, _, _ in mod.pages_at(d, NOV1 - 3 * 86400, mod.settings({"warn_pages": "upcoming"}))] == ["upcoming"]
    assert [k for k, _, _ in mod.pages_at(d, NOV1 - 3 * 86400, mod.settings({"warn_days": 0}))] == ["now"]


def test_groups_are_named_from_abbreviations_or_overrides():
    d = _data()
    g = mod.groups_of(d, d["states"][1], mod.settings({}))
    assert [(x["off"], x["name"], x["places"]) for x in g] == [(-480, "Pacific", ["US Pacific"]), (-420, "Mountain", ["BC"])]
    g = mod.groups_of(d, d["states"][1], mod.settings({"offset_names": {"-420": "BC time"}}))
    assert g[1]["name"] == "BC time"
    assert mod.fmt_offset(-210) == "UTC-3:30" and mod.fmt_offset(-480) == "UTC-8"


def test_clock_text():
    t = datetime(2026, 11, 2, 20, 5, tzinfo=UTC).timestamp()
    assert mod.clock_text(t, -480, "12h") == ("12:05", "PM")
    assert mod.clock_text(t, -210, "24h") == ("16:35", "")


def _pack():
    """Two square zones side by side in the middle of the continent."""
    sq = lambda lo0, lo1: [[[[lo0, 40.0], [lo1, 40.0], [lo1, 50.0], [lo0, 50.0], [lo0, 40.0]]]]
    return {"tag": "test", "zones": {"America/Vancouver": sq(-115, -100), "America/Los_Angeles": sq(-100, -85)}}


def test_rasterize_and_anchors():
    idx, lines, frames, rect = tz.rasterize(448, 192, _pack(), ["America/Vancouver", "America/Los_Angeles"],
                                            {"prov": [], "country": [], "lakes": []})
    assert idx.shape == (rect[3], rect[2]) and set(np.unique(idx)) == {0, 1, 2}
    x, y = mod.to_px(frames[0], -107.5, 45.0)
    assert idx[int(y), int(x)] == 1                                           # the projection lines up with the raster
    a = tz.anchors(idx, [-420, -480])
    assert {r[0] for r in a} == {-420, -480} and all(r[3] > 3 for r in a)


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "tzmap").mkdir()
    d = _data(time.time() + 5 * 86400)
    maps = {}
    for key, (w, h) in {"448x192": (448, 192), "448x134": (448, 134), "223x134": (223, 134)}.items():
        idx, lines, frames, rect = tz.rasterize(w, h, _pack(), [z["id"] for z in d["zones"]],
                                                {"prov": [], "country": [], "lakes": []})
        idx.tofile(tmp_path / "tzmap" / f"{key}.idx.bin")
        lines.tofile(tmp_path / "tzmap" / f"{key}.lines.bin")
        maps[key] = {"key": key, "zone": [w, h], "rect": rect, "size": [rect[2], rect[3]], "frames": frames,
                     "idx": f"{key}.idx.bin", "lines": f"{key}.lines.bin",
                     "crc": {"idx": tz.crc(idx), "lines": tz.crc(lines)}}
        for s in d["states"]:
            s.setdefault("anchors", {})[key] = tz.anchors(idx, s["offs"])
    d["maps"] = maps
    (tmp_path / "tzmap.json").write_text(json.dumps(d))
    return str(tmp_path)


@pytest.mark.parametrize("size", [(448, 192), (448, 134), (223, 134), (224, 134)])
def test_every_shape_renders_both_pages(data_dir, size):
    a = mod.render_preview(data_dir, size, {}, page=0)
    b = mod.render_preview(data_dir, size, {}, page=1)
    assert a.shape == (size[1], size[0], 3) and a.any() and b.any()
    assert not np.array_equal(a, b)                                           # upcoming vs now


def test_test_mode_after_the_change_has_one_page(data_dir):
    d = json.loads(open(f"{data_dir}/tzmap.json").read())
    after = d["changes"][0]["end"] + 3600
    a = mod.render_preview(data_dir, (448, 192), {}, as_of=after, page=0)
    b = mod.render_preview(data_dir, (448, 192), {}, as_of=after, page=1)
    assert np.array_equal(a, b)


def test_half_written_rasters_are_not_used(data_dir, tmp_path):
    with open(f"{data_dir}/tzmap/448x192.idx.bin", "r+b") as f:
        f.write(b"\x07")
    p = mod.TzMapPanel(type("C", (), {"modules": {}})(), data_dir)
    p._load(time.time())
    assert p.maps == {}


def test_timechange_rule(data_dir):
    p = mod.TzMapPanel(type("C", (), {"modules": {}})(), data_dir)
    assert p.changing()                                                       # the change is 5 days out
    p2 = mod.TzMapPanel(type("C", (), {"modules": {"timezones": {"warn_days": 2}}})(), data_dir)
    assert not p2.changing()


def _cfg(t):
    return {"system": {}, "layout": {}, "modules": {"timezones": t}}


def test_config_validation():
    from hub import config
    config.validate(_cfg({}))
    config.validate(_cfg({"warn_days": 14, "colors": {"-420": "#ff9d3a"}, "offset_names": {"-420": "BC"},
                          "cities": mod.DEFAULT_CITIES, "time_format": "24h"}))
    for bad in ({"warn_days": 99}, {"warn_pages": "x"}, {"colors": {"-420": "red"}}, {"colors": {"pst": "#ffffff"}},
                {"cities": [{"name": "X", "lat": 99, "lon": 0}]}, {"cities": [{"name": "", "lat": 1, "lon": 1}]},
                {"title": "x" * 40}, {"fill": 0}, {"names": {"PST": 5}}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg(bad))
