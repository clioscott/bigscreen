"""One rotation entry that plays as another shape at certain times ("variants")."""
from display.rotation import Rotation, entry_key, variants_of

BIZ = {"if": "biz"}


class Env:
    """Rules for the tests: {"if": "biz"} holds while biz is True; everything else holds."""
    def __init__(self):
        self.biz = True

    def allows(self, entry):
        return all((self.biz != bool(r.get("not"))) if r.get("if") == "biz" else True for r in entry.get("rules") or [])


def _run(r, t0, t1):
    """Draw from t0 to t1 in 0.5 s steps (screen time counts between reports); the panel shown at t1."""
    t = t0
    while t < t1:
        r.current(t)
        r.report(None, True, t)
        t += 0.5
    return r.current(t1)["panel"]


def _cam(**k):
    return {"panel": "webcam_full", "cam": "a", "dwell_sec": 10, "variants": [{"panel": "webcam", "rules": [BIZ]}], **k}


def test_variant_picks_the_shape_when_the_screen_starts():
    env = Env()
    r = Rotation("right", [_cam(), {"panel": "blank", "dwell_sec": 10}], env=env)
    e = r.current(0.0)
    assert e["panel"] == "webcam" and e["cam"] == "a"                 # business hours: half width
    assert _run(r, 0.0, 5.0) == "webcam"
    env.biz = False
    assert _run(r, 5.0, 9.0) == "webcam"                              # a started screen keeps its shape
    assert _run(r, 9.0, 15.0) == "blank"
    assert _run(r, 15.0, 26.0) == "webcam_full"                       # next time round: full width


def test_shaped_entry_keeps_its_identity():
    env = Env()
    r = Rotation("right", [_cam(), {"panel": "blank"}], env=env)
    shaped = r.current(0.0)
    assert entry_key(shaped) == entry_key(_cam())                     # reloads and priorities still recognise it
    r.reload([_cam(dwell_sec=20), {"panel": "blank"}])
    assert r.current(1.0)["cam"] == "a"


def test_restart_after_a_preview_decides_the_shape_afresh():
    env = Env()
    r = Rotation("right", [_cam(), {"panel": "blank"}], env=env)
    assert _run(r, 0.0, 1.0) == "webcam"
    r.play({"panel": "blank", "dwell_sec": 2}, now=1.0)
    env.biz = False
    assert _run(r, 1.0, 1.5) == "blank"
    assert _run(r, 1.5, 4.5) == "webcam_full"                         # the preview ended: the webcam restarts, full
    assert "_shape_of" not in r.current(4.5)                         # its own shape, not the stale variant


def test_entry_rules_still_gate_the_whole_entry():
    env = Env()
    r = Rotation("right", [_cam(rules=[{**BIZ, "not": True}]), {"panel": "blank"}], env=env)
    assert r.current(0.0)["panel"] == "blank"                         # entry not allowed at all in business hours


def test_malformed_variants_are_ignored():
    assert variants_of({"variants": [None, {"panel": ""}, {"rules": []}, {"panel": "webcam", "rules": "x"}]}) == \
        [{"panel": "webcam", "rules": []}]
    r = Rotation("right", [{"panel": "webcam_full", "cam": "a", "variants": "nope"}], env=Env())
    assert r.current(0.0)["panel"] == "webcam_full"
    assert variants_of({"variants": {"panel": "webcam"}}) == []


def _alert():
    return {"panel": "weather_alert", "max_sec": 900, "advance": "cycles", "cycles": 1, "rules": [],
            "variants": [{"panel": "weather_alert_half", "max_sec": 300, "rules": [BIZ]}]}


def test_variant_fields_override_while_it_plays_that_way():
    r = Rotation("right", [_alert(), {"panel": "blank"}], env=Env())
    e = r.current(0.0)
    assert e["panel"] == "weather_alert_half" and e["max_sec"] == 300 and e["cycles"] == 1
    assert e["priority"] == "next"                                     # the entry's own settings still apply


def test_restart_drops_the_old_variants_overrides():
    env = Env()
    r = Rotation("right", [_alert(), {"panel": "blank"}], env=env)
    assert r.current(0.0)["max_sec"] == 300
    r.play({"panel": "blank", "dwell_sec": 2}, now=1.0)
    env.biz = False
    _run(r, 1.0, 4.5)
    e = r.current(4.5)
    assert e["panel"] == "weather_alert" and e["max_sec"] == 900      # back to the entry's own size and cap


def test_alert_pairs_fold_into_one_entry_each():
    import copy
    from tools.alert_lifecycle_rotation import migrate
    cfg = {"modules": {"layout_cycle": {"left": [], "right": [
        {"panel": "weather_alert", "advance": "cycles", "cycles": 1, "max_sec": 900,
         "rules": [{"if": "alert", "phase": "full", "min_level": "watch"}], "priority": "next"},
        {"panel": "weather_alert_half", "rules": [{"if": "alert", "phase": "half"}], "max_sec": 240, "priority": ""},
        {"panel": "quake_alert", "rules": [{"if": "quakes", "phase": "full"}], "priority": "next"},
        {"panel": "quake_alert_half", "rules": [{"if": "quakes", "phase": "half"}], "max_sec": 300},
        {"panel": "naad_alert_half", "rules": [{"if": "naad", "phase": "half"}]},     # no full entry: left alone
        {"panel": "map", "track": "precip_radar"}]}}}
    migrate(cfg)
    right = cfg["modules"]["layout_cycle"]["right"]
    assert [e["panel"] for e in right] == ["weather_alert", "quake_alert", "naad_alert_half", "map"]
    wx = right[0]
    assert wx["rules"] == [{"if": "alert", "min_level": "watch"}] and wx["max_sec"] == 900
    assert wx["variants"] == [{"panel": "weather_alert_half", "max_sec": 240,
                               "rules": [{"if": "alert", "min_level": "watch", "phase": "full", "not": True}]}]
    once = copy.deepcopy(cfg)
    assert migrate(cfg) == [] and cfg == once                          # running it again changes nothing


def test_a_variant_cannot_switch_priority_or_enabled():
    v = variants_of({"variants": [{"panel": "webcam", "rules": [], "priority": "next", "enabled": False, "dwell_sec": 5}]})
    assert v == [{"dwell_sec": 5, "panel": "webcam", "rules": []}]
