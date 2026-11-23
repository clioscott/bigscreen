"""The panels' send on its own thread (display/main.py Presenter): frames in order, one in flight, brightness between
frames, drawing overlapped with sending."""

import threading
import time

import numpy as np

from display import main


class _Fb:
    def __init__(self, v):
        self.buf = np.full((4, 8, 3), v, np.uint8)


class _Out:
    def __init__(self, send_sec=0.0, fail_on=None):
        self.sent, self.events = [], []
        self.send_sec, self.fail_on = send_sec, fail_on
        self.active = 0
        self.overlap = False
        self.lock = threading.Lock()

    def _enter(self):
        with self.lock:
            self.active += 1
            self.overlap |= self.active > 1

    def _leave(self):
        with self.lock:
            self.active -= 1

    def present(self, fb):
        self._enter()
        try:
            v = int(fb.buf[0, 0, 0])
            if v == self.fail_on:
                raise RuntimeError("boom")
            if self.send_sec:
                time.sleep(self.send_sec)
            self.sent.append(v)
            self.events.append(("frame", v))
            return True
        finally:
            self._leave()

    def set_brightness(self, value):
        self._enter()
        self.events.append(("brightness", value))
        self._leave()


def test_frames_go_out_in_order_and_the_drawing_buffer_is_free_at_once():
    out = _Out(send_sec=0.002)
    p = main.Presenter(out)
    fb = _Fb(0)
    for v in range(1, 30):
        fb.buf[:] = v                       # the next frame drawn into the same buffer right after submitting
        p.submit(fb)
    assert p.idle()
    assert out.sent == list(range(1, 30))   # each one as drawn: the copy, not the buffer being drawn into


def test_brightness_is_applied_between_frames_never_during_one():
    out = _Out(send_sec=0.003)
    p = main.Presenter(out)
    for v in range(1, 10):
        p.submit(_Fb(v))
        if v == 5:
            p.set_brightness(40)
    assert p.idle()
    assert ("brightness", 40) in out.events and not out.overlap
    i = out.events.index(("brightness", 40))
    assert out.events[i - 1][0] == "frame"


def test_drawing_overlaps_sending():
    """Drawing 8 ms and sending 10 ms per frame: back to back that's 18 ms a frame, over 60 Hz; overlapped it's ~10."""
    out = _Out(send_sec=0.010)
    p = main.Presenter(out)
    n = 40
    t = time.perf_counter()
    for v in range(n):
        time.sleep(0.008)                   # the draw (sleeping releases the GIL, as the library's send does)
        p.submit(_Fb(v))
    assert p.idle()
    per = (time.perf_counter() - t) / n
    assert per < 0.016, per                 # under one 60 Hz frame (back to back would be ≥ 18 ms)


def test_a_failed_send_is_logged_and_the_next_frame_still_goes():
    out = _Out(fail_on=2)
    p = main.Presenter(out)
    for v in (1, 2, 3):
        p.submit(_Fb(v))
    assert p.idle()
    assert out.sent == [1, 3] and len(p.errors) == 1


def test_present_core_change_restarts_like_the_panel_timing_keys():
    import inspect
    assert "present_core" in inspect.getsource(main.HardwareOutput.__init__)
