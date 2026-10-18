"""Webcams: crop maths, the frame wire format, captions, config validation and the rotation's "skip"."""

import time

import numpy as np
import pytest

from display.modules import webcam as wc
from hub import webcams

FRAME = (np.arange(360 * 640 * 3) % 251).astype(np.uint8).reshape(360, 640, 3)
CAM = {"label": "Shibuya Crossing", "place": "Tokyo, Japan", "tz": "Asia/Tokyo", "credit": "FNN",
       "source": {"type": "youtube", "url": "https://www.youtube.com/watch?v=abc"}}


@pytest.mark.parametrize("zone", [(224, 134), (152, 130), (448, 134), (448, 192)])
def test_crop_keeps_the_zone_shape_and_stays_inside(zone):
    w, h = zone
    for crop in (None, {"x": 0.9, "y": 0.9, "w": 0.5}, {"x": -1, "y": 0.2, "w": 3}):
        l, t, r, b = webcams.crop_box(640, 360, w, h, crop)
        assert 0 <= l < r <= 640 and 0 <= t < b <= 360
        assert abs((r - l) / (b - t) - w / h) < 0.05
    fit = webcams.fit_crop(640, 360, w, h)
    assert fit["x"] >= 0 and fit["y"] >= 0 and 0 < fit["w"] <= 1


def test_render_and_pack():
    out = webcams.render(FRAME, 448, 192, {"x": 0.1, "y": 0.1, "w": 0.5}, {"brightness": 1.2, "saturation": 0})
    assert out.shape == (192, 448, 3) and out.dtype == np.uint8
    assert np.array_equal(out[..., 0], out[..., 1])                 # saturation 0 is grey
    raw = webcams.pack(out, 42)
    assert raw[:4] == wc.MAGIC and len(raw) == webcams.HEADER + 448 * 192 * 3
    assert int.from_bytes(raw[4:6], "big") == 448 and int.from_bytes(raw[6:8], "big") == 192


def test_adjust_identity_and_clipping():
    assert webcams.adjust(FRAME) is FRAME
    assert webcams.adjust(FRAME, brightness=3.0).max() == 255


def test_check_source():
    assert webcams.check_source({"type": "stream", "url": "rtsp://cam.example/live"}) == ("stream", "rtsp://cam.example/live")
    for bad in ({"type": "stream", "url": "file:///etc/passwd"}, {"type": "x", "url": "https://a.b/"}, {"url": ""}, None):
        with pytest.raises(webcams.SourceError):
            webcams.check_source(bad)


@pytest.mark.parametrize("style", wc.STYLES)
@pytest.mark.parametrize("font", list(wc.FONTS))
def test_captions_render_in_every_zone(style, font):
    cam = {**CAM, "overlay": {"style": style, "font": font, "corner": "tr", "show": {"credit": True}}}
    for zone in ((152, 130), (448, 192)):
        frame = webcams.render(FRAME, *zone)
        img = wc.render_preview(cam, frame, zone, 1.0)
        assert img.shape == (zone[1], zone[0], 3)
        if style == "none":
            assert np.array_equal(img, frame)


def test_caption_intro_hides_after_ten_seconds():
    cam = {**CAM, "overlay": {"when": "intro"}}
    frame = webcams.render(FRAME, 224, 134)
    assert not np.array_equal(wc.render_preview(cam, frame, (224, 134), 1.0), frame)
    assert np.array_equal(wc.render_preview(cam, frame, (224, 134), 11.0), frame)


def test_detail_line_drops_credit_then_place():
    from display.gfx import graphics
    f = graphics.Font()
    f.LoadFont(wc.FONTS["small"])
    bits = [("place", "Tokyo, Japan"), ("time", "04:12"), ("credit", "Fuji News Network")]
    assert "Fuji" in wc.fit_detail(f, bits, 400)
    narrow = wc.fit_detail(f, bits, 110)
    assert "Fuji" not in narrow and "Tokyo" in narrow
    assert wc.fit_detail(f, bits, 30) == "04:12"


def test_stream_query_carries_crop_and_colour():
    cam = {**CAM, "crops": {"right": {"x": 0.25, "y": 0.1, "w": 0.3}}, "colour": {"contrast": 1.3}}
    q = wc.stream_query(cam, "right", 152, 130)
    assert q["cw"] == 0.3 and q["x"] == 0.25 and q["c"] == 1.3 and "b" not in q
    assert "cw" not in wc.stream_query(cam, "left", 224, 134)


def _cfg(webcams_section):
    return {"system": {}, "layout": {}, "modules": {"webcams": webcams_section}}


def test_config_validation():
    from hub import config
    config.validate(_cfg({"cams": {"shibuya": {**CAM, "crops": {"full": {"x": 0, "y": 0.2, "w": 1}}}}}))
    for bad in ({"bad id!": CAM}, {"a": {**CAM, "source": {"type": "stream", "url": "ftp://x/"}}},
                {"a": {**CAM, "fps": 60}}, {"a": {**CAM, "crops": {"full": {"x": 2, "y": 0, "w": 1}}}},
                {"a": {**CAM, "crops": {"middle": {"x": 0, "y": 0, "w": 1}}}}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg({"cams": bad}))


def test_rotation_skip_ends_the_screen_at_once():
    from display.rotation import Rotation
    r = Rotation("right", [{"panel": "webcam", "cam": "a", "dwell_sec": 60}, {"panel": "blank", "dwell_sec": 60}])
    assert r.current(0.0)["panel"] == "webcam"
    r.report(None, True, 0.1)
    r.report("skip", True, 0.2)
    assert r.current(0.3)["panel"] == "blank"


def test_rotation_tells_webcam_entries_apart():
    from display.rotation import entry_key
    assert entry_key({"panel": "webcam", "cam": "a"}) != entry_key({"panel": "webcam", "cam": "b"})


def test_close_never_waits_for_a_blocked_read():
    """The render thread hangs up on idle streams: that must not wait out the socket timeout (review #1)."""
    import socket
    import sys
    import threading
    import time

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    held = []

    def serve():                     # answer the request with headers, then send nothing
        c, _ = srv.accept()
        held.append(c)
        c.recv(4096)
        c.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\n\r\n")

    threading.Thread(target=serve, daemon=True).start()
    st = wc.Stream(f"http://127.0.0.1:{srv.getsockname()[1]}", {"type": "stream", "url": "http://x/", "w": 8, "h": 8})
    time.sleep(0.5)                  # the reader is now blocked in read()
    t0 = time.time()
    st.close()
    assert time.time() - t0 < 0.2
    if sys.platform != "linux":      # Windows doesn't wake a recv() blocked in another thread on shutdown
        srv.close()
        return
    for _ in range(40):              # and the reader thread notices and stops
        if not st.alive and st._conn is None:
            break
        time.sleep(0.05)
    assert st._conn is None
    srv.close()


@pytest.mark.parametrize("style", ["tag", "bar"])
def test_badge_only_caption_stays_in_its_box(style):
    cam = {**CAM, "overlay": {"style": style, "corner": "bl", "show": {"name": False, "place": False, "time": False}}}
    black = np.zeros((130, 152, 3), np.uint8)
    img = wc.render_preview(cam, black, (152, 130), 1.0)
    rows = np.nonzero(img.any(axis=(1, 2)))[0]
    assert len(rows) and rows.max() <= 129 and rows.max() - rows.min() <= 14   # badge + padding, not spilling out


def test_config_validation_rejects_malformed_cams_cleanly():
    from hub import config
    for bad in ({"a": "not a cam"}, {"a": {**CAM, "fps": None}}, {"a": {**CAM, "crops": [1]}}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg({"cams": bad}))
    with pytest.raises(config.ConfigError):
        config.validate(_cfg({"cams": ["not", "a", "mapping"]}))


def test_hub_never_lets_ffmpeg_read_local_files():
    assert "file" not in webcams.PROTOCOLS.split(",") and "data" not in webcams.PROTOCOLS.split(",")


def test_gallery_entries_are_valid_webcams():
    """hub/web/ui/pages/webcam_gallery.js: every ready-made webcam passes config validation, no duplicates."""
    import re
    from pathlib import Path

    from hub import config
    js = (Path(__file__).resolve().parent.parent / "hub/web/ui/pages/webcam_gallery.js").read_text(encoding="utf-8")
    found = re.findall(r'\{ id: "([^"]+)", lat: (-?[\d.]+), lon: (-?[\d.]+), label: "([^"]+)"', js)
    assert all(-90 <= float(la) <= 90 and -180 <= float(lo) <= 180 for _, la, lo, _ in found)
    ids = [(i, lbl) for i, _, _, lbl in found]
    assert len(ids) >= 15
    assert len({i for i, _ in ids}) == len(ids) and len({lbl for _, lbl in ids}) == len(ids)
    cams = {f"c{n}": {"label": lbl, "source": {"type": "youtube", "url": f"https://www.youtube.com/watch?v={i}"}}
            for n, (i, lbl) in enumerate(ids)}
    config.validate(_cfg({"cams": cams}))
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{11}", i) for i, _ in ids)


def _wait_idle(timeout=3.0):
    import time
    end = time.time() + timeout
    while webcams._snap_busy and time.time() < end:
        time.sleep(0.02)


def test_snapshot_never_blocks_and_remembers_failures(monkeypatch):
    """The panel's stills (review of #8): a request gets an answer at once; grabs run in the background."""
    import os
    import time

    calls = []

    def fake_grab(kind, url):
        calls.append(url)
        time.sleep(0.2)
        if "dead" in url:
            raise webcams.SourceError("stream offline")
        p = webcams._snap_path(kind, url)
        p.write_bytes(b"\xff\xd8jpeg")

    monkeypatch.setattr(webcams, "_grab", fake_grab)
    ok, dead = {"type": "stream", "url": "https://cam.example/ok.m3u8"}, {"type": "stream", "url": "https://cam.example/dead.m3u8"}
    t0 = time.time()
    assert webcams.snapshot(ok) == (None, "pending")
    assert webcams.snapshot(ok) == (None, "pending")             # queued once, not twice
    assert time.time() - t0 < 0.1
    _wait_idle()
    assert webcams.snapshot(ok) == (b"\xff\xd8jpeg", "fresh") and calls.count(ok["url"]) == 1

    assert webcams.snapshot(dead)[1] == "pending"
    _wait_idle()
    assert webcams.snapshot(dead) == (None, "failed: stream offline")
    assert calls.count(dead["url"]) == 1                          # not retried within SNAP_RETRY

    p = webcams._snap_path("stream", ok["url"])                   # an old still is served while it's refreshed
    os.utime(p, (time.time() - 2 * webcams.SNAP_TTL,) * 2)
    assert webcams.snapshot(ok) == (b"\xff\xd8jpeg", "stale")
    _wait_idle()
    assert calls.count(ok["url"]) == 2


def test_pacer_evens_out_bursty_hls_segments(monkeypatch):
    """The stutter fix: frames arriving a segment at a time come out at an even 1/fps (no freeze per segment)."""
    import time

    monkeypatch.setattr(webcams, "BUFFER_SEC", 0.5)
    fps, seg = 20, 0.4                                   # 8 frames per "segment", all at once

    class Bursty(webcams.Feed):
        def _run(self):
            self._buffer_sec = webcams.BUFFER_SEC
            n = 0
            while self.state != "stopped" and n < 60:
                with self._buf_lock:
                    for _ in range(int(fps * seg)):
                        self._buf.append(np.full((2, 2, 3), n % 256, np.uint8))
                        n += 1
                time.sleep(seg)

    f = Bursty("stream", "https://cam.example/x.m3u8", fps)
    try:
        seen, times, seq = [], [], 0
        end = time.time() + 2.5
        while time.time() < end:
            seq, frame = f.wait(seq, 0.5)
            if frame is not None:
                seen.append(int(frame[0, 0, 0]))
                times.append(time.time())
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert len(seen) >= 30 and seen == sorted(seen)          # in order, none skipped in bursts
        assert max(gaps) < 3 / fps                               # never a segment-long freeze
        assert f.underflows == 0
    finally:
        f._stop.set()


def _segments(monkeypatch, buffer_sec, fps, seg, jitter, count):
    """A feed whose frames arrive one HLS segment at a time, every seg s (± jitter), no backlog at the start."""
    import random
    import time

    monkeypatch.setattr(webcams, "BUFFER_SEC", buffer_sec)
    rnd = random.Random(7)

    class Segments(webcams.Feed):
        def _run(self):
            self._buffer_sec = webcams.BUFFER_SEC
            for s in range(count):
                if self._stop.is_set():
                    return
                with self._buf_lock:
                    self._buf.extend(np.full((2, 2, 3), s, np.uint8) for _ in range(int(fps * seg)))
                time.sleep(seg + rnd.uniform(-jitter, jitter))

    return Segments("stream", "https://cam.example/y.m3u8", fps)


def test_rebuffering_waits_by_time_not_by_one_segment(monkeypatch):
    """Review of #12: segments longer than the buffer must not restart playout with zero margin."""
    import time
    f = _segments(monkeypatch, buffer_sec=0.3, fps=20, seg=0.4, jitter=0.08, count=12)
    try:
        time.sleep(4.0)
        assert f.underflows <= 1
        assert f.state == "live"
    finally:
        f._stop.set()


def test_buffer_memory_is_capped():
    import time

    class Flood(webcams.Feed):
        def _run(self):
            self._buffer_sec = 0.5
            for _ in range(2000):
                with self._buf_lock:
                    self._buf.append(np.zeros((2, 2, 3), np.uint8))
                    while len(self._buf) > self._cap():
                        self._buf.popleft()
            while not self._stop.is_set():
                time.sleep(0.05)

    f = Flood("stream", "https://cam.example/z.m3u8", 15)
    try:
        time.sleep(0.3)
        assert len(f._buf) <= f._cap() == int((0.5 + webcams.BUFFER_HEADROOM) * 15)
    finally:
        f._stop.set()


# --- captions v2 (display/modules/webcam_caption.py) ------------------------------------------------------------
from display.modules import webcam_caption as cap  # noqa: E402

WX = {"temp": 17.6, "code": 2, "is_day": 1, "sunrise": [], "sunset": [], "at": time.time()}
EVERYTHING = {k: True for k in cap.SECTIONS}


@pytest.mark.parametrize("layout", [l for l in cap.LAYOUTS if l != "none"])
@pytest.mark.parametrize("corner", ["tl", "tc", "tr", "bl", "bc", "br"])
def test_every_layout_and_position_renders_with_everything(layout, corner):
    cam = {**CAM, "overlay": {"style": layout, "corner": corner, "show": EVERYTHING, "custom_text": "Scramble cam"}}
    for zone in ((152, 130), (448, 192)):
        frame = webcams.render(FRAME, *zone)
        img = wc.render_preview(cam, frame, zone, 1.0, wx=WX)
        assert img.shape == (zone[1], zone[0], 3) and not np.array_equal(img, frame)


@pytest.mark.parametrize("lines", ["auto", "wrap", "stacked", "inline"])
def test_line_modes_render(lines):
    cam = {**CAM, "overlay": {"style": "tag", "lines": lines, "show": EVERYTHING}}
    wc.render_preview(cam, webcams.render(FRAME, 223, 134), (223, 134), 1.0, wx=WX)


def test_content_order_headline_and_sections():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    t = datetime(2026, 10, 19, 5, 41, tzinfo=ZoneInfo("Asia/Tokyo"))
    here = datetime(2026, 10, 18, 13, 41, tzinfo=ZoneInfo("America/Vancouver"))
    ov = cap.overlay_of({"overlay": {"headline": "place", "order": ["weather", "time"],
                                     "show": {"weather": True, "offset": True, "date": True}}})
    ct = cap.Content(CAM, ov, now_local=t, here=here, wx=WX)
    assert ct.title == "Tokyo, Japan"                                   # place is the headline…
    kinds = [k for k, _ in ct.bits()]
    assert kinds[:3] == ["weather", "time", "name"] and "place" not in kinds   # …the name moves to the details
    sec = dict(ct.bits())
    assert sec["weather"] == "18°C" and sec["time"] == "05:41" and sec["offset"] == "+16h"
    assert sec["date"] == "MON 19 OCT" and ct.icon == "partly"


def test_sun_section_names_the_next_event():
    import time as _t
    now = _t.time()
    ov = cap.overlay_of({"overlay": {"show": {"sun": True}}})
    ct = cap.Content(CAM, ov, wx={**WX, "sunrise": [now - 3600, now + 7200], "sunset": [now + 3600]})
    assert dict(ct.bits())["sun"].startswith("sunset ")


def test_uppercase_twelve_hour_and_separator():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    t = datetime(2026, 10, 19, 17, 5, tzinfo=ZoneInfo("Asia/Tokyo"))
    ov = cap.overlay_of({"overlay": {"uppercase": True, "time_format": "12h"}})
    ct = cap.Content(CAM, ov, now_local=t)
    assert ct.title == "SHIBUYA CROSSING" and dict(ct.bits())["time"] == "5:05 PM"
    from display.gfx import graphics
    f = graphics.Font()
    f.LoadFont(cap.FONTS["small"])
    assert " / " in cap.fit_detail(f, [("a", "x"), ("b", "y")], 200, sep="slash")


def test_inline_drops_sections_instead_of_cutting_them():
    from display.gfx import Canvas
    cam = {**CAM, "overlay": {"style": "outline", "lines": "inline", "show": EVERYTHING}}
    k = cap.Ctx(Canvas(152, 130), (0, 0, 152, 130), cap.overlay_of(cam), lambda p: _font(p))
    title, lines = cap._headline_and_lines(k, cap.Content(CAM, k.ov, wx=WX), 140)
    assert not lines and cap._tw(k.T, title) <= 140
    assert all(piece.strip() for piece in title.split("·"))           # no dangling half-section


def _font(path):
    from display.gfx import graphics
    f = graphics.Font()
    f.LoadFont(cap.FONTS.get(path, path))
    return f


def test_periodic_visibility_with_fades():
    ov = {"when": "periodic", "show_sec": 10, "every_min": 1, "fade": True}
    assert cap.visibility(ov, 2) == 1.0                                # the intro
    assert cap.visibility(ov, 30) == 0.0                               # hidden in between
    assert 0 < cap.visibility(ov, 60.3) < 1                            # fading back in at the minute
    assert cap.visibility(ov, 65) == 1.0
    assert cap.visibility({**ov, "fade": False}, 60.3) == 1.0


def test_webcam_location_validation():
    from hub import config
    config.validate(_cfg({"cams": {"a": {**CAM, "lat": 49.28, "lon": -123.12}}}))
    for bad in ({"lat": 100}, {"lon": "east"}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg({"cams": {"a": {**CAM, **bad}}}))



# --- PR #13 review regressions ---------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [{"opacity": None}, {"corner": ""}, {"corner": "middle"}, {"font": "huge"},
                                 {"lines": 7}, {"separator": None}, {"every_min": "x", "when": "periodic"},
                                 {"show_sec": None, "when": "periodic"}])
def test_malformed_overlay_never_raises_while_drawing(bad):
    cam = {**CAM, "overlay": {"style": "lower_third", **bad}}
    wc.render_preview(cam, webcams.render(FRAME, 223, 134), (223, 134), 30.0, wx=WX)


def test_overlay_validation():
    from hub import config
    config.validate(_cfg({"cams": {"a": {**CAM, "overlay": {"style": "split", "corner": "bc", "opacity": 0.5}}}}))
    for bad in ({"style": "fancy"}, {"corner": ""}, {"opacity": None}, {"opacity": 2}, {"every_min": 0},
                {"show": {"mood": True}}, {"order": "name"}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg({"cams": {"a": {**CAM, "overlay": bad}}}))


def test_ticker_stays_inside_its_zone():
    from display.gfx import Canvas
    c = Canvas(448, 134)
    c.buf[:] = 77
    cam = {**CAM, "overlay": {"style": "ticker", "show": EVERYTHING, "custom_text": "x" * 40}}
    fonts = {}

    def font_for(p):
        if p not in fonts:
            fonts[p] = _font(p)
        return fonts[p]
    for t in range(0, 4000, 137):                           # many scroll positions
        cap.draw_caption(c, (225, 0, 223, 134), cam, 1.0, font_for, wx=WX)
    assert (c.buf[:, :225] == 77).all()                     # the left side is untouched


def test_periodic_longer_than_the_gap_just_stays_up():
    ov = {"when": "periodic", "show_sec": 60, "every_min": 1, "fade": True}
    assert all(cap.visibility(ov, t) == 1.0 for t in (59.9, 60.0, 60.3, 60.7, 125))


def test_clock_shows_the_name_and_never_the_time_twice():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    t = datetime(2026, 10, 19, 5, 41, tzinfo=ZoneInfo("Asia/Tokyo"))
    drawn = []
    real = cap.graphics.DrawText

    def spy(c, f, x, y, col, s):
        drawn.append(s)
        return real(c, f, x, y, col, s)
    cap.graphics.DrawText = spy
    try:
        for head in ("time", "name"):
            drawn.clear()
            cam = {**CAM, "overlay": {"style": "clock", "headline": head}}
            from display.gfx import Canvas
            ov = cap.overlay_of(cam)                        # one render of the layout (draw_caption caches two)
            cap._l_clock(cap.Ctx(Canvas(223, 134), (0, 0, 223, 134), ov, _font), cap.Content(CAM, ov, now_local=t))
            text = " ".join(drawn)
            assert text.count("05:41") == 1 and "Shibuya Crossing" in text
    finally:
        cap.graphics.DrawText = real


def test_stale_weather_is_not_shown():
    ov = cap.overlay_of({"overlay": {"show": {"weather": True}}})
    assert dict(cap.Content(CAM, ov, wx={**WX, "at": time.time() - 3 * 3600}).bits()).get("weather") is None
    assert dict(cap.Content(CAM, ov, wx=WX).bits())["weather"] == "18°C"


def test_fade_blends_only_the_caption():
    from display.gfx import Canvas
    c = Canvas(448, 192)
    c.buf[:] = 90
    cam = {**CAM, "overlay": {"style": "tag", "corner": "bl", "when": "intro", "fade": True}}
    cap.draw_caption(c, (0, 0, 448, 192), cam, cap.INTRO_SEC + cap.FADE_SEC / 2, _font)
    assert (c.buf[:100] == 90).all()                        # the top of the frame is untouched mid-fade
    assert (c.buf[150:] != 90).any()                        # and the caption is there, half-faded


def test_weather_icon_room_is_reserved():
    from display.gfx import graphics
    f = graphics.Font()
    f.LoadFont(cap.FONTS["small"])
    bits = (("weather", "18°C"), ("time", "05:41"))
    need = cap._tw(f, "18°C · 05:41")
    (kind, text), = cap.detail_lines(f, bits, need + 9, icon_kind="weather")
    assert kind == "weather" and text == "18°C · 05:41"            # fits with the icon's 9 px counted
    (kind, text), = cap.detail_lines(f, bits, need + 8, max_lines=1, icon_kind="weather")
    assert kind == "time"                                          # one line only: the weather drops, nothing overruns
    lines = cap.detail_lines(f, bits, need + 8, icon_kind="weather")
    assert [k for k, _ in lines] == ["weather", "time"]           # fit: it wraps instead of dropping


def test_fit_wraps_before_dropping_and_reports_what_did_not_fit():
    cam = {**CAM, "overlay": {"style": "tag", "show": {"date": True, "offset": True, "credit": True}}}
    from datetime import datetime
    assert cap.dropped_sections(cam, (223, 134), _font) == []      # wraps onto more lines; nothing lost
    tiny = {**CAM, "overlay": {"style": "bar", "show": {k: True for k in cap.SECTIONS}, "custom_text": "x" * 30}}
    gone = cap.dropped_sections(tiny, (152, 130), _font, wx=WX)
    assert gone and set(gone) <= set(cap.SECTIONS)                # a narrow bar can't fit them all: they're named


def test_custom_layout_columns_lines_and_order():
    from display.gfx import Canvas
    ov = {"style": "custom", "show": {"weather": True, "date": True},
          "slots": {"name": [1, 1], "live": [1, 1], "place": [1, 2], "time": [2, 1], "weather": [2, 2], "date": [1, 2]},
          "order": ["date", "place"], "line_sizes": {"2.1": "big"}, "columns": [{}, {"align": "right"}]}
    cam = {**CAM, "overlay": ov}
    ct = cap.Content(CAM, cap.overlay_of(cam), wx=WX)
    grid = cap.custom_grid(ct, cap.overlay_of(cam))
    assert grid[1][1] == ["live", "name"] and grid[1][2] == ["date", "place"]   # your order within the line
    assert grid[2] == {1: ["time"], 2: ["weather"]}
    for container in ("box", "strip", "outline", "board"):
        for zone in ((223, 134), (448, 192)):
            c2 = {**cam, "overlay": {**ov, "container": container}}
            frame = webcams.render(FRAME, *zone)
            img = wc.render_preview(c2, frame, zone, 1.0, wx=WX)
            assert not np.array_equal(img, frame)


def test_custom_layout_validation():
    from hub import config
    good = {"style": "custom", "container": "strip", "slots": {"name": [1, 1], "time": [2, 1]},
            "line_sizes": {"2.1": "big"}, "columns": [{}, {"align": "right"}]}
    config.validate(_cfg({"cams": {"a": {**CAM, "overlay": good}}}))
    for bad in ({"container": "bubble"}, {"slots": {"name": [4, 1]}}, {"slots": {"mood": [1, 1]}}, {"slots": {"name": "1,1"}},
                {"line_sizes": {"1.1": "huge"}}, {"columns": [{"align": "up"}]}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg({"cams": {"a": {**CAM, "overlay": {**good, **bad}}}}))


def test_preview_reports_sections_that_do_not_fit():
    narrow = {**CAM, "overlay": {"style": "bar", "show": {k: True for k in cap.SECTIONS}, "custom_text": "x" * 30}}
    assert wc.caption_dropped(narrow, (152, 130), WX)
    assert wc.caption_dropped({**CAM, "overlay": {"style": "tag"}}, (448, 134), WX) == []


def test_custom_layout_never_clips_text_that_fits():
    """Measure and draw agree: with room to spare, the name isn't cut (it was, by a pixel after LIVE)."""
    ov = cap.overlay_of({**CAM, "overlay": {"style": "custom", "slots": {"live": [1, 1], "name": [1, 1], "time": [2, 1]}}})
    drawn = []
    real = cap.graphics.DrawText

    def spy(c, f, x, y, col, s):
        drawn.append(s)
        return real(c, f, x, y, col, s)
    cap.graphics.DrawText = spy
    try:
        from display.gfx import Canvas
        cap._l_custom(cap.Ctx(Canvas(448, 134), (0, 0, 448, 134), ov, _font), cap.Content(CAM, ov, wx=WX))
    finally:
        cap.graphics.DrawText = real
    assert "Shibuya Crossing" in drawn


# --- PR #14 review regressions ---------------------------------------------------------------------------------
def test_bar_reports_what_its_two_lines_cannot_hold():
    cam = {**CAM, "overlay": {"style": "bar", "show": {k: True for k in cap.SECTIONS},
                              "custom_text": "Scramble crossing, from the station"}}
    assert {"custom", "credit"} & set(wc.caption_dropped(cam, (223, 134), WX))   # needs a third line: named, not lost


def test_dropped_sections_is_safe_across_threads():
    import threading
    cams = [{**CAM, "overlay": {"style": s, "show": {k: True for k in cap.SECTIONS}}} for s in ("bar", "tag", "chips")]
    errors = []

    def run(c):
        try:
            for _ in range(20):
                cap.dropped_sections(c, (152, 130), _font, WX)
        except Exception as e:                              # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=run, args=(c,)) for c in cams * 2]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors


def test_custom_columns_fit_a_very_narrow_zone():
    ov = {"style": "custom", "container": "strip", "show": {"weather": True, "date": True},
          "slots": {"name": [1, 1], "time": [2, 1], "date": [3, 1], "place": [1, 2], "weather": [3, 2]}}
    for w in (60, 100, 150):
        img = wc.render_preview({**CAM, "overlay": ov}, webcams.render(FRAME, w, 130), (w, 130), 1.0, wx=WX)
        assert img.shape == (130, w, 3)


def test_one_per_line_shows_four_sections():
    from display.gfx import Canvas
    ov = cap.overlay_of({"overlay": {"lines": "stacked", "show": {"date": True, "offset": True, "credit": True}}})
    k = cap.Ctx(Canvas(448, 192), (0, 0, 448, 192), ov, _font)
    assert len(k.details(cap.Content(CAM, ov), 300)) == 4


def test_slot_and_line_size_validation_edges():
    from hub import config
    for bad in ({"slots": {"name": [True, 1]}}, {"line_sizes": {"top": "big"}}):
        with pytest.raises(config.ConfigError):
            config.validate(_cfg({"cams": {"a": {**CAM, "overlay": {"style": "custom", **bad}}}}))


# --- weather for previews of unsaved locations ------------------------------------------------------------------
def test_preview_weather_uses_saved_data_or_fetches_for_a_new_location(monkeypatch, tmp_path):
    import json
    from hub import app as hub_app, paths
    from hub.jobs import webcam_weather as job
    (paths.DATA_DIR).mkdir(parents=True, exist_ok=True)
    (paths.DATA_DIR / "webcam_weather.json").write_text(json.dumps(
        {"cam1": {**WX, "temp": 5.0, "lat": 49.2888, "lon": -123.1111}}))
    calls = []
    monkeypatch.setattr(job, "fetch", lambda pts, timeout=30: calls.append(pts) or {k: {**WX, "temp": 30.0} for k in pts})
    hub_app._wx_cache.clear()
    assert hub_app._webcam_wx("cam1", {"lat": 49.2888, "lon": -123.1111})["temp"] == 5.0     # the job's data
    assert hub_app._webcam_wx("cam1", {"lat": 35.6595, "lon": 139.7005})["temp"] == 30.0     # moved: fetched now
    assert hub_app._webcam_wx("cam1", {"lat": 35.6595, "lon": 139.7005})["temp"] == 30.0     # …and cached
    assert len(calls) == 1
    assert hub_app._webcam_wx("cam1", {}) is None                                             # no location


def test_wall_preview_carries_weather_for_unsaved_locations(monkeypatch):
    from hub import app as hub_app
    sent = {}
    monkeypatch.setattr(hub_app, "_webcam_wx", lambda cid, cam: {**WX, "temp": 12.0} if cam.get("lat") is not None else None)
    monkeypatch.setattr(hub_app, "_display_call", lambda path, payload: sent.update(payload) or {"ok": True})
    hub_app.display_preview({"overlay": {"modules": {"webcams": {"cams": {"a": {"lat": 49.0, "lon": -123.0}, "b": {}}}}},
                             "ttl_sec": 60})
    cams = sent["overlay"]["modules"]["webcams"]["cams"]
    assert cams["a"]["_wx"]["temp"] == 12.0 and "_wx" not in cams["b"]
