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
    return {"zones": [{"id": "America/Vancouver", "cc": "CA", "home": -480}, {"id": "America/Los_Angeles", "cc": "US", "home": -480}],
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


def test_groups_are_per_country_and_named_from_abbreviations_or_overrides():
    d = _data()
    g = mod.groups_of(d, d["states"][1], mod.settings({}))
    assert [(x["cc"], x["off"], x["name"], x["places"]) for x in g] == [("CA", -420, "Mountain", ["BC"]),
                                                                         ("US", -480, "Pacific", [])]
    g = mod.groups_of(d, d["states"][0], mod.settings({}))                 # same offset, still one row per country
    assert [(x["cc"], x["off"]) for x in g] == [("CA", -420), ("US", -420)]
    g = mod.groups_of(d, d["states"][1], mod.settings({"offset_names": {"CA:-420": "BC time", "-480": "West"}}))
    assert [x["name"] for x in g] == ["BC time", "West"]
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
    a = tz.anchors(idx, [-420, -480], ["CA", "US"])
    assert {(r[0], r[1]) for r in a} == {("CA", -420), ("US", -480)} and all(r[4] > 3 for r in a)


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
            s.setdefault("anchors", {})[key] = tz.anchors(idx, s["offs"], [z["cc"] for z in d["zones"]])
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
                {"title": "x" * 40}, {"fill": 0}, {"names": {"PST": 5}}, {"legend_rows": "x"},
                {"list_hide": "Pacific/Honolulu"}, {"row_names": {"pac": "P"}}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg(bad))


def test_warning_lasts_through_the_change_night():
    s = mod.settings({})
    d = _data()
    d["changes"][0]["end"] = NOV1 + 6 * 3600                                  # Newfoundland first, Alaska last
    assert [k for k, _, _ in mod.pages_at(d, NOV1 + 3600, s)] == ["upcoming", "now"]
    assert [k for k, _, _ in mod.pages_at(d, NOV1 + 7 * 3600, s)] == ["now"]


def test_rule_diff_ignores_changes_past_the_shorter_schedule():
    t0 = NOV1 - 86400
    old = {"A": _sched((t0, -420, "PDT"), (NOV1, -480, "PST"))}                # ended before spring
    new = {"A": _sched((t0, -420, "PDT"), (NOV1, -480, "PST"), (NOV1 + 130 * 86400, -420, "PDT"))}
    assert tz.rule_diff(old, new, t0, NOV1 + 100 * 86400) == []


def test_version_ignores_build_suffixes(tmp_path):
    (tmp_path / "tzdata.zi").write_text("# version 2026b-dirty\n")
    assert tz._version_of(tmp_path) == "2026b"


def test_wheel_extraction_stays_in_its_folder(tmp_path, monkeypatch):
    import hashlib
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("tzdata/zoneinfo/tzdata.zi", "# version 2026z\n")
        z.writestr("tzdata/zoneinfo/zone.tab", "CA\t+4916-12307\tAmerica/Vancouver\n")
        z.writestr("tzdata/zoneinfo//" + str(tmp_path / "escaped.txt").lstrip("/"), "x")
        z.writestr("tzdata/zoneinfo/../../escaped2.txt", "x")
    raw = buf.getvalue()

    class R:
        def __init__(self, data):
            self.data = data
            self.content = data if isinstance(data, bytes) else b""

        def json(self):
            return self.data

    info = {"info": {"version": "2026.99"}, "urls": [{"filename": "tzdata-2026.99-py2.py3-none-any.whl", "url": "w",
                                                       "digests": {"sha256": hashlib.sha256(raw).hexdigest()}}]}
    monkeypatch.setattr(tz, "TZDATA_DIR", tmp_path / "tzd")
    monkeypatch.setattr(tz, "_get", lambda url, **kw: R(raw if url == "w" else info))
    assert tz.check_tzdata() == {"latest": "2026z", "pypi": "2026.99", "error": ""}
    assert not (tmp_path / "escaped.txt").exists() and not (tmp_path / "escaped2.txt").exists()
    info["urls"][0]["digests"]["sha256"] = "0" * 64                          # a tampered download is refused
    monkeypatch.setattr(tz, "TZDATA_DIR", tmp_path / "tzd2")
    assert "sha256" in tz.check_tzdata()["error"]


def test_boundaries_fall_back_to_the_last_release(tmp_path, monkeypatch):
    import gzip
    monkeypatch.setattr(tz, "CACHE", tmp_path)
    (tmp_path / "zones-2026a.json.gz").write_bytes(gzip.compress(json.dumps(_pack()).encode()))
    monkeypatch.setattr(tz, "_fetch_boundaries", lambda *a: (_ for _ in ()).throw(OSError("offline")))
    pack, err = tz.boundaries({"America/Vancouver", "America/New_Zone"}, "2026f")
    assert pack["zones"] and "offline" in err
    pack, err = tz.boundaries({"America/New_Zone"}, "2026a")                  # a cached release is never refetched
    assert err == ""


def test_frames_are_drawn_once_a_minute(data_dir, monkeypatch):
    p = mod.TzMapPanel(type("C", (), {"modules": {}})(), data_dir)
    from display.gfx import Canvas
    calls = []
    orig = p._draw
    monkeypatch.setattr(p, "_draw", lambda *a: (calls.append(1), orig(*a)))
    c = Canvas(448, 192)
    for _ in range(5):
        p.draw(c, (0, 0, 448, 192), {"panel": "timezones_wall"}, False)
    assert len(calls) == 1 and c.buf.any()


def _table_data():
    """Vancouver (CA, Pacific), LA (US, Pacific), Edmonton (CA, Mountain), St John's (CA, on the map, not the list)."""
    zones = [{"id": "America/Vancouver", "cc": "CA", "home": -480, "area": 10},
             {"id": "America/Los_Angeles", "cc": "US", "home": -480, "area": 10},
             {"id": "America/Edmonton", "cc": "CA", "home": -420, "area": 10},
             {"id": "America/St_Johns", "cc": "CA", "home": -210, "area": 1}]
    st = {"from": 0, "offs": [-420, -480, -360, -210], "abbrs": ["MST", "PST", "CST", "NST"]}
    return {"zones": zones, "states": [st]}, st


def test_legend_rows_line_up_canada_and_the_us_by_old_zone():
    d, st = _table_data()
    t = mod.table_of(d, st, mod.settings({"legend_rows": "traditional"}))
    assert [r["label"] for r in t] == ["PACIFIC", "MOUNTAIN"]                  # Newfoundland: the map, not the list
    pac = t[0]["cells"]
    assert [e["off"] for e in pac["CA"]] == [-420] and [e["off"] for e in pac["US"]] == [-480]
    assert "US" not in t[1]["cells"]
    t = mod.table_of(d, st, mod.settings({}))                                # the default: a row per offset
    assert [r["label"] for r in t] == ["UTC-8", "UTC-7", "UTC-6"]
    assert set(t[1]["cells"]) == {"CA"} and set(t[0]["cells"]) == {"US"}
    t = mod.table_of(d, st, mod.settings({"legend_rows": "traditional", "list_hide": [], "row_names": {"-480": "WEST"}}))
    assert [r["label"] for r in t] == ["WEST", "MOUNTAIN", "NFLD"]


def test_a_cell_splits_places_by_rule():
    d, st = _table_data()
    d["zones"].append({"id": "America/Cambridge_Bay", "cc": "CA", "home": -420, "area": 10, "dst": True})
    d["zones"][2]["dst"] = False                                              # Edmonton: no more clock changes
    st["offs"].append(-360)                                                   # same offset as Edmonton right now
    st["abbrs"].append("MDT")
    cell = mod.table_of(d, st, mod.settings({"legend_rows": "traditional"}))[1]["cells"]["CA"]
    assert [(e["off"], e["dst"], e["places"]) for e in cell] == [(-360, False, ["AB"]), (-360, True, [])]
    by_off = mod.table_of(d, st, mod.settings({}))                           # by offset: one time for both
    assert [(e["off"], e["places"]) for e in by_off[-1]["cells"]["CA"]] == [(-360, ["AB"])]


def test_each_time_is_named_per_country():
    d, st = _table_data()
    t = mod.table_of(d, st, mod.settings({}))
    assert t[0]["cells"]["US"][0]["names"] == ["Pacific"] and t[1]["cells"]["CA"][0]["names"] == ["Mountain"]
    t = mod.table_of(d, st, mod.settings({"label_style": "abbr"}))
    assert t[1]["cells"]["CA"][0]["names"] == ["MST"]
    st2 = dict(st, offs=[-420, -420, -360, -210], abbrs=["MST", "PDT", "CST", "NST"])   # LA on daylight time
    d["zones"][1]["cc"] = "CA"
    e = mod.table_of(d, st2, mod.settings({}))[0]["cells"]["CA"][0]            # two zones share -7
    assert e["names"] == ["Mountain", "Pacific"] and e["abbrs"] == ["MST", "PDT"]
    d["zones"][1]["area"] = 1                                                 # a sliver adds no second name
    assert mod.table_of(d, st2, mod.settings({}))[0]["cells"]["CA"][0]["names"] == ["Mountain"]
    d["zones"][1]["area"] = 10
    d["zones"][1]["cc"] = "US"
    t = mod.table_of(d, st, mod.settings({"offset_names": {"CA:-420": "Pacific Time"}}))
    assert t[1]["cells"]["CA"][0]["names"] == ["Pacific Time"]


def test_unnamed_small_times_stay_off_the_list():
    d, st = _table_data()
    d["zones"].append({"id": "America/Creston", "cc": "CA", "home": -420, "area": 1})
    st["offs"].append(-420)
    st["abbrs"].append("MST")
    cells = mod.table_of(d, st, mod.settings({"legend_rows": "traditional"}))[1]["cells"]["CA"]
    assert [e["off"] for e in cells] == [-360]                                # Edmonton's, not Creston's


def test_boundaries_follow_each_change(data_dir):
    """The white lines between offsets are redrawn from each moment's offsets: none between the two zones while
    they share an offset, one once they differ."""
    p = mod.TzMapPanel(type("C", (), {"modules": {}})(), data_dir)
    p._load(time.time())
    p.data["zones"] = [dict(z, cc="US") for z in p.data["zones"]]          # one country: no border in the way
    m = p._map_for(448, 192)
    x, y = (int(v) for v in mod.to_px(m["frames"][0], -100.0, 45.0))      # the edge between the two squares
    white = lambda st: [tuple(px) for px in p._background(m, st, [], [])[y, x - 2:x + 2]].count((255, 255, 255))
    same, split = p.data["states"]
    assert white(same) == 0 and white(split) >= 1


def test_cities_are_off_by_default():
    assert not any(c["on"] for c in mod.DEFAULTS["cities"])


def test_lines_have_no_corner_only_steps():
    m = np.eye(5, dtype=bool)                                                # a diagonal: pixels touch at corners only
    s = tz.solid(m)
    assert s[m].all()
    for y in range(4):                                                        # each step now shares an edge
        assert s[y, y + 1] or s[y + 1, y]
    flip = tz.solid(m[:, ::-1])
    for y in range(4):
        assert flip[y, 3 - y] or flip[y + 1, 4 - y]


def test_the_list_leaves_atlantic_and_newfoundland_to_the_map():
    hide = set(mod.DEFAULTS["list_hide"])
    assert {"America/Halifax", "America/Moncton", "America/Goose_Bay", "America/St_Johns"} <= hide
    assert "America/Toronto" not in hide


def test_roomy_rows_on_the_whole_wall(data_dir, monkeypatch):
    """The whole wall has room for the big layout (clock, name, places)."""
    p = mod.TzMapPanel(type("C", (), {"modules": {}})(), data_dir)
    p._load(time.time())
    used = []
    monkeypatch.setattr(p, "_entry_big", lambda *a, **k: used.append("big"))
    monkeypatch.setattr(p, "_entry", lambda *a, **k: used.append("small"))
    from display.gfx import Canvas
    st = mod.state_at(p.data, time.time())
    p._draw(Canvas(448, 192), p._map_for(448, 192), "now", st, None, time.time(), None)
    assert used and set(used) == {"big"}
