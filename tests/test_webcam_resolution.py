"""Webcams at the stream's own resolution (hub/webcams.py): every format a YouTube stream has, the detail each crop
needs (never more than one stream pixel per LED), the hi picture (whole frame or the crops' region) and consumers
rendered from it as frames are decoded."""

import collections
import json
import threading
import types

import numpy as np

from hub import webcams as wc

FMT = [  # what yt-dlp -J lists for a 4K live stream (video-only HLS), plus audio and a muxed one
    {"format_id": "233", "url": "https://m/a.m3u8", "vcodec": "none", "acodec": "mp4a", "width": None, "height": None},
    {"format_id": "231", "url": "https://m/480.m3u8", "vcodec": "avc1.4D401F", "acodec": "none", "width": 854,
     "height": 480, "fps": 30, "tbr": 1283},
    {"format_id": "232", "url": "https://m/720.m3u8", "vcodec": "avc1.4D401F", "acodec": "none", "width": 1280,
     "height": 720, "fps": 30, "tbr": 2448},
    {"format_id": "270", "url": "https://m/1080.m3u8", "vcodec": "avc1.640028", "acodec": "none", "width": 1920,
     "height": 1080, "fps": 30, "tbr": 4500},
    {"format_id": "625", "url": "https://m/2160.m3u8", "vcodec": "vp09.00.51.08", "acodec": "none", "width": 3840,
     "height": 2160, "fps": 30, "tbr": 20000},
    {"format_id": "bad", "url": "file:///etc/passwd", "vcodec": "avc1", "width": 1920, "height": 1080},
]


def test_every_resolution_a_stream_has_smallest_first():
    f = wc.formats_of({"formats": FMT})
    assert [x["h"] for x in f] == [480, 720, 1080, 2160]                  # no audio, nothing but network URLs
    assert wc.native_of(f)["w"] == 3840 and f[-1]["vcodec"] == "vp09"


def test_the_stream_picked_is_the_smallest_with_enough_detail_but_never_under_720p():
    f = wc.formats_of({"formats": FMT})
    assert wc.pick_format(f, 0)["h"] == 720                               # 480p is soft and blocky: 720p at least
    assert wc.pick_format(f, 0, min_h=0)["h"] == 480                      # (snapshots: small is fine)
    assert wc.pick_format(f, 1300)["h"] == 1080
    assert wc.pick_format(f, 9000)["h"] == 2160                           # more than there is: the biggest


def test_lookup_reads_yt_dlps_json(monkeypatch):
    info = {"title": "Shibuya", "live_status": "is_live", "formats": FMT}
    monkeypatch.setattr(wc, "_yt_dlp", lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=json.dumps(info),
                                                                              stderr=""))
    wc._resolved.clear()
    got = wc.lookup("https://www.youtube.com/watch?v=abcdefghijk")
    assert got["title"] == "Shibuya" and len(got["formats"]) == 4
    assert wc.resolve("youtube", "https://www.youtube.com/watch?v=abcdefghijk", need_w=1500)[0] == "https://m/1080.m3u8"
    wc._resolved.clear()


def test_a_crop_that_lo_covers_needs_no_hi_picture():
    assert wc.plan(1280, 720, [(448, 175, None)]) is None                 # full width: 640 px is plenty for 448
    assert wc.plan(1920, 1080, [(448, 175, None), (224, 134, None)]) is None


def test_a_zoomed_crop_gets_the_whole_frame_at_the_detail_it_needs():
    p = wc.plan(1920, 1080, [(448, 175, {"x": 0.2, "y": 0.2, "w": 0.5})])
    assert (p["x"], p["y"], p["w"], p["h"]) == (0, 0, 1920, 1080)          # the whole frame
    assert abs(p["full_w"] - 448 / 0.5 * wc.OVERSAMPLE) < 4                 # 1.5 stream pixels per LED
    assert p["out_w"] <= 1920 and p["out_w"] * p["out_h"] <= wc.HI_BUDGET


def test_one_to_one_on_4k_gets_just_the_crops_region_at_full_detail():
    one = {"x": 0.4, "y": 0.4, "w": 448 / 3840}                            # one stream pixel per LED
    wants = [(448, 175, one), (448, 175, None), (224, 134, None)]          # + the other places' fit crops (lo)
    p = wc.plan(3840, 2160, wants)
    assert p["full_w"] == 3840 and p["out_w"] == p["w"]                     # not scaled: every stream pixel
    assert p["w"] < 3840 and p["out_w"] * p["out_h"] <= wc.HI_BUDGET        # just the region, within the budget
    assert wc.covers(p, 3840, 2160, wants[0]) and not wc.covers(p, 3840, 2160, wants[1])


def test_one_to_one_is_a_straight_copy_of_the_stream():
    rng = np.random.default_rng(3)
    src = rng.integers(0, 255, (2160, 3840, 3), np.uint8)
    one = {"x": 0.4, "y": 0.4, "w": 448 / 3840}
    p = wc.plan(3840, 2160, [(448, 175, one)])
    hi = np.ascontiguousarray(src[p["y"]:p["y"] + p["h"], p["x"]:p["x"] + p["w"]])
    out = wc.render_region(hi, p, 3840, 2160, 448, 175, one, {"sharpen": 0})
    l, t, r, b = wc.crop_box(3840, 2160, 448, 175, one)
    assert (out == src[t:b, l:r]).all()                                    # pixel for pixel


def test_a_region_renders_what_the_whole_frame_would():
    rng = np.random.default_rng(5)
    src = rng.integers(0, 255, (1080, 1920, 3), np.uint8)
    crop = {"x": 0.3, "y": 0.25, "w": 0.4}
    p = {"full_w": 1920, "x": 400, "y": 200, "w": 1200, "h": 700, "out_w": 1200, "out_h": 700}
    hi = np.ascontiguousarray(src[200:900, 400:1600])
    a = wc.render_region(hi, p, 1920, 1080, 448, 175, crop, {"sharpen": 0}).astype(int)
    b = wc.render(src, 448, 175, crop, {"sharpen": 0}).astype(int)
    assert np.abs(a - b).mean() < 1.0


def test_sharpening_is_on_for_video_by_default_and_off_for_the_grid():
    assert wc.sharpen_of({}) == wc.DEFAULT_SHARPEN and wc.sharpen_of(None) == 0
    assert wc.sharpen_of({"sharpen": 0}) == 0 and wc.sharpen_of({"sharpen": 9}) == 2
    img = np.zeros((90, 160, 3), np.uint8)
    img[:, 80:] = 200
    soft, sharp = wc.render(img, 80, 45, None, {"sharpen": 0}), wc.render(img, 80, 45, None, {"sharpen": 1.5})
    assert int(sharp[20].max()) >= int(soft[20].max()) and (sharp != soft).any()


def _feed_shell():
    f = wc.Feed.__new__(wc.Feed)
    f.kind, f.url, f.fps = "youtube", "u", 30
    f._lock = threading.Lock()
    f._cond = threading.Condition(f._lock)
    f.decoded = collections.deque(maxlen=181)
    f.consumers = {}
    f.source, f.hi = {"w": 1920, "h": 1080}, None
    f.error = ""
    return f


def test_a_new_consumer_starts_with_the_backlog_then_gets_every_frame():
    f = _feed_shell()
    lo = [np.full((360, 640, 3), i, np.uint8) for i in range(5)]
    for i, fr in enumerate(lo, 1):
        f.decoded.append((i, fr))
    c = f.consumer(448, 175)
    assert c.oldest() == 0 and c.after(0)[0] == 1 and c.after(5) is None   # seeded from lo
    f._render_consumers(np.full((360, 640, 3), 9, np.uint8), 6)
    assert c.after(5)[0] == 6 and int(c.after(5)[1][0, 0, 0]) == 9
    assert f.consumer(448, 175) is c and c.refs == 2                         # the same zone and crop: shared
    f.release(c)
    f.release(c)
    assert f.consumers == {}


def test_a_preview_uses_the_hi_render_only_for_its_current_draft():
    f = _feed_shell()
    d = f.consumer(448, 175, key=("preview", "t"), draft=True)
    f._render_consumers(np.zeros((360, 640, 3), np.uint8), 1)
    assert d.for_dseq(1) is not None
    d.set(448, 175, {"x": 0.1, "y": 0.1, "w": 0.5}, None)                  # the crop moved: re-render from lo
    assert d.for_dseq(1) is None and not d.settled()


def test_ffmpeg_gives_lo_and_the_hi_region_on_their_own_pipes():
    f = _feed_shell()
    f.decoded_t = 0.0
    f.source = {"w": 3840, "h": 2160}
    f.hi = wc.plan(3840, 2160, [(448, 175, {"x": 0.4, "y": 0.4, "w": 448 / 3840})])
    args = f._args("https://m/2160.m3u8", hi_fd=7, hw=True)
    graph = args[args.index("-filter_complex") + 1]
    assert "split=2" in graph and f"crop=w={f.hi['w']}:h={f.hi['h']}" in graph and "scale=640:-2" in graph
    assert "pipe:1" in args and "pipe:7" in args and args[args.index("-hwaccel") + 1] == "vaapi"
    f.hi = None
    plain = f._args("https://m/720.m3u8")
    assert "-filter_complex" not in plain and "-hwaccel" not in plain
