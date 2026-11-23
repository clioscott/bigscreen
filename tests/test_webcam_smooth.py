"""Smooth webcam playback: a deep buffer played at exactly the feed's rate (never sped up or slowed down, nothing
keeping webcams in line with each other), the render loop at the video's rate while video is on screen, and a warmed
feed that starts with the detail it needs."""

import collections
import threading
import time

import numpy as np

from display.modules import webcam as wm
from hub import webcams as wc


def _stream(fps=30):
    st = wm.Stream.__new__(wm.Stream)
    st.fps, st.video, st.frame, st.frame_t, st.black_since, st.error = fps, True, None, 0.0, None, ""
    st._q = collections.deque(maxlen=int(fps * wm.PLAY_MAX_SEC) + 4)
    st._play_t0, st._play_k, st._play_pos, st._play_last, st._shown = None, 0, 0.0, 0.0, None
    st._played, st._buffer_since = False, 0.0
    return st


def _play(st, seconds, fps=30, tick_hz=30, burst_sec=5.0, start=0, t0=100.0, stall=None):
    """Play ``seconds`` on a tick_hz loop while the hub sends burst_sec of frames at a time (YouTube's segments),
    at exactly the feed's rate on average; ``stall`` = (from, to): the hub is busy, so what it would send meanwhile
    comes all at once at the end. Returns the frame numbers shown on each tick."""
    n, shown = start, []
    next_burst = t0
    ticks = int(seconds * tick_hz)
    for i in range(ticks):
        now = t0 + i / tick_hz
        while now >= next_burst and not (stall and stall[0] <= now < stall[1]):
            for _ in range(int(burst_sec * fps)):
                st._q.append(np.full((1, 1, 3), n % 256, np.uint8))
                n += 1
            next_burst += burst_sec
        fr = st.current(now)
        shown.append(None if fr is None else int(fr[0, 0, 0]))
    return shown


def test_steady_feed_plays_every_frame_once_at_30hz():
    st = _stream()
    for k in range(int(30 * wm.PLAY_BUFFER_SEC)):                    # prerolled: a full buffer when it comes up
        st._q.append(np.full((1, 1, 3), k % 256, np.uint8))
    shown = _play(st, 120, start=int(30 * wm.PLAY_BUFFER_SEC))
    steps = [(b - a) % 256 for a, b in zip(shown, shown[1:])]
    assert set(steps) == {1}                                         # a new frame every tick: no dup, no skip
    assert st.dry_runs == 0


def test_steady_feed_at_60hz_shows_each_frame_for_two_draws():
    st = _stream()
    for k in range(int(30 * wm.PLAY_BUFFER_SEC)):
        st._q.append(np.full((1, 1, 3), k % 256, np.uint8))
    shown = _play(st, 60, tick_hz=60, start=int(30 * wm.PLAY_BUFFER_SEC))
    runs = [len(list(g)) for _, g in __import__("itertools").groupby(shown)]
    assert set(runs[1:-1]) == {2}


def test_a_hub_stall_shorter_than_the_buffer_never_shows():
    st = _stream()
    for k in range(int(30 * wm.PLAY_BUFFER_SEC)):
        st._q.append(np.full((1, 1, 3), k % 256, np.uint8))
    shown = _play(st, 90, start=int(30 * wm.PLAY_BUFFER_SEC), stall=(130.0, 136.5))   # the hub 6.5 s behind
    steps = [(b - a) % 256 for a, b in zip(shown, shown[1:])]
    assert st.dry_runs == 0 and set(steps) == {1}                    # no freeze, no catching up: never seen


def test_a_backlog_plays_at_exactly_the_feeds_rate_never_faster():
    st = _stream()
    for k in range(int(30 * wm.PLAY_BUFFER_SEC * 1.2)):              # over the target (a stall's backlog), under
        st._q.append(np.full((1, 1, 3), k % 256, np.uint8))          # the cap with the next segment on top
    st._shown = st._q.popleft()                                      # playing: the start took the first one
    st._play_t0, st._play_last, st._played, st._play_pos = 100.0, 100.0, True, 0.25
    shown = _play(st, 60, start=len(st._q) + 1, t0=100.0)            # (+1: the one on screen)
    steps = [(b - a) % 256 for a, b in zip(shown, shown[1:])]
    assert set(steps) == {1}                                         # one frame per draw: not sped up to drain it


def test_a_prerolled_stream_keeps_only_what_its_start_needs():
    st = _stream()
    assert st._play_t0 is None
    keep = int(30 * (wm.PLAY_BUFFER_SEC + wm.PREROLL_KEEP_SEC))
    for k in range(keep + 200):                                     # the reader's side, as _read does it
        st._q.append(k)
        if st._play_t0 is None:
            while len(st._q) > keep:
                st._q.popleft()
    assert len(st._q) == keep and st._q[-1] == keep + 199


def test_no_webcam_asks_the_hub_for_frames_in_step():
    yt = {"source": {"type": "youtube", "url": "https://youtu.be/x"}}
    assert "sync" not in wm.stream_query(yt, "full", 448, 134)
    assert "sync" not in wm.stream_query({**yt, "sync": True}, "full", 448, 134)   # an old setting: ignored


def test_warming_tells_the_hub_the_zone(monkeypatch):
    class Cfg:
        modules = {"webcams": {"cams": {"a": {"source": {"type": "youtube", "url": "https://youtu.be/x"},
                                              "crops": {"full": {"x": 0.1, "y": 0.2, "w": 0.5}}}}}}
    p = wm.WebcamPanel(Cfg())
    asked = []
    monkeypatch.setattr(wm.threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda s: asked.append(1)})())
    p._warm_hub("a", "full", (0, 45, 448, 134))
    key = next(iter(p._warmed))
    got = dict(key)
    assert got["w"] == 448 and got["h"] == 134 and got["cw"] == 0.5 and got["x"] == 0.1 and asked


def test_an_expected_zone_counts_as_playing_until_it_expires(monkeypatch):
    f = wc.Feed.__new__(wc.Feed)
    f._lock = threading.Lock()
    f.consumers = {}
    clock = [1000.0]
    monkeypatch.setattr(wc.time, "time", lambda: clock[0])
    f.expect(448, 175, {"x": 0.1, "y": 0.2, "w": 0.5})
    assert f.live_wants() == [(448, 175, {"x": 0.1, "y": 0.2, "w": 0.5})]
    f.expect(448, 175, {"x": 0.1, "y": 0.2, "w": 0.5})               # asked again: still once
    assert len(f.live_wants()) == 1
    clock[0] += wc.GRACE_SEC + 1
    assert f.live_wants() == []


def test_the_loop_runs_at_the_video_rate_while_video_is_on_screen():
    from display import main
    app = main.App.__new__(main.App)

    class Cfg:
        system = {"display": {"fps": 60}}
    app.cfg = Cfg()
    now = time.time()
    assert abs(app._period(now, None) - 1 / 60) < 1e-9
    assert abs(app._period(now, now - 0.1) - 1 / 30) < 1e-9
    assert abs(app._period(now, now - 5) - 1 / 60) < 1e-9
    Cfg.system = {"display": {"fps": 60, "video_fps": 60}}
    assert abs(app._period(now, now) - 1 / 60) < 1e-9
