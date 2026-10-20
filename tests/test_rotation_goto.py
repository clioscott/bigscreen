"""Jumping the rotation to an entry (the Rotation page's ⇥ button): it plays now and the rotation carries on after it."""
import pytest

from display.rotation import Rotation


class Env:
    """{"if": "no"} never holds; everything else does."""
    def allows(self, entry):
        return all(r.get("if") != "no" for r in entry.get("rules") or [])


def _entries():
    return [{"panel": "a", "dwell_sec": 10}, {"panel": "b", "dwell_sec": 10},
            {"panel": "c", "dwell_sec": 10}, {"panel": "d", "dwell_sec": 10}]


def _run(r, t0, t1):
    t = t0
    while t < t1:
        r.current(t)
        r.report(None, True, t)
        t += 0.5
    return r.current(t1)["panel"]


def test_goto_plays_the_entry_and_the_rotation_carries_on_from_it():
    r = Rotation("left", _entries(), env=Env())
    assert r.current(0.0)["panel"] == "a"
    assert r.goto(2, now=1.0) == 2
    e = r.current(1.0)
    assert e["panel"] == "c"
    d = r.describe(1.0)
    assert d["pos"] == 2 and d["current_idx"] == 2 and d["playing"]["source"] == "rotation"
    assert _run(r, 1.0, 12.0) == "d"                                   # then the next one, not back to b
    assert _run(r, 12.0, 23.0) == "a"


def test_goto_plays_an_entry_whose_rules_dont_hold_once():
    es = _entries()
    es[1]["rules"] = [{"if": "no"}]
    es[2]["enabled"] = False
    r = Rotation("left", es, env=Env())
    r.current(0.0)
    r.goto(1, now=1.0)
    assert r.current(1.0)["panel"] == "b"
    assert _run(r, 1.0, 12.0) == "d"                                   # b once, c is off, d next
    assert _run(r, 12.0, 23.0) == "a"
    assert _run(r, 23.0, 34.0) == "d"                                   # b and c skipped again as usual
    r.goto(2, now=35.0)
    assert r.current(35.0)["panel"] == "c"


def test_goto_ends_a_preview_without_resuming_it():
    r = Rotation("left", _entries(), env=Env())
    r.current(0.0)
    r.play({"panel": "x"}, hold=True, now=1.0)
    assert r.current(1.0)["panel"] == "x"
    r.goto(3, now=2.0)
    assert r.current(2.0)["panel"] == "d" and r.preview is None
    assert _run(r, 2.0, 13.0) == "a"


def test_goto_finds_the_entry_again_after_the_list_changed():
    r = Rotation("left", _entries(), env=Env())
    r.current(0.0)
    r.reload([{"panel": "c"}, {"panel": "a"}, {"panel": "b"}, {"panel": "d"}])
    assert r.goto(2, {"panel": "c", "dwell_sec": 10}, now=1.0) == 0    # index 2 is b now: matched by the entry
    assert r.current(1.0)["panel"] == "c"
    with pytest.raises(LookupError):
        r.goto(1, {"panel": "zzz"}, now=2.0)
    with pytest.raises(LookupError):
        r.goto(9, now=2.0)


def test_goto_picks_the_nearest_of_duplicate_entries():
    r = Rotation("left", [{"panel": "h"}, {"panel": "a"}, {"panel": "h"}, {"panel": "b"}], env=Env())
    assert r.goto(3, {"panel": "h"}, now=0.0) == 2
    assert r.goto(2, {"panel": "h"}, now=0.0) == 2


def test_goto_plays_the_variant_whose_rules_hold_and_drops_a_queued_jump():
    es = [{"panel": "a"}, {"panel": "webcam_full", "cam": "k", "variants": [{"panel": "webcam", "rules": []}]},
          {"panel": "weather_alert"}]
    r = Rotation("right", es, env=Env())
    r.current(0.0)
    r.current(1.5)
    assert r.pending == [2]                                            # the alert's priority jump is queued
    r.goto(1, {"panel": "webcam_full", "cam": "k"}, now=2.0)
    assert r.current(2.0)["panel"] == "webcam"                         # shaped like a normal pick
    r.goto(2, now=3.0)
    assert r.current(3.0)["panel"] == "weather_alert" and r.pending == []
