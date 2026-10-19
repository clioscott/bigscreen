"""Warming the next webcam: the hub starts its feed shortly before its screen comes up."""
from display.rotation import Rotation


def _run(r, t0, t1):
    t = t0
    while t < t1:
        r.current(t)
        r.report(None, True, t)
        t += 0.5


def test_upcoming_names_the_next_screen_only_near_the_end_of_this_one():
    r = Rotation("right", [{"panel": "blank", "dwell_sec": 60}, {"panel": "webcam", "cam": "a", "dwell_sec": 30}])
    _run(r, 0.0, 10.0)
    assert r.upcoming(10.0) is None                                     # 50 s left: too early
    _run(r, 10.0, 45.0)
    assert r.upcoming(45.0)["cam"] == "a"                               # under 20 s left


def test_screens_that_run_by_passes_use_how_long_their_passes_take():
    r = Rotation("right", [{"panel": "map", "advance": "cycles", "cycles": 3, "max_sec": 600},
                           {"panel": "webcam", "cam": "a"}])
    r.current(0.0)
    assert r.upcoming(0.5) is None                                      # no pass done yet: nothing to go on
    t = 0.0
    for _ in range(2):                                                  # two 30 s passes: about 30 s to go
        while t < 30.0 * (_ + 1):
            r.report(None, True, t)
            t += 0.5
        r.report("done", True, t)
    assert r.upcoming(t) is None
    while t < 72.0:                                                     # 12 s into the last pass: ~18 s to go
        r.report(None, True, t)
        t += 0.5
    assert r.upcoming(t)["cam"] == "a"
    one = Rotation("right", [{"panel": "headlines", "advance": "cycles", "cycles": 1, "max_sec": 1800},
                             {"panel": "webcam", "cam": "a"}])
    one.current(0.0)
    assert one.upcoming(0.5) is None                                    # one long pass: not warmed for 30 minutes


def test_upcoming_skips_entries_whose_rules_dont_hold_and_waits_out_previews():
    class Env:
        def allows(self, e):
            return e.get("cam") != "off"
    r = Rotation("right", [{"panel": "blank", "dwell_sec": 10}, {"panel": "webcam", "cam": "off"},
                           {"panel": "webcam", "cam": "b"}], env=Env())
    _run(r, 0.0, 5.0)
    assert r.upcoming(5.0)["cam"] == "b"
    r.play({"panel": "blank", "dwell_sec": 3}, now=5.0)
    assert r.upcoming(5.5) is None


def _panel(monkeypatch):
    from display.modules import webcam as wc
    asked = []

    class Conn:
        def __init__(self, host, port, timeout):
            pass

        def request(self, method, path):
            asked.append(path)

        def getresponse(self):
            return type("R", (), {"read": lambda s: b"{}"})()

        def close(self):
            pass

    class Now:                                                          # run the background ask at once
        def __init__(self, target, **k):
            self.t = target

        def start(self):
            self.t()
    monkeypatch.setattr(wc.http.client, "HTTPConnection", Conn)
    monkeypatch.setattr(wc.threading, "Thread", Now)
    cfg = type("C", (), {"modules": {"webcams": {"hub_url": "http://hub:8800", "cams": {
        "a": {"source": {"type": "youtube", "url": "https://youtu.be/x"}, "crops": {"full": {"x": 0, "y": 0, "w": 0.5}}},
        "d": {"source": {"type": "drivebc", "id": 18}}}}}})()
    return wc, wc.WebcamPanel(cfg), asked


def test_warm_asks_the_hub_for_the_source_once_per_interval(monkeypatch):
    wc, p, asked = _panel(monkeypatch)
    p.warm({"panel": "webcam", "cam": "a"})
    p.warm({"panel": "webcam_full", "cam": "a"})                       # same source: not asked again so soon
    p.warm({"panel": "webcam", "cam": "d"})
    p.warm({"panel": "webcam", "cam": "missing"})
    assert len(asked) == 2 and asked[0].startswith("/api/webcams/warm?") and "youtu.be" in asked[0]
    assert "cw=" not in asked[0] and "w=" not in asked[0].replace("fps", "")   # the source, not a zone
    assert "id=18" in asked[1]
    p._warmed.clear()
    p.streams["x"] = type("S", (), {"alive": True, "query": wc.stream_query(p.cams["a"], "full", 448, 134)})()
    p.warm({"panel": "webcam", "cam": "a"})
    assert len(asked) == 2                                              # already on screen: nothing to warm


def test_hub_warm_starts_the_feed_and_never_fails_loudly(monkeypatch):
    from hub import app as hub_app
    started = []
    monkeypatch.setattr(hub_app.webcams, "feed", lambda src, fps: started.append((src, fps)) or type("F", (), {"state": "starting"})())
    assert hub_app.webcams_warm(type="youtube", url="https://youtu.be/x", fps=99) == {"ok": True, "state": "starting"}
    assert started[0] == ({"type": "youtube", "url": "https://youtu.be/x", "id": None, "refresh_sec": None}, 30)

    def boom(src, fps):
        raise hub_app.webcams.SourceError("already decoding 4 webcams")
    monkeypatch.setattr(hub_app.webcams, "feed", boom)
    assert hub_app.webcams_warm(type="youtube", url="x")["ok"] is False
