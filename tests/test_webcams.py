"""Webcams: crop maths, the frame wire format, captions, config validation and the rotation's "skip"."""

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
    ids = re.findall(r'\{ id: "([^"]+)", label: "([^"]+)"', js)
    assert len(ids) >= 15
    assert len({i for i, _ in ids}) == len(ids) and len({lbl for _, lbl in ids}) == len(ids)
    cams = {f"c{n}": {"label": lbl, "source": {"type": "youtube", "url": f"https://www.youtube.com/watch?v={i}"}}
            for n, (i, lbl) in enumerate(ids)}
    config.validate(_cfg({"cams": cams}))
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{11}", i) for i, _ in ids)
