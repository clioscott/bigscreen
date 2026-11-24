"""A webcam never comes up loading in the rotation: the screen before it stays up (a little) until it's buffered, and
the Webcams list opens on one that is."""

from display.gfx import Canvas
from display.modules import webcam as wm
from display.rotation import HOLD_READY_SEC, Rotation


def _run(r, t0, t1):
    t = t0
    while t < t1:
        r.current(t)
        r.report(None, True, t)
        t += 0.5
    return r.current(t1)["panel"]


def _rot(ready):
    r = Rotation("right", [{"panel": "blank", "dwell_sec": 10}, {"panel": "webcam", "cam": "a", "dwell_sec": 10}])
    r.ready = ready
    return r


def test_a_screen_stays_up_while_the_webcam_after_it_buffers():
    state = {"ready": False}
    r = _rot(lambda e: state["ready"] or e["panel"] != "webcam")
    assert _run(r, 0, 12) == "blank"                                 # done at 10 s, but the webcam isn't ready
    state["ready"] = True
    assert _run(r, 12, 13.5) == "webcam"                             # buffered: on it comes


def test_the_hold_is_short_even_if_the_webcam_never_buffers():
    r = _rot(lambda e: e["panel"] != "webcam")
    assert _run(r, 0, 10 + HOLD_READY_SEC + 1) == "webcam"           # (it then loads, or is passed over)


def test_no_hold_without_a_ready_check_or_for_other_screens():
    r = _rot(None)
    assert _run(r, 0, 10.5) == "webcam"
    r = Rotation("right", [{"panel": "blank", "dwell_sec": 10}, {"panel": "clock", "dwell_sec": 10}])
    r.ready = lambda e: True
    assert _run(r, 0, 10.5) == "clock"


class _St:
    alive = True

    def __init__(self, ready):
        self._ready = ready

    def ready(self):
        return self._ready

    def down(self, now=None):
        return False


def _panel(monkeypatch, streams):
    class Cfg:
        modules = {"webcams": {"cams": {k: {"source": {"type": "youtube", "url": f"https://youtu.be/{k}"}}
                                        for k in "abc"},
                               "playlist": [{"cam": k, "sec": 20} for k in "abc"], "per_turn": "all"}}
    monkeypatch.setitem(wm._list_pos, "i", -1)
    p = wm.WebcamPanel(Cfg())
    p._open = lambda cid, shape, zone: streams.get(cid)
    return p


def test_the_list_counts_as_ready_once_one_of_its_first_webcams_is_buffered(monkeypatch):
    z, e = (0, 45, 448, 134), {"panel": "webcam_full"}
    assert not _panel(monkeypatch, {"a": _St(False), "b": _St(False)}).ready_for(e, z)
    assert _panel(monkeypatch, {"a": _St(False), "b": _St(True)}).ready_for(e, z)
    assert _panel(monkeypatch, {}).ready_for({"panel": "map"}, z)


def test_a_list_turn_opens_on_its_first_buffered_webcam(monkeypatch):
    p = _panel(monkeypatch, {"a": _St(False), "b": _St(True)})
    drawn = []
    p._draw_cam = lambda c, zone, entry, transition, **kw: drawn.append(entry["cam"])
    p._preroll = lambda cid, shape, zone: _St(True)
    p._warm_hub = lambda *a: None
    p.draw(Canvas(448, 134), (0, 45, 448, 134), {"panel": "webcam_full", "_play": 1}, True)
    assert drawn == ["b"]
